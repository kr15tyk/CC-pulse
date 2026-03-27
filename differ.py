"""
differ.py — Compares two snapshots and returns a structured diff.

Features:
  - Schema version compatibility check
  - Keyword-alert scanning (WATCH_KEYWORDS from config)
  - Type hints throughout
"""

from __future__ import annotations

import logging
from typing import Any

import config

log = logging.getLogger(__name__)

# Type alias for a snapshot or sub-dict
Snapshot = dict[str, Any]
Records  = list[dict[str, Any]]


# ── Schema compatibility ──────────────────────────────────────────────────────

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


# ── Keyword alert scanner ─────────────────────────────────────────────────────

def scan_watch_keywords(text: str) -> list[str]:
    """Return any WATCH_KEYWORDS found (case-insensitive) in text."""
    tl = text.lower()
    return [kw for kw in config.WATCH_KEYWORDS if kw.lower() in tl]


def flag_alerts(diff: Snapshot) -> list[dict[str, Any]]:
    """Walk a computed diff and return a list of high-priority alert objects.

    Each alert has: source, kind, title, matched_keywords.
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

    # CC Crypto Catalog page changes (publications / news / communities)
    for page_key, page_diff in diff.get("cc_crypto", {}).get("pages", {}).items():
              for item in page_diff.get("added", []):
                            _add(f"CC Crypto: {page_key}", "publication", item.get("text", ""))

    # CC Crypto document header changes (new PDF version detected)
    for doc_name, change in diff.get("cc_crypto", {}).get("doc_headers", {}).items():
              if change.get("changed"):
                            _add("CC Crypto Doc", "updated", doc_name)

        # NIST page changes (news, fips, cmvp_mip, pqc, crypto_standards)
        for page_key, page_diff in diff.get("nist", {}).get("pages", {}).items():
                      for item in page_diff.get("added", []):
                                        _add(f"NIST: {page_key}", "publication", item.get("text", ""))

        # NIST document header changes (new PDF version detected)
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
        "added": added, "removed": removed,
        "sunset_changes": sunset_changes, "status_changes": status_changes,
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
    added   = [new_cisco[i] for i in set(new_cisco) - set(old_cisco)]
    removed = [old_cisco[i] for i in set(old_cisco) - set(new_cisco)]

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
        added   = [
            i for i in new_labs.get(lab, [])
            if i.get("id", i.get("title", "")) not in old_ids
        ]
        if added:
            result[lab] = added
    return result


# ── Master diff ───────────────────────────────────────────────────────────────

def compute_diff(old_snapshot: Snapshot, new_snapshot: Snapshot) -> Snapshot:
    """Compare two full snapshots, scan for keyword alerts, return diff."""
    check_schema_compat(old_snapshot, new_snapshot)

    old_n = old_snapshot.get("niap", {})
    new_n = new_snapshot.get("niap", {})
    old_c = old_snapshot.get("cc_portal", {})
    new_c = new_snapshot.get("cc_portal", {})
    old_l = old_snapshot.get("cctl_labs", {})
    new_l = new_snapshot.get("cctl_labs", {})
      old_cs = old_snapshot.get("csfc", {})
    new_cs = new_snapshot.get("csfc", {})
    old_cc = old_snapshot.get("cc_crypto", {})
      new_cc = new_snapshot.get("cc_crypto", {})
        old_ni = old_snapshot.get("nist", {})
        new_ni = new_snapshot.get("nist", {})

    diff: Snapshot = {
        "period_start": old_snapshot.get("collected_at", ""),
        "period_end":   new_snapshot.get("collected_at", ""),
        "niap": {
            "pps":         diff_niap_pps(old_n.get("pps", []),    new_n.get("pps", [])),
            "tds":         diff_niap_tds(old_n.get("tds", []),    new_n.get("tds", [])),
            "cisco_ndcpp": diff_niap_pcl_cisco(old_n.get("pcl", []), new_n.get("pcl", [])),
            "news":        diff_niap_news(old_n.get("news", []),  new_n.get("news", [])),
            "events":      diff_niap_events(old_n.get("events", []), new_n.get("events", [])),
            "cctls":       diff_niap_cctls(old_n.get("cctls", []), new_n.get("cctls", [])),
        },
        "cc_portal": {
            "news":     diff_cc_news(old_c.get("news", []),     new_c.get("news", [])),
            "pps":      diff_cc_pps(old_c.get("pps", []),       new_c.get("pps", [])),
            "products": diff_cc_products(old_c.get("products", []), new_c.get("products", [])),
        },
        "cctl_labs": diff_cctl_labs(old_l, new_l),
              "csfc":      diff_csfc(old_cs, new_cs),
              "cc_crypto":  diff_cc_crypto(old_cc, new_cc),
                  "nist":      diff_nist(old_ni, new_ni),
    }

    diff["alerts"] = flag_alerts(diff)

    td_new = len(diff["niap"]["tds"]["added"])
    pp_new = len(diff["niap"]["pps"]["added"])
    alerts = len(diff["alerts"])
    log.info(
        "[Diff] PPs new:%d TDs new:%d alerts:%d",
        pp_new, td_new, alerts,
    )
    return diff


# ── Weekly merge ──────────────────────────────────────────────────────────────

def merge_weekly_diffs(diffs: list[Snapshot]) -> Snapshot:
    """Merge a list of daily diffs into one weekly summary."""
    def merge_lists(*lists: list) -> list:
        seen: set[str] = set()
        merged: list   = []
        for lst in lists:
            for item in lst:
                k = str(item)[:120]
                if k not in seen:
                    seen.add(k)
                    merged.append(item)
        return merged

    if not diffs:
        return {}

    weekly = diffs[0]
    for d in diffs[1:]:
        for key in ("added", "removed", "sunset_changes", "status_changes"):
            if key in weekly["niap"]["pps"] and key in d["niap"]["pps"]:
                weekly["niap"]["pps"][key] = merge_lists(
                    weekly["niap"]["pps"][key], d["niap"]["pps"][key])

        for key in ("added", "removed"):
            if key in weekly["niap"]["tds"] and key in d["niap"]["tds"]:
                weekly["niap"]["tds"][key] = merge_lists(
                    weekly["niap"]["tds"][key], d["niap"]["tds"][key])

        for key in ("added", "removed", "newly_archived"):
            if key in weekly["niap"]["cisco_ndcpp"]:
                weekly["niap"]["cisco_ndcpp"][key] = merge_lists(
                    weekly["niap"]["cisco_ndcpp"].get(key, []),
                    d["niap"]["cisco_ndcpp"].get(key, []))

        weekly["niap"]["news"]["added"] = merge_lists(
            weekly["niap"]["news"]["added"], d["niap"]["news"]["added"])
        weekly["niap"]["events"]["added"] = merge_lists(
            weekly["niap"]["events"]["added"], d["niap"]["events"]["added"])

        weekly["cc_portal"]["news"]["added"] = merge_lists(
            weekly["cc_portal"]["news"]["added"], d["cc_portal"]["news"]["added"])
        weekly["cc_portal"]["pps"]["added"] = merge_lists(
            weekly["cc_portal"]["pps"]["added"], d["cc_portal"]["pps"]["added"])

        for lab, items in d.get("cctl_labs", {}).items():
            weekly["cctl_labs"][lab] = merge_lists(
                weekly["cctl_labs"].get(lab, []), items)

        weekly["alerts"] = merge_lists(
            weekly.get("alerts", []), d.get("alerts", []))

        # Merge CSfC feed items
        for feed_name, items in d.get("csfc", {}).get("feeds", {}).items():
                      if "csfc" not in weekly:
                                        weekly["csfc"] = {"feeds": {}, "pages": {}, "capability_packages": {}}
                                    weekly["csfc"]["feeds"][feed_name] = merge_lists(
                                                      weekly["csfc"]["feeds"].get(feed_name, []), items)
        # Merge CSfC APL page items
        for page_key, page_diff in d.get("csfc", {}).get("pages", {}).items():
                      if "csfc" not in weekly:
                                        weekly["csfc"] = {"feeds": {}, "pages": {}, "capability_packages": {}}
                                    if isinstance(page_diff, dict) and "added" in page_diff:
                                                      if page_key not in weekly["csfc"]["pages"]:
                                                                            weekly["csfc"]["pages"][page_key] = {"added": []}
                                                                        weekly["csfc"]["pages"][page_key]["added"] = merge_lists(
                                                                                              weekly["csfc"]["pages"][page_key]["added"],
                                                                                              page_diff["added"])
        # Merge CSfC CP header changes
        for cp_name, cp_data in d.get("csfc", {}).get("capability_packages", {}).items():
                      if "csfc" not in weekly:
                                        weekly["csfc"] = {"feeds": {}, "pages": {}, "capability_packages": {}}
            weekly["csfc"]["capability_packages"][cp_name] = cp_data

        # Merge CC Crypto page items
        for page_key, page_diff in d.get("cc_crypto", {}).get("pages", {}).items():
                      if "cc_crypto" not in weekly:
                                        weekly["cc_crypto"] = {"pages": {}, "doc_headers": {}}
                                    if isinstance(page_diff, dict) and "added" in page_diff:
                                                      if page_key not in weekly["cc_crypto"]["pages"]:
                                                                            weekly["cc_crypto"]["pages"][page_key] = {"added": []}
                                                                        weekly["cc_crypto"]["pages"][page_key]["added"] = merge_lists(
                                                                                              weekly["cc_crypto"]["pages"][page_key]["added"],
                                                                                              page_diff["added"])
        # Merge CC Crypto document header changes
        for doc_name, doc_data in d.get("cc_crypto", {}).get("doc_headers", {}).items():
                      if "cc_crypto" not in weekly:
                                        weekly["cc_crypto"] = {"pages": {}, "doc_headers": {}}
            weekly["cc_crypto"]["doc_headers"][doc_name] = doc_data

        # Merge NIST page items
        for page_key, page_diff in d.get("nist", {}).get("pages", {}).items():
                      if "nist" not in weekly:
                                        weekly["nist"] = {"pages": {}, "doc_headers": {}, "feeds": {}}
            if isinstance(page_diff, dict) and "added" in page_diff:
                              if page_key not in weekly["nist"]["pages"]:
                                                    weekly["nist"]["pages"][page_key] = {"added": []}
                weekly["nist"]["pages"][page_key]["added"] = merge_lists(
                                      weekly["nist"]["pages"][page_key]["added"],
                                      page_diff["added"])

        # Merge NIST document header changes
        for doc_name, doc_data in d.get("nist", {}).get("doc_headers", {}).items():
                      if "nist" not in weekly:
                                        weekly["nist"] = {"pages": {}, "doc_headers": {}, "feeds": {}}
            weekly["nist"]["doc_headers"][doc_name] = doc_data

        # Merge NIST feed items
        for feed_name, items in d.get("nist", {}).get("feeds", {}).items():
                      if "nist" not in weekly:
                                        weekly["nist"] = {"pages": {}, "doc_headers": {}, "feeds": {}}
            weekly["nist"]["feeds"][feed_name] = merge_lists(
                              weekly["nist"]["feeds"].get(feed_name, []),
                              items)

    return weekly

# ── CSfC diff ────────────────────────────────────────────────────────────────

def diff_csfc(old_csfc: Snapshot, new_csfc: Snapshot) -> Snapshot:
      """Diff two CSfC snapshots.

          Returns a dict with three sections:
                pages            — per-page text items added or removed
                      capability_packages — CP PDFs whose Last-Modified / ETag header changed
                            feeds            — new RSS / scraped items from NSA / CISA / DISA feeds
                                """
    result: Snapshot = {
              "pages": {},
              "capability_packages": {},
              "feeds": {},
    }

    # ── 1. Page text diffs ───────────────────────────────────────────────────
    old_pages = old_csfc.get("pages", {})
    new_pages = new_csfc.get("pages", {})
    for page_key in set(old_pages) | set(new_pages):
              old_items = old_pages.get(page_key, [])
        new_items = new_pages.get(page_key, [])
        old_texts = {i["text"][:120] for i in old_items}
        new_texts = {i["text"][:120] for i in new_items}
        added   = [i for i in new_items if i["text"][:120] not in old_texts]
        removed = [i for i in old_items if i["text"][:120] not in new_texts]
        if added or removed:
                      result["pages"][page_key] = {"added": added, "removed": removed}

    # ── 2. Capability Package header diffs ───────────────────────────────────
    old_cps = old_csfc.get("capability_package_headers", {})
    new_cps = new_csfc.get("capability_package_headers", {})
    for cp_name in set(old_cps) | set(new_cps):
              old_h = old_cps.get(cp_name, {})
        new_h = new_cps.get(cp_name, {})
        changed = (
                      old_h.get("last_modified") != new_h.get("last_modified")
                      or old_h.get("etag") != new_h.get("etag")
                      or old_h.get("content_length") != new_h.get("content_length")
        )
        if changed and (old_h or new_h):
                      result["capability_packages"][cp_name] = {
                                        "changed": True,
                                        "old_last_modified": old_h.get("last_modified", ""),
                                        "new_last_modified": new_h.get("last_modified", ""),
                                        "old_etag":          old_h.get("etag", ""),
                                        "new_etag":          new_h.get("etag", ""),
                                        "old_content_length":old_h.get("content_length", ""),
                                        "new_content_length":new_h.get("content_length", ""),
                                        "url":               new_h.get("url", old_h.get("url", "")),
                      }

    # ── 3. Feed item diffs ───────────────────────────────────────────────────
    old_feeds = old_csfc.get("feeds", {})
    new_feeds = new_csfc.get("feeds", {})
    for feed_name in set(old_feeds) | set(new_feeds):
              old_items = old_feeds.get(feed_name, [])
        new_items = new_feeds.get(feed_name, [])
        old_ids = {i.get("id", i.get("title", i.get("link", ""))) for i in old_items}
        added = [
                      i for i in new_items
                      if i.get("id", i.get("title", i.get("link", ""))) not in old_ids
        ]
        if added:
                      result["feeds"][feed_name] = added

    cp_changes = len(result["capability_packages"])
    page_changes = sum(len(v.get("added", [])) for v in result["pages"].values())
    feed_new = sum(len(v) for v in result["feeds"].values())
    log.info(
              "[CSfC Diff] page-items-added:%d CP-changes:%d feed-new:%d",
              page_changes, cp_changes, feed_new,
    )
    return result

# ── CC Crypto Catalog diff ───────────────────────────────────────────────────

def diff_cc_crypto(old_cc: Snapshot, new_cc: Snapshot) -> Snapshot:
      """Diff two CC Crypto Catalog snapshots.

          Returns a dict with two sections:
                pages       — per-page text items added or removed (publications, news,
                                    communities)
                                          doc_headers — documents whose Last-Modified / ETag / Content-Length
                                                              changed, indicating a new version of the PDF was published
                                                                  """
    result: Snapshot = {
              "pages": {},
              "doc_headers": {},
    }

    # ── 1. Page text diffs ───────────────────────────────────────────────────
    old_pages = old_cc.get("pages", {})
    new_pages = new_cc.get("pages", {})
    for page_key in set(old_pages) | set(new_pages):
              old_items = old_pages.get(page_key, [])
        new_items = new_pages.get(page_key, [])
        old_texts = {i["text"][:120] for i in old_items}
        new_texts = {i["text"][:120] for i in new_items}
        added   = [i for i in new_items if i["text"][:120] not in old_texts]
        removed = [i for i in old_items if i["text"][:120] not in new_texts]
        if added or removed:
                      result["pages"][page_key] = {"added": added, "removed": removed}

    # ── 2. Document header diffs ─────────────────────────────────────────────
    old_docs = old_cc.get("doc_headers", {})
    new_docs = new_cc.get("doc_headers", {})
    for doc_name in set(old_docs) | set(new_docs):
              old_h = old_docs.get(doc_name, {})
        new_h = new_docs.get(doc_name, {})
        changed = (
                      old_h.get("last_modified") != new_h.get("last_modified")
                      or old_h.get("etag") != new_h.get("etag")
                      or old_h.get("content_length") != new_h.get("content_length")
        )
        if changed and (old_h or new_h):
                      result["doc_headers"][doc_name] = {
                                        "changed": True,
                                        "old_last_modified": old_h.get("last_modified", ""),
                                        "new_last_modified": new_h.get("last_modified", ""),
                                        "old_etag":          old_h.get("etag", ""),
                                        "new_etag":          new_h.get("etag", ""),
                                        "old_content_length":old_h.get("content_length", ""),
                                        "new_content_length":new_h.get("content_length", ""),
                                        "url":               new_h.get("url", old_h.get("url", "")),
                      }

    doc_changes  = len(result["doc_headers"])
    page_changes = sum(len(v.get("added", [])) for v in result["pages"].values())
    log.info(
              "[CC Crypto Diff] page-items-added:%d doc-changes:%d",
              page_changes, doc_changes,
    )
    return result


# ── NIST CSRC diff ────────────────────────────────────────────────────────────

def diff_nist(old_nist: Snapshot, new_nist: Snapshot) -> Snapshot:
      """Diff two NIST CSRC snapshots.
          Returns a dict with three sections:
                  pages        — per-page text items added or removed
                                         (news, fips, cmvp_mip, pqc, crypto_standards)
                                                 doc_headers  — NIST PDFs whose Last-Modified / ETag / Content-Length
                                                                        changed, indicating a new/revised document
                                                                                feeds        — new RSS / blog items from NIST feeds
                                                                                    """
    result: Snapshot = {
              "pages": {},
              "doc_headers": {},
              "feeds": {},
    }

    # ── 1. Page text diffs ──────────────────────────────────────────────────
    old_pages = old_nist.get("pages", {})
    new_pages = new_nist.get("pages", {})
    for page_key in set(old_pages) | set(new_pages):
              old_items = old_pages.get(page_key, [])
        new_items = new_pages.get(page_key, [])
        old_texts = {i["text"][:120] for i in old_items}
        new_texts = {i["text"][:120] for i in new_items}
        added   = [i for i in new_items if i["text"][:120] not in old_texts]
        removed = [i for i in old_items if i["text"][:120] not in new_texts]
        if added or removed:
                      result["pages"][page_key] = {"added": added, "removed": removed}

    # ── 2. Document header diffs ────────────────────────────────────────────
    old_docs = old_nist.get("doc_headers", {})
    new_docs = new_nist.get("doc_headers", {})
    for doc_name in set(old_docs) | set(new_docs):
              old_h = old_docs.get(doc_name, {})
        new_h = new_docs.get(doc_name, {})
        changed = (
                      old_h.get("last_modified") != new_h.get("last_modified")
                      or old_h.get("etag")         != new_h.get("etag")
                      or old_h.get("content_length") != new_h.get("content_length")
        )
        if changed and (old_h or new_h):
                      result["doc_headers"][doc_name] = {
                                        "changed": True,
                                        "old_last_modified":  old_h.get("last_modified", ""),
                                        "new_last_modified":  new_h.get("last_modified", ""),
                                        "old_etag":           old_h.get("etag", ""),
                                        "new_etag":           new_h.get("etag", ""),
                                        "old_content_length": old_h.get("content_length", ""),
                                        "new_content_length": new_h.get("content_length", ""),
                                        "url": new_h.get("url", old_h.get("url", "")),
                      }

    # ── 3. Feed item diffs ──────────────────────────────────────────────────
    old_feeds = old_nist.get("feeds", {})
    new_feeds = new_nist.get("feeds", {})
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
        if added:
                      result["feeds"][feed_name] = added

    doc_changes  = len(result["doc_headers"])
    page_changes = sum(len(v.get("added", [])) for v in result["pages"].values())
    feed_new     = sum(len(v) for v in result["feeds"].values())
    log.info(
              "[NIST Diff] page-items-added:%d doc-changes:%d feed-new:%d",
              page_changes, doc_changes, feed_new,
    )
    return result
