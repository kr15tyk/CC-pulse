# CC Pulse
> **Real-Time Certification Intelligence & Differential Analytics**
>
> CC Pulse is an automated monitoring engine that tracks the "heartbeat" of the Common Criteria (CC), NIAP, CSfC, CC Crypto Catalog, and NIST CSRC ecosystems. It eliminates the manual overhead of monitoring disparate government portals and lab feeds by capturing daily snapshots and surfacing only meaningful changes.

**Live dashboard:** https://kr15tyk.github.io/CC-pulse/cc_dashboard.html

---

## System Overview

CC Pulse operates as a stateful monitoring pipeline:

```
collect -> diff -> dashboard -> alert -> (weekly) email
```

Every day at **06:00 UTC**, GitHub Actions runs the pipeline. Every **Monday at 07:00 UTC**, a weekly email digest is sent automatically.

### Project Structure

```
CC-pulse/
|-- .github/
|   |-- workflows/
|   |   +-- cc_pulse.yml        # GitHub Actions scheduler (daily + weekly)
|   +-- dependabot.yml          # Automated dependency updates
|-- snapshots/                  # Auto-created; daily JSON snapshots (30-day rotation)
|   +-- diffs/                  # Daily diff JSONs (used by weekly merge)
|-- docs/                       # Auto-created; served by GitHub Pages
|   |-- cc_dashboard.html       # Live HTML dashboard
|   +-- cc_pulse.rss            # RSS feed (all domains)
|-- collector.py                # Multi-source aggregator (parallel I/O)
|-- differ.py                   # Diff engine + keyword alert scanner
|-- dashboard.py                # HTML dashboard + RSS renderer
|-- emailer.py                  # Weekly email + immediate alert email builder
|-- main.py                     # Entry point -- daily / weekly / bootstrap modes
|-- config.py                   # All configuration (URLs, keywords, thresholds)
|-- requirements.txt            # Pinned Python dependencies
|-- .env.example                # Local dev template -- copy to .env
+-- README.md
```

---

## Core Components

**Collector (`collector.py`)** -- Parallel HTTP aggregator using `ThreadPoolExecutor`. Six domain collectors run concurrently: NIAP APIs, CC Portal, CCTL lab RSS feeds, NSA CSfC pages, CC Crypto Catalog, and NIST CSRC. Includes exponential-backoff retry and a partial-GET content-hash fallback for PDF polling when servers do not serve `Last-Modified`/`ETag` headers.

**Differ (`differ.py`)** -- Compares two snapshots to produce a structured daily diff. Tracks changes across all NIAP domains (PPs, TDs, full PCL across all tech types, CCTLs, News, Events), CC Portal international items, CCTL lab posts, CSfC pages and CPs, CC Crypto docs, and NIST CSRC. Scans all added text against `WATCH_KEYWORDS` to produce actionable alerts. Weekly merge uses `source+title` deduplication so the same event is not reported twice.

**Dashboard (`dashboard.py`)** -- Jinja2-powered HTML dashboard. Features: collapsible cards (auto-collapsed when empty), 7-day sparklines per section, colour-coded section headers (green=new, amber=updated, red=removed/alert), clickable source links, CSfC CP content-length change display, 6-stat trend bar with anchor links, CCTL lab post counts with expand-on-click, and mobile-responsive grid layout. Also writes an RSS feed (`cc_pulse.rss`) covering all domains.

**Email + Webex (`emailer.py`)** -- Two delivery modes: immediate keyword-alert email (fired same day), and weekly HTML digest (sent automatically every Monday). Webex Space notification also fires on daily keyword matches.

**Orchestrator (`main.py`)** -- Three run modes: `daily` (collect/diff/alert), `weekly` (merge + email), `bootstrap` (first-run snapshot with no diff). Includes double-run guard and 30-day snapshot rotation. On `first_run`, all change lists are suppressed so the baseline does not flood the dashboard.

---

## Monitored Sources

