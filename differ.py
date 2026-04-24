"""
differ.py — Computes diffs between two CC Pulse snapshots.

Compares old and new snapshot dicts, returning a diff dict with
per-source change lists and keyword alerts.
"""

from __future__ import annotations

import copy
import logging
import re
from typing import Any

import config

log = logging.getLogger(__name__)

# -- Types ---------------------------------------------------------------------
Snapshot = dict[str, Any]
Records  = list[dict[str, Any]]

# -- Internal helpers ----------------------------------------------------------

def _add(alerts: list, source: str, kind: str, title: str, *,
         url: str = "", detail: str = "", keywords: list | None = None, tab: str = "us") -> None:
    """Append a structured alert entry."""
    alerts.append({
        "source":   source,
        "kind":     kind,
        "title":    title,
        "url":      url,
        "detail":   detail,
        "keywords": keywords or [],
        "tab":      tab,
    })


def check_schema_compat(old: Snapshot, new: Snapshot) -> None:
    """Warn if snapshot schema versions differ."""
    ov = old.get("schema_version", 1)
    nv = new.get("schema_version", 1)
    if ov != nv:
        log.warning("Schema version mismatch: old=%s new=%s — diff may be incomplete.", ov, nv)


# -- Keyword alert scanner -----------------------------------------------------


def _ids(records: Records, key: str) -> set[str]:
    """Return a set of stringified key values from a list of records."""
    return {str(r[key]) for r in records if key in r}


def byid(records: Records, key: str) -> dict[str, Any]:
    """Index a list of records by a string key field."""
    return {str(r[key]): r for r in records if key in r}


def categorize_news(title: str) -> str:
    """Map a news/feed item title to a category using NEWS_CATEGORY_KEYWORDS."""
    t = title.lower()
    for cat, keywords in config.NEWS_CATEGORY_KEYWORDS.items():
        if cat == "NEWS":
            continue
        if any(kw in t for kw in keywords):
            return cat
    return "NEWS"


def is_cisco_ndcpp(product: dict[str, Any]) -> bool:
    """Return True if a PCL product is a Cisco NDcPP certification."""
    vendor = (product.get("vendor_id_name") or "").lower()
    if not any(kw in vendor for kw in config.CISCO_VENDOR_KEYWORDS):
        return False
    pps = product.get("protection_profiles") or []
    return any(
        any(kw in (pp.get("pp_short_name") or "") for kw in config.NDCPP_PP_KEYWORDS)
        for pp in pps
    )


def _headers_changed(old_h: dict, new_h: dict) -> bool:
    """Return True if any change-detection field differs between two header dicts.

    Checks in order of reliability:
      1. ETag — most authoritative when present
      2. Last-Modified — widely supported
      3. Content-Length — rough version signal (can change without content change)
      4. partial_hash — MD5 of first 2 KB; populated only when 1-3 are all absent
    """
    for field in ("etag", "last_modified", "content_length", "partial_hash"):
        old_val = old_h.get(field, "")
        new_val = new_h.get(field, "")
        # Only compare if at least one side has a non-empty value
        if old_val or new_val:
            if old_val != new_val:
                return True
    return False

def flag_alerts(diff: Snapshot) -> list[dict]:
    """Scan a diff for keyword matches and return structured alert list."""
    alerts: list[dict] = []
    kw_tiers = config.WATCH_KEYWORDS  # {term: tier}

    def _matches(text: str) -> list[str]:
        if not text:
            return []
        text_l = text.lower()
        return [kw for kw in kw_tiers if kw.lower() in text_l]

    def _scan_items(source: str, items: list[dict], url_key: str = "url", tab: str = "us") -> None:
        for item in items:
            title  = item.get("title", "") or item.get("name", "")
            detail = item.get("detail", "") or item.get("description", "")
            url    = item.get(url_key, "") or item.get("url", "")
            hits   = _matches(title + " " + detail)
            if hits:
                _add(alerts, source, item.get("kind", "alert"), title,
                     url=url, detail=detail, keywords=hits, tab=tab)

    def _add_text(source: str, kind: str, text: str,
                  url: str = "", detail: str = "", tab: str = "us") -> None:
        """Scan a raw scraped text blob (lower-signal) against _matches only."""
        hits = _matches(text)
        if hits:
            truncated = text[:120].rstrip() + ("…" if len(text) > 120 else "")
            _add(alerts, source, kind, truncated, url=url, detail=detail, keywords=hits, tab=tab)


    # NIAP PCL (Cisco NDcPP certs)
    for item in diff.get("niap", {}).get("cisco_ndcpp", {}).get("added", []):
        title = item.get("product_name", item.get("title", item.get("name", "")))
        hits  = _matches(title)
        if hits:
            _add(alerts, "NIAP PCL", "new_cert", title,
                 url=item.get("url", ""), keywords=hits, tab="us")

    # NIAP Protection Profiles
    _scan_items("NIAP PP", diff.get("niap", {}).get("pps", {}).get("added", []), tab="us")
    _scan_items("NIAP PP", diff.get("niap", {}).get("pps", {}).get("sunset_changes", []))

    # NIAP Technical Decisions
    _TD_URL = "https://www.niap-ccevs.org/technical-decisions"
    for td in diff.get("niap", {}).get("tds", {}).get("added", []):
        title  = td.get("title", "") or td.get("identifier", "")
        detail = td.get("identifier", "")
        hits   = _matches(title + " " + detail)
        if hits:
            _add(alerts, "NIAP TD", "new", title,
                 url=_TD_URL, detail=detail, keywords=hits, tab="us")

    # NIAP News
    _scan_items("NIAP News", diff.get("niap", {}).get("news", {}).get("added", []), tab="us")

    # CC Portal
    _scan_items("CC Portal", diff.get("cc_portal", {}).get("news", {}).get("added", []), tab="intl")
    _scan_items("CC Portal", diff.get("cc_portal", {}).get("pps", {}).get("added", []), tab="intl")

    # CCTL Labs
    _scan_items("CCTL Labs", diff.get("cctl_labs", {}).get("added", []), tab="intl")

