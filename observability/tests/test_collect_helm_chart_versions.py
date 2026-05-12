"""
Unit tests for collect_helm_chart_versions.

Helm secrets are encoded as: base64(base64(gzip(json)))
These tests generate properly encoded fake secrets and mock the K8s API + filesystem,
so no real cluster access is needed.
"""

import base64
import gzip
import importlib.util
import json
import os
import sys
import unittest
from unittest.mock import MagicMock, mock_open, patch

# Load otel-observability.py by path since the hyphen makes it an invalid module name
_spec = importlib.util.spec_from_file_location(
    "otel_observability",
    os.path.join(os.path.dirname(__file__), "..", "otel-observability.py")
)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
collect_helm_chart_versions = _mod.collect_helm_chart_versions


def make_helm_secret(release_name: str, chart_name: str, chart_version: str,
                     revision: int = 1, dependencies: list = None) -> dict:
    """
    Build a fake K8s secret dict that mimics what the K8s API returns for a Helm release.

    Encoding: json -> gzip -> base64 (Helm) -> base64 (K8s)
    """
    release_json = {
        "name": release_name,
        "chart": {
            "metadata": {
                "name": chart_name,
                "version": chart_version,
                "dependencies": dependencies or []
            }
        },
        "version": revision
    }
    raw = json.dumps(release_json).encode("utf-8")
    helm_encoded = base64.b64encode(gzip.compress(raw))       # base64(gzip(json))
    k8s_encoded = base64.b64encode(helm_encoded).decode()     # base64(base64(gzip(json)))

    return {
        "metadata": {
            "name": f"sh.helm.release.v1.{release_name}.v{revision}",
            "labels": {
                "name": release_name,
                "owner": "helm",
                "status": "deployed"
            }
        },
        "data": {
            "release": k8s_encoded
        }
    }


