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
EMAIL_USERNAME = os.environ.get("CC_EMAIL_USERNAME", "your-sender@gmail.com")
EMAIL_PASSWORD = os.environ.get("CC_EMAIL_PASSWORD", "")
EMAIL_FROM = os.environ.get("CC_EMAIL_FROM", "CC Pulse <your-sender@gmail.com>")
EMAIL_RECIPIENTS = [
    r.strip()
    for r in os.environ.get("CC_EMAIL_RECIPIENTS", "you@example.com").split(",")
    if r.strip()
]
EMAIL_SUBJECT = "Weekly CC Pulse -- {date}"

# -- Notifications (Webex) --------------------------------------------------
WEBEX_BOT_TOKEN = os.environ.get("CC_WEBEX_BOT_TOKEN", "")
WEBEX_ROOM_ID = os.environ.get("CC_WEBEX_ROOM_ID", "")

# -- Notifications (Generic Webhook / MS Teams) -----------------------------
WEBHOOK_URL = os.environ.get("CC_WEBHOOK_URL", "")

# -- Dry-run mode (suppresses all Webex/email sends) ------------------------
DRY_RUN = os.environ.get("CC_DRY_RUN", "").lower() in ("1", "true", "yes")

# -- Dashboard --------------------------------------------------------------
DASHBOARD_DIR = "docs"
STAGING_DIR = "docs/staging"
DASHBOARD_FILENAME = "cc_dashboard.html"

# -- Snapshots --------------------------------------------------------------
# CC_SNAPSHOT_DIR / CC_DIFF_DIR allow the Actions workflow to redirect
# snapshot storage to a separate git worktree (the `snapshots` orphan
# branch) without touching the source-code history on `main`.
# Defaults preserve backward-compatibility for local development.
SNAPSHOT_DIR = os.environ.get("CC_SNAPSHOT_DIR", "snapshots")
DIFF_DIR = os.environ.get("CC_DIFF_DIR", "snapshots/diffs")

# -- Retry Settings ---------------------------------------------------------
RETRY_ATTEMPTS = 4
RETRY_BACKOFF_BASE = 2

# -- Sanity Check Thresholds ------------------------------------------------
SANITY_MIN_PCL = 50
SANITY_MIN_PPS = 10
SANITY_MIN_CSFC_APL = 5
SANITY_MIN_CSFC_ANNOUNCEMENTS = 1
SANITY_MIN_CC_CRYPTO_PUBS = 5
SANITY_MIN_CC_PORTAL_NEWS = 50
SANITY_MIN_CC_PORTAL_PPS = 100
SANITY_MIN_CC_PORTAL_PRODUCTS = 500

# -- Per-collection collapse guard ------------------------------------------
# A domain can pass its representative sanity check (e.g. NIAP PCL is fine)
# while a *secondary* collection inside it (a page or feed) silently collapses
# to near-zero from a partial fetch failure — producing false mass-removal
# diffs. The collapse guard retains last-known-good for any collection that
# had at least COLLAPSE_MIN_BASELINE items previously and dropped below half
# that count. Mirrors the NIAP news/policies suspicious-drop logic, generalised
# to every domain's pages/feeds/top-level lists.
COLLAPSE_MIN_BASELINE = 8

# -- NIAP API ---------------------------------------------------------------
NIAP_BASE = "https://www.niap-ccevs.org"
NIAP_ENDPOINTS = {
    "pcl": "/api/project/product/pcl_products_all/",
    "pps": "/api/protection-profile/public_pps_all/",
    "tds": "/api/technical-decision/frontend_tds/",
    "events_curr": "/api/publish/announcements/get_events_frontend/?limit=200&offset=0&current=true",
    "events_prev": "/api/publish/announcements/get_events_frontend/?limit=200&offset=0&previous=true",
    "news": "/api/publish/announcements/get_news_frontend/?limit=500&offset=0",
    "policies_active": "/api/publish/policies/get_public_policies/?archived=false",
    "policies_archived": "/api/publish/policies/get_public_policies/?archived=true",
}
NIAP_ANNOUNCEMENTS_URL = NIAP_BASE + "/announcements"
NIAP_POLICIES_URL = NIAP_BASE + "/policies"
SANITY_MIN_NIAP_NEWS = 1
SANITY_MIN_NIAP_POLICIES = 1

