# =============================================================
# CC PULSE -- CONFIGURATION
# Edit this file to match your environment.
# For local dev, copy .env.example to .env and set secrets there.
# =============================================================

import os

# -- Schema -----------------------------------------------------------------
# Increment whenever the snapshot structure changes in a breaking way.
SNAPSHOT_SCHEMA_VERSION = 2

# -- Logging ----------------------------------------------------------------
# One of: DEBUG, INFO, WARNING, ERROR
LOG_LEVEL = os.environ.get("CC_LOG_LEVEL", "INFO")

# -- Email Settings ---------------------------------------------------------
EMAIL_SMTP_HOST = os.environ.get("CC_SMTP_HOST", "smtp.gmail.com")
EMAIL_SMTP_PORT = int(os.environ.get("CC_SMTP_PORT", "587"))
EMAIL_USERNAME  = os.environ.get("CC_EMAIL_USERNAME", "your-sender@gmail.com")
EMAIL_PASSWORD  = os.environ.get("CC_EMAIL_PASSWORD", "")
EMAIL_FROM      = os.environ.get("CC_EMAIL_FROM",     "CC Pulse <your-sender@gmail.com>")
EMAIL_RECIPIENTS = [
        r.strip()
        for r in os.environ.get("CC_EMAIL_RECIPIENTS", "you@example.com").split(",")
        if r.strip()
]
EMAIL_SUBJECT = "Weekly CC Pulse -- {date}"

# -- Notifications (Webex) --------------------------------------------------
WEBEX_BOT_TOKEN = os.environ.get("CC_WEBEX_BOT_TOKEN", "")
WEBEX_ROOM_ID   = os.environ.get("CC_WEBEX_ROOM_ID",   "")

# -- Notifications (Generic Webhook / MS Teams) -----------------------------
WEBHOOK_URL = os.environ.get("CC_WEBHOOK_URL", "")

# -- Dashboard --------------------------------------------------------------
DASHBOARD_DIR      = "docs"
DASHBOARD_FILENAME = "cc_dashboard.html"
DASHBOARD_RSS      = "cc_feed.xml"

# -- Snapshots --------------------------------------------------------------
SNAPSHOT_DIR = "snapshots"
DIFF_DIR     = "snapshots/diffs"

# -- Retry Settings ---------------------------------------------------------
RETRY_ATTEMPTS     = 4
RETRY_BACKOFF_BASE = 2

# -- Sanity Check Thresholds ------------------------------------------------
SANITY_MIN_PCL           = 50
SANITY_MIN_PPS           = 10
SANITY_MIN_CSFC_APL      = 5
SANITY_MIN_CC_CRYPTO_PUBS = 5
SANITY_MIN_NIST_NEWS     = 10

# -- NIAP API ---------------------------------------------------------------
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

# -- CC Portal --------------------------------------------------------------
CC_PORTAL_BASE  = "https://www.commoncriteriaportal.org"
CC_PORTAL_PAGES = {
        "news":         "/news/index.cfm",
        "pps":          "/pps/index.cfm",
        "products":     "/products/index.cfm",
        "communities":  "/communities/index.cfm",
        "publications": "/cc/index.cfm",
}
CC_PORTAL_RSS = "https://www.commoncriteriaportal.org/rss/pps.xml"

# -- CCTL Lab Feeds ---------------------------------------------------------
CCTL_LABS = [
        # RSS feed working and CC-focused
        {"name": "Lightship Security",  "rss": "https://lightshipsec.com/feed/",  "url": "https://lightshipsec.com/blog/",  "scrape": False},
        # RSS broken after site rebuild (2025); scrape blog directly
        {"name": "atsec",               "rss": None,                              "url": "https://www.atsec.com/blog/",     "scrape": True},
]

# -- Product Filters --------------------------------------------------------
CISCO_VENDOR_KEYWORDS = ["cisco"]
NDCPP_PP_KEYWORDS     = ["CPP_ND"]

