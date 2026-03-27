"""
differ.py — Compares two snapshots and returns a structured diff.

Features:
  - Schema version compatibility check
  - Keyword-alert scanning (WATCH_KEYWORDS from config)
  - partial_hash field comparison for PDF polling fallback (fix #10)
  - Improved weekly alert deduplication (source+title key, not timestamp) (fix #11)
  - categorize_news() applied to CSfC/CC Crypto/NIST feed items (fix #12)
  - Type hints throughout
"""

from __future__ import annotations

import logging
from typing import Any

import config

log = logging.getLogger(__name__)

# Type aliases
Snapshot = dict[str, Any]
Records  = list[dict[str, Any]]


# ── Schema compatibility ───────────────────────────────────────────────────────
class SchemaVersionError(RuntimeError):
    pass

def check_schema_compat(old: Snapshot, new: Snapshot) -> None:
    """Warn (don't crash) if schema versions differ."""
    old_v = old.get("schema_version", 1)
    new_v = new.get("schema_version", 1)
    if old_v != new_v:
        log.warning(
            "Schema version mismatch: old=%s new=%s — diff may be inaccurate.",
            old_v, new_v,
        )


# ── Helpers ───────────────────────────────────────────────────────────────────
def _ids(records: Records, key: str) -> set[str]:
    return {str(r[key]) for r in records if key in r}

def byid(records: Records, key: str) -> dict[str, Any]:
    return {str(r[key]): r for r in records if key in r}

def categorize_news(title: str) -> str:
    """Map a news/feed item title to a category using NEWS_CATEGORY_KEYWORDS.
    Applied to NIAP news, CSfC feeds, CC Crypto feeds, and NIST feeds.
    """
    t = title.lower()
    for cat, keywords in config.NEWS_CATEGORY_KEYWORDS.items():
        if cat == "NEWS":
            continue
        if any(kw in t for kw in keywords):
            return cat
    return "NEWS"

