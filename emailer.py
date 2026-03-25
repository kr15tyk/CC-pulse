"""
emailer.py - Builds and sends the weekly CC Pulse email digest.
"""

import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import datetime, timezone
import config


def _row(label, content, color="#155724", bg="#d4edda"):
    return (
        f'<tr style="border-bottom:1px solid #eee">'
        f'<td style="width:90px"><span style="background:{bg};color:{color};'
        f'padding:2px 8px;border-radius:4px;font-size:11px;font-weight:700">'
        f'{label}</span></td><td>{content}</td></tr>'
    )


def _section(title, rows):
    if not rows:
        return ""
    body = "".join(rows)
    return (
        f'<h3 style="color:#003366;border-bottom:2px solid #0057a8;'
        f'padding-bottom:4px;margin-top:24px">{title}</h3>'
        f'<table width="100%" cellpadding="6" cellspacing="0" '
        f'style="border-collapse:collapse;font-size:13px">{body}</table>'
    )


def build_email_html(weekly_diff):
    now  = datetime.now(timezone.utc)
    date = now.strftime("%B %d, %Y")
    parts = []

    pp = weekly_diff.get("niap", {}).get("pps", {})
    rows = []
    for p in pp.get("added", []):
        rows.append(_row("NEW", f"<b>{p.get('pp_short_name','')}</b> - {p.get('pp_name','')}"))
    for p in pp.get("removed", []):
        rows.append(_row("REMOVED", f"<b>{p.get('pp_short_name','')}</b>", "#721c24", "#f8d7da"))
    for p in pp.get("sunset_changes", []):
        rows.append(_row("SUNSET",
                         f"<b>{p.get('pp_short_name','')}</b> - Sunset: {p.get('new_sunset','')[:10]}",
                         "#856404", "#fff3cd"))
    parts.append(_section("NIAP - Protection Profiles", rows))

    td = weekly_diff.get("niap", {}).get("tds", {})
    rows = []
    for t in td.get("added", []):
        rows.append(_row("NEW TD", f"<b>{t.get('identifier','')}</b> - {t.get('title','')}"))
    for t in td.get("removed", []):
        rows.append(_row("REMOVED", f"<b>{t.get('identifier','')}</b>", "#721c24", "#f8d7da"))
    parts.append(_section("NIAP - Technical Decisions", rows))

    cn = weekly_diff.get("niap", {}).get("cisco_ndcpp", {})
    rows = []
    for p in cn.get("added", []):
        rows.append(_row("CERTIFIED",
                         f"<b>{p.get('product_name','')}</b> ({p.get('vendor_id_name','')})"))
    for p in cn.get("newly_archived", []):
        rows.append(_row("ARCHIVED", f"<b>{p.get('product_name','')}</b>", "#856404", "#fff3cd"))
    for p in cn.get("removed", []):
        rows.append(_row("REMOVED", f"<b>{p.get('product_name','')}</b>", "#721c24", "#f8d7da"))
    parts.append(_section("Cisco NDcPP PCL Changes", rows))

    news = weekly_diff.get("niap", {}).get("news", {})
    rows = []
    for item in news.get("added", []):
        cat  = item.get("_category", "NEWS")
        link = item.get("url", "")
        title = item.get("title", "")
        txt  = f'<a href="{link}">{title}</a>' if link else title
        rows.append(_row(cat, txt, "#1a4a8a", "#e2eafc"))
    parts.append(_section("NIAP - News and Announcements", rows))

    labs = weekly_diff.get("cctl_labs", {})
    rows = []
    for lab, items in labs.items():
        for item in items[:5]:
            link  = item.get("link", "")
            title = item.get("title", "")
            txt   = f'<a href="{link}">{title}</a>' if link else title
            rows.append(_row(lab[:18], txt, "#1a4a8a", "#e2eafc"))
    parts.append(_section("CCTL Lab Intel", rows))

    body = "".join(parts) or "<p>No changes detected this week.</p>"
    generated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    return (
        '<html><body style="font-family:-apple-system,sans-serif;'
        'max-width:720px;margin:0 auto;color:#1a1a2e">'
        '<div style="background:#003366;color:white;padding:20px 28px">'
        f'<h1 style="margin:0;font-size:1.4rem">CC Pulse - Weekly Brief</h1>'
        f'<p style="margin:4px 0 0;opacity:0.75;font-size:0.85rem">Week ending {date}</p>'
        '</div><div style="background:white;padding:20px 28px;border:1px solid #d0d7e2">'
        f'{body}'
        '<hr style="margin-top:28px;border:none;border-top:1px solid #eee">'
        f'<p style="color:#888;font-size:0.75rem;margin-top:12px">'
        f'CC Pulse automated monitoring<br>Generated {generated}</p>'
        '</div></body></html>'
    )


def send_weekly_email(weekly_diff):
    password = os.environ.get("CC_EMAIL_PASSWORD", config.EMAIL_PASSWORD)
    if not password:
        print("[Email] No password set - skipping.")
        return
    html     = build_email_html(weekly_diff)
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    subject  = config.EMAIL_SUBJECT.format(date=date_str)
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = config.EMAIL_FROM
    msg["To"]      = ", ".join(config.EMAIL_RECIPIENTS)
    msg.attach(MIMEText(html, "html"))
    print(f"[Email] Sending to {config.EMAIL_RECIPIENTS}...")
    try:
        with smtplib.SMTP(config.EMAIL_SMTP_HOST, config.EMAIL_SMTP_PORT) as smtp:
            smtp.ehlo()
            smtp.starttls()
            smtp.login(config.EMAIL_USERNAME, password)
            smtp.sendmail(config.EMAIL_USERNAME, config.EMAIL_RECIPIENTS, msg.as_string())
        print("[Email] Sent successfully.")
    except Exception as e:
        print(f"[Email] Failed: {e}")
        raise
