"""
emailer.py — Builds and sends CC Pulse email digests.

Features:
  - Keyword alert section at top of email
  - Webex Space notification for immediate keyword alerts
    - Cisco-relevant alerts tagged and sorted to top (Tier 1)
    - Kind label promoted to front of each alert line
    - CSfC Capability Package changes show direct PDF link prominently
    - Tier-sorted output: Cisco/NDcPP > standards > general
  - Weekly digest covering NIAP, CC Portal, CCTL labs, CSfC, CC Crypto
  - Immediate alert email (send_alert_email) for same-day keyword matches
  - Structured logging
  - Generic webhook / MS Teams delivery via send_webhook_alert()
- Plain-language change descriptions via _describe_change() (issue #17)
"""
import html
import json
import logging
import os
import smtplib
import requests
import urllib.request
import urllib.error
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
ET = ZoneInfo("America/New_York")  # fix #25: was EST = timezone(timedelta(hours=-5)) — wrong during EDT
import config

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Cisco-relevance helpers
# ---------------------------------------------------------------------------

# Keywords that indicate an alert is directly relevant to Cisco engineers.
# Covers Cisco itself, NDcPP/VPN/WLAN PPs, and CSfC programmes Cisco
# participates in. Broad standards identifiers (FIPS 140-3 etc.) deliberately
# do NOT belong here: they appear in boilerplate on pages like the CMVP MIP
# table, where every vendor's row contains "FIPS 140-3" — that mislabeled
# TASS/Canonical/Palo Alto modules as "Cisco relevant" (2026-07-09 alert).
_CISCO_RELEVANT_KEYWORDS = {
    kw.lower() for kw in [
        "cisco",
        # NDcPP / PP-Modules
        "NDcPP", "CPP_ND", "PP-Module_VPN", "PP-Module_WLAN",
        "TLS 1.3", "SSH",
        # CSfC programmes
        "CSfC", "Commercial Solutions for Classified", "CSfC APL",
        "CSfC capability package",
        "CP-Mobile", "CP-MA", "CP-WAN", "CP-Campus WLAN", "CP-DAR", "CP-MDM",
        "NSA CSfC",
        # Crypto SFRs relevant to Cisco products
        "FCS_CKM", "FCS_COP", "FCS_RBG",
    ]
}

# Tier 2: standards that drive future cert requirements
_TIER2_KEYWORDS = {
    kw.lower() for kw in [
        "FIPS 140-3", "FIPS 186-4", "FIPS 186-5", "SP 800-131A",
        "FIPS 203", "FIPS 204", "FIPS 205",
        "SP 800-57",
        "ML-KEM", "ML-DSA", "SLH-DSA", "post-quantum", "PQC migration",
        "algorithm transition", "CCDB-018",
    ]
}


def _is_cisco_relevant(alert: dict) -> bool:
    """Return True if any matched keyword overlaps with _CISCO_RELEVANT_KEYWORDS."""
    hits = {kw.lower() for kw in alert.get("matched_keywords", [])}
    return bool(hits & _CISCO_RELEVANT_KEYWORDS)


def _alert_tier(alert: dict) -> int:
    """Return sort tier: 1 = Cisco/NDcPP direct, 2 = standards, 3 = general."""
    if _is_cisco_relevant(alert):
        return 1
    hits = {kw.lower() for kw in alert.get("matched_keywords", [])}
    if hits & _TIER2_KEYWORDS:
        return 2
    return 3


def _kind_label(kind: str) -> str:
    """Map kind value to a short uppercase label for the front of the alert line."""
    return {
        "new_cert":       "NEW CERT",
        "new_evaluation": "IN EVAL",
        "archived":       "ARCHIVED",
        "removed":        "REMOVED",
        "sunset":         "SUNSET",
        "updated":        "UPDATED",
        "new":            "NEW",
        "advisory":       "ADVISORY",
        "publication":    "PUBLISHED",
        "news":           "NEWS",
        "post":           "POST",
    }.get(kind, kind.upper() if kind else "CHANGE")



# ---------------------------------------------------------------------------
# Plain-language change descriptions (issue #17)
# ---------------------------------------------------------------------------

# Maps (kind, source_prefix) -> plain-English blurb shown below each alert.
# source_prefix is matched with str.startswith() so "NIAP" covers all NIAP sources.
# Falls back to a generic message when no specific entry matches.
_CHANGE_DESCRIPTIONS: dict[tuple[str, str], str] = {
    # New certifications
    ("new_cert",       "NIAP"):  "A product has been newly certified on the NIAP Validated Products List.",
    ("new_cert",       "CSfC"):  "A product has been added to the NSA Commercial Solutions for Classified (CSfC) Approved Products List.",
    ("new_cert",       "NATO"):  "A product has received a new listing on the NATO Information Assurance Product Catalogue (NIAPCL).",
    ("new_cert",       "EUCC"):  "A product has received a new EU Common Criteria (EUCC) certificate from ENISA.",
    # Products entering evaluation
    ("new_evaluation", "NIAP"):  "A product has entered the NIAP evaluation pipeline — it is not yet certified but is undergoing formal testing.",
    # Removals
    ("removed",        "NIAP"):  "A product or document has been removed from the NIAP site. This may indicate it was withdrawn, superseded, or delisted.",
    ("removed",        "CSfC"):  "An item has been removed from a CSfC/NSA list. Check the link for details.",
    ("removed",        "NATO"):  "An item has been removed from the NATO NIAPCL. This may indicate a product was delisted or superseded.",
    # Protection Profile changes
    ("new",            "NIAP PP"): "A new NIAP Protection Profile (PP) has been published. PPs define the security requirements a product category must meet for certification.",
    ("sunset",         "NIAP PP"): "A Protection Profile has been sunsetted. Products evaluated against it may no longer be accepted for new certifications after the sunset date.",
    ("updated",        "NIAP PP"): "An existing Protection Profile has been revised. Products in evaluation against it may be affected.",
    # Technical Decisions
    ("new",            "NIAP TD"): "A new NIAP Technical Decision (TD) has been issued. TDs clarify how a specific requirement in a Protection Profile should be interpreted by labs and vendors.",
    # CSfC
    ("updated", "CSfC Component Selections"): (
        "NSA has updated a Component Selection document. "
        "These documents list which CC-evaluated products are approved for a specific "
        "CSfC role (e.g. VPN gateway, MDM, IPsec client). "
        "Review the Components List page to see what changed and check whether any "
        "approved products relevant to your program have been added or removed."
    ),
    ("updated", "CSfC APL"): (
        "An existing CSfC Components List (APL) entry was revised \u2014 the underlying "
        "product/VID is unchanged, but the listing text (description, cert date, etc.) "
        "was edited. This is not a new product being added or an approved product "
        "losing approval."
    ),
    ("updated",        "CSfC"):  "A CSfC Capability Package or Component Selection document has changed. These documents define the approved architectures for handling classified information.",
    ("advisory",       "CSfC"):  "A new CSfC advisory or policy document has been published by the NSA.",
    # CC Portal
    ("new",            "CC Portal"): "New content has been posted to the international Common Criteria Portal.",
    # CCTL labs
    ("post",           "CCTL"):  "A Common Criteria Testing Laboratory (CCTL) has published a new post. Labs post updates about evaluations, tooling, and CC news.",
    ("new",            "CCTL"):  "A Common Criteria Testing Laboratory (CCTL) has published a new item.",
    # EUCC / ENISA
    ("updated",        "EUCC"):  "The EU EUCC scheme requirements or policy documents have been updated by ENISA.",
    # NATO
    ("new",            "NATO"):  "A new item has appeared on the NATO Information Assurance Product Catalogue (NIAPCL).",
}

_GENERIC_DESCRIPTIONS: dict[str, str] = {
    "new_cert":       "A new product certification has been detected.",
    "new_evaluation": "A product has entered a formal evaluation process.",
    "archived":       "An item has been archived — it is no longer actively maintained but remains visible for reference.",
    "removed":        "An item has been removed from the source list.",
    "sunset":         "An item has been sunsetted and is approaching or past its end-of-life date.",
    "updated":        "An existing item has been revised or updated.",
    "new":            "A new item has appeared.",
    "advisory":       "A new advisory or policy notice has been published.",
    "publication":    "A new document or standard has been published.",
    "news":           "A news item or announcement has been posted.",
    "post":           "A new post has been published.",
}


def _describe_change(kind: str, source: str) -> str:
    """Return a short plain-language description of what a change means.

    Looks up (kind, source_prefix) in _CHANGE_DESCRIPTIONS first (most specific),
    then falls back to _GENERIC_DESCRIPTIONS keyed on kind alone, then a catch-all.

    Args:
        kind:   The change kind string (e.g. "new_cert", "sunset", "updated").
        source: The source label (e.g. "NIAP PP", "CCTL Labs").

    Returns:
        A single sentence suitable for appending to a Webex or email alert.
    """
    kind   = kind   or ""  # guard against None
    source = source or ""  # guard against None
    # Sort by prefix length descending so the most-specific prefix wins
    # (e.g. "NIAP PP" beats "NIAP" when source is "NIAP PP Extra").
    candidates = sorted(
        ((k, s_prefix, desc) for (k, s_prefix), desc in _CHANGE_DESCRIPTIONS.items()),
        key=lambda x: len(x[1]),
        reverse=True,
    )
    for k, s_prefix, desc in candidates:
        if k == kind and source.startswith(s_prefix):
            return desc
    if kind in _GENERIC_DESCRIPTIONS:
        return _GENERIC_DESCRIPTIONS[kind]
    return "A change was detected on the monitored source."

