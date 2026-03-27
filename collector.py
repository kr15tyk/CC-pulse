"""
collector.py — Pulls all data from NIAP APIs, CC Portal, and CCTL labs.

Features:
  - Exponential-backoff retry on every HTTP call
  - Structured logging throughout
  - Sanity-check validation before accepting a snapshot
"""

import logging
import time
import feedparser
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timezone

import config

log = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": (
        "CCPulse/2.0 (automated monitoring tool; "
        "contact your-email@example.com)"
    )
}

SESSION = requests.Session()
SESSION.headers.update(HEADERS)


# ── Retry helper ──────────────────────────────────────────────────────────────

def _fetch_with_retry(fn, url, **kwargs):
    """Call fn(url, **kwargs), retrying up to config.RETRY_ATTEMPTS times
    with exponential backoff.  Returns None on permanent failure."""
    last_exc = None
    for attempt in range(config.RETRY_ATTEMPTS):
        try:
            return fn(url, **kwargs)
        except Exception as exc:
            last_exc = exc
            delay = config.RETRY_BACKOFF_BASE ** attempt
            log.warning(
                "Attempt %d/%d failed for %s: %s — retrying in %ss",
                attempt + 1, config.RETRY_ATTEMPTS, url, exc, delay,
            )
            time.sleep(delay)
    log.error("All %d attempts failed for %s: %s", config.RETRY_ATTEMPTS, url, last_exc)
    return None


# ── Low-level fetch helpers ───────────────────────────────────────────────────

def _do_get_json(url, params=None):
    r = SESSION.get(url, params=params, timeout=30)
    r.raise_for_status()
    return r.json()


def _do_get_html(url):
    r = SESSION.get(url, timeout=30)
    r.raise_for_status()
    return BeautifulSoup(r.text, "lxml")


def get_json(url, params=None):
    result = _fetch_with_retry(_do_get_json, url, params=params)
    if result is None:
        log.warning("get_json returning None for %s", url)
    return result


def get_html(url):
    result = _fetch_with_retry(_do_get_html, url)
    if result is None:
        log.warning("get_html returning None for %s", url)
    return result


def get_rss(url):
    def _parse(u, **kw):
        feed = feedparser.parse(u)
        if feed.get("bozo") and not feed.entries:
            raise ValueError(f"feedparser bozo error: {feed.get('bozo_exception')}")
        return [
            {
                "title":     e.get("title", ""),
                "link":      e.get("link", ""),
                "summary":   e.get("summary", ""),
                "published": e.get("published", ""),
                "id":        e.get("id", e.get("link", "")),
            }
            for e in feed.entries
        ]

    result = _fetch_with_retry(_parse, url)
    return result if result is not None else []


# ── NIAP ─────────────────────────────────────────────────────────────────────

def collect_niap():
    log.info("[NIAP] Collecting...")
    base = config.NIAP_BASE
    eps  = config.NIAP_ENDPOINTS
    data = {}

    log.info("  PCL...")
    pcl = get_json(base + eps["pcl"])
    data["pcl"] = pcl or []

    log.info("  Protection Profiles...")
    pps = get_json(base + eps["pps"])
    data["pps"] = pps or []

    log.info("  Technical Decisions...")
    tds = get_json(base + eps["tds"])
    data["tds"] = tds or []

    log.info("  CCTL Directory...")
    cctls_raw = get_json(base + eps["cctls"])
    data["cctls"] = (
        cctls_raw.get("results", {}).get("cctls", [])
        if cctls_raw else []
    )

    log.info("  Events...")
    ev_curr = get_json(base + eps["events_curr"])
    ev_prev = get_json(base + eps["events_prev"])
    curr = ev_curr.get("results", []) if ev_curr else []
    prev = ev_prev.get("results", []) if ev_prev else []
    data["events"] = curr + prev

    log.info("  News & Announcements...")
    news_raw = get_json(base + eps["news"])
    data["news"] = news_raw.get("results", []) if news_raw else []

    log.info(
        "[NIAP] PCL:%d PPs:%d TDs:%d Events:%d News:%d",
        len(data["pcl"]), len(data["pps"]), len(data["tds"]),
        len(data["events"]), len(data["news"]),
    )
    return data


# ── CC Portal ────────────────────────────────────────────────────────────────

def parsecc_news(soup):
    items = []
    if not soup:
        return items
    content = (
        soup.find("div", {"id": "main"})
        or soup.find("div", class_="main")
        or soup
    )
    for tag in content.find_all(["p", "li"]):
        text = tag.get_text(strip=True)
        link = tag.find("a")
        href = link["href"] if link and link.get("href") else ""
        if len(text) > 20:
            items.append({"text": text, "link": href})
    return items