def is_cisco_ndcpp(product: dict[str, Any]) -> bool:
    vendor = product.get("vendor_id_name", "").lower()
    if not any(kw in vendor for kw in config.CISCO_VENDOR_KEYWORDS):
        return False
    pps = product.get("protection_profiles", [])
    return any(
        any(kw in pp.get("pp_short_name", "") for kw in config.NDCPP_PP_KEYWORDS)
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


# ── Keyword alert scanner ─────────────────────────────────────────────────────
def scan_watch_keywords(text: str) -> list[str]:
    """Return any WATCH_KEYWORDS found (case-insensitive) in text."""
    tl = text.lower()
    return [kw for kw in config.WATCH_KEYWORDS if kw.lower() in tl]

def flag_alerts(diff: Snapshot) -> list[dict[str, Any]]:
    """Walk a computed diff and return a list of high-priority alert objects.
    Each alert: {source, kind, title, matched_keywords}.
    """
    alerts: list[dict[str, Any]] = []

    def _add(source: str, kind: str, title: str) -> None:
        hits = scan_watch_keywords(title)
        if hits:
            alerts.append({
                "source":           source,
                "kind":             kind,
                "title":            title,
                "matched_keywords": hits,
            })

    # NIAP PPs
    for p in diff.get("niap", {}).get("pps", {}).get("added", []):
        _add("NIAP PP", "new", p.get("pp_short_name", "") + " " + p.get("pp_name", ""))
    for p in diff.get("niap", {}).get("pps", {}).get("sunset_changes", []):
        _add("NIAP PP", "sunset", p.get("pp_short_name", ""))

    # NIAP TDs
    for t in diff.get("niap", {}).get("tds", {}).get("added", []):
        _add("NIAP TD", "new", t.get("title", "") + " " + t.get("identifier", ""))

    # NIAP News
    for item in diff.get("niap", {}).get("news", {}).get("added", []):
        _add("NIAP News", item.get("_category", "NEWS"), item.get("title", ""))

    # CCTL Labs
    for lab, items in diff.get("cctl_labs", {}).items():
        for item in items:
            _add(f"Lab: {lab}", "post", item.get("title", ""))

    # CSfC feeds
    for feed_name, items in diff.get("csfc", {}).get("feeds", {}).items():
        for item in items:
            _add(f"CSfC Feed: {feed_name}", "advisory", item.get("title", ""))

    # CSfC APL page changes
    for item in diff.get("csfc", {}).get("pages", {}).get("apl", {}).get("added", []):
        _add("CSfC APL", "new", item.get("text", ""))

    # CSfC Capability Package header changes
    for cp_name, change in diff.get("csfc", {}).get("capability_packages", {}).items():
        if change.get("changed"):
            _add("CSfC CP", "updated", cp_name)

    # CC Crypto Catalog page changes
    for page_key, page_diff in diff.get("cc_crypto", {}).get("pages", {}).items():
        for item in page_diff.get("added", []):
            _add(f"CC Crypto: {page_key}", "publication", item.get("text", ""))

    # CC Crypto document header changes
    for doc_name, change in diff.get("cc_crypto", {}).get("doc_headers", {}).items():
        if change.get("changed"):
            _add("CC Crypto Doc", "updated", doc_name)

    # NIST page changes
    for page_key, page_diff in diff.get("nist", {}).get("pages", {}).items():
        for item in page_diff.get("added", []):
            _add(f"NIST: {page_key}", "publication", item.get("text", ""))

    # NIST document header changes
    for doc_name, change in diff.get("nist", {}).get("doc_headers", {}).items():
        if change.get("changed"):
            _add("NIST Doc", "updated", doc_name)

    # NIST RSS feed new items
    for feed_name, items in diff.get("nist", {}).get("feeds", {}).items():
        for item in items:
            _add(f"NIST Feed: {feed_name}", "news", item.get("title", ""))

    if alerts:
        log.warning("[Alerts] %d keyword match(es) found!", len(alerts))
    return alerts


# ── NIAP diffs ────────────────────────────────────────────────────────────────
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
    added     = [new_cisco[i] for i in set(new_cisco) - set(old_cisco)]
    removed   = [old_cisco[i] for i in set(old_cisco) - set(new_cisco)]
    newly_archived = [
        new_cisco[pid]
        for pid in set(old_cisco) & set(new_cisco)
        if (old_cisco[pid].get("status_sort") == "Certified"
            and new_cisco[pid].get("status_sort") == "Archived")
    ]
    return {"added": added, "removed": removed, "newly_archived": newly_archived}

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

def diff_niap_cctls(old_cctls: Records, new_cctls: Records) -> dict[str, Any]:
    old_map = byid(old_cctls, "cctl_id")
    new_map = byid(new_cctls, "cctl_id")
    added   = [new_map[i] for i in set(new_map) - set(old_map)]
    removed = [old_map[i] for i in set(old_map) - set(new_map)]
    status_changes = []
    for cid in set(old_map) & set(new_map):
        old_s = old_map[cid].get("status_id", {}).get("status_name")
        new_s = new_map[cid].get("status_id", {}).get("status_name")
        if old_s != new_s:
            status_changes.append({**new_map[cid], "old_status": old_s, "new_status": new_s})
    return {"added": added, "removed": removed, "status_changes": status_changes}


# ── CC Portal diffs ───────────────────────────────────────────────────────────
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


# ── CCTL lab diffs ────────────────────────────────────────────────────────────
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


# ── Generic header diff helper ────────────────────────────────────────────────
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


# ── Generic page text diff helper ─────────────────────────────────────────────
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


# ── Generic feed diff helper ───────────────────────────────────────────────────
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


# ── Master diff ───────────────────────────────────────────────────────────────
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

    diff: Snapshot = {
        "period_start": old_snapshot.get("collected_at", ""),
        "period_end":   new_snapshot.get("collected_at", ""),
        "niap": {
            "pps":          diff_niap_pps(old_n.get("pps", []),     new_n.get("pps", [])),
            "tds":          diff_niap_tds(old_n.get("tds", []),     new_n.get("tds", [])),
            "cisco_ndcpp":  diff_niap_pcl_cisco(old_n.get("pcl", []), new_n.get("pcl", [])),
            "news":         diff_niap_news(old_n.get("news", []),   new_n.get("news", [])),
            "events":       diff_niap_events(old_n.get("events", []), new_n.get("events", [])),
            "cctls":        diff_niap_cctls(old_n.get("cctls", []), new_n.get("cctls", [])),
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
    }

    diff["alerts"] = flag_alerts(diff)

    td_new  = len(diff["niap"]["tds"]["added"])
    pp_new  = len(diff["niap"]["pps"]["added"])
    alerts  = len(diff["alerts"])
    log.info("[Diff] PPs new:%d TDs new:%d alerts:%d", pp_new, td_new, alerts)
    return diff


# ── Weekly merge ──────────────────────────────────────────────────────────────
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
        Default key: str(item)[:120] — override for smarter dedup.
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
                       "events": {"added":[]},
                       "cctls": {"added":[], "removed":[], "status_changes":[]}}),
        ("cc_portal", {"news": {"added":[]}, "pps": {"added":[]}, "products": {"added":[]}}),
        ("cctl_labs", {}),
        ("csfc",      {"feeds": {}, "pages": {}, "capability_packages": {}}),
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

        # Alerts — use source+title key to avoid duplicating same event (fix #11)
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
        for cp_name, cp_data in d.get("csfc", {}).get("capability_packages", {}).items():
            weekly["csfc"]["capability_packages"][cp_name] = cp_data

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


# ── CSfC diff ─────────────────────────────────────────────────────────────────
def diff_csfc(old_csfc: Snapshot, new_csfc: Snapshot) -> Snapshot:
    """Diff two CSfC snapshots."""
    pages = _diff_pages(old_csfc.get("pages", {}), new_csfc.get("pages", {}))
    cap_packages = _diff_doc_headers(
        old_csfc.get("capability_package_headers", {}),
        new_csfc.get("capability_package_headers", {}),
    )
    feeds = _diff_feeds(
        old_csfc.get("feeds", {}),
        new_csfc.get("feeds", {}),
        categorize=True,
    )

    page_changes  = sum(len(v.get("added", [])) for v in pages.values())
    cp_changes    = len(cap_packages)
    feed_new      = sum(len(v) for v in feeds.values())
    log.info(
        "[CSfC Diff] page-items-added:%d CP-changes:%d feed-new:%d",
        page_changes, cp_changes, feed_new,
    )
    return {"pages": pages, "capability_packages": cap_packages, "feeds": feeds}


# ── CC Crypto Catalog diff ────────────────────────────────────────────────────
def diff_cc_crypto(old_cc: Snapshot, new_cc: Snapshot) -> Snapshot:
    """Diff two CC Crypto Catalog snapshots."""
    pages       = _diff_pages(old_cc.get("pages", {}), new_cc.get("pages", {}))
    doc_headers = _diff_doc_headers(old_cc.get("doc_headers", {}), new_cc.get("doc_headers", {}))

    page_changes = sum(len(v.get("added", [])) for v in pages.values())
    doc_changes  = len(doc_headers)
    log.info("[CC Crypto Diff] page-items-added:%d doc-changes:%d", page_changes, doc_changes)
    return {"pages": pages, "doc_headers": doc_headers}


# ── NIST CSRC diff ────────────────────────────────────────────────────────────
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
