# 🛰️ CC Pulse

> **Real-Time Certification Intelligence & Differential Analytics**
>
> CC Pulse is an automated monitoring engine that tracks the "heartbeat" of the Common Criteria (CC), NIAP, CSfC, CC Crypto Catalog, and NIST CSRC ecosystems. It eliminates the manual overhead of monitoring disparate government portals and lab feeds by capturing daily snapshots and surfacing only meaningful changes.

---

## 🏗️ System Overview

CC Pulse operates as a stateful monitoring pipeline:

```
collect → diff → dashboard → alert → (weekly) email
```

Every day at **06:00 UTC**, GitHub Actions runs the pipeline. Every **Monday at 07:00 UTC**, a weekly email digest is sent automatically.

### Project Structure

```
CC-pulse/
├── .github/
│   ├── workflows/
│   │   └── cc_pulse.yml      # GitHub Actions scheduler (daily + weekly)
│   └── dependabot.yml        # Automated dependency updates
├── snapshots/                # Auto-created; daily JSON snapshots (30-day rotation)
│   └── diffs/                # Daily diff JSONs (used by weekly merge)
├── dashboard/                # Auto-created; HTML dashboard + RSS feed
│   ├── cc_dashboard.html     # Live HTML dashboard
│   └── cc_feed.xml           # RSS feed (all domains)
├── collector.py              # Multi-source aggregator (parallel I/O)
├── differ.py                 # Diff engine + keyword alert scanner
├── dashboard.py              # HTML dashboard + full-domain RSS renderer
├── emailer.py                # Weekly email + immediate alert email builder
├── main.py                   # Entry point — daily / weekly / bootstrap modes
├── config.py                 # All configuration (URLs, keywords, thresholds)
├── requirements.txt          # Pinned Python dependencies
├── .env.example              # Local dev template — copy to .env
└── README.md
```

---

## 🧩 Core Components

**Collector (`collector.py`)** — Parallel HTTP aggregator using `ThreadPoolExecutor`. Six domain collectors run concurrently: NIAP APIs, CC Portal, CCTL lab RSS feeds, NSA CSfC pages, CC Crypto Catalog, and NIST CSRC. Includes exponential-backoff retry and a partial-GET content-hash fallback for PDF polling when servers don't serve `Last-Modified`/`ETag` headers.

**Differ (`differ.py`)** — Compares two snapshots to produce a structured daily diff. Scans all added text, titles, and document header changes against `WATCH_KEYWORDS` to produce actionable alerts. Weekly merge uses `source+title` deduplication so the same event isn't reported twice.

**Dashboard (`dashboard.py`)** — Jinja2-powered HTML dashboard with colour-coded cards for all five monitored domains. Also writes an RSS feed (`cc_feed.xml`) covering all domains — suitable for RSS readers or downstream tooling.

**Email + Webex (`emailer.py`)** — Two delivery modes: immediate keyword-alert email (fired same day), and weekly HTML digest (sent automatically every Monday). Webex Space notification also fires on daily keyword matches.

**Orchestrator (`main.py`)** — Three run modes: `daily` (collect/diff/alert), `weekly` (merge + email), `bootstrap` (first-run snapshot with no diff). Includes double-run guard and 30-day snapshot rotation.

---

## 🎯 Monitored Sources

| Domain | Sources | Change Signal |
|--------|---------|---------------|
| **NIAP** | PCL, PPs, TDs, CCTLs, News API | New/removed/sunsetted items |
| **CC Portal** | News, PPs, Products, Publications, Communities | Page text additions |
| **CCTL Labs** | 8 lab RSS feeds + scraped sites | New blog / news posts |
| **CSfC** | 6 NSA pages, 8 CP PDFs (HEAD+hash), NSA/CISA/DISA feeds | APL changes, CP revisions, advisories |
| **CC Crypto Catalog** | CCDB-018 + 2 related PDFs (HEAD+hash), 3 CC Portal pages | New PDF versions, CCDB announcements |
| **NIST CSRC** | 5 CSRC pages (news, FIPS, CMVP MIP, PQC, crypto-standards), 7 PDFs (HEAD+hash), 3 RSS feeds | New FIPS/SP 800, CMVP MIP entries, PQC milestones |

---

## 🛠️ Deployment

### Prerequisites

