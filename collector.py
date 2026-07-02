"""
collector.py — Pulls all data from NIAP APIs, CC Portal, CCTL labs,
              CSfC pages, CC Crypto Catalog, and NIST CSRC.

Features:
  - Exponential-backoff retry on every HTTP call
  - Partial-GET content-hash fallback for PDF polling (fix #10)
  - Parallel domain collection via ThreadPoolExecutor (fix #16)
  - Structured logging throughout
  - Sanity-check validation before accepting a snapshot
  - Structured CSfC APL records with component type tagging (fix #18)
  - CCTL scraper health warnings for empty-result detectors (fix #19)
  - Structured CMVP MIP table parsing with named fields (fix #20)
"""

import hashlib
import copy
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import feedparser
import requests
from curl_cffi import requests as curl_requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin
from datetime import datetime, timezone

import config

log = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
}

SESSION = requests.Session()
SESSION.headers.update(HEADERS)


# ── Retry helper ──────────────────────────────────────────────────────────────
def _fetch_with_retry(fn, url, **kwargs):
    """Call fn(url, **kwargs), retrying up to config.RETRY_ATTEMPTS times
    with exponential backoff. Returns None on permanent failure."""
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
    if r.status_code == 403:
        log.warning(
            "403 Forbidden for %s — WAF may be blocking this request "
            "(bot-detection, IP reputation, or missing browser headers)",
            url,
        )
        if url.lower().startswith(config.CSFC_BASE.lower()):
            log.info("Retrying NSA page with a Chrome-compatible TLS fingerprint...")
            browser_response = curl_requests.get(
                url,
                impersonate="chrome",
                timeout=30,
                allow_redirects=True,
            )
            browser_response.raise_for_status()
            return BeautifulSoup(browser_response.text, "lxml")
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


# ── Partial-GET content-hash fallback (fix #10) ───────────────────────────────
def _partial_get_hash(url: str, nbytes: int = 2048) -> str:
    """Fetch the first `nbytes` bytes of a URL and return an MD5 hex-digest.

    Used as a fallback when a server does not return useful Last-Modified /
    ETag headers (common for NSA and NIST PDF servers behind CDNs/WAFs).
    If the partial GET fails, returns an empty string so the diff logic
    still works without crashing.
    """
    try:
        r = SESSION.get(
            url,
            headers={"Range": f"bytes=0-{nbytes - 1}"},
            timeout=20,
            allow_redirects=True,
            stream=True,
        )
        # Accept both 206 Partial Content and 200 OK (server may ignore Range)
        if r.status_code in (200, 206):
            chunk = b""
            for block in r.iter_content(chunk_size=nbytes):
                chunk += block
                if len(chunk) >= nbytes:
                    break
            return hashlib.md5(chunk).hexdigest()
        return ""
    except Exception as exc:
        log.debug("_partial_get_hash failed for %s: %s", url, exc)
        return ""


def _poll_doc_headers(doc_dict: dict, domain_tag: str) -> dict:
    """Generic HEAD-poll helper for any dict of {name: url} documents.

    Strategy (fix #10):
      1. Try HTTP HEAD for Last-Modified, ETag, Content-Length.
      2. If ALL three are empty (server doesn't serve them), fall back to
         a partial GET of the first 2 KB and store an MD5 of that prefix.
    Returns a dict keyed by document name.
    """
    results = {}
    for name, url in doc_dict.items():
        log.debug("  [%s] HEAD %s ...", domain_tag, name)
        entry: dict = {
            "url":            url,
            "status_code":    None,
            "last_modified":  "",
            "etag":           "",
            "content_length": "",
            "partial_hash":   "",  # populated only as fallback
        }
        try:
            r = SESSION.head(url, timeout=20, allow_redirects=True)
            entry["status_code"]    = r.status_code
            entry["last_modified"]  = r.headers.get("Last-Modified", "")
            entry["etag"]           = r.headers.get("ETag", "")
            entry["content_length"] = r.headers.get("Content-Length", "")

            # Fallback: if server returns no useful change-detection headers
            if not any([entry["last_modified"], entry["etag"], entry["content_length"]]):
                log.debug(
                    "  [%s] No useful headers for %s — using partial GET hash.",
                    domain_tag, name
                )
                entry["partial_hash"] = _partial_get_hash(url)

        except Exception as exc:
            log.warning("  [%s] HEAD failed for %s: %s", domain_tag, name, exc)
            # Still attempt partial GET as a last resort
            entry["partial_hash"] = _partial_get_hash(url)

        results[name] = entry
    return results


# ── NIAP ──────────────────────────────────────────────────────────────────────
def _get_browser_json(url: str):
    """Fetch JSON with a browser TLS fingerprint for NIAP WAF-sensitive APIs."""
    def _fetch(target: str):
        response = curl_requests.get(
            target,
            impersonate="chrome",
            timeout=30,
            allow_redirects=True,
            headers={"Accept": "application/json"},
        )
        response.raise_for_status()
        return response.json()

    result = _fetch_with_retry(_fetch, url)
    if result is None:
        log.warning("browser JSON fetch returning None for %s", url)
    return result


