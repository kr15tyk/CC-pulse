"""
dashboard.py — Renders the daily HTML dashboard and RSS feed.

Features:
  - Keyword alert banner (red, top of page)
  - Trend summary stats panel (NIAP + CSfC + CC Crypto + NIST)
  - Dashboard cards for all 5 monitored domains
  - RSS feed (cc_feed.xml) with items from all domains
  - Structured logging
"""
import logging
import os
from datetime import datetime, timezone
from xml.sax.saxutils import escape as xml_escape

from jinja2 import Template

import config

log = logging.getLogger(__name__)

# ── Dashboard HTML template ──────────────────────────────────────────────────
DASHBOARD_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta http-equiv="refresh" content="3600">
  <title>CC Pulse Dashboard &mdash; {{ date }}</title>
  <style>
    :root {
      --navy: #003366; --blue: #0057a8; --green: #1a7a4a;
      --red: #a82222; --amber: #b86a00; --gray: #f4f6f9;
      --border: #d0d7e2; --purple: #5a2d82; --teal: #006b77;
    }
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
           background: #eef1f7; color: #1a1a2e; }
    header { background: var(--navy); color: white; padding: 18px 32px;
             display: flex; justify-content: space-between; align-items: center; }
    header h1 { font-size: 1.5rem; letter-spacing: 0.05em; }
    header span { font-size: 0.85rem; opacity: 0.75; }
    .alert-banner { background: #a82222; color: white; padding: 14px 32px; }
    .alert-banner h2 { font-size: 1rem; margin-bottom: 8px; letter-spacing: 0.05em; }
    .alert-item { background: rgba(255,255,255,0.15); border-radius: 6px;
                  padding: 8px 12px; margin-bottom: 6px; font-size: 0.875rem; }
    .alert-item strong { display: block; }
    .kw-chip { display: inline-block; background: rgba(255,255,255,0.3);
               border-radius: 4px; font-size: 0.7rem; font-weight: 700;
               padding: 1px 6px; margin: 2px 2px 0 0; }
    .trend-bar { background: white; border-bottom: 1px solid var(--border);
                 padding: 12px 32px; display: flex; gap: 28px; flex-wrap: wrap; }
    .stat { text-align: center; }
    .stat-num { font-size: 1.6rem; font-weight: 700; color: var(--navy); }
    .stat-lbl { font-size: 0.7rem; color: #666; text-transform: uppercase; letter-spacing: 0.07em; }
    .domain-tabs { background: white; border-bottom: 2px solid var(--border);
                   padding: 0 32px; display: flex; gap: 0; }
    .domain-tab { padding: 10px 18px; font-size: 0.8rem; font-weight: 700;
                  text-transform: uppercase; letter-spacing: 0.05em; color: #666;
                  border-bottom: 3px solid transparent; cursor: pointer; }
    .domain-tab.active { color: var(--navy); border-bottom-color: var(--blue); }
    .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(520px, 1fr));
            gap: 20px; padding: 24px 32px; }
    .card { background: white; border-radius: 8px; box-shadow: 0 1px 4px rgba(0,0,0,0.1);
            border-top: 4px solid var(--blue); overflow: hidden; }
    .card.green { border-color: var(--green); }
    .card.red { border-color: var(--red); }
    .card.amber { border-color: var(--amber); }
    .card.purple { border-color: var(--purple); }
    .card.teal { border-color: var(--teal); }
    .card-header { padding: 14px 18px; background: var(--gray);
                   border-bottom: 1px solid var(--border);
                   display: flex; justify-content: space-between; align-items: center; }
    .card-header h2 { font-size: 0.95rem; font-weight: 700; text-transform: uppercase;
                      letter-spacing: 0.06em; color: var(--navy); }
    .badge { font-size: 0.75rem; font-weight: 700; padding: 2px 9px;
             border-radius: 12px; background: var(--blue); color: white; }
    .badge.zero { background: #aaa; }
    .card-body { padding: 14px 18px; }
    .item { padding: 8px 0; border-bottom: 1px solid #eee; font-size: 0.875rem; }
    .item:last-child { border-bottom: none; }
    .item-title { font-weight: 600; color: var(--navy); }
    .item-title a { color: var(--blue); text-decoration: none; }
    .item-title a:hover { text-decoration: underline; }
    .item-meta { color: #666; font-size: 0.8rem; margin-top: 3px; }
    .tag { display: inline-block; font-size: 0.7rem; font-weight: 700; padding: 1px 7px;
           border-radius: 4px; margin-right: 5px; text-transform: uppercase; }
    .tag-add { background: #d4edda; color: #155724; }
    .tag-remove { background: #f8d7da; color: #721c24; }
    .tag-sunset { background: #fff3cd; color: #856404; }
    .tag-cat { background: #e2eafc; color: #1a4a8a; }
    .tag-csfc { background: #e8d5ff; color: #5a2d82; }
    .tag-nist { background: #d0e4ff; color: #003366; }
    .tag-crypto { background: #ffd5e8; color: #7a0040; }
    .tag-update { background: #fff3cd; color: #856404; }
    .empty { color: #999; font-size: 0.85rem; font-style: italic; padding: 10px 0; }
    .section-label { font-size: 0.7rem; font-weight: 700; color: #888;
                     text-transform: uppercase; letter-spacing: 0.08em; padding: 6px 0 3px; }
    .domain-header { background: var(--gray); border-bottom: 1px solid var(--border);
                     padding: 10px 32px; font-size: 0.85rem; font-weight: 700;
                     color: var(--navy); text-transform: uppercase; letter-spacing: 0.08em; }
    footer { text-align: center; padding: 20px; color: #888; font-size: 0.8rem; }
    footer a { color: #0057a8; }
  </style>
</head>
<body>

<header>
  <h1>&#127760; CC Pulse Dashboard</h1>
  <span>Last updated: {{ date }} {{ time }} UTC &bull;
    <a href="{{ rss_filename }}" style="color:rgba(255,255,255,0.7)">&#128280; RSS</a>
  </span>
</header>

{# ── Keyword alert banner ─────────────────────────────────────── #}
{% if diff.alerts %}
<div class="alert-banner">
  <h2>&#9888; {{ diff.alerts|length }} HIGH-PRIORITY ALERT(S) — WATCH KEYWORD MATCH</h2>
  {% for a in diff.alerts %}
  <div class="alert-item">
    <strong>[{{ a.source }}] {{ a.title }}</strong>
    {% for kw in a.matched_keywords %}<span class="kw-chip">{{ kw }}</span>{% endfor %}
  </div>
  {% endfor %}
</div>
{% endif %}

{# ── Trend / summary stats ────────────────────────────────────── #}
<div class="trend-bar">
  <div class="stat">
    <div class="stat-num">{{ diff.niap.pps.added|length }}</div>
    <div class="stat-lbl">New PPs</div>
  </div>
  <div class="stat">
    <div class="stat-num">{{ diff.niap.pps.sunset_changes|length }}</div>
    <div class="stat-lbl">PP Sunsets</div>
  </div>
  <div class="stat">
    <div class="stat-num">{{ diff.niap.tds.added|length }}</div>
    <div class="stat-lbl">New TDs</div>
  </div>
  <div class="stat">
    <div class="stat-num">{{ diff.niap.cisco_ndcpp.added|length }}</div>
    <div class="stat-lbl">Cisco Cert.</div>
  </div>
  <div class="stat">
    <div class="stat-num">{{ diff.niap.news.added|length }}</div>
    <div class="stat-lbl">NIAP News</div>
  </div>
  <div class="stat">
    <div class="stat-num">{{ diff.csfc.capability_packages.values()|selectattr('changed')|list|length if diff.csfc.capability_packages is defined else 0 }}</div>
    <div class="stat-lbl">CSfC CP Updates</div>
  </div>
  <div class="stat">
    <div class="stat-num">{{ diff.nist.doc_headers.values()|selectattr('changed')|list|length if diff.nist.doc_headers is defined else 0 }}</div>
    <div class="stat-lbl">NIST Doc Updates</div>
  </div>
  <div class="stat">
    <div class="stat-num" style="color:{% if diff.alerts %}#a82222{% else %}#1a7a4a{% endif %}">
      {{ diff.alerts|length }}
    </div>
    <div class="stat-lbl">Alerts</div>
  </div>
</div>

<div class="grid">

{# ── NIAP Protection Profiles ──────────────────────────────────── #}
{% set pp = diff.niap.pps %}
{% set pp_total = pp.added|length + pp.removed|length + pp.sunset_changes|length %}
<div class="card {% if pp_total > 0 %}green{% endif %}">
  <div class="card-header">
    <h2>&#128737; NIAP &mdash; Protection Profiles</h2>
    <span class="badge {% if pp_total == 0 %}zero{% endif %}">{{ pp_total }} change(s)</span>
  </div>
  <div class="card-body">
    {% if pp.added %}<div class="section-label">New / Added</div>
    {% for p in pp.added %}
    <div class="item">
      <div class="item-title"><span class="tag tag-add">NEW</span>{{ p.pp_short_name }}</div>
      <div class="item-meta">{{ p.pp_name }} &bull; Published: {{ p.pp_date[:10] if p.pp_date else '&mdash;' }}</div>
    </div>{% endfor %}{% endif %}
    {% if pp.removed %}<div class="section-label">Removed</div>
    {% for p in pp.removed %}
    <div class="item">
      <div class="item-title"><span class="tag tag-remove">REMOVED</span>{{ p.pp_short_name }}</div>
      <div class="item-meta">{{ p.pp_name }}</div>
    </div>{% endfor %}{% endif %}
    {% if pp.sunset_changes %}<div class="section-label">Sunset Date Set / Changed</div>
    {% for p in pp.sunset_changes %}
    <div class="item">
      <div class="item-title"><span class="tag tag-sunset">SUNSET</span>{{ p.pp_short_name }}</div>
      <div class="item-meta">Sunset: {{ p.new_sunset[:10] if p.new_sunset else '&mdash;' }}{% if p.old_sunset %} (was: {{ p.old_sunset[:10] }}){% endif %}</div>
    </div>{% endfor %}{% endif %}
    {% if pp_total == 0 %}<div class="empty">No changes today.</div>{% endif %}
  </div>
</div>

{# ── NIAP Technical Decisions ──────────────────────────────────── #}
{% set td = diff.niap.tds %}
{% set td_total = td.added|length + td.removed|length %}
<div class="card {% if td_total > 0 %}green{% endif %}">
  <div class="card-header">
    <h2>&#9878; NIAP &mdash; Technical Decisions</h2>
    <span class="badge {% if td_total == 0 %}zero{% endif %}">{{ td_total }} change(s)</span>
  </div>
  <div class="card-body">
    {% if td.added %}<div class="section-label">New TDs</div>
    {% for t in td.added %}
    <div class="item">
      <div class="item-title"><span class="tag tag-add">NEW</span>{{ t.identifier }}</div>
      <div class="item-meta">{{ t.title }}</div>
    </div>{% endfor %}{% endif %}
    {% if td.removed %}<div class="section-label">Removed / Superseded</div>
    {% for t in td.removed %}
    <div class="item">
      <div class="item-title"><span class="tag tag-remove">REMOVED</span>{{ t.identifier }}</div>
    </div>{% endfor %}{% endif %}
    {% if td_total == 0 %}<div class="empty">No changes today.</div>{% endif %}
  </div>
</div>

{# ── Cisco NDcPP ──────────────────────────────────────────────── #}
{% set cn = diff.niap.cisco_ndcpp %}
{% set cn_total = cn.added|length + cn.removed|length + cn.newly_archived|length %}
<div class="card {% if cn_total > 0 %}{% if cn.newly_archived %}amber{% else %}green{% endif %}{% endif %}">
  <div class="card-header">
    <h2>&#128225; Cisco NDcPP PCL Changes</h2>
    <span class="badge {% if cn_total == 0 %}zero{% endif %}">{{ cn_total }} change(s)</span>
  </div>
  <div class="card-body">
    {% if cn.added %}<div class="section-label">Newly Certified</div>
    {% for p in cn.added %}
    <div class="item">
      <div class="item-title"><span class="tag tag-add">CERTIFIED</span>{{ p.product_name }}</div>
      <div class="item-meta">{{ p.vendor_id_name }}</div>
    </div>{% endfor %}{% endif %}
    {% if cn.newly_archived %}<div class="section-label">Newly Archived</div>
    {% for p in cn.newly_archived %}
    <div class="item">
      <div class="item-title"><span class="tag tag-sunset">ARCHIVED</span>{{ p.product_name }}</div>
    </div>{% endfor %}{% endif %}
    {% if cn.removed %}<div class="section-label">Removed from PCL</div>
    {% for p in cn.removed %}
    <div class="item">
      <div class="item-title"><span class="tag tag-remove">REMOVED</span>{{ p.product_name }}</div>
    </div>{% endfor %}{% endif %}
    {% if cn_total == 0 %}<div class="empty">No changes today.</div>{% endif %}
  </div>
</div>

{# ── NIAP News ─────────────────────────────────────────────────── #}
{% set news_total = diff.niap.news.added|length %}
<div class="card {% if news_total > 0 %}green{% endif %}">
  <div class="card-header">
    <h2>&#128240; NIAP &mdash; News &amp; Announcements</h2>
    <span class="badge {% if news_total == 0 %}zero{% endif %}">{{ news_total }} new</span>
  </div>
  <div class="card-body">
    {% for item in diff.niap.news.added %}
    <div class="item">
      <div class="item-title">
        <span class="tag tag-cat">{{ item._category }}</span>
        {% if item.url %}<a href="{{ item.url }}" target="_blank">{{ item.title }}</a>{% else %}{{ item.title }}{% endif %}
      </div>
      <div class="item-meta">{{ item.date[:10] if item.date else '' }}</div>
    </div>
    {% else %}<div class="empty">No new announcements today.</div>{% endfor %}
  </div>
</div>

{# ── CCTL Lab Intel ────────────────────────────────────────────── #}
{% set lab_count = diff.cctl_labs|length %}
<div class="card {% if lab_count > 0 %}green{% endif %}">
  <div class="card-header">
    <h2>&#128269; CCTL Lab Intel</h2>
    <span class="badge {% if lab_count == 0 %}zero{% endif %}">{{ lab_count }} lab(s) with updates</span>
  </div>
  <div class="card-body">
    {% for lab, items in diff.cctl_labs.items() %}
    <div class="section-label">{{ lab }}</div>
    {% for item in items[:5] %}
    <div class="item">
      <div class="item-title">
        {% if item.link %}<a href="{{ item.link }}" target="_blank">{{ item.title }}</a>{% else %}{{ item.title }}{% endif %}
      </div>
      <div class="item-meta">{{ item.published[:10] if item.published else '' }}</div>
    </div>{% endfor %}
    {% else %}<div class="empty">No new lab posts today.</div>{% endfor %}
  </div>
</div>

{# ── CSfC Capability Packages ──────────────────────────────────── #}
{% set csfc = diff.csfc if diff.csfc is defined else {} %}
{% set csfc_cp_changes = csfc.capability_packages.values()|selectattr('changed')|list if csfc.capability_packages is defined else [] %}
{% set csfc_page_adds = [] %}
{% if csfc.pages is defined %}{% for pk, pd in csfc.pages.items() %}{% if pd.added is defined %}{% for i in pd.added %}{% set _ = csfc_page_adds.append(i) %}{% endfor %}{% endif %}{% endfor %}{% endif %}
{% set csfc_feed_items = [] %}
{% if csfc.feeds is defined %}{% for fn, fi in csfc.feeds.items() %}{% for i in fi[:3] %}{% set _ = csfc_feed_items.append(i) %}{% endfor %}{% endfor %}{% endif %}
{% set csfc_total = csfc_cp_changes|length + csfc_page_adds|length + csfc_feed_items|length %}
<div class="card {% if csfc_total > 0 %}purple{% endif %}">
  <div class="card-header">
    <h2>&#128274; CSfC &mdash; Capability Packages &amp; APL</h2>
    <span class="badge {% if csfc_total == 0 %}zero{% endif %}">{{ csfc_total }} change(s)</span>
  </div>
  <div class="card-body">
    {% if csfc_cp_changes %}
    <div class="section-label">Capability Package Document Updates</div>
    {% for cp_name, change in csfc.capability_packages.items() %}{% if change.changed %}
    <div class="item">
      <div class="item-title">
        <span class="tag tag-update">CP UPDATE</span>
        {% if change.url %}<a href="{{ change.url }}" target="_blank">{{ cp_name }}</a>{% else %}{{ cp_name }}{% endif %}
      </div>
      <div class="item-meta">Last-Modified: {{ change.old_last_modified or '—' }} &rarr; {{ change.new_last_modified or '—' }}</div>
    </div>
    {% endif %}{% endfor %}{% endif %}
    {% if csfc_page_adds %}
    <div class="section-label">NSA CSfC Page Changes</div>
    {% for item in csfc_page_adds[:6] %}
    <div class="item">
      <div class="item-title"><span class="tag tag-csfc">NSA</span>{{ item.text[:120] if item.text else '' }}</div>
      {% if item.href %}<div class="item-meta"><a href="{{ item.href }}" target="_blank">{{ item.href }}</a></div>{% endif %}
    </div>{% endfor %}{% endif %}
    {% if csfc_feed_items %}
    <div class="section-label">CSfC / Advisory Feeds</div>
    {% for item in csfc_feed_items %}
    <div class="item">
      <div class="item-title">
        <span class="tag tag-csfc">ADVISORY</span>
        {% if item.link %}<a href="{{ item.link }}" target="_blank">{{ item.title }}</a>{% else %}{{ item.title }}{% endif %}
      </div>
    </div>{% endfor %}{% endif %}
    {% if csfc_total == 0 %}<div class="empty">No CSfC changes today.</div>{% endif %}
  </div>
</div>

{# ── CC Crypto Catalog ─────────────────────────────────────────── #}
{% set cc = diff.cc_crypto if diff.cc_crypto is defined else {} %}
{% set cc_doc_changes = cc.doc_headers.values()|selectattr('changed')|list if cc.doc_headers is defined else [] %}
{% set cc_page_adds = [] %}
{% if cc.pages is defined %}{% for pk, pd in cc.pages.items() %}{% if pd.added is defined %}{% for i in pd.added %}{% set _ = cc_page_adds.append(i) %}{% endfor %}{% endif %}{% endfor %}{% endif %}
{% set cc_total = cc_doc_changes|length + cc_page_adds|length %}
<div class="card {% if cc_total > 0 %}amber{% endif %}">
  <div class="card-header">
    <h2>&#128196; CC Crypto Catalog &amp; Working Group</h2>
    <span class="badge {% if cc_total == 0 %}zero{% endif %}">{{ cc_total }} change(s)</span>
  </div>
  <div class="card-body">
    {% if cc_doc_changes %}
    <div class="section-label">Document Version Changes</div>
    {% for doc_name, change in cc.doc_headers.items() %}{% if change.changed %}
    <div class="item">
      <div class="item-title">
        <span class="tag tag-update">DOC UPDATE</span>
        {% if change.url %}<a href="{{ change.url }}" target="_blank">{{ doc_name }}</a>{% else %}{{ doc_name }}{% endif %}
      </div>
      <div class="item-meta">New version detected via header change</div>
    </div>
    {% endif %}{% endfor %}{% endif %}
    {% if cc_page_adds %}
    <div class="section-label">CC Portal Page Changes</div>
    {% for item in cc_page_adds[:5] %}
    <div class="item">
      <div class="item-title"><span class="tag tag-crypto">CC</span>{{ item.text[:120] if item.text else '' }}</div>
      {% if item.href %}<div class="item-meta"><a href="{{ item.href }}" target="_blank">{{ item.href }}</a></div>{% endif %}
    </div>{% endfor %}{% endif %}
    {% if cc_total == 0 %}<div class="empty">No CC Crypto Catalog changes today.</div>{% endif %}
  </div>
</div>

{# ── NIST CSRC ─────────────────────────────────────────────────── #}
{% set ni = diff.nist if diff.nist is defined else {} %}
{% set nist_doc_changes = ni.doc_headers.values()|selectattr('changed')|list if ni.doc_headers is defined else [] %}
{% set nist_feed_items = [] %}
{% if ni.feeds is defined %}{% for fn, fi in ni.feeds.items() %}{% for i in fi[:3] %}{% set _ = nist_feed_items.append(i) %}{% endfor %}{% endfor %}{% endif %}
{% set nist_mip_adds = ni.pages.cmvp_mip.added if ni.pages is defined and ni.pages.cmvp_mip is defined else [] %}
{% set nist_total = nist_doc_changes|length + nist_feed_items|length + nist_mip_adds|length %}
<div class="card {% if nist_total > 0 %}teal{% endif %}">
  <div class="card-header">
    <h2>&#128203; NIST CSRC &mdash; Standards, CMVP &amp; PQC</h2>
    <span class="badge {% if nist_total == 0 %}zero{% endif %}">{{ nist_total }} change(s)</span>
  </div>
  <div class="card-body">
    {% if nist_doc_changes %}
    <div class="section-label">Document Revisions Detected</div>
    {% for doc_name, change in ni.doc_headers.items() %}{% if change.changed %}
    <div class="item">
      <div class="item-title">
        <span class="tag tag-nist">REVISED</span>
        {% if change.url %}<a href="{{ change.url }}" target="_blank">{{ doc_name }}</a>{% else %}{{ doc_name }}{% endif %}
      </div>
      <div class="item-meta">Header change detected — new version likely published</div>
    </div>
    {% endif %}{% endfor %}{% endif %}
    {% if nist_mip_adds %}
    <div class="section-label">CMVP Modules In Process — New Entries</div>
    {% for item in nist_mip_adds[:5] %}
    <div class="item">
      <div class="item-title"><span class="tag tag-nist">CMVP MIP</span>{{ item.text[:120] if item.text else '' }}</div>
    </div>{% endfor %}{% endif %}
    {% if nist_feed_items %}
    <div class="section-label">NIST Cybersecurity News</div>
    {% for item in nist_feed_items %}
    <div class="item">
      <div class="item-title">
        <span class="tag tag-nist">NIST</span>
        {% if item.link %}<a href="{{ item.link }}" target="_blank">{{ item.title }}</a>{% else %}{{ item.title }}{% endif %}
      </div>
    </div>{% endfor %}{% endif %}
    {% if nist_total == 0 %}<div class="empty">No NIST changes today.</div>{% endif %}
  </div>
</div>

</div>{# end .grid #}

<footer>
  CC Pulse &mdash; Generated {{ date }} {{ time }} UTC &bull;
  <a href="{{ rss_filename }}">RSS Feed</a> &bull;
  <a href="https://github.com/kr15tyk/CC-pulse" target="_blank">GitHub</a>
</footer>
</body>
</html>
"""


# ── RSS feed builder ─────────────────────────────────────────────────────────
def _build_rss(diff: dict, date: str, time_str: str) -> str:
    """Generate RSS 2.0 feed covering all monitored domains."""
    pub_date = datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S +0000")
    items: list[str] = []

    def _item(title: str, desc: str, link: str = "") -> None:
        items.append(
            f"<item>"
            f"<title>{xml_escape(title)}</title>"
            f"<description>{xml_escape(desc)}</description>"
            f"<link>{xml_escape(link)}</link>"
            f"<pubDate>{pub_date}</pubDate>"
            f"<guid isPermaLink='false'>{xml_escape(title + pub_date)}</guid>"
            f"</item>"
        )

    # Alerts first (all domains)
    for a in diff.get("alerts", []):
        _item(
            f"[ALERT] {a['source']}: {a['title']}",
            "Matched keywords: " + ", ".join(a["matched_keywords"]),
        )

    # NIAP — New PPs
    for p in diff.get("niap", {}).get("pps", {}).get("added", []):
        _item(f"New NIAP PP: {p.get('pp_short_name', '')}", p.get("pp_name", ""))

    # NIAP — New TDs
    for t in diff.get("niap", {}).get("tds", {}).get("added", []):
        _item(f"New NIAP TD: {t.get('identifier', '')}", t.get("title", ""))

    # NIAP — Cisco certs
    for p in diff.get("niap", {}).get("cisco_ndcpp", {}).get("added", []):
        _item(f"Cisco NDcPP Certified: {p.get('product_name', '')}", p.get("vendor_id_name", ""))

    # NIAP — News
    for n in diff.get("niap", {}).get("news", {}).get("added", []):
        _item(n.get("title", ""), n.get("_category", "NEWS"), n.get("url", ""))

    # CSfC — Capability Package updates
    for cp_name, change in diff.get("csfc", {}).get("capability_packages", {}).items():
        if change.get("changed"):
            url = change.get("url", "")
            lm = change.get("new_last_modified", "")
            _item(f"CSfC CP Updated: {cp_name}", f"Last-Modified: {lm}", url)

    # CSfC — Page changes
    for page_key, page_diff in diff.get("csfc", {}).get("pages", {}).items():
        for item in page_diff.get("added", [])[:3]:
            _item(
                f"CSfC NSA Page Change [{page_key}]",
                item.get("text", "")[:200],
                item.get("href", ""),
            )

    # CSfC — Advisory feeds
    for feed_name, feed_items in diff.get("csfc", {}).get("feeds", {}).items():
        for item in feed_items[:3]:
            _item(
                f"CSfC Advisory: {item.get('title', '')}",
                feed_name,
                item.get("link", ""),
            )

    # CC Crypto Catalog — Document changes
    for doc_name, change in diff.get("cc_crypto", {}).get("doc_headers", {}).items():
        if change.get("changed"):
            url = change.get("url", "")
            _item(f"CC Crypto Catalog Updated: {doc_name}", "New version detected via HTTP header change", url)

    # CC Crypto — Page changes
    for page_key, page_diff in diff.get("cc_crypto", {}).get("pages", {}).items():
        for item in page_diff.get("added", [])[:3]:
            _item(
                f"CC Portal Crypto Change [{page_key}]",
                item.get("text", "")[:200],
                item.get("href", ""),
            )

    # NIST — Document revisions
    for doc_name, change in diff.get("nist", {}).get("doc_headers", {}).items():
        if change.get("changed"):
            url = change.get("url", "")
            _item(f"NIST Document Revised: {doc_name}", "Header change detected — new version likely published", url)

    # NIST — RSS feed items
    for feed_name, feed_items in diff.get("nist", {}).get("feeds", {}).items():
        for item in feed_items[:5]:
            _item(
                f"NIST: {item.get('title', '')}",
                feed_name,
                item.get("link", ""),
            )

    # NIST — CMVP Modules In Process new entries
    for item in diff.get("nist", {}).get("pages", {}).get("cmvp_mip", {}).get("added", [])[:5]:
        _item(f"CMVP MIP New Entry: {item.get('text', '')[:80]}", "New module entered FIPS 140-3 validation queue")

    items_xml = "\n".join(items) if items else "<item><title>No changes today</title></item>"
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom">
  <channel>
    <title>CC Pulse Daily Feed</title>
    <link>https://github.com/kr15tyk/CC-pulse</link>
    <description>Automated CC, NIAP, CSfC, CC Crypto Catalog, and NIST monitoring</description>
    <lastBuildDate>{pub_date}</lastBuildDate>
    {items_xml}
  </channel>
</rss>"""


# ── Render ───────────────────────────────────────────────────────────────────
def render_dashboard(diff: dict) -> str:
    now = datetime.now(timezone.utc)
    date = now.strftime("%Y-%m-%d")
    time_str = now.strftime("%H:%M")

    os.makedirs(config.DASHBOARD_DIR, exist_ok=True)

    # HTML
    tmpl = Template(DASHBOARD_TEMPLATE)
    html = tmpl.render(
        diff=diff,
        date=date,
        time=time_str,
        rss_filename=config.DASHBOARD_RSS,
    )
    html_path = os.path.join(config.DASHBOARD_DIR, config.DASHBOARD_FILENAME)
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html)
    log.info("[Dashboard] HTML written to %s", html_path)

    # RSS
    rss = _build_rss(diff, date, time_str)
    rss_path = os.path.join(config.DASHBOARD_DIR, config.DASHBOARD_RSS)
    with open(rss_path, "w", encoding="utf-8") as f:
        f.write(rss)
    log.info("[Dashboard] RSS feed written to %s", rss_path)

    return html_path