| Domain | Sources | Change Signal |
|--------|---------|---------------|
| **NIAP** | PCL (all 1,600+ certified products across all tech types), Cisco NDcPP subset, PPs, TDs, CCTLs, News, Events APIs | New/removed/archived certifications; PP adds/sunsets/status; TD adds/supersessions; CCTL lab status changes; announcements |
| **CC Portal** | News, PPs, Products RSS | New international certifications, PPs, and news items |
| **CCTL Labs** | 8 lab RSS feeds + scraped sites | New blog posts and lab announcements |
| **CSfC** | 7 NSA pages (incl. Announcements), 8 CP PDFs (HEAD+hash), NSA/CISA/DISA feeds | CP revisions, CSfC announcements, advisories |
| **CC Crypto Catalog** | CCDB-018 + related PDFs (HEAD+hash), CC Portal crypto pages | New PDF versions, CCDB announcements |
| **NIST CSRC** | 6 CSRC pages (news, FIPS, CMVP MIP, PQC, crypto-standards, CMVP validated modules), 7 PDFs (HEAD+hash), 3 RSS feeds | New FIPS/SP 800, CMVP MIP entries, PQC milestones, CMVP validation changes |

### Dashboard Cards

| Card | Diff Key | What it shows |
|------|----------|---------------|
| NIAP Protection Profiles | `niap.pps` | Added, removed, sunset-date changes, status changes -- with PP title and tech type |
| NIAP Technical Decisions | `niap.tds` | Added and removed TDs -- with full title and which PP(s) the TD applies to |
| Cisco NDcPP Certifications | `niap.cisco_ndcpp` | New, removed, and newly-archived Cisco NDcPP products -- with PP short names |
| NIAP In-Evaluation Products | `niap.in_evaluation` | Products currently under evaluation (22 active); alerts on newly added or removed entries |
| NIAP PCL -- All Certifications | `niap.pcl_all` | New, archived, and removed certified products across **all** NIAP tech types |
| NIAP News & Announcements | `niap.news` + `niap.events` | NIAP news posts and event announcements, categorised (LABGRAM, VALGRAM, CSfC, POLICY, etc.); Policy Letters sub-section for PDF policy items |
| NIAP CCTL Registry | `niap.cctls` | Lab additions, removals, and accreditation status changes |
| CCTL Lab Intel | `cctl_labs` | New blog/news posts from each accredited lab, with per-lab post counts |
| CSfC Capability Packages | `csfc.capability_packages` | Updated CPs with old vs. new content size |
| CC Portal (International) | `cc_portal` | International news, PPs, and certified product additions from the CC Portal |
| CC Crypto Documentation | `cc_crypto` | Updated CC Crypto Catalog PDFs and related page changes |
| NIST Documentation | `nist` | NIST CSRC page additions and document header changes (FIPS, SP 800, PQC) |
| Alerts | `alerts` | All items whose title matched a `WATCH_KEYWORD` -- also triggers Webex + email |

---

## Deployment

### Prerequisites

- Python **3.10+** (3.11 recommended)
- A GitHub account (free tier is sufficient)
- An SMTP email account for sending digests (Gmail with App Password recommended)
- Optional: Webex Bot Token + Room ID for real-time Slack-style alerts

### Step 1 -- Fork / Clone the repository

```bash
git clone https://github.com/kr15tyk/CC-pulse.git
cd CC-pulse
pip install -r requirements.txt
```

### Step 2 -- Configure your local environment

```bash
cp .env.example .env
# Edit .env -- fill in your email credentials and optionally Webex tokens
```

Key variables in `.env`:

| Variable | Description |
|----------|-------------|
| `CC_SMTP_HOST` | SMTP server hostname (default: `smtp.gmail.com`) |
| `CC_EMAIL_USERNAME` | Sender email address |
| `CC_EMAIL_PASSWORD` | SMTP password / Gmail App Password |
| `CC_EMAIL_FROM` | Display name + address for From header |
| `CC_EMAIL_RECIPIENTS` | Comma-separated recipient list |
| `CC_WEBEX_BOT_TOKEN` | Webex Bot token (optional) |
| `CC_WEBEX_ROOM_ID` | Webex Space ID (optional) |
| `CC_LOG_LEVEL` | `DEBUG` / `INFO` / `WARNING` / `ERROR` |