def _get_paginated_results(url: str, fetch_json=None) -> tuple[list[dict], dict]:
    """Fetch every page from a DRF-style list endpoint.

    The returned metadata deliberately distinguishes a valid empty result from
    a failed or incomplete request.  The orchestrator uses it to retain only
    the affected NIAP subcollection's last-known-good data.
    """
    fetch_json = fetch_json or get_json
    items: list[dict] = []
    seen_urls: set[str] = set()
    next_url: str | None = url
    reported_count: int | None = None
    pages = 0

    while next_url:
        # NIAP currently emits http:// pagination links even though the public
        # site is HTTPS.  Normalize them before following the next page.
        if next_url.startswith("http://www.niap-ccevs.org/"):
            next_url = "https://www.niap-ccevs.org/" + next_url.split(
                "http://www.niap-ccevs.org/", 1
            )[1]
        if next_url in seen_urls:
            return items, {
                "success": False,
                "complete": False,
                "observed": len(items),
                "reported_count": reported_count,
                "pages": pages,
                "detail": "pagination loop detected",
            }
        seen_urls.add(next_url)

        payload = fetch_json(next_url)
        if payload is None:
            return items, {
                "success": False,
                "complete": False,
                "observed": len(items),
                "reported_count": reported_count,
                "pages": pages,
                "detail": "request failed",
            }
        pages += 1

        if isinstance(payload, list):
            page_items = payload
            next_url = None
            if reported_count is None:
                reported_count = len(payload)
        elif isinstance(payload, dict) and isinstance(payload.get("results"), list):
            page_items = payload["results"]
            if reported_count is None and isinstance(payload.get("count"), int):
                reported_count = payload["count"]
            next_url = payload.get("next")
        else:
            return items, {
                "success": False,
                "complete": False,
                "observed": len(items),
                "reported_count": reported_count,
                "pages": pages,
                "detail": "unexpected response shape",
            }
        items.extend(item for item in page_items if isinstance(item, dict))

    complete = reported_count is None or len(items) >= reported_count
    return items, {
        "success": complete,
        "complete": complete,
        "observed": len(items),
        "reported_count": reported_count,
        "pages": pages,
        "detail": "" if complete else "reported count exceeds collected records",
    }


def _policy_record(record: dict, archived: bool) -> dict:
    """Return a public policy record annotated with stable document URLs."""
    policy = copy.deepcopy(record)
    policy["archived"] = archived
    filename = policy.get("filename") or policy.get("attachment_file") or ""
    if filename:
        policy["url"] = urljoin(config.NIAP_BASE, f"/Policy/{filename}")
    for addendum in policy.get("addendums") or []:
        addendum["archived"] = archived
        addendum_filename = addendum.get("filename") or addendum.get("attachment_file") or ""
        if addendum_filename:
            addendum["url"] = urljoin(config.NIAP_BASE, f"/Policy/{addendum_filename}")
    return policy


def _hash_policy_document(url: str) -> str:
    """Download a NIAP policy PDF and return its full SHA-256 digest."""
    def _fetch(target: str) -> str:
        response = curl_requests.get(
            target,
            impersonate="chrome",
            timeout=30,
            allow_redirects=True,
        )
        if response.status_code in (403, 404):
            log.warning(
                "[NIAP] Policy document is not publicly retrievable (HTTP %d): %s",
                response.status_code,
                target,
            )
            return ""
        response.raise_for_status()
        return hashlib.sha256(response.content).hexdigest()

    return _fetch_with_retry(_fetch, url) or ""


def _attach_policy_document_hashes(policies: list[dict]) -> dict:
    """Attach full PDF hashes to policy and addendum records in parallel."""
    targets: dict[str, list[dict]] = {}
    for policy in policies:
        if policy.get("url"):
            targets.setdefault(policy["url"], []).append(policy)
        for addendum in policy.get("addendums") or []:
            if addendum.get("url"):
                targets.setdefault(addendum["url"], []).append(addendum)

    hashes: dict[str, str] = {}
    if targets:
        with ThreadPoolExecutor(max_workers=min(8, len(targets)), thread_name_prefix="niap_policy") as executor:
            futures = {
                executor.submit(_hash_policy_document, url): url
                for url in targets
            }
            for future in as_completed(futures):
                url = futures[future]
                try:
                    hashes[url] = future.result()
                except Exception as exc:
                    log.warning("[NIAP] Policy document hash failed for %s: %s", url, exc)
                    hashes[url] = ""

    for url, records in targets.items():
        digest = hashes.get(url, "")
        if digest:
            for record in records:
                record["document_sha256"] = digest

    hashed = sum(1 for digest in hashes.values() if digest)
    failed = len(targets) - hashed
    return {
        "expected_documents": len(targets),
        "hashed_documents": hashed,
        "failed_documents": failed,
        "complete": failed == 0,
    }


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


    collection_health = {}

    log.info("  Events...")
    curr, curr_health = _get_paginated_results(base + eps["events_curr"])
    prev, prev_health = _get_paginated_results(base + eps["events_prev"])
    event_map = {
        str(item.get("id")): item
        for item in curr + prev
        if item.get("id") is not None
    }
    data["events"] = list(event_map.values())
    collection_health["events"] = {
        "success": curr_health["success"] and prev_health["success"],
        "complete": curr_health["complete"] and prev_health["complete"],
        "observed": len(data["events"]),
        "detail": "; ".join(filter(None, [curr_health.get("detail"), prev_health.get("detail")])),
    }

    log.info("  News & Announcements...")
    data["news"], collection_health["news"] = _get_paginated_results(base + eps["news"])

    log.info("  Policy Letters...")
    active_policies, active_health = _get_paginated_results(
        base + eps["policies_active"], fetch_json=_get_browser_json
    )
    archived_policies, archived_health = _get_paginated_results(
        base + eps["policies_archived"], fetch_json=_get_browser_json
    )
    policies = [
        *(_policy_record(item, False) for item in active_policies),
        *(_policy_record(item, True) for item in archived_policies),
    ]
    policy_map = {
        f"{item.get('policy_id') or item.get('policy_num')}|{'archived' if item.get('archived') else 'active'}": item
        for item in policies
        if item.get("policy_id") is not None or item.get("policy_num") is not None
    }
    data["policies"] = list(policy_map.values())
    document_health = _attach_policy_document_hashes(data["policies"])
    collection_health["policies"] = {
        **document_health,
        # API completeness controls last-known-good fallback. Some historical
        # archived PDFs are permanently 403/404; retain their metadata while
        # reporting hash coverage rather than making policies stale forever.
        "success": active_health["success"] and archived_health["success"],
        "complete": active_health["complete"] and archived_health["complete"],
        "observed": len(data["policies"]),
        "document_hash_complete": document_health["complete"],
        "detail": "; ".join(filter(None, [
            active_health.get("detail"), archived_health.get("detail"),
            (
                f"{document_health['failed_documents']} policy document hash(es) failed"
                if document_health["failed_documents"] else ""
            ),
        ])),
    }
    data["_collection_health"] = collection_health

    log.info(
        "[NIAP] PCL:%d PPs:%d TDs:%d Events:%d News:%d Policies:%d",
        len(data["pcl"]), len(data["pps"]), len(data["tds"]),
        len(data["events"]), len(data["news"]), len(data["policies"]),
    )
    return data


