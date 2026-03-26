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

    return weekly
