# =============================================================
# CC PULSE — CONFIGURATION
# Edit this file to match your environment.
# For local dev, copy .env.example to .env and set secrets there.
# =============================================================

import os

# ── Schema ───────────────────────────────────────────────────────────────────
SNAPSHOT_SCHEMA_VERSION = 2

# ── Logging ──────────────────────────────────────────────────────────────────
# One of: DEBUG, INFO, WARNING, ERROR
LOG_LEVEL = os.environ.get("CC_LOG_LEVEL", "INFO")

# ── Email Settings ───────────────────────────────────────────────────────────
EMAIL_SMTP_HOST = os.environ.get("CC_SMTP_HOST", "smtp.gmail.com")
EMAIL_SMTP_PORT = int(os.environ.get("CC_SMTP_PORT", "587"))
EMAIL_USERNAME = os.environ.get("CC_EMAIL_USERNAME", "your-sender@gmail.com")
EMAIL_PASSWORD = os.environ.get("CC_EMAIL_PASSWORD", "")
EMAIL_FROM = os.environ.get("CC_EMAIL_FROM", "CC Pulse <your-sender@gmail.com>")
EMAIL_RECIPIENTS = [
      r.strip()
      for r in os.environ.get("CC_EMAIL_RECIPIENTS", "you@example.com").split(",")
      if r.strip()
]
EMAIL_SUBJECT = "Weekly CC Pulse — {date}"

# ── Notifications (Webex) ────────────────────────────────────────────────────
# Set CC_WEBEX_BOT_TOKEN and CC_WEBEX_ROOM_ID in your environment or GitHub
# Secrets to enable real-time Webex Space notifications.
# Leave blank to disable Webex notifications.
WEBEX_BOT_TOKEN = os.environ.get("CC_WEBEX_BOT_TOKEN", "")
WEBEX_ROOM_ID = os.environ.get("CC_WEBEX_ROOM_ID", "")

# ── Dashboard ────────────────────────────────────────────────────────────────
DASHBOARD_DIR = "dashboard"
DASHBOARD_FILENAME = "cc_dashboard.html"
DASHBOARD_RSS = "cc_feed.xml"  # written alongside the HTML

# ── Snapshots ────────────────────────────────────────────────────────────────
SNAPSHOT_DIR = "snapshots"       # daily JSON snapshots
DIFF_DIR = "snapshots/diffs"     # daily diff JSONs (for weekly merge)

# ── Retry Settings ───────────────────────────────────────────────────────────
RETRY_ATTEMPTS = 4
RETRY_BACKOFF_BASE = 2  # seconds; actual delay = base ** attempt

# ── Sanity Check Thresholds ──────────────────────────────────────────────────
# If a collection returns fewer items than these minimums, it is treated as a
# fetch failure and the snapshot is rejected.
SANITY_MIN_PCL = 50   # NIAP PCL usually has 300+ products
SANITY_MIN_PPS = 10   # NIAP PPs usually has 50+

# ── NIAP API ─────────────────────────────────────────────────────────────────
NIAP_BASE = "https://www.niap-ccevs.org"
NIAP_ENDPOINTS = {
      "pcl":         "/api/project/product/pcl_products_all/",
      "pps":         "/api/protection-profile/public_pps_all/",
      "tds":         "/api/technical-decision/frontend_tds/",
      "cctls":       "/api/cctl/directory/frontend_cctls/?status=Certified&limit=100&offset=0",
      "events_curr": "/api/publish/announcements/get_events_frontend/?limit=200&offset=0&current=true",
      "events_prev": "/api/publish/announcements/get_events_frontend/?limit=200&offset=0&previous=true",
      "news":        "/api/publish/announcements/get_news_frontend/?limit=500&offset=0",
}

# ── CC Portal ────────────────────────────────────────────────────────────────
CC_PORTAL_BASE = "https://www.commoncriteriaportal.org"
CC_PORTAL_PAGES = {
      "news":         "/news/index.cfm",
      "pps":          "/pps/index.cfm",
      "products":     "/products/index.cfm",
      "communities":  "/communities/index.cfm",
      "publications": "/cc/index.cfm",
}
CC_PORTAL_RSS = "https://www.commoncriteriaportal.org/rss/pps.xml"