# Protection Profiles whose published documents can carry CNSA/PQC-relevant
# requirements for network products. S/MIME and CMC are intentionally excluded:
# they are not CC Pulse monitoring targets.
NIAP_PQC_PP_PATTERNS = (
    "CPP_ND",
    "PP_APP",
    "PKG_TLS",
    "PKG_SSH",
    "PKG_X509",
    "MOD_VPNGW",
    "MOD_MACSEC",
    "MOD_WLAN",
)
NIAP_PP_FILES_ENDPOINT = (
    "/api/file/get_public_files_by_type_and_type_id/"
    "?file_type=protection-profile&file_type_id={pp_id}"
)
NIAP_PP_STATIC_PATH = "/static_html/protection-profile/{pp_id}/{filename}"
NIAP_PP_DOCUMENT_MAX_BYTES = 10 * 1024 * 1024
SANITY_MIN_NIAP_PQC_PP_FILES = 1
SANITY_MIN_NIAP_PQC_PP_HASHES = 1

# -- CC Portal --------------------------------------------------------------
CC_PORTAL_BASE = "https://www.commoncriteriaportal.org"
CC_PORTAL_PAGES = {
    "news": "/news/index.cfm",
    "pps": "/pps/index.cfm",
    "products": "/products/index.cfm",
    "communities": "/communities/index.cfm",
    "publications": "/cc/index.cfm",
}
CC_PORTAL_RSS = "https://www.commoncriteriaportal.org/rss/pps.xml"
CC_PORTAL_EMBEDDED_JSON_MAX_CHARS = 20 * 1024 * 1024
RSS_FEED_MAX_BYTES = 2 * 1024 * 1024

# -- CCTL Lab Feeds ---------------------------------------------------------
CCTL_LABS = [
    # RSS feed working and CC-focused
    {"name": "Lightship Security", "rss": "https://lightshipsec.com/feed/", "url": "https://lightshipsec.com/blog/", "scrape": False},
    # RSS broken after site rebuild (2025); scrape blog directly
    {"name": "atsec", "rss": None, "url": "https://www.atsec.com/blog/", "scrape": True},
]

# -- Product Filters --------------------------------------------------------
CISCO_VENDOR_KEYWORDS = ["cisco"]
NDCPP_PP_KEYWORDS = ["CPP_ND"]

