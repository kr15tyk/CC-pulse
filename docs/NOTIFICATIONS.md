# Notifications

CC Pulse separates source-content changes from operational failures.

## Public and team-facing outputs

- **Dashboard:** current changes, history, source links, and keyword alerts.
- **RSS:** content changes suitable for feed readers.
- **Webex:** genuine content updates, keyword alerts, and configured certification notices.
- **Weekly email:** deduplicated changes from the latest seven daily diffs.

NIAP announcement, event, and policy notifications identify whether an item was added, revised, deactivated, reactivated, archived, or removed.

## Internal operational outputs

- Workflow logs record every source-health failure.
- Daily status email reports degraded sources from the first failure.
- Dedicated operational email escalates a persistent failure on run three and every seventh failed run afterward.

Operational health warnings are intentionally excluded from the public dashboard and Webex.

## Keyword alerts

`WATCH_KEYWORDS` in `config.py` controls high-priority matching. Matching is performed against structured titles and descriptions where available. Keyword alerts can produce immediate Webex, webhook, and email notifications in addition to appearing in generated content outputs.

## Webex operational scripts

One-off Webex room maintenance is handled by plaintext scripts under `scripts/webex/`, each run from its own manually-dispatched workflow (checkout + setup-python + run):

- `post_maintenance_notice.py` — post a "resolved, no action needed" notice after a benign failure alert.
- `delete_failure_alerts.py` — scan the room and delete any messages matching the failure-alert marker.
- `delete_message_by_id.py` — delete one message by explicit ID (argument or `MESSAGE_ID`).

All three read `CC_WEBEX_BOT_TOKEN` / `CC_WEBEX_ROOM_ID` from the environment. They were previously base64-encoded inside their workflow YAML; keeping them as plaintext scripts makes them reviewable and scannable.

## Dry run

Set `CC_DRY_RUN=true` to suppress every outbound notification. Generated snapshots, diffs, dashboard files, RSS, and logs remain available for review.