# CSfC Component Selections -- hash change means the PDF content changed
    for sel_name, change in diff.get("csfc", {}).get("component_selections", {}).items():
        if change.get("changed"):
            _add_text(
                "CSfC Component Selections",
                "updated",
                sel_name,
                url=config.CSFC_COMPONENTS_LIST_URL,
                detail="Selections document content changed",
                tab="us",
            )

    # CC Crypto Catalog page changes (scraped text — use _add_text for narrower matching)
    for page_key, page_diff in diff.get("cc_crypto", {}).get("pages", {}).items():
        for item in page_diff.get("added", []):
            _add_text(f"CC Crypto: {page_key}", "publication",
                      item.get("text", ""),
                      url=item.get("href") or "https://www.commoncriteriaportal.org/cc/index.cfm",
                      detail=f"New item on CC Portal {page_key} page",
                      tab="us")

    # NIST page changes (scraped text — use _add_text for narrower matching)
    _nist_page_urls = {
        "news":           "https://csrc.nist.gov/news",
        "fips":           "https://csrc.nist.gov/publications/fips",
        "cmvp_mip":       "https://csrc.nist.gov/projects/cryptographic-module-validation-program/modules-in-process/modules-in-process-list",
        "pqc":            "https://csrc.nist.gov/projects/post-quantum-cryptography",
        "crypto_standards":"https://csrc.nist.gov/projects/cryptographic-standards-and-guidelines",
        "cmvp_validated": "https://csrc.nist.gov/projects/cryptographic-module-validation-program/validated-modules",
    }
    for page_key, page_diff in diff.get("nist", {}).get("pages", {}).items():
        for item in page_diff.get("added", []):
            _add_text(f"NIST: {page_key}", "publication",
                      item.get("text", ""),
                      url=item.get("href") or _nist_page_urls.get(page_key, "https://csrc.nist.gov/"),
                      detail=f"New item on NIST CSRC {page_key.replace('_', ' ')} page",
                      tab="us")

    # NIST RSS feed new items
    for feed_name, items in diff.get("nist", {}).get("feeds", {}).items():
        for item in items:
            _add_text(f"NIST Feed: {feed_name}", "news",
                 item.get("title", ""),
                 url=item.get("link") or "https://csrc.nist.gov/",
                 detail=f"Feed: {feed_name} · Published: {(item.get('published') or '')[:16] or 'N/A'}",
                 tab="us")


    # NATO NIAPCL page changes
    for page_key, page_diff in diff.get("nato", {}).get("pages", {}).items():
        for item in page_diff.get("added", []):
            _add_text(f"NATO NIAPCL: {page_key}", "new",
                      item.get("text", "") or item.get("raw_text", ""),
                      url=item.get("link") or config.NATO_NIAPCL_URL,
                      detail=f"New item on NATO NIAPCL {page_key} page",
                      tab="intl")

    # NATO NIAPCL Cisco-specific additions (Tier 1 EU/NATO)
    for item in diff.get("nato", {}).get("cisco_added", []):
        _add_text("NATO NIAPCL", "new_cert",
             item.get("name", "") or item.get("raw_text", "")[:80],
             url=item.get("link") or config.NATO_NIAPCL_URL,
             detail=f"New Cisco product on NATO NIAPCL · {item.get('manufacturer', '')}",
             tab="intl")

    # EUCC requirements page changes
    for item in diff.get("eucc", {}).get("pages", {}).get("requirements", {}).get("added", []):
        _add_text("EUCC Requirements", "updated",
                  item.get("text", ""),
                  url=item.get("href") or config.EUCC_REQUIREMENTS_URL,
                  detail="New item on EUCC requirements / scheme page",
                  tab="eu")

    # EUCC certificate additions (general)
    for item in diff.get("eucc", {}).get("pages", {}).get("certificates", {}).get("added", []):
        _add_text("EUCC Certificates", "new_cert",
                  item.get("text", "") or item.get("name", ""),
                  url=item.get("href") or config.EUCC_CERTIFICATES_URL,
                  detail="New EUCC certified product",
               tab="eu")

    # EUCC Cisco-specific certificates (Tier 1 EU)
    for item in diff.get("eucc", {}).get("cisco_added", []):
        _add_text("EUCC Certificates", "new_cert",
             item.get("name", "") or item.get("text", "")[:80],
             url=item.get("href") or config.EUCC_CERTIFICATES_URL,
             detail="New Cisco EUCC certified product",
         tab="eu")

    if alerts:
        log.warning("[Alerts] %d keyword match(es) found!", len(alerts))
    return alerts