# ── CC Portal ─────────────────────────────────────────────────────────────────
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


# ── CCTL Labs ─────────────────────────────────────────────────────────────────
def scrapelab_items(url):
    """Generic scraper — extracts headlines/links from a lab page."""
    soup = get_html(url)
    if not soup:
        return []
    items = []
    for tag in soup.find_all(["h2", "h3", "h4", "article"]):
        a = tag.find("a") if tag.name != "a" else tag
        if a and a.get_text(strip=True):
            href = a.get("href", "")
            if href:
                href = urljoin(url, href)
            items.append({
                "title":     a.get_text(strip=True),
                "link":      href,
                "published": "",
                "id":        href or a.get_text(strip=True),
            })
    return items[:20]

def collect_cctl_labs():
    """Collect CCTL lab blog/news items.

    Labs with RSS feeds are reliable.  Labs configured with scrape=True use
    a generic HTML scraper that is frequently broken by site redesigns — they
    are collected on a best-effort basis and logged with a warning when empty
    so operators know to check (fix #19).
    """
    log.info("[CCTL Labs] Collecting...")
    results = {}
    rss_labs = [l for l in config.CCTL_LABS if l["rss"]]
    scrape_labs = [l for l in config.CCTL_LABS if not l["rss"] and l.get("scrape") and l["url"]]
    disabled_labs = [l for l in config.CCTL_LABS if not l["rss"] and not l.get("scrape")]

    for lab in rss_labs:
        name = lab["name"]
        log.debug("  [RSS] %s...", name)
        items = get_rss(lab["rss"])
        results[name] = items or []
        log.debug("    -> %d items", len(results[name]))

    for lab in scrape_labs:
        name = lab["name"]
        log.debug("  [Scrape] %s...", name)
        items = scrapelab_items(lab["url"])
        results[name] = items or []
        if not results[name]:
            log.warning(
                "  [CCTL] %s scraper returned 0 items — site may have changed layout. "
                "Consider adding an RSS feed or disabling this scraper in config.",
                name,
            )
        else:
            log.debug("    -> %d items", len(results[name]))

    for lab in disabled_labs:
        name = lab["name"]
        log.debug("  [Skip] %s — no RSS and scrape=False", name)
        results[name] = []

    active = sum(1 for v in results.values() if v)
    log.info("[CCTL Labs] %d/%d sources returned items.", active, len(config.CCTL_LABS))
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

    # Warn-only checks — external sites that may legitimately be slow/blocked
    csfc_apl_count = len(snapshot.get("csfc", {}).get("pages", {}).get("apl", []))
    if csfc_apl_count < config.SANITY_MIN_CSFC_APL:
        log.warning(
            "CSfC APL returned only %d items (minimum expected: %d). "
            "NSA site may be down or blocking — snapshot kept but flagged.",
            csfc_apl_count, config.SANITY_MIN_CSFC_APL,
        )

    csfc_announcements_count = len(
        snapshot.get("csfc", {}).get("pages", {}).get("announcements", [])
    )
    if csfc_announcements_count < config.SANITY_MIN_CSFC_ANNOUNCEMENTS:
        log.warning(
            "CSfC Announcements returned only %d items (minimum expected: %d). "
            "NSA site may be down or blocking — snapshot kept but flagged.",
            csfc_announcements_count,
            config.SANITY_MIN_CSFC_ANNOUNCEMENTS,
        )

    crypto_pubs_count = len(
        snapshot.get("cc_crypto", {}).get("pages", {}).get("publications", [])
    )
    if crypto_pubs_count < config.SANITY_MIN_CC_CRYPTO_PUBS:
        log.warning(
            "CC Crypto publications page returned only %d items (minimum expected: %d). "
            "CC Portal may be down — snapshot kept but flagged.",
            crypto_pubs_count, config.SANITY_MIN_CC_CRYPTO_PUBS,
        )

    nist_news_count = len(
        snapshot.get("nist", {}).get("pages", {}).get("news", [])
    )
    if nist_news_count < config.SANITY_MIN_NIST_NEWS:
        log.warning(
            "NIST CSRC news page returned only %d items (minimum expected: %d). "
            "NIST CSRC may be down or blocking — snapshot kept but flagged.",
            nist_news_count, config.SANITY_MIN_NIST_NEWS,
        )

    log.info(
        "[Validation] Sanity checks passed "
        "(PCL:%d PPs:%d CSfC-APL:%d CSfC-Announcements:%d CryptoPubs:%d NISTNews:%d).",
        pcl_count, pps_count, csfc_apl_count, csfc_announcements_count,
        crypto_pubs_count, nist_news_count,
    )