class TestCollectHelmChartVersions(unittest.TestCase):

    def _run(self, secrets: list, namespace: str = "test-ns", cluster: str = "test-cluster") -> list:
        """Helper: run collect_helm_chart_versions with mocked K8s API and env."""
        api_response = {"items": secrets}

        mock_response = MagicMock()
        mock_response.json.return_value = api_response
        mock_response.raise_for_status = MagicMock()

        with patch("builtins.open", mock_open(read_data="fake-token")), \
             patch("requests.get", return_value=mock_response), \
             patch.dict("os.environ", {"CLUSTER": cluster, "NAMESPACE": namespace,
                                       "KUBERNETES_SERVICE_HOST": "k8s.local",
                                       "KUBERNETES_SERVICE_PORT": "443"}):
            return collect_helm_chart_versions(namespace)

    # ------------------------------------------------------------------
    # Basic cases
    # ------------------------------------------------------------------

    def test_single_plain_chart(self):
        """A non-umbrella chart emits exactly one record with no parent_chart."""
        secrets = [make_helm_secret("prometheus", "prometheus", "25.3.1")]
        records = self._run(secrets)

        self.assertEqual(len(records), 1)
        spec = records[0]["spec"]
        self.assertEqual(spec["release"], "prometheus")
        self.assertEqual(spec["chart"], "prometheus")
        self.assertEqual(spec["chart_version"], "25.3.1")
        self.assertNotIn("parent_chart", spec)

    def test_umbrella_chart_emits_parent_and_subcharts(self):
        """An umbrella chart emits one parent record plus one record per subchart."""
        deps = [
            {"name": "grafana",     "version": "7.0.0",  "repository": "https://grafana.github.io/helm-charts"},
            {"name": "prometheus",  "version": "25.3.1", "repository": "https://prometheus-community.github.io/helm-charts"},
        ]
        secrets = [make_helm_secret("my-stack", "my-stack", "1.0.0", dependencies=deps)]
        records = self._run(secrets)

        self.assertEqual(len(records), 3)  # 1 parent + 2 subcharts

        parent = records[0]
        self.assertEqual(parent["spec"]["chart"], "my-stack")
        self.assertEqual(parent["spec"]["chart_version"], "1.0.0")
        self.assertNotIn("parent_chart", parent["spec"])

        subchart_names = {r["spec"]["chart"] for r in records[1:]}
        self.assertEqual(subchart_names, {"grafana", "prometheus"})

        for r in records[1:]:
            self.assertEqual(r["spec"]["parent_chart"], "my-stack")
            self.assertEqual(r["spec"]["release"], "my-stack")
            self.assertIn(r["spec"]["chart_version"], {"7.0.0", "25.3.1"})

    def test_subchart_metadata_is_correct(self):
        """Subchart records carry the same cluster/namespace metadata as the parent."""
        deps = [{"name": "loki", "version": "6.0.0", "repository": ""}]
        secrets = [make_helm_secret("logging", "logging", "2.0.0", dependencies=deps)]
        records = self._run(secrets, namespace="logging-ns", cluster="prod-cluster")

        for r in records:
            self.assertEqual(r["metadata"]["cluster"], "prod-cluster")
            self.assertEqual(r["metadata"]["namespace"], "logging-ns")

    # ------------------------------------------------------------------
    # Revision deduplication
    # ------------------------------------------------------------------

    def test_picks_highest_revision_when_multiple_deployed_secrets(self):
        """If two secrets exist for the same release, the higher revision wins."""
        old = make_helm_secret("myapp", "myapp", "1.0.0", revision=1)
        new = make_helm_secret("myapp", "myapp", "1.1.0", revision=2)
        records = self._run([old, new])

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["spec"]["chart_version"], "1.1.0")

    def test_picks_highest_revision_regardless_of_order(self):
        """Revision selection works even if secrets are returned newest-first."""
        new = make_helm_secret("myapp", "myapp", "1.1.0", revision=2)
        old = make_helm_secret("myapp", "myapp", "1.0.0", revision=1)
        records = self._run([new, old])

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["spec"]["chart_version"], "1.1.0")

    def test_multiple_independent_releases(self):
        """Multiple distinct releases each produce their own records."""
        secrets = [
            make_helm_secret("prometheus", "prometheus", "25.3.1"),
            make_helm_secret("grafana",    "grafana",    "7.0.0"),
        ]
        records = self._run(secrets)

        self.assertEqual(len(records), 2)
        release_names = {r["spec"]["release"] for r in records}
        self.assertEqual(release_names, {"prometheus", "grafana"})

    # ------------------------------------------------------------------
    # Edge / error cases
    # ------------------------------------------------------------------

    def test_empty_namespace_returns_empty_list(self):
        """No secrets → empty result."""
        records = self._run([])
        self.assertEqual(records, [])

    def test_secret_missing_release_data_is_skipped(self):
        """A secret with no data.release field is silently skipped."""
        bad_secret = {
            "metadata": {
                "name": "sh.helm.release.v1.broken.v1",
                "labels": {"name": "broken", "owner": "helm", "status": "deployed"}
            },
            "data": {}   # no 'release' key
        }
        records = self._run([bad_secret])
        self.assertEqual(records, [])

    def test_corrupted_release_data_is_skipped(self):
        """A secret with undecodable release data is skipped with a warning, not a crash."""
        bad_secret = {
            "metadata": {
                "name": "sh.helm.release.v1.broken.v1",
                "labels": {"name": "broken", "owner": "helm", "status": "deployed"}
            },
            "data": {"release": "not-valid-base64!!"}
        }
        records = self._run([bad_secret])
        self.assertEqual(records, [])

    def test_secret_missing_release_name_label_is_skipped(self):
        """Secrets without the 'name' label are skipped."""
        secret = make_helm_secret("myapp", "myapp", "1.0.0")
        del secret["metadata"]["labels"]["name"]
        records = self._run([secret])
        self.assertEqual(records, [])

    def test_subchart_with_no_name_is_skipped(self):
        """A dependency entry missing 'name' is not added to subcharts."""
        deps = [
            {"name": "grafana", "version": "7.0.0"},
            {"version": "1.0.0"},             # no name → should be skipped
            {"name": "",        "version": "2.0.0"},  # empty name → should be skipped
        ]
        secrets = [make_helm_secret("stack", "stack", "1.0.0", dependencies=deps)]
        records = self._run(secrets)

        # 1 parent + 1 valid subchart (grafana only)
        self.assertEqual(len(records), 2)
        self.assertEqual(records[1]["spec"]["chart"], "grafana")

    def test_service_account_token_missing_returns_empty(self):
        """If the SA token file cannot be read, return empty list without crashing."""
        with patch("builtins.open", side_effect=OSError("no such file")), \
             patch.dict("os.environ", {"CLUSTER": "c", "NAMESPACE": "n"}):
            records = collect_helm_chart_versions("test-ns")
        self.assertEqual(records, [])

    def test_k8s_api_error_returns_empty(self):
        """If the K8s API call fails, return empty list without crashing."""
        import requests as req
        with patch("builtins.open", mock_open(read_data="fake-token")), \
             patch("requests.get", side_effect=req.exceptions.ConnectionError("refused")), \
             patch.dict("os.environ", {"CLUSTER": "c", "NAMESPACE": "n"}):
            records = collect_helm_chart_versions("test-ns")
        self.assertEqual(records, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