# -- News Category Keywords -------------------------------------------------
NEWS_CATEGORY_KEYWORDS = {
        "LABGRAM":     ["labgram"],
        "VALGRAM":     ["valgram"],
        "POLICY":      ["policy", "policies"],
        "PUBLICATION": ["publication", "published", "progress report"],
        "EVENT":       ["event", "conference", "workshop", "webinar"],
        "CISA":        ["cisa", "emergency directive", "vulnerability"],
        "PP UPDATE":   ["pp-module", "protection profile", "cpp_", "pp_"],
        "CSFC":        ["csfc", "commercial solutions for classified"],
        "NATO":        ["nato", "niapc", "niapcl"],
        "EUCC":        ["eucc", "enisa", "european union agency for cybersecurity"],
        "CRYPTO": [
                    "ccdb-018", "crypto catalog", "cryptograph", "fcs_",
                    "key establishment", "digital signature", "hash function",
                    "random bit generator", "rbg", "algorithm transition",
        ],
        "NIST": [
                    "nist", "fips 140", "fips 186", "fips 197", "fips 203", "fips 204", "fips 205",
                    "sp 800", "cmvp", "cavp", "post-quantum", "pqc",
                    "ml-kem", "ml-dsa", "slh-dsa", "csrc",
        ],
        "NEWS": [],
}

