"""
emailer.py — Builds and sends CC Pulse email digests.

Features:
  - Keyword alert section at top of email
  - Webex Space notification for immediate keyword alerts
  - Weekly digest covering NIAP, CC Portal, CCTL labs, CSfC, CC Crypto, NIST
  - Immediate alert email (send_alert_email) for same-day keyword matches
  - Structured logging
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


# ── Webex notification ────────────────────────────────────────────────────────
def send_webex_alert(alerts: list[dict]) -> None:
    """POST a compact Webex message for high-priority keyword alerts."""
    token = config.WEBEX_BOT_TOKEN
    room_id = config.WEBEX_ROOM_ID
    if not token or not room_id:
        log.debug("[Webex] Bot token or Room ID not configured — skipping.")
        return
    if not alerts:
        return

    lines = ["**CC Pulse Alert** — keyword match(es) detected:"]
    for a in alerts[:10]:
        kws = ", ".join(a.get("matched_keywords", []))
        lines.append(f"- **[{a['source']}]** {a['title']} — _{kws}_")
    if len(alerts) > 10:
        lines.append(f"_...and {len(alerts) - 10} more. Check the dashboard._")

    payload = json.dumps({
        "roomId": room_id,
        "markdown": "\n".join(lines),
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
            log.info("[Webex] Alert sent (HTTP %d).", resp.status)
    except urllib.error.URLError as exc:
        log.warning("[Webex] Failed to send message: %s", exc)


# ── Email HTML helpers ────────────────────────────────────────────────────────
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


# ── Email builder ─────────────────────────────────────────────────────────────
def build_email_html(weekly_diff: dict) -> str:
    now = datetime.now(timezone.utc)
    date = now.strftime("%B %d, %Y")
    parts: list[str] = []

    # ── Keyword alerts (top, red) ─────────────────────────────────────────────
    alerts = weekly_diff.get("alerts", [])
    if alerts:
        alert_rows = []
        for a in alerts:
            kws = ", ".join(a.get("matched_keywords", []))
            alert_rows.append(
                _row(a.get("source", "ALERT")[:14],
                     f"<b>{a.get('title', '')}</b> &mdash; {kws}",
                     "#ffffff", "#a82222")
            )
        parts.append(
            '<div style="background:#a82222;color:white;padding:14px 18px;'
            'border-radius:6px;margin-bottom:8px">'
            f'<b style="font-size:1rem">&#9888; {len(alerts)} KEYWORD ALERT(S)</b>'
            '</div>'
            + _section("High-Priority Matches", alert_rows)
        )

    # ── NIAP PPs ──────────────────────────────────────────────────────────────
    pp = weekly_diff.get("niap", {}).get("pps", {})
    rows: list[str] = []
    for p in pp.get("added", []):
        rows.append(_row("NEW", f"<b>{p.get('pp_short_name','')}</b> - {p.get('pp_name','')}"))
    for p in pp.get("removed", []):
        rows.append(_row("REMOVED", f"<b>{p.get('pp_short_name','')}</b>", "#721c24", "#f8d7da"))
    for p in pp.get("sunset_changes", []):
        rows.append(_row("SUNSET", f"<b>{p.get('pp_short_name','')}</b> - Sunset: {p.get('new_sunset','')[:10]}", "#856404", "#fff3cd"))
    parts.append(_section("NIAP - Protection Profiles", rows))

    # ── NIAP TDs ──────────────────────────────────────────────────────────────
    td = weekly_diff.get("niap", {}).get("tds", {})
    rows = []
    for t in td.get("added", []):
        rows.append(_row("NEW TD", f"<b>{t.get('identifier','')}</b> - {t.get('title','')}"))
    for t in td.get("removed", []):
        rows.append(_row("REMOVED", f"<b>{t.get('identifier','')}</b>", "#721c24", "#f8d7da"))
    parts.append(_section("NIAP - Technical Decisions", rows))

    # ── Cisco NDcPP ───────────────────────────────────────────────────────────
    cn = weekly_diff.get("niap", {}).get("cisco_ndcpp", {})
    rows = []
    for p in cn.get("added", []):
        rows.append(_row("CERTIFIED", f"<b>{p.get('product_name','')}</b> ({p.get('vendor_id_name','')})"))
    for p in cn.get("newly_archived", []):
        rows.append(_row("ARCHIVED", f"<b>{p.get('product_name','')}</b>", "#856404", "#fff3cd"))
    for p in cn.get("removed", []):
        rows.append(_row("REMOVED", f"<b>{p.get('product_name','')}</b>", "#721c24", "#f8d7da"))
    parts.append(_section("Cisco NDcPP PCL Changes", rows))

    # ── NIAP News ─────────────────────────────────────────────────────────────
    news = weekly_diff.get("niap", {}).get("news", {})
    rows = []
    for item in news.get("added", []):
        cat = item.get("_category", "NEWS")
        link = item.get("url", "")
        title = item.get("title", "")
        txt = f'<a href="{link}">{title}</a>' if link else title
        rows.append(_row(cat, txt, "#1a4a8a", "#e2eafc"))
    parts.append(_section("NIAP - News and Announcements", rows))

    # ── CCTL Labs ─────────────────────────────────────────────────────────────
    labs = weekly_diff.get("cctl_labs", {})
    rows = []
    for lab, items in labs.items():
        for item in items[:5]:
            link = item.get("link", "")
            title = item.get("title", "")
            txt = f'<a href="{link}">{title}</a>' if link else title
            rows.append(_row(lab[:18], txt, "#1a4a8a", "#e2eafc"))
    parts.append(_section("CCTL Lab Intel", rows))

    # ── CSfC ──────────────────────────────────────────────────────────────────
    csfc = weekly_diff.get("csfc", {})
    rows = []
    for cp_name, change in csfc.get("capability_packages", {}).items():
        if change.get("changed"):
            old_lm = change.get("old_last_modified", "")
            new_lm = change.get("new_last_modified", "")
            url = change.get("url", "")
            detail = f"Last-Modified: {old_lm or '—'} → {new_lm or '—'}"
            txt = f'<a href="{url}">{cp_name}</a>' if url else cp_name
            rows.append(_row("CP UPDATE", f"<b>{txt}</b><br><small>{detail}</small>", "#5a3e00", "#fff3cd"))
    for page_key, page_diff in csfc.get("pages", {}).items():
        for item in page_diff.get("added", [])[:3]:
            rows.append(_row(f"NSA:{page_key[:8]}", item.get("text", "")[:120], "#1a4a8a", "#e8f0fe"))
    for feed_name, items in csfc.get("feeds", {}).items():
        for item in items[:3]:
            link = item.get("link", "")
            title = item.get("title", "")
            txt = f'<a href="{link}">{title}</a>' if link else title
            rows.append(_row("ADVISORY", txt, "#1a4a8a", "#e2eafc"))
    parts.append(_section("CSfC — Capability Packages & APL", rows))

    # ── CC Crypto Catalog ─────────────────────────────────────────────────────
    cc_crypto = weekly_diff.get("cc_crypto", {})
    rows = []
    for doc_name, change in cc_crypto.get("doc_headers", {}).items():
        if change.get("changed"):
            url = change.get("url", "")
            txt = f'<a href="{url}">{doc_name}</a>' if url else doc_name
            rows.append(_row("DOC UPDATE", f"<b>{txt}</b> — new version detected", "#5a0000", "#f8d7da"))
    for page_key, page_diff in cc_crypto.get("pages", {}).items():
        for item in page_diff.get("added", [])[:3]:
            rows.append(_row(f"CC:{page_key[:8]}", item.get("text", "")[:120], "#1a4a8a", "#e8f0fe"))
    parts.append(_section("CC Crypto Catalog & Working Group", rows))

    # ── NIST CSRC ─────────────────────────────────────────────────────────────
    nist = weekly_diff.get("nist", {})
    rows = []
    for doc_name, change in nist.get("doc_headers", {}).items():
        if change.get("changed"):
            url = change.get("url", "")
            txt = f'<a href="{url}">{doc_name}</a>' if url else doc_name
            rows.append(_row("NIST DOC", f"<b>{txt}</b> — revised", "#003366", "#d0e4ff"))
    for feed_name, items in nist.get("feeds", {}).items():
        for item in items[:5]:
            link = item.get("link", "")
            title = item.get("title", "")
            txt = f'<a href="{link}">{title}</a>' if link else title
            rows.append(_row("NIST", txt, "#003366", "#d0e4ff"))
    for item in nist.get("pages", {}).get("cmvp_mip", {}).get("added", [])[:5]:
        rows.append(_row("CMVP MIP", item.get("text", "")[:120], "#003366", "#d0e4ff"))
    parts.append(_section("NIST CSRC — Standards, CMVP & PQC", rows))

    body = "".join(parts) or "<p>No changes detected this week.</p>"
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


def _send_email(subject: str, html: str) -> None:
    """Low-level helper: authenticate and send one HTML email."""
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


def send_weekly_email(weekly_diff: dict) -> None:
    """Build and send the weekly HTML email digest."""
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    subject = config.EMAIL_SUBJECT.format(date=date_str)
    html = build_email_html(weekly_diff)
    _send_email(subject, html)


def send_alert_email(alerts: list[dict]) -> None:
    """Send an immediate alert email when keyword matches are found on a daily run."""
    if not alerts:
        return

    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    subject = f"CC Pulse ALERT — {len(alerts)} keyword match(es) on {date_str}"

    rows = []
    for a in alerts:
        kws = ", ".join(a.get("matched_keywords", []))
        rows.append(
            _row(a.get("source", "ALERT")[:14],
                 f"<b>{a.get('title', '')}</b> &mdash; {kws}",
                 "#ffffff", "#a82222")
        )

    body = (
        '<div style="background:#a82222;color:white;padding:14px 18px;'
        'border-radius:6px;margin-bottom:8px">'
        f'<b style="font-size:1rem">&#9888; {len(alerts)} KEYWORD ALERT(S) DETECTED</b>'
        f'<p style="margin:4px 0 0;font-size:0.85rem;opacity:0.85">'
        f'{date_str} — immediate notification</p>'
        '</div>'
        + _section("Matched Items", rows)
    )

    generated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    html = (
        '<html><body style="font-family:-apple-system,BlinkMacSystemFont,sans-serif;'
        'max-width:720px;margin:0 auto;color:#1a1a2e">'
        '<div style="background:#a82222;color:white;padding:20px 28px;border-radius:8px 8px 0 0">'
        '<h1 style="margin:0;font-size:1.4rem">&#9888; CC Pulse — Immediate Alert</h1>'
        f'<p style="margin:4px 0 0;opacity:0.75;font-size:0.85rem">{date_str}</p>'
        '</div>'
        '<div style="background:white;padding:20px 28px;border:1px solid #d0d7e2;'
        'border-top:none;border-radius:0 0 8px 8px">'
        f'{body}'
        '<hr style="margin-top:28px;border:none;border-top:1px solid #eee">'
        f'<p style="color:#888;font-size:0.75rem;margin-top:12px">'
        f'CC Pulse automated monitoring — immediate alert<br>Generated {generated}</p>'
        '</div></body></html>'
    )
    _send_email(subject, html)