# ---------------------------------------------------------------------------
# Webex / webhook message formatter
# ---------------------------------------------------------------------------

def _format_alert_lines(alerts: list[dict], max_items: int = 15) -> list[str]:
    """Format alert objects into Markdown lines for Webex / webhook.

    Improvements for Cisco engineers:
    - Alerts sorted by tier: Cisco-relevant first, then standards, then general.
    - Cisco-relevant alerts prefixed with a blue tag.
    - Kind label promoted to the front of each line (bold, uppercase).
    - CSfC Capability Package changes surface the direct PDF URL prominently.
    """
    sorted_alerts = sorted(alerts, key=lambda a: (_alert_tier(a), alerts.index(a)))

    lines = []
    for a in sorted_alerts[:max_items]:
        kws    = ", ".join(a.get("matched_keywords", []))
        title  = a.get("title", "")
        detail = a.get("detail", "")
        url    = a.get("url", "")
        kind   = a.get("kind", "")
        src    = a.get("source", "")
        tier   = _alert_tier(a)

        cisco_tag = "🔵 **CISCO RELEVANT** | " if tier == 1 else ""
        kind_str  = f"**[{_kind_label(kind)}]** " if kind else ""

        desc = f"{cisco_tag}{kind_str}**[{src}]** {title}"

        if detail:
            desc += f"\n ↳ {detail}"

        if url:
            if src == "CSfC CP":
                desc += f"\n 📄 **PDF:** {url}"
            else:
                desc += f"\n 🔗 {url}"

        blurb = _describe_change(kind, src)
        desc += f"\n \U0001f4ac _{blurb}_"
        desc += f"\n 🔑 _{kws}_"
        lines.append(desc)

    if len(alerts) > max_items:
        lines.append(
            f"_…and {len(alerts) - max_items} more — "
            f"[view full dashboard](https://kr15tyk.github.io/CC-pulse/cc_dashboard.html)._"
        )
    return lines


# ---------------------------------------------------------------------------
# Webex notification
# ---------------------------------------------------------------------------

