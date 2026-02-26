#!/usr/bin/env python3
"""
Update Loki integration dashboards from the Loki SSD mixin compiled repo.
Source: production/loki-mixin-compiled-ssd/dashboards/

Local customizations preserved:
  - editable: false
  - uid: loki-<name> prefix (instead of upstream short UIDs)
  - Grizzly YAML wrapper (apiVersion, kind, metadata)
  - job matchers rewritten to use $namespace/$cluster-<component> format
    so dashboards work with arbitrary Helm release names (e.g. duplo-logging)
    instead of only matching 'loki.*' or 'enterprise-logs'.
"""

import json
import re
import urllib.request
import yaml
import os

BASE_SSD = "https://raw.githubusercontent.com/grafana/loki/main/production/loki-mixin-compiled-ssd/dashboards"
PROV_DIR = os.path.join(os.path.dirname(__file__), "provisioning")

# (local_file, upstream_filename, grizzly_name, local_uid)
MAPPINGS = [
    ("dashboard-loki-bloom-build.yaml",       "loki-bloom-build.json",           "loki-bloom-build",        "loki-bloom-build"),
    ("dashboard-loki-bloom-gateway.yaml",     "loki-bloom-gateway.json",         "loki-bloom-gateway",      "loki-bloom-gateway"),
    ("dashboard-loki-chunks.yaml",            "loki-chunks.json",                "loki-chunks",             "loki-chunks"),
    ("dashboard-loki-deletion.yaml",          "loki-deletion.json",              "loki-deletion",           "loki-deletion"),
    ("dashboard-loki-logs.yaml",              "loki-logs.json",                  "loki-logs",               "loki-logs"),
    ("dashboard-loki-object-store.yaml",      "loki-thanos-object-storage.json", "loki-object-store",       "loki-object-store"),
    ("dashboard-loki-operational.yaml",       "loki-operational.json",           "loki-operational",        "loki-operational"),
    ("dashboard-loki-reads.yaml",             "loki-reads.json",                 "loki-reads",              "loki-reads"),
    ("dashboard-loki-recording-rules.yaml",   "loki-mixin-recording-rules.json", "loki-recording-rules",    "loki-recording-rules"),
    ("dashboard-loki-resources-overview.yaml","loki-resources-overview.json",    "loki-resources-overview", "loki-resources-overview"),
    ("dashboard-loki-retention.yaml",         "loki-retention.json",             "loki-retention",          "loki-retention"),
    ("dashboard-loki-writes.yaml",            "loki-writes.json",                "loki-writes",             "loki-writes"),
]


class IndentDumper(yaml.Dumper):
    """YAML Dumper with 4-space indentation and proper list block style."""
    def increase_indent(self, flow=False, indentless=False):
        return super().increase_indent(flow=flow, indentless=False)


# Job matcher replacements: rewrite all upstream patterns that hardcode
# 'loki.*|enterprise-logs' as the release-name prefix to use $cluster instead.
# This makes dashboards work with any Helm release name (e.g. duplo-logging).
#
# Pattern format becomes: $namespace/$cluster-<component>
# matching job labels like: duploservices-otel-staging/duplo-logging-write
JOB_REPLACEMENTS = [
    # Patterns with (loki.*|enterprise-logs) prefix — SSD components
    (r'(\$namespace)/\(loki\.\*\|enterprise-logs\)-write',   r'\1/$cluster-write'),
    (r'\(\$namespace\)/\(loki\.\*\|enterprise-logs\)-write', r'$namespace/$cluster-write'),
    (r'\(\$namespace\)/\(loki\.\*\|enterprise-logs\)-read',  r'$namespace/$cluster-read'),
    (r'\(\$namespace\)/\(loki\.\*\|enterprise-logs\)-backend', r'$namespace/$cluster-backend'),
    # Bloom and index-gateway sub-components
    (r'\(\$namespace\)/bloom-gateway', r'$namespace/$cluster-bloom-gateway'),
    (r'\$namespace/bloom-gateway',     r'$namespace/$cluster-bloom-gateway'),
    (r'\$namespace/bloom-builder',     r'$namespace/$cluster-bloom-builder'),
    (r'\$namespace/bloom-planner',     r'$namespace/$cluster-bloom-planner'),
    (r'\$namespace/index-gateway',     r'$namespace/$cluster-index-gateway'),
]


def apply_job_replacements(raw_json: str) -> str:
    """Apply job matcher rewrites to the raw JSON string."""
    for pattern, replacement in JOB_REPLACEMENTS:
        raw_json = re.sub(pattern, replacement, raw_json)
    return raw_json


def fetch_json(url):
    with urllib.request.urlopen(url) as resp:
        raw = resp.read().decode()
    raw = apply_job_replacements(raw)
    return json.loads(raw)


def main():
    for local_file, upstream_filename, grizzly_name, local_uid in MAPPINGS:
        url = f"{BASE_SSD}/{upstream_filename}"
        print(f"Fetching {upstream_filename} ...")
        dashboard = fetch_json(url)

        # Apply local customizations
        dashboard["editable"] = False
        dashboard["uid"] = local_uid

        doc = {
            "apiVersion": "grizzly.grafana.com/v1alpha1",
            "kind": "Dashboard",
            "metadata": {
                "folder": "integration-loki",
                "name": grizzly_name,
            },
            "spec": dashboard,
        }

        out_path = os.path.join(PROV_DIR, local_file)
        with open(out_path, "w") as f:
            yaml.dump(
                doc,
                f,
                Dumper=IndentDumper,
                default_flow_style=False,
                allow_unicode=True,
                indent=4,
                sort_keys=False,
            )
        print(f"  -> Written: {local_file}")

    print("\nAll 12 SSD dashboards updated successfully.")


if __name__ == "__main__":
    main()