# -- NIAP diffs ----------------------------------------------------------------
def diff_niap_pps(old_pps: Records, new_pps: Records) -> dict[str, Any]:
    old_map = byid(old_pps, "pp_id")
    new_map = byid(new_pps, "pp_id")
    old_ids = set(old_map)
    new_ids = set(new_map)

    added   = [new_map[i] for i in new_ids - old_ids]
    removed = [old_map[i] for i in old_ids - new_ids]

    sunset_changes = []
    for pid in old_ids & new_ids:
        old_s = old_map[pid].get("sunset_date")
        new_s = new_map[pid].get("sunset_date")
        if old_s != new_s and new_s:
            sunset_changes.append({**new_map[pid], "old_sunset": old_s, "new_sunset": new_s})

    status_changes = []
    for pid in old_ids & new_ids:
        old_st = old_map[pid].get("status")
        new_st = new_map[pid].get("status")
        if old_st != new_st:
            status_changes.append({**new_map[pid], "old_status": old_st, "new_status": new_st})

    return {
        "added":          added,
        "removed":        removed,
        "sunset_changes": sunset_changes,
        "status_changes": status_changes,
    }

def diff_niap_tds(old_tds: Records, new_tds: Records) -> dict[str, Any]:
    old_map = byid(old_tds, "td_id")
    new_map = byid(new_tds, "td_id")
    old_ids = set(old_map)
    new_ids = set(new_map)
    added   = [new_map[i] for i in new_ids - old_ids]
    removed = []
    for tid in old_ids & new_ids:
        if not old_map[tid].get("removed_on") and new_map[tid].get("removed_on"):
            removed.append({**new_map[tid]})
    return {"added": added, "removed": removed}

def diff_niap_pcl_cisco(old_pcl: Records, new_pcl: Records) -> dict[str, Any]:
    old_cisco = {str(p["product_id"]): p for p in old_pcl if is_cisco_ndcpp(p)}
    new_cisco = {str(p["product_id"]): p for p in new_pcl if is_cisco_ndcpp(p)}
    # Newly added to PCL already certified
    brand_new = [new_cisco[i] for i in set(new_cisco) - set(old_cisco)
                 if new_cisco[i].get("status_sort") == "Certified"]
    # Was In Progress, now Certified (the most common transition)
    newly_certified = [
        new_cisco[pid] for pid in set(old_cisco) & set(new_cisco)
        if (old_cisco[pid].get("status_sort") in ("In Progress", "In Review")
            and new_cisco[pid].get("status_sort") == "Certified")
    ]
    added = brand_new + newly_certified
    removed = [old_cisco[i] for i in set(old_cisco) - set(new_cisco)]
    newly_archived = [
        new_cisco[pid] for pid in set(old_cisco) & set(new_cisco)
        if (old_cisco[pid].get("status_sort") == "Certified"
            and new_cisco[pid].get("status_sort") == "Archived")
    ]
    return {"added": added, "removed": removed, "newly_archived": newly_archived}