# ── NATO NIAPCL ──────────────────────────────────────────────────────────────
def _parse_nato_niapcl_products(soup) -> list:
    """Parse the NATO NIAPCL product listing page into structured records.
    Each record: {name, manufacturer, category, link, raw_text}
    """
    if not soup:
        return []
    items = []
    # Try table-based layout
    table = soup.find("table")
    if table:
        headers = [th.get_text(strip=True).lower() for th in table.find_all("th")]
        for tr in table.find_all("tr")[1:]:
            cells = [td.get_text(strip=True) for td in tr.find_all("td")]
            if not cells:
                continue
            link_tag = tr.find("a")
            href = link_tag["href"] if link_tag and link_tag.get("href") else ""
            if href and not href.startswith("http"):
                href = config.NATO_BASE + href
            record: dict = {
                "name": "", "manufacturer": "", "category": "",
                "link": href, "raw_text": " | ".join(cells[:4])
            }
            for i, h in enumerate(headers):
                if i >= len(cells):
                    break
                if "product" in h or "name" in h:
                    record["name"] = cells[i]
                elif "manufactur" in h or "vendor" in h or "company" in h:
                    record["manufacturer"] = cells[i]
                elif "categor" in h or "type" in h:
                    record["category"] = cells[i]
            if record["name"] or record["raw_text"]:
                items.append(record)
        if items:
            return items
    # Fallback: generic scrape
    content = soup.find("main") or soup.find("div", {"id": "content"}) or soup
    for tag in content.find_all(["li", "tr", "div", "p"]):
        text = tag.get_text(separator=" ", strip=True)
        link_tag = tag.find("a")
        href = link_tag["href"] if link_tag and link_tag.get("href") else ""
        if href and not href.startswith("http"):
            href = config.NATO_BASE + href
        if len(text) > 10:
            items.append({"name": text[:200], "manufacturer": "", "category": "", "link": href, "raw_text": text[:400]})
    # Deduplicate
    seen: set = set()
    unique: list = []
    for item in items:
        key = item["raw_text"][:80]
        if key not in seen:
            seen.add(key)
            unique.append(item)
    return unique


def collect_nato() -> dict:
    """Collect NATO NIAPCL product listings.
    Monitors the NATO Information Assurance Product Catalogue for:
    - New Cisco product listings (triggers Cisco celebration alert)
    - General product additions / removals
    """
    log.info("[NATO NIAPCL] Collecting...")
    data: dict = {
        "pages": {},
        "cisco_products": [],
    }

    # Warm up the session on the NATO base domain to pick up any required
    # cookies before hitting the NIAPCL search pages (helps bypass WAF/bot filters).
    log.info("[NATO NIAPCL] Warming up session on NATO base domain...")
    try:
        SESSION.get("https://www.ia.nato.int/", timeout=15)
    except Exception as exc:
        log.warning("[NATO] Session warm-up failed: %s", exc)

    for page_key, path in config.NATO_NIAPCL_PAGES.items():
        url = config.NATO_BASE + path
        log.info("  [NATO] Fetching page: %s (%s)...", page_key, url)
        soup = get_html(url)
        products = _parse_nato_niapcl_products(soup)
        data["pages"][page_key] = products
        log.info("  -> %d products", len(products))
    # Extract Cisco-specific entries
    all_products = data["pages"].get("all_products", [])
    data["cisco_products"] = [
        p for p in all_products
        if any(kw in (p.get("manufacturer", "") + p.get("name", "") + p.get("raw_text", "")).lower()
               for kw in config.NATO_CISCO_KEYWORDS)
    ]
    log.info("[NATO NIAPCL] total:%d cisco:%d", len(all_products), len(data["cisco_products"]))
    return data


