# integration-duplo-notification

Opt-in integration that provisions duplo's alert **notification** path. It is applied only
when **both** `global.slackWebhookUrl` and `global.oncallWebhookUrl` are set in the `aos`
Helm chart (see `charts/aos/templates/bootstrap-integration-job.yaml`).

Everything here depends on one of those two webhooks, so none of it is safe to provision
unconditionally — an unset webhook previously produced a contact point with an empty URL
and an alert-relay loop.

## Contents

| File | Resource | Requires |
|---|---|---|
| `provisioning/01-contact-point-duplo-slack.yaml` | `AlertContactPoint` `duplo-aos-slack` | `AOS_SLACK_WEBHOOK` |
| `provisioning/01-contact-point-duplo-webhook.yaml` | `AlertContactPoint` `duplo-webhook-notifications` | `AOS_AUTOMATION_WEBHOOK` → relays to `ONCALL_WEBHOOK_URL` |
| `provisioning/02-duplo-notification-policy.yaml` | `AlertNotificationPolicy` `global` (all routes) | both |
| `provisioning/03-duplo-oncall-alert-rules.yaml` | `AlertRuleGroup` `duplo-automation-alerts` — the `AlertmanagerWebhookFailed` self-monitoring rule | `ONCALL_WEBHOOK_URL` |
| `provisioning_mimir_alertmanager/duplo-default-config.yaml` | Full Mimir Alertmanager config, incl. the `duplo-webhook-notifications` receiver | `ONCALL_WEBHOOK_URL` |
| `provisioning_mimir_alertmanager/duplo_templates.tpl` | Slack message templates. `mimirtool` requires a `.tpl` beside the config, so this must stay. | — |

## Ordering

Filenames are numerically prefixed because `grr apply` is given the whole directory: the
contact points (`01-`) must exist before the policy (`02-`) that routes to them. This
mirrors `integration-duplo-common/provisioning/01-duplo-alerting-folder.yaml`.

The `duplo-common` folder (`folderUid: duplo-common`) used by `03-` is created by
`integration-duplo-common`, which the chart applies at an earlier hook weight.

## Relationship to `integration-duplo-bootstrap`

`integration-duplo-bootstrap` ships the **base** Mimir Alertmanager config — only the
`default-receiver` and the always-on `duplo-loki-webhook` route that feeds alert history.
When this integration is enabled it loads the full config on top, which supersedes the base.

The base must keep at least one receiver: the AOS validation job checks
`.alertmanager_config.receivers | length >= 1` and fails the release if the Mimir
Alertmanager has no config at all.