# ── CCTL Lab Feeds ───────────────────────────────────────────────────────────
CCTL_LABS = [
      {"name": "atsec information security",  "rss": "https://www.atsec.com/feed/",         "url": "https://www.atsec.com/blog/",                      "scrape": False},
      {"name": "Lightship Security",          "rss": "https://lightshipsec.com/feed/",      "url": "https://lightshipsec.com/blog/",                   "scrape": False},
      {"name": "Advanced Data Security",      "rss": "https://adseclab.com/feed/",          "url": "https://adseclab.com/",                            "scrape": False},
      {"name": "Acumen Security (Intertek)",  "rss": None,                                  "url": "https://www.intertek.com/iot/cybersecurity/",       "scrape": True},
      {"name": "Booz Allen Hamilton CCTL",    "rss": None,                                  "url": "https://www.boozallen.com/insights.html",           "scrape": True},
      {"name": "DEKRA Cybersecurity",         "rss": None,                                  "url": "https://www.dekra.com/en/common-criteria/",         "scrape": True},
      {"name": "Gossamer Security Solutions", "rss": None,                                  "url": "https://gossamericsec.com/",                       "scrape": True},
      {"name": "Leidos CCTL",                 "rss": None,                                  "url": "https://www.leidos.com/",                          "scrape": True},
]

# ── Product Filters ──────────────────────────────────────────────────────────
CISCO_VENDOR_KEYWORDS = ["cisco"]
NDCPP_PP_KEYWORDS = ["CPP_ND"]

# ── News Category Keywords ───────────────────────────────────────────────────
NEWS_CATEGORY_KEYWORDS = {
      "LABGRAM":     ["labgram"],
      "VALGRAM":     ["valgram"],
      "POLICY":      ["policy", "policies"],
      "PUBLICATION": ["publication", "published", "progress report"],
      "EVENT":       ["event", "conference", "workshop", "webinar"],
      "CISA":        ["cisa", "emergency directive", "vulnerability"],
      "PP UPDATE":   ["pp-module", "protection profile", "cpp_", "pp_"],
      "CSFC":        ["csfc", "csfC", "commercial solutions for classified"],
          "CRYPTO":      ["ccdb-018", "crypto catalog", "cryptograph", "fcs_", "cavp", "cmvp", "key establishment", "digital signature"],
              "NIST":         ["nist", "fips 140", "fips 203", "fips 204", "fips 205", "sp 800", "cmvp", "cavp", "post-quantum", "pqc", "ml-kem", "ml-dsa", "slh-dsa", "csrc"],
      "NEWS":        [],  # catch-all
}

# ── Watch Keywords (high-priority alert terms) ───────────────────────────────
# Any TD title, PP name, or news item containing one of these strings (case-
# insensitive) will be flagged at the top of the dashboard and email, and
# trigger an immediate Webex Space notification.
WATCH_KEYWORDS = [
      "FIPS 186-4",
      "FIPS 186-5",
      "NDcPP",
      "CPP_ND",
      "TLS 1.3",
      "SSH",
      "PP-Module_VPN",
      "PP-Module_WLAN",
      "labgram",
      "valgram",
      "emergency",
      # ── CSfC-specific watch terms ─────────────────────────────────────────
      "CSfC",
      "Commercial Solutions for Classified",
      "CSfC APL",
      "CSfC capability package",
      "CP-Mobile",
      "CP-MA",
      "CP-WAN",
      "CP-Campus WLAN",
      "CP-DAR",
      "CP-MDM",
      "NSA CSfC",
    # ── CC Crypto Catalog watch terms ─────────────────────────────────────
    "CCDB-018",
    "Crypto Catalog",
    "crypto catalog",
    "cryptography working group",
    "Specification of Functional Requirements for Cryptography",
    "FCS_CKM",
    "FCS_COP",
    "FCS_RBG",
    "CAVP",
    "CMVP",
          # ── NIST/CSRC watch terms ────────────────────────────────────────────────
          "FIPS 140-3",
          "FIPS 203",
          "FIPS 204",
          "FIPS 205",
          "SP 800-131A",
          "SP 800-57",
          "NIST IR 8547",
          "post-quantum cryptography",
          "PQC migration",
          "ML-KEM",
          "ML-DSA",
          "SLH-DSA",
          "algorithm transition",
          "CMVP validated",
          "modules in process",
]

# =============================================================
# CSfC (Commercial Solutions for Classified) Monitoring
# NSA's CSfC program approves commercial products for use in
# classified environments via layered, NIAP-certified solutions.
# Sources below are scraped/polled for Approved Products List
# (APL) changes, new/revised Capability Packages, and program
# news.  collector.py reads CSFC_PAGES and CSFC_FEEDS.
# =============================================================