# ── EUCC / ENISA ─────────────────────────────────────────────────────────────
def _scrape_eucc_page(path: str) -> list:
    """Scrape a single ENISA EUCC page and return a list of text/link items."""
    url = config.EUCC_BASE + path
    soup = get_html(url)
    if not soup:
        return []
    items = []
    content = (
        soup.find("main")
        or soup.find("div", {"id": "content"})
        or soup.find("div", {"class": "content"})
        or soup
    )
    # Try table layout first (certificates page)
    table = content.find("table")
    if table:
        headers = [th.get_text(strip=True).lower() for th in table.find_all("th")]
        for tr in table.find_all("tr")[1:]:
            cells = [td.get_text(strip=True) for td in tr.find_all("td")]
            if not cells:
                continue
            link_tag = tr.find("a")
            href = link_tag["href"] if link_tag and link_tag.get("href") else ""
            if href and not href.startswith("http"):
                href = config.EUCC_BASE + href
            record: dict = {
                "name": cells[0] if cells else "",
                "text": " | ".join(cells[:4])[:400],
                "href": href,
            }
            # Try to map headers to fields
            for i, h in enumerate(headers):
                if i < len(cells):
                    if "product" in h or "name" in h:
                        record["name"] = cells[i]
            items.append(record)
        if items:
            return items
    # Fallback: generic text scrape
    for tag in content.find_all(["p", "li", "h2", "h3", "h4", "td"]):
        text = tag.get_text(separator=" ", strip=True)
        link_tag = tag.find("a")
        href = link_tag["href"] if link_tag and link_tag.get("href") else ""
        if href and not href.startswith("http"):
            href = config.EUCC_BASE + href
        if len(text) > 15:
            items.append({"name": text[:200], "text": text[:400], "href": href})
    # Deduplicate
    seen: set = set()
    unique: list = []
    for item in items:
        key = item["text"][:80]
        if key not in seen:
            seen.add(key)
            unique.append(item)
    return unique


def collect_eucc() -> dict:
    """Collect EUCC / ENISA certification data.
    Monitors two sources:
    1. EUCC scheme requirements / news page (policy updates, scheme changes)
    2. EUCC certificates page (new certified products)
    Cisco-specific products in certificates page trigger a dedicated alert.
    """
    log.info("[EUCC] Collecting...")
    data: dict = {
        "pages": {},
        "cisco_certs": [],
    }
    for page_key, path in config.EUCC_PAGES.items():
        log.info("  [EUCC] Scraping page: %s (%s)...", page_key, path)
        data["pages"][page_key] = _scrape_eucc_page(path)
        log.info("  -> %d items", len(data["pages"][page_key]))
    # Extract Cisco-specific certificates
    certs = data["pages"].get("certificates", [])
    data["cisco_certs"] = [
        c for c in certs
        if any(kw in (c.get("name", "") + c.get("text", "")).lower()
               for kw in config.EUCC_CISCO_KEYWORDS)
    ]
    req_count  = len(data["pages"].get("requirements", []))
    cert_count = len(data["pages"].get("certificates", []))
    log.info("[EUCC] requirements-items:%d certificates:%d cisco-certs:%d",
             req_count, cert_count, len(data["cisco_certs"]))
    return data


# ── Master snapshot — parallel collection (fix #16) ──────────────────────────
def collect_all(validate: bool = True):
    """Collect all domains concurrently and return a timestamped snapshot.

    Validation is enabled by default. Orchestrators that can apply a
    last-known-good fallback may pass ``validate=False`` and validate after
    fallback selection.

    The five top-level collector functions are dispatched in parallel via
    ThreadPoolExecutor (I/O-bound, safe for concurrent HTTP). Each runs
    independently; failures in one domain do not block others.
    """
    log.info("[Collect] Starting parallel collection across all domains...")

    domain_collectors = {
        "niap":      collect_niap,
        "cc_portal": collect_cc_portal,
        "cctl_labs": collect_cctl_labs,
        "csfc":      collect_csfc,
        "cc_crypto": collect_cc_crypto,
        "nist":      collect_nist,
        "nato":      collect_nato,
        "eucc":      collect_eucc,
    }

    results: dict = {}
    errors:  dict = {}

    with ThreadPoolExecutor(max_workers=8, thread_name_prefix="cc_pulse") as executor:
        future_to_domain = {
            executor.submit(fn): domain
            for domain, fn in domain_collectors.items()
        }
        for future in as_completed(future_to_domain):
            domain = future_to_domain[future]
            try:
                results[domain] = future.result()
                log.info("[Collect] %s — done.", domain)
            except Exception as exc:
                log.error("[Collect] %s — FAILED: %s", domain, exc, exc_info=True)
                errors[domain] = str(exc)
                results[domain] = {}  # empty fallback so snapshot stays structurally valid

    if errors:
        log.warning(
            "[Collect] %d domain(s) failed during parallel collection: %s",
            len(errors), list(errors.keys())
        )

    snapshot = {
        "schema_version": config.SNAPSHOT_SCHEMA_VERSION,
        "collected_at":   datetime.now(timezone.utc).isoformat(),
        "niap":           results.get("niap",      {}),
        "cc_portal":      results.get("cc_portal", {}),
        "cctl_labs":      results.get("cctl_labs", {}),
        "csfc":           results.get("csfc",      {}),
        "cc_crypto":      results.get("cc_crypto", {}),
        "nist":           results.get("nist",      {}),
        "nato":           results.get("nato",      {}),
        "eucc":           results.get("eucc",      {}),
    }

    if validate:
        validate_snapshot(snapshot)  # raises SanityError on bad NIAP data
    return snapshot


