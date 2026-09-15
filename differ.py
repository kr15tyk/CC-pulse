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
from urllib.parse import urlsplit, urlunsplit

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
    for item in diff.get("niap", {}).get("pps", {}).get("revised", []):
        title = item.get("pp_short_name") or item.get("pp_name") or "Protection Profile"
        _add(
            alerts, "NIAP PP", "updated", title,
            url=item.get("document_url") or config.NIAP_BASE + "/protection-profiles",
            detail="Protection Profile metadata revised: " + ", ".join(item.get("changed_fields", [])),
            keywords=["CNSA/PQC"] if item.get("document_url") else [], tab="us",
        )
    for item in diff.get("niap", {}).get("pps", {}).get("content_changes", []):
        title = item.get("pp_short_name") or item.get("pp_name") or "Protection Profile"
        markers = ", ".join(item.get("cnsa_markers") or []) or "no detected CNSA markers"
        _add(
            alerts, "NIAP PP Document", "updated", title,
            url=item.get("document_url") or config.NIAP_BASE + "/protection-profiles",
            detail=(
                ("Published PP document content changed" if item.get("hash_changed")
                 else "Published PP file version changed")
                + f" · markers: {markers}"
            ),
            keywords=["CNSA", "PQC"], tab="us",
        )

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

    for kind in ("added", "removed", "updated"):
        for item in diff.get("csfc", {}).get("documents", {}).get(kind, []):
            _add(
                alerts,
                "CSfC Capability Package",
                kind,
                item.get("label") or item.get("key") or "Capability package",
                url=item.get("url") or item.get("old_url") or (
                    config.CSFC_BASE + config.CSFC_PAGES["cap_packages"]
                ),
                detail="Tracked CSfC capability-package document changed",
                keywords=["CSfC", "CNSA", "PQC"],
                tab="us",
            )

    # CSfC Components List page changes — new items on the Components List page
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
                "CSfC Components List" if page_key == "apl" else f"CSfC: {page_key}",
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
                "CSfC Components List" if page_key == "apl" else f"CSfC: {page_key}",
                "removed",
                item.get("text", ""),
                url=(_csfc_page_urls.get(page_key, config.CSFC_PRODUCT_LIST_URL) if page_key == "apl" else item.get("href") or item.get("link") or _csfc_page_urls.get(page_key, config.CSFC_PRODUCT_LIST_URL)),
                detail=f"Item removed from CSfC {page_key.replace('_', ' ')} page",
                tab="us",
                additional_keywords=vendor_keywords,
            )

        for item in page_diff.get("updated", []):
            vendor_keywords = config.CISCO_VENDOR_KEYWORDS if page_key == "apl" else []
            _add_text(
                "CSfC Components List" if page_key == "apl" else f"CSfC: {page_key}",
                "updated",
                item.get("text", ""),
                url=item.get("href") or item.get("link") or _csfc_page_urls.get(page_key, config.CSFC_PRODUCT_LIST_URL),
                detail=f"Listing updated on CSfC {page_key.replace('_', ' ')} page",
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

    # ND-iTC — NIT RFIs and Allowed-With lists. Every change alerts.
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
            return config.ND_ITC_AWL_URLS.get(item.get("list", ""), config.ND_ITC_BASE)

        for item in awl.get("added", []):
            _add(alerts, "ND-iTC Allowed-With", "new", item.get("object_id", ""),
                 url=_awl_url(item),
                 detail=(f"Added to the {item.get('section', '')} allowed-with "
                         f"list · version {item.get('object_version', '')}"),
                 keywords=["NDcPP"], tab="intl")
        for item in awl.get("removed", []):
            _add(alerts, "ND-iTC Allowed-With", "removed", item.get("object_id", ""),
                 url=_awl_url(item),
                 detail=f"Removed from the {item.get('section', '')} allowed-with list",
                 keywords=["NDcPP"], tab="intl")
        for item in awl.get("version_changes", []):
            _add(alerts, "ND-iTC Allowed-With", "updated", item.get("object_id", ""),
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

    # IETF CNSA profiles and the RFC 8446 -> RFC 9846 transition are direct,
    # high-value standards signals and do not depend on keyword matching.
    ietf = diff.get("ietf_cnsa", {})
    for kind in ("added", "removed", "updated"):
        for item in ietf.get(kind, []):
            detail = "IETF CNSA profile metadata or full text changed"
            if item.get("changed_fields"):
                detail += ": " + ", ".join(item["changed_fields"])
            _add(alerts, "IETF CNSA", kind,
                 item.get("title") or item.get("name") or "CNSA profile",
                 url=item.get("document_url") or config.IETF_DATATRACKER_BASE,
                 detail=detail, keywords=["CNSA", "PQC"], tab="us")
    for item in ietf.get("rfc9846_adoption", []):
        detail = (
            "TLS CNSA profile replaced RFC 8446 with RFC 9846"
            if item.get("replaced_rfc8446")
            else "TLS CNSA profile now references RFC 9846"
        )
        _add(alerts, "IETF CNSA TLS", "rfc_transition",
             "RFC 9846 adopted by the CNSA 2.0 TLS profile",
             url=item.get("document_url") or config.IETF_DATATRACKER_BASE,
             detail=detail, keywords=["CNSA", "RFC 9846", "TLS"], tab="us")

    ieee = diff.get("ieee_pqc", {})
    for kind in ("added", "removed", "updated"):
        for item in ieee.get(kind, []):
            draft_change = ""
            if item.get("old_draft") != item.get("draft"):
                draft_change = f" · draft {item.get('old_draft') or '?'} → {item.get('draft') or '?'}"
            _add(alerts, "IEEE 802.11bt", kind,
                 f"{item.get('project', 'P802.11bt')} {item.get('title', 'Post-Quantum Cryptography')}",
                 url=item.get("timeline_url") or config.IEEE_80211_TIMELINE_URL,
                 detail="IEEE 802.11bt milestone/status changed" + draft_change,
                 keywords=["PQC", "802.11bt"], tab="us")

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

    metadata_fields = (
        "pp_short_name", "pp_name", "pp_date", "pp_transition",
        "cc_version", "tech_type", "pp_sponsor_id",
    )
    revised = []
    content_changes = []
    for pid in old_ids & new_ids:
        old_item = old_map[pid]
        new_item = new_map[pid]
        changed_fields = [
            field for field in metadata_fields
            if old_item.get(field) != new_item.get(field)
        ]
        if changed_fields:
            revised.append({
                **new_item,
                "changed_fields": changed_fields,
                "old_values": {field: old_item.get(field) for field in changed_fields},
            })

        old_hash = old_item.get("document_sha256") or ""
        new_hash = new_item.get("document_sha256") or ""
        hash_changed = bool(old_hash and new_hash and old_hash != new_hash)
        markers_comparable = "cnsa_markers" in old_item and "cnsa_markers" in new_item
        markers_changed = (
            markers_comparable
            and sorted(old_item.get("cnsa_markers") or [])
            != sorted(new_item.get("cnsa_markers") or [])
        )
        old_file_id = old_item.get("document_file_id")
        new_file_id = new_item.get("document_file_id")
        old_filename = old_item.get("document_filename") or ""
        new_filename = new_item.get("document_filename") or ""
        file_changed = bool(
            old_file_id not in (None, "") and new_file_id not in (None, "")
            and str(old_file_id) != str(new_file_id)
        ) or bool(old_filename and new_filename and old_filename != new_filename)
        if hash_changed or markers_changed or file_changed:
            content_changes.append({
                **new_item,
                "old_document_sha256": old_hash,
                "old_cnsa_markers": old_item.get("cnsa_markers") or [],
                "hash_changed": hash_changed,
                "markers_changed": markers_changed,
                "file_changed": file_changed,
                "old_document_file_id": old_file_id,
                "old_document_filename": old_filename,
            })

    return {
        "added": added,
        "removed": removed,
        "sunset_changes": sunset_changes,
        "status_changes": status_changes,
        "revised": revised,
        "content_changes": content_changes,
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
        return str(
            row.get("id")
            or row.get("pp_id")
            or row.get("PPID")
            or row.get("ID")
            or row.get("link")
            or row.get("title")
            or row.get("text")
            or ""
        ).strip()
    old_keys = {key(r) for r in old_pps}
    return {"added": [r for r in new_pps if key(r) not in old_keys]}

def diff_cc_products(old_products: Records, new_products: Records) -> dict[str, Any]:
    def key(row: dict) -> str:
        product_id = row.get("id") or row.get("product_id") or row.get("ID")
        if product_id not in (None, ""):
            return f"id:{str(product_id).strip()}"
        title = re.sub(
            r"\s+", " ", str(row.get("title") or row.get("name") or "")
        ).strip().casefold()
        cert_date = str(
            row.get("certificate_date") or row.get("certified") or ""
        ).strip().casefold()
        link = str(row.get("link") or row.get("certificate_link") or "").strip()
        return f"metadata:{title}|{cert_date}" if title else f"url:{link}"
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


def _diff_pages_with_updates(old_pages: dict, new_pages: dict) -> dict:
    """Diff pages by stable text prefix and report same-record revisions.

    Full-content hashes are compared only when both snapshots contain them;
    that makes adding the hash field backwards compatible during rollout.
    """
    result = {}
    for page_key in set(old_pages) | set(new_pages):
        old_items = old_pages.get(page_key, [])
        new_items = new_pages.get(page_key, [])
        old_map = {
            item.get("text", "")[:120]: item
            for item in old_items if item.get("text")
        }
        new_map = {
            item.get("text", "")[:120]: item
            for item in new_items if item.get("text")
        }
        old_keys = set(old_map)
        new_keys = set(new_map)
        added = [new_map[key] for key in new_keys - old_keys]
        removed = [old_map[key] for key in old_keys - new_keys]
        updated = []
        for key in old_keys & new_keys:
            old_item = old_map[key]
            new_item = new_map[key]
            old_hash = old_item.get("content_sha256") or ""
            new_hash = new_item.get("content_sha256") or ""
            hash_changed = bool(old_hash and new_hash and old_hash != new_hash)
            text_changed = (old_item.get("text") or "") != (new_item.get("text") or "")
            href_changed = (
                (old_item.get("href") or old_item.get("link") or "")
                != (new_item.get("href") or new_item.get("link") or "")
            )
            if hash_changed or text_changed or href_changed:
                changed = copy.deepcopy(new_item)
                changed["_old_text"] = old_item.get("text") or ""
                changed["_old_content_sha256"] = old_hash
                updated.append(changed)
        if added or removed or updated:
            result[page_key] = {"added": added, "removed": removed}
            if updated:
                result[page_key]["updated"] = updated
    return result


def _diff_named_documents(old_docs: dict, new_docs: dict) -> dict:
    """Diff stable-name document mappings with rollout-safe hash comparison."""
    old_keys = set(old_docs)
    new_keys = set(new_docs)
    added = [{"key": key, **new_docs[key]} for key in new_keys - old_keys]
    removed = [{"key": key, **old_docs[key]} for key in old_keys - new_keys]
    updated = []
    for key in old_keys & new_keys:
        old_item = old_docs[key]
        new_item = new_docs[key]
        old_hash = old_item.get("sha256") or ""
        new_hash = new_item.get("sha256") or ""
        hash_changed = bool(old_hash and new_hash and old_hash != new_hash)
        url_changed = bool(
            old_item.get("url") and new_item.get("url")
            and old_item.get("url") != new_item.get("url")
        )
        if hash_changed or url_changed:
            updated.append({
                "key": key,
                **new_item,
                "old_url": old_item.get("url", ""),
                "old_sha256": old_hash,
                "hash_changed": hash_changed,
                "url_changed": url_changed,
            })
    return {"added": added, "removed": removed, "updated": updated}


# -- CSfC Components List diff helper -----------------------------------------
def _normalize_csfc_component_type(value) -> str:
    return re.sub(r"\s+", " ", str(value or "").replace("\xa0", " ")).strip().casefold()


def _csfc_apl_key(item: dict) -> str:
    """Stable CSfC APL identity: NIAP product plus component role.

    A single NIAP validation can appear under multiple CSfC component types.
    URL-only identity collapsed those roles into one row and hid additions.
    NIAP product IDs also survive URL capitalization and international-product
    path changes better than the raw URL.
    """
    component_type = _normalize_csfc_component_type(item.get("type"))
    href = str(item.get("href") or "").strip()
    if href:
        parsed = urlsplit(href)
        product_match = re.search(
            r"/products/(?:international-product/)?([0-9]+(?:\.[0-9]+)*)(?:/|$)",
            parsed.path,
            flags=re.IGNORECASE,
        )
        if product_match and (parsed.hostname or "").casefold().endswith("niap-ccevs.org"):
            identity = f"niap:{product_match.group(1)}"
        else:
            normalized_url = urlunsplit((
                parsed.scheme.casefold(),
                parsed.netloc.casefold(),
                parsed.path.rstrip("/") or "/",
                "",
                "",
            ))
            identity = f"url:{normalized_url}"
    else:
        text = re.sub(r"\s+", " ", str(item.get("text") or "")).strip().casefold()
        identity = f"text:{text[:120]}"
    return f"{identity}|type:{component_type}"


def _diff_csfc_apl(old_items: list, new_items: list) -> dict:
    """Diff CSfC Components List entries by product and component role.

    _diff_pages() keys purely on the first 120 chars of display text, so an
    edit to an existing listing's wording (a cert-date tweak, a description
    reformat) with the underlying product/VID unchanged shows up as a
    spurious removed+added pair. APL records carry a stable href to the
    underlying NIAP product page (collector._parse_csfc_apl_structured), so
    key on that plus the component type (falling back to the text prefix for
    any record without a link) and report same-key text changes as "updated".
    """
    old_by_key = {
        _csfc_apl_key(item): item
        for item in old_items
    }
    new_by_key = {
        _csfc_apl_key(item): item
        for item in new_items
    }
    old_keys = set(old_by_key)
    new_keys = set(new_by_key)

    added = [new_by_key[k] for k in new_keys - old_keys]
    removed = [old_by_key[k] for k in old_keys - new_keys]
    updated = []
    for k in old_keys & new_keys:
        old_item = old_by_key[k]
        new_item = new_by_key[k]
        if (old_item.get("text") or "") != (new_item.get("text") or ""):
            changed = copy.deepcopy(new_item)
            changed["_old_text"] = old_item.get("text") or ""
            updated.append(changed)

    return {"added": added, "removed": removed, "updated": updated}

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
def _diff_nato_pages(old_pages: dict, new_pages: dict) -> dict:
  """Diff two NATO {page_key: [product_records]} dicts.

  NATO product records use link/raw_text (not the generic 'text' field
  _diff_pages() expects), so keying on 'text' matches nothing and every
  retained product re-appears as "new" every run regardless of real
  change. Key the same way the Cisco-specific diff below already does.
  """
  result = {}
  for page_key in set(old_pages) | set(new_pages):
    old_items = old_pages.get(page_key, [])
    new_items = new_pages.get(page_key, [])
    old_keys = {_record_key(p, url_field="link", text_field="raw_text") for p in old_items}
    new_keys = {_record_key(p, url_field="link", text_field="raw_text") for p in new_items}
    added = [p for p in new_items
             if _record_key(p, url_field="link", text_field="raw_text") not in old_keys]
    removed = [p for p in old_items
               if _record_key(p, url_field="link", text_field="raw_text") not in new_keys]
    if added or removed:
      result[page_key] = {"added": added, "removed": removed}
  return result

# -- NATO NIAPCL diff ---------------------------------------------------------
def diff_nato(old_nato: Snapshot, new_nato: Snapshot) -> Snapshot:
    """Diff two NATO NIAPCL snapshots."""
    old_pages = old_nato.get("pages", {})
    new_pages = new_nato.get("pages", {})

    pages = _diff_nato_pages(old_pages, new_pages)

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


def _eucc_metadata_key(record: dict) -> str | None:
    """Return metadata identity for an EUCC certificate when available.

    ENISA has migrated certificate detail URLs while retaining the same
    product metadata and certification date.  A URL-only key turns that
    migration into a false removal/addition pair and can fire a duplicate
    Cisco celebration.  Product name + certificate date identifies the
    certificate across that URL churn. Records without both fields return
    ``None`` and are reconciled by URL/text identity instead.
    """
    product_name = re.sub(r"\s+", " ", str(record.get("name") or "").strip()).casefold()
    cert_date = re.sub(r"\s+", " ", str(record.get("cert_date") or "").strip()).casefold()
    if product_name and cert_date:
        return f"metadata:{product_name}|{cert_date}"
    return None


def _diff_eucc_certificates(
    old_records: Records,
    new_records: Records,
) -> tuple[list[dict], list[dict]]:
    """Return EUCC additions/removals with URL and metadata reconciliation.

    Exact URLs are matched first to preserve the existing revision behavior.
    Only records left unmatched are paired by product name + certificate date,
    which handles detail-URL migrations without hiding genuine new dates.
    """
    unmatched_old = set(range(len(old_records)))
    unmatched_new = set(range(len(new_records)))
    new_by_url: dict[str, list[int]] = {}
    for index, record in enumerate(new_records):
        url = (record.get("href") or "").strip()
        if url:
            new_by_url.setdefault(url, []).append(index)

    # Preserve URL identity wherever it still exists.
    for old_index, record in enumerate(old_records):
        url = (record.get("href") or "").strip()
        if not url:
            continue
        for new_index in new_by_url.get(url, []):
            if new_index in unmatched_new:
                unmatched_old.discard(old_index)
                unmatched_new.discard(new_index)
                break

    old_by_metadata: dict[str, list[int]] = {}
    new_by_metadata: dict[str, list[int]] = {}
    for index in unmatched_old:
        key = _eucc_metadata_key(old_records[index])
        if key:
            old_by_metadata.setdefault(key, []).append(index)
    for index in unmatched_new:
        key = _eucc_metadata_key(new_records[index])
        if key:
            new_by_metadata.setdefault(key, []).append(index)

    # Reconcile only unmatched records so an exact URL match always wins.
    for key in old_by_metadata.keys() & new_by_metadata.keys():
        for old_index, new_index in zip(old_by_metadata[key], new_by_metadata[key]):
            unmatched_old.discard(old_index)
            unmatched_new.discard(new_index)

    added = [copy.deepcopy(new_records[index]) for index in unmatched_new]
    removed = [copy.deepcopy(old_records[index]) for index in unmatched_old]
    return added, removed


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

    # Diff Cisco-specific certificates by URL first, then product metadata/date
    # so ENISA URL migrations do not look like new certifications.
    cisco_added, cisco_removed = _diff_eucc_certificates(
        old_eucc.get("cisco_certs", []),
        new_eucc.get("cisco_certs", []),
    )

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


# -- IETF CNSA and IEEE PQC diffs ---------------------------------------------
def diff_ietf_cnsa(old_ietf: Snapshot, new_ietf: Snapshot) -> Snapshot:
    old_map = byid(old_ietf.get("documents", []), "name")
    new_map = byid(new_ietf.get("documents", []), "name")
    old_names = set(old_map)
    new_names = set(new_map)
    added = [copy.deepcopy(new_map[name]) for name in new_names - old_names]
    removed = [copy.deepcopy(old_map[name]) for name in old_names - new_names]
    updated = []
    rfc9846_adoption = []
    metadata_fields = (
        "revision", "expires", "rfc_number",
        "workflow_state", "states", "relations", "cnsa_markers",
    )
    for name in old_names & new_names:
        old_item = old_map[name]
        new_item = new_map[name]
        changed_fields = [
            field for field in metadata_fields
            if old_item.get(field) != new_item.get(field)
        ]
        old_hash = old_item.get("content_sha256") or ""
        new_hash = new_item.get("content_sha256") or ""
        if old_hash and new_hash and old_hash != new_hash:
            changed_fields.append("content_sha256")
        for field in ("references_rfc8446", "references_rfc9846"):
            if field in old_item and field in new_item and old_item[field] != new_item[field]:
                changed_fields.append(field)
        if changed_fields:
            updated.append({
                **copy.deepcopy(new_item),
                "changed_fields": changed_fields,
                "old_revision": old_item.get("revision", ""),
                "old_workflow_state": old_item.get("workflow_state", ""),
                "old_expires": old_item.get("expires", ""),
                "old_content_sha256": old_hash,
            })
        if (
            name == "draft-becker-cnsa2-tls-profile"
            and old_item.get("references_rfc9846") is False
            and new_item.get("references_rfc9846") is True
        ):
            rfc9846_adoption.append({
                **copy.deepcopy(new_item),
                "replaced_rfc8446": (
                    old_item.get("references_rfc8446") is True
                    and new_item.get("references_rfc8446") is False
                ),
            })
    return {
        "added": added,
        "removed": removed,
        "updated": updated,
        "rfc9846_adoption": rfc9846_adoption,
    }


def diff_ieee_pqc(old_ieee: Snapshot, new_ieee: Snapshot) -> Snapshot:
    old_map = byid(old_ieee.get("projects", []), "project")
    new_map = byid(new_ieee.get("projects", []), "project")
    old_names = set(old_map)
    new_names = set(new_map)
    added = [copy.deepcopy(new_map[name]) for name in new_names - old_names]
    removed = [copy.deepcopy(old_map[name]) for name in old_names - new_names]
    updated = []
    for name in old_names & new_names:
        old_item = old_map[name]
        new_item = new_map[name]
        changed_fields = []
        for field in ("draft", "dates", "status_text"):
            if old_item.get(field) != new_item.get(field):
                changed_fields.append(field)
        for field in ("timeline_sha256", "status_sha256"):
            old_hash = old_item.get(field) or ""
            new_hash = new_item.get(field) or ""
            if old_hash and new_hash and old_hash != new_hash and field not in changed_fields:
                changed_fields.append(field)
        if changed_fields:
            updated.append({
                **copy.deepcopy(new_item),
                "changed_fields": changed_fields,
                "old_draft": old_item.get("draft", ""),
                "old_status_text": old_item.get("status_text", ""),
            })
    return {"added": added, "removed": removed, "updated": updated}


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
        "ietf_cnsa": diff_ietf_cnsa(
            old_snapshot.get("ietf_cnsa", {}), new_snapshot.get("ietf_cnsa", {})
        ),
        "ieee_pqc": diff_ieee_pqc(
            old_snapshot.get("ieee_pqc", {}), new_snapshot.get("ieee_pqc", {})
        ),
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
        ("niap", {"pps": {"added":[], "removed":[], "sunset_changes":[], "status_changes":[],
                            "revised":[], "content_changes":[]},
                  "tds": {"added":[], "removed":[]},
                  "cisco_ndcpp": {"added":[], "removed":[], "newly_archived":[]},
                  "news": {"added":[], "revised":[], "deactivated":[], "reactivated":[], "removed":[]},
                  "events": {"added":[], "revised":[], "deactivated":[], "reactivated":[], "removed":[]},
                  "policies": {"added":[], "revised":[], "archived":[], "reactivated":[], "removed":[]}}),
        ("cc_portal", {"news": {"added":[]}, "pps": {"added":[]}, "products": {"added":[]}}),
        ("cctl_labs", {}),
        ("csfc", {"feeds": {}, "pages": {}, "selection_links": {},
                  "documents": {"added": [], "removed": [], "updated": []}}),
        ("cc_crypto", {"pages": {}}),
        ("nato", {"pages": {}, "cisco_added": [], "cisco_removed": []}),  # fix #23
        ("eucc", {"pages": {}, "cisco_added": [], "cisco_removed": []}),  # fix #23
        ("nd_itc", copy.deepcopy(_EMPTY_ND_ITC_DIFF)),
        ("ietf_cnsa", {"added": [], "removed": [], "updated": [], "rfc9846_adoption": []}),
        ("ieee_pqc", {"added": [], "removed": [], "updated": []}),
        ("alerts", []),
    ]:
        if domain_key not in weekly:
            weekly[domain_key] = default
    # Older daily diffs can have a NIAP section without the newer content
    # subcollections. Seed nested defaults so mixed-version weekly windows are
    # safe during rollout.
    weekly.setdefault("niap", {})
    for key, default in {
        "pps": {"added": [], "removed": [], "sunset_changes": [], "status_changes": [],
                "revised": [], "content_changes": []},
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
        for key in ("added", "removed", "sunset_changes", "status_changes", "revised", "content_changes"):
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
            if not isinstance(page_diff, dict):
                continue
            for kind in ("added", "removed", "updated"):
                if kind not in page_diff:
                    continue
                weekly["csfc"]["pages"].setdefault(page_key, {})
                weekly["csfc"]["pages"][page_key][kind] = merge_lists(
                    weekly["csfc"]["pages"][page_key].get(kind, []),
                    page_diff[kind],
                )
        for sel_name, sel_data in d.get("csfc", {}).get("selection_links", {}).items():
            weekly["csfc"]["selection_links"][sel_name] = sel_data
        weekly["csfc"].setdefault(
            "documents", {"added": [], "removed": [], "updated": []}
        )
        for key in ("added", "removed", "updated"):
            weekly["csfc"]["documents"][key] = merge_lists(
                weekly["csfc"]["documents"].get(key, []),
                d.get("csfc", {}).get("documents", {}).get(key, []),
            )

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

        for key in ("added", "removed", "updated", "rfc9846_adoption"):
            weekly["ietf_cnsa"][key] = merge_lists(
                weekly["ietf_cnsa"].get(key, []),
                d.get("ietf_cnsa", {}).get(key, []),
            )
        for key in ("added", "removed", "updated"):
            weekly["ieee_pqc"][key] = merge_lists(
                weekly["ieee_pqc"].get(key, []),
                d.get("ieee_pqc", {}).get(key, []),
            )

    return weekly
# -- CSfC diff -----------------------------------------------------------------
def diff_csfc(old_csfc: Snapshot, new_csfc: Snapshot) -> Snapshot:
    """Diff two CSfC snapshots."""
    old_pages = old_csfc.get("pages", {})
    new_pages = new_csfc.get("pages", {})
    non_apl_old = {k: v for k, v in old_pages.items() if k != "apl"}
    non_apl_new = {k: v for k, v in new_pages.items() if k != "apl"}
    pages = _diff_pages_with_updates(non_apl_old, non_apl_new)
    apl_diff = _diff_csfc_apl(old_pages.get("apl", []), new_pages.get("apl", []))
    if apl_diff["added"] or apl_diff["removed"] or apl_diff["updated"]:
        pages["apl"] = apl_diff
    selection_links = _diff_selection_links(
        old_csfc.get("selection_links", {}),
        new_csfc.get("selection_links", {}),
    )
    feeds = _diff_feeds(
        old_csfc.get("feeds", {}),
        new_csfc.get("feeds", {}),
        categorize=True,
    )
    # Roll out a newly introduced document monitor as its own baseline instead
    # of baselining the entire, already-healthy CSfC domain. Once the key exists
    # (including as an explicit empty mapping), additions and revisions are
    # diffed normally.
    documents = (
        _diff_named_documents(
            old_csfc.get("documents", {}), new_csfc.get("documents", {})
        )
        if "documents" in old_csfc
        else {"added": [], "removed": [], "updated": []}
    )
    page_changes = sum(len(v.get("added", [])) for v in pages.values())
    sel_changes = len(selection_links)
    feed_new = sum(len(v) for v in feeds.values())
    apl_updated = len(apl_diff["updated"])
    log.info(
        "[CSfC Diff] page-items-added:%d apl-updated:%d selection-link-changes:%d feed-new:%d",
        page_changes, apl_updated, sel_changes, feed_new,
    )
    return {
        "pages": pages,
        "selection_links": selection_links,
        "documents": documents,
        "feeds": feeds,
    }

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