def diff_niap_pcl_all(old_pcl: Records, new_pcl: Records) -> dict[str, Any]:
    """Diff the full NIAP PCL across all tech types."""
    old_all = {str(p["product_id"]): p for p in old_pcl}
    new_all = {str(p["product_id"]): p for p in new_pcl}
    added     = [new_all[i] for i in set(new_all) - set(old_all)]
    removed   = [old_all[i] for i in set(old_all) - set(new_all)]
    newly_archived = [
        new_all[pid]
        for pid in set(old_all) & set(new_all)
        if (old_all[pid].get("status_sort") == "Certified"
            and new_all[pid].get("status_sort") == "Archived")
    ]
    return {"added": added, "removed": removed, "newly_archived": newly_archived}


def diff_niap_in_evaluation(old_pcl: Records, new_pcl: Records) -> dict[str, Any]:
    """Diff in-evaluation (In Progress) products from the NIAP PCL."""
    old_ie = {str(p["product_id"]): p for p in old_pcl if p.get("status_sort") == "In Progress"}
    new_ie = {str(p["product_id"]): p for p in new_pcl if p.get("status_sort") == "In Progress"}
    added   = [new_ie[i] for i in set(new_ie) - set(old_ie)]
    removed = [old_ie[i] for i in set(old_ie) - set(new_ie)]
    return {"added": added, "removed": removed, "current_count": len(new_ie)}


def diff_niap_news(old_news: Records, new_news: Records) -> dict[str, Any]:
    old_ids = _ids(old_news, "id")
    new_ids = _ids(new_news, "id")
    new_map = byid(new_news, "id")
    added   = [new_map[i] for i in new_ids - old_ids]
    for item in added:
        item["_category"] = categorize_news(item.get("title", ""))
    return {"added": added}

def diff_niap_events(old_events: Records, new_events: Records) -> dict[str, Any]:
    old_ids = _ids(old_events, "id")
    new_ids = _ids(new_events, "id")
    new_map = byid(new_events, "id")
    return {"added": [new_map[i] for i in new_ids - old_ids]}

# -- CC Portal diffs -----------------------------------------------------------
def diff_cc_news(old_items: Records, new_items: Records) -> dict[str, Any]:
    old_texts = {i["text"][:80] for i in old_items}
    return {"added": [i for i in new_items if i["text"][:80] not in old_texts]}

def diff_cc_pps(old_pps: Records, new_pps: Records) -> dict[str, Any]:
    def key(row: dict) -> str:
        vals = list(row.values())
        return vals[0] if vals else ""
    old_keys = {key(r) for r in old_pps}
    return {"added": [r for r in new_pps if key(r) not in old_keys]}

def diff_cc_products(old_products: Records, new_products: Records) -> dict[str, Any]:
    def key(row: dict) -> str:
        vals = list(row.values())
        return " ".join(vals[:2]) if len(vals) >= 2 else (vals[0] if vals else "")
    old_keys = {key(r) for r in old_products}
    return {"added": [r for r in new_products if key(r) not in old_keys]}


# -- CCTL lab diffs ------------------------------------------------------------
def diff_cctl_labs(
    old_labs: dict[str, Records],
    new_labs: dict[str, Records],
) -> dict[str, Records]:
    result: dict[str, Records] = {}
    for lab in set(old_labs) | set(new_labs):
        old_ids = {i.get("id", i.get("title", "")) for i in old_labs.get(lab, [])}
        added = [
            i for i in new_labs.get(lab, [])
            if i.get("id", i.get("title", "")) not in old_ids
        ]
        if added:
            result[lab] = added
    return result


# -- Generic header diff helper ------------------------------------------------
def _diff_doc_headers(old_docs: dict, new_docs: dict) -> dict:
    """Diff two {name: header_dict} mappings using _headers_changed().

    Handles the partial_hash fallback field added by collector._poll_doc_headers()
    so that servers that don't serve Last-Modified/ETag/Content-Length are still
    detected (fix #10).
    """
    result = {}
    for doc_name in set(old_docs) | set(new_docs):
        old_h = old_docs.get(doc_name, {})
        new_h = new_docs.get(doc_name, {})
        if _headers_changed(old_h, new_h):
            result[doc_name] = {
                "changed":           True,
                "old_last_modified": old_h.get("last_modified", ""),
                "new_last_modified": new_h.get("last_modified", ""),
                "old_etag":          old_h.get("etag", ""),
                "new_etag":          new_h.get("etag", ""),
                "old_content_length":old_h.get("content_length", ""),
                "new_content_length":new_h.get("content_length", ""),
                "old_partial_hash":  old_h.get("partial_hash", ""),
                "new_partial_hash":  new_h.get("partial_hash", ""),
                "url":               new_h.get("url", old_h.get("url", "")),
            }
    return result


