"""
collector.py — Pulls all data from NIAP APIs, CC Portal, CCTL labs,
              CSfC pages, CC Crypto Catalog.

Features:
  - Exponential-backoff retry on every HTTP call
  - Partial-GET content-hash fallback for PDF polling (fix #10)
  - Parallel domain collection via ThreadPoolExecutor (fix #16)
  - Structured logging throughout
  - Sanity-check validation before accepting a snapshot
  - Structured CSfC APL records with component type tagging (fix #18)
  - CCTL scraper health warnings for empty-result detectors (fix #19)
"""

import hashlib
import copy
import json
import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import feedparser
import requests
from curl_cffi import requests as curl_requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin, quote, urlsplit
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

def _do_get_html(url, timeout=30):
    r = SESSION.get(url, timeout=timeout)
    if r.status_code == 403:
        log.warning(
            "403 Forbidden for %s — WAF may be blocking this request "
            "(bot-detection, IP reputation, or missing browser headers)",
            url,
        )
        csfc_base = str(getattr(config, "CSFC_BASE", "")).lower()
        if csfc_base and url.lower().startswith(csfc_base):
            log.info("Retrying WAF-protected page with a Chrome-compatible TLS fingerprint...")
            browser_response = curl_requests.get(
                url,
                impersonate="chrome",
                timeout=timeout,
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

def get_html(url, timeout=30):
    result = _fetch_with_retry(_do_get_html, url, timeout=timeout)
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


def _fetch_fixed_source(
    url: str,
    *,
    allowed_hosts: set[str] | frozenset[str],
    max_bytes: int,
    timeout: int = 30,
    accept: str = "*/*",
    browser_fallback: bool = False,
) -> dict:
    """Fetch one fixed public source with HTTPS/host and size enforcement.

    Callers construct URLs only from constants and validated identifiers.  The
    allow-list is still enforced before and after redirects so a compromised
    upstream page cannot turn document monitoring into an SSRF primitive.
    """
    parsed = urlsplit(url)
    if parsed.scheme != "https" or parsed.hostname not in allowed_hosts:
        raise ValueError(f"Refusing non-allow-listed source URL: {url}")
    if not isinstance(max_bytes, int) or max_bytes <= 0:
        raise ValueError("max_bytes must be a positive integer")

    def _consume(response) -> dict:
        final_url = str(response.url)
        final = urlsplit(final_url)
        if final.scheme != "https" or final.hostname not in allowed_hosts:
            response.close()
            raise ValueError(f"Redirect left the allow-listed source: {final_url}")
        content_length = response.headers.get("Content-Length", "")
        if content_length:
            try:
                parsed_length = int(content_length)
            except (TypeError, ValueError):
                parsed_length = 0
            if parsed_length > max_bytes:
                response.close()
                raise ValueError(f"Source exceeds {max_bytes} byte limit")

        body = bytearray()
        for block in response.iter_content(chunk_size=64 * 1024):
            if not block:
                continue
            body.extend(block)
            if len(body) > max_bytes:
                response.close()
                raise ValueError(f"Source exceeds {max_bytes} byte limit")
        response.close()
        payload = bytes(body)
        return {
            "url": final_url,
            "content": payload,
            "sha256": hashlib.sha256(payload).hexdigest(),
            "size": len(payload),
            "etag": response.headers.get("ETag", ""),
            "last_modified": response.headers.get("Last-Modified", ""),
            "content_type": response.headers.get("Content-Type", ""),
        }

    def _fetch(target: str) -> dict:
        request_headers = {"Accept": accept, "Accept-Encoding": "identity"}
        response = SESSION.get(
            target,
            timeout=timeout,
            allow_redirects=True,
            stream=True,
            # Keep the byte cap meaningful and avoid optional Brotli/Zstandard
            # decoding differences across runner environments.
            headers=request_headers,
        )
        if response.status_code == 403 and browser_fallback:
            response.close()
            response = curl_requests.get(
                target,
                impersonate="chrome",
                timeout=timeout,
                allow_redirects=True,
                stream=True,
                headers=request_headers,
            )
        response.raise_for_status()
        return _consume(response)

    result = _fetch_with_retry(_fetch, url)
    return result or {}


# ── Partial-GET content-hash fallback (fix #10) ───────────────────────────────
def _partial_get_hash(url: str, nbytes: int = 2048) -> str:
    """Fetch the first `nbytes` bytes of a URL and return an MD5 hex-digest.

    Used as a fallback when a server does not return useful Last-Modified /
    ETag headers (common for NSA PDF servers behind CDNs/WAFs).
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
            # MD5 is a non-security content fingerprint for change detection
            # only (not an integrity/auth control); collisions are irrelevant here.
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


_CNSA_MARKER_PATTERNS = {
    "CNSA 2.0": re.compile(r"\bCNSA\s*2(?:\.0)?\b", re.IGNORECASE),
    "ML-KEM": re.compile(r"\bML[- ]KEM\b", re.IGNORECASE),
    "ML-DSA": re.compile(r"\bML[- ]DSA\b", re.IGNORECASE),
    "SLH-DSA": re.compile(r"\bSLH[- ]DSA\b", re.IGNORECASE),
    "LMS": re.compile(r"\bLMS\b", re.IGNORECASE),
    "XMSS": re.compile(r"\bXMSS\b", re.IGNORECASE),
    "post-quantum": re.compile(r"\bpost[- ]quantum\b", re.IGNORECASE),
}


def _cnsa_markers(text: str) -> list[str]:
    """Return stable CNSA/PQC markers present in bounded public text."""
    return sorted(label for label, pattern in _CNSA_MARKER_PATTERNS.items() if pattern.search(text))


def _targeted_niap_pp(record: dict) -> bool:
    short_name = str(record.get("pp_short_name") or "").upper()
    family_match = any(
        short_name.startswith(pattern.upper() + "_V")
        or (pattern.upper() == "MOD_WLAN" and short_name.startswith("MOD_WLAN"))
        for pattern in config.NIAP_PQC_PP_PATTERNS
    )
    if not family_match:
        return False
    sunset = str(record.get("sunset_date") or "")
    if sunset:
        try:
            if datetime.fromisoformat(sunset.replace("Z", "+00:00")) < datetime.now(timezone.utc):
                return False
        except ValueError:
            pass
    return True


def _fetch_niap_pp_document(record: dict) -> dict:
    """Find and fingerprint the published HTML document for one targeted PP."""
    pp_id = str(record.get("pp_id") or "")
    if not re.fullmatch(r"\d+", pp_id):
        return {}
    files_url = config.NIAP_BASE + config.NIAP_PP_FILES_ENDPOINT.format(pp_id=pp_id)
    files = _get_browser_json(files_url)
    if not isinstance(files, list):
        return {}
    html_candidates = [
        item for item in files
        if isinstance(item, dict)
        and not item.get("isFolder")
        and (
            str(item.get("file_mime_type") or "").lower() == "text/html"
            or str(item.get("file_name") or "").lower().endswith((".html", ".htm"))
        )
    ]
    pdf_candidates = [
        item for item in files
        if isinstance(item, dict)
        and not item.get("isFolder")
        and str(item.get("file_mime_type") or "").lower() == "application/pdf"
        and "protection profile" in str(item.get("file_display_name") or "").lower()
    ]
    candidates = html_candidates or pdf_candidates
    if not candidates:
        return {}
    candidates.sort(key=lambda item: (
        "protection profile" not in str(item.get("file_display_name") or "").lower(),
        str(item.get("file_name") or ""),
    ))
    selected = candidates[0]
    filename = str(selected.get("file_name") or "")
    if not filename or "/" in filename or "\\" in filename:
        return {}
    file_metadata = {
        "document_file_id": selected.get("file_id"),
        "document_filename": filename,
        "document_mime_type": str(selected.get("file_mime_type") or ""),
    }
    if selected in pdf_candidates and selected not in html_candidates:
        # NIAP exposes current PDF metadata publicly but its download endpoint
        # requires authentication. Preserve the stable file id/name as a
        # version signal; full hashing remains available for published HTML.
        return file_metadata
    document_url = config.NIAP_BASE + config.NIAP_PP_STATIC_PATH.format(
        pp_id=pp_id,
        filename=quote(filename, safe=""),
    )
    fetched = _fetch_fixed_source(
        document_url,
        allowed_hosts={"www.niap-ccevs.org"},
        max_bytes=config.NIAP_PP_DOCUMENT_MAX_BYTES,
    )
    if not fetched:
        return {**file_metadata, "document_url": document_url}
    text = fetched["content"].decode("utf-8", errors="ignore")
    return {
        **file_metadata,
        "document_url": fetched["url"],
        "document_sha256": fetched["sha256"],
        "document_size": fetched["size"],
        "document_etag": fetched["etag"],
        "document_last_modified": fetched["last_modified"],
        "cnsa_markers": _cnsa_markers(text),
    }


def _attach_niap_pp_document_hashes(pps: list[dict]) -> dict:
    """Attach full-content hashes to CNSA/PQC-relevant NIAP PP records."""
    targets = [record for record in pps if _targeted_niap_pp(record)]
    results: dict[str, dict] = {}
    if targets:
        with ThreadPoolExecutor(
            max_workers=min(6, len(targets)), thread_name_prefix="niap_pp"
        ) as executor:
            futures = {executor.submit(_fetch_niap_pp_document, record): record for record in targets}
            for future in as_completed(futures):
                record = futures[future]
                key = str(record.get("pp_id") or "")
                try:
                    results[key] = future.result()
                except Exception as exc:
                    log.warning("[NIAP] PP document fingerprint failed for %s: %s", key, exc)
                    results[key] = {}
    for record in targets:
        record.update(results.get(str(record.get("pp_id") or ""), {}))
    hashed = sum(1 for result in results.values() if result.get("document_sha256"))
    versioned = sum(
        1 for result in results.values()
        if result.get("document_file_id") not in (None, "")
    )
    missing = len(targets) - versioned
    return {
        "success": True,
        "complete": missing == 0,
        "observed": len(targets),
        "expected_documents": len(targets),
        "versioned_documents": versioned,
        "hashed_documents": hashed,
        "failed_documents": missing,
        "detail": (
            f"{hashed}/{len(targets)} full-content hashes; "
            f"{len(targets) - hashed} PDF-only profile(s) tracked by public file id/name"
            if not missing else f"{missing} PP document metadata lookup(s) failed"
        ),
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


    collection_health = {
        "pp_documents": _attach_niap_pp_document_hashes(data["pps"]),
    }

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
    log.info(
        "[Validation] Sanity checks passed "
        "(PCL:%d PPs:%d CSfC-APL:%d CSfC-Announcements:%d CryptoPubs:%d).",
        pcl_count, pps_count, csfc_apl_count, csfc_announcements_count,
        crypto_pubs_count,
    )



# ── NATO NIAPCL ──────────────────────────────────────────────────────────────
# NATO NIAPCL is a manual-only domain: ia.nato.int blocks automated access,
# so there is no collector for it. The Cisco baseline is maintained via the
# "NATO Cisco Baseline Update" GitHub Issue workflow
# (scripts/nato_issue_intake.py); the daily pipeline carries the stored
# baseline forward in main._apply_source_health(). See config.MANUAL_DOMAINS.


# ── EUCC / ENISA ─────────────────────────────────────────────────────────────
def _parse_eucc_cards(content) -> list:
    """Parse ENISA EUCC certificate cards (Drupal 11 ``ecl-card`` layout).

    Each certificate renders as::

        <article class="ecl-card">
          <time datetime="...">16 October 2025</time>
          <div class="ecl-content-block__title"><a href="...">EUCC-3090-...</a></div>
          <div class="ecl-content-block__description">The product evaluated is ...</div>
          <dd class="ecl-description-list__definition">(UE) 2024/482 - EUCC</dd>
        </article>

    The certificate identifier is the EUCC number; the product/vendor name lives
    in the description text, so ``text`` folds id + date + description together to
    keep the existing Cisco keyword filter working. Returns ``[]`` when no cards
    are present so the caller falls back to the table / generic scrape.
    """
    items = []
    for card in content.find_all("article", class_="ecl-card"):
        title_tag = card.find(class_="ecl-content-block__title")
        link_tag = title_tag.find("a") if title_tag else None
        cert_id = link_tag.get_text(strip=True) if link_tag else ""
        href = link_tag["href"] if link_tag and link_tag.get("href") else ""
        if href and not href.startswith("http"):
            href = config.EUCC_BASE + href
        desc_tag = card.find(class_="ecl-content-block__description")
        desc = desc_tag.get_text(" ", strip=True) if desc_tag else ""
        # ENISA descriptions contain &nbsp; (\xa0); normalize to plain spaces and
        # collapse runs so stored text is stable and diffs don't churn on whitespace.
        desc = " ".join(desc.replace("\xa0", " ").split())
        time_tag = card.find("time")
        cert_date = time_tag.get("datetime", "") if time_tag else ""
        date_text = time_tag.get_text(strip=True) if time_tag else ""
        if not cert_id and not desc:
            continue
        items.append({
            "name": cert_id,
            "text": f"{cert_id} | {date_text} | {desc}"[:400],
            "href": href,
            "cert_date": cert_date,
            "description": desc[:400],
        })
    return items


def _scrape_eucc_page(path: str, page_key: str = "") -> list:
    """Scrape a single ENISA EUCC page and return a list of text/link items.

    The certificates page is cards-only: if the ``ecl-card`` parser returns
    nothing we return empty rather than falling through to the generic text
    scrape. That generic scrape is what silently produced garbage (bare dates,
    page chrome) for weeks when ENISA moved off a <table> — a hard empty makes
    the sanity floor / collapse guard fire instead of masking the break.
    Other pages (e.g. requirements) still use table + generic fallback.
    """
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
    # Certificates page: Drupal 11 ecl-card grid, cards-or-empty (no fallback).
    if page_key == "certificates":
        return _parse_eucc_cards(content)
    # Other pages: try cards, then table, then generic text scrape.
    cards = _parse_eucc_cards(content)
    if cards:
        return cards
    # Try table layout next (legacy / other pages)
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
        data["pages"][page_key] = _scrape_eucc_page(path, page_key)
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


# ── ND-iTC (Network Device iTC) ──────────────────────────────────────────────
def _parse_nd_itc_rfi_table(soup) -> list:
    """Parse the ND-iTC NIT RFI table (active or archived TD page).

    Header-driven: finds the first table whose header row contains 'ID' and
    'Status', then maps cells by header name so column reordering or extra
    columns don't break the parse. Returns records:
      {rfi_id, title, href, reference, publication_date, impact, status}
    """
    if not soup:
        return []
    for table in soup.find_all("table"):
        rows = table.find_all("tr")
        if len(rows) < 2:
            continue
        headers = [c.get_text(" ", strip=True).lower()
                   for c in rows[0].find_all(["th", "td"])]
        if "id" not in headers or "status" not in headers:
            continue
        idx = {h: i for i, h in enumerate(headers)}
        records = []
        for tr in rows[1:]:
            cells = tr.find_all(["td", "th"])
            if not cells:
                continue

            def _cell(name: str) -> str:
                i = idx.get(name)
                if i is None or i >= len(cells):
                    return ""
                return cells[i].get_text(" ", strip=True)

            href = ""
            title_i = idx.get("title")
            if title_i is not None and title_i < len(cells):
                a = cells[title_i].find("a")
                if a and a.get("href"):
                    href = urljoin(config.ND_ITC_BASE + "/", a["href"])
            record = {
                "rfi_id": _cell("id"),
                "title": _cell("title"),
                "href": href,
                "reference": _cell("reference"),
                "publication_date": _cell("publication date"),
                "impact": _cell("impact"),
                "status": _cell("status"),
            }
            if record["rfi_id"] or record["title"]:
                records.append(record)
        if records:
            return records
    return []


# Allowed-With list entries render as "Object ID: ... Object version: ...
# Owner: ... Notes: ..." runs. Text-pattern extraction is deliberate: it
# survives Asciidoctor changing list markup (ol/dl/table), which is exactly
# the kind of cosmetic churn that broke the EUCC parser on 2026-07-09.
_ND_ITC_AWL_ENTRY_RE = re.compile(
    r"Object ID:\s*(?P<object_id>.+?)\s*"
    r"Object version:\s*(?P<object_version>.+?)\s*"
    r"Owner:\s*(?P<owner>.+?)\s*"
    r"(?:Notes:\s*(?P<notes>.*?))?"
    r"(?=\s*(?:\d+\s*\.\s*)?Object ID:|\s*$)"
)


def _parse_nd_itc_awl(soup, list_key: str) -> dict:
    """Parse an ND-iTC Allowed-With list page.

    Entries are extracted per document section (h2, e.g. 'NDcPP v4.0' vs
    'NDcPP v3.0e') so the same PP-Module allowed with two cPP versions is
    tracked separately. Returns:
      {awl_version, awl_date, entries: [
          {list, section, object_id, object_version, owner, notes, text}]}
    """
    empty = {"awl_version": "", "awl_date": "", "entries": []}
    if not soup:
        return empty
    page_text = " ".join(soup.get_text(" ", strip=True).split())
    m = re.search(r"Allowed-with list version\s*:?\s*(\S+)", page_text, re.I)
    awl_version = m.group(1) if m else ""
    md = re.search(r"\bDate\s*:?\s*(\d{1,2}\s+\w+\s+\d{4})", page_text)
    awl_date = md.group(1) if md else ""

    # Asciidoctor wraps each h2 section in <div class="sect1">; fall back to
    # treating the whole page as one section if that structure is absent.
    sections = []
    for sect in soup.find_all("div", class_="sect1"):
        h2 = sect.find("h2")
        title = h2.get_text(" ", strip=True) if h2 else ""
        sections.append((title, " ".join(sect.get_text(" ", strip=True).split())))
    if not sections:
        sections = [("", page_text)]

    entries = []
    for section_title, section_text in sections:
        for em in _ND_ITC_AWL_ENTRY_RE.finditer(section_text):
            object_id = (em.group("object_id") or "").strip(" .")
            version = (em.group("object_version") or "").strip()
            entries.append({
                "list": list_key,
                "section": section_title,
                "object_id": object_id,
                "object_version": version,
                "owner": (em.group("owner") or "").strip(),
                "notes": (em.group("notes") or "").strip(),
                "text": f"{section_title} | {object_id} | {version}",
            })
    return {"awl_version": awl_version, "awl_date": awl_date, "entries": entries}


def collect_nd_itc() -> dict:
    """Collect ND-iTC (nd-itc.github.io) NIT RFIs and Allowed-With lists.

    Monitors:
    - Active NIT RFIs (the ND-iTC's Technical Decisions — named NIT RFIs in
      CC Pulse to distinguish them from NIAP TDs)
    - Archived NIT RFIs (so active→archived transitions are detected)
    - Allowed-With lists for the NDcPP and the FW PP-Module
    """
    log.info("[ND-iTC] Collecting...")
    data: dict = {"nit_rfis": [], "nit_rfis_archived": [],
                  "awl_entries": [], "awl_meta": []}
    for page_key, path in config.ND_ITC_PAGES.items():
        url = config.ND_ITC_BASE + path
        log.info("  [ND-iTC] %s (%s)...", page_key, path)
        soup = get_html(url)
        if not soup:
            log.warning("[ND-iTC] Failed to fetch %s", url)
            continue
        if page_key.startswith("nit_rfis"):
            records = _parse_nd_itc_rfi_table(soup)
            archived = page_key.endswith("archived")
            for record in records:
                record["archived"] = archived
            data[page_key] = records
        else:
            parsed = _parse_nd_itc_awl(soup, page_key)
            data["awl_entries"].extend(parsed["entries"])
            data["awl_meta"].append({
                "list": page_key,
                "awl_version": parsed["awl_version"],
                "awl_date": parsed["awl_date"],
            })
    log.info("[ND-iTC] rfis:%d archived:%d awl-entries:%d awl-lists:%d",
             len(data["nit_rfis"]), len(data["nit_rfis_archived"]),
             len(data["awl_entries"]), len(data["awl_meta"]))
    return data


# ── IETF CNSA 2.0 profiles ───────────────────────────────────────────────────
def _fixed_json(url: str, *, allowed_hosts: set[str], max_bytes: int) -> dict:
    fetched = _fetch_fixed_source(
        url, allowed_hosts=allowed_hosts, max_bytes=max_bytes,
        accept="application/json",
    )
    if not fetched:
        return {}
    try:
        payload = json.loads(fetched["content"].decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        log.warning("Invalid JSON from %s: %s", url, exc)
        return {}
    return payload if isinstance(payload, dict) else {}


def _api_uri_tail(value) -> str:
    if not isinstance(value, str):
        return ""
    return value.strip("/").rsplit("/", 1)[-1]


def _ietf_state_map() -> dict[str, dict]:
    payload = _fixed_json(
        config.IETF_DATATRACKER_API + "/state/?limit=500",
        allowed_hosts={"datatracker.ietf.org"},
        max_bytes=config.IETF_TEXT_MAX_BYTES,
    )
    objects = payload.get("objects", [])
    if not isinstance(objects, list):
        return {}
    states = {}
    for item in objects:
        if not isinstance(item, dict) or not isinstance(item.get("resource_uri"), str):
            continue
        states[item["resource_uri"]] = {
            "type": _api_uri_tail(item.get("type")),
            "slug": str(item.get("slug") or ""),
            "name": str(item.get("name") or ""),
        }
    return states


def _ietf_relations(document_name: str) -> list[dict]:
    if not re.fullmatch(r"(?:draft-[a-z0-9-]+|rfc\d+)", document_name):
        return []
    url = (
        config.IETF_DATATRACKER_API
        + f"/relateddocument/?source__name={document_name}&limit=100"
    )
    payload = _fixed_json(
        url,
        allowed_hosts={"datatracker.ietf.org"},
        max_bytes=config.IETF_TEXT_MAX_BYTES,
    )
    objects = payload.get("objects", [])
    if not isinstance(objects, list):
        return []
    relations = []
    for item in objects:
        if not isinstance(item, dict):
            continue
        relationship = _api_uri_tail(item.get("relationship"))
        target = _api_uri_tail(item.get("target"))
        if relationship in {"obs", "updates"} and target:
            relations.append({"relationship": relationship, "target": target})
    return sorted(relations, key=lambda item: (item["relationship"], item["target"]))


def _collect_ietf_document(name: str, state_map: dict[str, dict]) -> dict:
    if name not in config.IETF_CNSA_DOCUMENTS:
        raise ValueError(f"Unconfigured IETF document: {name}")
    api_url = config.IETF_DATATRACKER_API + f"/document/{name}/"
    payload = _fixed_json(
        api_url,
        allowed_hosts={"datatracker.ietf.org"},
        max_bytes=config.IETF_TEXT_MAX_BYTES,
    )
    if not payload or payload.get("name") != name:
        return {}

    state_records = [
        state_map[uri]
        for uri in payload.get("states", [])
        if isinstance(uri, str) and uri in state_map
    ]
    state_records.sort(key=lambda item: (item.get("type", ""), item.get("name", "")))
    workflow_candidates = [
        item for item in state_records
        if item.get("type", "").startswith("draft-stream-")
    ] or [item for item in state_records if item.get("type") == "draft-iesg"] \
      or [item for item in state_records if item.get("type") == "draft"] \
      or state_records

    revision = str(payload.get("rev") or "")
    if name.startswith("draft-") and re.fullmatch(r"\d{2}", revision):
        text_url = f"{config.IETF_DRAFT_ARCHIVE_BASE}/{name}-{revision}.txt"
        allowed_hosts = {"www.ietf.org"}
    elif name.startswith("rfc"):
        text_url = f"{config.RFC_EDITOR_BASE}/{name}.txt"
        allowed_hosts = {"www.rfc-editor.org"}
    else:
        text_url = ""
        allowed_hosts = set()

    fetched = {}
    if text_url:
        fetched = _fetch_fixed_source(
            text_url,
            allowed_hosts=allowed_hosts,
            max_bytes=config.IETF_TEXT_MAX_BYTES,
        )
    full_text = fetched.get("content", b"").decode("utf-8", errors="ignore")
    record = {
        "name": name,
        "title": str(payload.get("title") or name),
        "revision": revision,
        "expires": str(payload.get("expires") or ""),
        "updated_at": str(payload.get("time") or ""),
        "rfc_number": payload.get("rfc_number"),
        "document_url": f"{config.IETF_DATATRACKER_BASE}/doc/{name}/",
        "text_url": fetched.get("url", text_url),
        "content_sha256": fetched.get("sha256", ""),
        "content_size": fetched.get("size", 0),
        "content_etag": fetched.get("etag", ""),
        "content_last_modified": fetched.get("last_modified", ""),
        "states": state_records,
        "workflow_state": workflow_candidates[0].get("name", "") if workflow_candidates else "",
        "references_rfc8446": bool(re.search(r"\bRFC\s*8446\b", full_text, re.IGNORECASE)),
        "references_rfc9846": bool(re.search(r"\bRFC\s*9846\b", full_text, re.IGNORECASE)),
        "cnsa_markers": _cnsa_markers(full_text),
        "relations": _ietf_relations(name) if name == "rfc9846" else [],
    }
    return record


def collect_ietf_cnsa() -> dict:
    """Collect official metadata and full-text fingerprints for CNSA profiles."""
    log.info("[IETF CNSA] Collecting %d profiles/RFCs...", len(config.IETF_CNSA_DOCUMENTS))
    state_map = _ietf_state_map()
    documents = []
    for name in config.IETF_CNSA_DOCUMENTS:
        try:
            record = _collect_ietf_document(name, state_map)
        except Exception as exc:
            log.warning("[IETF CNSA] Collection failed for %s: %s", name, exc)
            record = {}
        if record:
            documents.append(record)
    log.info(
        "[IETF CNSA] documents:%d hashed:%d",
        len(documents), sum(bool(item.get("content_sha256")) for item in documents),
    )
    return {"documents": documents}


# ── IEEE 802.11bt post-quantum cryptography ──────────────────────────────────
def _parse_ieee_80211bt(timeline_soup, home_soup) -> list[dict]:
    if not timeline_soup:
        return []
    timeline_candidates = []
    for row in timeline_soup.find_all("tr"):
        text = " ".join(row.get_text(" ", strip=True).split())
        if "P802.11bt" in text or re.search(r"\b802\.11bt\b", text, re.IGNORECASE):
            timeline_candidates.append(text)
    if not timeline_candidates:
        return []
    timeline_text = max(timeline_candidates, key=len)

    status_candidates = []
    if home_soup:
        for tag in home_soup.find_all(["li", "p", "td"]):
            text = " ".join(tag.get_text(" ", strip=True).split())
            if ("TGbt" in text or "P802.11bt" in text) and "quantum" in text.lower():
                status_candidates.append(text)
    detailed_status = [
        text for text in status_candidates
        if re.search(r"\b(approved|draft|ballot|recirculation|published)\b", text, re.IGNORECASE)
    ]
    status_text = (
        min(detailed_status, key=len) if detailed_status
        else max(status_candidates, key=len) if status_candidates
        else ""
    )
    draft_match = re.search(r"\bD\d+(?:\.\d+)?\b", timeline_text)
    if not draft_match:
        draft_match = re.search(r"\bD\d+(?:\.\d+)?\b", status_text)
    dates = sorted(set(re.findall(r"\b20\d{2}-\d{2}-\d{2}\b", timeline_text)))
    return [{
        "project": "P802.11bt",
        "title": "Post-Quantum Cryptography",
        "draft": draft_match.group(0) if draft_match else "",
        "timeline_url": config.IEEE_80211_TIMELINE_URL,
        "status_url": config.IEEE_80211_HOME_URL,
        "timeline_text": timeline_text[:2000],
        "status_text": status_text[:1000],
        "timeline_sha256": hashlib.sha256(timeline_text.encode("utf-8")).hexdigest(),
        "status_sha256": (
            hashlib.sha256(status_text.encode("utf-8")).hexdigest() if status_text else ""
        ),
        "dates": dates,
    }]


def collect_ieee_pqc() -> dict:
    """Collect the official IEEE 802.11bt timeline and task-group status."""
    log.info("[IEEE PQC] Collecting P802.11bt...")
    timeline = _fetch_fixed_source(
        config.IEEE_80211_TIMELINE_URL,
        allowed_hosts={"www.ieee802.org"},
        max_bytes=config.IETF_TEXT_MAX_BYTES,
    )
    home = _fetch_fixed_source(
        config.IEEE_80211_HOME_URL,
        allowed_hosts={"www.ieee802.org"},
        max_bytes=config.IETF_TEXT_MAX_BYTES,
    )
    timeline_soup = BeautifulSoup(timeline["content"], "lxml") if timeline else None
    home_soup = BeautifulSoup(home["content"], "lxml") if home else None
    records = _parse_ieee_80211bt(timeline_soup, home_soup)
    log.info("[IEEE PQC] records:%d", len(records))
    return {"projects": records}


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

    # "nato" is deliberately absent: it is a manual-only domain
    # (config.MANUAL_DOMAINS) whose baseline enters via the GitHub issue
    # intake workflow, never by fetching ia.nato.int.
    domain_collectors = {
        "niap":      collect_niap,
        "cc_portal": collect_cc_portal,
        "cctl_labs": collect_cctl_labs,
        "csfc":      collect_csfc,
        "cc_crypto": collect_cc_crypto,
        "eucc":      collect_eucc,
        "nd_itc":    collect_nd_itc,
        "ietf_cnsa": collect_ietf_cnsa,
        "ieee_pqc":  collect_ieee_pqc,
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
        "nato":           results.get("nato",      {}),
        "eucc":           results.get("eucc",      {}),
        "nd_itc":         results.get("nd_itc",    {}),
        "ietf_cnsa":      results.get("ietf_cnsa", {}),
        "ieee_pqc":       results.get("ieee_pqc",  {}),
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
            items.append({
                "text": text[:1000],
                "href": href,
                "content_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
            })

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
                "content_sha256": hashlib.sha256(
                    f"{cells[0]} | {cells[1]}".encode("utf-8")
                ).hexdigest(),
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
        results[heading] = quote(href, safe=":/?=&%")
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
                "content_sha256": hashlib.sha256(raw_text.encode("utf-8")).hexdigest(),
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
        items.append({
            "name": raw[:200], "type": comp_type, "vendor": "", "link": href,
            "href": href, "raw_text": raw[:400], "text": raw[:1000],
            "content_sha256": hashlib.sha256(raw.encode("utf-8")).hexdigest(),
        })
    # Deduplicate
    seen: set = set()
    unique: list = []
    for item in items:
        key = item["name"][:80] or item["raw_text"][:80]
        if key not in seen:
            seen.add(key)
            unique.append(item)
    return unique


def _find_csfc_capability_documents(soup) -> dict:
    """Resolve current relevant capability-package PDFs by stable identity."""
    if not soup:
        return {}
    anchors = []
    for anchor in soup.find_all("a", href=True):
        href = urljoin(config.CSFC_BASE, anchor.get("href", ""))
        if ".pdf" not in href.lower() and "/portals/" not in href.lower():
            continue
        anchor_text = " ".join(anchor.get_text(" ", strip=True).split())
        parent_text = anchor.parent.get_text(" ", strip=True) if anchor.parent else ""
        text = anchor_text or parent_text
        lowered = anchor_text.lower()
        if any(excluded in lowered for excluded in ("mapped to", "mapping", "cnssi")):
            continue
        anchors.append((text, href))

    documents = {}
    for key, label in config.CSFC_PQC_DOCUMENT_LABELS.items():
        normalize = lambda value: value.lower().replace("capability package", "cp")
        label_lower = normalize(label)
        candidates = [entry for entry in anchors if label_lower in normalize(entry[0])]
        if key == "key_management_requirements":
            candidates = [entry for entry in candidates if "symmetric" not in entry[0].lower()]
        if not candidates:
            continue
        candidates.sort(key=lambda entry: (len(entry[0]), entry[1]))
        documents[key] = {"label": label, "url": quote(candidates[0][1], safe=":/?=&%")}
    return documents


def _fingerprint_csfc_documents(documents: dict) -> dict:
    """Attach bounded full-document SHA-256 fingerprints to CSfC PDFs."""
    if not documents:
        return {}
    output = copy.deepcopy(documents)
    with ThreadPoolExecutor(
        max_workers=min(5, len(documents)), thread_name_prefix="csfc_doc"
    ) as executor:
        futures = {
            executor.submit(
                _fetch_fixed_source,
                document["url"],
                allowed_hosts={"www.nsa.gov", "media.defense.gov"},
                max_bytes=config.CSFC_DOCUMENT_MAX_BYTES,
                browser_fallback=True,
            ): key
            for key, document in documents.items()
        }
        for future in as_completed(futures):
            key = futures[future]
            try:
                fetched = future.result()
            except Exception as exc:
                log.warning("[CSfC] Capability document fingerprint failed for %s: %s", key, exc)
                continue
            if fetched:
                output[key].update({
                    "url": fetched["url"],
                    "sha256": fetched["sha256"],
                    "size": fetched["size"],
                    "etag": fetched["etag"],
                    "last_modified": fetched["last_modified"],
                })
    return output


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
        "documents": {},
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
        elif page_key == "cap_packages":
            packages_soup = get_html(config.CSFC_BASE + path)
            data["pages"][page_key] = _scrape_csfc_page_from_soup(packages_soup)
            documents = _find_csfc_capability_documents(packages_soup)
            data["documents"] = _fingerprint_csfc_documents(documents)
            log.debug(
                "    -> %d page items, %d capability documents",
                len(data["pages"][page_key]), len(data["documents"]),
            )
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



# ── Per-domain CLI entry point (issue #20 — parallel matrix support) ─────────────────────────────────────────

#: Maps domain name → collector function.
#: Used by `collect_domain()` and the `--domain` CLI flag so that each
#: GitHub Actions matrix job can collect exactly one domain and write a
#: partial snapshot to `snapshots/partial/<domain>.json`.
#: "nato" is deliberately absent: manual-only domain (config.MANUAL_DOMAINS),
#: maintained via the GitHub issue intake workflow.
DOMAIN_COLLECTORS: dict = {
    "niap":      collect_niap,
    "cc_portal": collect_cc_portal,
    "cctl_labs": collect_cctl_labs,
    "csfc":      collect_csfc,
    "cc_crypto": collect_cc_crypto,
    "eucc":      collect_eucc,
    "nd_itc":    collect_nd_itc,
    "ietf_cnsa": collect_ietf_cnsa,
    "ieee_pqc":  collect_ieee_pqc,
}


def collect_domain(name: str, out_dir: str = "snapshots/partial") -> dict:
    """Collect a single domain and persist the result as a partial snapshot.

    Called by `python collector.py --domain <name>` in each GitHub Actions
    matrix job.  The partial file is later merged by `main.py --merge`.

    Args:
        name:    One of the keys in DOMAIN_COLLECTORS (e.g. "niap", "csfc").
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
        help="Domain to collect (e.g. niap, csfc, eucc).",
    )
    parser.add_argument(
        "--out-dir",
        default="snapshots/partial",
        help="Directory to write the partial snapshot JSON (default: snapshots/partial).",
    )
    args = parser.parse_args()
    collect_domain(args.domain, out_dir=args.out_dir)
