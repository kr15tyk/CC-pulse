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

## Dry run

Set `CC_DRY_RUN=true` to suppress every outbound notification. Generated snapshots, diffs, dashboard files, RSS, and logs remain available for review.
