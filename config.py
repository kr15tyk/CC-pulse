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
