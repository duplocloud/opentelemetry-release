# Integration: Duplo Microsoft IIS

Grafana dashboards, alert rules, and Loki recording rules for monitoring Microsoft IIS sites via the `windows_exporter` and IIS W3C access logs.

## Contents

| File | Description |
|------|-------------|
| `provisioning/folder-microsoft-iis.yaml` | Grafana folder definition |
| `provisioning/rules-microsoft-iis.yaml` | Prometheus alert rules (IIS metrics) |
| `provisioning/dashboard-duplo-microsoft-iis-overview.yaml` | Main overview dashboard (includes Apdex section) |
| `provisioning/dashboard-duplo-microsoft-iis-applications.yaml` | Per-application worker/thread dashboard |

> **Note:** Loki recording rules are **not stored as a file** in this integration — they have no Grizzly resource kind and must be applied manually via the Loki ruler API. See the [Loki Recording Rules](#loki-recording-rules) section below.

---

## Loki Recording Rules

The Apdex and response-time panels in the overview dashboard rely on **7 Loki recording rules** that parse IIS W3C access logs and write pre-aggregated metrics into Mimir. These rules are **not applied automatically by Grizzly** — they must be pushed to the Loki ruler API once per environment.

### IIS W3C Log Pattern

```
<date> <time> <sip> <method> <uri> <query> <port> <user> <cip> <ua> <ref> <status> <sub> <win32> <sent> <received> <time_taken>
```

`time_taken` is in **milliseconds**.

### Apdex Thresholds (T = 1 s)

| Zone | Condition |
|------|-----------|
| Satisfied | `time_taken ≤ 1000 ms` |
| Tolerating | `1000 ms < time_taken ≤ 4000 ms` |
| Frustrated | `time_taken > 4000 ms` |

### Apply the Rules — run inside the `duplo-automation` pod

```bash
curl -s -X POST \
  "${GRR_GRAFANA_URL}/api/ruler/duplo-logging/api/v1/rules/duplo-custom" \
  -u "${GRR_GRAFANA_USER}:${GRR_GRAFANA_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{
  "name": "duplo-custom",
  "interval": "1m",
  "rules": [
    {
      "record": "iis:request_rate:rate1m",
      "expr": "sum by (job, site, cluster, namespace, aws_account, instance, instance_id) (rate({job=\"integrations/iis\"} [1m]))"
    },
    {
      "record": "iis:response_time_ms_avg:rate1m",
      "expr": "sum by (job, site, cluster, namespace, aws_account, instance, instance_id) (rate({job=\"integrations/iis\"} | pattern `<date> <time> <sip> <method> <uri> <query> <port> <user> <cip> <ua> <ref> <status> <sub> <win32> <sent> <received> <time_taken>` | unwrap time_taken | __error__=\"\" [1m])) / sum by (job, site, cluster, namespace, aws_account, instance, instance_id) (rate({job=\"integrations/iis\"} [1m]))"
    },
    {
      "record": "iis:response_time_ms_p50:rate1m",
      "expr": "quantile_over_time(0.50, {job=\"integrations/iis\"} | pattern `<date> <time> <sip> <method> <uri> <query> <port> <user> <cip> <ua> <ref> <status> <sub> <win32> <sent> <received> <time_taken>` | unwrap time_taken | __error__=\"\" [1m]) by (job, site, cluster, namespace, aws_account, instance, instance_id)"
    },
    {
      "record": "iis:response_time_ms_p95:rate1m",
      "expr": "quantile_over_time(0.95, {job=\"integrations/iis\"} | pattern `<date> <time> <sip> <method> <uri> <query> <port> <user> <cip> <ua> <ref> <status> <sub> <win32> <sent> <received> <time_taken>` | unwrap time_taken | __error__=\"\" [1m]) by (job, site, cluster, namespace, aws_account, instance, instance_id)"
    },
    {
      "record": "iis:response_time_ms_p99:rate1m",
      "expr": "quantile_over_time(0.99, {job=\"integrations/iis\"} | pattern `<date> <time> <sip> <method> <uri> <query> <port> <user> <cip> <ua> <ref> <status> <sub> <win32> <sent> <received> <time_taken>` | unwrap time_taken | __error__=\"\" [1m]) by (job, site, cluster, namespace, aws_account, instance, instance_id)"
    },
    {
      "record": "iis:requests_satisfied:rate1m",
      "expr": "sum by (job, site, cluster, namespace, aws_account, instance, instance_id) (rate({job=\"integrations/iis\"} | pattern `<date> <time> <sip> <method> <uri> <query> <port> <user> <cip> <ua> <ref> <status> <sub> <win32> <sent> <received> <time_taken>` | __error__=\"\" | time_taken <= 1000 [1m]))"
    },
    {
      "record": "iis:requests_tolerating:rate1m",
      "expr": "sum by (job, site, cluster, namespace, aws_account, instance, instance_id) (rate({job=\"integrations/iis\"} | pattern `<date> <time> <sip> <method> <uri> <query> <port> <user> <cip> <ua> <ref> <status> <sub> <win32> <sent> <received> <time_taken>` | __error__=\"\" | time_taken > 1000 | time_taken <= 4000 [1m]))"
    }
  ]
}'
```

### Verify the rules were applied

Go to **Grafana → Alerts & IRM → Alert rules** and filter by namespace `duplo-custom`. You should see 7 recording rules listed under the `duplo-custom` group.

> **Note:** The environment variables `GRR_GRAFANA_URL`, `GRR_GRAFANA_USER`, and `GRR_GRAFANA_TOKEN` are already available inside the `duplo-automation` pod.
