# Notifications

CC Pulse separates source-content changes from operational failures.

## Public and team-facing outputs

- **Dashboard:** current changes, history, source links, and keyword alerts.
- **RSS:** content changes suitable for feed readers.
- **Webex:** genuine content updates, keyword alerts, and configured certification notices.
- **Weekly email:** deduplicated changes from the latest seven daily diffs.

NIAP announcement, event, and policy notifications identify whether an item was added, revised, deactivated, reactivated, archived, or removed.

## Cisco certification notices

New Cisco certifications trigger a Webex celebration and a matching email. These are **source-aware**: the subject, header, and links name the actual registry (NIAP NDcPP PCL, CSfC Components List, NATO NIAPCL, or EUCC), and each product links directly to its own certification page — a NIAP product page, an ENISA certificate page, and so on. There is no registry-wide footer button; the per-product links and the dashboard link are the navigation.

Notices are suppressed when the source diff is flagged as a baseline reset (see Monitoring) — a mass re-detection caused by a source format change is not a wave of new certifications.

## Alert tiers

Alert emails and Webex messages sort matches into three tiers:

1. **Cisco-relevant** — a matched keyword names Cisco itself, NDcPP/PP-Modules, CSfC programmes, or FCS_* crypto SFRs. Broad standards identifiers (FIPS 140-3, FIPS 186-x, SP 800-131A) deliberately do *not* confer this tier: they appear in boilerplate (every CMVP MIP row contains "FIPS 140-3") and previously mislabeled other vendors' modules as Cisco-relevant.
2. **Standards/NIST** — standards identifiers and transition keywords (FIPS 140-3/203/204/205, CMVP, CAVP, PQC terms).
3. **General** — everything else that matched a watch keyword.

## CMVP Modules-in-Process

Only **Cisco modules** on the CMVP MIP list generate alerts (new entries and status changes, with old → new detail). All vendors' MIP movements remain visible on the dashboard; they are deliberately excluded from email/Webex because the full list churns daily.

## ND-iTC

Every detected ND-iTC change alerts unconditionally at tier 1, under two sources: **ND-iTC NIT RFI** (new RFIs, status changes, revisions, active→archived moves) and **ND-iTC Allowed-With** (entry additions/removals, object version changes, list-document version bumps). The name "NIT RFI" is used everywhere instead of "Technical Decision" so ND-iTC decisions are never confused with NIAP TDs.

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
- `delete_failure_alerts.py` — scan the room and delete any messages matching the failure-alert marker. **Group-space limitation:** the Webex API forbids bots from listing group-space messages (HTTP 403), so this scan only works in a direct space; in a group space, find the message ID with a personal access token and use `delete_message_by_id.py`.
- `delete_message_by_id.py` — delete one message by explicit ID (argument or `MESSAGE_ID`). Bots may always delete their own messages by ID.

All three read `CC_WEBEX_BOT_TOKEN` / `CC_WEBEX_ROOM_ID` from the environment. They were previously base64-encoded inside their workflow YAML; keeping them as plaintext scripts makes them reviewable and scannable.

## Dry run

Set `CC_DRY_RUN=true` to suppress every outbound notification. Generated snapshots, diffs, dashboard files, RSS, and logs remain available for review.
