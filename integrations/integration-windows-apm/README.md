# Integration: APM V3 - Windows Services (IIS)

Grafana dashboards and recording rules for APM-style monitoring of Windows IIS sites, modeled after the Kubernetes APM V3 integration but using IIS W3C access logs and `windows_exporter` metrics.

## Contents

| File | Description |
|------|-------------|
| `provisioning/dashboard-windows-service-overview.yaml` | Overview table of all IIS sites with Apdex, Rate, Duration P95, Errors |
| `provisioning/dashboard-windows-service.yaml` | Individual IIS site dashboard with Apdex, RED metrics, and infrastructure panels |
| `provisioning/dashboard-windows-service-operations.yaml` | Per-URI operations table with RED metrics drill-down |
| `provisioning/dashboard-windows-service-span.yaml` | URI endpoint detail with Rate, Errors, Duration, and access logs |
| `provisioning_mimir_ruler/windows-app-o11y.yaml` | Prometheus recording rules for pre-aggregated Windows APM metrics |

## Prerequisites

### Site-Level Loki Recording Rules (Required)

The site-level dashboards (overview and service) rely on the **7 Loki recording rules** defined in the `integration-duplo-microsoft-iis` README. These must be applied first:

- `iis:request_rate:rate1m`
- `iis:response_time_ms_avg:rate1m`
- `iis:response_time_ms_p50:rate1m`
- `iis:response_time_ms_p95:rate1m`
- `iis:response_time_ms_p99:rate1m`
- `iis:requests_satisfied:rate1m`
- `iis:requests_tolerating:rate1m`

See: `integrations/integration-duplo-microsoft-iis/README.md`

---

### Per-URI Loki Recording Rules (Required for Operations & Span Dashboards)

The operations and span dashboards require **additional** Loki recording rules that break down metrics by URI. These must be pushed to the Loki ruler API alongside the site-level rules.

#### IIS W3C Log Pattern

```
<date> <time> <sip> <method> <uri> <query> <port> <user> <cip> <ua> <ref> <status> <sub> <win32> <sent> <received> <time_taken>
```

#### Apply the Per-URI Rules — run inside the `duplo-automation` pod