def _diff_selection_hashes(old_sels: dict, new_sels: dict) -> dict:
    """Diff two {name: {url, hash, fetch_error}} dicts by SHA-256 hash.
    Only flags a change when both old and new have a valid hash and they differ.
    Skips entries where either side had a fetch error to avoid false positives
    from transient network failures.
    """
    result = {}
    for name in set(old_sels) | set(new_sels):
        old_s = old_sels.get(name, {})
        new_s = new_sels.get(name, {})
        if old_s.get("fetch_error") or new_s.get("fetch_error"):
            log.warning(
                "[CSfC Selections] Skipping diff for %s due to fetch error: old=%r new=%r",
                name, old_s.get("fetch_error", ""), new_s.get("fetch_error", ""),
            )
            continue
        old_hash = old_s.get("hash", "")
        new_hash = new_s.get("hash", "")
        if old_hash and new_hash and old_hash != new_hash:
            result[name] = {
                "changed": True,
                "old_hash": old_hash,
                "new_hash": new_hash,
            }
    return result


def _diff_selection_hashes(old_sels: dict, new_sels: dict) -> dict:
    """Diff two {name: {url, hash, fetch_error}} dicts by SHA-256 hash.
    Only flags a change when both old and new have a valid hash and they differ.
    Skips entries where either side had a fetch error to avoid false positives
    from transient network failures.
    """
    result = {}
    for name in set(old_sels) | set(new_sels):
        old_s = old_sels.get(name, {})
        new_s = new_sels.get(name, {})
        if old_s.get("fetch_error") or new_s.get("fetch_error"):
            log.warning(
                "[CSfC Selections] Skipping diff for %s due to fetch error: old=%r new=%r",
                name, old_s.get("fetch_error", ""), new_s.get("fetch_error", ""),
            )
            continue
        old_hash = old_s.get("hash", "")
        new_hash = new_s.get("hash", "")
        if old_hash and new_hash and old_hash != new_hash:
            result[name] = {
                "changed": True,
                "old_hash": old_hash,
                "new_hash": new_hash,
            }
    return result


# -- Generic page text diff helper ---------------------------------------------
def _diff_pages(old_pages: dict, new_pages: dict) -> dict:
    """Diff two {page_key: [items]} dicts by text prefix."""
    result = {}
    for page_key in set(old_pages) | set(new_pages):
        old_items = old_pages.get(page_key, [])
        new_items = new_pages.get(page_key, [])
        old_texts = {i["text"][:120] for i in old_items if i.get("text")}
        new_texts = {i["text"][:120] for i in new_items if i.get("text")}
        added   = [i for i in new_items if i.get("text", "")[:120] not in old_texts]
        removed = [i for i in old_items if i.get("text", "")[:120] not in new_texts]
        if added or removed:
            result[page_key] = {"added": added, "removed": removed}
    return result


# -- Generic feed diff helper ---------------------------------------------------
def _diff_feeds(old_feeds: dict, new_feeds: dict, categorize: bool = False) -> dict:
    """Diff two {feed_name: [items]} dicts by id/title/link key.

    If categorize=True, applies categorize_news() to each new item's title
    and stores result as _category (fix #12).
    """
    result = {}
    for feed_name in set(old_feeds) | set(new_feeds):
        old_items = old_feeds.get(feed_name, [])
        new_items = new_feeds.get(feed_name, [])
        old_ids = {
            i.get("id", i.get("title", i.get("link", "")))
            for i in old_items
        }
        added = [
            i for i in new_items
            if i.get("id", i.get("title", i.get("link", ""))) not in old_ids
        ]
        if categorize:
            for item in added:
                item.setdefault("_category", categorize_news(item.get("title", "")))
        if added:
            result[feed_name] = added
    return result



# -- NATO NIAPCL diff ---------------------------------------------------------
def diff_nato(old_nato: Snapshot, new_nato: Snapshot) -> Snapshot:
    """Diff two NATO NIAPCL snapshots."""
    old_pages = old_nato.get("pages", {})
    new_pages = new_nato.get("pages", {})

    pages = _diff_pages(old_pages, new_pages)

    # Diff Cisco products specifically
    old_cisco = {p.get("raw_text", "")[:80]: p for p in old_nato.get("cisco_products", [])}
    new_cisco = {p.get("raw_text", "")[:80]: p for p in new_nato.get("cisco_products", [])}
    cisco_added   = [new_cisco[k] for k in set(new_cisco) - set(old_cisco)]
    cisco_removed = [old_cisco[k] for k in set(old_cisco) - set(new_cisco)]

    page_changes = sum(len(v.get("added", [])) for v in pages.values())
    log.info("[NATO Diff] page-items-added:%d cisco-added:%d cisco-removed:%d",
             page_changes, len(cisco_added), len(cisco_removed))
    return {
        "pages": pages,
        "cisco_added":   cisco_added,
        "cisco_removed": cisco_removed,
    }