def parsecc_pps(soup):
    rows = []
    if not soup:
        return rows
    table = soup.find("table")
    if not table:
        return rows
    headers = [th.get_text(strip=True) for th in table.find_all("th")]
    for tr in table.find_all("tr")[1:]:
        cells = [td.get_text(strip=True) for td in tr.find_all("td")]
        if cells:
            row = dict(zip(headers, cells))
            link = tr.find("a")
            row["_link"] = link["href"] if link and link.get("href") else ""
            rows.append(row)
    return rows


def parsecc_products(soup):
    rows = []
    if not soup:
        return rows
    table = soup.find("table")
    if not table:
        return rows
    headers = [th.get_text(strip=True) for th in table.find_all("th")]
    for tr in table.find_all("tr")[1:]:
        cells = [td.get_text(strip=True) for td in tr.find_all("td")]
        if cells:
            rows.append(dict(zip(headers, cells)))
    return rows


def parsecc_communities(soup):
    items = []
    if not soup:
        return items
    content = soup.find("div", {"id": "main"}) or soup
    for a in content.find_all("a"):
        text = a.get_text(strip=True)
        href = a.get("href", "")
        if text:
            items.append({"name": text, "link": href})
    return items


def collect_cc_portal():
    log.info("[CC Portal] Collecting...")
    base  = config.CC_PORTAL_BASE
    pages = config.CC_PORTAL_PAGES
    data  = {}

    log.info("  News...")
    data["news"] = parsecc_news(get_html(base + pages["news"]))
    log.info("  Protection Profiles...")
    data["pps"] = parsecc_pps(get_html(base + pages["pps"]))
    log.info("  Certified Products...")
    data["products"] = parsecc_products(get_html(base + pages["products"]))
    log.info("  Technical Communities...")
    data["communities"] = parsecc_communities(get_html(base + pages["communities"]))
    log.info("  Publications...")
    data["publications"] = parsecc_news(get_html(base + pages["publications"]))
    log.info("  PP RSS Feed...")
    data["pp_rss"] = get_rss(config.CC_PORTAL_RSS)

    log.info(
        "[CC Portal] News:%d PPs:%d Products:%d",
        len(data["news"]), len(data["pps"]), len(data["products"]),
    )
    return data


# ── CCTL Labs ────────────────────────────────────────────────────────────────

def scrapelab_items(url):
    """Generic scraper — extracts headlines/links from a lab page."""
    soup = get_html(url)
    if not soup:
        return []
    items = []
    for tag in soup.find_all(["h2", "h3", "h4", "article"]):
        a = tag.find("a") if tag.name != "a" else tag
        if a and a.get_text(strip=True):
            items.append({
                "title":     a.get_text(strip=True),
                "link":      a.get("href", ""),
                "published": "",
                "id":        a.get("href", a.get_text(strip=True)),
            })
    return items[:20]


def collect_cctl_labs():
    log.info("[CCTL Labs] Collecting...")
    results = {}
    for lab in config.CCTL_LABS:
        name = lab["name"]
        log.info("  %s...", name)
        if lab["rss"]:
            items = get_rss(lab["rss"])
        elif lab["scrape"] and lab["url"]:
            items = scrapelab_items(lab["url"])
        else:
            items = []
        results[name] = items
        log.info("    -> %d items", len(items))
    return results


# ── Sanity validation ─────────────────────────────────────────────────────────

class SanityError(RuntimeError):
    """Raised when collected data looks like a fetch failure."""


