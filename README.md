# CC Pulse — What You're Seeing

**CC Pulse** is an automated monitor that watches government and international cybersecurity certification portals so you don't have to. Every day it checks for changes and pushes a summary to this Webex space and the live dashboard.

**Live dashboard:** https://kr15tyk.github.io/CC-pulse/cc_dashboard.html

---

## What Does It Watch?

CC Pulse monitors these sources daily:

| Source | What's tracked |
|---|---|
| **NIAP** | Certified products (PCL), Protection Profiles, Technical Decisions, CCTLs, and news/events |
| **CSfC / NSA** | Approved Products List and Capability Package documents |
| **CC Portal** | International CC news, Protection Profiles, and certified products |
| **CCTL Labs** | New posts from accredited Common Criteria evaluation labs |
| **NIST CSRC** | Cryptography news, FIPS publications, CMVP, and post-quantum standards |

It runs automatically every day at **06:00 UTC** and posts to this space only when something relevant is found.

---

## What You See in Webex

When CC Pulse finds something relevant, it posts a message to this space. Here's how to read it.

**The message header** tells you the tier breakdown — for example:

> 🔵 2 Cisco-relevant · 📐 1 standards/NIST

**Each alert in the message shows:**

- **[KIND]** — what type of change it is (see kinds below)
- **Source** — which portal it came from (e.g., NIAP PP, NIST CSRC)
- **Title** — the name of the item that changed
- **↳ Detail** — a short description of what changed
- **🔗 URL** — a direct link to the source page
- **🔑 Keywords** — which tracked terms matched (e.g., NDcPP, CSfC, FIPS 204)

**Cisco-relevant alerts appear first** (🔵), followed by standards/NIST items (📐), then general CC changes.

**Change kinds you'll see:**

| Kind | Meaning |
|---|---|
| `new` | A new item appeared (new PP, new TD, new news item) |
| `new_cert` | A product was newly certified on the NIAP PCL |
| `new_evaluation` | A product entered the NIAP in-evaluation list |
| `removed` | An item was removed from a list |
| `sunset` | A Protection Profile has been sunsetted |
| `archived` | A certified product has been archived |
| `updated` | An existing item was modified |
| `advisory` | A new advisory or policy was published |
| `publication` | A new document or standard was published |

---

## Cisco Certification Celebration 🏆

If a **new Cisco product is certified on the NIAP PCL**, the space gets a dedicated celebration message — separate from the regular alert. It includes:

- Product name (linked to the NIAP page), vendor, and certification date
- Valid-until date, evaluated Protection Profiles, and evaluating lab
- A rotating celebration image

This fires whenever a new Cisco cert lands, regardless of whether anything else changed that day.

A matching email is sent to all distribution list recipients at the same time.

---

## The Dashboard

The dashboard at https://kr15tyk.github.io/CC-pulse/cc_dashboard.html refreshes automatically after each daily run.

### The Stat Bar (Top Row)

Six tiles show a count of changes detected that day:

| Tile | What it counts |
|---|---|
| **NIAP Changes** | Total changes across all NIAP sources |
| **CCTL Items** | New posts from evaluation labs |
| **CSfC CP Updates** | Changes to NSA Capability Package documents |
| **NIST Doc Updates** | Changes to NIST publications or pages |
| **CC Portal** | Changes from the international CC Portal |
| **Alerts** | Number of keyword matches (Cisco/relevant items) |

Tiles with a count are highlighted in blue. The **Alerts** tile is highlighted in orange when keyword matches exist.

### Source Health Panel

Just below the stat bar, the **Source Health** panel shows whether each source reported changes or not, and confirms when all sources were last polled. This lets you see at a glance if a particular source was quiet vs. if it simply wasn't reached.

### The Cards

Below Source Health, the page is divided into collapsible cards, one per data area. Cards with changes are expanded by default; quiet cards are collapsed. You can click any card header to expand or collapse it.

**What you'll find in the cards:**

- **NIAP Cisco NDcPP Certifications** — New or archived Cisco NDcPP products
- **NIAP CCTL Registry** — Changes to the list of accredited evaluation labs
- **NIAP In-Evaluation Products** — Products that newly entered NIAP evaluation
- **NIAP News & Announcements** — Policy letters, events, and news from NIAP
- **NIAP PCL — All Certifications** — New certifications and archived products across all vendors
- **NIAP Protection Profiles** — New, updated, or sunsetted PPs
- **NIAP Technical Decisions** — New or updated TDs
- **CCTL Lab Intel** — New posts from evaluation labs
- **CSfC Capability Packages** — Document changes from NSA
- **CC Crypto Documentation** — Changes to CC cryptography documents
- **NIST Documentation** — Changes to NIST FIPS, SP, and CMVP publications
- **CC Portal (International)** — International CC news and certified products
- **Alerts** — Full list of keyword-matched items (only appears when alerts exist)

### Colour Coding

Card borders are colour-coded by what's inside:

- **Green border** — new items
- **Amber border** — updates to existing items
- **Red/magenta border** — keyword alerts

### Alert Banner

When keyword matches exist, an amber banner appears at the very top of the dashboard:

> *"N keyword alerts — see Alerts section below."*

This is intentionally low-key — it's a pointer, not an alarm. The full detail is in the Alerts card at the bottom.

### Searching and Filtering

You can use the search bar to filter by keyword, or use the filter chips (New / Removed / Updated / Archived / Alert) to narrow by change type. Keyboard shortcuts: **E** expands all cards, **C** collapses all, **F** toggles hiding of empty cards, **/** focuses the search bar.

---

## Weekly Digest Email

Every **Monday morning**, a digest email goes out covering all changes from the past 7 daily runs, deduplicated — so if the same item changed multiple days in a row, it only appears once.

---

## Questions?

If you see something in Webex or on the dashboard and want to know more, click the direct link in the alert — it goes straight to the source page (NIAP, NIST, NSA, etc.). No login required.