```bash
curl -s -X POST \
  "${GRR_GRAFANA_URL}/api/ruler/duplo-logging/api/v1/rules/duplo-custom-uri" \
  -u "${GRR_GRAFANA_USER}:${GRR_GRAFANA_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{
  "name": "duplo-custom-uri",
  "interval": "1m",
  "rules": [
    {
      "record": "iis:uri_request_rate:rate1m",
      "expr": "sum by (job, site, uri, cluster, namespace, aws_account, instance, instance_id) (rate({job=\"integrations/iis\"} | pattern `<date> <time> <sip> <method> <uri> <query> <port> <user> <cip> <ua> <ref> <status> <sub> <win32> <sent> <received> <time_taken>` | __error__=\"\" [1m]))"
    },
    {
      "record": "iis:uri_response_time_ms_avg:rate1m",
      "expr": "sum by (job, site, uri, cluster, namespace, aws_account, instance, instance_id) (rate({job=\"integrations/iis\"} | pattern `<date> <time> <sip> <method> <uri> <query> <port> <user> <cip> <ua> <ref> <status> <sub> <win32> <sent> <received> <time_taken>` | unwrap time_taken | __error__=\"\" [1m])) / sum by (job, site, uri, cluster, namespace, aws_account, instance, instance_id) (rate({job=\"integrations/iis\"} | pattern `<date> <time> <sip> <method> <uri> <query> <port> <user> <cip> <ua> <ref> <status> <sub> <win32> <sent> <received> <time_taken>` | __error__=\"\" [1m]))"
    },
    {
      "record": "iis:uri_response_time_ms_p95:rate1m",
      "expr": "quantile_over_time(0.95, {job=\"integrations/iis\"} | pattern `<date> <time> <sip> <method> <uri> <query> <port> <user> <cip> <ua> <ref> <status> <sub> <win32> <sent> <received> <time_taken>` | unwrap time_taken | __error__=\"\" [1m]) by (job, site, uri, cluster, namespace, aws_account, instance, instance_id)"
    },
    {
      "record": "iis:uri_response_time_ms_p99:rate1m",
      "expr": "quantile_over_time(0.99, {job=\"integrations/iis\"} | pattern `<date> <time> <sip> <method> <uri> <query> <port> <user> <cip> <ua> <ref> <status> <sub> <win32> <sent> <received> <time_taken>` | unwrap time_taken | __error__=\"\" [1m]) by (job, site, uri, cluster, namespace, aws_account, instance, instance_id)"
    },
    {
      "record": "iis:uri_error_rate:rate1m",
      "expr": "sum by (job, site, uri, cluster, namespace, aws_account, instance, instance_id) (rate({job=\"integrations/iis\"} | pattern `<date> <time> <sip> <method> <uri> <query> <port> <user> <cip> <ua> <ref> <status> <sub> <win32> <sent> <received> <time_taken>` | __error__=\"\" | status >= 500 [1m]))"
    },
    {
      "record": "iis:uri_requests_satisfied:rate1m",
      "expr": "sum by (job, site, uri, cluster, namespace, aws_account, instance, instance_id) (rate({job=\"integrations/iis\"} | pattern `<date> <time> <sip> <method> <uri> <query> <port> <user> <cip> <ua> <ref> <status> <sub> <win32> <sent> <received> <time_taken>` | __error__=\"\" | time_taken <= 1000 [1m]))"
    },
    {
      "record": "iis:uri_requests_tolerating:rate1m",
      "expr": "sum by (job, site, uri, cluster, namespace, aws_account, instance, instance_id) (rate({job=\"integrations/iis\"} | pattern `<date> <time> <sip> <method> <uri> <query> <port> <user> <cip> <ua> <ref> <status> <sub> <win32> <sent> <received> <time_taken>` | __error__=\"\" | time_taken > 1000 | time_taken <= 4000 [1m]))"
    }
  ]
}'
```

#### Verify the rules were applied

Go to **Grafana > Alerts & IRM > Alert rules** and filter by namespace `duplo-custom-uri`. You should see 7 recording rules listed under the `duplo-custom-uri` group.

> **Note:** The environment variables `GRR_GRAFANA_URL`, `GRR_GRAFANA_USER`, and `GRR_GRAFANA_TOKEN` are already available inside the `duplo-automation` pod.

---

## Dashboard Navigation

```
Windows Service Overview  ──>  Windows Service Dashboard  ──>  Windows Service Operations  ──>  Windows Service Span
(all IIS sites table)          (single site RED + Apdex)       (per-URI operations table)       (single URI detail + logs)
```

Each dashboard links to the next for progressive drill-down, with back-navigation links to parent dashboards.

## Metric Sources

| Source | Metrics | Used For |
|--------|---------|----------|
| Loki recording rules (site-level) | `iis:request_rate:rate1m`, `iis:response_time_ms_*`, `iis:requests_satisfied:rate1m`, `iis:requests_tolerating:rate1m` | Site-level Apdex, Rate, Duration |
| Loki recording rules (per-URI) | `iis:uri_request_rate:rate1m`, `iis:uri_response_time_ms_*`, `iis:uri_error_rate:rate1m` | Operations & Span dashboards |
| Prometheus (windows_exporter) | `windows_iis_requests_total`, `windows_iis_current_connections`, `windows_iis_sent_bytes_total`, `windows_iis_received_bytes_total`, `windows_iis_server_*_cache_*` | Error ratios, connections, traffic, cache hit ratios |
| Mimir recording rules | `winappo11y:*` | Pre-aggregated APM metrics, anomaly detection baselines |
