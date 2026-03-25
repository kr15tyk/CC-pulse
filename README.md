# 🛰️ CC Pulse

> Real-Time Certification Intelligence & Differential Analytics
>
> **CC Pulse** is an automated monitoring engine designed to track the "heartbeat" of the Common Criteria (CC) and NIAP ecosystems. Engineered for security researchers specializing in NDcPP v4.0 and FIPS 186-4 transitions, it eliminates the manual overhead of monitoring disparate government portals and lab feeds.
>
> ---
>
> ## 🏗️ System Overview
>
> CC Pulse operates as a stateful monitoring pipeline, capturing daily snapshots of the certification landscape and isolating critical deltas.
>
> ### Project Structure
>
> ```
> cc-pulse/
> ├── .github/
> │   └── workflows/
> │       └── cc_pulse.yml          # GitHub Actions scheduler
> ├── snapshots/                    # Auto-created, stores daily JSON snapshots
> ├── dashboard/                    # Auto-created, stores daily HTML dashboard
> ├── collector.py                  # Pulls all data sources
> ├── differ.py                     # Compares snapshots, extracts changes
> ├── dashboard.py                  # Renders daily HTML dashboard
> ├── emailer.py                    # Builds and sends weekly email
> ├── main.py                       # Entry point — orchestrates everything
> ├── config.py                     # All configuration (URLs, filters, email)
> ├── requirements.txt
> └── README.md
> ```
>
> ---
>
> ## 🧩 Core Components
>
> - **Collector (`collector.py`)** — A multi-source aggregator fetching data from NIAP REST endpoints, the CC Portal, and major CCTL lab RSS feeds.
> - - **Differ (`differ.py`)** — A logic engine that compares daily JSON state files to identify additions, removals, and sunset date modifications.
>   - - **Pulse Dashboard (`dashboard.py`)** — A Jinja2-powered HTML interface providing an at-a-glance view of the 24-hour certification cycle.
>     - - **Weekly Brief (`emailer.py`)** — An automated SMTP reporter that summarizes technical decisions (TDs) and protection profile (PP) updates.
>      
>       - ---
>
> ## 🎯 Strategic Research Focus
>
> This repository is pre-configured to prioritize high-impact security research data:
>
> ### 1. NDcPP v4.0 & FIPS 186-4 Tracking
>
> Monitors RFIs and Technical Decisions (TDs) mapped to the Network Device collaborative Protection Profile, flagging any FIPS 186-4 requirement shifts immediately for impact analysis.
>
> ### 2. Infrastructure Sentry
>
> - **PCL Transitions** — Track products moving from In-Evaluation → Certified → Archived.
> - - **Lab Intelligence** — Scrape news from top CCTLs (atsec, Lightship, Gossamer, etc.) to catch industry trends before they hit official portals.
>   - - **Policy Alerts** — Immediate notification of new Labgrams or Valgrams released by NIAP.
>    
>     - ---
>
> ## 🛠️ Deployment
>
> ### Prerequisites
>
> - Python 3.10+
> - - Dependencies: `requests`, `beautifulsoup4`, `lxml`, `feedparser`, `jinja2`
>  
>   - ### Installation
>  
>   - ```bash
>     git clone https://github.com/kr15tyk/CC-pulse.git
>     cd CC-pulse
>     pip install -r requirements.txt
>     cp config.py config.py  # edit EMAIL_* and recipient settings
>     python main.py
>     ```
>
> ### Automation (GitHub Actions)
>
> The project includes a pre-configured workflow in `.github/workflows/cc_pulse.yml` to execute the daily "Pulse Check" and update the hosted dashboard automatically.
>
> ---
>
> ## 📈 Dashboard Color Legend
>
> | Color | Meaning |
> |-------|---------|
> | 🟢 Green | New Certifications / Protection Profiles |
> | 🟡 Amber | Sunset Date changes or Status updates |
> | 🔴 Red | Archived products or Removed Technical Decisions |
>
> ---
>
> ## ⚙️ Configuration
>
> All settings live in `config.py`:
>
> - `EMAIL_*` — SMTP credentials and recipient list (use env var `CC_EMAIL_PASSWORD`)
> - - `NIAP_ENDPOINTS` — NIAP API routes
>   - - `CCTL_LABS` — Lab RSS feeds and scrape targets
>     - - `CISCO_VENDOR_KEYWORDS` / `NDCPP_PP_KEYWORDS` — Filters for Cisco NDcPP tracking
>       - - `NEWS_CATEGORY_KEYWORDS` — Auto-categorization rules for NIAP news items
>        
>         - ---
>
> ## 📄 License
>
> MIT
