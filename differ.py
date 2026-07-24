"""
differ.py — Computes diffs between two CC Pulse snapshots.

Compares old and new snapshot dicts, returning a diff dict with
per-source change lists and keyword alerts.
"""

from __future__ import annotations

import copy
import json
import logging
import re
from typing import Any

import config

log = logging.getLogger(__name__)

# -- Types ---------------------------------------------------------------------
Snapshot = dict[str, Any]
Records = list[dict[str, Any]]

# -- Internal helpers ----------------------------------------------------------

def _add(alerts: list, source: str, kind: str, title: str, *,
         url: str = "", detail: str = "", keywords: list | None = None, tab: str = "us") -> None:
    """Append a structured alert entry."""
    alerts.append({
        "source": source,
        "kind": kind,
        "title": title,
        "url": url,
        "detail": detail,
        "matched_keywords": keywords or [],  # fix #22: was "keywords"
        "tab": tab,
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


def _record_fingerprint(record: dict) -> str:
    """Return a deterministic representation excluding derived diff fields."""
    clean = {
        key: value
        for key, value in record.items()
        if not key.startswith("_")
    }
    return json.dumps(clean, sort_keys=True, default=str, separators=(",", ":"))


def _diff_revision_records(
    old_records: Records,
    new_records: Records,
    identity,
) -> dict[str, list[dict]]:
    """Diff API records by identity and detect content revisions/removals."""
    old_map = {
        str(identity(record)): record
        for record in old_records
        if identity(record) not in (None, "")
    }
    new_map = {
        str(identity(record)): record
        for record in new_records
        if identity(record) not in (None, "")
    }
    old_ids = set(old_map)
    new_ids = set(new_map)
    added = [copy.deepcopy(new_map[key]) for key in new_ids - old_ids]
    removed = [copy.deepcopy(old_map[key]) for key in old_ids - new_ids]
    revised = []
    deactivated = []
    reactivated = []
    for key in old_ids & new_ids:
        old_item = old_map[key]
        new_item = new_map[key]
        old_active = old_item.get("active")
        new_active = new_item.get("active")
        if old_active is True and new_active is False:
            changed = copy.deepcopy(new_item)
            changed["_old_active"] = True
            deactivated.append(changed)
        elif old_active is False and new_active is True:
            changed = copy.deepcopy(new_item)
            changed["_old_active"] = False
            reactivated.append(changed)
        elif _record_fingerprint(old_item) != _record_fingerprint(new_item):
            changed = copy.deepcopy(new_item)
            changed["_old_moddate"] = old_item.get("moddate", "")
            revised.append(changed)
    return {
        "added": added,
        "revised": revised,
        "deactivated": deactivated,
        "reactivated": reactivated,
        "removed": removed,
    }

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
            title = (
                item.get("title", "") or item.get("policy_title", "")
                or item.get("name", "")
            )
            detail = item.get("detail", "") or item.get("description", "")
            url = (
                item.get(url_key, "")
                or item.get("url", "")
                or item.get("link", "")
                or item.get("href", "")
            )
            hits = _matches(title + " " + detail)
            if hits:
                _add(alerts, source, item.get("kind", "alert"), title,
                     url=url, detail=detail, keywords=hits, tab=tab)

    def _add_text(source: str, kind: str, text: str,
                  url: str = "", detail: str = "", tab: str = "us",
                  additional_keywords: list[str] | None = None) -> None:
        """Scan a raw scraped text blob (lower-signal) against _matches only."""
        hits = _matches(text)
        for keyword in additional_keywords or []:
            if keyword.lower() in text.lower() and keyword not in hits:
                hits.append(keyword)
        if hits:
            truncated = text[:120].rstrip() + ("…" if len(text) > 120 else "")
            _add(alerts, source, kind, truncated, url=url, detail=detail, keywords=hits, tab=tab)
    # NIAP PCL (Cisco NDcPP certs)
    for item in diff.get("niap", {}).get("cisco_ndcpp", {}).get("added", []):
        title = item.get("product_name", item.get("title", item.get("name", "")))
        hits = _matches(title)
        if hits:
            _add(alerts, "NIAP PCL", "new_cert", title,
                 url=item.get("url", ""), keywords=hits, tab="us")

    # NIAP Protection Profiles
    _scan_items("NIAP PP", diff.get("niap", {}).get("pps", {}).get("added", []), tab="us")
    _scan_items("NIAP PP", diff.get("niap", {}).get("pps", {}).get("sunset_changes", []))

    # NIAP Technical Decisions
    _TD_BASE = "https://www.niap-ccevs.org/technical-decisions"
    for td in diff.get("niap", {}).get("tds", {}).get("added", []):
        title = td.get("title", "") or td.get("identifier", "")
        detail = td.get("identifier", "")
        url = f"{_TD_BASE}/{detail}" if detail else _TD_BASE
        hits = _matches(title + " " + detail)
        if hits:
            _add(alerts, "NIAP TD", "new", title,
                 url=url, detail=detail, keywords=hits, tab="us")

    # NIAP announcements and policy letters
    for kind in ("added", "revised", "reactivated"):
        _scan_items(
            "NIAP News",
            diff.get("niap", {}).get("news", {}).get(kind, []),
            tab="us",
        )
        _scan_items(
            "NIAP Events",
            diff.get("niap", {}).get("events", {}).get(kind, []),
            tab="us",
        )
        _scan_items(
            "NIAP Policies",
            diff.get("niap", {}).get("policies", {}).get(kind, []),
            tab="us",
        )

    # CC Portal
    _scan_items("CC Portal", diff.get("cc_portal", {}).get("news", {}).get("added", []), tab="intl")
    _scan_items("CC Portal", diff.get("cc_portal", {}).get("pps", {}).get("added", []), tab="intl")

    # CCTL Labs -- fix #22: diff_cctl_labs returns {lab_name: [items]}, not {"added": [...]}
    for lab_items in diff.get("cctl_labs", {}).values():
        _scan_items("CCTL Labs", lab_items, tab="intl")
    # CSfC Component Selections -- href/ver token change means NSA updated the document
    for sel_name, change in diff.get("csfc", {}).get("selection_links", {}).items():
        if change.get("changed"):
            old_href = change.get("old_href", "")
            new_href = change.get("new_href", "")
            detail = (
                "NSA added a new component selection document for this role"
                if not old_href
                else "NSA removed the component selection document for this role"
                if not new_href
                else "NSA updated the component selection document for this role"
            )
            pdf_href = new_href or old_href
            # Every Selection-document change is high-value CSfC content; it
            # should not depend on the role name matching a watch keyword.
            _add(
                alerts,
                "CSfC Component Selections",
                "updated",
                sel_name,
                url=pdf_href or config.CSFC_PRODUCT_LIST_URL,
                detail=detail,
                keywords=["CSfC"],
                tab="us",
            )

    # CSfC APL page changes — new items on the Components List page
    _csfc_page_urls = {
        "apl":           config.CSFC_PRODUCT_LIST_URL,
        "home":          config.CSFC_BASE + "/Resources/Commercial-Solutions-for-Classified-Program/",
        "cap_packages":  config.CSFC_BASE + "/Resources/Commercial-Solutions-for-Classified-Program/Capability-Packages/",
        "announcements": config.CSFC_BASE + "/Resources/Commercial-Solutions-for-Classified-Program/Announcements/",
    }
    for page_key, page_diff in diff.get("csfc", {}).get("pages", {}).items():
        for item in page_diff.get("added", []):
            vendor_keywords = config.CISCO_VENDOR_KEYWORDS if page_key == "apl" else []
            _add_text(
                "CSfC APL" if page_key == "apl" else f"CSfC: {page_key}",
                "new_cert" if page_key == "apl" else "new",
                item.get("text", ""),
                url=(_csfc_page_urls.get(page_key, config.CSFC_PRODUCT_LIST_URL) if page_key == "apl" else item.get("href") or item.get("link") or _csfc_page_urls.get(page_key, config.CSFC_PRODUCT_LIST_URL)),
                detail=f"New item on CSfC {page_key.replace('_', ' ')} page",
                tab="us",
                additional_keywords=vendor_keywords,
            )
        for item in page_diff.get("removed", []):
            vendor_keywords = config.CISCO_VENDOR_KEYWORDS if page_key == "apl" else []
            _add_text(
                "CSfC APL" if page_key == "apl" else f"CSfC: {page_key}",
                "removed",
                item.get("text", ""),
                url=(_csfc_page_urls.get(page_key, config.CSFC_PRODUCT_LIST_URL) if page_key == "apl" else item.get("href") or item.get("link") or _csfc_page_urls.get(page_key, config.CSFC_PRODUCT_LIST_URL)),
                detail=f"Item removed from CSfC {page_key.replace('_', ' ')} page",
                tab="us",
                additional_keywords=vendor_keywords,
            )

    # CC Crypto Catalog page changes — fire unconditionally for publications (fix #27)
    # The publications page is curated CC crypto content; all new items are relevant.
    _cc_crypto_urls = {
        "publications": "https://www.commoncriteriaportal.org/cc/index.cfm",
        "news": "https://www.commoncriteriaportal.org/news/index.cfm",
        "communities": "https://www.commoncriteriaportal.org/communities/index.cfm",
    }
    _cc_crypto_unconditional = {"publications", "news"}
    for page_key, page_diff in diff.get("cc_crypto", {}).get("pages", {}).items():
        for item in page_diff.get("added", []):
            page_url = item.get("href") or _cc_crypto_urls.get(page_key, "https://www.commoncriteriaportal.org/cc/index.cfm")
            detail = f"New item on CC Crypto {page_key} page"
            if page_key in _cc_crypto_unconditional:
                title = item.get("text", "")[:120]
                _add(alerts, f"CC Crypto: {page_key}", "publication", title,
                     url=page_url, detail=detail, tab="us")
            else:
                _add_text(f"CC Crypto: {page_key}", "publication",
                          item.get("text", ""),
                          url=page_url, detail=detail, tab="us")
    # NATO NIAPCL — Cisco additions are alert-worthy by construction (the
    # list is already Cisco-filtered), so they use unconditional _add with an
    # explicit "cisco" keyword rather than the keyword-gated _add_text. The
    # old _add_text gating silently dropped every one of these: "cisco" was
    # only in the never-referenced BODY_WATCH_KEYWORDS list, so the scan
    # found no hits and the alert never fired.
    nato = diff.get("nato", {})
    nato_baseline = nato.get("baseline_reset", False)
    nato_cisco_urls = {
        (item.get("link") or "").strip()
        for item in nato.get("cisco_added", [])
    } - {""}
    if not nato_baseline:
        for item in nato.get("cisco_added", []):
            _add(alerts, "NATO NIAPCL", "new_cert",
                 item.get("name", "") or item.get("raw_text", "")[:80],
                 url=item.get("link") or config.NATO_NIAPCL_URL,
                 detail=f"New Cisco product on NATO NIAPCL · {item.get('manufacturer', '')}",
                 keywords=["cisco"], tab="intl")

    # NATO NIAPCL page changes (skip items already alerted as Cisco adds —
    # the monitored NIAPCL pages are Cisco-filtered searches, so page items
    # and cisco_added overlap)
    for page_key, page_diff in nato.get("pages", {}).items():
        for item in page_diff.get("added", []):
            if (item.get("link") or "").strip() in nato_cisco_urls:
                continue
            _add_text(f"NATO NIAPCL: {page_key}", "new",
                      item.get("text", "") or item.get("raw_text", ""),
                      url=item.get("link") or config.NATO_NIAPCL_URL,
                      detail=f"New item on NATO NIAPCL {page_key} page",
                      tab="intl")

    # EUCC requirements page changes
    eucc = diff.get("eucc", {})
    eucc_baseline = eucc.get("baseline_reset", False)
    for item in eucc.get("pages", {}).get("requirements", {}).get("added", []):
        _add_text("EUCC Requirements", "updated",
                  item.get("text", ""),
                  url=item.get("href") or config.EUCC_REQUIREMENTS_URL,
                  detail="New item on EUCC requirements / scheme page",
                  tab="eu")

    # EUCC Cisco-specific certificates — unconditional, same reasoning as
    # NATO above. Suppressed on baseline resets (source format change makes
    # old certificates re-detect as new; see diff_eucc).
    eucc_cisco_urls = {
        (item.get("href") or "").strip()
        for item in eucc.get("cisco_added", [])
    } - {""}
    if not eucc_baseline:
        for item in eucc.get("cisco_added", []):
            _add(alerts, "EUCC Certificates", "new_cert",
                 item.get("name", "") or item.get("text", "")[:80],
                 url=item.get("href") or config.EUCC_CERTIFICATES_URL,
                 detail="New Cisco EUCC certified product",
                 keywords=["cisco", "EUCC"], tab="eu")

        # EUCC certificate additions (general, keyword-gated; skip Cisco
        # items already alerted above)
        for item in eucc.get("pages", {}).get("certificates", {}).get("added", []):
            if (item.get("href") or "").strip() in eucc_cisco_urls:
                continue
            _add_text("EUCC Certificates", "new_cert",
                      item.get("text", "") or item.get("name", ""),
                      url=item.get("href") or config.EUCC_CERTIFICATES_URL,
                      detail="New EUCC certified product",
                      tab="eu")

    # ND-iTC — NIT RFIs and Allowed-With lists. Every change alerts,
    # unconditionally, tagged tier 1 via the NDcPP keyword. "NIT RFI" naming
    # keeps ND-iTC Technical Decisions distinct from NIAP TDs.
    nd = diff.get("nd_itc", {})
    if not nd.get("baseline_reset") and not nd.get("collection_failure"):
        rfis = nd.get("nit_rfis", {})
        for item in rfis.get("added", []):
            _add(alerts, "ND-iTC NIT RFI", "new",
                 f"{item.get('rfi_id', '')}: {item.get('title', '')}".strip(": "),
                 url=item.get("href") or config.ND_ITC_TD_URL,
                 detail=(f"New NIT RFI · Impact: {item.get('impact') or 'N/A'}"
                         f" · Status: {item.get('status', '')}"),
                 keywords=["NDcPP"], tab="intl")
        for item in rfis.get("status_changes", []):
            _add(alerts, "ND-iTC NIT RFI", "updated",
                 f"{item.get('rfi_id', '')}: {item.get('title', '')}".strip(": "),
                 url=item.get("href") or config.ND_ITC_TD_URL,
                 detail=(f"NIT RFI status: {item.get('old_status', '?')}"
                         f" → {item.get('new_status', '?')}"),
                 keywords=["NDcPP"], tab="intl")
        for item in rfis.get("revised", []):
            _add(alerts, "ND-iTC NIT RFI", "updated",
                 f"{item.get('rfi_id', '')}: {item.get('title', '')}".strip(": "),
                 url=item.get("href") or config.ND_ITC_TD_URL,
                 detail="NIT RFI revised (title, reference, impact, or PDF changed)",
                 keywords=["NDcPP"], tab="intl")
        for item in rfis.get("newly_archived", []):
            _add(alerts, "ND-iTC NIT RFI", "archived",
                 f"{item.get('rfi_id', '')}: {item.get('title', '')}".strip(": "),
                 url=item.get("href") or config.ND_ITC_TD_URL,
                 detail="NIT RFI moved to the archived Technical Decisions list",
                 keywords=["NDcPP"], tab="intl")

        awl = nd.get("awl", {})

        def _awl_url(item: dict) -> str:
            return config.ND_ITC_AWL_URLS.get(
                item.get("list", ""), config.ND_ITC_BASE)

        for item in awl.get("added", []):
            _add(alerts, "ND-iTC Allowed-With", "new",
                 item.get("object_id", ""),
                 url=_awl_url(item),
                 detail=(f"Added to the {item.get('section', '')} allowed-with "
                         f"list · version {item.get('object_version', '')}"),
                 keywords=["NDcPP"], tab="intl")
        for item in awl.get("removed", []):
            _add(alerts, "ND-iTC Allowed-With", "removed",
                 item.get("object_id", ""),
                 url=_awl_url(item),
                 detail=f"Removed from the {item.get('section', '')} allowed-with list",
                 keywords=["NDcPP"], tab="intl")
        for item in awl.get("version_changes", []):
            _add(alerts, "ND-iTC Allowed-With", "updated",
                 item.get("object_id", ""),
                 url=_awl_url(item),
                 detail=(f"{item.get('section', '')} allowed-with entry version: "
                         f"{item.get('old_version', '?')} → {item.get('new_version', '?')}"),
                 keywords=["NDcPP"], tab="intl")
        for item in awl.get("list_updates", []):
            _add(alerts, "ND-iTC Allowed-With", "updated",
                 f"Allowed-with list document updated ({item.get('list', '')})",
                 url=_awl_url(item),
                 detail=(f"List version: {item.get('old_awl_version', '?')}"
                         f" → {item.get('awl_version', '?')}"
                         f" ({item.get('awl_date', '')})"),
                 keywords=["NDcPP"], tab="intl")

    if alerts:
        log.warning("[Alerts] %d keyword match(es) found!", len(alerts))
    return alerts
# -- NIAP diffs ----------------------------------------------------------------
def diff_niap_pps(old_pps: Records, new_pps: Records) -> dict[str, Any]:
    old_map = byid(old_pps, "pp_id")
    new_map = byid(new_pps, "pp_id")
    old_ids = set(old_map)
    new_ids = set(new_map)

    added = [new_map[i] for i in new_ids - old_ids]
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
        "added": added,
        "removed": removed,
        "sunset_changes": sunset_changes,
        "status_changes": status_changes,
    }

def diff_niap_tds(old_tds: Records, new_tds: Records) -> dict[str, Any]:
    old_map = byid(old_tds, "td_id")
    new_map = byid(new_tds, "td_id")
    old_ids = set(old_map)
    new_ids = set(new_map)
    added = [new_map[i] for i in new_ids - old_ids]
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
    """Diff the full NIAP PCL across all tech types — certifications only.

    A PCL listing is not a certification. Products appearing with status
    "In Progress"/"In Review" belong to the in_evaluation diff and were
    previously double-reported here as "New Certifications" on the
    dashboard. "added" therefore means newly certified: brand-new to the
    list with status Certified, or an existing listing (typically
    in-evaluation) transitioning to Certified. "removed" likewise excludes
    in-evaluation listings leaving the list — those are reported as
    "Left Evaluation" by diff_niap_in_evaluation.
    """
    old_all = {str(p["product_id"]): p for p in old_pcl}
    new_all = {str(p["product_id"]): p for p in new_pcl}
    brand_new = [
        new_all[i] for i in set(new_all) - set(old_all)
        if new_all[i].get("status_sort") == "Certified"
    ]
    newly_certified = [
        new_all[pid] for pid in set(old_all) & set(new_all)
        if (old_all[pid].get("status_sort") != "Certified"
            and new_all[pid].get("status_sort") == "Certified")
    ]
    added = brand_new + newly_certified
    removed = [
        old_all[i] for i in set(old_all) - set(new_all)
        if old_all[i].get("status_sort") not in ("In Progress", "In Review")
    ]
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
    added = [new_ie[i] for i in set(new_ie) - set(old_ie)]
    removed = [old_ie[i] for i in set(old_ie) - set(new_ie)]
    return {"added": added, "removed": removed, "current_count": len(new_ie)}

def diff_niap_news(old_news: Records, new_news: Records) -> dict[str, Any]:
    result = _diff_revision_records(old_news, new_news, lambda item: item.get("id"))
    for kind in ("added", "revised", "deactivated", "reactivated", "removed"):
        for item in result[kind]:
            item["_category"] = categorize_news(item.get("title", ""))
            item["_change_kind"] = kind
    return result

def diff_niap_events(old_events: Records, new_events: Records) -> dict[str, Any]:
    result = _diff_revision_records(old_events, new_events, lambda item: item.get("id"))
    for kind, items in result.items():
        for item in items:
            item["_change_kind"] = kind
    return result


def _policy_identity(item: dict) -> str | int | None:
    policy_number = item.get("policy_id") or item.get("policy_num")
    if policy_number in (None, ""):
        return None
    return f"{policy_number}|{'archived' if item.get('archived') else 'active'}"


def diff_niap_policies(old_policies: Records, new_policies: Records) -> dict[str, Any]:
    """Detect new, revised, archived, reactivated, and removed policies."""
    result = _diff_revision_records(old_policies, new_policies, _policy_identity)
    revised = list(result.pop("revised"))
    archived = list(result.pop("deactivated"))
    reactivated = list(result.pop("reactivated"))

    # Public policy records have no stable ID and active/archived versions can
    # share a policy number. Pair status-qualified additions/removals so a move
    # between the two lists is reported as a transition, not two unrelated
    # changes.
    def policy_num(item: dict) -> str:
        return str(item.get("policy_id") or item.get("policy_num") or "")

    removed_active = {
        policy_num(item): item for item in result["removed"]
        if not item.get("archived")
    }
    added_archived = {
        policy_num(item): item for item in result["added"]
        if item.get("archived")
    }
    for number in set(removed_active) & set(added_archived):
        archived.append(added_archived[number])
        result["removed"].remove(removed_active[number])
        result["added"].remove(added_archived[number])

    # If an older archived version already existed, the archived row is a
    # revision rather than an addition. Pair that with the disappearing active
    # row as the same archive transition.
    removed_active = {
        policy_num(item): item for item in result["removed"]
        if not item.get("archived")
    }
    revised_archived = {
        policy_num(item): item for item in revised
        if item.get("archived")
    }
    for number in set(removed_active) & set(revised_archived):
        archived.append(revised_archived[number])
        result["removed"].remove(removed_active[number])
        revised.remove(revised_archived[number])

    removed_archived = {
        policy_num(item): item for item in result["removed"]
        if item.get("archived")
    }
    added_active = {
        policy_num(item): item for item in result["added"]
        if not item.get("archived")
    }
    for number in set(removed_archived) & set(added_active):
        reactivated.append(added_active[number])
        result["removed"].remove(removed_archived[number])
        result["added"].remove(added_active[number])

    removed_archived = {
        policy_num(item): item for item in result["removed"]
        if item.get("archived")
    }
    revised_active = {
        policy_num(item): item for item in revised
        if not item.get("archived")
    }
    for number in set(removed_archived) & set(revised_active):
        reactivated.append(revised_active[number])
        result["removed"].remove(removed_archived[number])
        revised.remove(revised_active[number])
    for kind, items in {
        "added": result["added"],
        "revised": revised,
        "archived": archived,
        "reactivated": reactivated,
        "removed": result["removed"],
    }.items():
        for item in items:
            item["_change_kind"] = kind
    return {
        "added": result["added"],
        "revised": revised,
        "archived": archived,
        "reactivated": reactivated,
        "removed": result["removed"],
    }
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
    """Diff two {name: header_dict} mappings using _headers_changed()."""
    result = {}
    for doc_name in set(old_docs) | set(new_docs):
        old_h = old_docs.get(doc_name, {})
        new_h = new_docs.get(doc_name, {})
        if _headers_changed(old_h, new_h):
            result[doc_name] = {
                "changed": True,
                "old_last_modified": old_h.get("last_modified", ""),
                "new_last_modified": new_h.get("last_modified", ""),
                "old_etag": old_h.get("etag", ""),
                "new_etag": new_h.get("etag", ""),
                "old_content_length":old_h.get("content_length", ""),
                "new_content_length":new_h.get("content_length", ""),
                "old_partial_hash": old_h.get("partial_hash", ""),
                "new_partial_hash": new_h.get("partial_hash", ""),
                "url": new_h.get("url", old_h.get("url", "")),
            }
    return result

def _diff_selection_links(old_links: dict, new_links: dict) -> dict:
    """Diff two {category_heading: full_href} dicts.

    Detects added entries, removed entries, and href changes (including DNN
    ?ver= token changes which signal that the document was updated).
    Returns a dict keyed by heading only when there is a change.
    """
    result: dict = {}
    all_headings = set(old_links) | set(new_links)
    for heading in all_headings:
        old_href = old_links.get(heading, "")
        new_href = new_links.get(heading, "")
        if old_href != new_href:
            result[heading] = {
                "changed": True,
                "old_href": old_href,
                "new_href": new_href,
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
        added = [i for i in new_items if i.get("text", "")[:120] not in old_texts]
        removed = [i for i in old_items if i.get("text", "")[:120] not in new_texts]
        if added or removed:
            result[page_key] = {"added": added, "removed": removed}
    return result

# -- Generic feed diff helper ---------------------------------------------------
def _diff_feeds(old_feeds: dict, new_feeds: dict, categorize: bool = False) -> dict:
    """Diff two {feed_name: [items]} dicts by id/title/link key."""
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

    # Diff Cisco products specifically (keyed on product URL, fix: raw_text
    # keys churned on NIAPCL display-format changes)
    old_cisco = {_record_key(p, url_field="link", text_field="raw_text"): p
                 for p in old_nato.get("cisco_products", [])}
    new_cisco = {_record_key(p, url_field="link", text_field="raw_text"): p
                 for p in new_nato.get("cisco_products", [])}
    cisco_added = [new_cisco[k] for k in set(new_cisco) - set(old_cisco)]
    cisco_removed = [old_cisco[k] for k in set(old_cisco) - set(new_cisco)]

    baseline_reset = _is_baseline_reset(
        len(old_cisco), len(cisco_added), len(new_cisco),
    )
    if baseline_reset:
        log.warning(
            "[NATO Diff] %d of %d Cisco products re-detected as new — "
            "treating as baseline reset; notifications suppressed.",
            len(cisco_added), len(new_cisco),
        )

    page_changes = sum(len(v.get("added", [])) for v in pages.values())
    log.info("[NATO Diff] page-items-added:%d cisco-added:%d cisco-removed:%d baseline-reset:%s",
             page_changes, len(cisco_added), len(cisco_removed), baseline_reset)
    return {
        "pages": pages,
        "cisco_added": cisco_added,
        "cisco_removed": cisco_removed,
        "baseline_reset": baseline_reset,
    }

# -- EUCC / ENISA diff --------------------------------------------------------
def _record_key(record: dict, *, url_field: str, text_field: str) -> str:
    """Stable identity for a scraped record: URL when present, else text prefix.

    Display text embeds dates and descriptions, so keying on it re-detects the
    entire list whenever the source reformats (ENISA's card-title change on
    2026-07-09 made 45 old certificates look 'new'). The record's own URL is
    stable across cosmetic changes; text is kept only as a fallback for
    records the parser couldn't find a link for.
    """
    return (record.get(url_field) or "").strip() or record.get(text_field, "")[:80]


def _is_baseline_reset(old_count: int, added_count: int, new_count: int,
                       min_items: int = 5, frac: float = 0.8) -> bool:
    """True when a diff looks like a re-keying/format change, not real news.

    Heuristic: a previously non-trivial list where most of today's items
    register as 'new' means the source (or our keying) changed shape — a new
    baseline. Callers keep the diff data for the dashboard but suppress
    notifications.
    """
    return (
        old_count >= min_items
        and added_count >= min_items
        and new_count > 0
        and added_count >= frac * new_count
    )


def diff_eucc(old_eucc: Snapshot, new_eucc: Snapshot) -> Snapshot:
    """Diff two EUCC / ENISA snapshots."""
    old_pages = old_eucc.get("pages", {})
    new_pages = new_eucc.get("pages", {})

    pages = _diff_pages(old_pages, new_pages)

    # Diff Cisco-specific certificates (keyed on certificate URL, fix: text
    # keys churned on ENISA display-format changes)
    old_cisco = {_record_key(c, url_field="href", text_field="text"): c
                 for c in old_eucc.get("cisco_certs", [])}
    new_cisco = {_record_key(c, url_field="href", text_field="text"): c
                 for c in new_eucc.get("cisco_certs", [])}
    cisco_added = [new_cisco[k] for k in set(new_cisco) - set(old_cisco)]
    cisco_removed = [old_cisco[k] for k in set(old_cisco) - set(new_cisco)]

    # Baseline detection: if most of the certificates page re-registered as
    # new, this is a format change / re-key, not a wave of certifications.
    old_cert_page = old_pages.get("certificates", [])
    new_cert_page = new_pages.get("certificates", [])
    cert_page_added = len(pages.get("certificates", {}).get("added", []))
    baseline_reset = _is_baseline_reset(
        len(old_cert_page), cert_page_added, len(new_cert_page),
    )
    if baseline_reset:
        log.warning(
            "[EUCC Diff] %d of %d certificate-page items re-detected as new — "
            "treating as baseline reset; notifications suppressed.",
            cert_page_added, len(new_cert_page),
        )

    req_changes = len(pages.get("requirements", {}).get("added", []))
    log.info("[EUCC Diff] req-changes:%d cert-additions:%d cisco-added:%d baseline-reset:%s",
             req_changes, cert_page_added, len(cisco_added), baseline_reset)
    return {
        "pages": pages,
        "cisco_added": cisco_added,
        "cisco_removed": cisco_removed,
        "baseline_reset": baseline_reset,
    }
# -- ND-iTC diff ---------------------------------------------------------------
_EMPTY_ND_ITC_DIFF: dict = {
    "nit_rfis": {"added": [], "status_changes": [], "revised": [],
                 "newly_archived": []},
    "awl": {"added": [], "removed": [], "version_changes": [],
            "list_updates": []},
    "baseline_reset": False,
    "collection_failure": False,
}


def diff_nd_itc(old_nd: Snapshot, new_nd: Snapshot) -> Snapshot:
    """Diff two ND-iTC snapshots: NIT RFIs and Allowed-With lists.

    The ND-iTC's Technical Decisions are called NIT RFIs throughout to
    distinguish them from NIAP TDs. First sight of the source (no prior
    nd_itc data in the old snapshot) establishes a baseline silently —
    30 existing RFIs must not fire 30 "new RFI" notifications.
    """
    import copy as _copy
    empty = _copy.deepcopy(_EMPTY_ND_ITC_DIFF)

    old_has_data = bool(old_nd.get("nit_rfis") or old_nd.get("awl_entries"))
    new_has_data = bool(new_nd.get("nit_rfis") or new_nd.get("awl_entries"))
    if not old_has_data:
        if new_has_data:
            log.info("[ND-iTC Diff] No prior ND-iTC data — baseline established, "
                     "no changes reported.")
        return empty
    if not new_has_data:
        # Fetch/parse failure — do not report the whole list as removed.
        log.warning("[ND-iTC Diff] Collection returned no data but prior data "
                    "exists — treating as collection failure.")
        empty["collection_failure"] = True
        return empty

    old_rfis = byid(old_nd.get("nit_rfis", []), "rfi_id")
    new_rfis = byid(new_nd.get("nit_rfis", []), "rfi_id")
    rfis_added = [new_rfis[k] for k in set(new_rfis) - set(old_rfis)]
    status_changes = []
    revised = []
    for rid in set(old_rfis) & set(new_rfis):
        o, n = old_rfis[rid], new_rfis[rid]
        if (o.get("status") or "") != (n.get("status") or ""):
            status_changes.append({
                **n,
                "old_status": o.get("status", ""),
                "new_status": n.get("status", ""),
            })
        elif any((o.get(f) or "") != (n.get(f) or "")
                 for f in ("title", "href", "reference", "impact",
                           "publication_date")):
            revised.append(dict(n))

    # Active → archived transitions
    old_arch_ids = {r.get("rfi_id") for r in old_nd.get("nit_rfis_archived", [])}
    new_arch = byid(new_nd.get("nit_rfis_archived", []), "rfi_id")
    newly_archived = [
        new_arch[k] for k in set(new_arch) - old_arch_ids if k in old_rfis
    ]

    # Allowed-With entries keyed by list|section|object id; the tracked
    # value is the object version.
    def _awl_key(e: dict) -> str:
        return f"{e.get('list', '')}|{e.get('section', '')}|{e.get('object_id', '')}"

    old_awl = {_awl_key(e): e for e in old_nd.get("awl_entries", [])}
    new_awl = {_awl_key(e): e for e in new_nd.get("awl_entries", [])}
    awl_added = [new_awl[k] for k in set(new_awl) - set(old_awl)]
    awl_removed = [old_awl[k] for k in set(old_awl) - set(new_awl)]
    awl_version_changes = [
        {**new_awl[k],
         "old_version": old_awl[k].get("object_version", ""),
         "new_version": new_awl[k].get("object_version", "")}
        for k in set(old_awl) & set(new_awl)
        if (old_awl[k].get("object_version") or "")
        != (new_awl[k].get("object_version") or "")
    ]

    # Allowed-With list document version bumps (e.g. 4.0r1 → 4.0r2)
    old_meta = {m.get("list"): m for m in old_nd.get("awl_meta", [])}
    awl_list_updates = [
        {**m, "old_awl_version": old_meta[m["list"]].get("awl_version", "")}
        for m in new_nd.get("awl_meta", [])
        if m.get("list") in old_meta
        and (m.get("awl_version") or "")
        != (old_meta[m["list"]].get("awl_version") or "")
    ]

    baseline_reset = (
        _is_baseline_reset(len(old_rfis), len(rfis_added), len(new_rfis))
        or _is_baseline_reset(len(old_awl), len(awl_added), len(new_awl))
    )
    if baseline_reset:
        log.warning("[ND-iTC Diff] Mass re-detection — treating as baseline "
                    "reset; notifications suppressed.")

    log.info("[ND-iTC Diff] rfis-added:%d status-changes:%d revised:%d "
             "archived:%d awl-added:%d awl-removed:%d awl-version-changes:%d "
             "awl-list-updates:%d baseline-reset:%s",
             len(rfis_added), len(status_changes), len(revised),
             len(newly_archived), len(awl_added), len(awl_removed),
             len(awl_version_changes), len(awl_list_updates), baseline_reset)
    return {
        "nit_rfis": {
            "added": rfis_added,
            "status_changes": status_changes,
            "revised": revised,
            "newly_archived": newly_archived,
        },
        "awl": {
            "added": awl_added,
            "removed": awl_removed,
            "version_changes": awl_version_changes,
            "list_updates": awl_list_updates,
        },
        "baseline_reset": baseline_reset,
        "collection_failure": False,
    }


# -- Master diff ---------------------------------------------------------------
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
    old_na = old_snapshot.get("nato", {})
    new_na = new_snapshot.get("nato", {})
    old_eu = old_snapshot.get("eucc", {})
    new_eu = new_snapshot.get("eucc", {})

    diff: Snapshot = {
        "period_start": old_snapshot.get("collected_at", ""),
        "period_end": new_snapshot.get("collected_at", ""),
        "niap": {
            "pps": diff_niap_pps(old_n.get("pps", []), new_n.get("pps", [])),
            "tds": diff_niap_tds(old_n.get("tds", []), new_n.get("tds", [])),
            "cisco_ndcpp": diff_niap_pcl_cisco(old_n.get("pcl", []), new_n.get("pcl", [])),
            "pcl_all": diff_niap_pcl_all(old_n.get("pcl", []), new_n.get("pcl", [])),
            "in_evaluation": diff_niap_in_evaluation(old_n.get("pcl", []), new_n.get("pcl", [])),
            "news": diff_niap_news(old_n.get("news", []), new_n.get("news", [])),
            "events": diff_niap_events(old_n.get("events", []), new_n.get("events", [])),
            "policies": diff_niap_policies(old_n.get("policies", []), new_n.get("policies", [])),
        },
        "cc_portal": {
            "news": diff_cc_news(old_c.get("news", []), new_c.get("news", [])),
            "pps": diff_cc_pps(old_c.get("pps", []), new_c.get("pps", [])),
            "products": diff_cc_products(old_c.get("products", []), new_c.get("products", [])),
        },
        "cctl_labs": diff_cctl_labs(old_l, new_l),
        "csfc": diff_csfc(old_cs, new_cs),
        "cc_crypto": diff_cc_crypto(old_cc, new_cc),
        "nato": diff_nato(old_na, new_na),
        "eucc": diff_eucc(old_eu, new_eu),
        "nd_itc": diff_nd_itc(old_snapshot.get("nd_itc", {}),
                              new_snapshot.get("nd_itc", {})),
        "source_health": new_snapshot.get("source_health", {}),
    }

    diff["alerts"] = flag_alerts(diff)

    td_new = len(diff["niap"]["tds"]["added"])
    pp_new = len(diff["niap"]["pps"]["added"])
    alerts = len(diff["alerts"])
    log.info("[Diff] PPs new:%d TDs new:%d alerts:%d", pp_new, td_new, alerts)
    return diff
# -- Weekly merge --------------------------------------------------------------
def merge_weekly_diffs(diffs: list[Snapshot]) -> Snapshot:
    """Merge a list of daily diffs into one weekly summary.

    Fixes:
    - #22: alert_key now reads matched_keywords (consistent with _add())
    - #23: nato and eucc sections added to initializer and merge loop
    """
    if not diffs:
        return {}

    def merge_lists(*lists, key_fn=None):
        seen: set = set()
        merged: list = []
        for lst in lists:
            for item in lst:
                k = key_fn(item) if key_fn else str(item)[:120]
                if k not in seen:
                    seen.add(k)
                    merged.append(item)
        return merged

    def alert_key(a: dict) -> str:
        """Deduplicate alerts on source+title+keywords."""
        return f"{a.get('source','')}|{a.get('title','')}|{','.join(sorted(a.get('matched_keywords', [])))}"

    import copy
    weekly = copy.deepcopy(diffs[0])

    # Ensure all top-level domain keys exist on weekly -- fix #23: added nato, eucc
    for domain_key, default in [
        ("niap", {"pps": {"added":[], "removed":[], "sunset_changes":[], "status_changes":[]},
                  "tds": {"added":[], "removed":[]},
                  "cisco_ndcpp": {"added":[], "removed":[], "newly_archived":[]},
                  "news": {"added":[], "revised":[], "deactivated":[], "reactivated":[], "removed":[]},
                  "events": {"added":[], "revised":[], "deactivated":[], "reactivated":[], "removed":[]},
                  "policies": {"added":[], "revised":[], "archived":[], "reactivated":[], "removed":[]}}),
        ("cc_portal", {"news": {"added":[]}, "pps": {"added":[]}, "products": {"added":[]}}),
        ("cctl_labs", {}),
        ("csfc", {"feeds": {}, "pages": {}, "selection_links": {}}),
        ("cc_crypto", {"pages": {}}),
        ("nato", {"pages": {}, "cisco_added": [], "cisco_removed": []}),  # fix #23
        ("eucc", {"pages": {}, "cisco_added": [], "cisco_removed": []}),  # fix #23
        ("nd_itc", copy.deepcopy(_EMPTY_ND_ITC_DIFF)),
        ("alerts", []),
    ]:
        if domain_key not in weekly:
            weekly[domain_key] = default
    # Older daily diffs can have a NIAP section without the newer content
    # subcollections. Seed nested defaults so mixed-version weekly windows are
    # safe during rollout.
    weekly.setdefault("niap", {})
    for key, default in {
        "news": {"added": [], "revised": [], "deactivated": [], "reactivated": [], "removed": []},
        "events": {"added": [], "revised": [], "deactivated": [], "reactivated": [], "removed": []},
        "policies": {"added": [], "revised": [], "archived": [], "reactivated": [], "removed": []},
    }.items():
        weekly["niap"].setdefault(key, copy.deepcopy(default))
    for d in diffs[1:]:
        # Health is point-in-time metadata; the latest day wins.
        if "source_health" in d:
            weekly["source_health"] = copy.deepcopy(d["source_health"])
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
        for key in ("added", "revised", "deactivated", "reactivated", "removed"):
            weekly["niap"]["news"][key] = merge_lists(
                weekly["niap"]["news"].get(key, []),
                d.get("niap", {}).get("news", {}).get(key, []))
            weekly["niap"]["events"][key] = merge_lists(
                weekly["niap"]["events"].get(key, []),
                d.get("niap", {}).get("events", {}).get(key, []))
        for key in ("added", "revised", "archived", "reactivated", "removed"):
            weekly["niap"]["policies"][key] = merge_lists(
                weekly["niap"]["policies"].get(key, []),
                d.get("niap", {}).get("policies", {}).get(key, []))

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

        # Alerts -- use source+title+keywords key to avoid duplicates
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
        for sel_name, sel_data in d.get("csfc", {}).get("selection_links", {}).items():
            weekly["csfc"]["selection_links"][sel_name] = sel_data

        # CC Crypto
        for page_key, page_diff in d.get("cc_crypto", {}).get("pages", {}).items():
            if isinstance(page_diff, dict) and "added" in page_diff:
                if page_key not in weekly["cc_crypto"]["pages"]:
                    weekly["cc_crypto"]["pages"][page_key] = {"added": []}
                weekly["cc_crypto"]["pages"][page_key]["added"] = merge_lists(
                    weekly["cc_crypto"]["pages"][page_key]["added"], page_diff["added"])

        # NATO -- fix #23: merge nato pages and cisco_added/removed
        for page_key, page_diff in d.get("nato", {}).get("pages", {}).items():
            if isinstance(page_diff, dict) and "added" in page_diff:
                if page_key not in weekly["nato"]["pages"]:
                    weekly["nato"]["pages"][page_key] = {"added": []}
                weekly["nato"]["pages"][page_key]["added"] = merge_lists(
                    weekly["nato"]["pages"][page_key]["added"], page_diff["added"])
        weekly["nato"]["cisco_added"] = merge_lists(
            weekly["nato"].get("cisco_added", []),
            d.get("nato", {}).get("cisco_added", []))
        weekly["nato"]["cisco_removed"] = merge_lists(
            weekly["nato"].get("cisco_removed", []),
            d.get("nato", {}).get("cisco_removed", []))

        # EUCC -- fix #23: merge eucc pages and cisco_added/removed
        for page_key, page_diff in d.get("eucc", {}).get("pages", {}).items():
            if isinstance(page_diff, dict) and "added" in page_diff:
                if page_key not in weekly["eucc"]["pages"]:
                    weekly["eucc"]["pages"][page_key] = {"added": []}
                weekly["eucc"]["pages"][page_key]["added"] = merge_lists(
                    weekly["eucc"]["pages"][page_key]["added"], page_diff["added"])
        weekly["eucc"]["cisco_added"] = merge_lists(
            weekly["eucc"].get("cisco_added", []),
            d.get("eucc", {}).get("cisco_added", []))
        weekly["eucc"]["cisco_removed"] = merge_lists(
            weekly["eucc"].get("cisco_removed", []),
            d.get("eucc", {}).get("cisco_removed", []))

        # ND-iTC: merge NIT RFI and Allowed-With change lists
        nd_daily = d.get("nd_itc", {})
        for group, keys in (
            ("nit_rfis", ("added", "status_changes", "revised", "newly_archived")),
            ("awl", ("added", "removed", "version_changes", "list_updates")),
        ):
            for key in keys:
                weekly["nd_itc"][group][key] = merge_lists(
                    weekly["nd_itc"][group].get(key, []),
                    nd_daily.get(group, {}).get(key, []))

    return weekly
# -- CSfC diff -----------------------------------------------------------------
def diff_csfc(old_csfc: Snapshot, new_csfc: Snapshot) -> Snapshot:
    """Diff two CSfC snapshots."""
    pages = _diff_pages(old_csfc.get("pages", {}), new_csfc.get("pages", {}))
    selection_links = _diff_selection_links(
        old_csfc.get("selection_links", {}),
        new_csfc.get("selection_links", {}),
    )
    feeds = _diff_feeds(
        old_csfc.get("feeds", {}),
        new_csfc.get("feeds", {}),
        categorize=True,
    )
    page_changes = sum(len(v.get("added", [])) for v in pages.values())
    sel_changes = len(selection_links)
    feed_new = sum(len(v) for v in feeds.values())
    log.info(
        "[CSfC Diff] page-items-added:%d selection-link-changes:%d feed-new:%d",
        page_changes, sel_changes, feed_new,
    )
    return {"pages": pages, "selection_links": selection_links, "feeds": feeds}

# -- CC Crypto Catalog diff ----------------------------------------------------
def diff_cc_crypto(old_cc: Snapshot, new_cc: Snapshot) -> Snapshot:
    """Diff two CC Crypto Catalog snapshots.

    Doc header polling removed (fix #27): unreliable CDN headers replaced
    by structured page-text diffing. Publication changes on the CC Portal
    crypto pages are now surfaced directly as notifications.
    """
    pages = _diff_pages(old_cc.get("pages", {}), new_cc.get("pages", {}))

    page_changes = sum(len(v.get("added", [])) for v in pages.values())
    log.info("[CC Crypto Diff] page-items-added:%d", page_changes)
    return {"pages": pages}