# -- EUCC / ENISA diff --------------------------------------------------------
def diff_eucc(old_eucc: Snapshot, new_eucc: Snapshot) -> Snapshot:
    """Diff two EUCC / ENISA snapshots.
    Handles two sub-sources:
    - requirements page (scheme policy / requirement changes)
    - certificates page (new certified products, including Cisco)
    """
    old_pages = old_eucc.get("pages", {})
    new_pages = new_eucc.get("pages", {})

    pages = _diff_pages(old_pages, new_pages)

    # Diff Cisco-specific certificates
    old_cisco = {c.get("text", "")[:80]: c for c in old_eucc.get("cisco_certs", [])}
    new_cisco = {c.get("text", "")[:80]: c for c in new_eucc.get("cisco_certs", [])}
    cisco_added   = [new_cisco[k] for k in set(new_cisco) - set(old_cisco)]
    cisco_removed = [old_cisco[k] for k in set(old_cisco) - set(new_cisco)]

    req_changes  = len(pages.get("requirements", {}).get("added", []))
    cert_changes = len(pages.get("certificates", {}).get("added", []))
    log.info("[EUCC Diff] req-changes:%d cert-additions:%d cisco-added:%d",
             req_changes, cert_changes, len(cisco_added))
    return {
        "pages": pages,
        "cisco_added":   cisco_added,
        "cisco_removed": cisco_removed,
    }


# -- Master diff ---------------------------------------------------------------
def compute_diff(old_snapshot: Snapshot, new_snapshot: Snapshot) -> Snapshot:
    """Compare two full snapshots, scan for keyword alerts, return diff."""
    check_schema_compat(old_snapshot, new_snapshot)

    old_n  = old_snapshot.get("niap",      {})
    new_n  = new_snapshot.get("niap",      {})
    old_c  = old_snapshot.get("cc_portal", {})
    new_c  = new_snapshot.get("cc_portal", {})
    old_l  = old_snapshot.get("cctl_labs", {})
    new_l  = new_snapshot.get("cctl_labs", {})
    old_cs = old_snapshot.get("csfc",      {})
    new_cs = new_snapshot.get("csfc",      {})
    old_cc = old_snapshot.get("cc_crypto", {})
    new_cc = new_snapshot.get("cc_crypto", {})
    old_ni = old_snapshot.get("nist",      {})
    new_ni = new_snapshot.get("nist",      {})
    old_na = old_snapshot.get("nato",      {})
    new_na = new_snapshot.get("nato",      {})
    old_eu = old_snapshot.get("eucc",      {})
    new_eu = new_snapshot.get("eucc",      {})

    diff: Snapshot = {
        "period_start": old_snapshot.get("collected_at", ""),
        "period_end":   new_snapshot.get("collected_at", ""),
        "niap": {
            "pps":          diff_niap_pps(old_n.get("pps", []),     new_n.get("pps", [])),
            "tds":          diff_niap_tds(old_n.get("tds", []),     new_n.get("tds", [])),
            "cisco_ndcpp":  diff_niap_pcl_cisco(old_n.get("pcl", []), new_n.get("pcl", [])),
            "pcl_all":      diff_niap_pcl_all(old_n.get("pcl", []),   new_n.get("pcl", [])),
            "in_evaluation": diff_niap_in_evaluation(old_n.get("pcl", []), new_n.get("pcl", [])),
            "news":         diff_niap_news(old_n.get("news", []),   new_n.get("news", [])),
            "events":       diff_niap_events(old_n.get("events", []), new_n.get("events", [])),
        },
        "cc_portal": {
            "news":     diff_cc_news(old_c.get("news", []),     new_c.get("news", [])),
            "pps":      diff_cc_pps(old_c.get("pps", []),       new_c.get("pps", [])),
            "products": diff_cc_products(old_c.get("products", []), new_c.get("products", [])),
        },
        "cctl_labs": diff_cctl_labs(old_l, new_l),
        "csfc":      diff_csfc(old_cs, new_cs),
        "cc_crypto": diff_cc_crypto(old_cc, new_cc),
        "nist":      diff_nist(old_ni, new_ni),
        "nato":      diff_nato(old_na, new_na),
        "eucc":      diff_eucc(old_eu, new_eu),
    }

    diff["alerts"] = flag_alerts(diff)

    td_new  = len(diff["niap"]["tds"]["added"])
    pp_new  = len(diff["niap"]["pps"]["added"])
    alerts  = len(diff["alerts"])
    log.info("[Diff] PPs new:%d TDs new:%d alerts:%d", pp_new, td_new, alerts)
    return diff