def send_webex_alert(alerts: list[dict]) -> None:
    """POST an actionable Webex message for high-priority keyword alerts.

    Message structure:
      - Header with total count and tier breakdown summary
      - Tier 1 (Cisco-relevant) alerts listed first
      - Tier 2 (standards) next
      - Tier 3 (general) last
      - Dashboard link footer
    """
    token   = config.WEBEX_BOT_TOKEN
    room_id = config.WEBEX_ROOM_ID
    if not token or not room_id:
        log.debug("[Webex] Bot token or Room ID not configured — skipping.")
        return
    if not alerts:
        return

    tier_counts = {1: 0, 2: 0, 3: 0}
    for a in alerts:
        tier_counts[_alert_tier(a)] += 1

    tier_parts = []
    if tier_counts[1]:
        tier_parts.append(f"🔵 {tier_counts[1]} Cisco-relevant")
    if tier_counts[2]:
        tier_parts.append(f"📐 {tier_counts[2]} standards")
    if tier_counts[3]:
        tier_parts.append(f"📋 {tier_counts[3]} general")

    header = (
        f"## ⚠️ CC Pulse — {len(alerts)} Keyword Alert{'s' if len(alerts) != 1 else ''}\n"
        f"_{' · '.join(tier_parts)}_\n"
    )
    body           = "\n\n---\n".join(_format_alert_lines(alerts))
    dashboard_link = "\n\n[View full dashboard](https://kr15tyk.github.io/CC-pulse/cc_dashboard.html)"

    payload = json.dumps({
        "roomId":   room_id,
        "markdown": header + body + dashboard_link,
    }).encode("utf-8")

    req = urllib.request.Request(
        "https://webexapis.com/v1/messages",
        data=payload,
        headers={
            "Content-Type":  "application/json",
            "Authorization": f"Bearer {token}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            log.info("[Webex] Alert sent (HTTP %d).", resp.status)
    except urllib.error.URLError as exc:
        log.warning("[Webex] Failed to send message: %s", exc)


# ---------------------------------------------------------------------------
# Generic Webhook / MS Teams notification
# ---------------------------------------------------------------------------

def send_webhook_alert(alerts: list[dict]) -> None:
    """POST a compact notification to a generic webhook (e.g. MS Teams).

    Uses the same tier-sorted, Cisco-tagged format as the Webex message.
    Payload: {"text": "..."} compatible with MS Teams and Slack-style webhooks.
    """
    url = config.WEBHOOK_URL
    if not url:
        log.debug("[Webhook] WEBHOOK_URL not configured — skipping.")
        return
    if not alerts:
        return

    header    = f"⚠️ CC Pulse — {len(alerts)} Keyword Alert{'s' if len(alerts) != 1 else ''}\n"
    body_text = (
        header
        + "\n\n---\n".join(_format_alert_lines(alerts))
        + "\n\nDashboard: https://kr15tyk.github.io/CC-pulse/cc_dashboard.html"
    )

    payload = json.dumps({"text": body_text}).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            log.info("[Webhook] Alert sent (HTTP %d).", resp.status)
    except urllib.error.URLError as exc:
        log.warning("[Webhook] Failed to send message: %s", exc)


# ---------------------------------------------------------------------------
# Email HTML helpers
# ---------------------------------------------------------------------------

def _esc(s) -> str:
    """HTML-escape a scraped/vendor-controlled value for safe embedding in
    notification HTML. Neutralises markup injection (e.g. a product name or
    RSS title containing <script> or "-breakout into an attribute)."""
    return html.escape("" if s is None else str(s), quote=True)


def _safe_url(u) -> str:
    """Return an escaped URL only if it uses a safe scheme, else ''.
    Blocks javascript:/data:/vbscript: hrefs that survive plain escaping
    because they contain no HTML metacharacters."""
    u = ("" if u is None else str(u)).strip()
    low = u.lower()
    if low.startswith(("http://", "https://", "mailto:")) or u.startswith("/"):
        return html.escape(u, quote=True)
    return ""


def _link(url, title) -> str:
    """Build an anchor from untrusted url+title, escaping both. Falls back to
    escaped title text when the URL is missing or uses an unsafe scheme."""
    safe = _safe_url(url)
    return f'<a href="{safe}">{_esc(title)}</a>' if safe else _esc(title)


def _row(label: str, content: str, color: str = "#22D3EE", bg: str = "#12102E") -> str:
    return (
        f'<tr style="border-bottom:1px solid #312E81">'
        f'<td style="width:90px"><span style="background:{bg};color:{color};'
        f'padding:2px 8px;border-radius:4px;font-size:11px;font-weight:700;font-family:Courier New,monospace">'
        f'{label}</span></td><td style="color:#E0E7FF">{content}</td></tr>'
    )


def _section(title: str, rows: list[str]) -> str:
    if not rows:
        return ""
    body = "".join(rows)
    return (
        f'<h3 style="color:#60A5FA;border-bottom:1px solid #3730A3;font-family:Courier New,monospace;letter-spacing:0.08em;padding-bottom:4px;margin-top:24px">{title}</h3>'
        f'<table width="100%" cellpadding="6" cellspacing="0" '
        f'style="border-collapse:collapse;font-size:13px;background:#12102E;border:1px solid #312E81">{body}</table>'
    )


# ---------------------------------------------------------------------------
# Email builder
# ---------------------------------------------------------------------------

def build_email_html(weekly_diff: dict) -> str:
    now  = datetime.now(ET)
    date = now.strftime("%B %d, %Y")
    date_str = date  # fix: alert branch referenced undefined date_str (latent NameError)
    parts: list[str] = []

    # ── Keyword alerts (top, sorted by tier) ──────────────────────────────
    alerts = weekly_diff.get("alerts", [])
    if alerts:
        alert_rows = []
        for a in sorted(alerts, key=lambda x: (_alert_tier(x), alerts.index(x))):
            kws    = _esc(", ".join(a.get("matched_keywords", [])))
            title  = _esc(a.get("title", ""))
            detail = _esc(a.get("detail", ""))
            url    = _safe_url(a.get("url", ""))
            kind   = a.get("kind", "")
            src    = _esc(a.get("source", "ALERT"))
            tier   = _alert_tier(a)
            cisco_badge = (
                '<span style="background:#3B82F6;color:#fff;padding:1px 6px;'
                'border-radius:3px;font-size:10px;margin-right:4px">CISCO</span>'
                if tier == 1 else ""
            )
            kind_badge = (
                f'<span style="background:#6366F1;color:#fff;padding:1px 6px;'
                f'border-radius:3px;font-size:10px;margin-right:4px">{_kind_label(kind)}</span>'
                if kind else ""
            )
            title_html  = (
                f'<a href="{url}" style="color:#60A5FA;font-weight:700">{title}</a>'
                if url else f"<b>{title}</b>"
            )
            detail_html = (
                f'<div style="font-size:11px;margin-top:3px;opacity:0.85">{detail}</div>'
                if detail else ""
            )
            blurb      = _describe_change(kind, src)
            blurb_html = (
                f'<div style="font-size:11px;margin-top:3px;color:#93C5FD;font-style:italic">'
                f'\U0001f4ac {blurb}</div>'
            )
            kw_html = f'<div style="font-size:11px;margin-top:2px;opacity:0.75">\U0001f511 {kws}</div>'
            row_bg  = "#1E1B4B" if tier == 1 else "#12102E"
            alert_rows.append(
                 _row(src[:14], cisco_badge + kind_badge + title_html + detail_html + blurb_html + kw_html,
                     "#ffffff", row_bg)
            )
        parts.append(_section("⚠️ Keyword Alerts — Source, Detail & Links", alert_rows))

    # ── NIAP PPs ───────────────────────────────────────────────────────────
    pp   = weekly_diff.get("niap", {}).get("pps", {})
    rows: list[str] = []
    for p in pp.get("added", []):
        rows.append(_row("NEW", f"<b>{_esc(p.get('pp_short_name',''))}</b> - {_esc(p.get('pp_name',''))}"))
    for p in pp.get("removed", []):
        rows.append(_row("REMOVED", f"<b>{_esc(p.get('pp_short_name',''))}</b>", "#F87171", "#1E1B4B"))
    for p in pp.get("sunset_changes", []):
        rows.append(_row("SUNSET",
            f"<b>{_esc(p.get('pp_short_name',''))}</b> - Sunset: {_esc(p.get('new_sunset','')[:10])}",
            "#FBBF24", "#1E1B4B"))
    parts.append(_section("NIAP - Protection Profiles", rows))

    # ── NIAP TDs ───────────────────────────────────────────────────────────
    td   = weekly_diff.get("niap", {}).get("tds", {})
    rows = []
    for t in td.get("added", []):
        rows.append(_row("NEW TD", f"<b>{_esc(t.get('identifier',''))}</b> - {_esc(t.get('title',''))}"))
    for t in td.get("removed", []):
        rows.append(_row("REMOVED", f"<b>{_esc(t.get('identifier',''))}</b>", "#F87171", "#1E1B4B"))
    parts.append(_section("NIAP - Technical Decisions", rows))

    # ── Cisco NDcPP ────────────────────────────────────────────────────────
    cn   = weekly_diff.get("niap", {}).get("cisco_ndcpp", {})
    rows = []
    for p in cn.get("added", []):
        rows.append(_row("CERTIFIED",
            f"<b>{_esc(p.get('product_name',''))}</b> ({_esc(p.get('vendor_id_name',''))})"))
    for p in cn.get("newly_archived", []):
        rows.append(_row("ARCHIVED", f"<b>{_esc(p.get('product_name',''))}</b>", "#FBBF24", "#1E1B4B"))
    for p in cn.get("removed", []):
        rows.append(_row("REMOVED", f"<b>{_esc(p.get('product_name',''))}</b>", "#F87171", "#1E1B4B"))
    parts.append(_section("Cisco NDcPP PCL Changes", rows))

    # ── NIAP announcements, events, and policy letters ─────────────────────
    rows = []
    niap_content = weekly_diff.get("niap", {})
    for section, kinds in (
        ("news", ("added", "revised", "deactivated", "reactivated", "removed")),
        ("events", ("added", "revised", "deactivated", "reactivated", "removed")),
        ("policies", ("added", "revised", "archived", "reactivated", "removed")),
    ):
        for kind in kinds:
            for item in niap_content.get(section, {}).get(kind, []):
                category = item.get("_category") or section.rstrip("s").upper()
                link = item.get("url", "") or item.get("link", "")
                title = (
                    item.get("title") or item.get("policy_title")
                    or item.get("name") or f"NIAP {section.rstrip('s')}"
                )
                txt = _link(link, title)
                rows.append(_row(f"{kind.upper()} {category}", txt, "#60A5FA", "#12102E"))
    parts.append(_section("NIAP - Announcements, Events, and Policies", rows))

    # ── CCTL Labs ──────────────────────────────────────────────────────────
    labs = weekly_diff.get("cctl_labs", {})
    rows = []
    for lab, items in labs.items():
        for item in items[:5]:
            link  = item.get("link", "")
            title = item.get("title", "")
            txt   = _link(link, title)
            rows.append(_row(lab[:18], txt, "#60A5FA", "#12102E"))
    parts.append(_section("CCTL Lab Intel", rows))

    # ── CSfC ───────────────────────────────────────────────────────────────
    csfc = weekly_diff.get("csfc", {})
    rows = []
    for cp_name, change in csfc.get("capability_packages", {}).items():
        if change.get("changed"):
            old_lm = change.get("old_last_modified", "")
            new_lm = change.get("new_last_modified", "")
            url    = change.get("url", "")
            detail = f"Last-Modified: {old_lm or '—'} → {new_lm or '—'}"
            txt    = _link(url, cp_name)
            rows.append(_row("CP UPDATE",
                f"<b>{txt}</b><br><small>{_esc(detail)}</small>", "#FBBF24", "#12102E"))
    _csfc_page_urls = {
        "apl":           config.CSFC_PRODUCT_LIST_URL,
        "home":          config.CSFC_BASE + "/Resources/Commercial-Solutions-for-Classified-Program/",
        "cap_packages":  config.CSFC_BASE + "/Resources/Commercial-Solutions-for-Classified-Program/Capability-Packages/",
        "announcements": config.CSFC_BASE + "/Resources/Commercial-Solutions-for-Classified-Program/Announcements/",
    }
    for page_key, page_diff in csfc.get("pages", {}).items():
        for item in page_diff.get("added", [])[:3]:
            page_url = (_csfc_page_urls.get(page_key, config.CSFC_PRODUCT_LIST_URL) if page_key == "apl" else item.get("href") or item.get("link") or _csfc_page_urls.get(page_key, config.CSFC_PRODUCT_LIST_URL))
            label    = "APL" if page_key == "apl" else page_key[:8]
            txt      = _link(page_url, item.get("text", "")[:120])
            rows.append(_row(f"NSA:{label}", txt, "#60A5FA", "#12102E"))
        for item in page_diff.get("updated", [])[:3]:
            item_url = item.get("href") or item.get("link") or _csfc_page_urls.get(page_key, config.CSFC_PRODUCT_LIST_URL)
            label    = "APL" if page_key == "apl" else page_key[:8]
            txt      = _link(item_url, item.get("text", "")[:120])
            rows.append(_row(f"NSA:{label}-UPD", txt, "#FBBF24", "#12102E"))
    for feed_name, items in csfc.get("feeds", {}).items():
        for item in items[:3]:
            link  = item.get("link", "")
            title = item.get("title", "")
            txt   = _link(link, title)
            rows.append(_row("ADVISORY", txt, "#60A5FA", "#12102E"))
    parts.append(_section("CSfC — Capability Packages & APL", rows))

    # ── CC Crypto Catalog ──────────────────────────────────────────────────────
    cc_crypto = weekly_diff.get("cc_crypto", {})
    rows = []
    _cc_urls = {
        "publications": "https://www.commoncriteriaportal.org/cc/index.cfm",
        "news": "https://www.commoncriteriaportal.org/news/index.cfm",
        "communities": "https://www.commoncriteriaportal.org/communities/index.cfm",
    }
    for page_key, page_diff in cc_crypto.get("pages", {}).items():
        if not isinstance(page_diff, dict):
            continue
        for item in page_diff.get("added", [])[:5]:
            page_url = item.get("href") or _cc_urls.get(page_key, "https://www.commoncriteriaportal.org/cc/index.cfm")
            txt = _link(page_url, item.get("text", "")[:120])
            label = "CC PUB" if page_key == "publications" else f"CC:{page_key[:6]}"
            rows.append(_row(label, txt, "#60A5FA", "#12102E"))
    parts.append(_section("CC Crypto Catalog & Working Group", rows))

    # ── ND-iTC (NIT RFIs & Allowed-With lists) ────────────────────────────
    nd = weekly_diff.get("nd_itc", {})
    nd_rfis = nd.get("nit_rfis", {})
    nd_awl = nd.get("awl", {})
    rows = []
    for r in nd_rfis.get("added", []):
        txt = _link(r.get("href", ""), r.get("title", ""))
        rows.append(_row("NEW RFI",
            f"<b>{_esc(r.get('rfi_id', ''))}</b> - {txt}"
            f"<br><small>Impact: {_esc(r.get('impact', '') or 'N/A')}</small>"))
    for r in nd_rfis.get("status_changes", []):
        rows.append(_row("RFI STATUS",
            f"<b>{_esc(r.get('rfi_id', ''))}</b> - "
            f"{_esc(r.get('old_status', '?'))} → {_esc(r.get('new_status', '?'))}",
            "#FBBF24", "#12102E"))
    for r in nd_rfis.get("revised", []):
        rows.append(_row("RFI REVISED",
            f"<b>{_esc(r.get('rfi_id', ''))}</b> - {_esc(r.get('title', ''))}",
            "#FBBF24", "#12102E"))
    for r in nd_rfis.get("newly_archived", []):
        rows.append(_row("RFI ARCHIVED",
            f"<b>{_esc(r.get('rfi_id', ''))}</b> - {_esc(r.get('title', ''))}",
            "#F87171", "#1E1B4B"))
    for e in nd_awl.get("added", []):
        rows.append(_row("AWL ADD",
            f"<b>{_esc(e.get('object_id', ''))}</b> - added to "
            f"{_esc(e.get('section', ''))} allowed-with list"))
    for e in nd_awl.get("removed", []):
        rows.append(_row("AWL REMOVE",
            f"<b>{_esc(e.get('object_id', ''))}</b> - removed from "
            f"{_esc(e.get('section', ''))} allowed-with list",
            "#F87171", "#1E1B4B"))
    for e in nd_awl.get("version_changes", []):
        rows.append(_row("AWL VERSION",
            f"<b>{_esc(e.get('object_id', ''))}</b> - "
            f"{_esc(e.get('old_version', '?'))} → {_esc(e.get('new_version', '?'))}",
            "#FBBF24", "#12102E"))
    for e in nd_awl.get("list_updates", []):
        rows.append(_row("AWL UPDATE",
            f"Allowed-with list {_esc(e.get('list', ''))}: "
            f"{_esc(e.get('old_awl_version', '?'))} → {_esc(e.get('awl_version', '?'))}",
            "#FBBF24", "#12102E"))
    parts.append(_section("ND-iTC — NIT RFIs & Allowed-With Lists", rows))

    # Assemble the weekly digest. The alert section (if any) is already the
    # first entry in `parts`; the previous tail here referenced undefined
    # locals (date_str/tier_note/dashboard_link/subject) copied from the
    # immediate-alert sender and never returned, so the weekly email was
    # effectively dead. Build the outer shell and RETURN the HTML;
    # send_weekly_email() is responsible for sending.
    dashboard_link = (
        '<p style="margin-top:20px"><a '
        'href="https://kr15tyk.github.io/CC-pulse/cc_dashboard.html" '
        'style="color:#60A5FA">View full dashboard \u2192</a></p>'
    )
    generated = datetime.now(ET).strftime("%Y-%m-%d %H:%M ET")
    return (
        '<html><body style="font-family:-apple-system,BlinkMacSystemFont,sans-serif;'
        'max-width:720px;margin:0 auto;color:#E0E7FF">'
        '<div style="background:#0B0F1A;color:#FB923C;padding:20px 28px;border-bottom:2px solid #C026D3">'
        '<h1 style="margin:0;font-size:1.4rem;color:#FB923C;font-family:Courier New,monospace;letter-spacing:0.08em">// CC Pulse &#8212; Weekly Digest</h1>'
        f'<p style="margin:4px 0 0;opacity:0.75;font-size:0.85rem">{date}</p>'
        '</div>'
        '<div style="background:#E0E7FF;padding:20px 28px;border:1px solid #3730A3;'
        'border-top:none;border-radius:0 0 8px 8px">'
        + "".join(parts)
        + dashboard_link
        + '<hr style="margin-top:28px;border:none;border-top:1px solid #312E81">'
        f'<p style="color:#6366F1;font-size:0.75rem;margin-top:12px">'
        f'CC Pulse automated monitoring \u2014 weekly digest<br>Generated {generated}</p>'
        '</div></body></html>'
    )


# ---------------------------------------------------------------------------
# Low-level email sender
# ---------------------------------------------------------------------------

def _send_email(subject: str, html: str) -> None:
    """Authenticate and send one HTML email."""
    password = os.environ.get("CC_EMAIL_PASSWORD", config.EMAIL_PASSWORD)
    if not password:
        log.warning("[Email] No password set — skipping email send.")
        return
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = config.EMAIL_FROM
    msg["To"] = ", ".join(config.EMAIL_RECIPIENTS)
    msg.attach(MIMEText(html, "html"))
    log.info("[Email] Sending '%s' to %s...", subject, config.EMAIL_RECIPIENTS)
    try:
        with smtplib.SMTP(config.EMAIL_SMTP_HOST, config.EMAIL_SMTP_PORT) as smtp:
            smtp.ehlo()
            smtp.starttls()
            smtp.login(config.EMAIL_USERNAME, password)
            smtp.sendmail(config.EMAIL_USERNAME, config.EMAIL_RECIPIENTS, msg.as_string())
        log.info("[Email] Sent successfully.")
    except Exception as exc:
        log.error("[Email] Failed: %s", exc)
        raise

# ---------------------------------------------------------------------------
# Public send functions
# ---------------------------------------------------------------------------

def send_weekly_email(weekly_diff: dict) -> None:
    """Build and send the weekly HTML email digest."""
    date_str = datetime.now(ET).strftime("%Y-%m-%d")
    subject = config.EMAIL_SUBJECT.format(date=date_str)
    html = build_email_html(weekly_diff)
    _send_email(subject, html)

def send_alert_email(alerts: list[dict]) -> None:
    """Send an immediate alert email when keyword matches are found."""
    if not alerts:
        return
    date_str = datetime.now(ET).strftime("%Y-%m-%d")
    tier1_count = sum(1 for a in alerts if _alert_tier(a) == 1)
    subject = (
        f"CC Pulse ALERT — {tier1_count} Cisco-relevant + {len(alerts)-tier1_count} other match(es) on {date_str}"
        if tier1_count else
        f"CC Pulse ALERT — {len(alerts)} keyword match(es) on {date_str}"
    )
    rows = []
    for a in sorted(alerts, key=lambda x: (_alert_tier(x), alerts.index(x))):
        kws = _esc(", ".join(a.get("matched_keywords", [])))
        title = _esc(a.get("title", ""))
        detail = _esc(a.get("detail", ""))
        url = _safe_url(a.get("url", ""))
        kind = a.get("kind", "")
        src = _esc(a.get("source", "ALERT"))
        tier = _alert_tier(a)
        cisco_badge = (
            '<span style="background:#3B82F6;color:#fff;padding:1px 6px;'
            'border-radius:3px;font-size:10px;margin-right:4px">CISCO</span>'
            if tier == 1 else ""
        )
        kind_badge = (
            f'<span style="background:#6366F1;color:#fff;padding:1px 6px;'
            f'border-radius:3px;font-size:10px;margin-right:4px">{_kind_label(kind)}</span>'
            if kind else ""
        )
        title_html = (
            f'<a href="{url}" style="color:#60A5FA;font-weight:700">{title}</a>'
            if url else f"<b>{title}</b>"
        )
        detail_html = (
            f'<div style="font-size:11px;margin-top:3px;opacity:0.85">{detail}</div>'
            if detail else ""
        )
        kw_html = f'<div style="font-size:11px;margin-top:2px;opacity:0.75">\U0001f511 {kws}</div>'
        blurb = _describe_change(kind, src)
        blurb_html = (
            f'<div style="font-size:11px;margin-top:3px;color:#93C5FD;font-style:italic">'
            f'\U0001f4ac {blurb}</div>'
        )
        row_bg = "#1E1B4B" if tier == 1 else "#12102E"
        rows.append(
            _row(src[:14], cisco_badge + kind_badge + title_html + detail_html + blurb_html + kw_html,
                 "#ffffff", row_bg)
        )
    tier_note = (
        f'<p style="margin:6px 0 0;font-size:0.8rem;opacity:0.85">'
        f'\U0001f535 {tier1_count} Cisco-relevant \u00b7 '
        f'\U0001f4d0 {sum(1 for a in alerts if _alert_tier(a)==2)} standards \u00b7 '
        f'\U0001f4cb {sum(1 for a in alerts if _alert_tier(a)==3)} general</p>'
    ) if tier1_count else ""
    dashboard_link = (
        '<p style="margin-top:16px">'
        '<a href="https://kr15tyk.github.io/CC-pulse/cc_dashboard.html" '
        'style="background:#0B0F1A;color:#E0E7FF;padding:8px 16px;'
        'border-radius:4px;text-decoration:none;font-size:0.85rem">'
        '&#128202; View Full Dashboard</a></p>'
    )
    body = (
        '<div style="background:#12102E;color:#FB923C;padding:14px 18px;border:1px solid #C026D3;'
        'border-radius:6px;margin-bottom:8px">'
        f'<b style="font-size:1rem">&#9888; {len(alerts)} KEYWORD ALERT(S) DETECTED</b>'
        f'<p style="margin:4px 0 0;font-size:0.85rem;opacity:0.85">'
        f'{date_str} — immediate notification</p>'
        f'{tier_note}'
        '</div>'
        + _section("Keyword Matches — Source, Detail & Links", rows)
        + dashboard_link
    )
    generated = datetime.now(ET).strftime("%Y-%m-%d %H:%M ET")
    html = (
        '<html><body style="font-family:-apple-system,BlinkMacSystemFont,sans-serif;'
        'max-width:720px;margin:0 auto;color:#E0E7FF">'
        '<div style="background:#0B0F1A;color:#FB923C;padding:20px 28px;border-bottom:2px solid #C026D3">'
        '<h1 style="margin:0;font-size:1.4rem;color:#FB923C;font-family:Courier New,monospace;'
        'letter-spacing:0.08em">// CC Pulse &#8212; Immediate Alert</h1>'
        f'<p style="margin:4px 0 0;opacity:0.75;font-size:0.85rem">{date_str}</p>'
        '</div>'
        '<div style="background:#E0E7FF;padding:20px 28px;border:1px solid #3730A3;'
        'border-top:none;border-radius:0 0 8px 8px">'
        f'{body}'
        '<hr style="margin-top:28px;border:none;border-top:1px solid #312E81">'
        f'<p style="color:#6366F1;font-size:0.75rem;margin-top:12px">'
        f'CC Pulse automated monitoring — immediate alert<br>Generated {generated}</p>'
        '</div></body></html>'
    )
    _send_email(subject, html)

# ---------------------------------------------------------------------------
# Cisco NDcPP PCL certification celebration
# ---------------------------------------------------------------------------

# Curated list of celebration meme image URLs (static imgflip, no API key needed).
# Webex renders inline images from public HTTPS URLs in markdown messages.
_CELEBRATION_MEMES = [
    # Success Kid
    "https://i.imgflip.com/1bhk.jpg",
    # Leonardo DiCaprio Cheers
    "https://i.imgflip.com/39t1o.jpg",
    # Oprah You Get A
    "https://i.imgflip.com/gtj5t.jpg",
    # Blinking White Guy (approval)
    "https://i.imgflip.com/2miy4p.jpg",
    # Excited screaming Kermit
    "https://i.imgflip.com/2gnnjh.jpg",
    # The Rock raising eyebrow
    "https://i.imgflip.com/grr.jpg",
    # We Did It — Dora
    "https://i.imgflip.com/1c1uej.jpg",
    # Ancient Aliens (always a classic)
    "https://i.imgflip.com/26am.jpg",
]


def _cert_meme_url() -> str:
    """Return a pseudo-random celebration meme URL using the current minute as seed."""
    import time
    return _CELEBRATION_MEMES[int(time.time() / 60) % len(_CELEBRATION_MEMES)]


# Per-source metadata for Cisco certification notifications. The celebration
# Webex message and email were originally written for NIAP PCL product dicts
# only, but main._fire_alerts() also routes CSfC, NATO NIAPCL, and EUCC
# additions through them. Each source scrapes a different record shape, so
# every notification must say which registry it came from and link to it.
CERT_SOURCES: dict[str, dict[str, str]] = {
    "niap": {
        "program":        "NDcPP PCL",
        "list_name":      "NIAP Certified Products List",
        "added_to":       "NIAP Validated Products List under the NDcPP program",
        "registry_url":   "https://www.niap-ccevs.org/products",
        "link_label":     "View on NIAP PCL",
    },
    "csfc": {
        "program":        "CSfC Components List",
        "list_name":      "NSA CSfC Components List",
        "added_to":       "NSA Commercial Solutions for Classified (CSfC) Components List",
        "registry_url":   config.CSFC_PRODUCT_LIST_URL,
        "link_label":     "View on CSfC Components List",
    },
    "nato": {
        "program":        "NATO NIAPCL",
        "list_name":      "NATO Information Assurance Product Catalogue",
        "added_to":       "NATO Information Assurance Product Catalogue (NIAPCL)",
        "registry_url":   config.NATO_NIAPCL_URL,
        "link_label":     "View on NATO NIAPCL",
    },
    "eucc": {
        "program":        "EUCC",
        "list_name":      "EUCC Certified Products List (ENISA)",
        "added_to":       "EUCC certified products list published by ENISA",
        "registry_url":   config.EUCC_CERTIFICATES_URL,
        "link_label":     "View EUCC Certificate",
    },
}


def _normalize_cert_record(product: dict, source: str) -> dict:
    """Map a source-specific record onto the canonical NIAP-style keys.

    Record shapes per source:
      niap: product_id, product_name, vendor_id_name, certification_date,
            sunset_date, assigned_lab_name, submitting_country_id_name,
            protection_profiles[{pp_short_name}]
      csfc: keyword-alert dicts — title, url, detail
      nato: name, manufacturer, category, link, raw_text
      eucc: name (card title), text, href, cert_date, description

    Returns a dict with product_name, vendor, cert_date, sunset_date, lab,
    country, pp_names, detail, and url (the product's certification page,
    falling back to the source registry).
    """
    meta = CERT_SOURCES.get(source, CERT_SOURCES["niap"])
    if source == "niap":
        pid = product.get("product_id", "")
        url = (
            f"https://www.niap-ccevs.org/products/{pid}"
            if pid else meta["registry_url"]
        )
    else:
        url = (
            product.get("url") or product.get("href")
            or product.get("link") or meta["registry_url"]
        )
    pps = product.get("protection_profiles") or []
    return {
        "product_name": (
            product.get("product_name") or product.get("name")
            or product.get("title") or (product.get("raw_text") or "")[:120]
            or "Unknown product"
        ),
        "vendor": (
            product.get("vendor_id_name") or product.get("manufacturer")
            or "Cisco"
        ),
        "cert_date": (
            product.get("certification_date") or product.get("cert_date") or ""
        )[:10],
        "sunset_date": (product.get("sunset_date") or "")[:10],
        "lab":       product.get("assigned_lab_name") or "",
        "country":   product.get("submitting_country_id_name") or "",
        "pp_names":  ", ".join(
            p.get("pp_short_name", "") for p in pps if p.get("pp_short_name")
        ),
        "detail": (
            product.get("description") or product.get("detail") or ""
        )[:300],
        "url": url,
    }


def _format_cisco_cert_block(product: dict, source: str = "niap") -> str:
    """Format a single Cisco certification into a Webex Markdown block.

    Renders the product name linked to its certification page, then only the
    detail rows the source actually provides (NIAP has the full set; CSfC,
    NATO, and EUCC records are sparser — empty rows are omitted rather than
    shown as blank/N/A).
    """
    r = _normalize_cert_record(product, source)

    rows = [("Vendor", r["vendor"])]
    rows += [
        (label, value) for label, value in (
            ("Certified",           r["cert_date"]),
            ("Valid until",         r["sunset_date"]),
            ("Evaluated against",   r["pp_names"]),
            ("Evaluating lab",      r["lab"]),
            ("Submitting country",  r["country"]),
            ("Details",             r["detail"]),
        ) if value
    ]
    table = "".join(f"| **{label}** | {value} |\n" for label, value in rows)

    return (
        f"### 🎉 [{r['product_name']}]({r['url']})\n"
        f"| Field | Value |\n"
        f"|-------|-------|\n"
        f"{table}"
    )


def send_cisco_cert_celebration(new_certs: list[dict], source: str = "niap") -> None:
    """Post a celebration message to Webex for each new Cisco certification.

    Fires once per daily run for each source with new Cisco entries (NIAP PCL,
    CSfC Components List, NATO NIAPCL, EUCC). Each new cert gets:
      - A header banner with confetti emoji naming the source registry
      - A markdown table with the certificate details the source provides
      - A random celebration meme image
      - A direct link to the product's certification page

    Args:
        new_certs: List of source-shaped record dicts (see _normalize_cert_record).
        source: One of CERT_SOURCES — "niap", "csfc", "nato", "eucc".
    """
    token   = config.WEBEX_BOT_TOKEN
    room_id = config.WEBEX_ROOM_ID
    if not token or not room_id:
        log.debug("[Webex] Bot token or Room ID not configured — skipping celebration.")
        return
    if not new_certs:
        return

    meta      = CERT_SOURCES.get(source, CERT_SOURCES["niap"])
    count     = len(new_certs)
    meme_url  = _cert_meme_url()
    cert_word = "certification" if count == 1 else "certifications"

    header = (
        f"# 🏆 Cisco {meta['program']} — {count} New {cert_word.title()}!\n\n"
        f"🎊 🎊 🎊\n\n"
        f"_CC Pulse detected {count} new Cisco product {cert_word} on the {meta['list_name']}._\n\n"
    )

    cert_blocks = "\n\n---\n\n".join(
        _format_cisco_cert_block(p, source) for p in new_certs
    )

    footer = (
        f"\n\n---\n\n"
        f"![]({meme_url})\n\n"
        f"[Full dashboard](https://kr15tyk.github.io/CC-pulse/cc_dashboard.html)"
    )

    payload = json.dumps({
        "roomId":   room_id,
        "markdown": header + cert_blocks + footer,
    }).encode("utf-8")

    req = urllib.request.Request(
        "https://webexapis.com/v1/messages",
        data=payload,
        headers={
            "Content-Type":  "application/json",
            "Authorization": f"Bearer {token}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            log.info(
                "[Webex] Cisco cert celebration sent for %d product(s) (HTTP %d).",
                count, resp.status,
            )
    except urllib.error.URLError as exc:
        log.warning("[Webex] Failed to send celebration message: %s", exc)

def send_new_tds_webex(new_tds: list[dict]) -> None:
    """Post a Webex notification for every new NIAP Technical Decision.

    Fires unconditionally whenever new TDs are detected in the daily diff,
    regardless of keyword matches.

    Args:
        new_tds: List of TD dicts from diff["niap"]["tds"]["added"].
    """
    token   = config.WEBEX_BOT_TOKEN
    room_id = config.WEBEX_ROOM_ID
    if not token or not room_id:
        log.debug("[Webex] Bot token or Room ID not configured — skipping TD notification.")
        return
    if not new_tds:
        return

    count   = len(new_tds)
    td_word = "Decision" if count == 1 else "Decisions"

    lines = []
    for td in new_tds:
        ident = td.get("identifier", "")
        title = td.get("title", "") or ident
        pps   = td.get("protection_profile", []) or []
        pp_names = ", ".join(pp.get("pp_short_name", "") for pp in pps[:3] if pp.get("pp_short_name"))
        if len(pps) > 3:
            pp_names += f" +{len(pps) - 3} more"
        url = f"https://www.niap-ccevs.org/technical-decisions/{ident}" if ident else "https://www.niap-ccevs.org/technical-decisions"
        line = f"**[NEW TD]** [{ident} — {title}]({url})"
        if pp_names:
            line += f"\n ↳ Applies to: {pp_names}"
        lines.append(line)

    body = "\n\n---\n".join(lines)
    header = (
        f"## 📋 NIAP — {count} New Technical {td_word}\n"
        f"_CC Pulse detected {count} new TD{'s' if count != 1 else ''} on the NIAP site._\n"
    )
    footer = "\n\n[View full dashboard](https://kr15tyk.github.io/CC-pulse/cc_dashboard.html)"

    payload = json.dumps({
        "roomId":   room_id,
        "markdown": header + body + footer,
    }).encode("utf-8")

    req = urllib.request.Request(
        "https://webexapis.com/v1/messages",
        data=payload,
        headers={
            "Content-Type":  "application/json",
            "Authorization": f"Bearer {token}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            log.info("[Webex] New TDs notification sent for %d TD(s) (HTTP %d).", count, resp.status)
    except urllib.error.URLError as exc:
        log.warning("[Webex] Failed to send new TDs notification: %s", exc)



def send_new_pps_webex(new_pps: list[dict], pp_sunsets: list[dict]) -> None:
    """Post a Webex notification for new NIAP Protection Profiles and sunset changes.

    Fires unconditionally whenever new PPs are added or PP sunset dates change
    in the daily diff, regardless of keyword matches.

    Args:
        new_pps:    List of PP dicts from diff["niap"]["pps"]["added"].
        pp_sunsets: List of PP dicts from diff["niap"]["pps"]["sunset_changes"].
    """
    token   = config.WEBEX_BOT_TOKEN
    room_id = config.WEBEX_ROOM_ID
    if not token or not room_id:
        log.debug("[Webex] Bot token or Room ID not configured — skipping PP notification.")
        return
    if not new_pps and not pp_sunsets:
        return

    lines = []

    if new_pps:
        lines.append(f"### 📄 {len(new_pps)} New Protection Profile{'s' if len(new_pps) != 1 else ''}")
        for pp in new_pps:
            short = pp.get("pp_short_name", "")
            name  = pp.get("pp_name", "") or short
            tech  = pp.get("tech_type", "")
            date  = (pp.get("pp_date") or "")[:10]
            url   = f"https://www.niap-ccevs.org/protectionprofiles/{pp.get('pp_id','')}" if pp.get("pp_id") else "https://www.niap-ccevs.org/protectionprofiles"
            line  = f"**[NEW PP]** [{short} — {name}]({url})"
            if tech:
                line += f"\n ↳ Technology: {tech}"
            if date:
                line += f" · Published: {date}"
            lines.append(line)

    if pp_sunsets:
        lines.append(f"### 🌅 {len(pp_sunsets)} PP Sunset Change{'s' if len(pp_sunsets) != 1 else ''}")
        for pp in pp_sunsets:
            short      = pp.get("pp_short_name", "")
            new_sunset = (pp.get("new_sunset") or pp.get("sunset_date") or "")[:10]
            url        = f"https://www.niap-ccevs.org/protectionprofiles/{pp.get('pp_id','')}" if pp.get("pp_id") else "https://www.niap-ccevs.org/protectionprofiles"
            line       = f"**[PP SUNSET]** [{short}]({url}) — Sunset date: {new_sunset}"
            lines.append(line)

    total = len(new_pps) + len(pp_sunsets)
    header = (
        f"## 📋 NIAP — {total} Protection Profile Update{'s' if total != 1 else ''}\n"
        f"_CC Pulse detected NIAP Protection Profile changes._\n"
    )
    footer = "\n\n[View NIAP PPs](https://www.niap-ccevs.org/Profile/) · [Full dashboard](https://kr15tyk.github.io/CC-pulse/cc_dashboard.html)"

    body = "\n\n---\n".join(lines)
    payload = json.dumps({
        "roomId": room_id,
        "markdown": header + body + footer,
    }).encode("utf-8")

    req = urllib.request.Request(
        "https://webexapis.com/v1/messages",
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            log.info(
                "[Webex] New PPs notification sent: %d new, %d sunsets (HTTP %d).",
                len(new_pps), len(pp_sunsets), resp.status,
            )
    except urllib.error.URLError as exc:
        log.warning("[Webex] Failed to send new PPs notification: %s", exc)


def send_niap_news_webex(new_news: list[dict]) -> None:
    """Post a Webex notification for genuine NIAP content changes.

    Operational collection failures are intentionally excluded; those use the
    internal source-health email path instead.

    Args:
        new_news: Announcement, event, or policy records annotated with
            ``_change_kind`` and ``_content_type``.
    """
    token = config.WEBEX_BOT_TOKEN
    room_id = config.WEBEX_ROOM_ID
    if not token or not room_id:
        log.debug("[Webex] Bot token or Room ID not configured — skipping NIAP news notification.")
        return
    if not new_news:
        return

    count = len(new_news)
    item_word = "Item" if count == 1 else "Items"

    lines = []
    for item in new_news:
        title = (
            item.get("title", "") or item.get("policy_title", "")
            or item.get("name", "") or "NIAP content"
        )
        content_type = item.get("_content_type", "news").rstrip("s").upper()
        change_kind = item.get("_change_kind", "added").upper()
        fallback_url = (
            "https://www.niap-ccevs.org/policies"
            if item.get("_content_type") == "policies"
            else "https://www.niap-ccevs.org/announcements"
        )
        url = item.get("url", "") or item.get("link", "") or fallback_url
        category = item.get("_category", "") or item.get("category", "NEWS")
        date = (
            item.get("date") or item.get("published") or item.get("posted")
            or item.get("policy_date") or ""
        )[:10]
        label = f"[{change_kind} {content_type}]"
        if content_type == "NEWS" and category:
            label = f"[{change_kind} {category.upper()}]"
        link_text = f"[{title}]({url})" if url else title
        line = f"**{label}** {link_text}"
        if date:
            line += f" _({date})_"
        lines.append(line)

    header = (
        f"## 📰 NIAP — {count} Content {item_word}\n"
        f"_CC Pulse detected changes to NIAP announcements, events, or policies._\n"
    )
    footer = (
        "\n\n[View NIAP Announcements](https://www.niap-ccevs.org/announcements)"
        " · [View NIAP Policies](https://www.niap-ccevs.org/policies)"
        " · [Full dashboard](https://kr15tyk.github.io/CC-pulse/cc_dashboard.html)"
    )

    body = "\n\n---\n".join(lines)
    payload = json.dumps({
        "roomId": room_id,
        "markdown": header + body + footer,
    }).encode("utf-8")

    req = urllib.request.Request(
        "https://webexapis.com/v1/messages",
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            log.info("[Webex] NIAP content notification sent for %d item(s) (HTTP %d).", count, resp.status)
    except urllib.error.URLError as exc:
        log.warning("[Webex] Failed to send NIAP news notification: %s", exc)


def send_cisco_cert_email(new_certs: list[dict], source: str = "niap") -> None:
    """Send a dedicated celebration email for new Cisco certifications.

    Fires once per daily run alongside send_cisco_cert_celebration() (Webex),
    for each source with new Cisco entries (NIAP PCL, CSfC Components List,
    NATO NIAPCL, EUCC). Each certification gets an HTML block matching the
    Webex card: product name linked to its certification page, plus whichever
    detail fields the source provides (empty rows are omitted).

    Args:
        new_certs: List of source-shaped record dicts (see _normalize_cert_record).
        source: One of CERT_SOURCES \u2014 "niap", "csfc", "nato", "eucc".
    """
    if not new_certs:
        return

    meta      = CERT_SOURCES.get(source, CERT_SOURCES["niap"])
    date_str  = datetime.now(ET).strftime("%Y-%m-%d")
    count     = len(new_certs)
    cert_word = "Certification" if count == 1 else "Certifications"
    subject   = (
        f"\U0001f3c6 CC Pulse \u2014 {count} New Cisco "
        f"{meta['program']} {cert_word} on {date_str}"
    )

    # Build one detail block per certified product
    cert_blocks = []
    for p in new_certs:
        r        = _normalize_cert_record(p, source)
        name     = _esc(r["product_name"])
        cert_url = _safe_url(r["url"])

        rows = [("Vendor", _esc(r["vendor"]))]
        rows += [
            (label, _esc(value)) for label, value in (
                ("Certified",          r["cert_date"]),
                ("Valid until",        r["sunset_date"]),
                ("Evaluated against",  r["pp_names"]),
                ("Evaluating lab",     r["lab"]),
                ("Submitting country", r["country"]),
                ("Details",            r["detail"]),
            ) if value
        ]
        row_html = "".join(
            (
                f'<tr style="background:#1E1B4B">' if i % 2 == 0 else '<tr>'
            )
            + f'<td style="width:160px;font-weight:700;color:#6366F1">{label}</td>'
            + f'<td>{value}</td></tr>'
            for i, (label, value) in enumerate(rows)
        )

        product_title = (
            f'<h3 style="margin:0 0 8px;font-size:1rem;color:#0B0F1A">'
            f'<a href="{cert_url}" style="color:#3B82F6;text-decoration:none">'
            f'\U0001f3c5 {name}</a></h3>'
        )
        detail_table = (
            '<table width="100%" cellpadding="6" cellspacing="0" '
            'style="border-collapse:collapse;font-size:13px;margin-bottom:6px">'
            f'{row_html}'
            '</table>'
        )
        cert_blocks.append(
            '<div style="background:#ffffff;border:1px solid #312E81;border-left:4px solid #3B82F6;'
            'border-radius:4px;padding:14px 16px;margin-bottom:16px">'
            + product_title + detail_table +
            f'<p style="margin:6px 0 0;font-size:12px">'
            f'<a href="{cert_url}" style="color:#60A5FA">{meta["link_label"]} \u2192</a></p>'
            '</div>'
        )

    certs_html     = "\n".join(cert_blocks)
    dashboard_link = (
        '<p style="margin-top:20px">'
        '<a href="https://kr15tyk.github.io/CC-pulse/cc_dashboard.html" '
        'style="background:#0B0F1A;color:#E0E7FF;padding:8px 16px;'
        'border-radius:4px;text-decoration:none;font-size:0.85rem">'
        '\U0001f4ca Full Dashboard</a>'
        '</p>'
    )

    generated = datetime.now(ET).strftime("%Y-%m-%d %H:%M ET")
    html = (
        '<html><body style="font-family:-apple-system,BlinkMacSystemFont,sans-serif;'
        'max-width:720px;margin:0 auto;color:#E0E7FF">'

        # Header banner
        '<div style="background:linear-gradient(135deg,#0B0F1A,#1E1B4B);color:#E0E7FF;'
        'padding:24px 28px;border-radius:8px 8px 0 0">'
        f'<div style="font-size:2rem;margin-bottom:6px">\U0001f3c6</div>'
        f'<h1 style="margin:0;font-size:1.4rem">Cisco {meta["program"]} \u2014 {count} New {cert_word}</h1>'
        f'<p style="margin:6px 0 0;opacity:0.8;font-size:0.85rem">{date_str} \u2014 {meta["list_name"]}</p>'
        '</div>'

        # Body
        '<div style="background:#12102E;padding:20px 28px;border:1px solid #312E81;'
        'border-top:none;border-radius:0 0 8px 8px">'
        f'<p style="color:#6366F1;font-size:0.9rem;margin:0 0 16px">'
        f'The following Cisco product{"s have" if count > 1 else " has"} been added to the '
        f'{meta["added_to"]}.</p>'
        f'{certs_html}'
        f'{dashboard_link}'
        '<hr style="margin-top:28px;border:none;border-top:1px solid #312E81">'
        f'<p style="color:#6366F1;font-size:0.75rem;margin-top:12px">'
        f'CC Pulse automated monitoring \u2014 Cisco {meta["program"]} alert<br>Generated {generated}</p>'
        '</div>'
        '</body></html>'
    )

    _send_email(subject, html)


# ── README / Pinned message ────────────────────────────────────────────────

def send_readme_message() -> None:
    """Post the CC Pulse README to the Webex space as a pinnable info message.

    Call via:  python main.py --readme
    Then manually pin the resulting message in the Webex space.
    """
    token   = config.WEBEX_BOT_TOKEN
    room_id = config.WEBEX_ROOM_ID
    if not token or not room_id:
        log.debug("[Webex] Bot token or Room ID not configured — skipping README post.")
        return

    msg = (
        "## CC Pulse — What You're Seeing\n\n"
        "**CC Pulse** is an automated monitor that watches government and international "
        "cybersecurity certification portals so you don't have to. Every day it checks for "
        "changes and pushes a summary to this Webex space and the live dashboard.\n\n"
        "**Live dashboard:** https://kr15tyk.github.io/CC-pulse/cc_dashboard.html\n\n"
        "---\n\n"
        "## What Does It Watch?\n\n"
        "| Source | What's tracked |\n"
        "|---|---|\n"
        "| **NIAP** | Certified products (PCL), Protection Profiles, Technical Decisions, CCTLs, news/events |\n"
        "| **CSfC / NSA** | Approved Products List and Component Selection documents |\n"
        "| **NATO NIAPCL** | NATO Information Assurance Product Catalogue — certified products and components |\n"
        "| **EUCC / ENISA** | EU Common Criteria certification scheme — requirements and issued certificates |\n"
        "| **ND-iTC** | NIT RFIs (ND-iTC Technical Decisions — distinct from NIAP TDs) and Allowed-With lists |\n"
        "| **CC Portal** | International CC news, Protection Profiles, and certified products |\n"
        "| **CCTL Labs** | New posts from accredited Common Criteria evaluation labs |\n"
                "Runs automatically every day at **06:00 UTC / 01:00 ET** (adjusts for EDT in summer) and posts here only when something relevant is found.\n\n"
        "---\n\n"
        "## Dashboard Navigation\n\n"
        "The dashboard has three tabs:\n\n"
        "- 🇺🇸 **US (NIAP / CSfC)** — All NIAP, CSfC, CC Portal, and CCTL cards\n"
        "- 🌐 **NATO NIAPCL** — Changes to the NATO Information Assurance Product Catalogue\n"
        "- 🇪🇺 **EU (EUCC)** — Changes to EUCC requirements and certificates from ENISA\n\n"
        "---\n\n"
        "## Cisco Celebration 🏆\n\n"
        "When a new **Cisco product is certified** on NIAP PCL, CSfC, NATO NIAPCL, or EUCC, "
        "the space gets a dedicated celebration message with product details and a rotating image. "
        "A matching email goes to the distribution list at the same time.\n\n"
        "---\n\n"
        "## Questions?\n\n"
        "Click any direct link in an alert — it goes straight to the source page "
        "(NIAP, NSA, NATO, ENISA). No login required."
    )

    payload = {"roomId": room_id, "markdown": msg}
    resp = requests.post(
        "https://webexapis.com/v1/messages",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json=payload,
        timeout=15,
    )
    if resp.ok:
        log.info("[Webex] README posted successfully (id=%s). Pin it in the space.", resp.json().get("id"))
    else:
        log.error("[Webex] Failed to post README: %s %s", resp.status_code, resp.text)


# ---------------------------------------------------------------------------
# Workflow failure notification (issue #20)
# ---------------------------------------------------------------------------

def send_workflow_failure_alert(
    run_id: str = "",
    run_url: str = "",
    workflow: str = "CC Pulse",
    branch: str = "main",
) -> None:
    """Post a Webex alert when the GitHub Actions workflow itself fails.

    Called from a dedicated ``notify_failure`` job in cc_pulse.yml that has
    ``if: failure()`` so it only fires when an upstream job errored or was
    cancelled. The message is intentionally minimal — just enough context
    to let an engineer know a run was skipped and where to look.

    Args:
        run_id:   GitHub Actions run ID (used to build the run URL if
                  ``run_url`` is not supplied).
        run_url:  Direct URL to the failed Actions run. If blank, a URL is
                  constructed from ``run_id`` and the hard-coded repo path.
        workflow: Human-readable workflow name shown in the message header.
        branch:   Branch the workflow ran on (for context).
    """
    token = config.WEBEX_BOT_TOKEN
    room_id = config.WEBEX_ROOM_ID
    if not token or not room_id:
        log.debug("[Webex] Bot token or Room ID not configured — skipping failure alert.")
        return

    if not run_url and run_id:
        run_url = (
            f"https://github.com/kr15tyk/CC-pulse/actions/runs/{run_id}"
        )

    ts = datetime.now(ET).strftime("%Y-%m-%d %H:%M ET")
    run_link = f"[View failed run]({run_url})" if run_url else "Check the Actions tab for details."

    msg = (
        f"## ⚠️ CC Pulse — Workflow Failure\n"
        f"_{workflow} on `{branch}` failed at {ts}._\n\n"
        f"Today’s snapshot and dashboard were **not** updated. "
        f"No diff or alert emails were sent.\n\n"
        f"{run_link}\n\n"
        f"_This is an automated failure notification from CC Pulse (issue [#20]("
        f"https://github.com/kr15tyk/CC-pulse/issues/20))._"
    )

    payload = json.dumps({"roomId": room_id, "markdown": msg}).encode("utf-8")
    req = urllib.request.Request(
        "https://webexapis.com/v1/messages",
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            log.info("[Webex] Workflow failure alert sent (HTTP %d).", resp.status)
    except urllib.error.URLError as exc:
        log.warning("[Webex] Failed to send workflow failure alert: %s", exc)


    # -- Email failure alert -------------------------------------------------------
    subject = f"⚠️ CC Pulse — Workflow Failure ({branch})"
    ts_plain = datetime.now(ET).strftime("%Y-%m-%d %H:%M ET")
    run_link_html = (
        f'<a href="{run_url}">View failed run →</a>' if run_url
        else "Check the Actions tab for details."
    )
    html = f"""
<html><body style="font-family:sans-serif;color:#222;max-width:600px;margin:auto">
<h2 style="color:#c0392b">⚠️ CC Pulse — Workflow Failure</h2>
<p><strong>{workflow}</strong> on <code>{branch}</code> failed at {ts_plain}.</p>
<p>Today's snapshot and dashboard were <strong>not</strong> updated.
No diff or alert emails were sent.</p>
<p>{run_link_html}</p>
<hr style="border:none;border-top:1px solid #eee">
<p style="font-size:11px;color:#999">Automated failure notification from CC Pulse.</p>
</body></html>"""
    _send_email(subject, html)


def send_source_health_email(source_health: dict[str, dict]) -> None:
    """Email newly failed or persistently stale source details to operators."""
    if not source_health:
        return

    html_rows = []
    for source, health in sorted(source_health.items()):
        label = health.get("label") or source.replace("_", " ").title()
        status = health.get("status", "failed").upper()
        failures = health.get("consecutive_failures", 1)
        detail = health.get("detail", "collector returned unusable data")
        fallback = " Last-known-good data is being retained." if health.get("using_last_known_good") else ""
        html_rows.append(
            f"<li><strong>{label}</strong> — {status} for {failures} run(s): "
            f"{detail}.{fallback}</li>"
        )

    subject = f"⚠️ CC Pulse — {len(source_health)} source health issue(s)"
    html = f"""
<html><body style="font-family:sans-serif;color:#222;max-width:680px;margin:auto">
<h2 style="color:#c0392b">⚠️ CC Pulse — Source Health Alert</h2>
<p>The run completed, but these monitored sources returned unusable data:</p>
<ul>{''.join(html_rows)}</ul>
<p><a href="https://kr15tyk.github.io/CC-pulse/cc_dashboard.html">View dashboard →</a></p>
<hr style="border:none;border-top:1px solid #eee">
<p style="font-size:11px;color:#999">Escalations fire on the third failed run, then every seventh run.</p>
</body></html>"""
    _send_email(subject, html)


def send_daily_status_email(diff: dict, run_date: str = "") -> None:
    """Send a brief daily status email summarising what CC Pulse found today.

    Always fires after every successful scheduled merge run so you have a
    heartbeat confirmation even on quiet days when no changes were detected.
    Suppressed when DRY_RUN is set.

    Args:
        diff:      The computed diff dict from differ.compute_diff().
        run_date:  ISO date string (YYYY-MM-DD) for the subject line.
                   Defaults to today in ET if not supplied.
    """
    import config as _cfg
    if _cfg.DRY_RUN:
        log.info("[DRY RUN] Daily status email suppressed.")
        return

    today_str = run_date or datetime.now(ET).strftime("%Y-%m-%d")
    ts_plain  = datetime.now(ET).strftime("%Y-%m-%d %H:%M ET")

    alerts     = diff.get("alerts", [])
    new_tds    = diff.get("niap", {}).get("tds", {}).get("added", [])
    new_certs  = diff.get("niap", {}).get("cisco_ndcpp", {}).get("added", [])
    nato_adds  = diff.get("nato", {}).get("cisco_added", [])
    eucc_adds  = diff.get("eucc", {}).get("cisco_added", [])

    cc_crypto_new = sum(
        len(v.get("added", [])) for v in diff.get("cc_crypto", {}).get("pages", {}).values()
        if isinstance(v, dict)
    )
    niap_pp_changes = len(diff.get("niap", {}).get("pps", {}).get("added", [])) + \
                      len(diff.get("niap", {}).get("pps", {}).get("sunset_changes", []))
    total_changes = len(alerts) + len(new_tds) + len(new_certs) + len(nato_adds) + len(eucc_adds) + cc_crypto_new + niap_pp_changes
    unhealthy_sources = {
        source: health for source, health in diff.get("source_health", {}).items()
        if health.get("status") in ("stale", "failed")
    }

    if unhealthy_sources:
        status_icon = "⚠️"
        status_label = f"{len(unhealthy_sources)} source(s) degraded"
        status_color = "#DC2626"
        health_lines = []
        for source, health in sorted(unhealthy_sources.items()):
            label = health.get("label") or source.replace("_", " ").title()
            fallback = " (last-known-good data retained)" if health.get("using_last_known_good") else ""
            health_lines.append(
                f"{label}: {health.get('status', 'failed')} — {health.get('detail', '')}{fallback}"
            )
        body_detail = (
            "<p style='margin:0 0 12px;color:#94a3b8;font-size:14px;'>"
            "The run completed, but monitoring coverage is degraded:<br><br>"
            + "<br>".join("&bull; " + line for line in health_lines)
            + "</p>"
        )
    elif total_changes == 0:
        status_icon  = "✅"
        status_label = "No changes detected"
        status_color = "#16A34A"
        body_detail  = ("<p style='margin:0 0 12px;color:#94a3b8;font-size:14px;'>"
                        "CC Pulse ran successfully and found no new activity across all"
                        " monitored sources. No alerts or digest updates were sent."
                        "</p>")
    else:
        status_icon  = "⚠️"
        status_label = f"{total_changes} change(s) detected — alerts sent"
        status_color = "#F59E0B"
        lines_detail = []
        if alerts:    lines_detail.append(f"{len(alerts)} keyword alert(s)")
        if new_tds:   lines_detail.append(f"{len(new_tds)} new NIAP TD(s)")
        if new_certs: lines_detail.append(f"{len(new_certs)} new Cisco NDcPP cert(s)")
        if nato_adds: lines_detail.append(f"{len(nato_adds)} new Cisco NATO listing(s)")
        if eucc_adds: lines_detail.append(f"{len(eucc_adds)} new Cisco EUCC cert(s)")
        if cc_crypto_new: lines_detail.append(f"{cc_crypto_new} CC Crypto publication(s)")
        if niap_pp_changes: lines_detail.append(f"{niap_pp_changes} new/updated NIAP PP(s)")
        body_detail  = ("<p style='margin:0 0 12px;color:#94a3b8;font-size:14px;'>"
                        "CC Pulse detected new activity and sent the appropriate alerts:"
                        "<br><br>" + "<br>".join("&bull; " + l for l in lines_detail)
                        + "</p>")

    subject = f"{status_icon} CC Pulse — {today_str} — {status_label}"

    html = f"""
<!DOCTYPE html>
<html><head><meta charset="utf-8"></head>
<body style="background:#0f0e1a;margin:0;padding:0;font-family:'Segoe UI',Arial,sans-serif;">
<table width="100%" cellpadding="0" cellspacing="0" style="background:#0f0e1a;">
  <tr><td align="center" style="padding:32px 16px;">
    <table width="600" cellpadding="0" cellspacing="0" style="background:#1a1830;border-radius:10px;overflow:hidden;max-width:600px;">
      <tr><td style="background:#1e1b3a;padding:24px 32px;border-bottom:1px solid #2d2b50;">
        <span style="font-size:22px;font-weight:700;color:#e2e8f0;">CC Pulse</span>
        <span style="font-size:14px;color:#94a3b8;margin-left:12px;">Daily Status</span>
      </td></tr>
      <tr><td style="padding:28px 32px;">
        <p style="margin:0 0 8px;font-size:26px;">{status_icon}</p>
        <p style="margin:0 0 16px;font-size:20px;font-weight:600;color:{status_color};">{status_label}</p>
        {body_detail}
        <p style="margin:0;color:#64748b;font-size:12px;">Run completed at {ts_plain}</p>
      </td></tr>
      <tr><td style="background:#12102e;padding:16px 32px;border-top:1px solid #2d2b50;">
        <p style="margin:0;color:#475569;font-size:11px;">
          CC Pulse automated monitoring — 
          <a href="https://kr15tyk.github.io/CC-pulse/cc_dashboard.html" style="color:#22d3ee;text-decoration:none;">View Dashboard</a>
        </p>
      </td></tr>
    </table>
  </td></tr>
</table>
</body></html>
"""

    _send_email(subject, html)
