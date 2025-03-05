{{ define "slack.alert.title" }}
<<DuploPortalName>> - {{ .CommonLabels.cluster }} [{{ .Status | toUpper }}:{{ .Alerts | len }}]
{{ end }}

{{ define "slack.alert.title_link" }}
https://<<GrafanaURL>>/alerting/list?search={{ .CommonLabels.alertname }}
{{ end }}

{{ define "slack.alert.text" }}
{{ range .Alerts -}}
📢 *Alert:* {{ if .Labels.severity }} - `{{ .Labels.severity }}` {{- end }}  {{ .Labels.alertname }} for {{ if .Labels.duplo_slo_name }}{{ .Labels.duplo_slo_name }}{{ else if .Labels.pod }}{{ .Labels.pod }}{{ else if .Labels.daemonset }}{{ .Labels.daemonset }}{{ else if .Labels.instance }}{{ .Labels.instance }}{{ else if .Labels.node }}{{ .Labels.node }}{{ else if .Labels.job }}{{ .Labels.job }}{{ else if .Labels.cluster }}{{ .Labels.cluster }}{{ else }}unknown{{ end }}

*📝 Description:* {{ .Annotations.description }}

*📌 Details:*
  {{ range .Labels.SortedPairs }}• *{{ .Name }}:* `{{ .Value }}`
  {{ end }}
📖 {{ if .Annotations.runbook_url }}<{{ .Annotations.runbook_url }}|Runbook>{{ else }}No Runbook Available{{ end }}
🔗 <https://<<GrafanaURL>>/alerting/list?search={{ .Labels.alertname }}|Grafana Dashboard>

{{ end }}
{{ end }}