# -- Weekly merge --------------------------------------------------------------
def merge_weekly_diffs(diffs: list[Snapshot]) -> Snapshot:
    """Merge a list of daily diffs into one weekly summary.

    Improvements (fix #11):
      - Alert deduplication uses (source, title) as key instead of str(item)[:120],
        so the same underlying event isn't duplicated because timestamps differ.
      - All domain sections are explicitly initialized so a missing key on diffs[0]
        doesn't cause KeyError when later diffs have it.
    """
    if not diffs:
        return {}

    def merge_lists(*lists, key_fn=None):
        """Merge one or more lists, deduplicating by key_fn(item).
        Default key: str(item)[:120] -- override for smarter dedup.
        """
        seen:   set  = set()
        merged: list = []
        for lst in lists:
            for item in lst:
                k = key_fn(item) if key_fn else str(item)[:120]
                if k not in seen:
                    seen.add(k)
                    merged.append(item)
        return merged

    def alert_key(a: dict) -> str:
        """Deduplicate alerts on source+title, ignoring timestamps."""
        return f"{a.get('source','')}|{a.get('title','')}|{','.join(sorted(a.get('matched_keywords', [])))}"

    import copy
    weekly = copy.deepcopy(diffs[0])

    # Ensure all top-level domain keys exist on weekly
    for domain_key, default in [
        ("niap",      {"pps": {"added":[], "removed":[], "sunset_changes":[], "status_changes":[]},
                       "tds": {"added":[], "removed":[]},
                       "cisco_ndcpp": {"added":[], "removed":[], "newly_archived":[]},
                       "news": {"added":[]},
                       "events": {"added":[]}}),
        ("cc_portal", {"news": {"added":[]}, "pps": {"added":[]}, "products": {"added":[]}}),
        ("cctl_labs", {}),
        ("csfc",      {"feeds": {}, "pages": {}, "component_selections": {}}),
        ("cc_crypto", {"pages": {}, "doc_headers": {}}),
        ("nist",      {"pages": {}, "doc_headers": {}, "feeds": {}}),
        ("alerts",    []),
    ]:
        if domain_key not in weekly:
            weekly[domain_key] = default

    for d in diffs[1:]:
        # NIAP
        for key in ("added", "removed", "sunset_changes", "status_changes"):
            if key in weekly["niap"]["pps"] and key in d.get("niap", {}).get("pps", {}):
                weekly["niap"]["pps"][key] = merge_lists(
                    weekly["niap"]["pps"][key], d["niap"]["pps"][key])
        for key in ("added", "removed"):
            if key in weekly["niap"]["tds"] and key in d.get("niap", {}).get("tds", {}):
                weekly["niap"]["tds"][key] = merge_lists(
                    weekly["niap"]["tds"][key], d["niap"]["tds"][key])
        for key in ("added", "removed", "newly_archived"):
            weekly["niap"]["cisco_ndcpp"][key] = merge_lists(
                weekly["niap"]["cisco_ndcpp"].get(key, []),
                d.get("niap", {}).get("cisco_ndcpp", {}).get(key, []))
        weekly["niap"]["news"]["added"] = merge_lists(
            weekly["niap"]["news"]["added"],
            d.get("niap", {}).get("news", {}).get("added", []))
        weekly["niap"]["events"]["added"] = merge_lists(
            weekly["niap"]["events"]["added"],
            d.get("niap", {}).get("events", {}).get("added", []))

        # CC Portal
        weekly["cc_portal"]["news"]["added"] = merge_lists(
            weekly["cc_portal"]["news"]["added"],
            d.get("cc_portal", {}).get("news", {}).get("added", []))
        weekly["cc_portal"]["pps"]["added"] = merge_lists(
            weekly["cc_portal"]["pps"]["added"],
            d.get("cc_portal", {}).get("pps", {}).get("added", []))

        # CCTL Labs
        for lab, items in d.get("cctl_labs", {}).items():
            weekly["cctl_labs"][lab] = merge_lists(
                weekly["cctl_labs"].get(lab, []), items)

        # Alerts -- use source+title key to avoid duplicating same event (fix #11)
        weekly["alerts"] = merge_lists(
            weekly["alerts"], d.get("alerts", []), key_fn=alert_key)

        # CSfC
        for feed_name, items in d.get("csfc", {}).get("feeds", {}).items():
            weekly["csfc"]["feeds"][feed_name] = merge_lists(
                weekly["csfc"]["feeds"].get(feed_name, []), items)
        for page_key, page_diff in d.get("csfc", {}).get("pages", {}).items():
            if isinstance(page_diff, dict) and "added" in page_diff:
                if page_key not in weekly["csfc"]["pages"]:
                    weekly["csfc"]["pages"][page_key] = {"added": []}
                weekly["csfc"]["pages"][page_key]["added"] = merge_lists(
                    weekly["csfc"]["pages"][page_key]["added"], page_diff["added"])
        # CSfC component selections -- keep latest hash for each doc
        for sel_name, sel_data in d.get("csfc", {}).get("component_selections", {}).items():
            weekly["csfc"]["component_selections"][sel_name] = sel_data

        # CC Crypto
        for page_key, page_diff in d.get("cc_crypto", {}).get("pages", {}).items():
            if isinstance(page_diff, dict) and "added" in page_diff:
                if page_key not in weekly["cc_crypto"]["pages"]:
                    weekly["cc_crypto"]["pages"][page_key] = {"added": []}
                weekly["cc_crypto"]["pages"][page_key]["added"] = merge_lists(
                    weekly["cc_crypto"]["pages"][page_key]["added"], page_diff["added"])
        for doc_name, doc_data in d.get("cc_crypto", {}).get("doc_headers", {}).items():
            weekly["cc_crypto"]["doc_headers"][doc_name] = doc_data

        # NIST
        for page_key, page_diff in d.get("nist", {}).get("pages", {}).items():
            if isinstance(page_diff, dict) and "added" in page_diff:
                if page_key not in weekly["nist"]["pages"]:
                    weekly["nist"]["pages"][page_key] = {"added": []}
                weekly["nist"]["pages"][page_key]["added"] = merge_lists(
                    weekly["nist"]["pages"][page_key]["added"], page_diff["added"])
        for doc_name, doc_data in d.get("nist", {}).get("doc_headers", {}).items():
            weekly["nist"]["doc_headers"][doc_name] = doc_data
        for feed_name, items in d.get("nist", {}).get("feeds", {}).items():
            weekly["nist"]["feeds"][feed_name] = merge_lists(
                weekly["nist"]["feeds"].get(feed_name, []), items)

    return weekly