# ── CSfC (Commercial Solutions for Classified) ────────────────────────────────
def _extract_csfc_page_items(soup) -> list:
    """Extract meaningful text/link records, including NSA table rows."""
    if not soup:
        return []
    content = (
        soup.find("div", {"id": "dnn_ContentPane"})
        or soup.find("div", {"id": "ContentPane"})
        or soup.find("main")
        or soup.find("div", class_="field-items")
        or soup
    )
    items = []
    for tag in content.find_all(["p", "li", "h2", "h3", "h4", "tr"]):
        if getattr(tag, "name", "") == "tr":
            cells = [td.get_text(separator=" ", strip=True) for td in tag.find_all("td")]
            if not cells:
                continue
            text = " | ".join(cells)
        else:
            text = tag.get_text(separator=" ", strip=True)
        link = tag.find("a")
        href = link["href"] if link and link.get("href") else ""
        if href:
            href = urljoin(config.CSFC_BASE, href)
        if len(text) > 15:
            items.append({"text": text[:1000], "href": href})

    seen: set = set()
    unique: list = []
    for item in items:
        key = item["text"][:240]
        if key not in seen:
            seen.add(key)
            unique.append(item)
    return unique


def _scrape_csfc_page_from_soup(soup) -> list:
    """Scrape a pre-fetched NSA CSfC page soup and return a list of text/link items.
    Used by collect_csfc() for the APL page so soup is only fetched once (fix #24).
    """
    return _extract_csfc_page_items(soup)


def _scrape_csfc_announcements(soup) -> list:
    """Extract only dated rows from the CSfC Announcements table."""
    if not soup:
        return []
    main = soup.find("main") or soup.find("div", {"id": "dnn_ContentPane"}) or soup
    items = []
    for table in main.find_all("table"):
        heading = table.get_text(" ", strip=True)
        if "CSfC Announcements" not in heading:
            continue
        for row in table.find_all("tr"):
            cells = [cell.get_text(" ", strip=True) for cell in row.find_all("td")]
            if len(cells) < 2:
                continue
            link = row.find("a")
            href = link["href"] if link and link.get("href") else ""
            if href:
                href = urljoin(config.CSFC_BASE, href)
            items.append({
                "text": f"{cells[0]} | {cells[1]}"[:1000],
                "href": href,
            })
        if items:
            break
    return items


def _scrape_csfc_selection_links(soup) -> dict:
    """Walk the NSA CSfC Components List page and return a mapping of
    {category_heading: full_href} for every Component Selections PDF link.

    Each link has text "Click for Selections" or "Click for Selection" and
    is preceded by an <h2> or <h3> heading with the category name.
    The href is preserved in full, including the DNN ?ver= cache-busting
    token — changes in that token signal that the document was updated.

    Returns {} if soup is None or no links are found.
    """
    if not soup:
        return {}
    results: dict = {}
    for a_tag in soup.find_all("a", href=True):
        href = a_tag["href"]
        text = a_tag.get_text(strip=True).lower()
        # Only process Selection PDF links
        if "click for selection" not in text:
            continue
        if "/portals/" not in href.lower() and ".pdf" not in href.lower():
            continue
        # Walk up to find the nearest preceding <h2> or <h3> heading
        heading = ""
        for sibling in a_tag.find_all_previous(["h2", "h3"]):
            heading = sibling.get_text(strip=True)
            break
        if not heading:
            heading = a_tag.get_text(strip=True)
        # Build absolute URL if needed
        if href.startswith("/"):
            href = config.CSFC_BASE + href
        results[heading] = href
    log.debug("[CSfC Selections] Scraped %d selection links from Components List page.", len(results))
    return results

def _scrape_csfc_page(path: str) -> list:
    """Scrape a single NSA CSfC page and return a list of text/link items."""
    url = config.CSFC_BASE + path
    soup = get_html(url)
    if not soup:
        return []
    return _extract_csfc_page_items(soup)


def _parse_csfc_apl_structured(soup) -> list:
    """Parse the NSA CSfC APL page into structured component records.
    Each record: {name, type, vendor, link, raw_text}
    The APL page lists components in a table or as dl/li rows.
    Falls back to flat text items if no table is found (fix #18).
    """
    if not soup:
        return []
    items = []
    # The Components List has one table per component category.
    for table in soup.find_all("table"):
        headers = [th.get_text(strip=True).lower() for th in table.find_all("th")]
        for tr in table.find_all("tr")[1:]:
            cells = [td.get_text(separator=" ", strip=True) for td in tr.find_all("td")]
            if not cells:
                continue
            link_tag = tr.find("a")
            href = link_tag["href"] if link_tag and link_tag.get("href") else ""
            if href:
                href = urljoin(config.CSFC_BASE, href)
            heading_tag = table.find_previous(["h2", "h3"])
            category = heading_tag.get_text(" ", strip=True) if heading_tag else ""
            raw_text = " | ".join(([category] if category else []) + cells)
            record: dict = {
                "name": cells[1] if len(cells) > 1 else cells[0],
                "type": category,
                "vendor": cells[0],
                "link": href,
                "href": href,
                "raw_text": raw_text,
                "text": raw_text,
            }
            for i, h in enumerate(headers):
                if i >= len(cells):
                    break
                if "product" in h or "component" in h or "name" in h:
                    record["name"] = cells[i]
                elif "type" in h or "categor" in h or "technolog" in h:
                    record["type"] = cells[i]
                elif "vendor" in h or "manufactur" in h or "company" in h:
                    record["vendor"] = cells[i]
            if record["name"] or record["raw_text"]:
                items.append(record)
    if items:
        return items
    # Fallback: use existing _scrape_csfc_page-style text items, augmented with type detection
    content = (
        soup.find("div", {"id": "ContentPane"})
        or soup.find("main")
        or soup.find("div", class_="field-items")
        or soup
    )
    for tag in content.find_all(["p", "li", "h2", "h3", "h4"]):
        raw = tag.get_text(separator=" ", strip=True)
        link_tag = tag.find("a")
        href = link_tag["href"] if link_tag and link_tag.get("href") else ""
        if len(raw) < 10:
            continue
        # Categorise by keyword
        comp_type = "unknown"
        raw_lower = raw.lower()
        for cat, kws in config.CSFC_APL_COMPONENT_KEYWORDS.items():
            if any(kw in raw_lower for kw in kws):
                comp_type = cat
                break
        items.append({"name": raw[:200], "type": comp_type, "vendor": "", "link": href, "raw_text": raw[:400]})
    # Deduplicate
    seen: set = set()
    unique: list = []
    for item in items:
        key = item["name"][:80] or item["raw_text"][:80]
        if key not in seen:
            seen.add(key)
            unique.append(item)
    return unique