### Step 3 -- Bootstrap the initial snapshot

```bash
export $(cat .env | xargs)
python main.py --bootstrap
```

This collects the first snapshot without producing a diff or dashboard. The next daily run will diff against it.

### Step 4 -- Add GitHub Actions secrets

In your GitHub repo, go to **Settings -> Secrets and variables -> Actions -> New repository secret** and add:

| Secret name | Value |
|-------------|-------|
| `CC_EMAIL_PASSWORD` | Your SMTP password / Gmail App Password |
| `CC_EMAIL_USERNAME` | Sender email address |
| `CC_EMAIL_RECIPIENTS` | Comma-separated recipient list |
| `CC_SMTP_HOST` | SMTP server (e.g. `smtp.gmail.com`) |
| `CC_WEBEX_BOT_TOKEN` | *(optional)* Webex Bot token |
| `CC_WEBEX_ROOM_ID` | *(optional)* Webex Space Room ID |

> **Gmail users**: You must use an [App Password](https://support.google.com/accounts/answer/185833), not your regular account password. Enable 2FA first, then generate an App Password under "Security -> 2-Step Verification -> App passwords".

### Step 5 -- Enable GitHub Pages

1. Go to **Settings -> Pages**
2. Set **Source** to "Deploy from a branch"
3. Branch: `main`, Folder: `/docs`
4. Click **Save**

Your dashboard will be live at `https://<your-username>.github.io/<repo-name>/cc_dashboard.html`.

### Step 6 -- Trigger the first automated run

The workflow fires automatically at 06:00 UTC daily. To trigger manually:

1. Go to **Actions -> CC Pulse -- Daily & Weekly Run -> Run workflow**
2. Select mode: `daily` (or `bootstrap` if you skipped Step 3)

---

## Automation Schedule

| Schedule | Mode | What happens |
|----------|------|--------------|
| Daily at 06:00 UTC | `daily` | Collect -> Diff -> Dashboard -> Commit -> Alert (if keywords) |
| Monday at 07:00 UTC | `weekly` | Merge last 7 diffs -> Send email digest |

Both schedules are defined in `.github/workflows/cc_pulse.yml`. Manual dispatch supports `daily`, `weekly`, and `bootstrap` modes.

---

## Dashboard Features

- **Trend bar** -- 6 stat tiles (NIAP Changes, CCTL Items, CSfC CP Updates, NIST Doc Updates, CC Portal, Alerts) with anchor links to the relevant card
- **Collapsible cards** -- Empty sections auto-collapse; click any header to expand/collapse
- **7-day sparklines** -- Per-card mini bar chart showing recent activity history
- **Colour-coded headers** -- Green border = new items, Amber = updates, Red = alerts
- **Clickable links** -- PP names, TD identifiers, product names link directly to the source
- **Rich context** -- TDs show which PP(s) they apply to; certifications show PP short names
- **CSfC CP size diff** -- Shows content-length change when Last-Modified header is unavailable
- **RSS feed** -- `cc_pulse.rss` covers all domains; suitable for RSS readers or downstream tooling
- **Mobile responsive** -- Auto-fit grid scales from single column on mobile to multi-column on desktop

---

## Diff Schema

The daily diff JSON written to `snapshots/diffs/YYYY-MM-DD_diff.json` has this top-level structure:

```json
{
  "period_start": "<ISO timestamp of old snapshot>",
  "period_end":   "<ISO timestamp of new snapshot>",
  "niap": {
    "pps":          { "added": [], "removed": [], "sunset_changes": [], "status_changes": [] },
    "tds":          { "added": [], "removed": [] },
    "cisco_ndcpp":  { "added": [], "removed": [], "newly_archived": [] },
    "pcl_all":      { "added": [], "removed": [], "newly_archived": [] },
    "news":         { "added": [] },
    "events":       { "added": [] },
    "cctls":        { "added": [], "removed": [], "status_changes": [] },
    "in_evaluation": { "added": [], "removed": [], "current_count": 0 }
  },
  "cc_portal":  { "news": { "added": [] }, "pps": { "added": [] }, "products": { "added": [] } },
  "cctl_labs":  { "<lab_name>": [ { "title": "", "link": "", "published": "" } ] },
  "csfc":       { "capability_packages": {}, "feeds": {}, "pages": {} },
  "cc_crypto":  { "doc_headers": {}, "pages": {} },
  "nist":       { "doc_headers": {}, "pages": {}, "feeds": {} },
  "alerts":     [ { "source": "", "kind": "", "title": "", "matched_keywords": [] } ]
}
```

---

## Configuration Reference

### Monitored Sources

| Config key | Description |
|-----------|-------------|
| `NIAP_ENDPOINTS` | NIAP REST API routes |
| `CC_PORTAL_PAGES` | CC Portal pages to scrape |
| `CCTL_LABS` | Lab RSS feeds and scrape targets |
| `CSFC_PAGES` | NSA CSfC pages to snapshot |
| `CSFC_CAPABILITY_PACKAGES` | CP PDF URLs to HEAD-poll |
| `CSFC_FEEDS` | NSA/CISA/DISA advisory feeds |
| `CC_CRYPTO_DOCS` | CC Crypto Catalog PDF URLs |
| `CC_CRYPTO_PAGES` | CC Portal crypto-relevant pages |
| `NIST_CSRC_PAGES` | NIST CSRC pages (news, FIPS, CMVP MIP, PQC, crypto-standards, CMVP validated modules) |
| `NIST_CRYPTO_DOCS` | NIST PDF URLs (FIPS 140-3, SP 800-131A, FIPS 203-205, etc.) |
| `NIST_FEEDS` | NIST cybersecurity RSS feeds |

### Alerts & Keywords

| Config key | Description |
|-----------|-------------|
| `WATCH_KEYWORDS` | High-priority alert terms -- matches trigger Webex + email + dashboard banner |
| `NEWS_CATEGORY_KEYWORDS` | Category routing for news items (LABGRAM, VALGRAM, CSfC, CRYPTO, NIST, etc.) |
| `CISCO_VENDOR_KEYWORDS` | Vendor filter for Cisco NDcPP PCL tracking |
| `NDCPP_PP_KEYWORDS` | PP name filter for NDcPP tracking |

### Sanity Thresholds

| Config key | Default | Description |
|-----------|---------|-------------|
| `SANITY_MIN_PCL` | 50 | Fatal -- rejects snapshot if NIAP PCL returns fewer products |
| `SANITY_MIN_PPS` | 10 | Fatal -- rejects snapshot if NIAP PP list looks empty |
| `SANITY_MIN_CSFC_APL` | 5 | Warn-only -- NSA site may block bots |
| `SANITY_MIN_CC_CRYPTO_PUBS` | 5 | Warn-only -- CC Portal publications page |
| `SANITY_MIN_NIST_NEWS` | 10 | Warn-only -- NIST CSRC news page |

---

## Running Locally

```bash
# Full daily run (collect + diff + dashboard)
export $(cat .env | xargs)
python main.py

# Send weekly email digest from stored diff files
python main.py --weekly

# Bootstrap first snapshot (no diff)
python main.py --bootstrap

# Verbose debug output
CC_LOG_LEVEL=DEBUG python main.py
```

---

## Security Notes

- **Secrets**: Never commit `.env` or credentials. All secrets go in GitHub Actions Secrets.
- **Permissions**: The workflow has minimal permissions (`contents: write` only for committing generated files). No admin access is granted.
- **Outbound calls only**: CC Pulse makes read-only HTTP calls to public government websites. No credentials are sent to external sites.
- **Supply chain**: All GitHub Actions are pinned to commit SHAs (not floating tags). Dependabot monitors both pip dependencies and Actions for security updates.
- **Bot governance**: If deploying a Webex Bot in a corporate environment, register it in your organisation's Bot Registration Portal to comply with IT policies.

---

## License

MIT