# -- CSfC diff -----------------------------------------------------------------
def diff_csfc(old_csfc: Snapshot, new_csfc: Snapshot) -> Snapshot:
    """Diff two CSfC snapshots."""
    pages = _diff_pages(old_csfc.get("pages", {}), new_csfc.get("pages", {}))
    component_selections = _diff_selection_hashes(
        old_csfc.get("component_selection_hashes", {}),
        new_csfc.get("component_selection_hashes", {}),
    )
    feeds = _diff_feeds(
        old_csfc.get("feeds", {}),
        new_csfc.get("feeds", {}),
        categorize=True,
    )
    page_changes = sum(len(v.get("added", [])) for v in pages.values())
    sel_changes = len(component_selections)
    feed_new = sum(len(v) for v in feeds.values())
    log.info(
        "[CSfC Diff] page-items-added:%d selection-changes:%d feed-new:%d",
        page_changes, sel_changes, feed_new,
    )
    return {"pages": pages, "component_selections": component_selections, "feeds": feeds}


# -- CC Crypto Catalog diff ----------------------------------------------------
def diff_cc_crypto(old_cc: Snapshot, new_cc: Snapshot) -> Snapshot:
    """Diff two CC Crypto Catalog snapshots."""
    pages       = _diff_pages(old_cc.get("pages", {}), new_cc.get("pages", {}))
    doc_headers = _diff_doc_headers(old_cc.get("doc_headers", {}), new_cc.get("doc_headers", {}))

    page_changes = sum(len(v.get("added", [])) for v in pages.values())
    doc_changes  = len(doc_headers)
    log.info("[CC Crypto Diff] page-items-added:%d doc-changes:%d", page_changes, doc_changes)
    return {"pages": pages, "doc_headers": doc_headers}


# -- NIST CSRC diff ------------------------------------------------------------
def diff_nist(old_nist: Snapshot, new_nist: Snapshot) -> Snapshot:
    """Diff two NIST CSRC snapshots."""
    pages       = _diff_pages(old_nist.get("pages", {}), new_nist.get("pages", {}))
    doc_headers = _diff_doc_headers(old_nist.get("doc_headers", {}), new_nist.get("doc_headers", {}))
    feeds       = _diff_feeds(
        old_nist.get("feeds", {}),
        new_nist.get("feeds", {}),
        categorize=True,
    )

    page_changes = sum(len(v.get("added", [])) for v in pages.values())
    doc_changes  = len(doc_headers)
    feed_new     = sum(len(v) for v in feeds.values())
    log.info(
        "[NIST Diff] page-items-added:%d doc-changes:%d feed-new:%d",
        page_changes, doc_changes, feed_new,
    )
    return {"pages": pages, "doc_headers": doc_headers, "feeds": feeds}



