# duplo-sm

Dashboards, alert rules, and datasources for the Kubernetes-native
Prometheus + Grafana + Alertmanager stack ("Duplo Standard Monitoring",
`prometheus-community/kube-prometheus-stack`) — a separate product from the
AOS/opentelemetry-stack content under `integrations/`.

Plain Kubernetes manifests (ConfigMaps + `PrometheusRule` objects). Every
dashboard also carries a `Duplo Managed` tag inside its JSON, visible in
Grafana's own dashboard list/search UI, so it's obvious at a glance which
dashboards are centrally provisioned (sidecar-picked-up, not hand-editable)
vs. customer-authored.

| Path | Contents |
|------|----------|
| `dashboards/` | One ConfigMap per dashboard (`grafana_dashboard: "1"` label, picked up by the chart's Grafana sidecar) |
| `rules/` | `PrometheusRule` objects |
| `datasources-configmap.yaml` | Prometheus, CloudWatch, and Azure Monitor datasources |

## Applying to a cluster

**One-off / manual**, from a local checkout:

```
kubectl apply -f duplo-sm/dashboards/ -f duplo-sm/rules/ -f duplo-sm/datasources-configmap.yaml
```

**Standard mechanism** — a short-lived in-cluster pod fetches this repo
straight from GitHub and applies it, so no local checkout is required and
every customer cluster runs the same procedure (same pattern used for the
Grafana/Prometheus snapshot-restore steps). One-time RBAC, then re-run the
pod on demand to pick up updates:

```
kubectl apply -n duplo-monitoring -f - <<'EOF'
apiVersion: v1
kind: ServiceAccount
metadata: {name: duplo-sm-sync, namespace: duplo-monitoring}
---
apiVersion: rbac.authorization.k8s.io/v1
kind: Role
metadata: {name: duplo-sm-sync, namespace: duplo-monitoring}
rules:
- apiGroups: [""]
  resources: ["configmaps"]
  verbs: ["get", "list", "watch", "create", "update", "patch"]
- apiGroups: ["monitoring.coreos.com"]
  resources: ["prometheusrules"]
  verbs: ["get", "list", "watch", "create", "update", "patch"]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata: {name: duplo-sm-sync, namespace: duplo-monitoring}
subjects: [{kind: ServiceAccount, name: duplo-sm-sync, namespace: duplo-monitoring}]
roleRef: {kind: Role, name: duplo-sm-sync, apiGroup: rbac.authorization.k8s.io}
EOF

kubectl run duplo-sm-sync -n duplo-monitoring --rm -i --restart=Never \
  --overrides='{"spec":{"serviceAccountName":"duplo-sm-sync"}}' \
  --image=bitnami/kubectl:latest --command -- sh -c '
    set -e; cd /tmp
    curl -sL https://github.com/duplocloud/opentelemetry-release/archive/refs/heads/main.tar.gz -o repo.tar.gz
    tar xzf repo.tar.gz
    SRC=opentelemetry-release-main/duplo-sm
    kubectl apply -n duplo-monitoring -f $SRC/dashboards/ -f $SRC/rules/ -f $SRC/datasources-configmap.yaml
  '
```

`bitnami/kubectl` is used because it bundles `curl`/`tar`/`kubectl` in one
image — confirmed live under both restore pods' non-root UIDs (Grafana's
`472:472`, Prometheus's `1000:2000`) as well as plain default, so the same
image works across every ad hoc pod this migration uses.