# -- Watch Keywords (high-priority alert terms) -----------------------------
WATCH_KEYWORDS = [
        # -- NIAP / NDcPP -------------------------------------------------
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
        # -- CSfC ---------------------------------------------------------
        "CSfC",
        "Commercial Solutions for Classified",
        "CSfC APL",
        "CSfC component selections",
        "NSA CSfC",
        # -- NATO NIAPCL --------------------------------------------------
        "NATO NIAPC",
        "NIAPCL",
        # -- EUCC / ENISA -------------------------------------------------
        "EUCC",
        "ENISA",
        # -- CC Crypto Catalog ---------------------------------------------
        "CCDB-018",
        "Crypto Catalog",
        "cryptography working group",
        "Specification of Functional Requirements for Cryptography",
        "FCS_CKM",
        "FCS_COP",
        "FCS_RBG",
        "CAVP",
        "CMVP",
        # -- NIST / CSRC ---------------------------------------------------
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

# -- Body Watch Keywords (page-scrape tier) ----------------------------------
BODY_WATCH_KEYWORDS = [
        "FIPS 203", "FIPS 204", "FIPS 205", "NIST IR 8547", "SP 800-131A",
        "CSfC APL", "CSfC component selections",
        "CCDB-018",
        "post-quantum cryptography", "PQC migration",
        "ML-KEM", "ML-DSA", "SLH-DSA",
        "EUCC", "ENISA",
        "NATO NIAPC", "NIAPCL",
        "cisco",
]

# =============================================================
# CSfC (Commercial Solutions for Classified) Monitoring
# =============================================================
CSFC_BASE = "https://www.nsa.gov"

CSFC_PAGES = {
        "home":          "/resources/everyone/csfc/",
        "apl":           "/resources/everyone/csfc/approved-products-list/",
        "cap_packages":  "/resources/everyone/csfc/capability-packages/",
        "faq":           "/resources/everyone/csfc/faqs/",
        "registration":  "/resources/everyone/csfc/registration/",
        "kmr":           "/resources/everyone/csfc/key-management/",
        "announcements": "/Resources/Commercial-Solutions-for-Classified-Program/Announcements/",
}

CSFC_COMPONENT_SELECTIONS = {
        "Authentication Server":           "https://www.nsa.gov/portals/75/documents/resources/everyone/csfc/components-list/selections/authentication-servers.pdf",
        "Certificate Authority":           "https://www.nsa.gov/portals/75/documents/resources/everyone/csfc/components-list/selections/certificate-authorities.pdf",
        "Client Virtualization Systems":   "https://www.nsa.gov/Portals/75/documents/resources/everyone/csfc/components-list/selections/Client-Virtualization.pdf",
        "E-mail Clients":                  "https://www.nsa.gov/portals/75/documents/resources/everyone/csfc/components-list/selections/email-clients.pdf",
        "End User Device / Mobile Platform": "https://www.nsa.gov/portals/75/documents/resources/everyone/csfc/capability-packages/CSfC%20Selections%20for%20End%20User%20Devices%20and%20Mobile%20Platform_Nov%202018.pdf",
        "File Encryption":                 "https://www.nsa.gov/portals/75/documents/resources/everyone/csfc/components-list/selections/file-encryption.pdf",
        "General Purpose Operating System":"https://www.nsa.gov/Portals/75/documents/resources/everyone/csfc/components-list/General%20Purpose%20Operating%20System%20Selectors.pdf",
        "General Purpose Compute Platform":"https://www.nsa.gov/Portals/75/documents/resources/everyone/csfc/components-list/General%20Purpose%20Compute%20Platform%20Selectors.pdf",
        "Hardware Full Drive Encryption":  "https://www.nsa.gov/portals/75/documents/resources/everyone/csfc/components-list/selections/sw-fde.pdf",
        "IPS":                             "https://www.nsa.gov/portals/75/documents/resources/everyone/csfc/components-list/selections/ips.pdf",
        "IPsec VPN Client":                "https://www.nsa.gov/portals/75/documents/resources/everyone/csfc/components-list/selections/vpn-clients.pdf",
        "IPsec VPN Gateway":               "https://www.nsa.gov/portals/75/documents/resources/everyone/csfc/components-list/selections/vpn-gateways.pdf",
        "MACSEC Ethernet Encryption":      "https://www.nsa.gov/portals/75/documents/resources/everyone/csfc/components-list/selections/macsec.pdf",
        "MDM":                             "https://www.nsa.gov/portals/75/documents/resources/everyone/csfc/components-list/selections/mdm.pdf",
        "Session Border Controller":       "https://www.nsa.gov/portals/75/documents/resources/everyone/csfc/components-list/selections/sbc.pdf",
        "Enterprise Session Controller":   "https://www.nsa.gov/portals/75/documents/resources/everyone/csfc/components-list/selections/esc.pdf",
        "Software Full Drive Encryption":  "https://www.nsa.gov/portals/75/documents/resources/everyone/csfc/components-list/selections/sw-fde.pdf",
        "TLS Protected Servers":           "https://www.nsa.gov/Portals/75/documents/resources/everyone/csfc/components-list/selections/TLS-Protected-Servers-2022-08-24.pdf",
        "TLS Software Applications":       "https://www.nsa.gov/Portals/75/documents/resources/everyone/csfc/components-list/selections/CSfC%20Selections%20for%20TLS%20Software%20Application-2022-11-03.pdf",
        "Traffic Filtering Firewall":      "https://www.nsa.gov/portals/75/documents/resources/everyone/csfc/components-list/selections/tffw.pdf",
        "VoIP Applications":               "https://www.nsa.gov/portals/75/documents/resources/everyone/csfc/components-list/selections/voip-applications.pdf",
        "Web Browsers":                    "https://www.nsa.gov/portals/75/documents/resources/everyone/csfc/components-list/selections/web-browsers.pdf",
        "WLAN Access System":              "https://www.nsa.gov/portals/75/documents/resources/everyone/csfc/components-list/selections/wlan-access-systems.pdf",
}

CSFC_COMPONENTS_LIST_URL = "https://www.nsa.gov/Resources/Commercial-Solutions-for-Classified-Program/Components-List/"

# -- CSfC Product List (approved products) ----------------------------------
# Monitored separately from component selections for Cisco-specific alerting.
CSFC_PRODUCT_LIST_URL = "https://www.nsa.gov/Resources/Commercial-Solutions-for-Classified-Program/Components-List/"

CSFC_FEEDS = [
        {"name": "NSA Cybersecurity Advisories", "rss": "https://www.nsa.gov/rss/rssfeed.aspx?POIID=9", "scrape": False},
        {"name": "CISA Alerts",                  "rss": "https://www.cisa.gov/cybersecurity-advisories/all.xml", "scrape": False},
        {"name": "DISA STIGs & APL News",        "rss": None, "url": "https://public.cyber.mil/stigs/", "scrape": True},
]

CSFC_APL_COMPONENT_KEYWORDS = {
        "SSH":        ["ssh", "secure shell"],
        "TLS/VPN":    ["tls", "vpn", "ipsec", "ssl"],
        "WLAN":       ["wlan", "wi-fi", "wireless", "802.11"],
        "DAR":        ["data at rest", "dar", "self-encrypting", "sed", "fde"],
        "MDM":        ["mdm", "mobile device management", "uem"],
        "Email":      ["email", "s/mime", "smime"],
        "VoIP":       ["voip", "sip", "voice over"],
        "Multi-Site": ["wan", "multi-site", "sd-wan"],
}

# =============================================================
# NATO NIAPCL Monitoring
# Monitors NATO's Information Assurance Product Catalogue for
# new Cisco listings and general changes.
# =============================================================
NATO_BASE = "https://www.ia.nato.int"

# Main NIAPCL search page filtered to show all products
NATO_NIAPCL_PAGES = {
        "all_products": "/Search/NIAPC/AND/Category_/Manufacturer_10/Country_/SecurityGroup_",
        "cisco_products": "/Search/NIAPC/AND/Category_/Manufacturer_10/Country_/SecurityGroup_",
}

# Canonical URL for NATO NIAPCL alerts and dashboard links
NATO_NIAPCL_URL = "https://www.ia.nato.int/Search/NIAPC/AND/Category_/Manufacturer_10/Country_/SecurityGroup_"

# Vendor keywords to identify Cisco in NATO listings
NATO_CISCO_KEYWORDS = ["cisco"]

# Sanity minimum: NATO NIAPCL typically lists dozens of products
SANITY_MIN_NATO_PRODUCTS = 3

# =============================================================
# EUCC (EU Common Criteria) / ENISA Monitoring
# Two sources:
#   1. EUCC scheme requirements / news (enisa.europa.eu/browse-topic/eucc_en)
#   2. EUCC certificates (enisa.europa.eu/certificates_en)
# =============================================================
EUCC_BASE = "https://certification.enisa.europa.eu"

EUCC_PAGES = {
        # Requirements, scheme news, policy updates
    "requirements": "/browse-topic/eucc_en",
        # Certified product listings
        "certificates": "/certificates_en",
}

# Canonical URLs for EUCC alerts and dashboard links
EUCC_REQUIREMENTS_URL = "https://certification.enisa.europa.eu/browse-topic/eucc_en"
EUCC_CERTIFICATES_URL = "https://certification.enisa.europa.eu/certificates_en"

# Vendor keywords to identify Cisco in EUCC listings
EUCC_CISCO_KEYWORDS = ["cisco"]

# EU-specific tier keywords -- items matching these get flagged as Tier 1 EU
EUCC_TIER1_KEYWORDS = [
        "cisco",
        "EUCC",
        "ENISA",
        "EU Common Criteria",
        "European Common Criteria",
]

# Sanity minimum: EUCC certificates page should have some entries
SANITY_MIN_EUCC_CERTS = 2

# =============================================================
# CC Crypto Catalog & Working Group Monitoring
# =============================================================
CC_CRYPTO_BASE  = "https://www.commoncriteriaportal.org"

CC_CRYPTO_DOCS = {
        "CCDB-018 v1.0 Crypto Catalog (Jan 2025)": (
                    "https://www.commoncriteriaportal.org/files/ccfiles/"
                    "CCDB-018-v1.0-2025-Jan-31-Final-"
                    "Specification_of_Functional_Requirements_for_Cryptography.pdf"
        ),
        "CCDB-014 v3.1 Assurance Continuity (Feb 2024)": (
                    "https://www.commoncriteriaportal.org/files/ccfiles/"
                    "CCDB-014-v3.1-2024-February-29.pdf"
        ),
        "CC:2022 Part 2 Security Functional Requirements": (
                    "https://www.commoncriteriaportal.org/files/ccfiles/CC2022PART2R1.pdf"
        ),
}

CC_CRYPTO_PAGES = {
        "publications": "/cc/index.cfm",
        "news":         "/news/index.cfm",
        "communities":  "/communities/index.cfm",
}

CC_CRYPTO_NEWS_KEYWORDS = [
        "ccdb-018", "crypto catalog", "cryptography", "cryptographic",
        "specification of functional requirements for cryptography",
        "ccdb working group", "crypto working group",
        "fcs_", "algorithm", "key establishment", "key generation",
        "digital signature", "hash function", "random bit generator", "rbg",
]

# =============================================================
# NIST CSRC Monitoring
# =============================================================
NIST_CSRC_BASE = "https://csrc.nist.gov"
NIST_BASE      = "https://www.nist.gov"

NIST_CSRC_PAGES = {
        "news":            "/news",
        "fips":            "/publications/fips",
        "cmvp_mip":        "/projects/cryptographic-module-validation-program/modules-in-process/modules-in-process-list",
        "pqc":             "/projects/post-quantum-cryptography",
        "crypto_standards":"/projects/cryptographic-standards-and-guidelines",
        "cmvp_validated":  "/projects/cryptographic-module-validation-program/validated-modules",
}

NIST_CRYPTO_DOCS = {
        "FIPS 140-3":       "https://nvlpubs.nist.gov/nistpubs/FIPS/NIST.FIPS.140-3.pdf",
        "SP 800-131A Rev 2":"https://nvlpubs.nist.gov/nistpubs/SpecialPublications/NIST.SP.800-131Ar2.pdf",
        "SP 800-57 Part 1 Rev 5": "https://nvlpubs.nist.gov/nistpubs/SpecialPublications/NIST.SP.800-57pt1r5.pdf",
        "NIST IR 8547":     "https://nvlpubs.nist.gov/nistpubs/ir/2024/NIST.IR.8547.ipd.pdf",
        "FIPS 203 ML-KEM":  "https://nvlpubs.nist.gov/nistpubs/FIPS/NIST.FIPS.203.pdf",
        "FIPS 204 ML-DSA":  "https://nvlpubs.nist.gov/nistpubs/FIPS/NIST.FIPS.204.pdf",
        "FIPS 205 SLH-DSA": "https://nvlpubs.nist.gov/nistpubs/FIPS/NIST.FIPS.205.pdf",
}

NIST_FEEDS = [
        {"name": "NIST Cybersecurity News",          "rss": "https://www.nist.gov/news-events/cybersecurity/rss.xml",                  "scrape": False},
        {"name": "NIST Information Technology News", "rss": "https://www.nist.gov/news-events/information%20technology/rss.xml",       "scrape": False},
        {"name": "NIST Cybersecurity Insights Blog", "rss": "https://www.nist.gov/blogs/cybersecurity-insights/rss.xml",               "scrape": False},
]

NIST_NEWS_KEYWORDS = [
        "nist", "fips 140", "fips 186", "fips 197", "fips 203", "fips 204", "fips 205",
        "sp 800", "cmvp", "cavp", "post-quantum", "pqc",
        "ml-kem", "ml-dsa", "slh-dsa", "algorithm transition",
        "key management", "cryptographic module", "csrc",
]

SANITY_MIN_NIST_NEWS = 10