def validate_snapshot(snapshot):
    """Raise SanityError if critical collections look suspiciously empty.

    This prevents a network blip from writing a near-empty snapshot and
    producing thousands of false 'removed' diff events the next day.
    """
    pcl_count = len(snapshot.get("niap", {}).get("pcl", []))
    pps_count = len(snapshot.get("niap", {}).get("pps", []))

    if pcl_count < config.SANITY_MIN_PCL:
        raise SanityError(
            f"NIAP PCL returned only {pcl_count} products "
            f"(minimum expected: {config.SANITY_MIN_PCL}). "
            "Snapshot rejected — possible fetch failure."
        )
    if pps_count < config.SANITY_MIN_PPS:
        raise SanityError(
            f"NIAP PPs returned only {pps_count} entries "
            f"(minimum expected: {config.SANITY_MIN_PPS}). "
            "Snapshot rejected — possible fetch failure."
        )
          csfc_apl_count = len(snapshot.get("csfc", {}).get("pages", {}).get("apl", []))
          if csfc_apl_count < config.SANITY_MIN_CSFC_APL:
                    log.warning(
                                  "CSfC APL returned only %d items (minimum expected: %d). "
                                  "NSA site may be down or blocking — snapshot kept but flagged.",
                                  csfc_apl_count,
                                  config.SANITY_MIN_CSFC_APL,
                    )
          # CC Crypto Catalog publications page sanity check (warn only)
    crypto_pubs_count = len(snapshot.get("cc_crypto", {}).get("pages", {}).get("publications", []))
    if crypto_pubs_count < config.SANITY_MIN_CC_CRYPTO_PUBS:
              log.warning(
                            "CC Crypto publications page returned only %d items (minimum expected: %d). "
                            "CC Portal may be down — snapshot kept but flagged.",
                            crypto_pubs_count,
                            config.SANITY_MIN_CC_CRYPTO_PUBS,
              )
              # NIST CSRC news page sanity check (warn only — external site)
          nist_news_count = len(snapshot.get("nist", {}).get("pages", {}).get("news", []))
        if nist_news_count < config.SANITY_MIN_NIST_NEWS:
                      log.warning(
                                                    "NIST CSRC news page returned only %d items (minimum expected: %d). "
                                                    "NIST CSRC may be down or blocking — snapshot kept but flagged.",
                                                    nist_news_count,
                                                    config.SANITY_MIN_NIST_NEWS,
                      )
          log.info(
                                "[Validation] Sanity checks passed (PCL:%d PPs:%d CSfC-APL:%d CryptoPubs:%d NISTNews:%d).",
                        pcl_count, pps_count, csfc_apl_count, crypto_pubs_count, nist_news_count,
          )


# ── Master snapshot ───────────────────────────────────────────────────────────

def collect_all():
    """Collect everything, validate, and return a timestamped snapshot dict."""
    snapshot = {
        "schema_version": config.SNAPSHOT_SCHEMA_VERSION,
        "collected_at":   datetime.now(timezone.utc).isoformat(),
        "niap":           collect_niap(),
        "cc_portal":      collect_cc_portal(),
        "cctl_labs":      collect_cctl_labs(),
              "csfc":      collect_csfc(),
              "cc_crypto": collect_cc_crypto(),
                  "nist":      collect_nist(),
    }
    validate_snapshot(snapshot)   # raises SanityError on bad data
    return snapshot

# ── CSfC (Commercial Solutions for Classified) ──────────────────────────────

def _poll_cp_headers() -> dict:
      """HEAD-poll each Capability Package PDF for Last-Modified / ETag changes.
          Returns a dict keyed by CP name with header metadata."""
      results = {}
      for name, url in config.CSFC_CAPABILITY_PACKAGES.items():
                log.info("  [CSfC CP] HEAD %s ...", name)
                try:
                              r = SESSION.head(url, timeout=20, allow_redirects=True)
                              results[name] = {
                                                "url": url,
                                                "status_code": r.status_code,
                                                "last_modified": r.headers.get("Last-Modified", ""),
                                                "etag": r.headers.get("ETag", ""),
                                                "content_length": r.headers.get("Content-Length", ""),
                              }
except Exception as exc:
            log.warning("  [CSfC CP] HEAD failed for %s: %s", name, exc)
            results[name] = {"url": url, "status_code": None,
                                                          "last_modified": "", "etag": "", "content_length": ""}
    return results


def _scrape_csfc_page(path: str) -> list:
      """Scrape a single NSA CSfC page and return a list of text/link items."""
      url = config.CSFC_BASE + path
      soup = get_html(url)
      if not soup:
                return []
            items = []
    content = (
              soup.find("div", {"id": "ContentPane"})
              or soup.find("main")
              or soup.find("div", class_="field-items")
              or soup
    )
    # Grab all paragraph and list-item text + links
    for tag in content.find_all(["p", "li", "h2", "h3", "h4"]):
              text = tag.get_text(separator=" ", strip=True)
              link = tag.find("a")
              href = link["href"] if link and link.get("href") else ""
              if len(text) > 15:
                            items.append({"text": text[:400], "link": href})
                    # Deduplicate by text prefix
    seen: set = set()
    unique = []
    for item in items:
              key = item["text"][:80]
              if key not in seen:
                            seen.add(key)
                            unique.append(item)
                    return unique