CSFC_BASE = "https://www.nsa.gov"

# ── CSfC web pages to snapshot for change detection ──────────────────────────
# Each entry is scraped for text/link changes between runs.
CSFC_PAGES = {
      # Main CSfC program landing page
    "home":          "/resources/everyone/csfc/",
      # Approved Products List (APL) — component-level approval
      "apl":           "/resources/everyone/csfc/approved-products-list/",
      # All Capability Packages (active and archived)
      "cap_packages":  "/resources/everyone/csfc/capability-packages/",
      # Frequently Asked Questions — policy language changes here
      "faq":           "/resources/everyone/csfc/faqs/",
      # Registration / enrollment guidance
      "registration":  "/resources/everyone/csfc/registration/",
      # Key Management Requirements
      "kmr":           "/resources/everyone/csfc/key-management/",
}

# ── CSfC Capability Package documents (direct PDF URLs) ──────────────────────
# These are polled for HTTP Last-Modified / ETag header changes.
# When a header changes the diff will report the CP as "updated".
# Add or remove entries as NSA publishes new packages.
CSFC_CAPABILITY_PACKAGES = {
      "CP-Mobile Access":           "https://www.nsa.gov/portals/75/documents/resources/everyone/csfc/capability-packages/mobile-access.pdf",
      "CP-Multi-Site Connectivity": "https://www.nsa.gov/portals/75/documents/resources/everyone/csfc/capability-packages/multi-site-connectivity.pdf",
      "CP-Campus WLAN":             "https://www.nsa.gov/portals/75/documents/resources/everyone/csfc/capability-packages/campus-wlan.pdf",
      "CP-Data at Rest":            "https://www.nsa.gov/portals/75/documents/resources/everyone/csfc/capability-packages/data-at-rest.pdf",
      "CP-Mobile Device Management":"https://www.nsa.gov/portals/75/documents/resources/everyone/csfc/capability-packages/mdm.pdf",
      "CP-WAN":                     "https://www.nsa.gov/portals/75/documents/resources/everyone/csfc/capability-packages/wan.pdf",
      "CP-Email":                   "https://www.nsa.gov/portals/75/documents/resources/everyone/csfc/capability-packages/email.pdf",
      "CP-Voice over IP":           "https://www.nsa.gov/portals/75/documents/resources/everyone/csfc/capability-packages/voip.pdf",
}

# ── CSfC RSS / news feeds ─────────────────────────────────────────────────────
# NSA does not publish a CSfC-specific RSS feed, so we supplement with
# CISA and DoD cybersecurity advisory feeds that commonly carry CSfC news.
CSFC_FEEDS = [
      {
                "name": "NSA Cybersecurity Advisories",
                "rss":  "https://www.nsa.gov/rss/rssfeed.aspx?POIID=9",
                "scrape": False,
      },
      {
                "name": "CISA Alerts",
                "rss":  "https://www.cisa.gov/cybersecurity-advisories/all.xml",
                "scrape": False,
      },
      {
                "name": "DISA STIGs & APL News",
                "rss":  None,
                "url":  "https://public.cyber.mil/stigs/",
                "scrape": True,
      },
]

# ── CSfC keyword filters for APL product categorisation ──────────────────────
# Used to flag APL entries that belong to a particular capability package
# when the collector parses the APL page.
CSFC_APL_COMPONENT_KEYWORDS = {
      "SSH":            ["ssh", "secure shell"],
      "TLS/VPN":        ["tls", "vpn", "ipsec", "ssl"],
      "WLAN":           ["wlan", "wi-fi", "wireless", "802.11"],
      "DAR":            ["data at rest", "dar", "self-encrypting", "sed", "fde"],
      "MDM":            ["mdm", "mobile device management", "uem"],
      "Email":          ["email", "s/mime", "smime"],
      "VoIP":           ["voip", "sip", "voice over"],
      "Multi-Site":     ["wan", "multi-site", "sd-wan"],
}

# ── Sanity minimum for CSfC APL ───────────────────────────────────────────────
# If the collector returns fewer APL entries than this, treat it as a
# fetch failure and reject the snapshot.
SANITY_MIN_CSFC_APL = 5  # NSA APL typically lists dozens of components