def collect_csfc() -> dict:
    """Collect all CSfC monitoring data:
    - NSA CSfC page snapshots (home, APL, components list, FAQ, etc.)
    - Component Selection PDF links and version tokens
    - CSfC-tagged RSS / news feeds
    """
    log.info("[CSfC] Collecting...")
    data: dict = {
        "pages": {},
        "apl_structured": [],           # structured APL records (fix #18)
        "selection_links": {},
        "feeds": {},
    }

    # Warm up the session on the NSA base domain to pick up any required
    # cookies before hitting deep CSfC sub-pages (helps bypass WAF/bot filters).
    log.info("[CSfC] Warming up session on NSA base domain...")
    try:
        SESSION.get("https://www.nsa.gov/", timeout=15)
    except Exception as exc:
        log.warning("[CSfC] Session warm-up failed: %s", exc)

    # 1. Scrape CSfC pages; for the APL, also parse structured records (fix #18)
    for page_key, path in config.CSFC_PAGES.items():
        log.debug("  [CSfC] Scraping page: %s (%s)...", page_key, path)
        if page_key == "apl":
            # fix #24: fetch APL page once; reuse soup for both parsers to avoid
            # double GET on a WAF-protected NSA URL that intermittently 403s.
            apl_soup = get_html(config.CSFC_BASE + path)
            data["apl_structured"] = _parse_csfc_apl_structured(apl_soup)
            data["pages"][page_key] = data["apl_structured"] or _scrape_csfc_page_from_soup(apl_soup)
            log.debug("    -> %d items, %d structured APL records",
                      len(data["pages"][page_key]), len(data["apl_structured"]))
            # Scrape Component Selection links from the already-fetched APL soup (fix #25)
            data["selection_links"] = _scrape_csfc_selection_links(apl_soup)
            log.debug(" -> %d selection links", len(data["selection_links"]))
        elif page_key == "announcements":
            announcements_soup = get_html(config.CSFC_BASE + path)
            data["pages"][page_key] = _scrape_csfc_announcements(announcements_soup)
            log.debug("    -> %d dated announcements", len(data["pages"][page_key]))
        else:
            data["pages"][page_key] = _scrape_csfc_page(path)
            log.debug("    -> %d items", len(data["pages"][page_key]))


    # 3. RSS / news feeds
    for feed in config.CSFC_FEEDS:
        name = feed["name"]
        log.debug("  [CSfC] Feed: %s...", name)
        if feed.get("rss"):
            items = get_rss(feed["rss"])
        elif feed.get("scrape") and feed.get("url"):
            items = scrapelab_items(feed["url"])
        else:
            items = []
        data["feeds"][name] = items
        log.debug("    -> %d items", len(items))

    apl_count = len(data["pages"].get("apl", []))
    sel_count = len(data["selection_links"])
    log.info("[CSfC] APL items:%d Component Selection links scraped:%d", apl_count, sel_count)
    return data


# ── CC Crypto Catalog & Working Group ─────────────────────────────────────────
def _scrape_cc_crypto_page(path: str) -> list:
    """Scrape a CC Portal page and return text/link items."""
    url  = config.CC_CRYPTO_BASE + path
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
            items.append({"text": text[:500], "href": href})
    seen:   set = set()
    unique: list = []
    for item in items:
        key = item["text"][:120]
        if key not in seen:
            seen.add(key)
            unique.append(item)
    return unique

def collect_cc_crypto() -> dict:
    """Collect CC Crypto Catalog and working group monitoring data:
      - CC Portal page snapshots (publications, news, communities)

    Doc header polling removed (fix #27): CDN headers were unreliable
    and produced false positives. Page scrapes are the correct signal.
    """
    log.info("[CC Crypto] Collecting...")
    data: dict = {
        "pages": {},
    }

    for page_key, path in config.CC_CRYPTO_PAGES.items():
        log.debug("  [CC Crypto] Scraping page: %s (%s)...", page_key, path)
        data["pages"][page_key] = _scrape_cc_crypto_page(path)
        log.debug("    -> %d items", len(data["pages"][page_key]))

    pubs_count = len(data["pages"].get("publications", []))
    log.info("[CC Crypto] publications-items:%d", pubs_count)
    return data


