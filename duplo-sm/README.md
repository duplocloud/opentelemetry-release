# duplo-sm

Dashboards, alert rules, and datasources for the Kubernetes-native
Prometheus + Grafana + Alertmanager stack ("Duplo Standard Monitoring",
`prometheus-community/kube-prometheus-stack`) — a separate product from the
AOS/opentelemetry-stack content under `integrations/`/`integrations-sm/`.

Plain Kubernetes manifests (ConfigMaps + PrometheusRule objects), applied
directly:

```
kubectl apply -f duplo-sm/dashboards/ -f duplo-sm/rules/ -f duplo-sm/datasources-configmap.yaml
```

| Path | Contents |
|------|----------|
| `dashboards/` | One ConfigMap per dashboard (`grafana_dashboard: "1"` label, picked up by the chart's Grafana sidecar) |
| `rules/` | `PrometheusRule` objects |
| `datasources-configmap.yaml` | Prometheus, CloudWatch, and Azure Monitor datasources |