# =============================================================
# CC Crypto Catalog & Working Group Monitoring
#
# The "CC Crypto Catalog" (formally: Specification of Functional
# Requirements for Cryptography, CCDB-018) is a CCRA Supporting
# Document published by the Common Criteria Development Board
# (CCDB) Cryptography Working Group.  It defines the approved
# cryptographic algorithms and parameters for use in CC-evaluated
# products.  New releases or errata require vendors and labs to
# update their Security Targets / Protection Profiles.
#
# Sources monitored:
#   1. PDF header poll — detect silent version bumps via
#      Last-Modified / ETag / Content-Length changes.
#   2. CC Portal publications page (/cc/index.cfm) — new CCDB-018
#      revisions or errata entries appear here first.
#   3. CC Portal news page (/news/index.cfm) — release announcements
#      from the CCDB / Management Committee.
#   4. CC Portal communities page (/communities/index.cfm) — any
#      new working-group charters or spin-off groups.
#   5. NIAP news endpoint — NIAP often posts labgrams/valgrams that
#      reference crypto catalog changes (already collected via
#      NIAP_ENDPOINTS["news"]; keywords route them here).
# =============================================================

CC_CRYPTO_BASE = "https://www.commoncriteriaportal.org"

# ── Crypto Catalog PDF documents to header-poll ──────────────────────────────
# Key  = human-readable name shown in dashboard / alerts
# Value = direct URL to the PDF
# Add new entries as the CCDB publishes revised versions or errata.
CC_CRYPTO_DOCS = {
          # Current release: CCDB-018 v1.0 (31 Jan 2025)
          "CCDB-018 v1.0 Crypto Catalog (Jan 2025)": (
                        "https://www.commoncriteriaportal.org/files/ccfiles/"
                        "CCDB-018-v1.0-2025-Jan-31-Final-"
                        "Specification_of_Functional_Requirements_for_Cryptography.pdf"
          ),
          # Assurance Continuity SD (frequently updated alongside crypto rules)
          "CCDB-014 v3.1 Assurance Continuity (Feb 2024)": (
                        "https://www.commoncriteriaportal.org/files/ccfiles/"
                        "CCDB-014-v3.1-2024-February-29.pdf"
          ),
          # CC:2022 Part 2 — Security Functional Requirements (crypto SFRs live here)
          "CC:2022 Part 2 Security Functional Requirements": (
                        "https://www.commoncriteriaportal.org/files/ccfiles/CC2022PART2R1.pdf"
          ),
}

# ── CC Portal pages to scrape for Crypto Catalog / working group changes ──────
# Scraped for new text/link entries between runs; any change triggers a diff.
CC_CRYPTO_PAGES = {
          # Publications — new CCDB-018 versions / errata first appear here
          "publications":  "/cc/index.cfm",
          # News — CCDB / Management Committee release announcements
          "news":          "/news/index.cfm",
          # Communities — working group charters, new groups
          "communities":   "/communities/index.cfm",
}

# ── Keywords for routing news items into the CRYPTO category ─────────────────
# Extend NEWS_CATEGORY_KEYWORDS with this entry (merged at runtime by
# collector.py so the existing categorize_news() function picks them up).
CC_CRYPTO_NEWS_KEYWORDS = [
          "ccdb-018",
          "crypto catalog",
          "cryptography",
          "cryptographic",
          "specification of functional requirements for cryptography",
          "ccdb working group",
          "crypto working group",
          "fcs_",          # CC SFR family prefix for all crypto requirements
          "algorithm",
          "key establishment",
          "key generation",
          "digital signature",
          "hash function",
          "random bit generator",
          "rbg",
]

# ── Sanity minimum for Crypto Catalog page scrape ────────────────────────────
# If the publications page returns fewer items than this it is likely a fetch
# failure — warn but do not abort (same pattern as SANITY_MIN_CSFC_APL).
SANITY_MIN_CC_CRYPTO_PUBS = 5

# =============================================================
# NIST CSRC Monitoring (Option B)
#
# Monitors NIST's Computer Security Resource Center for changes
# relevant to CC/CSfC practitioners:
#   1. CSRC News page — all NIST cyber publication announcements
#   2. NIST Cybersecurity RSS feed — filtered news stream
#   3. CMVP "Modules In Process" list — FIPS 140-3 validation pipeline
#      (directly relevant to CSfC component approvals)
#   4. PQC project page — post-quantum standardization milestones
#   5. FIPS publications page — new/revised crypto standards
#   6. HTTP header poll of key PDFs:
#        FIPS 140-3, SP 800-131A (algorithm transitions),
#        SP 800-57 Part 1 (key management), NIST IR 8547 (PQC migration)
# =============================================================

