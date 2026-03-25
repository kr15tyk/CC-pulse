# =============================================================
# CC PULSE — CONFIGURATION
# Edit this file to match your environment.
# =============================================================

# --- EMAIL SETTINGS ---
EMAIL_SMTP_HOST     = "smtp.gmail.com"          # or smtp.office365.com
EMAIL_SMTP_PORT     = 587
EMAIL_USERNAME      = "your-sender@gmail.com"   # sender address
EMAIL_PASSWORD      = ""                         # use env var CC_EMAIL_PASSWORD
EMAIL_FROM          = "CC Pulse <your-sender@gmail.com>"
EMAIL_RECIPIENTS    = [
      "you@example.com",
      "colleague@example.com",
]
EMAIL_SUBJECT       = "Weekly CC Pulse — {date}"

# --- DASHBOARD ---
DASHBOARD_DIR       = "dashboard"
DASHBOARD_FILENAME  = "cc_dashboard.html"       # overwritten daily

# --- SNAPSHOTS ---
SNAPSHOT_DIR        = "snapshots"               # one JSON file per day

# --- NIAP API BASE ---
NIAP_BASE           = "https://www.niap-ccevs.org"
NIAP_ENDPOINTS = {
      "pcl":          "/api/project/product/pcl_products_all/",
      "pps":          "/api/protection-profile/public_pps_all/",
      "tds":          "/api/technical-decision/frontend_tds/",
      "cctls":        "/api/cctl/directory/frontend_cctls/?status=Certified&limit=100&offset=0",
      "events_curr":  "/api/publish/announcements/get_events_frontend/?limit=200&offset=0&current=true",
      "events_prev":  "/api/publish/announcements/get_events_frontend/?limit=200&offset=0&previous=true",
      "news":         "/api/publish/announcements/get_news_frontend/?limit=500&offset=0",
}

# --- CC PORTAL ---
CC_PORTAL_BASE      = "https://www.commoncriteriaportal.org"
CC_PORTAL_PAGES = {
      "news":         "/news/index.cfm",
      "pps":          "/pps/index.cfm",
      "products":     "/products/index.cfm",
      "communities":  "/communities/index.cfm",
      "publications": "/cc/index.cfm",
}
CC_PORTAL_RSS       = "https://www.commoncriteriaportal.org/rss/pps.xml"

# --- CCTL LAB FEEDS ---
# Labs with RSS: pull feed. Labs without: scrape the URL.
CCTL_LABS = [
      {
                "name":     "atsec information security",
                "rss":      "https://www.atsec.com/feed/",
                "url":      "https://www.atsec.com/blog/",
                "scrape":   False,
      },
      {
                "name":     "Lightship Security",
                "rss":      "https://lightshipsec.com/feed/",
                "url":      "https://lightshipsec.com/blog/",
                "scrape":   False,
      },
      {
                "name":     "Advanced Data Security",
                "rss":      "https://adseclab.com/feed/",
                "url":      "https://adseclab.com/",
                "scrape":   False,
      },
      {
                "name":     "Acumen Security (Intertek)",
                "rss":      None,
                "url":      "https://www.intertek.com/iot/cybersecurity/",
                "scrape":   True,
      },
      {
                "name":     "Booz Allen Hamilton CCTL",
                "rss":      None,
                "url":      "https://www.boozallen.com/insights.html",
                "scrape":   True,
      },
      {
                "name":     "DEKRA Cybersecurity",
                "rss":      None,
                "url":      "https://www.dekra.com/en/common-criteria/",
                "scrape":   True,
      },
      {
                "name":     "Gossamer Security Solutions",
                "rss":      None,
                "url":      "https://gossamericsec.com/",
                "scrape":   True,
      },
      {
                "name":     "Leidos CCTL",
                "rss":      None,
                "url":      "https://www.leidos.com/",
                "scrape":   True,
      },
]

# --- PRODUCT FILTERS ---
# Cisco NDcPP: vendor name contains "Cisco", PP short name contains "CPP_ND"
CISCO_VENDOR_KEYWORDS   = ["cisco"]
NDCPP_PP_KEYWORDS       = ["CPP_ND"]

# --- NEWS CATEGORY KEYWORDS ---
NEWS_CATEGORY_KEYWORDS = {
      "LABGRAM":      ["labgram"],
      "VALGRAM":      ["valgram"],
      "POLICY":       ["policy", "policies"],
      "PUBLICATION":  ["publication", "published", "progress report"],
      "EVENT":        ["event", "conference", "workshop", "webinar"],
      "CISA":         ["cisa", "emergency directive", "vulnerability"],
      "PP UPDATE":    ["pp-module", "protection profile", "cpp_", "pp_"],
      "NEWS":         [],   # catch-all
}