def collect_csfc() -> dict:
      """Collect all CSfC monitoring data:
          - NSA CSfC page snapshots (home, APL, capability packages, FAQ, etc.)
              - HTTP header polling of Capability Package PDFs
                  - CSfC-tagged RSS / news feeds
                      """
    log.info("[CSfC] Collecting...")
    data: dict = {
              "pages": {},
              "capability_package_headers": {},
              "feeds": {},
    }

    # 1. Scrape CSfC pages
    for page_key, path in config.CSFC_PAGES.items():
              log.info("  [CSfC] Scraping page: %s (%s)...", page_key, path)
        data["pages"][page_key] = _scrape_csfc_page(path)
        log.info("  -> %d items", len(data["pages"][page_key]))

    # 2. HEAD-poll Capability Package PDFs
    log.info("  [CSfC] Polling Capability Package PDF headers...")
    data["capability_package_headers"] = _poll_cp_headers()

    # 3. RSS / news feeds
    for feed in config.CSFC_FEEDS:
              name = feed["name"]
        log.info("  [CSfC] Feed: %s...", name)
        if feed.get("rss"):
                      items = get_rss(feed["rss"])
elif feed.get("scrape") and feed.get("url"):
            items = scrapelab_items(feed["url"])
else:
            items = []
        data["feeds"][name] = items
        log.info("  -> %d items", len(items))

    apl_count = len(data["pages"].get("apl", []))
    cp_count = len(data["capability_package_headers"])
    log.info(
              "[CSfC] APL items:%d CPs polled:%d",
              apl_count,
              cp_count,
    )
    return data

# ── CC Crypto Catalog & Working Group ────────────────────────────────────────

def _scrape_cc_crypto_page(path: str) -> list:
      """Scrape a CC Portal page and return text/link items.
          Reuses the same BeautifulSoup extraction pattern as _scrape_csfc_page
              but targets the CC Portal base URL instead of the NSA base URL."""
    url = config.CC_CRYPTO_BASE + path
    soup = get_html(url)
    if not soup:
              return []
    items = []
    content = (
              soup.find("div", {"id": "main"})
              or soup.find("div", {"id": "content"})
              or soup.find("main")
              or soup
    )
    for tag in content.find_all(["p", "li", "h2", "h3", "h4", "td"]):
              text = tag.get_text(separator=" ", strip=True)
        link = tag.find("a")
        href = link["href"] if link and link.get("href") else ""
        if len(text) > 10:
                      items.append({"text": text[:500], "link": href})
              # Deduplicate on first 120 chars
    seen: set = set()
    unique = []
    for item in items:
              key = item["text"][:120]
        if key not in seen:
                      seen.add(key)
                      unique.append(item)
              return unique


def _poll_crypto_doc_headers() -> dict:
      """HEAD-poll each CC Crypto Catalog PDF for Last-Modified / ETag changes.
          Returns a dict keyed by document name with HTTP header metadata."""
    results = {}
    for name, url in config.CC_CRYPTO_DOCS.items():
              log.info("  [CC Crypto] HEAD %s ...", name)
        try:
                      r = SESSION.head(url, timeout=20, allow_redirects=True)
                      results[name] = {
                                        "url": url,
                                        "status_code": r.status_code,
                                        "last_modified": r.headers.get("Last-Modified", ""),
                                        "etag": r.headers.get("ETag", ""),
                                        "content_length": r.headers.get("Content-Length", ""),
                      }
except Exception as exc:
            log.warning("  [CC Crypto] HEAD failed for %s: %s", name, exc)
            results[name] = {
                              "url": url,
                              "status_code": None,
                              "last_modified": "",
                              "etag": "",
                              "content_length": "",
            }
    return results


def collect_cc_crypto() -> dict:
      """Collect CC Crypto Catalog and working group monitoring data:
          - CC Portal page snapshots (publications, news, communities)
              - HTTP header polling of crypto-related PDFs (CCDB-018, CC:2022 Part 2, etc.)
                  """
    log.info("[CC Crypto] Collecting...")
    data: dict = {
              "pages": {},
              "doc_headers": {},
    }

    # 1. Scrape CC Portal pages for crypto catalog / working group content
    for page_key, path in config.CC_CRYPTO_PAGES.items():
              log.info("  [CC Crypto] Scraping page: %s (%s)...", page_key, path)
        data["pages"][page_key] = _scrape_cc_crypto_page(path)
        log.info("  -> %d items", len(data["pages"][page_key]))

    # 2. HEAD-poll Crypto Catalog PDF documents
    log.info("  [CC Crypto] Polling document headers...")
    data["doc_headers"] = _poll_crypto_doc_headers()

    pubs_count = len(data["pages"].get("publications", []))
    docs_polled = len(data["doc_headers"])
    log.info(
              "[CC Crypto] publications-items:%d docs-polled:%d",
              pubs_count,
              docs_polled,
    )
    return data