NIST_CSRC_BASE = "https://csrc.nist.gov"
NIST_BASE = "https://www.nist.gov"

# ── NIST CSRC pages to scrape for change detection ─────────────────────────
NIST_CSRC_PAGES = {
          # Chronological news feed — new publication/draft announcements land here
    "news":              "/news",
          # FIPS standards listing — watch for new drafts or final FIPS
          "fips":              "/publications/fips",
          # CMVP Modules In Process — FIPS 140-3 validation pipeline (HTML table)
          "cmvp_mip":          "/projects/cryptographic-module-validation-program/modules-in-process/modules-in-process-list",
          # PQC project overview — ongoing standardization milestone page
          "pqc":               "/projects/post-quantum-cryptography",
          # Cryptographic Standards and Guidelines — algorithm suite overview
          "crypto_standards":  "/projects/cryptographic-standards-and-guidelines",
}

# ── NIST PDF documents to HTTP-HEAD-poll for version changes ────────────────
# Key = human-readable label; Value = direct PDF URL.
# Last-Modified / ETag / Content-Length changes indicate a new revision.
NIST_CRYPTO_DOCS = {
          # FIPS 140-3 Security Requirements for Cryptographic Modules (Mar 2019)
    "FIPS 140-3": (
                  "https://nvlpubs.nist.gov/nistpubs/FIPS/NIST.FIPS.140-3.pdf"
    ),
          # SP 800-131A Rev 2 — Transitioning Cryptographic Algorithms and Key Sizes
          "SP 800-131A Rev 2": (
                        "https://nvlpubs.nist.gov/nistpubs/SpecialPublications/NIST.SP.800-131Ar2.pdf"
          ),
          # SP 800-57 Part 1 Rev 5 — Key Management Recommendation
          "SP 800-57 Part 1 Rev 5": (
                        "https://nvlpubs.nist.gov/nistpubs/SpecialPublications/NIST.SP.800-57pt1r5.pdf"
          ),
          # NIST IR 8547 — Transition to Post-Quantum Cryptography Standards
          "NIST IR 8547": (
                        "https://nvlpubs.nist.gov/nistpubs/ir/2024/NIST.IR.8547.ipd.pdf"
          ),
          # FIPS 203 — ML-KEM (Module-Lattice Key Encapsulation, Aug 2024)
          "FIPS 203 ML-KEM": (
                        "https://nvlpubs.nist.gov/nistpubs/FIPS/NIST.FIPS.203.pdf"
          ),
          # FIPS 204 — ML-DSA (Module-Lattice Digital Signature, Aug 2024)
          "FIPS 204 ML-DSA": (
                        "https://nvlpubs.nist.gov/nistpubs/FIPS/NIST.FIPS.204.pdf"
          ),
          # FIPS 205 — SLH-DSA (Stateless Hash-Based Digital Signature, Aug 2024)
          "FIPS 205 SLH-DSA": (
                        "https://nvlpubs.nist.gov/nistpubs/FIPS/NIST.FIPS.205.pdf"
          ),
}

# ── NIST RSS / news feeds ────────────────────────────────────────────────────
NIST_FEEDS = [
          {
                        "name": "NIST Cybersecurity News",
                        "rss": "https://www.nist.gov/news-events/cybersecurity/rss.xml",
                        "scrape": False,
          },
          {
                        "name": "NIST Information Technology News",
                        "rss": "https://www.nist.gov/news-events/information%20technology/rss.xml",
                        "scrape": False,
          },
          {
                        "name": "NIST Cybersecurity Insights Blog",
                        "rss": "https://www.nist.gov/blogs/cybersecurity-insights/rss.xml",
                        "scrape": False,
          },
]

# ── Keywords for routing NIST news into the NIST category ───────────────────
NIST_NEWS_KEYWORDS = [
          "nist",
          "fips 140",
          "fips 186",
          "fips 197",
          "fips 203",
          "fips 204",
          "fips 205",
          "sp 800",
          "cmvp",
          "cavp",
          "post-quantum",
          "pqc",
          "ml-kem",
          "ml-dsa",
          "slh-dsa",
          "algorithm transition",
          "key management",
          "cryptographic module",
          "csrc",
]

# ── Sanity minimum for NIST CSRC news scrape ────────────────────────────────
# The CSRC /news page consistently shows 1000+ items; warn if suspiciously low.
SANITY_MIN_NIST_NEWS = 10
