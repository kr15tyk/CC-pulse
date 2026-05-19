"""
emailer.py — Builds and sends CC Pulse email digests.

Features:
  - Keyword alert section at top of email
  - Webex Space notification for immediate keyword alerts
    - Cisco-relevant alerts tagged and sorted to top (Tier 1)
    - Kind label promoted to front of each alert line
    - CSfC Capability Package changes show direct PDF link prominently
    - Tier-sorted output: Cisco/NDcPP > NIST/standards > general
  - Weekly digest covering NIAP, CC Portal, CCTL labs, CSfC, CC Crypto, NIST
  - Immediate alert email (send_alert_email) for same-day keyword matches
  - Structured logging
  - Generic webhook / MS Teams delivery via send_webhook_alert()
- Plain-language change descriptions via _describe_change() (issue #17)
"""
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
EST = timezone(timedelta(hours=-5))
import config

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Cisco-relevance helpers
# ---------------------------------------------------------------------------

# Keywords that indicate an alert is directly relevant to Cisco engineers.
# Covers NDcPP/VPN/WLAN PPs, CSfC programmes Cisco participates in,
# and the crypto/algorithm standards that drive CC cert requirements.
_CISCO_RELEVANT_KEYWORDS = {
    kw.lower() for kw in [
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
        # FIPS standards cited in NDcPP / CSfC
        "FIPS 140-3", "FIPS 186-4", "FIPS 186-5",
        "SP 800-131A",
    ]
}

# Tier 2: standards that drive future cert requirements
_TIER2_KEYWORDS = {
    kw.lower() for kw in [
        "FIPS 203", "FIPS 204", "FIPS 205",
        "SP 800-57", "NIST IR 8547",
        "ML-KEM", "ML-DSA", "SLH-DSA", "post-quantum", "PQC migration",
        "CMVP", "CAVP", "algorithm transition", "CCDB-018",
    ]
}


def _is_cisco_relevant(alert: dict) -> bool:
    """Return True if any matched keyword overlaps with _CISCO_RELEVANT_KEYWORDS."""
    hits = {kw.lower() for kw in alert.get("matched_keywords", [])}
    return bool(hits & _CISCO_RELEVANT_KEYWORDS)


