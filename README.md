# CC Pulse
> **Common Criteria & NIAP Ecosystem Monitor**
>
> CC Pulse is an automated monitoring engine that tracks the "heartbeat" of the Common Criteria (CC), NIAP, CSfC, CC Crypto Catalog, and NIST CSRC ecosystems. It eliminates the manual overhead of checking disparate government portals and lab feeds by capturing daily snapshots and surfacing only meaningful changes — delivered to your dashboard, RSS reader, email, Webex, or Teams channel.

**Live dashboard:** https://kr15tyk.github.io/CC-pulse/cc_dashboard.html

---

## What it monitors

| Domain | What is tracked |
|--------|-----------------|
| **NIAP** | Full PCL (1,600+ certified products across all tech types), Protection Profiles, Technical Decisions, CCTLs, News & Events — via JSON REST APIs |
| **CSfC / NSA** | Approved Products List, 8 Capability Package PDFs (HTTP HEAD + partial hash), 7 NSA/CISA/DISA advisory feeds |
| **CC Portal** | International news, PPs, certified products — HTML scraping + RSS |
| **CCTL Labs** | New posts from 8 accredited evaluation labs — RSS feeds (reliable) and HTML scraping |
| **CC Crypto Catalog** | CCDB-018 and related PDFs, CC Portal crypto publications and community pages |
| **NIST CSRC** | 6 CSRC pages (news, FIPS, CMVP MIP, PQC, crypto-standards, CMVP validated modules), 7 key PDFs (FIPS 203/204/205, SP 800-131A, NIST IR 8547, etc.), 3 RSS feeds |

---

## How it works

The pipeline runs automatically every day at **06:00 UTC**. A weekly digest emails every **Monday at 07:00 UTC**.

```
collect → diff → dashboard → commit → alert
                                        ↓
                              Webex · Teams/Webhook · Email
```

1. **Collect** — Six domain collectors run in parallel, fetching APIs, scraping pages, polling PDF headers, and reading RSS feeds. If the NIAP data looks thin (< 50 products), the run aborts and nothing is written.
2. **Diff** — Today's snapshot is compared against yesterday's. Structured records are matched by stable IDs; scraped content is matched by text prefix. Changes are categorised (new, removed, sunset, archived, updated).
3. **Alert scan** — All structured fields (PP names, TD titles, RSS titles, product names) are scanned against `WATCH_KEYWORDS`. Raw scraped page text is scanned against the narrower `BODY_WATCH_KEYWORDS` list to avoid alert fatigue from noisy prose. Each alert carries: source label, title, what changed (`detail`), change kind, direct URL, and matched keywords.
4. **Dashboard** — The diff is rendered into `docs/cc_dashboard.html` via a Jinja2 template and committed back to `main`. GitHub Pages serves it immediately.
5. **RSS** — `docs/cc_pulse.rss` is written alongside the dashboard, covering all domain changes.
6. **Notifications** — If keyword alerts were found, three delivery channels fire in parallel: Webex Space, generic webhook (Teams-compatible), and immediate alert email. Each notification is self-contained: source, title, what changed, direct link, matched keywords. No need to open the dashboard to act on it.

---

## Notification channels

### Immediate alert (daily, keyword matches only)

| Channel | Format | Content |
|---------|--------|---------|
| **Webex Space** | Markdown message | Source, title, ↳ detail, kind, 🔗 URL, 🔑 keywords — one block per alert, with a dashboard link |
| **Teams / Webhook** | JSON `{"text": ...}` | Same content, compatible with MS Teams Incoming Webhook and Slack-style webhooks |
| **Alert email** | HTML | Colour-coded rows: source badge, linked title, detail, kind, keywords; "View Full Dashboard" button |

### Weekly digest (every Monday)

A single HTML email summarising all changes from the past 7 daily diffs, deduplicated by `source + title` so the same event is never listed twice.

---

## Dashboard

**Live:** https://kr15tyk.github.io/CC-pulse/cc_dashboard.html