# ── NIST CSRC Monitoring ──────────────────────────────────────────────────────
def _scrape_nist_page(path: str) -> list:
    """Scrape a NIST CSRC page and return a list of text/link items."""
    url  = config.NIST_CSRC_BASE + path
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
    # CMVP MIP page uses a table — extract rows as structured records (fix #20)
    # Columns (as of 2024): Vendor | Module Name | FIPS Cert # | Validation Auth Date | Status
    table = content.find("table") if content else None
    if table:
        headers_raw = [th.get_text(strip=True) for th in table.find_all("th")]
        for tr in table.find_all("tr")[1:]:
            cells = [td.get_text(strip=True) for td in tr.find_all("td")]
            if not cells:
                continue
            link_tag = tr.find("a")
            href = link_tag["href"] if link_tag and link_tag.get("href") else ""
            # Build a named-field record when headers are available
            if headers_raw and len(headers_raw) >= len(cells):
                record = dict(zip(headers_raw, cells))
                record["href"] = href
                # Also include a text summary for backwards compat with existing diff logic
                record["text"] = " | ".join(cells[:4])[:400]
            else:
                record = {"text": " | ".join(cells[:4])[:400], "href": href}
            items.append(record)
    else:
        for tag in content.find_all(["h2", "h3", "h4", "p", "li", "td"]):
            text = tag.get_text(separator=" ", strip=True)
            link = tag.find("a")
            href = link["href"] if link and link.get("href") else ""
            if len(text) > 20:
                items.append({"text": text[:400], "href": href})
    seen:   set = set()
    unique: list = []
    for item in items:
        key = item["text"][:120]
        if key not in seen:
            seen.add(key)
            unique.append(item)
    return unique

def collect_nist() -> dict:
    """Collect NIST CSRC monitoring data:
      - CSRC page snapshots (news, FIPS, CMVP MIP, PQC project, crypto standards)
      - HTTP header polling of key NIST crypto PDFs (with partial-GET fallback)
      - NIST cybersecurity RSS feeds
    """
    log.info("[NIST] Collecting...")
    data: dict = {
        "pages": {},
        "feeds": {},
    }

    for page_key, path in config.NIST_CSRC_PAGES.items():
        log.debug("  [NIST] Scraping page: %s (%s)...", page_key, path)
        data["pages"][page_key] = _scrape_nist_page(path)
        log.debug("    -> %d items", len(data["pages"][page_key]))

    for feed in config.NIST_FEEDS:
        name = feed["name"]
        log.debug("  [NIST] Feed: %s...", name)
        if feed.get("rss"):
            items = get_rss(feed["rss"])
        elif feed.get("scrape") and feed.get("url"):
            items = scrapelab_items(feed["url"])
        else:
            items = []
        data["feeds"][name] = items
        log.debug("    -> %d items", len(items))

    news_count = len(data["pages"].get("news", []))
    mip_count  = len(data["pages"].get("cmvp_mip", []))
    log.info("[NIST] news-items:%d cmvp-mip-modules:%d", news_count, mip_count)
    return data


# ── Per-domain CLI entry point (issue #20 — parallel matrix support) ─────────────────

#: Maps domain name → collector function.
#: Used by `collect_domain()` and the `--domain` CLI flag so that each
#: GitHub Actions matrix job can collect exactly one domain and write a
#: partial snapshot to `snapshots/partial/<domain>.json`.
DOMAIN_COLLECTORS: dict = {
    "niap":      collect_niap,
    "cc_portal": collect_cc_portal,
    "cctl_labs": collect_cctl_labs,
    "csfc":      collect_csfc,
    "cc_crypto": collect_cc_crypto,
    "nist":      collect_nist,
    "nato":      collect_nato,
    "eucc":      collect_eucc,
}


def collect_domain(name: str, out_dir: str = "snapshots/partial") -> dict:
    """Collect a single domain and persist the result as a partial snapshot.

    Called by `python collector.py --domain <name>` in each GitHub Actions
    matrix job.  The partial file is later merged by `main.py --merge`.

    Args:
        name:    One of the keys in DOMAIN_COLLECTORS (e.g. "niap", "nist").
        out_dir: Directory in which to write `<name>.json`.  Created if absent.

    Returns:
        The collected data dict for the domain.

    Raises:
        ValueError: If `name` is not a recognised domain key.
        Any exception raised by the underlying collector propagates unchanged.
    """
    import json
    import os

    if name not in DOMAIN_COLLECTORS:
        raise ValueError(
            f"Unknown domain '{name}'. "
            f"Valid options: {sorted(DOMAIN_COLLECTORS)}"
        )

    log.info("[collect_domain] Collecting domain: %s", name)
    data = DOMAIN_COLLECTORS[name]()

    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, f"{name}.json")
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, default=str)
    log.info("[collect_domain] Written to %s", out_path)
    return data


# ── CLI ───────────────────────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    import logging as _logging

    _logging.basicConfig(
        level=_logging.INFO,
        format="%(asctime)s %(levelname)-8s %(name)s — %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%SZ",
    )
    for _noisy in ("urllib3", "requests", "feedparser"):
        _logging.getLogger(_noisy).setLevel(_logging.WARNING)

    parser = argparse.ArgumentParser(
        description="CC Pulse collector — run a single-domain collection pass."
    )
    parser.add_argument(
        "--domain",
        required=True,
        choices=sorted(DOMAIN_COLLECTORS),
        help="Domain to collect (e.g. niap, nist, nato).",
    )
    parser.add_argument(
        "--out-dir",
        default="snapshots/partial",
        help="Directory to write the partial snapshot JSON (default: snapshots/partial).",
    )
    args = parser.parse_args()
    collect_domain(args.domain, out_dir=args.out_dir)