# ── NIST CSRC Monitoring ──────────────────────────────────────────────────────

def _scrape_nist_page(path: str) -> list:
      """Scrape a NIST CSRC page and return a list of text/link items.
          Targets the CSRC base URL. Falls back gracefully if the page structure
              differs across CSRC sub-pages (news list, FIPS table, CMVP MIP table, etc.)
                  """
    url = config.NIST_CSRC_BASE + path
    soup = get_html(url)
    if not soup:
              return []
    items = []
    content = (
              soup.find("div", {"id": "main-content"})
              or soup.find("main")
              or soup.find("div", {"class": "container"})
              or soup
    )
    # CMVP MIP page uses a table — extract rows
    table = content.find("table") if content else None
    if table:
              headers = [th.get_text(strip=True) for th in table.find_all("th")]
        for tr in table.find_all("tr")[1:]:
                      cells = [td.get_text(strip=True) for td in tr.find_all("td")]
                      if cells:
                                        text = " | ".join(cells[:4])  # Module | Vendor | Standard | Status
                link = tr.find("a")
                href = link["href"] if link and link.get("href") else ""
                if text.strip():
                                      items.append({"text": text[:400], "link": href})
else:
        # News / project pages: extract headlines, paragraphs, list items
          for tag in content.find_all(["h2", "h3", "h4", "p", "li", "td"]):
                        text = tag.get_text(separator=" ", strip=True)
                        link = tag.find("a")
                        href = link["href"] if link and link.get("href") else ""
                        if len(text) > 20:
                                          items.append({"text": text[:400], "link": href})
                              # Deduplicate on first 120 chars
                              seen: set = set()
    unique = []
    for item in items:
              key = item["text"][:120]
        if key not in seen:
                      seen.add(key)
            unique.append(item)
    return unique


def _poll_nist_doc_headers() -> dict:
      """HEAD-poll each NIST crypto PDF for Last-Modified / ETag changes.
          Returns a dict keyed by document name with HTTP header metadata."""
    results = {}
    for name, url in config.NIST_CRYPTO_DOCS.items():
              log.info("  [NIST Docs] HEAD %s ...", name)
        try:
                      r = SESSION.head(url, timeout=20, allow_redirects=True)
            results[name] = {
                              "url": url,
                              "status_code": r.status_code,
                              "last_modified": r.headers.get("Last-Modified", ""),
                              "etag": r.headers.get("ETag", ""),
                              "content_length": r.headers.get("Content-Length", ""),
            }
except Exception as exc:
            log.warning("  [NIST Docs] HEAD failed for %s: %s", name, exc)
            results[name] = {
                              "url": url,
                              "status_code": None,
                              "last_modified": "",
                              "etag": "",
                              "content_length": "",
            }
    return results


def collect_nist() -> dict:
      """Collect NIST CSRC monitoring data:
          - CSRC page snapshots (news, FIPS, CMVP MIP, PQC project, crypto standards)
              - HTTP header polling of key NIST crypto PDFs
                  - NIST cybersecurity RSS feeds
                      """
    log.info("[NIST] Collecting...")
    data: dict = {
              "pages": {},
              "doc_headers": {},
              "feeds": {},
    }

    # 1. Scrape NIST CSRC pages
    for page_key, path in config.NIST_CSRC_PAGES.items():
              log.info("  [NIST] Scraping page: %s (%s)...", page_key, path)
        data["pages"][page_key] = _scrape_nist_page(path)
        log.info("  -> %d items", len(data["pages"][page_key]))

    # 2. HEAD-poll NIST crypto PDF documents
    log.info("  [NIST] Polling document headers...")
    data["doc_headers"] = _poll_nist_doc_headers()

    # 3. RSS / news feeds
    for feed in config.NIST_FEEDS:
              name = feed["name"]
        log.info("  [NIST] Feed: %s...", name)
        if feed.get("rss"):
                      items = get_rss(feed["rss"])
elif feed.get("scrape") and feed.get("url"):
            items = scrapelab_items(feed["url"])
else:
            items = []
        data["feeds"][name] = items
        log.info("  -> %d items", len(items))

    news_count = len(data["pages"].get("news", []))
    docs_polled = len(data["doc_headers"])
    log.info(
              "[NIST] news-items:%d docs-polled:%d",
              news_count,
              docs_polled,
    )
    return data
