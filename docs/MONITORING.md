# Monitoring

CC Pulse collects public records into dated JSON snapshots and compares each accepted snapshot with the latest prior snapshot.

## Sources

| Domain | Change detection |
|---|---|
| NIAP products | New certifications, archive transitions, removals, and in-evaluation changes |
| NIAP Protection Profiles | Additions, removals, sunset-date changes, and status changes |
| NIAP Technical Decisions | Additions and removals |
| NIAP announcements and events | Additions, revisions, deactivations, reactivations, and removals |
| NIAP policies | Active/archived transitions, metadata revisions, addendum changes, removals, and PDF SHA-256 changes |
| NSA CSfC | Components List additions/removals, announcements, and selection-link version changes |
| CC Crypto | New page items, feed items, and documents |
| CC Portal and CCTL labs | New international portal records and laboratory posts |
| EUCC | Certificate additions, removals, and Cisco-specific changes |
| NATO NIAPCL | Cisco listing additions/removals — manual-only domain: ia.nato.int blocks automated access, so the baseline is updated by a human via the weekly capture reminder and the "NATO Cisco Baseline Update" issue form (`scripts/nato_issue_intake.py`) |
| ND-iTC | NIT RFI additions, status changes, revisions, and archival; Allowed-With list entry additions/removals, version changes, and list-document updates |

ND-iTC Technical Decisions are reported as **NIT RFIs** everywhere in CC Pulse. NIAP also issues "Technical Decisions"; the distinct name prevents the two from being confused in notifications.

## NIAP announcements and policies

Announcement APIs are followed through every pagination link. Existing IDs are compared by content, so an edited announcement is reported even when its ID does not change.

Active and archived policies are collected separately because the same policy number can exist in both lists. Policy and addendum PDFs are hashed when publicly retrievable, allowing same-filename replacements to be detected. If an old archived PDF is unavailable, its API metadata remains monitored and the snapshot records the document-hash coverage gap.

## Collection safety

Each source domain is validated before diffing. NIAP announcements, events, and policies are validated independently from NIAP products so one failed endpoint does not freeze otherwise healthy NIAP data.

When a collection fails, is incomplete, or drops suspiciously:

1. The affected collection keeps its last-known-good records. This applies at three levels: a whole domain that fails its representative check, a NIAP subcollection (announcements, events, policies) validated independently, and any secondary collection — any domain's `pages`/`feeds`/top-level list — that collapses to below half its prior size from a baseline of at least `COLLAPSE_MIN_BASELINE` items. The last catches partial-fetch collapses in collections that aren't the domain's representative check (for example a NIAP `tds`/`pcl_all`/`in_evaluation` list, or an EUCC/CSfC secondary page/feed) which would otherwise pass domain-level health while emitting false removals.
2. Healthy domains continue normally; a collection collapse downgrades only the affected domain to `stale`, carrying its failure counter forward.
3. The degraded state is recorded in `source_health` metadata and workflow logs.
4. No false mass-removal diff is generated.

A collapse guard covers *volume* — a collection dropping to near-zero. It does not detect a small-but-corrupt payload that stays above the volume threshold; that class of failure is caught, where it is caught, by per-source validation, not by the collapse guard.

A newly introduced source receives a silent first healthy baseline so deployment does not create an alert flood.

## Manual domains

Domains listed in `config.MANUAL_DOMAINS` (currently NATO NIAPCL) are never collected automatically and have no fetch-based health checks. Each daily run carries the stored baseline forward unchanged and records the domain as `healthy` with `mode: manual` in `source_health`, so it never appears in the degraded-sources status email. The baseline changes only when the issue-intake workflow applies a manually captured update, which diffs against the stored baseline and fires the normal Cisco alerts itself.

## Record identity and baseline resets

EUCC certificates, NATO NIAPCL products, and ND-iTC Allowed-With entries are keyed by their stable URL (NIT RFIs by their RFI ID), with display text only as a fallback for records without a link. Display text embeds dates and descriptions, so keying on it re-detects the entire list whenever a source reformats — ENISA's card-title change on 2026-07-09 made 45 unchanged certificates look "new."

If most of a previously non-trivial list still re-registers as new (a format change that also alters URLs), the diff is flagged as a **baseline reset**: the changes remain visible on the dashboard, but no Webex, email, or keyword-alert notifications fire for that run.