# -- News Category Keywords -------------------------------------------------
NEWS_CATEGORY_KEYWORDS = {
    "LABGRAM": ["labgram"],
    "VALGRAM": ["valgram"],
    "POLICY": ["policy", "policies"],
    "PUBLICATION": ["publication", "published", "progress report"],
    "EVENT": ["event", "conference", "workshop", "webinar"],
    "CISA": ["cisa", "emergency directive", "vulnerability"],
    "PP UPDATE": ["pp-module", "protection profile", "cpp_", "pp_"],
    "CSFC": ["csfc", "commercial solutions for classified"],
    "NATO": ["nato", "niapc", "niapcl"],
    "EUCC": ["eucc", "enisa", "european union agency for cybersecurity"],
    "CRYPTO": [
        "ccdb-018", "crypto catalog", "cryptograph", "fcs_",
        "key establishment", "digital signature", "hash function",
        "random bit generator", "rbg", "algorithm transition",
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
    "CNSA 2.0",
    "ML-KEM",
    "ML-DSA",
    "SLH-DSA",
    "post-quantum",
    "RFC 9846",
    "802.11bt",
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
]

# NOTE: BODY_WATCH_KEYWORDS was removed (2026-07-10). It was never referenced
# by any code — keywords added there (notably "cisco") silently did nothing.
# The active list is WATCH_KEYWORDS above; Cisco-specific detection uses
# CISCO_VENDOR_KEYWORDS and the dedicated cisco_added diff paths.

# =============================================================
# CSfC (Commercial Solutions for Classified) Monitoring
# =============================================================
CSFC_BASE = "https://www.nsa.gov"

CSFC_PAGES = {
    "home": "/Resources/Commercial-Solutions-for-Classified-Program/",
    "apl": "/Resources/Commercial-Solutions-for-Classified-Program/Components-List/",
    "cap_packages": "/Resources/Commercial-Solutions-for-Classified-Program/Capability-Packages/",
    "faq": "/Resources/Commercial-Solutions-for-Classified-Program/faq/",
    "registration": "/Resources/Commercial-Solutions-for-Classified-Program/Solution-Registration/",
    "kmr": "/Resources/Commercial-Solutions-for-Classified-Program/Customer-Handbook/",
    "announcements": "/Resources/Commercial-Solutions-for-Classified-Program/Announcements/",
}


# -- CSfC Product List (approved products) ----------------------------------
# Monitored separately from component selections for Cisco-specific alerting.
CSFC_PRODUCT_LIST_URL = "https://www.nsa.gov/Resources/Commercial-Solutions-for-Classified-Program/Components-List/"  # CSfC APL: confirmed correct (Components List = approved CC-evaluated products for CSfC)

CSFC_FEEDS = [
    {"name": "NSA Cybersecurity Advisories", "rss": "https://www.nsa.gov/DesktopModules/ArticleCS/RSS.ashx?ContentType=1&Site=1282&max=20", "scrape": False, "minimum": 1},
    {"name": "CISA Alerts", "rss": "https://www.cisa.gov/cybersecurity-advisories/all.xml", "scrape": False, "minimum": 1},
    {"name": "DISA STIGs & APL News", "rss": None, "url": "https://public.cyber.mil/stigs/", "scrape": True, "minimum": 1},
]

CSFC_APL_COMPONENT_KEYWORDS = {
    "SSH": ["ssh", "secure shell"],
    "TLS/VPN": ["tls", "vpn", "ipsec", "ssl"],
    "WLAN": ["wlan", "wi-fi", "wireless", "802.11"],
    "DAR": ["data at rest", "dar", "self-encrypting", "sed", "fde"],
    "MDM": ["mdm", "mobile device management", "uem"],
    "Email": ["email", "s/mime", "smime"],
    "VoIP": ["voip", "sip", "voice over"],
    "Multi-Site": ["wan", "multi-site", "sd-wan"],
}

# Stable capability-package identities used to find and hash the current NSA
# documents even when their CDN URLs or version tokens change.
CSFC_PQC_DOCUMENT_LABELS = {
    "mobile_access": "Mobile Access Capability Package",
    "campus_wlan": "Campus WLAN Capability Package",
    "multi_site_connectivity": "Multi-Site Connectivity Capability Package",
    "key_management_requirements": "Key Management Requirements Annex",
    "symmetric_key_management_requirements": "Symmetric Key Management Requirements Annex",
}
CSFC_DOCUMENT_MAX_BYTES = 30 * 1024 * 1024
SANITY_MIN_CSFC_PQC_DOCUMENTS = len(CSFC_PQC_DOCUMENT_LABELS)

# =============================================================
# NATO NIAPCL Monitoring (manual-only domain)
# NATO's Information Assurance Product Catalogue blocks automated
# access (403 + warnings against scraping), so CC Pulse never
# contacts ia.nato.int. The Cisco baseline is maintained entirely
# by hand:
#   1. Weekly reminder email (scripts/send_nato_capture_reminder.py)
#      prompts a human to visit the Cisco-filtered NIAPCL search.
#   2. The copied product text is submitted via the "NATO Cisco
#      Baseline Update" GitHub Issue Form.
#   3. scripts/nato_issue_intake.py diffs it against the stored
#      baseline, fires the normal Cisco alerts, and updates the
#      latest snapshot.
# The daily pipeline carries the stored baseline forward unchanged
# and reports the domain as healthy/manual (see MANUAL_DOMAINS).
# =============================================================

# Canonical URL for NATO NIAPCL alerts, dashboard links, and the
# manual capture reminder. Never fetched by CC Pulse itself.
NATO_NIAPCL_URL = "https://www.ia.nato.int/Search/NIAPC/AND/Category_/Manufacturer_10/Country_/SecurityGroup_"

# Domains whose snapshot data is maintained by a human workflow rather
# than automated collection. These are excluded from daily collection
# and from fetch-based source-health checks: the prior snapshot's data
# is carried forward and the domain is reported as healthy ("manual").
MANUAL_DOMAINS = frozenset({"nato"})

# =============================================================
# EUCC (EU Common Criteria) / ENISA Monitoring
# Two sources:
# 1. EUCC scheme requirements / news (enisa.europa.eu/browse-topic/eucc_en)
# 2. EUCC certificates (enisa.europa.eu/certificates_en)
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
EUCC_CONTINUITY_MIN_BASELINE = 10
EUCC_MIN_IDENTITY_OVERLAP = 0.70

# =============================================================
# ND-iTC (Network Device iTC) Monitoring — nd-itc.github.io
#
# Monitors the ND-iTC's Technical Decisions and Allowed-With lists.
# Naming: the ND-iTC's Technical Decisions are called "NIT RFIs"
# throughout CC Pulse to distinguish them from NIAP Technical
# Decisions — both bodies issue "Technical Decisions" and mixing them
# up in a notification would be actively misleading. The Allowed-With
# lists are per-PP (NDcPP, FW PP-Module).
# =============================================================
ND_ITC_BASE = "https://nd-itc.github.io"

ND_ITC_PAGES = {
    # Active NIT RFIs (ND-iTC Technical Decisions table)
    "nit_rfis": "/TD/tech_dec.html",
    # Archived NIT RFIs — monitored so active→archived moves are detected
    "nit_rfis_archived": "/TD/tech_dec_arch.html",
    # Allowed-With list for the Network Device cPP
    "awl_ndcpp": "/AWL/NDcPP_allowed_with_list.html",
    # Allowed-With list for the Stateful Traffic Filter Firewall PP-Module
    "awl_fw": "/AWL/FW_allowed_with_list.html",
}

# Canonical URLs for alerts and dashboard links
ND_ITC_TD_URL = ND_ITC_BASE + "/TD/tech_dec.html"
ND_ITC_AWL_URLS = {
    "awl_ndcpp": ND_ITC_BASE + "/AWL/NDcPP_allowed_with_list.html",
    "awl_fw": ND_ITC_BASE + "/AWL/FW_allowed_with_list.html",
}

# Sanity minimum: the active NIT RFI table has ~30 published entries
SANITY_MIN_ND_ITC_RFIS = 5

# =============================================================
# CNSA 2.0 IETF profile monitoring
# =============================================================
IETF_DATATRACKER_BASE = "https://datatracker.ietf.org"
IETF_DATATRACKER_API = IETF_DATATRACKER_BASE + "/api/v1/doc"
IETF_DRAFT_ARCHIVE_BASE = "https://www.ietf.org/archive/id"
RFC_EDITOR_BASE = "https://www.rfc-editor.org/rfc"

# PKIX applies to CC. S/MIME and CMC profiles are deliberately not monitored.
IETF_CNSA_DOCUMENTS = (
    "draft-guthrie-cnsa2-ipsec-profile",
    "draft-becker-cnsa2-tls-profile",
    "draft-becker-cnsa2-ssh-profile",
    "draft-jenkins-cnsa2-pkix-profile",
    "rfc9846",
)
IETF_TEXT_MAX_BYTES = 2 * 1024 * 1024
SANITY_MIN_IETF_CNSA_DOCUMENTS = len(IETF_CNSA_DOCUMENTS)

# =============================================================
# IEEE 802.11bt post-quantum cryptography monitoring
# =============================================================
IEEE_80211_TIMELINE_URL = "https://www.ieee802.org/11/Reports/802.11_Timelines.htm"
IEEE_80211_HOME_URL = "https://www.ieee802.org/11/"
SANITY_MIN_IEEE_PQC_RECORDS = 1

# =============================================================
# CC Crypto Catalog & Working Group Monitoring
# =============================================================
CC_CRYPTO_BASE = "https://www.commoncriteriaportal.org"

# CC_CRYPTO_DOCS removed (fix #27): doc header polling was unreliable.
# CC Crypto changes are tracked via CC_CRYPTO_PAGES page scrapes.

CC_CRYPTO_PAGES = {
    "publications": "/cc/index.cfm",
    "news": "/news/index.cfm",
    "communities": "/communities/index.cfm",
}

CC_CRYPTO_NEWS_KEYWORDS = [
    "ccdb-018", "crypto catalog", "cryptography", "cryptographic",
    "specification of functional requirements for cryptography",
    "ccdb working group", "crypto working group",
    "fcs_", "algorithm", "key establishment", "key generation",
    "digital signature", "hash function", "random bit generator", "rbg",
]
