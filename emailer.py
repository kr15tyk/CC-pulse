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
"""
import json
import logging
import os
import smtplib
import urllib.request
import urllib.error
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import datetime, timezone
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

def _row(label: str, content: str, color: str = "#155724", bg: str = "#d4edda") -> str:
    return (
        f'<tr style="border-bottom:1px solid #eee">'
        f'<td style="width:90px"><span style="background:{bg};color:{color};'
        f'padding:2px 8px;border-radius:4px;font-size:11px;font-weight:700">'
        f'{label}</span></td><td>{content}</td></tr>'
    )


def _section(title: str, rows: list[str]) -> str:
    if not rows:
        return ""
    body = "".join(rows)
    return (
        f'<h3 style="color:#003366;border-bottom:2px solid #0057a8;'
        f'padding-bottom:4px;margin-top:24px">{title}</h3>'
        f'<table width="100%" cellpadding="6" cellspacing="0" '
        f'style="border-collapse:collapse;font-size:13px">{body}</table>'
    )


# ---------------------------------------------------------------------------
# Email builder
# ---------------------------------------------------------------------------

def build_email_html(weekly_diff: dict) -> str:
    now  = datetime.now(timezone.utc)
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
                '<span style="background:#1e40af;color:#fff;padding:1px 6px;'
                'border-radius:3px;font-size:10px;margin-right:4px">CISCO</span>'
                if tier == 1 else ""
            )
            kind_badge = (
                f'<span style="background:#374151;color:#fff;padding:1px 6px;'
                f'border-radius:3px;font-size:10px;margin-right:4px">{_kind_label(kind)}</span>'
                if kind else ""
            )
            title_html  = (
                f'<a href="{url}" style="color:#ffffff;font-weight:700">{title}</a>'
                if url else f"<b>{title}</b>"
            )
            detail_html = (
                f'<div style="font-size:11px;margin-top:3px;opacity:0.85">{detail}</div>'
                if detail else ""
            )
            kw_html = f'<div style="font-size:11px;margin-top:2px;opacity:0.75">\U0001f511 {kws}</div>'
            row_bg  = "#8b1a1a" if tier == 1 else "#a82222"
            alert_rows.append(
                _row(src[:14], cisco_badge + kind_badge + title_html + detail_html + kw_html,
                     "#ffffff", row_bg)
            )
        parts.append(_section("⚠️ Keyword Alerts — Source, Detail & Links", alert_rows))

    # ── NIAP PPs ───────────────────────────────────────────────────────────
    pp   = weekly_diff.get("niap", {}).get("pps", {})
    rows: list[str] = []
    for p in pp.get("added", []):
        rows.append(_row("NEW", f"<b>{p.get('pp_short_name','')}</b> - {p.get('pp_name','')}"))
    for p in pp.get("removed", []):
        rows.append(_row("REMOVED", f"<b>{p.get('pp_short_name','')}</b>", "#721c24", "#f8d7da"))
    for p in pp.get("sunset_changes", []):
        rows.append(_row("SUNSET",
            f"<b>{p.get('pp_short_name','')}</b> - Sunset: {p.get('new_sunset','')[:10]}",
            "#856404", "#fff3cd"))
    parts.append(_section("NIAP - Protection Profiles", rows))

    # ── NIAP TDs ───────────────────────────────────────────────────────────
    td   = weekly_diff.get("niap", {}).get("tds", {})
    rows = []
    for t in td.get("added", []):
        rows.append(_row("NEW TD", f"<b>{t.get('identifier','')}</b> - {t.get('title','')}"))
    for t in td.get("removed", []):
        rows.append(_row("REMOVED", f"<b>{t.get('identifier','')}</b>", "#721c24", "#f8d7da"))
    parts.append(_section("NIAP - Technical Decisions", rows))

    # ── Cisco NDcPP ────────────────────────────────────────────────────────
    cn   = weekly_diff.get("niap", {}).get("cisco_ndcpp", {})
    rows = []
    for p in cn.get("added", []):
        rows.append(_row("CERTIFIED",
            f"<b>{p.get('product_name','')}</b> ({p.get('vendor_id_name','')})"))
    for p in cn.get("newly_archived", []):
        rows.append(_row("ARCHIVED", f"<b>{p.get('product_name','')}</b>", "#856404", "#fff3cd"))
    for p in cn.get("removed", []):
        rows.append(_row("REMOVED", f"<b>{p.get('product_name','')}</b>", "#721c24", "#f8d7da"))
    parts.append(_section("Cisco NDcPP PCL Changes", rows))

    # ── NIAP News ──────────────────────────────────────────────────────────
    news = weekly_diff.get("niap", {}).get("news", {})
    rows = []
    for item in news.get("added", []):
        cat   = item.get("_category", "NEWS")
        link  = item.get("url", "")
        title = item.get("title", "")
        txt   = f'<a href="{link}">{title}</a>' if link else title
        rows.append(_row(cat, txt, "#1a4a8a", "#e2eafc"))
    parts.append(_section("NIAP - News and Announcements", rows))

    # ── CCTL Labs ──────────────────────────────────────────────────────────
    labs = weekly_diff.get("cctl_labs", {})
    rows = []
    for lab, items in labs.items():
        for item in items[:5]:
            link  = item.get("link", "")
            title = item.get("title", "")
            txt   = f'<a href="{link}">{title}</a>' if link else title
            rows.append(_row(lab[:18], txt, "#1a4a8a", "#e2eafc"))
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
                f"<b>{txt}</b><br><small>{detail}</small>", "#5a3e00", "#fff3cd"))
    for page_key, page_diff in csfc.get("pages", {}).items():
        for item in page_diff.get("added", [])[:3]:
            rows.append(_row(f"NSA:{page_key[:8]}", item.get("text", "")[:120],
                "#1a4a8a", "#e8f0fe"))
    for feed_name, items in csfc.get("feeds", {}).items():
        for item in items[:3]:
            link  = item.get("link", "")
            title = item.get("title", "")
            txt   = f'<a href="{link}">{title}</a>' if link else title
            rows.append(_row("ADVISORY", txt, "#1a4a8a", "#e2eafc"))
    parts.append(_section("CSfC — Capability Packages & APL", rows))

    # ── CC Crypto Catalog ──────────────────────────────────────────────────
    cc_crypto = weekly_diff.get("cc_crypto", {})
    rows = []
    for doc_name, change in cc_crypto.get("doc_headers", {}).items():
        if change.get("changed"):
            url = change.get("url", "")
            txt = f'<a href="{url}">{doc_name}</a>' if url else doc_name
            rows.append(_row("DOC UPDATE",
                f"<b>{txt}</b> — new version detected", "#5a0000", "#f8d7da"))
    for page_key, page_diff in cc_crypto.get("pages", {}).items():
        for item in page_diff.get("added", [])[:3]:
            rows.append(_row(f"CC:{page_key[:8]}", item.get("text", "")[:120],
                "#1a4a8a", "#e8f0fe"))
    parts.append(_section("CC Crypto Catalog & Working Group", rows))

    # ── NIST CSRC ──────────────────────────────────────────────────────────
    nist = weekly_diff.get("nist", {})
    rows = []
    for doc_name, change in nist.get("doc_headers", {}).items():
        if change.get("changed"):
            url = change.get("url", "")
            txt = f'<a href="{url}">{doc_name}</a>' if url else doc_name
            rows.append(_row("NIST DOC", f"<b>{txt}</b> — revised", "#003366", "#d0e4ff"))
    for feed_name, items in nist.get("feeds", {}).items():
        for item in items[:5]:
            link  = item.get("link", "")
            title = item.get("title", "")
            txt   = f'<a href="{link}">{title}</a>' if link else title
            rows.append(_row("NIST", txt, "#003366", "#d0e4ff"))
    for item in nist.get("pages", {}).get("cmvp_mip", {}).get("added", [])[:5]:
        rows.append(_row("CMVP MIP", item.get("text", "")[:120], "#003366", "#d0e4ff"))
    parts.append(_section("NIST CSRC — Standards, CMVP & PQC", rows))

    body      = "".join(parts) or "<p>No changes detected this week.</p>"
    generated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    return (
        '<html><body style="font-family:-apple-system,BlinkMacSystemFont,sans-serif;'
        'max-width:720px;margin:0 auto;color:#1a1a2e">'
        '<div style="background:#003366;color:white;padding:20px 28px;border-radius:8px 8px 0 0">'
        '<h1 style="margin:0;font-size:1.4rem">&#127760; CC Pulse - Weekly Brief</h1>'
        f'<p style="margin:4px 0 0;opacity:0.75;font-size:0.85rem">Week ending {date}</p>'
        '</div>'
        '<div style="background:white;padding:20px 28px;border:1px solid #d0d7e2;'
        'border-top:none;border-radius:0 0 8px 8px">'
        f'{body}'
        '<hr style="margin-top:28px;border:none;border-top:1px solid #eee">'
        f'<p style="color:#888;font-size:0.75rem;margin-top:12px">'
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
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
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

    date_str   = datetime.now(timezone.utc).strftime("%Y-%m-%d")
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
            '<span style="background:#1e40af;color:#fff;padding:1px 6px;'
            'border-radius:3px;font-size:10px;margin-right:4px">CISCO</span>'
            if tier == 1 else ""
        )
        kind_badge = (
            f'<span style="background:#374151;color:#fff;padding:1px 6px;'
            f'border-radius:3px;font-size:10px;margin-right:4px">{_kind_label(kind)}</span>'
            if kind else ""
        )
        title_html  = (
            f'<a href="{url}" style="color:#ffffff;font-weight:700">{title}</a>'
            if url else f"<b>{title}</b>"
        )
        detail_html = (
            f'<div style="font-size:11px;margin-top:3px;opacity:0.85">{detail}</div>'
            if detail else ""
        )
        kw_html = f'<div style="font-size:11px;margin-top:2px;opacity:0.75">\U0001f511 {kws}</div>'
        row_bg  = "#8b1a1a" if tier == 1 else "#a82222"
        rows.append(
            _row(src[:14], cisco_badge + kind_badge + title_html + detail_html + kw_html,
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
        'style="background:#003366;color:white;padding:8px 16px;'
        'border-radius:4px;text-decoration:none;font-size:0.85rem">'
        '&#128202; View Full Dashboard</a></p>'
    )

    body = (
        '<div style="background:#a82222;color:white;padding:14px 18px;'
        'border-radius:6px;margin-bottom:8px">'
        f'<b style="font-size:1rem">&#9888; {len(alerts)} KEYWORD ALERT(S) DETECTED</b>'
        f'<p style="margin:4px 0 0;font-size:0.85rem;opacity:0.85">'
        f'{date_str} \u2014 immediate notification</p>'
        f'{tier_note}'
        '</div>'
        + _section("Keyword Matches \u2014 Source, Detail & Links", rows)
        + dashboard_link
    )

    generated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    html = (
        '<html><body style="font-family:-apple-system,BlinkMacSystemFont,sans-serif;'
        'max-width:720px;margin:0 auto;color:#1a1a2e">'
        '<div style="background:#a82222;color:white;padding:20px 28px;border-radius:8px 8px 0 0">'
        '<h1 style="margin:0;font-size:1.4rem">&#9888; CC Pulse \u2014 Immediate Alert</h1>'
        f'<p style="margin:4px 0 0;opacity:0.75;font-size:0.85rem">{date_str}</p>'
        '</div>'
        '<div style="background:white;padding:20px 28px;border:1px solid #d0d7e2;'
        'border-top:none;border-radius:0 0 8px 8px">'
        f'{body}'
        '<hr style="margin-top:28px;border:none;border-top:1px solid #eee">'
        f'<p style="color:#888;font-size:0.75rem;margin-top:12px">'
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
    niap_url     = f"https://www.niap-ccevs.org/product/index.cfm?pid={pid}" if pid else "https://www.niap-ccevs.org/"

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
        f"[View Cisco products on NIAP PCL](https://www.niap-ccevs.org/product/index.cfm)"
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

    date_str  = datetime.now(timezone.utc).strftime("%Y-%m-%d")
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
            f"https://www.niap-ccevs.org/product/index.cfm?pid={pid}"
            if pid else "https://www.niap-ccevs.org/"
        )

        product_title = (
            f'<h3 style="margin:0 0 8px;font-size:1rem;color:#1e3a5f">'
            f'<a href="{niap_url}" style="color:#1e40af;text-decoration:none">'
            f'\U0001f3c5 {name}</a></h3>'
        )
        detail_table = (
            '<table width="100%" cellpadding="6" cellspacing="0" '
            'style="border-collapse:collapse;font-size:13px;margin-bottom:6px">'
            f'<tr style="background:#f0f4ff"><td style="width:160px;font-weight:700;color:#374151">Vendor</td><td>{vendor}</td></tr>'
            f'<tr><td style="font-weight:700;color:#374151">Certified</td><td>{cert_date}</td></tr>'
            f'<tr style="background:#f0f4ff"><td style="font-weight:700;color:#374151">Valid until</td><td>{sunset_date}</td></tr>'
            f'<tr><td style="font-weight:700;color:#374151">Evaluated against</td><td>{pp_names}</td></tr>'
            f'<tr style="background:#f0f4ff"><td style="font-weight:700;color:#374151">Evaluating lab</td><td>{lab}</td></tr>'
            f'<tr><td style="font-weight:700;color:#374151">Submitting country</td><td>{country}</td></tr>'
            '</table>'
        )
        cert_blocks.append(
            '<div style="background:#ffffff;border:1px solid #c7d7f0;border-left:4px solid #1e40af;'
            'border-radius:4px;padding:14px 16px;margin-bottom:16px">'
            + product_title + detail_table +
            f'<p style="margin:6px 0 0;font-size:12px">'
            f'<a href="{niap_url}" style="color:#1e40af">View on NIAP PCL \u2192</a></p>'
            '</div>'
        )

    certs_html  = "\n".join(cert_blocks)
    pcl_link    = (
        '<p style="margin-top:20px">'
        '<a href="https://www.niap-ccevs.org/product/index.cfm" '
        'style="background:#1e40af;color:white;padding:8px 16px;'
        'border-radius:4px;text-decoration:none;font-size:0.85rem;margin-right:8px">'
        '\U0001f4cb View Cisco PCL</a>'
        '<a href="https://kr15tyk.github.io/CC-pulse/cc_dashboard.html" '
        'style="background:#003366;color:white;padding:8px 16px;'
        'border-radius:4px;text-decoration:none;font-size:0.85rem">'
        '\U0001f4ca Full Dashboard</a>'
        '</p>'
    )

    generated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    html = (
        '<html><body style="font-family:-apple-system,BlinkMacSystemFont,sans-serif;'
        'max-width:720px;margin:0 auto;color:#1a1a2e">'

        # Header banner
        '<div style="background:linear-gradient(135deg,#1e3a5f,#1e40af);color:white;'
        'padding:24px 28px;border-radius:8px 8px 0 0">'
        f'<div style="font-size:2rem;margin-bottom:6px">\U0001f3c6</div>'
        f'<h1 style="margin:0;font-size:1.4rem">Cisco NDcPP PCL \u2014 {count} New {cert_word}</h1>'
        f'<p style="margin:6px 0 0;opacity:0.8;font-size:0.85rem">{date_str} \u2014 NIAP Certified Products List</p>'
        '</div>'

        # Body
        '<div style="background:#f8faff;padding:20px 28px;border:1px solid #c7d7f0;'
        'border-top:none;border-radius:0 0 8px 8px">'
        f'<p style="color:#374151;font-size:0.9rem;margin:0 0 16px">'
        f'The following Cisco product{"s have" if count > 1 else " has"} been added to the '
        f'NIAP Validated Products List under the NDcPP program.</p>'
        f'{certs_html}'
        f'{pcl_link}'
        '<hr style="margin-top:28px;border:none;border-top:1px solid #dde6f0">'
        f'<p style="color:#888;font-size:0.75rem;margin-top:12px">'
        f'CC Pulse automated monitoring \u2014 Cisco NDcPP alert<br>Generated {generated}</p>'
        '</div>'
        '</body></html>'
    )

    _send_email(subject, html)
