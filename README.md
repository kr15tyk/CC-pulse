# CC Pulse — What You're Seeing

**CC Pulse** is an automated monitor that watches government and international cybersecurity certification portals so you don't have to. Every day it checks for changes and pushes a summary to this Webex space and the live dashboard.

**Live dashboard:** https://kr15tyk.github.io/CC-pulse/cc_dashboard.html

---

## What Does It Watch?

CC Pulse monitors these sources daily:

| Source | What's tracked |
|---|---|
| **NIAP** | Certified products (PCL), Protection Profiles, Technical Decisions, CCTLs, and news/events |
| **CSfC / NSA** | Approved Products List and Component Selection documents |
| **NATO NIAPCL** | NATO Information Assurance Product Catalogue — certified products and components |
| **EUCC / ENISA** | EU Common Criteria certification scheme — requirements pages and issued certificates |
| **CC Portal** | International CC news, Protection Profiles, and certified products |
| **CCTL Labs** | New posts from accredited Common Criteria evaluation labs |
| **NIST CSRC** | Cryptography news, FIPS publications, CMVP, and post-quantum standards |

It runs automatically every day at **01:00 EST** and posts to this space only when something relevant is found.

---

## What You See in Webex

When CC Pulse finds something relevant, it posts a message to this space. Here's how to read it.

The message header tells you the tier breakdown — for example: 🔵 2 Cisco-relevant · 📐 1 standards/NIST

Each alert in the message shows:

- **[KIND]** — what type of change it is (see kinds below)
- **Source** — which portal it came from (e.g., NIAP PP, NIST CSRC, NATO NIAPCL, EUCC)
- **Title** — the name of the item that changed
- **↳ Detail** — a short description of what changed
- **🔗 URL** — a direct link to the source page
- **🔑 Keywords** — which tracked terms matched (e.g., NDcPP, CSfC, FIPS 204)

Cisco-relevant alerts appear first (🔵), followed by standards/NIST items (📐), then general CC changes.

**Change kinds you'll see:**

| Kind | Meaning |
|------|---------|
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

If a new Cisco product is certified on the NIAP PCL, CSfC Approved Products List, NATO NIAPCL, or EUCC, the space gets a dedicated celebration message — separate from the regular alert. It includes:

- Product name (linked to the source page), vendor, and certification date
- Evaluated Protection Profiles / scheme details and evaluating lab (where available)
- A rotating celebration image

This fires whenever a new Cisco cert lands on any of those four lists, regardless of whether anything else changed that day. A matching email is sent to all distribution list recipients at the same time.

---

## The Dashboard

The dashboard at https://kr15tyk.github.io/CC-pulse/cc_dashboard.html refreshes automatically after each daily run.

### Navigation Bar

The nav bar shows the **CC Pulse Dashboard** title and the **Last run** timestamp (in ET) on the right. A **hamburger menu (☰)** in the top-right corner opens a dropdown with:

- 🏠 **Home** — scroll to the top of the page
- 🌙/☀️ **Dark Mode / Light Mode** — toggle dark/light theme (persists via localStorage)
- 📋 **Source List** — opens a modal listing all monitored source URLs
- 📡 **Copy RSS URL** — copies the dashboard RSS feed URL to your clipboard

### The Stat Bar (Top Row)

Eight tiles show a count of changes detected that day:

| Tile | What it counts |
|------|---------------|
| NIAP Changes | Total changes across all NIAP sources |
| CSfC Selection Updates | Changes to NSA Component Selection documents |
| NIST Doc Updates | Changes to NIST publications or pages |
| CC Portal | Changes from the international CC Portal |
| NATO NIAPCL | Changes to the NATO Information Assurance Product Catalogue |
| CCTL Updates | New posts from accredited Common Criteria evaluation labs |
| EUCC Changes | Changes to EUCC requirements and issued certificates |
| Alerts | Number of keyword matches (Cisco/relevant items) |

Tiles with a count are highlighted in blue. The Alerts tile is highlighted in orange when keyword matches exist. Zero-count tiles are de-emphasized (dimmed).

### Tabbed Navigation

Below the stat bar, the dashboard is split into four tabs:

| Tab | What's in it |
|-----|-------------|
| 🇺🇸 US (NIAP / CSfC / NIST) | All NIAP, CSfC, NIST, and CC Portal cards |
| 🌐 International | NATO NIAPCL, EUCC, CC Portal, and CCTL Updates cards |
| 📅 History | Full change timeline across all sources |
| ⚡ Alerts | Keyword-matched items (only appears when alerts exist) |

Tab buttons show a change-count badge when there is activity in that tab (e.g., 🌐 International (3)).

### Controls

Below the stat bar and tabs you'll find:

- **[E] Expand All** / **[C] Collapse All** — expand or collapse all cards
- **[F] Hide Empty** — toggle hiding of cards with no changes
- **⌨ Keys** — collapsible tooltip showing all keyboard shortcuts
- **Search bar** — filter cards by keyword
- **Filter chips** — narrow by change type: New / Removed / Updated / Archived / Alert

**Keyboard shortcuts:** `E` expands all cards, `C` collapses all, `F` toggles hiding of empty cards, `/` focuses the search bar.

### The Cards

Below the tab bar, the page is divided into collapsible cards, one per data area. Cards with changes are expanded by default; quiet cards are collapsed. You can click any card header to expand or collapse it.

What you'll find in the cards:

- **NIAP Cisco NDcPP Certifications** — New or archived Cisco NDcPP products
- **NIAP In-Evaluation Products** — Products that newly entered NIAP evaluation
- **NIAP News & Announcements** — Policy letters, events, and news from NIAP
- **NIAP PCL — All Certifications** — New certifications and archived products across all vendors
- **NIAP Protection Profiles** — New, updated, or sunsetted PPs
- **NIAP Technical Decisions** — New or updated TDs
- **CSfC Component Selections** — Changes to NSA Component Selection PDFs
- **CC Crypto Documentation** — Changes to CC cryptography documents
- **NIST Documentation** — Changes to NIST FIPS, SP, and CMVP publications
- **CC Portal (International)** — International CC news and certified products
- **NATO NIAPCL** — New, removed, or changed NATO-certified products (International tab)
- **CCTL Updates** — New posts from evaluation labs (International tab)
- **EUCC Certificates** — New or changed EUCC-certified products (International tab)
- **Alerts** — Full list of keyword-matched items (only appears when alerts exist)

### Colour Coding

Card borders are colour-coded by what's inside:

- **Green border** — new items
- **Amber border** — updates to existing items
- **Red/magenta border** — keyword alerts

### Alert Banner

When keyword matches exist, an amber banner appears at the very top of the dashboard: "N keyword alerts — see Alerts section below." This is intentionally low-key — it's a pointer, not an alarm. The full detail is in the Alerts card.

### Alerts Card — Mark All Seen

The Alerts card shows a **"Mark All Seen"** button when there are unseen alerts. Clicking it marks all current alerts as seen (stored in localStorage) so they no longer appear as new on your next visit.

### History Tab

The History tab shows a full timeline of all changes across recent daily runs. By default it shows the **last 7 days**; use the **All History** / **Last 7 Days** navigation buttons to switch views. Entries that are new since your last visit are tagged with a **NEW** badge.

---

## Weekly Digest Email

Every Monday morning, a digest email goes out covering all changes from the past 7 daily runs, deduplicated — so if the same item changed multiple days in a row, it only appears once.

---

## Questions?

If you see something in Webex or on the dashboard and want to know more, click the direct link in the alert — it goes straight to the source page (NIAP, NIST, NSA, NATO, ENISA, etc.). No login required.