The dashboard auto-refreshes daily at 06:00 UTC. It can also be regenerated on demand at any time using the `redash` workflow dispatch mode (see [Manual runs](#manual-runs)).

### Layout

Cards are organised into labelled sections matching the stat bar at the top:

- **NIAP** — Cisco NDcPP Certifications, NIAP CCTL Registry, NIAP In-Evaluation Products, NIAP News & Announcements, NIAP PCL — All Certifications, NIAP Protection Profiles, NIAP Technical Decisions
- **CCTL** — CCTL Lab Intel
- **CSfC** — CSfC Capability Packages
- **Documentation** — CC Crypto Documentation, NIST Documentation
- **CC Portal** — CC Portal (International)
- **Alerts** — keyword matches (only shown when alerts exist)

### Dashboard features

| Feature | Description |
|---------|-------------|
| **Trend bar** | 6 stat tiles (NIAP Changes, CCTL Items, CSfC CP Updates, NIST Doc Updates, CC Portal, Alerts) with anchor links |
| **Source Health panel** | Per-source status badges (green = changes, muted = quiet, red = alerts) and the covered date range |
| **Collapsible cards** | Empty sections auto-collapse; click any header to expand |
| **7-day sparklines** | Mini bar chart per card showing recent activity — hover for exact counts |
| **Colour-coded borders** | Green = new items, Amber = updates, Red = alerts/removed |
| **Clickable links** | Every item links directly to the source: NIAP product page, NIST PDF, NSA page, etc. |
| **Alert detail** | Alert rows show source, linked title, what changed (detail), kind, and matched keywords |
| **Stable layout** | Single-column flex layout — cards never reflow or shift when the window is resized |
| **RSS feed** | `cc_pulse.rss` covers all domains; subscribe in any feed reader |
| **Footer** | "Auto-refreshes daily (06:00 UTC) · Data from NIAP, CSfC, NIST, CC Portal · Last run: ..." |

### Alert banner

When keyword alerts exist, an amber banner appears at the top: *"N keyword alerts — see Alerts section below."* Deliberately non-alarming — the Alerts card at the bottom of the page contains the full detail.

---

## Project structure

```
CC-pulse/
├── .github/
│   ├── workflows/
│   │   └── cc_pulse.yml        # GitHub Actions: daily, weekly, bootstrap, redash
│   └── dependabot.yml          # Automated dependency updates
├── snapshots/                  # Auto-created; daily JSON snapshots (30-day rotation)
│   └── diffs/                  # Daily diff JSONs (used by weekly merge)
├── docs/                       # Auto-created; served by GitHub Pages
│   ├── cc_dashboard.html       # Live HTML dashboard
│   └── cc_pulse.rss            # RSS feed (all domains)
├── collector.py                # Parallel multi-source aggregator
├── differ.py                   # Diff engine + two-tier keyword alert scanner
├── dashboard.py                # Jinja2 HTML dashboard + RSS renderer
├── emailer.py                  # Alert email, weekly digest, Webex, webhook
├── main.py                     # Entry point: daily / weekly / bootstrap / redash
├── config.py                   # All configuration (URLs, keywords, thresholds)
├── requirements.txt            # Pinned Python dependencies
├── .env.example                # Local dev template — copy to .env
└── README.md
```

---

## Deployment

### Prerequisites

- Python **3.10+** (3.11 recommended)
- A GitHub account (free tier is sufficient)
- An SMTP email account for sending digests (Gmail with App Password recommended)
- Optional: Webex Bot Token + Room ID for real-time Webex alerts
- Optional: Teams Incoming Webhook URL (or any generic JSON webhook)

### Step 1 — Fork / Clone

```bash
git clone https://github.com/kr15tyk/CC-pulse.git
cd CC-pulse
pip install -r requirements.txt
```

### Step 2 — Configure your local environment

```bash
cp .env.example .env
# Edit .env — fill in your email credentials and optionally Webex/webhook
```

| Variable | Description |
|----------|-------------|
| `CC_SMTP_HOST` | SMTP server hostname (default: `smtp.gmail.com`) |
| `CC_EMAIL_USERNAME` | Sender email address |
| `CC_EMAIL_PASSWORD` | SMTP password / Gmail App Password |
| `CC_EMAIL_FROM` | Display name + address for From header |
| `CC_EMAIL_RECIPIENTS` | Comma-separated recipient list |
| `CC_WEBEX_BOT_TOKEN` | Webex Bot token (optional) |
| `CC_WEBEX_ROOM_ID` | Webex Space ID (optional) |
| `CC_WEBHOOK_URL` | Teams Incoming Webhook URL or generic JSON webhook (optional) |
| `CC_LOG_LEVEL` | `DEBUG` / `INFO` / `WARNING` / `ERROR` |

### Step 3 — Bootstrap the initial snapshot

```bash
export $(cat .env | xargs)
python main.py --bootstrap
```

This collects the first snapshot without producing a diff or dashboard. The next daily run diffs against it.

### Step 4 — Add GitHub Actions secrets

Go to **Settings → Secrets and variables → Actions → New repository secret**:

| Secret name | Value |
|-------------|-------|
| `CC_EMAIL_PASSWORD` | SMTP password / Gmail App Password |
| `CC_EMAIL_USERNAME` | Sender email address |
| `CC_EMAIL_RECIPIENTS` | Comma-separated recipient list |
| `CC_SMTP_HOST` | SMTP server (e.g. `smtp.gmail.com`) |
| `CC_WEBEX_BOT_TOKEN` | *(optional)* Webex Bot token |
| `CC_WEBEX_ROOM_ID` | *(optional)* Webex Space Room ID |
| `CC_WEBHOOK_URL` | *(optional)* Teams Incoming Webhook or generic JSON webhook URL |

> **Gmail users:** Use an [App Password](https://support.google.com/accounts/answer/185833), not your regular account password. Enable 2FA first, then generate an App Password under Security → 2-Step Verification → App passwords.

### Step 5 — Enable GitHub Pages

1. Go to **Settings → Pages**
2. Set **Source** to "Deploy from a branch"
3. Branch: `main`, Folder: `/docs`
4. Click **Save**

Your dashboard will be live at `https://<your-username>.github.io/<repo-name>/cc_dashboard.html`.

### Step 6 — Trigger the first automated run

The workflow fires automatically at 06:00 UTC daily. To trigger manually:

1. Go to **Actions → CC Pulse — Daily & Weekly Run → Run workflow**
2. Select mode: `daily` (or `redash` to regenerate the dashboard only — see below)

---

## Automation schedule

| Schedule | Mode | What happens |
|----------|------|--------------|
| Daily at 06:00 UTC | `daily` | Collect → Diff → Dashboard → Commit → Alert (if keywords matched) |
| Monday at 07:00 UTC | `weekly` | Merge last 7 diffs → Send weekly email digest |

---

## Manual runs

All four modes are available via **Actions → CC Pulse — Daily & Weekly Run → Run workflow**:

| Mode | Use when |
|------|----------|
| `daily` | Force a full collect + diff + dashboard cycle |
| `weekly` | Send the weekly email digest immediately |
| `bootstrap` | First-time setup — collects baseline snapshot, no diff |
| `redash` | Re-render the dashboard and RSS from the latest diff without collecting new data — useful after template or config changes |

---

## Alert schema

Each alert object in the diff JSON and in all notifications has these fields:

```json
{
  "source": "NIAP PP",
  "kind": "new",
  "title": "NDcPP v3.0 — Network Device collaborative PP",
  "url": "https://www.niap-ccevs.org/Profile/PP.cfm?id=1234",
  "detail": "New Protection Profile · Tech type: Network",
  "matched_keywords": ["NDcPP", "NDcPP v3.0"]
}
```

`kind` values: `new`, `removed`, `sunset`, `new_cert`, `new_evaluation`, `updated`, `advisory`, `publication`, `news`, `post`.

### Two-tier keyword scanning

- **Structured fields** (PP names, TD titles, RSS item titles, product names, news headlines) are scanned against the full `WATCH_KEYWORDS` list — high-confidence matches.
- **Scraped page blobs** (NSA page text, NIST CSRC paragraphs, CC Portal prose) are scanned against the narrower `BODY_WATCH_KEYWORDS` list — prevents alert fatigue from common terms appearing in background page text.

PDF header changes (ETag / Last-Modified / Content-Length drift) are shown in the Documentation cards as low-priority informational entries but do **not** trigger keyword alerts. Alerts only fire when something is actually published to a structured feed, RSS item, or API record.

---

## Diff schema

The daily diff JSON at `snapshots/diffs/YYYY-MM-DD_diff.json`:

```json
{
  "period_start": "<ISO timestamp of old snapshot>",
  "period_end":   "<ISO timestamp of new snapshot>",
  "niap": {
    "pps":          { "added": [], "removed": [], "sunset_changes": [], "status_changes": [] },
    "tds":          { "added": [], "removed": [] },
    "cisco_ndcpp":  { "added": [], "removed": [], "newly_archived": [] },
    "pcl_all":      { "added": [], "removed": [], "newly_archived": [] },
    "in_evaluation":{ "added": [], "removed": [], "current_count": 0 },
    "news":         { "added": [] },
    "events":       { "added": [] },
    "cctls":        { "added": [], "removed": [], "status_changes": [] }
  },
  "cc_portal": {
    "news":     { "added": [] },
    "pps":      { "added": [] },
    "products": { "added": [] }
  },
  "cctl_labs": {
    "<lab_name>": [{ "title": "", "link": "", "published": "" }]
  },
  "csfc": {
    "capability_packages": {},
    "feeds": {},
    "pages": {}
  },
  "cc_crypto": {
    "doc_headers": {},
    "pages": {}
  },
  "nist": {
    "doc_headers": {},
    "pages": {},
    "feeds": {}
  },
  "alerts": [
    {
      "source": "",
      "kind": "",
      "title": "",
      "url": "",
      "detail": "",
      "matched_keywords": []
    }
  ]
}
```

---

## Configuration reference

### Keywords

| Config key | Scanned against | Purpose |
|-----------|----------------|---------|
| `WATCH_KEYWORDS` | Structured fields (titles, identifiers, feed items) | Triggers alerts + Webex + email |
| `BODY_WATCH_KEYWORDS` | Raw scraped page text | Narrower list; avoids prose false-positives |
| `NEWS_CATEGORY_KEYWORDS` | News/feed item titles | Routes items to categories (LABGRAM, VALGRAM, CSfC, POLICY, CRYPTO, NIST, etc.) |
| `CISCO_VENDOR_KEYWORDS` | PCL vendor name field | Filters Cisco products for the NDcPP card |
| `NDCPP_PP_KEYWORDS` | PP short name field | Identifies NDcPP-family PPs |

### Sanity thresholds

| Config key | Default | Behaviour |
|-----------|---------|-----------|
| `SANITY_MIN_PCL` | 50 | **Fatal** — rejects snapshot if NIAP PCL returns fewer products |
| `SANITY_MIN_PPS` | 10 | **Fatal** — rejects snapshot if NIAP PP list looks empty |
| `SANITY_MIN_CSFC_APL` | 5 | Warn-only — NSA site may block bots |
| `SANITY_MIN_CC_CRYPTO_PUBS` | 5 | Warn-only — CC Portal publications page |
| `SANITY_MIN_NIST_NEWS` | 10 | Warn-only — NIST CSRC news page |

---

## Running locally

```bash
# Full daily run (collect + diff + dashboard)
export $(cat .env | xargs)
python main.py

# Re-render dashboard from latest diff only (no collection)
python main.py --redash

# Send weekly email digest from stored diff files
python main.py --weekly

# Bootstrap first snapshot (no diff)
python main.py --bootstrap

# Verbose debug output
CC_LOG_LEVEL=DEBUG python main.py
```

---

## Security notes

- **Secrets:** Never commit `.env` or credentials. All secrets go in GitHub Actions Secrets.
- **Permissions:** The workflow uses `contents: write` only — no admin access.
- **Outbound only:** CC Pulse makes read-only HTTP calls to public government websites. No credentials are sent to external sites.
- **Supply chain:** All GitHub Actions are pinned to commit SHAs. Dependabot monitors both pip dependencies and Actions for security updates.
- **Bot governance:** If deploying a Webex Bot in a corporate environment, register it in your organisation's Bot Registration Portal to comply with IT policies.

---

## License

MIT