- Python **3.10+** (3.11 recommended; that's what GitHub Actions uses)
- A GitHub account (free tier is sufficient)
- An SMTP email account for sending digests (Gmail with App Password recommended)
- Optional: Webex Bot Token + Room ID for real-time Slack-style alerts

### Step 1 — Fork / Clone the repository

```bash
git clone https://github.com/kr15tyk/CC-pulse.git
cd CC-pulse
pip install -r requirements.txt
```

### Step 2 — Configure your local environment

```bash
cp .env.example .env
# Edit .env — fill in your email credentials and optionally Webex tokens
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

### Step 3 — Bootstrap the initial snapshot (required before first diff)

```bash
export $(cat .env | xargs)
python main.py --bootstrap
```

This collects the first snapshot without producing a diff. The next daily run will diff against it.

### Step 4 — Add GitHub Actions secrets

In your GitHub repo, go to **Settings → Secrets and variables → Actions → New repository secret** and add:

| Secret name | Value |
|-------------|-------|
| `CC_EMAIL_PASSWORD` | Your SMTP password / Gmail App Password |
| `CC_EMAIL_USERNAME` | Sender email address |
| `CC_EMAIL_RECIPIENTS` | Comma-separated recipient list |
| `CC_SMTP_HOST` | SMTP server (e.g. `smtp.gmail.com`) |
| `CC_WEBEX_BOT_TOKEN` | *(optional)* Webex Bot token |
| `CC_WEBEX_ROOM_ID` | *(optional)* Webex Space Room ID |

> ⚠️ **Gmail users**: You must use an [App Password](https://support.google.com/accounts/answer/185833), not your regular account password. Enable 2FA first, then generate an App Password under "Security → 2-Step Verification → App passwords".

### Step 5 — Enable GitHub Pages (optional live dashboard)

1. Go to **Settings → Pages**
2. Set **Source** to "Deploy from a branch"
3. Branch: `main`, Folder: `/dashboard`
4. Click **Save**

Your dashboard will be live at `https://kr15tyk.github.io/CC-pulse/cc_dashboard.html` and the RSS feed at `.../cc_feed.xml`.

> Note: The first page build takes ~2 minutes after the next commit to the `dashboard/` folder.

### Step 6 — Trigger the first automated run

The workflow fires automatically at 06:00 UTC daily. To trigger manually:

1. Go to **Actions → CC Pulse — Daily & Weekly Run → Run workflow**
2. Select mode: `daily` (or `bootstrap` if you skipped Step 3)

---

## 🔄 Automation Schedule

| Schedule | Mode | What happens |
|----------|------|--------------|
| Daily at 06:00 UTC | `daily` | Collect → Diff → Dashboard → Commit → Alert (if keywords) |
| Monday at 07:00 UTC | `weekly` | Merge last 7 diffs → Send email digest |

Both schedules are defined in `.github/workflows/cc_pulse.yml`. Manual dispatch supports `daily`, `weekly`, and `bootstrap` modes.

---

## 📈 Dashboard Color Legend

| Color | Domain | Meaning |
|-------|--------|---------|
| 🟢 Green | NIAP, CCTL Labs | New Certifications / Protection Profiles / Lab Posts |
| 🟡 Amber | NIAP, CC Crypto | Sunset Date changes / Doc Version Updates |
| 🔴 Red | NIAP | Removed items |
| 🟣 Purple | CSfC | Capability Package or APL changes |
| 🔵 Teal | NIST | CSRC Standards / CMVP / PQC updates |
| 🔴 Red banner | All | WATCH_KEYWORD match — immediate Webex + email alert |

---

## ⚙️ Configuration Reference

All settings live in `config.py` and can be overridden by environment variables.

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
| `NIST_CSRC_PAGES` | NIST CSRC pages (news, FIPS, CMVP MIP, PQC) |
| `NIST_CRYPTO_DOCS` | NIST PDF URLs (FIPS 140-3, SP 800-131A, FIPS 203-205, etc.) |
| `NIST_FEEDS` | NIST cybersecurity RSS feeds |

### Alerts & Keywords

| Config key | Description |
|-----------|-------------|
| `WATCH_KEYWORDS` | High-priority alert terms — matches trigger Webex + email + dashboard banner |
| `NEWS_CATEGORY_KEYWORDS` | Category routing for news items (LABGRAM, VALGRAM, CSFC, CRYPTO, NIST, etc.) |
| `CISCO_VENDOR_KEYWORDS` | Vendor filter for Cisco NDcPP PCL tracking |
| `NDCPP_PP_KEYWORDS` | PP name filter for NDcPP tracking |

### Sanity Thresholds

| Config key | Default | Description |
|-----------|---------|-------------|
| `SANITY_MIN_PCL` | 50 | Fatal — rejects snapshot if NIAP PCL returns fewer products |
| `SANITY_MIN_PPS` | 10 | Fatal — rejects snapshot if NIAP PP list looks empty |
| `SANITY_MIN_CSFC_APL` | 5 | Warn-only — NSA site may block bots |
| `SANITY_MIN_CC_CRYPTO_PUBS` | 5 | Warn-only — CC Portal publications page |
| `SANITY_MIN_NIST_NEWS` | 10 | Warn-only — NIST CSRC news page |

---

## 🚀 Running Locally

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

## 🔒 Security Notes

- **Secrets**: Never commit `.env` or credentials. All secrets go in GitHub Actions Secrets.
- **Permissions**: The workflow has minimal permissions (`contents: write` only for committing generated files). No admin access is granted.
- **Outbound calls only**: CC Pulse makes read-only HTTP calls to public government websites. No credentials are sent to external sites.
- **Supply chain**: All GitHub Actions are pinned to commit SHAs (not floating tags). Dependabot monitors both pip dependencies and Actions for security updates.

---

## 📄 License

MIT