def _alert_tier(alert: dict) -> int:
    """Return sort tier: 1 = Cisco/NDcPP direct, 2 = standards/NIST, 3 = general."""
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
    # NIST / standards
    ("publication",    "NIST"):  "A new or updated NIST cryptography publication has appeared. NIST standards often drive future Common Criteria and CSfC requirements.",
    ("news",           "NIST"):  "New content has appeared on the NIST CSRC website (news, FIPS, CMVP, or post-quantum standards).",
    ("updated",        "NIST"):  "A NIST standards document has been revised. Review the link for what changed.",
    # CSfC
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
        source: The source label (e.g. "NIAP PP", "NIST: fips", "CCTL Labs").

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
    - Alerts sorted by tier: Cisco-relevant first, then NIST/standards, then general.
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
      - Tier 2 (standards/NIST) next
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
        tier_parts.append(f"📐 {tier_counts[2]} standards/NIST")
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
    now  = datetime.now(EST)
    date = now.strftime("%B %d, %Y")
    parts: list[str] = []

    # ── Keyword alerts (top, sorted by tier) ──────────────────────────────
    alerts = weekly_diff.get("alerts", [])
    if alerts:
        alert_rows = []
        for a in sorted(alerts, key=lambda x: (_alert_tier(x), alerts.index(x))):
            kws    = ", ".join(a.get("matched_keywords", []))
            title  = a.get("title", "")
            detail = a.get("detail", "")
            url    = a.get("url", "")
            kind   = a.get("kind", "")
            src    = a.get("source", "ALERT")
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
        rows.append(_row("NEW", f"<b>{p.get('pp_short_name','')}</b> - {p.get('pp_name','')}"))
    for p in pp.get("removed", []):
        rows.append(_row("REMOVED", f"<b>{p.get('pp_short_name','')}</b>", "#F87171", "#1E1B4B"))
    for p in pp.get("sunset_changes", []):
        rows.append(_row("SUNSET",
            f"<b>{p.get('pp_short_name','')}</b> - Sunset: {p.get('new_sunset','')[:10]}",
            "#FBBF24", "#1E1B4B"))
    parts.append(_section("NIAP - Protection Profiles", rows))

    # ── NIAP TDs ───────────────────────────────────────────────────────────
    td   = weekly_diff.get("niap", {}).get("tds", {})
    rows = []
    for t in td.get("added", []):
        rows.append(_row("NEW TD", f"<b>{t.get('identifier','')}</b> - {t.get('title','')}"))
    for t in td.get("removed", []):
        rows.append(_row("REMOVED", f"<b>{t.get('identifier','')}</b>", "#F87171", "#1E1B4B"))
    parts.append(_section("NIAP - Technical Decisions", rows))

    # ── Cisco NDcPP ────────────────────────────────────────────────────────
    cn   = weekly_diff.get("niap", {}).get("cisco_ndcpp", {})
    rows = []
    for p in cn.get("added", []):
        rows.append(_row("CERTIFIED",
            f"<b>{p.get('product_name','')}</b> ({p.get('vendor_id_name','')})"))
    for p in cn.get("newly_archived", []):
        rows.append(_row("ARCHIVED", f"<b>{p.get('product_name','')}</b>", "#FBBF24", "#1E1B4B"))
    for p in cn.get("removed", []):
        rows.append(_row("REMOVED", f"<b>{p.get('product_name','')}</b>", "#F87171", "#1E1B4B"))
    parts.append(_section("Cisco NDcPP PCL Changes", rows))

    # ── NIAP News ──────────────────────────────────────────────────────────
    news = weekly_diff.get("niap", {}).get("news", {})
    rows = []
    for item in news.get("added", []):
        cat   = item.get("_category", "NEWS")
        link  = item.get("url", "")
        title = item.get("title", "")
        txt   = f'<a href="{link}">{title}</a>' if link else title
        rows.append(_row(cat, txt, "#60A5FA", "#12102E"))
    parts.append(_section("NIAP - News and Announcements", rows))

    # ── CCTL Labs ──────────────────────────────────────────────────────────
    labs = weekly_diff.get("cctl_labs", {})
    rows = []
    for lab, items in labs.items():
        for item in items[:5]:
            link  = item.get("link", "")
            title = item.get("title", "")
            txt   = f'<a href="{link}">{title}</a>' if link else title
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
            txt    = f'<a href="{url}">{cp_name}</a>' if url else cp_name
            rows.append(_row("CP UPDATE",
                f"<b>{txt}</b><br><small>{detail}</small>", "#FBBF24", "#12102E"))
    for page_key, page_diff in csfc.get("pages", {}).items():
        for item in page_diff.get("added", [])[:3]:
            rows.append(_row(f"NSA:{page_key[:8]}", item.get("text", "")[:120],
                "#60A5FA", "#12102E"))
    for feed_name, items in csfc.get("feeds", {}).items():
        for item in items[:3]:
            link  = item.get("link", "")
            title = item.get("title", "")
            txt   = f'<a href="{link}">{title}</a>' if link else title
            rows.append(_row("ADVISORY", txt, "#60A5FA", "#12102E"))
    parts.append(_section("CSfC — Capability Packages & APL", rows))

    # ── CC Crypto Catalog ──────────────────────────────────────────────────
    cc_crypto = weekly_diff.get("cc_crypto", {})
    rows = []
    for doc_name, change in cc_crypto.get("doc_headers", {}).items():
        if change.get("changed"):
            url = change.get("url", "")
            txt = f'<a href="{url}">{doc_name}</a>' if url else doc_name
            rows.append(_row("DOC UPDATE",
                f"<b>{txt}</b> — new version detected", "#F87171", "#1E1B4B"))
    for page_key, page_diff in cc_crypto.get("pages", {}).items():
        for item in page_diff.get("added", [])[:3]:
            rows.append(_row(f"CC:{page_key[:8]}", item.get("text", "")[:120],
                "#60A5FA", "#12102E"))
    parts.append(_section("CC Crypto Catalog & Working Group", rows))

    # ── NIST CSRC ──────────────────────────────────────────────────────────
    nist = weekly_diff.get("nist", {})
    rows = []
    for doc_name, change in nist.get("doc_headers", {}).items():
        if change.get("changed"):
            url = change.get("url", "")
            txt = f'<a href="{url}">{doc_name}</a>' if url else doc_name
            rows.append(_row("NIST DOC", f"<b>{txt}</b> — revised", "#22D3EE", "#12102E"))
    for feed_name, items in nist.get("feeds", {}).items():
        for item in items[:5]:
            link  = item.get("link", "")
            title = item.get("title", "")
            txt   = f'<a href="{link}">{title}</a>' if link else title
            rows.append(_row("NIST", txt, "#22D3EE", "#12102E"))
    for item in nist.get("pages", {}).get("cmvp_mip", {}).get("added", [])[:5]:
        rows.append(_row("CMVP MIP", item.get("text", "")[:120], "#22D3EE", "#12102E"))
    parts.append(_section("NIST CSRC — Standards, CMVP & PQC", rows))

    body      = "".join(parts) or "<p>No changes detected this week.</p>"
    generated = datetime.now(EST).strftime("%Y-%m-%d %H:%M EST")
    return (
        '<html><body style="font-family:-apple-system,BlinkMacSystemFont,sans-serif;'
        'max-width:720px;margin:0 auto;color:#E0E7FF">'
        '<div style="background:#0B0F1A;color:#E0E7FF;padding:20px 28px;border-radius:8px 8px 0 0">'
        '<h1 style="margin:0;font-size:1.4rem;color:#60A5FA;font-family:Courier New,monospace;letter-spacing:0.08em">// CC Pulse &#8212; Weekly Brief</h1>'
        f'<p style="margin:4px 0 0;opacity:0.75;font-size:0.85rem">Week ending {date}</p>'
        '</div>'
        '<div style="background:#E0E7FF;padding:20px 28px;border:1px solid #3730A3;'
        'border-top:none;border-radius:0 0 8px 8px">'
        f'{body}'
        '<hr style="margin-top:28px;border:none;border-top:1px solid #312E81">'
        f'<p style="color:#6366F1;font-size:0.75rem;margin-top:12px">'
        f'CC Pulse automated Common Criteria monitoring<br>Generated {generated}</p>'
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
    msg            = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = config.EMAIL_FROM
    msg["To"]      = ", ".join(config.EMAIL_RECIPIENTS)
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
    date_str = datetime.now(EST).strftime("%Y-%m-%d")
    subject  = config.EMAIL_SUBJECT.format(date=date_str)
    html     = build_email_html(weekly_diff)
    _send_email(subject, html)


def send_alert_email(alerts: list[dict]) -> None:
    """Send an immediate alert email when keyword matches are found.

    Alert rows are sorted tier-first (Cisco-relevant at top) with CISCO badge
    and kind label on each row for fast scanning.
    """
    if not alerts:
        return

    date_str   = datetime.now(EST).strftime("%Y-%m-%d")
    tier1_count = sum(1 for a in alerts if _alert_tier(a) == 1)
    subject    = (
        f"CC Pulse ALERT \u2014 {tier1_count} Cisco-relevant + {len(alerts)-tier1_count} other match(es) on {date_str}"
        if tier1_count else
        f"CC Pulse ALERT \u2014 {len(alerts)} keyword match(es) on {date_str}"
    )

    rows = []
    for a in sorted(alerts, key=lambda x: (_alert_tier(x), alerts.index(x))):
        kws    = ", ".join(a.get("matched_keywords", []))
        title  = a.get("title", "")
        detail = a.get("detail", "")
        url    = a.get("url", "")
        kind   = a.get("kind", "")
        src    = a.get("source", "ALERT")
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
        kw_html = f'<div style="font-size:11px;margin-top:2px;opacity:0.75">\U0001f511 {kws}</div>'
        blurb      = _describe_change(kind, src)
        blurb_html = (
            f'<div style="font-size:11px;margin-top:3px;color:#93C5FD;font-style:italic">'
            f'\U0001f4ac {blurb}</div>'
        )
        row_bg  = "#1E1B4B" if tier == 1 else "#12102E"
        rows.append(
            _row(src[:14], cisco_badge + kind_badge + title_html + detail_html + blurb_html + kw_html,
                 "#ffffff", row_bg)
        )

    tier_note = (
        f'<p style="margin:6px 0 0;font-size:0.8rem;opacity:0.85">'
        f'🔵 {tier1_count} Cisco-relevant · '
        f'📐 {sum(1 for a in alerts if _alert_tier(a)==2)} standards/NIST · '
        f'📋 {sum(1 for a in alerts if _alert_tier(a)==3)} general</p>'
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
        f'{date_str} \u2014 immediate notification</p>'
        f'{tier_note}'
        '</div>'
        + _section("Keyword Matches \u2014 Source, Detail & Links", rows)
        + dashboard_link
    )

    generated = datetime.now(EST).strftime("%Y-%m-%d %H:%M EST")
    html = (
        '<html><body style="font-family:-apple-system,BlinkMacSystemFont,sans-serif;'
        'max-width:720px;margin:0 auto;color:#E0E7FF">'
        '<div style="background:#0B0F1A;color:#FB923C;padding:20px 28px;border-bottom:2px solid #C026D3">'
        '<h1 style="margin:0;font-size:1.4rem;color:#FB923C;font-family:Courier New,monospace;letter-spacing:0.08em">// CC Pulse &#8212; Immediate Alert</h1>'
        f'<p style="margin:4px 0 0;opacity:0.75;font-size:0.85rem">{date_str}</p>'
        '</div>'
        '<div style="background:#E0E7FF;padding:20px 28px;border:1px solid #3730A3;'
        'border-top:none;border-radius:0 0 8px 8px">'
        f'{body}'
        '<hr style="margin-top:28px;border:none;border-top:1px solid #312E81">'
        f'<p style="color:#6366F1;font-size:0.75rem;margin-top:12px">'
        f'CC Pulse automated monitoring \u2014 immediate alert<br>Generated {generated}</p>'
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


def _format_cisco_cert_block(product: dict) -> str:
    """Format a single Cisco NDcPP PCL certification into a Webex Markdown block.

    Included fields:
      - Product name (bold, linked to NIAP product page)
      - Vendor
      - Certification date
      - Sunset date
      - Evaluated against (PP short names)
      - Evaluating lab
      - Submitting country
    """
    pid          = product.get("product_id", "")
    name         = product.get("product_name", "Unknown product")
    vendor       = product.get("vendor_id_name", "Cisco")
    cert_date    = (product.get("certification_date") or "")[:10]
    sunset_date  = (product.get("sunset_date") or "")[:10]
    lab          = product.get("assigned_lab_name", "N/A")
    country      = product.get("submitting_country_id_name", "N/A")
    pps          = product.get("protection_profiles", [])
    pp_names     = ", ".join(
        p.get("pp_short_name", "") for p in pps if p.get("pp_short_name")
    ) or "N/A"
    niap_url     = f"https://www.niap-ccevs.org/products/{pid}" if pid else "https://www.niap-ccevs.org/products"

    return (
        f"### 🎉 [{name}]({niap_url})\n"
        f"| Field | Value |\n"
        f"|-------|-------|\n"
        f"| **Vendor** | {vendor} |\n"
        f"| **Certified** | {cert_date} |\n"
        f"| **Valid until** | {sunset_date} |\n"
        f"| **Evaluated against** | {pp_names} |\n"
        f"| **Evaluating lab** | {lab} |\n"
        f"| **Submitting country** | {country} |\n"
    )


def send_cisco_cert_celebration(new_certs: list[dict]) -> None:
    """Post a celebration message to Webex for each new Cisco NDcPP PCL certification.

    Fires once per daily run when differ.diff_niap_pcl_cisco() finds new entries
    in cisco_ndcpp.added. Each new cert gets:
      - A header banner with confetti emoji
      - A markdown table with the full certificate details
      - A random celebration meme image
      - A direct link to the NIAP product page

    Args:
        new_certs: List of product dicts from diff["niap"]["cisco_ndcpp"]["added"].
    """
    token   = config.WEBEX_BOT_TOKEN
    room_id = config.WEBEX_ROOM_ID
    if not token or not room_id:
        log.debug("[Webex] Bot token or Room ID not configured — skipping celebration.")
        return
    if not new_certs:
        return

    count     = len(new_certs)
    meme_url  = _cert_meme_url()
    cert_word = "certification" if count == 1 else "certifications"

    header = (
        f"# 🏆 Cisco NDcPP PCL — {count} New {cert_word.title()}!\n\n"
        f"🎊 🎊 🎊\n\n"
        f"_CC Pulse detected {count} new Cisco product {cert_word} on the NIAP PCL._\n\n"
    )

    cert_blocks = "\n\n---\n\n".join(
        _format_cisco_cert_block(p) for p in new_certs
    )

    footer = (
        f"\n\n---\n\n"
        f"![]({meme_url})\n\n"
        f"[View Cisco products on NIAP PCL](https://www.niap-ccevs.org/products)"
        f" · [Full dashboard](https://kr15tyk.github.io/CC-pulse/cc_dashboard.html)"
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



def send_cisco_cert_email(new_certs: list[dict]) -> None:
    """Send a dedicated celebration email for new Cisco NDcPP PCL certifications.

    Fires once per daily run alongside send_cisco_cert_celebration() (Webex).
    Each certification gets a full-detail HTML row matching the Webex card:
    product name (linked), vendor, cert date, valid-until, evaluated PPs,
    evaluating lab, submitting country.

    Args:
        new_certs: List of product dicts from diff["niap"]["cisco_ndcpp"]["added"].
    """
    if not new_certs:
        return

    date_str  = datetime.now(EST).strftime("%Y-%m-%d")
    count     = len(new_certs)
    cert_word = "Certification" if count == 1 else "Certifications"
    subject   = f"\U0001f3c6 CC Pulse \u2014 {count} New Cisco NDcPP {cert_word} on {date_str}"

    # Build one detail block per certified product
    cert_blocks = []
    for p in new_certs:
        pid         = p.get("product_id", "")
        name        = p.get("product_name", "Unknown product")
        vendor      = p.get("vendor_id_name", "Cisco")
        cert_date   = (p.get("certification_date") or "")[:10]
        sunset_date = (p.get("sunset_date") or "")[:10]
        lab         = p.get("assigned_lab_name", "N/A")
        country     = p.get("submitting_country_id_name", "N/A")
        pps         = p.get("protection_profiles", [])
        pp_names    = ", ".join(
            pp.get("pp_short_name", "") for pp in pps if pp.get("pp_short_name")
        ) or "N/A"
        niap_url    = (
            f"https://www.niap-ccevs.org/products/{pid}"
            if pid else "https://www.niap-ccevs.org/products"
        )

        product_title = (
            f'<h3 style="margin:0 0 8px;font-size:1rem;color:#0B0F1A">'
            f'<a href="{niap_url}" style="color:#3B82F6;text-decoration:none">'
            f'\U0001f3c5 {name}</a></h3>'
        )
        detail_table = (
            '<table width="100%" cellpadding="6" cellspacing="0" '
            'style="border-collapse:collapse;font-size:13px;margin-bottom:6px">'
            f'<tr style="background:#1E1B4B"><td style="width:160px;font-weight:700;color:#6366F1">Vendor</td><td>{vendor}</td></tr>'
            f'<tr><td style="font-weight:700;color:#6366F1">Certified</td><td>{cert_date}</td></tr>'
            f'<tr style="background:#1E1B4B"><td style="font-weight:700;color:#6366F1">Valid until</td><td>{sunset_date}</td></tr>'
            f'<tr><td style="font-weight:700;color:#6366F1">Evaluated against</td><td>{pp_names}</td></tr>'
            f'<tr style="background:#1E1B4B"><td style="font-weight:700;color:#6366F1">Evaluating lab</td><td>{lab}</td></tr>'
            f'<tr><td style="font-weight:700;color:#6366F1">Submitting country</td><td>{country}</td></tr>'
            '</table>'
        )
        cert_blocks.append(
            '<div style="background:#ffffff;border:1px solid #312E81;border-left:4px solid #3B82F6;'
            'border-radius:4px;padding:14px 16px;margin-bottom:16px">'
            + product_title + detail_table +
            f'<p style="margin:6px 0 0;font-size:12px">'
            f'<a href="{niap_url}" style="color:#60A5FA">View on NIAP PCL \u2192</a></p>'
            '</div>'
        )

    certs_html  = "\n".join(cert_blocks)
    pcl_link    = (
        '<p style="margin-top:20px">'
        '<a href="https://www.niap-ccevs.org/products" '
        'style="background:#3B82F6;color:#E0E7FF;padding:8px 16px;'
        'border-radius:4px;text-decoration:none;font-size:0.85rem;margin-right:8px">'
        '\U0001f4cb View Cisco PCL</a>'
        '<a href="https://kr15tyk.github.io/CC-pulse/cc_dashboard.html" '
        'style="background:#0B0F1A;color:#E0E7FF;padding:8px 16px;'
        'border-radius:4px;text-decoration:none;font-size:0.85rem">'
        '\U0001f4ca Full Dashboard</a>'
        '</p>'
    )

    generated = datetime.now(EST).strftime("%Y-%m-%d %H:%M EST")
    html = (
        '<html><body style="font-family:-apple-system,BlinkMacSystemFont,sans-serif;'
        'max-width:720px;margin:0 auto;color:#E0E7FF">'

        # Header banner
        '<div style="background:linear-gradient(135deg,#0B0F1A,#1E1B4B);color:#E0E7FF;'
        'padding:24px 28px;border-radius:8px 8px 0 0">'
        f'<div style="font-size:2rem;margin-bottom:6px">\U0001f3c6</div>'
        f'<h1 style="margin:0;font-size:1.4rem">Cisco NDcPP PCL \u2014 {count} New {cert_word}</h1>'
        f'<p style="margin:6px 0 0;opacity:0.8;font-size:0.85rem">{date_str} \u2014 NIAP Certified Products List</p>'
        '</div>'

        # Body
        '<div style="background:#12102E;padding:20px 28px;border:1px solid #312E81;'
        'border-top:none;border-radius:0 0 8px 8px">'
        f'<p style="color:#6366F1;font-size:0.9rem;margin:0 0 16px">'
        f'The following Cisco product{"s have" if count > 1 else " has"} been added to the '
        f'NIAP Validated Products List under the NDcPP program.</p>'
        f'{certs_html}'
        f'{pcl_link}'
        '<hr style="margin-top:28px;border:none;border-top:1px solid #312E81">'
        f'<p style="color:#6366F1;font-size:0.75rem;margin-top:12px">'
        f'CC Pulse automated monitoring \u2014 Cisco NDcPP alert<br>Generated {generated}</p>'
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
        "| **CC Portal** | International CC news, Protection Profiles, and certified products |\n"
        "| **CCTL Labs** | New posts from accredited Common Criteria evaluation labs |\n"
        "| **NIST CSRC** | Cryptography news, FIPS publications, CMVP, and post-quantum standards |\n\n"
        "Runs automatically every day at **01:00 EST** and posts here only when something relevant is found.\n\n"
        "---\n\n"
        "## Dashboard Navigation\n\n"
        "The dashboard has three tabs:\n\n"
        "- 🇺🇸 **US (NIAP / CSfC / NIST)** — All NIAP, CSfC, NIST, CC Portal, and CCTL cards\n"
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
        "(NIAP, NIST, NSA, NATO, ENISA). No login required."
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
