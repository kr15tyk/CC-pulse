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
| NIST CSRC and CC Crypto | New page items, feed items, documents, and CMVP changes |
| CC Portal and CCTL labs | New international portal records and laboratory posts |
| NATO NIAPCL and EUCC | Product/certificate additions, removals, and Cisco-specific changes |

## NIAP announcements and policies

Announcement APIs are followed through every pagination link. Existing IDs are compared by content, so an edited announcement is reported even when its ID does not change.

Active and archived policies are collected separately because the same policy number can exist in both lists. Policy and addendum PDFs are hashed when publicly retrievable, allowing same-filename replacements to be detected. If an old archived PDF is unavailable, its API metadata remains monitored and the snapshot records the document-hash coverage gap.

## Collection safety

Each source domain is validated before diffing. NIAP announcements, events, and policies are validated independently from NIAP products so one failed endpoint does not freeze otherwise healthy NIAP data.

When a collection fails, is incomplete, or drops suspiciously:

1. The affected domain or NIAP subcollection keeps its last-known-good records.
2. Healthy domains continue normally.
3. The degraded state is recorded in `source_health` metadata and workflow logs.
4. No false mass-removal diff is generated.

A newly introduced source receives a silent first healthy baseline so deployment does not create an alert flood.
