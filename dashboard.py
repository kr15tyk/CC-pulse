"""
dashboard.py — Renders the daily HTML dashboard and RSS feed.

Features:
  - Keyword alert banner (red, top of page)
  - Trend summary stats panel with anchor links (NIAP + CSfC + CC Crypto + NIST)
  - Collapsible cards (empty sections auto-collapsed)
  - Color-coded section headers (green=new, amber=updated, red=removed/alert)
  - Clickable source links on all items
  - CSfC CP entries show content-length change when dates unavailable
  - 7-day activity sparkline per section
  - Responsive mobile layout
  - CCTL Lab Intel: compact per-lab counts with expand
  - RSS feed (cc_feed.xml) with items from all domains
  - Structured logging
"""
import glob
import json
import logging
import os
from datetime import datetime, timezone
from xml.sax.saxutils import escape as xml_escape

from jinja2 import Template

import config

log = logging.getLogger(__name__)


def _load_recent_diffs(n: int = 7) -> list[dict]:
    """Load the most recent N daily diff files for sparkline data."""
    pattern = os.path.join(config.SNAPSHOTS_DIR, "diffs", "*_diff.json")
    files = sorted(glob.glob(pattern))[-n:]
    result = []
    for f in files:
        try:
            with open(f, encoding="utf-8") as fh:
                result.append(json.load(fh))
        except Exception:
            pass
    return result


def _section_daily_counts(diffs: list[dict]) -> dict:
    """Return per-section change counts for the last N days (for sparkline)."""
    counts: dict[str, list[int]] = {
        "niap_pps": [], "niap_tds": [], "niap_cisco": [],
        "niap_news": [], "csfc": [], "cc_crypto": [], "nist": [],
    }
    for d in diffs:
        pp = d.get("niap", {}).get("pps", {})
        counts["niap_pps"].append(len(pp.get("added", [])) + len(pp.get("removed", [])) + len(pp.get("sunset_changes", [])))
        td = d.get("niap", {}).get("tds", {})
        counts["niap_tds"].append(len(td.get("added", [])) + len(td.get("removed", [])))
        cn = d.get("niap", {}).get("cisco_ndcpp", {})
        counts["niap_cisco"].append(len(cn.get("added", [])) + len(cn.get("removed", [])) + len(cn.get("newly_archived", [])))
        counts["niap_news"].append(len(d.get("niap", {}).get("news", {}).get("added", [])))
        csfc_cps = d.get("csfc", {}).get("capability_packages", {})
        counts["csfc"].append(sum(1 for v in csfc_cps.values() if v.get("changed")))
        cc_docs = d.get("cc_crypto", {}).get("doc_headers", {})
        counts["cc_crypto"].append(sum(1 for v in cc_docs.values() if v.get("changed")))
        nist_docs = d.get("nist", {}).get("doc_headers", {})
        counts["nist"].append(sum(1 for v in nist_docs.values() if v.get("changed")))
    return counts


# ── Dashboard HTML template ────────────────────────────────────────────────
DASHBOARD_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta http-equiv="refresh" content="3600">
<meta name="viewport" content="width=device-width, initial-scale=1">
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
header { background: var(--navy); color: white; padding: 14px 24px;
         display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 8px; }
header h1 { font-size: 1.3rem; letter-spacing: 0.05em; }
header span { font-size: 0.82rem; opacity: 0.75; }
.alert-banner { background: #a82222; color: white; padding: 12px 24px; }
.alert-banner h2 { font-size: 0.95rem; margin-bottom: 8px; letter-spacing: 0.05em; }
.alert-item { background: rgba(255,255,255,0.15); border-radius: 6px;
              padding: 8px 12px; margin-bottom: 6px; font-size: 0.875rem; }
.alert-item strong { display: block; }
.kw-chip { display: inline-block; background: rgba(255,255,255,0.3); border-radius: 4px;
           font-size: 0.7rem; font-weight: 700; padding: 1px 6px; margin: 2px 2px 0 0; }

/* ── Summary bar ── */
.trend-bar { background: white; border-bottom: 1px solid var(--border);
             padding: 10px 24px; display: flex; gap: 4px; flex-wrap: wrap; }
.stat { text-align: center; padding: 6px 12px; border-radius: 6px;
        cursor: pointer; transition: background 0.15s; min-width: 70px; flex: 1; }
.stat:hover { background: var(--gray); }
.stat a { text-decoration: none; color: inherit; display: block; }
.stat-num { font-size: 1.5rem; font-weight: 700; color: var(--navy); }
.stat-num.alert-num { color: #a82222; }
.stat-num.active-num { color: var(--green); }
.stat-lbl { font-size: 0.65rem; color: #666; text-transform: uppercase; letter-spacing: 0.06em; }

/* ── Cards grid ── */
.grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(480px, 1fr));
        gap: 18px; padding: 20px 24px; }
@media (max-width: 600px) {
  .grid { grid-template-columns: 1fr; padding: 12px; gap: 12px; }
  .trend-bar { gap: 2px; }
  .stat { min-width: 50px; padding: 4px 6px; }
  .stat-num { font-size: 1.2rem; }
  header { padding: 10px 14px; }
}

/* ── Card ── */
.card { background: white; border-radius: 8px; box-shadow: 0 1px 4px rgba(0,0,0,0.1);
        border-top: 4px solid #aaa; overflow: hidden; }
.card.has-changes.green  { border-color: var(--green); }
.card.has-changes.amber  { border-color: var(--amber); }
.card.has-changes.red    { border-color: var(--red); }
.card.has-changes.purple { border-color: var(--purple); }
.card.has-changes.teal   { border-color: var(--teal); }
.card.has-changes.blue   { border-color: var(--blue); }
.card-header { padding: 12px 16px; background: var(--gray); border-bottom: 1px solid var(--border);
               display: flex; justify-content: space-between; align-items: center;
               cursor: pointer; user-select: none; }
.card-header:hover { background: #e8ecf3; }
.card-header h2 { font-size: 0.9rem; font-weight: 700; text-transform: uppercase;
                  letter-spacing: 0.06em; color: var(--navy); display: flex; align-items: center; gap: 8px; }
.header-right { display: flex; align-items: center; gap: 10px; }
.badge { font-size: 0.72rem; font-weight: 700; padding: 2px 9px; border-radius: 12px;
         background: var(--blue); color: white; white-space: nowrap; }
.badge.zero { background: #bbb; }
.badge.has-changes { background: var(--green); }
.badge.has-changes.amber { background: var(--amber); }
.badge.has-changes.purple { background: var(--purple); }
.badge.has-changes.teal { background: var(--teal); }
.chevron { font-size: 0.75rem; color: #888; transition: transform 0.2s; }
.card.collapsed .chevron { transform: rotate(-90deg); }
.card.collapsed .card-body { display: none; }
.card-body { padding: 12px 16px; }

/* ── Sparkline ── */
.sparkline-wrap { display: flex; align-items: flex-end; gap: 2px; height: 22px; margin-left: 4px; }
.spark-bar { width: 5px; min-height: 2px; background: #bbb; border-radius: 2px 2px 0 0;
             transition: background 0.2s; }
.spark-bar.active { background: var(--green); }

/* ── Items ── */
.item { padding: 7px 0; border-bottom: 1px solid #eee; font-size: 0.875rem; }
.item:last-child { border-bottom: none; }
.item-title { font-weight: 600; color: var(--navy); }
.item-title a { color: var(--blue); text-decoration: none; }
.item-title a:hover { text-decoration: underline; }
.item-meta { color: #666; font-size: 0.78rem; margin-top: 2px; }
.tag { display: inline-block; font-size: 0.68rem; font-weight: 700;
       padding: 1px 6px; border-radius: 4px; margin-right: 4px; text-transform: uppercase; }
.tag-add    { background: #d4edda; color: #155724; }
.tag-remove { background: #f8d7da; color: #721c24; }
.tag-sunset { background: #fff3cd; color: #856404; }
.tag-cat    { background: #e2eafc; color: #1a4a8a; }
.tag-csfc   { background: #e8d5ff; color: #5a2d82; }
.tag-nist   { background: #d0e4ff; color: #003366; }
.tag-crypto { background: #ffd5e8; color: #7a0040; }
.tag-update { background: #fff3cd; color: #856404; }
.tag-cert   { background: #d4edda; color: #155724; }
.empty { color: #999; font-size: 0.82rem; font-style: italic; padding: 8px 0; }
.section-label { font-size: 0.68rem; font-weight: 700; color: #888;
                 text-transform: uppercase; letter-spacing: 0.08em; padding: 8px 0 4px; }

/* ── Lab Intel ── */
.lab-row { display: flex; justify-content: space-between; align-items: center;
           padding: 6px 0; border-bottom: 1px solid #eee; font-size: 0.85rem; }
.lab-row:last-child { border-bottom: none; }
.lab-name { font-weight: 600; color: var(--navy); }
.lab-count { font-size: 0.75rem; padding: 1px 7px; border-radius: 10px;
             background: var(--green); color: white; font-weight: 700; }
.lab-count.zero { background: #ccc; color: #666; }
.lab-items { padding: 0 0 0 12px; margin-top: 4px; }

/* ── CP update detail ── */
.cp-detail { font-size: 0.75rem; color: #666; margin-top: 2px; }
.cp-change-badge { display: inline-block; font-size: 0.68rem; font-weight: 700;
                   padding: 1px 5px; border-radius: 3px; background: #fff3cd; color: #856404; margin-right: 4px; }

footer { text-align: center; padding: 16px; color: #888; font-size: 0.78rem; }
footer a { color: #0057a8; }
</style>
</head>
<body>
<header>
  <h1>&#127760; CC Pulse Dashboard</h1>
  <span>Last updated: {{ date }} {{ time }} UTC &bull; <a href="{{ rss_filename }}" style="color:rgba(255,255,255,0.7)">&#128280; RSS</a></span>
</header>

{# ── Keyword alert banner ── #}
{% if diff.alerts %}
<div class="alert-banner">
  <h2>&#9888; {{ diff.alerts|length }} HIGH-PRIORITY ALERT(S) &mdash; KEYWORD MATCH</h2>
  {% for a in diff.alerts %}
  <div class="alert-item">
    <strong>[{{ a.source }}] {{ a.title }}</strong>
    {% for kw in a.matched_keywords %}<span class="kw-chip">{{ kw }}</span>{% endfor %}
  </div>
  {% endfor %}
</div>
{% endif %}

{# ── Trend summary bar ── #}
{% set pp = diff.niap.pps %}
{% set td = diff.niap.tds %}
{% set cn = diff.niap.cisco_ndcpp %}
{% set pp_total = pp.added|length + pp.removed|length + pp.sunset_changes|length %}
{% set td_total = td.added|length + td.removed|length %}
{% set cn_total = cn.added|length + cn.removed|length + cn.newly_archived|length %}
{% set news_total = diff.niap.news.added|length %}
{% set csfc_cp_total = diff.csfc.capability_packages.values()|selectattr('changed')|list|length if diff.csfc.capability_packages is defined else 0 %}
{% set nist_total_stat = diff.nist.doc_headers.values()|selectattr('changed')|list|length if diff.nist.doc_headers is defined else 0 %}
{% set alert_total = diff.alerts|length %}

<div class="trend-bar">
  <div class="stat"><a href="#sec-niap-pp">
    <div class="stat-num {% if pp_total > 0 %}active-num{% endif %}">{{ pp_total }}</div>
    <div class="stat-lbl">New PPs</div>
  </a></div>
  <div class="stat"><a href="#sec-niap-pp">
    <div class="stat-num {% if pp.sunset_changes|length > 0 %}active-num{% endif %}">{{ pp.sunset_changes|length }}</div>
    <div class="stat-lbl">PP Sunsets</div>
  </a></div>
  <div class="stat"><a href="#sec-niap-td">
    <div class="stat-num {% if td_total > 0 %}active-num{% endif %}">{{ td_total }}</div>
    <div class="stat-lbl">New TDs</div>
  </a></div>
  <div class="stat"><a href="#sec-cisco">
    <div class="stat-num {% if cn_total > 0 %}active-num{% endif %}">{{ cn_total }}</div>
    <div class="stat-lbl">Cisco Cert.</div>
  </a></div>
  <div class="stat"><a href="#sec-niap-news">
    <div class="stat-num {% if news_total > 0 %}active-num{% endif %}">{{ news_total }}</div>
    <div class="stat-lbl">NIAP News</div>
  </a></div>
  <div class="stat"><a href="#sec-csfc">
    <div class="stat-num {% if csfc_cp_total > 0 %}active-num{% endif %}">{{ csfc_cp_total }}</div>
    <div class="stat-lbl">CSfC CP Updates</div>
  </a></div>
  <div class="stat"><a href="#sec-nist">
    <div class="stat-num {% if nist_total_stat > 0 %}active-num{% endif %}">{{ nist_total_stat }}</div>
    <div class="stat-lbl">NIST Doc Updates</div>
  </a></div>
  <div class="stat"><a href="#sec-niap-pp">
    <div class="stat-num {% if alert_total > 0 %}alert-num{% endif %}">{{ alert_total }}</div>
    <div class="stat-lbl">Alerts</div>
  </a></div>
</div>

<div class="grid">

<!-- ═══════════ NIAP PPs ═══════════ -->
<div class="card {% if niap_pp_total > 0 %}card-new{% endif %}" id="sec-niap-pp">
  <div class="card-hdr" onclick="toggleCard(this)">
    <span>NIAP Protection Profiles</span>
    <span class="card-count">{{ niap_pp_total }} change{% if niap_pp_total != 1 %}s{% endif %}</span>
    <span class="sparkline">
      {% for v in sparklines.niap_pp %}
      <span class="sp-bar" style="height:{{ v }}%" title="{{ v }}%"></span>
      {% endfor %}
    </span>
    <span class="toggle-icon">{% if niap_pp_total == 0 %}▶{% else %}▼{% endif %}</span>
  </div>
  <div class="card-body {% if niap_pp_total == 0 %}collapsed{% endif %}">

    {% if diff.niap.pps.added %}
    <div class="sub-hdr sub-new">New PPs ({{ diff.niap.pps.added | length }})</div>
    {% for pp in diff.niap.pps.added %}
    <div class="item-row">
      <a class="item-link" href="https://www.niap-ccevs.org/Profile/PP.cfm?id={{ pp.pp_id }}" target="_blank">{{ pp.pp_short_name }}</a>
      <span class="item-meta">{{ pp.tech_type }} · {{ pp.pp_date }}</span>
    </div>
    {% endfor %}
    {% endif %}

    {% if diff.niap.pps.removed %}
    <div class="sub-hdr sub-removed">Removed PPs ({{ diff.niap.pps.removed | length }})</div>
    {% for pp in diff.niap.pps.removed %}
    <div class="item-row">
      <a class="item-link" href="https://www.niap-ccevs.org/Profile/PP.cfm?id={{ pp.pp_id }}" target="_blank">{{ pp.pp_short_name }}</a>
      <span class="item-meta">{{ pp.tech_type }} · {{ pp.pp_date }}</span>
    </div>
    {% endfor %}
    {% endif %}

    {% if diff.niap.pps.sunset_changes %}
    <div class="sub-hdr sub-updated">Sunset Changes ({{ diff.niap.pps.sunset_changes | length }})</div>
    {% for pp in diff.niap.pps.sunset_changes %}
    <div class="item-row">
      <a class="item-link" href="https://www.niap-ccevs.org/Profile/PP.cfm?id={{ pp.pp_id }}" target="_blank">{{ pp.pp_short_name }}</a>
      <span class="item-meta">{{ pp.tech_type }} · Sunset: {{ pp.sunset_date }}</span>
    </div>
    {% endfor %}
    {% endif %}

    {% if diff.niap.pps.status_changes %}
    <div class="sub-hdr sub-updated">Status Changes ({{ diff.niap.pps.status_changes | length }})</div>
    {% for pp in diff.niap.pps.status_changes %}
    <div class="item-row">
      <a class="item-link" href="https://www.niap-ccevs.org/Profile/PP.cfm?id={{ pp.pp_id }}" target="_blank">{{ pp.pp_short_name }}</a>
      <span class="item-meta">{{ pp.tech_type }} · Status: {{ pp.status }}</span>
    </div>
    {% endfor %}
    {% endif %}

    {% if niap_pp_total == 0 %}
    <p class="no-change">No changes detected.</p>
    {% endif %}
  </div>
</div>

<!-- ═══════════ NIAP TDs ═══════════ -->
<div class="card {% if niap_td_total > 0 %}card-new{% endif %}" id="sec-niap-td">
  <div class="card-hdr" onclick="toggleCard(this)">
    <span>NIAP Technical Decisions</span>
    <span class="card-count">{{ niap_td_total }} change{% if niap_td_total != 1 %}s{% endif %}</span>
    <span class="sparkline">
      {% for v in sparklines.niap_td %}
      <span class="sp-bar" style="height:{{ v }}%" title="{{ v }}%"></span>
      {% endfor %}
    </span>
    <span class="toggle-icon">{% if niap_td_total == 0 %}▶{% else %}▼{% endif %}</span>
  </div>
  <div class="card-body {% if niap_td_total == 0 %}collapsed{% endif %}">

    {% if diff.niap.tds.added %}
    <div class="sub-hdr sub-new">New TDs ({{ diff.niap.tds.added | length }})</div>
    {% for td in diff.niap.tds.added %}
    <div class="item-row">
      <span class="item-link">{{ td.identifier }}</span>
      <span class="item-meta">{{ td.pp_short_name }} · {{ td.status }}</span>
    </div>
    {% endfor %}
    {% endif %}

    {% if diff.niap.tds.removed %}
    <div class="sub-hdr sub-removed">Removed TDs ({{ diff.niap.tds.removed | length }})</div>
    {% for td in diff.niap.tds.removed %}
    <div class="item-row">
      <span class="item-link">{{ td.identifier }}</span>
      <span class="item-meta">{{ td.pp_short_name }} · {{ td.status }}</span>
    </div>
    {% endfor %}
    {% endif %}

    {% if niap_td_total == 0 %}
    <p class="no-change">No changes detected.</p>
    {% endif %}
  </div>
</div>

<!-- ═══════════ Cisco NDcPP ═══════════ -->
<div class="card {% if cisco_total > 0 %}card-new{% endif %}" id="sec-cisco">
  <div class="card-hdr" onclick="toggleCard(this)">
    <span>Cisco NDcPP Evaluations</span>
    <span class="card-count">{{ cisco_total }} change{% if cisco_total != 1 %}s{% endif %}</span>
    <span class="toggle-icon">{% if cisco_total == 0 %}▶{% else %}▼{% endif %}</span>
  </div>
  <div class="card-body {% if cisco_total == 0 %}collapsed{% endif %}">

    {% if diff.niap.cisco_ndcpp.added %}
    <div class="sub-hdr sub-new">New Evaluations ({{ diff.niap.cisco_ndcpp.added | length }})</div>
    {% for item in diff.niap.cisco_ndcpp.added %}
    <div class="item-row">
      <a class="item-link" href="https://www.niap-ccevs.org/Product/PCL.cfm?tech_type=Network+Device" target="_blank">{{ item.vendor }} – {{ item.product }}</a>
      <span class="item-meta">{{ item.status }} · {{ item.comp_date }}</span>
    </div>
    {% endfor %}
    {% endif %}

    {% if diff.niap.cisco_ndcpp.removed %}
    <div class="sub-hdr sub-removed">Removed ({{ diff.niap.cisco_ndcpp.removed | length }})</div>
    {% for item in diff.niap.cisco_ndcpp.removed %}
    <div class="item-row">
      <span class="item-link">{{ item.vendor }} – {{ item.product }}</span>
      <span class="item-meta">{{ item.status }}</span>
    </div>
    {% endfor %}
    {% endif %}

    {% if diff.niap.cisco_ndcpp.newly_archived %}
    <div class="sub-hdr sub-updated">Newly Archived ({{ diff.niap.cisco_ndcpp.newly_archived | length }})</div>
    {% for item in diff.niap.cisco_ndcpp.newly_archived %}
    <div class="item-row">
      <span class="item-link">{{ item.vendor }} – {{ item.product }}</span>
      <span class="item-meta">{{ item.status }}</span>
    </div>
    {% endfor %}
    {% endif %}

    {% if cisco_total == 0 %}
    <p class="no-change">No changes detected.</p>
    {% endif %}
  </div>
</div>

<!-- ═══════════ NIAP News ═══════════ -->
<div class="card {% if niap_news_total > 0 %}card-new{% endif %}" id="sec-niap-news">
  <div class="card-hdr" onclick="toggleCard(this)">
    <span>NIAP News</span>
    <span class="card-count">{{ niap_news_total }} new item{% if niap_news_total != 1 %}s{% endif %}</span>
    <span class="sparkline">
      {% for v in sparklines.niap_news %}
      <span class="sp-bar" style="height:{{ v }}%" title="{{ v }}%"></span>
      {% endfor %}
    </span>
    <span class="toggle-icon">{% if niap_news_total == 0 %}▶{% else %}▼{% endif %}</span>
  </div>
  <div class="card-body {% if niap_news_total == 0 %}collapsed{% endif %}">

    {% if diff.niap.news.added %}
    {% for item in diff.niap.news.added %}
    <div class="item-row">
      <a class="item-link" href="{{ item.link or item.url or '#' }}" target="_blank">{{ item.title }}</a>
      <span class="item-meta">{{ item.date }}</span>
    </div>
    {% endfor %}
    {% endif %}

    {% if niap_news_total == 0 %}
    <p class="no-change">No new items.</p>
    {% endif %}
  </div>
</div>

<!-- ═══════════ CCTL Lab Intel ═══════════ -->
<div class="card {% if cctl_total > 0 %}card-new{% endif %}" id="sec-cctl">
  <div class="card-hdr" onclick="toggleCard(this)">
    <span>CCTL Lab Intel</span>
    <span class="card-count">{{ cctl_total }} new item{% if cctl_total != 1 %}s{% endif %}</span>
    <span class="sparkline">
      {% for v in sparklines.cctl %}
      <span class="sp-bar" style="height:{{ v }}%" title="{{ v }}%"></span>
      {% endfor %}
    </span>
    <span class="toggle-icon">{% if cctl_total == 0 %}▶{% else %}▼{% endif %}</span>
  </div>
  <div class="card-body {% if cctl_total == 0 %}collapsed{% endif %}">

    {% for lab_name, lab_items in diff.cctl_labs.items() %}
    {% if lab_items %}
    <div class="lab-row">
      <div class="lab-hdr" onclick="toggleLab(this)">
        <span class="lab-name">{{ lab_name }}</span>
        <span class="lab-cnt">{{ lab_items | length }} item{% if lab_items | length != 1 %}s{% endif %}</span>
        <span class="toggle-icon">▶</span>
      </div>
      <div class="lab-body collapsed">
        {% for item in lab_items %}
        <div class="item-row">
          <a class="item-link" href="{{ item.link }}" target="_blank">{{ item.title }}</a>
          <span class="item-meta">{{ item.published }}</span>
        </div>
        {% endfor %}
      </div>
    </div>
    {% endif %}
    {% endfor %}

    {% if cctl_total == 0 %}
    <p class="no-change">No new items.</p>
    {% endif %}
  </div>
</div>

<!-- ═══════════ CSfC Capability Packages ═══════════ -->
<div class="card {% if csfc_total > 0 %}card-updated{% endif %}" id="sec-csfc">
  <div class="card-hdr" onclick="toggleCard(this)">
    <span>CSfC Capability Packages</span>
    <span class="card-count">{{ csfc_total }} update{% if csfc_total != 1 %}s{% endif %}</span>
    <span class="sparkline">
      {% for v in sparklines.csfc %}
      <span class="sp-bar" style="height:{{ v }}%" title="{{ v }}%"></span>
      {% endfor %}
    </span>
    <span class="toggle-icon">{% if csfc_total == 0 %}▶{% else %}▼{% endif %}</span>
  </div>
  <div class="card-body {% if csfc_total == 0 %}collapsed{% endif %}">

    {% for cp_name, cp in diff.csfc.capability_packages.items() %}
    {% if cp.changed %}
    <div class="cp-row">
      <a class="item-link" href="{{ cp.url }}" target="_blank">{{ cp_name }}</a>
      <div class="cp-detail">
        {% if cp.old_last_modified and cp.new_last_modified %}
        <span class="cp-date">{{ cp.old_last_modified }} → {{ cp.new_last_modified }}</span>
        {% elif cp.old_content_length is defined and cp.new_content_length is defined %}
        <span class="cp-date">Size: {{ cp.old_content_length | int | filesizeformat }} → {{ cp.new_content_length | int | filesizeformat }}</span>
        {% else %}
        <span class="cp-date">Content changed</span>
        {% endif %}
      </div>
    </div>
    {% endif %}
    {% endfor %}

    {% if csfc_total == 0 %}
    <p class="no-change">No changes detected.</p>
    {% endif %}
  </div>
</div>

<!-- ═══════════ CC Crypto Docs ═══════════ -->
<div class="card {% if cc_crypto_total > 0 %}card-updated{% endif %}" id="sec-cc-crypto">
  <div class="card-hdr" onclick="toggleCard(this)">
    <span>CC Crypto Documentation</span>
    <span class="card-count">{{ cc_crypto_total }} update{% if cc_crypto_total != 1 %}s{% endif %}</span>
    <span class="sparkline">
      {% for v in sparklines.cc_crypto %}
      <span class="sp-bar" style="height:{{ v }}%" title="{{ v }}%"></span>
      {% endfor %}
    </span>
    <span class="toggle-icon">{% if cc_crypto_total == 0 %}▶{% else %}▼{% endif %}</span>
  </div>
  <div class="card-body {% if cc_crypto_total == 0 %}collapsed{% endif %}">

    {% for doc_name, doc in diff.cc_crypto.doc_headers.items() %}
    {% if doc.changed %}
    <div class="item-row">
      <a class="item-link" href="{{ doc.url }}" target="_blank">{{ doc_name }}</a>
      <span class="item-meta">
        {% if doc.old_last_modified and doc.new_last_modified %}
        {{ doc.old_last_modified }} → {{ doc.new_last_modified }}
        {% else %}
        Header changed
        {% endif %}
      </span>
    </div>
    {% endif %}
    {% endfor %}

    {% if cc_crypto_total == 0 %}
    <p class="no-change">No changes detected.</p>
    {% endif %}
  </div>
</div>

<!-- ═══════════ NIST Docs ═══════════ -->
<div class="card {% if nist_total > 0 %}card-updated{% endif %}" id="sec-nist">
  <div class="card-hdr" onclick="toggleCard(this)">
    <span>NIST Documentation</span>
    <span class="card-count">{{ nist_total }} update{% if nist_total != 1 %}s{% endif %}</span>
    <span class="sparkline">
      {% for v in sparklines.nist %}
      <span class="sp-bar" style="height:{{ v }}%" title="{{ v }}%"></span>
      {% endfor %}
    </span>
    <span class="toggle-icon">{% if nist_total == 0 %}▶{% else %}▼{% endif %}</span>
  </div>
  <div class="card-body {% if nist_total == 0 %}collapsed{% endif %}">

    {% for doc_name, doc in diff.nist.doc_headers.items() %}
    {% if doc.changed %}
    <div class="item-row">
      <a class="item-link" href="{{ doc.url }}" target="_blank">{{ doc_name }}</a>
      <span class="item-meta">
        {% if doc.old_last_modified and doc.new_last_modified %}
        {{ doc.old_last_modified }} → {{ doc.new_last_modified }}
        {% else %}
        Header changed
        {% endif %}
      </span>
    </div>
    {% endif %}
    {% endfor %}

    {% if nist_total == 0 %}
    <p class="no-change">No changes detected.</p>
    {% endif %}
  </div>
</div>

<!-- ═══════════ Alerts ═══════════ -->
{% if diff.alerts %}
<div class="card card-alert" id="sec-alerts">
  <div class="card-hdr" onclick="toggleCard(this)">
    <span>⚠ Alerts</span>
    <span class="card-count">{{ alert_total }} alert{% if alert_total != 1 %}s{% endif %}</span>
    <span class="toggle-icon">▼</span>
  </div>
  <div class="card-body">
    {% for alert in diff.alerts %}
    <div class="item-row alert-item">
      <span class="item-link">{{ alert.type }}</span>
      <span class="item-meta">{{ alert.message }}</span>
    </div>
    {% endfor %}
  </div>
</div>
{% endif %}

</div><!-- /grid -->

<footer>
  <span>CC Pulse · Auto-refreshes hourly · Data from NIAP, CSfC, NIST, CC Portal</span>
  <span>Last run: {{ generated_at }}</span>
</footer>

<script>
function toggleCard(hdr) {
  var body = hdr.nextElementSibling;
  var icon = hdr.querySelector('.toggle-icon');
  if (body.classList.contains('collapsed')) {
    body.classList.remove('collapsed');
    if (icon) icon.textContent = '\u25bc';
  } else {
    body.classList.add('collapsed');
    if (icon) icon.textContent = '\u25b6';
  }
}
function toggleLab(hdr) {
  var body = hdr.nextElementSibling;
  var icon = hdr.querySelector('.toggle-icon');
  if (body.classList.contains('collapsed')) {
    body.classList.remove('collapsed');
    if (icon) icon.textContent = '\u25bc';
  } else {
    body.classList.add('collapsed');
    if (icon) icon.textContent = '\u25b6';
  }
}
</script>

</body>
</html>
"""


# ── RSS feed builder ────────────────────────────────────────────────────────
def _build_rss(diff: dict, generated_at: str) -> str:
    """Return an RSS 2.0 XML string summarising the diff."""
    items_xml = []

    for pp in diff.get("niap", {}).get("pps", {}).get("added", []):
        title = f"New PP: {pp.get('pp_short_name', '')}"
        link  = f"https://www.niap-ccevs.org/Profile/PP.cfm?id={pp.get('pp_id', '')}"
        items_xml.append(
            f"<item><title>{title}</title><link>{link}</link>"
            f"<description>{pp.get('pp_name', '')}</description></item>"
        )

    for pp in diff.get("niap", {}).get("pps", {}).get("removed", []):
        title = f"Removed PP: {pp.get('pp_short_name', '')}"
        link  = f"https://www.niap-ccevs.org/Profile/PP.cfm?id={pp.get('pp_id', '')}"
        items_xml.append(
            f"<item><title>{title}</title><link>{link}</link>"
            f"<description>{pp.get('pp_name', '')}</description></item>"
        )

    for td in diff.get("niap", {}).get("tds", {}).get("added", []):
        title = f"New TD: {td.get('identifier', '')}"
        items_xml.append(
            f"<item><title>{title}</title><link>https://www.niap-ccevs.org/</link>"
            f"<description>{td.get('pp_short_name', '')}</description></item>"
        )

    for item in diff.get("niap", {}).get("news", {}).get("added", []):
        title = item.get("title", "NIAP News")
        link  = item.get("link") or item.get("url") or "https://www.niap-ccevs.org/"
        items_xml.append(
            f"<item><title>{title}</title><link>{link}</link>"
            f"<description>{item.get('date', '')}</description></item>"
        )

    for lab, lab_items in diff.get("cctl_labs", {}).items():
        for it in (lab_items or []):
            title = f"[{lab}] {it.get('title', '')}"
            link  = it.get("link", "#")
            items_xml.append(
                f"<item><title>{title}</title><link>{link}</link>"
                f"<description>{it.get('summary', '')[:200]}</description></item>"
            )

    for cp_name, cp in diff.get("csfc", {}).get("capability_packages", {}).items():
        if cp.get("changed"):
            link = cp.get("url", "https://www.nsa.gov/")
            items_xml.append(
                f"<item><title>CSfC CP Updated: {cp_name}</title>"
                f"<link>{link}</link><description>Capability package updated.</description></item>"
            )

    for doc_name, doc in diff.get("nist", {}).get("doc_headers", {}).items():
        if doc.get("changed"):
            link = doc.get("url", "https://csrc.nist.gov/")
            items_xml.append(
                f"<item><title>NIST Doc Updated: {doc_name}</title>"
                f"<link>{link}</link><description>Document header updated.</description></item>"
            )

    for doc_name, doc in diff.get("cc_crypto", {}).get("doc_headers", {}).items():
        if doc.get("changed"):
            link = doc.get("url", "https://www.commoncriteriaportal.org/")
            items_xml.append(
                f"<item><title>CC Crypto Doc Updated: {doc_name}</title>"
                f"<link>{link}</link><description>Document header updated.</description></item>"
            )

    for alert in diff.get("alerts", []):
        items_xml.append(
            f"<item><title>ALERT: {alert.get('type', '')}</title>"
            f"<link>https://kr15tyk.github.io/CC-pulse/cc_dashboard.html</link>"
            f"<description>{alert.get('message', '')}</description></item>"
        )

    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<rss version="2.0"><channel>\n'
        f'<title>CC Pulse</title>\n'
        f'<link>https://kr15tyk.github.io/CC-pulse/cc_dashboard.html</link>\n'
        f'<description>Common Criteria monitoring · {generated_at}</description>\n'
        + "\n".join(items_xml)
        + "\n</channel></rss>\n"
    )
    return xml


# ── Main render entry point ──────────────────────────────────────────────────
def render_dashboard(diff: dict, output_dir: str = "docs") -> None:
    """Render the HTML dashboard and RSS feed from a diff dict."""
    import os
    from datetime import datetime, timezone
    from jinja2 import Environment

    os.makedirs(output_dir, exist_ok=True)
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    # ── Compute totals ───────────────────────────────────────────────────────
    niap         = diff.get("niap", {})
    pps          = niap.get("pps", {})
    tds          = niap.get("tds", {})
    cisco        = niap.get("cisco_ndcpp", {})
    news         = niap.get("news", {})

    niap_pp_total   = (len(pps.get("added", [])) + len(pps.get("removed", [])) +
                       len(pps.get("sunset_changes", [])) + len(pps.get("status_changes", [])))
    niap_td_total   = len(tds.get("added", [])) + len(tds.get("removed", []))
    cisco_total     = (len(cisco.get("added", [])) + len(cisco.get("removed", [])) +
                       len(cisco.get("newly_archived", [])))
    niap_news_total = len(news.get("added", []))

    cctl_total      = sum(len(v) for v in diff.get("cctl_labs", {}).values() if v)
    csfc_total      = sum(1 for cp in diff.get("csfc", {}).get("capability_packages", {}).values()
                          if cp.get("changed"))
    cc_crypto_total = sum(1 for d in diff.get("cc_crypto", {}).get("doc_headers", {}).values()
                          if d.get("changed"))
    nist_total      = sum(1 for d in diff.get("nist", {}).get("doc_headers", {}).values()
                          if d.get("changed"))
    alert_total     = len(diff.get("alerts", []))

    niap_total_stat   = niap_pp_total + niap_td_total + cisco_total + niap_news_total
    cctl_total_stat   = cctl_total
    csfc_total_stat   = csfc_total
    nist_total_stat   = nist_total + cc_crypto_total

    # ── Sparkline data ───────────────────────────────────────────────────────
    recent_diffs = _load_recent_diffs()
    def _sp(section_key):
dashboard.py — Renders the daily HTML dashboard and RSS feed.

Features:
  - Keyword alert banner (red, top of page)
  - Trend summary stats panel with anchor links (NIAP + CSfC + CC Crypto + NIST)
  - Collapsible cards (empty sections auto-collapsed)
  - Color-coded section headers (green=new, amber=updated, red=removed/alert)
  - Clickable source links on all items
  - CSfC CP entries show content-length change when dates unavailable
  - 7-day activity sparkline per section
  - Responsive mobile layout
  - CCTL Lab Intel: compact per-lab counts with expand
  - RSS feed (cc_feed.xml) with items from all domains
  - Structured logging
"""
import glob
import json
import logging
import os
from datetime import datetime, timezone
from xml.sax.saxutils import escape as xml_escape

from jinja2 import Template

import config

log = logging.getLogger(__name__)


def _load_recent_diffs(n: int = 7) -> list[dict]:
    """Load the most recent N daily diff files for sparkline data."""
    pattern = os.path.join(config.SNAPSHOTS_DIR, "diffs", "*_diff.json")
    files = sorted(glob.glob(pattern))[-n:]
    result = []
    for f in files:
        try:
            with open(f, encoding="utf-8") as fh:
                result.append(json.load(fh))
        except Exception:
            pass
    return result


def _section_daily_counts(diffs: list[dict]) -> dict:
    """Return per-section change counts for the last N days (for sparkline)."""
    counts: dict[str, list[int]] = {
        "niap_pps": [], "niap_tds": [], "niap_cisco": [],
        "niap_news": [], "csfc": [], "cc_crypto": [], "nist": [],
    }
    for d in diffs:
        pp = d.get("niap", {}).get("pps", {})
        counts["niap_pps"].append(len(pp.get("added", [])) + len(pp.get("removed", [])) + len(pp.get("sunset_changes", [])))
        td = d.get("niap", {}).get("tds", {})
        counts["niap_tds"].append(len(td.get("added", [])) + len(td.get("removed", [])))
        cn = d.get("niap", {}).get("cisco_ndcpp", {})
        counts["niap_cisco"].append(len(cn.get("added", [])) + len(cn.get("removed", [])) + len(cn.get("newly_archived", [])))
        counts["niap_news"].append(len(d.get("niap", {}).get("news", {}).get("added", [])))
        csfc_cps = d.get("csfc", {}).get("capability_packages", {})
        counts["csfc"].append(sum(1 for v in csfc_cps.values() if v.get("changed")))
        cc_docs = d.get("cc_crypto", {}).get("doc_headers", {})
        counts["cc_crypto"].append(sum(1 for v in cc_docs.values() if v.get("changed")))
        nist_docs = d.get("nist", {}).get("doc_headers", {})
        counts["nist"].append(sum(1 for v in nist_docs.values() if v.get("changed")))
    return counts


# ── Dashboard HTML template ────────────────────────────────────────────────
DASHBOARD_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta http-equiv="refresh" content="3600">
<meta name="viewport" content="width=device-width, initial-scale=1">
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
header { background: var(--navy); color: white; padding: 14px 24px;
         display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 8px; }
header h1 { font-size: 1.3rem; letter-spacing: 0.05em; }
header span { font-size: 0.82rem; opacity: 0.75; }
.alert-banner { background: #a82222; color: white; padding: 12px 24px; }
.alert-banner h2 { font-size: 0.95rem; margin-bottom: 8px; letter-spacing: 0.05em; }
.alert-item { background: rgba(255,255,255,0.15); border-radius: 6px;
              padding: 8px 12px; margin-bottom: 6px; font-size: 0.875rem; }
.alert-item strong { display: block; }
.kw-chip { display: inline-block; background: rgba(255,255,255,0.3); border-radius: 4px;
           font-size: 0.7rem; font-weight: 700; padding: 1px 6px; margin: 2px 2px 0 0; }

/* ── Summary bar ── */
.trend-bar { background: white; border-bottom: 1px solid var(--border);
             padding: 10px 24px; display: flex; gap: 4px; flex-wrap: wrap; }
.stat { text-align: center; padding: 6px 12px; border-radius: 6px;
        cursor: pointer; transition: background 0.15s; min-width: 70px; flex: 1; }
.stat:hover { background: var(--gray); }
.stat a { text-decoration: none; color: inherit; display: block; }
.stat-num { font-size: 1.5rem; font-weight: 700; color: var(--navy); }
.stat-num.alert-num { color: #a82222; }
.stat-num.active-num { color: var(--green); }
.stat-lbl { font-size: 0.65rem; color: #666; text-transform: uppercase; letter-spacing: 0.06em; }

/* ── Cards grid ── */
.grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(480px, 1fr));
        gap: 18px; padding: 20px 24px; }
@media (max-width: 600px) {
  .grid { grid-template-columns: 1fr; padding: 12px; gap: 12px; }
  .trend-bar { gap: 2px; }
  .stat { min-width: 50px; padding: 4px 6px; }
  .stat-num { font-size: 1.2rem; }
  header { padding: 10px 14px; }
}

/* ── Card ── */
.card { background: white; border-radius: 8px; box-shadow: 0 1px 4px rgba(0,0,0,0.1);
        border-top: 4px solid #aaa; overflow: hidden; }
.card.has-changes.green  { border-color: var(--green); }
.card.has-changes.amber  { border-color: var(--amber); }
.card.has-changes.red    { border-color: var(--red); }
.card.has-changes.purple { border-color: var(--purple); }
.card.has-changes.teal   { border-color: var(--teal); }
.card.has-changes.blue   { border-color: var(--blue); }
.card-header { padding: 12px 16px; background: var(--gray); border-bottom: 1px solid var(--border);
               display: flex; justify-content: space-between; align-items: center;
               cursor: pointer; user-select: none; }
.card-header:hover { background: #e8ecf3; }
.card-header h2 { font-size: 0.9rem; font-weight: 700; text-transform: uppercase;
                  letter-spacing: 0.06em; color: var(--navy); display: flex; align-items: center; gap: 8px; }
.header-right { display: flex; align-items: center; gap: 10px; }
.badge { font-size: 0.72rem; font-weight: 700; padding: 2px 9px; border-radius: 12px;
         background: var(--blue); color: white; white-space: nowrap; }
.badge.zero { background: #bbb; }
.badge.has-changes { background: var(--green); }
.badge.has-changes.amber { background: var(--amber); }
.badge.has-changes.purple { background: var(--purple); }
.badge.has-changes.teal { background: var(--teal); }
.chevron { font-size: 0.75rem; color: #888; transition: transform 0.2s; }
.card.collapsed .chevron { transform: rotate(-90deg); }
.card.collapsed .card-body { display: none; }
.card-body { padding: 12px 16px; }

/* ── Sparkline ── */
.sparkline-wrap { display: flex; align-items: flex-end; gap: 2px; height: 22px; margin-left: 4px; }
.spark-bar { width: 5px; min-height: 2px; background: #bbb; border-radius: 2px 2px 0 0;
             transition: background 0.2s; }
.spark-bar.active { background: var(--green); }

/* ── Items ── */
.item { padding: 7px 0; border-bottom: 1px solid #eee; font-size: 0.875rem; }
.item:last-child { border-bottom: none; }
.item-title { font-weight: 600; color: var(--navy); }
.item-title a { color: var(--blue); text-decoration: none; }
.item-title a:hover { text-decoration: underline; }
.item-meta { color: #666; font-size: 0.78rem; margin-top: 2px; }
.tag { display: inline-block; font-size: 0.68rem; font-weight: 700;
       padding: 1px 6px; border-radius: 4px; margin-right: 4px; text-transform: uppercase; }
.tag-add    { background: #d4edda; color: #155724; }
.tag-remove { background: #f8d7da; color: #721c24; }
.tag-sunset { background: #fff3cd; color: #856404; }
.tag-cat    { background: #e2eafc; color: #1a4a8a; }
.tag-csfc   { background: #e8d5ff; color: #5a2d82; }
.tag-nist   { background: #d0e4ff; color: #003366; }
.tag-crypto { background: #ffd5e8; color: #7a0040; }
.tag-update { background: #fff3cd; color: #856404; }
.tag-cert   { background: #d4edda; color: #155724; }
.empty { color: #999; font-size: 0.82rem; font-style: italic; padding: 8px 0; }
.section-label { font-size: 0.68rem; font-weight: 700; color: #888;
                 text-transform: uppercase; letter-spacing: 0.08em; padding: 8px 0 4px; }

/* ── Lab Intel ── */
.lab-row { display: flex; justify-content: space-between; align-items: center;
           padding: 6px 0; border-bottom: 1px solid #eee; font-size: 0.85rem; }
.lab-row:last-child { border-bottom: none; }
.lab-name { font-weight: 600; color: var(--navy); }
.lab-count { font-size: 0.75rem; padding: 1px 7px; border-radius: 10px;
             background: var(--green); color: white; font-weight: 700; }
.lab-count.zero { background: #ccc; color: #666; }
.lab-items { padding: 0 0 0 12px; margin-top: 4px; }

/* ── CP update detail ── */
.cp-detail { font-size: 0.75rem; color: #666; margin-top: 2px; }
.cp-change-badge { display: inline-block; font-size: 0.68rem; font-weight: 700;
                   padding: 1px 5px; border-radius: 3px; background: #fff3cd; color: #856404; margin-right: 4px; }

footer { text-align: center; padding: 16px; color: #888; font-size: 0.78rem; }
footer a { color: #0057a8; }
</style>
</head>
<body>
<header>
  <h1>&#127760; CC Pulse Dashboard</h1>
  <span>Last updated: {{ date }} {{ time }} UTC &bull; <a href="{{ rss_filename }}" style="color:rgba(255,255,255,0.7)">&#128280; RSS</a></span>
</header>

{# ── Keyword alert banner ── #}
{% if diff.alerts %}
<div class="alert-banner">
  <h2>&#9888; {{ diff.alerts|length }} HIGH-PRIORITY ALERT(S) &mdash; KEYWORD MATCH</h2>
  {% for a in diff.alerts %}
  <div class="alert-item">
    <strong>[{{ a.source }}] {{ a.title }}</strong>
    {% for kw in a.matched_keywords %}<span class="kw-chip">{{ kw }}</span>{% endfor %}
  </div>
  {% endfor %}
</div>
{% endif %}

{# ── Trend summary bar ── #}
{% set pp = diff.niap.pps %}
{% set td = diff.niap.tds %}
{% set cn = diff.niap.cisco_ndcpp %}
{% set pp_total = pp.added|length + pp.removed|length + pp.sunset_changes|length %}
{% set td_total = td.added|length + td.removed|length %}
{% set cn_total = cn.added|length + cn.removed|length + cn.newly_archived|length %}
{% set news_total = diff.niap.news.added|length %}
{% set csfc_cp_total = diff.csfc.capability_packages.values()|selectattr('changed')|list|length if diff.csfc.capability_packages is defined else 0 %}
{% set nist_total_stat = diff.nist.doc_headers.values()|selectattr('changed')|list|length if diff.nist.doc_headers is defined else 0 %}
{% set alert_total = diff.alerts|length %}

<div class="trend-bar">
  <div class="stat"><a href="#sec-niap-pp">
    <div class="stat-num {% if pp_total > 0 %}active-num{% endif %}">{{ pp_total }}</div>
    <div class="stat-lbl">New PPs</div>
  </a></div>
  <div class="stat"><a href="#sec-niap-pp">
    <div class="stat-num {% if pp.sunset_changes|length > 0 %}active-num{% endif %}">{{ pp.sunset_changes|length }}</div>
    <div class="stat-lbl">PP Sunsets</div>
  </a></div>
  <div class="stat"><a href="#sec-niap-td">
    <div class="stat-num {% if td_total > 0 %}active-num{% endif %}">{{ td_total }}</div>
    <div class="stat-lbl">New TDs</div>
  </a></div>
  <div class="stat"><a href="#sec-cisco">
    <div class="stat-num {% if cn_total > 0 %}active-num{% endif %}">{{ cn_total }}</div>
    <div class="stat-lbl">Cisco Cert.</div>
  </a></div>
  <div class="stat"><a href="#sec-niap-news">
    <div class="stat-num {% if news_total > 0 %}active-num{% endif %}">{{ news_total }}</div>
    <div class="stat-lbl">NIAP News</div>
  </a></div>
  <div class="stat"><a href="#sec-csfc">
    <div class="stat-num {% if csfc_cp_total > 0 %}active-num{% endif %}">{{ csfc_cp_total }}</div>
    <div class="stat-lbl">CSfC CP Updates</div>
  </a></div>
  <div class="stat"><a href="#sec-nist">
    <div class="stat-num {% if nist_total_stat > 0 %}active-num{% endif %}">{{ nist_total_stat }}</div>
    <div class="stat-lbl">NIST Doc Updates</div>
  </a></div>
  <div class="stat"><a href="#sec-niap-pp">
    <div class="stat-num {% if alert_total > 0 %}alert-num{% endif %}">{{ alert_total }}</div>
    <div class="stat-lbl">Alerts</div>
  </a></div>
</div>

<div class="grid">

<!-- ═══════════ NIAP PPs ═══════════ -->
<div class="card {% if niap_pp_total > 0 %}card-new{% endif %}" id="sec-niap-pp">
  <div class="card-hdr" onclick="toggleCard(this)">
    <span>NIAP Protection Profiles</span>
    <span class="card-count">{{ niap_pp_total }} change{% if niap_pp_total != 1 %}s{% endif %}</span>
    <span class="sparkline">
      {% for v in sparklines.niap_pp %}
      <span class="sp-bar" style="height:{{ v }}%" title="{{ v }}%"></span>
      {% endfor %}
    </span>
    <span class="toggle-icon">{% if niap_pp_total == 0 %}▶{% else %}▼{% endif %}</span>
  </div>
  <div class="card-body {% if niap_pp_total == 0 %}collapsed{% endif %}">

    {% if diff.niap.pps.added %}
    <div class="sub-hdr sub-new">New PPs ({{ diff.niap.pps.added | length }})</div>
    {% for pp in diff.niap.pps.added %}
    <div class="item-row">
      <a class="item-link" href="https://www.niap-ccevs.org/Profile/PP.cfm?id={{ pp.pp_id }}" target="_blank">{{ pp.pp_short_name }}</a>
      <span class="item-meta">{{ pp.tech_type }} · {{ pp.pp_date }}</span>
    </div>
    {% endfor %}
    {% endif %}

    {% if diff.niap.pps.removed %}
    <div class="sub-hdr sub-removed">Removed PPs ({{ diff.niap.pps.removed | length }})</div>
    {% for pp in diff.niap.pps.removed %}
    <div class="item-row">
      <a class="item-link" href="https://www.niap-ccevs.org/Profile/PP.cfm?id={{ pp.pp_id }}" target="_blank">{{ pp.pp_short_name }}</a>
      <span class="item-meta">{{ pp.tech_type }} · {{ pp.pp_date }}</span>
    </div>
    {% endfor %}
    {% endif %}

    {% if diff.niap.pps.sunset_changes %}
    <div class="sub-hdr sub-updated">Sunset Changes ({{ diff.niap.pps.sunset_changes | length }})</div>
    {% for pp in diff.niap.pps.sunset_changes %}
    <div class="item-row">
      <a class="item-link" href="https://www.niap-ccevs.org/Profile/PP.cfm?id={{ pp.pp_id }}" target="_blank">{{ pp.pp_short_name }}</a>
      <span class="item-meta">{{ pp.tech_type }} · Sunset: {{ pp.sunset_date }}</span>
    </div>
    {% endfor %}
    {% endif %}

    {% if diff.niap.pps.status_changes %}
    <div class="sub-hdr sub-updated">Status Changes ({{ diff.niap.pps.status_changes | length }})</div>
    {% for pp in diff.niap.pps.status_changes %}
    <div class="item-row">
      <a class="item-link" href="https://www.niap-ccevs.org/Profile/PP.cfm?id={{ pp.pp_id }}" target="_blank">{{ pp.pp_short_name }}</a>
      <span class="item-meta">{{ pp.tech_type }} · Status: {{ pp.status }}</span>
    </div>
    {% endfor %}
    {% endif %}

    {% if niap_pp_total == 0 %}
    <p class="no-change">No changes detected.</p>
    {% endif %}
  </div>
</div>

<!-- ═══════════ NIAP TDs ═══════════ -->
<div class="card {% if niap_td_total > 0 %}card-new{% endif %}" id="sec-niap-td">
  <div class="card-hdr" onclick="toggleCard(this)">
    <span>NIAP Technical Decisions</span>
    <span class="card-count">{{ niap_td_total }} change{% if niap_td_total != 1 %}s{% endif %}</span>
    <span class="sparkline">
      {% for v in sparklines.niap_td %}
      <span class="sp-bar" style="height:{{ v }}%" title="{{ v }}%"></span>
      {% endfor %}
    </span>
    <span class="toggle-icon">{% if niap_td_total == 0 %}▶{% else %}▼{% endif %}</span>
  </div>
  <div class="card-body {% if niap_td_total == 0 %}collapsed{% endif %}">

    {% if diff.niap.tds.added %}
    <div class="sub-hdr sub-new">New TDs ({{ diff.niap.tds.added | length }})</div>
    {% for td in diff.niap.tds.added %}
    <div class="item-row">
      <span class="item-link">{{ td.identifier }}</span>
      <span class="item-meta">{{ td.pp_short_name }} · {{ td.status }}</span>
    </div>
    {% endfor %}
    {% endif %}

    {% if diff.niap.tds.removed %}
    <div class="sub-hdr sub-removed">Removed TDs ({{ diff.niap.tds.removed | length }})</div>
    {% for td in diff.niap.tds.removed %}
    <div class="item-row">
      <span class="item-link">{{ td.identifier }}</span>
      <span class="item-meta">{{ td.pp_short_name }} · {{ td.status }}</span>
    </div>
    {% endfor %}
    {% endif %}

    {% if niap_td_total == 0 %}
    <p class="no-change">No changes detected.</p>
    {% endif %}
  </div>
</div>

<!-- ═══════════ Cisco NDcPP ═══════════ -->
<div class="card {% if cisco_total > 0 %}card-new{% endif %}" id="sec-cisco">
  <div class="card-hdr" onclick="toggleCard(this)">
    <span>Cisco NDcPP Evaluations</span>
    <span class="card-count">{{ cisco_total }} change{% if cisco_total != 1 %}s{% endif %}</span>
    <span class="toggle-icon">{% if cisco_total == 0 %}▶{% else %}▼{% endif %}</span>
  </div>
  <div class="card-body {% if cisco_total == 0 %}collapsed{% endif %}">

    {% if diff.niap.cisco_ndcpp.added %}
    <div class="sub-hdr sub-new">New Evaluations ({{ diff.niap.cisco_ndcpp.added | length }})</div>
    {% for item in diff.niap.cisco_ndcpp.added %}
    <div class="item-row">
      <a class="item-link" href="https://www.niap-ccevs.org/Product/PCL.cfm?tech_type=Network+Device" target="_blank">{{ item.vendor }} – {{ item.product }}</a>
      <span class="item-meta">{{ item.status }} · {{ item.comp_date }}</span>
    </div>
    {% endfor %}
    {% endif %}

    {% if diff.niap.cisco_ndcpp.removed %}
    <div class="sub-hdr sub-removed">Removed ({{ diff.niap.cisco_ndcpp.removed | length }})</div>
    {% for item in diff.niap.cisco_ndcpp.removed %}
    <div class="item-row">
      <span class="item-link">{{ item.vendor }} – {{ item.product }}</span>
      <span class="item-meta">{{ item.status }}</span>
    </div>
    {% endfor %}
    {% endif %}

    {% if diff.niap.cisco_ndcpp.newly_archived %}
    <div class="sub-hdr sub-updated">Newly Archived ({{ diff.niap.cisco_ndcpp.newly_archived | length }})</div>
    {% for item in diff.niap.cisco_ndcpp.newly_archived %}
    <div class="item-row">
      <span class="item-link">{{ item.vendor }} – {{ item.product }}</span>
      <span class="item-meta">{{ item.status }}</span>
    </div>
    {% endfor %}
    {% endif %}

    {% if cisco_total == 0 %}
    <p class="no-change">No changes detected.</p>
    {% endif %}
  </div>
</div>

<!-- ═══════════ NIAP News ═══════════ -->
<div class="card {% if niap_news_total > 0 %}card-new{% endif %}" id="sec-niap-news">
  <div class="card-hdr" onclick="toggleCard(this)">
    <span>NIAP News</span>
    <span class="card-count">{{ niap_news_total }} new item{% if niap_news_total != 1 %}s{% endif %}</span>
    <span class="sparkline">
      {% for v in sparklines.niap_news %}
      <span class="sp-bar" style="height:{{ v }}%" title="{{ v }}%"></span>
      {% endfor %}
    </span>
    <span class="toggle-icon">{% if niap_news_total == 0 %}▶{% else %}▼{% endif %}</span>
  </div>
  <div class="card-body {% if niap_news_total == 0 %}collapsed{% endif %}">

    {% if diff.niap.news.added %}
    {% for item in diff.niap.news.added %}
    <div class="item-row">
      <a class="item-link" href="{{ item.link or item.url or '#' }}" target="_blank">{{ item.title }}</a>
      <span class="item-meta">{{ item.date }}</span>
    </div>
    {% endfor %}
    {% endif %}

    {% if niap_news_total == 0 %}
    <p class="no-change">No new items.</p>
    {% endif %}
  </div>
</div>

<!-- ═══════════ CCTL Lab Intel ═══════════ -->
<div class="card {% if cctl_total > 0 %}card-new{% endif %}" id="sec-cctl">
  <div class="card-hdr" onclick="toggleCard(this)">
    <span>CCTL Lab Intel</span>
    <span class="card-count">{{ cctl_total }} new item{% if cctl_total != 1 %}s{% endif %}</span>
    <span class="sparkline">
      {% for v in sparklines.cctl %}
      <span class="sp-bar" style="height:{{ v }}%" title="{{ v }}%"></span>
      {% endfor %}
    </span>
    <span class="toggle-icon">{% if cctl_total == 0 %}▶{% else %}▼{% endif %}</span>
  </div>
  <div class="card-body {% if cctl_total == 0 %}collapsed{% endif %}">

    {% for lab_name, lab_items in diff.cctl_labs.items() %}
    {% if lab_items %}
    <div class="lab-row">
      <div class="lab-hdr" onclick="toggleLab(this)">
        <span class="lab-name">{{ lab_name }}</span>
        <span class="lab-cnt">{{ lab_items | length }} item{% if lab_items | length != 1 %}s{% endif %}</span>
        <span class="toggle-icon">▶</span>
      </div>
      <div class="lab-body collapsed">
        {% for item in lab_items %}
        <div class="item-row">
          <a class="item-link" href="{{ item.link }}" target="_blank">{{ item.title }}</a>
          <span class="item-meta">{{ item.published }}</span>
        </div>
        {% endfor %}
      </div>
    </div>
    {% endif %}
    {% endfor %}

    {% if cctl_total == 0 %}
    <p class="no-change">No new items.</p>
    {% endif %}
  </div>
</div>

<!-- ═══════════ CSfC Capability Packages ═══════════ -->
<div class="card {% if csfc_total > 0 %}card-updated{% endif %}" id="sec-csfc">
  <div class="card-hdr" onclick="toggleCard(this)">
    <span>CSfC Capability Packages</span>
    <span class="card-count">{{ csfc_total }} update{% if csfc_total != 1 %}s{% endif %}</span>
    <span class="sparkline">
      {% for v in sparklines.csfc %}
      <span class="sp-bar" style="height:{{ v }}%" title="{{ v }}%"></span>
      {% endfor %}
    </span>
    <span class="toggle-icon">{% if csfc_total == 0 %}▶{% else %}▼{% endif %}</span>
  </div>
  <div class="card-body {% if csfc_total == 0 %}collapsed{% endif %}">

    {% for cp_name, cp in diff.csfc.capability_packages.items() %}
    {% if cp.changed %}
    <div class="cp-row">
      <a class="item-link" href="{{ cp.url }}" target="_blank">{{ cp_name }}</a>
      <div class="cp-detail">
        {% if cp.old_last_modified and cp.new_last_modified %}
        <span class="cp-date">{{ cp.old_last_modified }} → {{ cp.new_last_modified }}</span>
        {% elif cp.old_content_length is defined and cp.new_content_length is defined %}
        <span class="cp-date">Size: {{ cp.old_content_length | int | filesizeformat }} → {{ cp.new_content_length | int | filesizeformat }}</span>
        {% else %}
        <span class="cp-date">Content changed</span>
        {% endif %}
      </div>
    </div>
    {% endif %}
    {% endfor %}

    {% if csfc_total == 0 %}
    <p class="no-change">No changes detected.</p>
    {% endif %}
  </div>
</div>

<!-- ═══════════ CC Crypto Docs ═══════════ -->
<div class="card {% if cc_crypto_total > 0 %}card-updated{% endif %}" id="sec-cc-crypto">
  <div class="card-hdr" onclick="toggleCard(this)">
    <span>CC Crypto Documentation</span>
    <span class="card-count">{{ cc_crypto_total }} update{% if cc_crypto_total != 1 %}s{% endif %}</span>
    <span class="sparkline">
      {% for v in sparklines.cc_crypto %}
      <span class="sp-bar" style="height:{{ v }}%" title="{{ v }}%"></span>
      {% endfor %}
    </span>
    <span class="toggle-icon">{% if cc_crypto_total == 0 %}▶{% else %}▼{% endif %}</span>
  </div>
  <div class="card-body {% if cc_crypto_total == 0 %}collapsed{% endif %}">

    {% for doc_name, doc in diff.cc_crypto.doc_headers.items() %}
    {% if doc.changed %}
    <div class="item-row">
      <a class="item-link" href="{{ doc.url }}" target="_blank">{{ doc_name }}</a>
      <span class="item-meta">
        {% if doc.old_last_modified and doc.new_last_modified %}
        {{ doc.old_last_modified }} → {{ doc.new_last_modified }}
        {% else %}
        Header changed
        {% endif %}
      </span>
    </div>
    {% endif %}
    {% endfor %}

    {% if cc_crypto_total == 0 %}
    <p class="no-change">No changes detected.</p>
    {% endif %}
  </div>
</div>

<!-- ═══════════ NIST Docs ═══════════ -->
<div class="card {% if nist_total > 0 %}card-updated{% endif %}" id="sec-nist">
  <div class="card-hdr" onclick="toggleCard(this)">
    <span>NIST Documentation</span>
    <span class="card-count">{{ nist_total }} update{% if nist_total != 1 %}s{% endif %}</span>
    <span class="sparkline">
      {% for v in sparklines.nist %}
      <span class="sp-bar" style="height:{{ v }}%" title="{{ v }}%"></span>
      {% endfor %}
    </span>
    <span class="toggle-icon">{% if nist_total == 0 %}▶{% else %}▼{% endif %}</span>
  </div>
  <div class="card-body {% if nist_total == 0 %}collapsed{% endif %}">

    {% for doc_name, doc in diff.nist.doc_headers.items() %}
    {% if doc.changed %}
    <div class="item-row">
      <a class="item-link" href="{{ doc.url }}" target="_blank">{{ doc_name }}</a>
      <span class="item-meta">
        {% if doc.old_last_modified and doc.new_last_modified %}
        {{ doc.old_last_modified }} → {{ doc.new_last_modified }}
        {% else %}
        Header changed
        {% endif %}
      </span>
    </div>
    {% endif %}
    {% endfor %}

    {% if nist_total == 0 %}
    <p class="no-change">No changes detected.</p>
    {% endif %}
  </div>
</div>

<!-- ═══════════ Alerts ═══════════ -->
{% if diff.alerts %}
<div class="card card-alert" id="sec-alerts">
  <div class="card-hdr" onclick="toggleCard(this)">
    <span>⚠ Alerts</span>
    <span class="card-count">{{ alert_total }} alert{% if alert_total != 1 %}s{% endif %}</span>
    <span class="toggle-icon">▼</span>
  </div>
  <div class="card-body">
    {% for alert in diff.alerts %}
    <div class="item-row alert-item">
      <span class="item-link">{{ alert.type }}</span>
      <span class="item-meta">{{ alert.message }}</span>
    </div>
    {% endfor %}
  </div>
</div>
{% endif %}

</div><!-- /grid -->

<footer>
  <span>CC Pulse · Auto-refreshes hourly · Data from NIAP, CSfC, NIST, CC Portal</span>
  <span>Last run: {{ generated_at }}</span>
</footer>

<script>
function toggleCard(hdr) {
  var body = hdr.nextElementSibling;
  var icon = hdr.querySelector('.toggle-icon');
  if (body.classList.contains('collapsed')) {
    body.classList.remove('collapsed');
    if (icon) icon.textContent = '\u25bc';
  } else {
    body.classList.add('collapsed');
    if (icon) icon.textContent = '\u25b6';
  }
}
function toggleLab(hdr) {
  var body = hdr.nextElementSibling;
  var icon = hdr.querySelector('.toggle-icon');
  if (body.classList.contains('collapsed')) {
    body.classList.remove('collapsed');
    if (icon) icon.textContent = '\u25bc';
  } else {
    body.classList.add('collapsed');
    if (icon) icon.textContent = '\u25b6';
  }
}
</script>

</body>
</html>
"""


# ── RSS feed builder ────────────────────────────────────────────────────────
def _build_rss(diff: dict, generated_at: str) -> str:
    """Return an RSS 2.0 XML string summarising the diff."""
    items_xml = []

    for pp in diff.get("niap", {}).get("pps", {}).get("added", []):
        title = f"New PP: {pp.get('pp_short_name', '')}"
        link  = f"https://www.niap-ccevs.org/Profile/PP.cfm?id={pp.get('pp_id', '')}"
        items_xml.append(
            f"<item><title>{title}</title><link>{link}</link>"
            f"<description>{pp.get('pp_name', '')}</description></item>"
        )

    for pp in diff.get("niap", {}).get("pps", {}).get("removed", []):
        title = f"Removed PP: {pp.get('pp_short_name', '')}"
        link  = f"https://www.niap-ccevs.org/Profile/PP.cfm?id={pp.get('pp_id', '')}"
        items_xml.append(
            f"<item><title>{title}</title><link>{link}</link>"
            f"<description>{pp.get('pp_name', '')}</description></item>"
        )

    for td in diff.get("niap", {}).get("tds", {}).get("added", []):
        title = f"New TD: {td.get('identifier', '')}"
        items_xml.append(
            f"<item><title>{title}</title><link>https://www.niap-ccevs.org/</link>"
            f"<description>{td.get('pp_short_name', '')}</description></item>"
        )

    for item in diff.get("niap", {}).get("news", {}).get("added", []):
        title = item.get("title", "NIAP News")
        link  = item.get("link") or item.get("url") or "https://www.niap-ccevs.org/"
        items_xml.append(
            f"<item><title>{title}</title><link>{link}</link>"
            f"<description>{item.get('date', '')}</description></item>"
        )

    for lab, lab_items in diff.get("cctl_labs", {}).items():
        for it in (lab_items or []):
            title = f"[{lab}] {it.get('title', '')}"
            link  = it.get("link", "#")
            items_xml.append(
                f"<item><title>{title}</title><link>{link}</link>"
                f"<description>{it.get('summary', '')[:200]}</description></item>"
            )

    for cp_name, cp in diff.get("csfc", {}).get("capability_packages", {}).items():
        if cp.get("changed"):
            link = cp.get("url", "https://www.nsa.gov/")
            items_xml.append(
                f"<item><title>CSfC CP Updated: {cp_name}</title>"
                f"<link>{link}</link><description>Capability package updated.</description></item>"
            )

    for doc_name, doc in diff.get("nist", {}).get("doc_headers", {}).items():
        if doc.get("changed"):
            link = doc.get("url", "https://csrc.nist.gov/")
            items_xml.append(
                f"<item><title>NIST Doc Updated: {doc_name}</title>"
                f"<link>{link}</link><description>Document header updated.</description></item>"
            )

    for doc_name, doc in diff.get("cc_crypto", {}).get("doc_headers", {}).items():
        if doc.get("changed"):
            link = doc.get("url", "https://www.commoncriteriaportal.org/")
            items_xml.append(
                f"<item><title>CC Crypto Doc Updated: {doc_name}</title>"
                f"<link>{link}</link><description>Document header updated.</description></item>"
            )

    for alert in diff.get("alerts", []):
        items_xml.append(
            f"<item><title>ALERT: {alert.get('type', '')}</title>"
            f"<link>https://kr15tyk.github.io/CC-pulse/cc_dashboard.html</link>"
            f"<description>{alert.get('message', '')}</description></item>"
        )

    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<rss version="2.0"><channel>\n'
        f'<title>CC Pulse</title>\n'
        f'<link>https://kr15tyk.github.io/CC-pulse/cc_dashboard.html</link>\n'
        f'<description>Common Criteria monitoring · {generated_at}</description>\n'
        + "\n".join(items_xml)
        + "\n</channel></rss>\n"
    )
    return xml


# ── Main render entry point ──────────────────────────────────────────────────
def render_dashboard(diff: dict, output_dir: str = "docs") -> None:
    """Render the HTML dashboard and RSS feed from a diff dict."""
    import os
    from datetime import datetime, timezone
    from jinja2 import Environment

    os.makedirs(output_dir, exist_ok=True)
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    # ── Compute totals ───────────────────────────────────────────────────────
    niap         = diff.get("niap", {})
    pps          = niap.get("pps", {})
    tds          = niap.get("tds", {})
    cisco        = niap.get("cisco_ndcpp", {})
    news         = niap.get("news", {})

    niap_pp_total   = (len(pps.get("added", [])) + len(pps.get("removed", [])) +
                       len(pps.get("sunset_changes", [])) + len(pps.get("status_changes", [])))
    niap_td_total   = len(tds.get("added", [])) + len(tds.get("removed", []))
    cisco_total     = (len(cisco.get("added", [])) + len(cisco.get("removed", [])) +
                       len(cisco.get("newly_archived", [])))
    niap_news_total = len(news.get("added", []))

    cctl_total      = sum(len(v) for v in diff.get("cctl_labs", {}).values() if v)
    csfc_total      = sum(1 for cp in diff.get("csfc", {}).get("capability_packages", {}).values()
                          if cp.get("changed"))
    cc_crypto_total = sum(1 for d in diff.get("cc_crypto", {}).get("doc_headers", {}).values()
                          if d.get("changed"))
    nist_total      = sum(1 for d in diff.get("nist", {}).get("doc_headers", {}).values()
                          if d.get("changed"))
    alert_total     = len(diff.get("alerts", []))

    niap_total_stat   = niap_pp_total + niap_td_total + cisco_total + niap_news_total
    cctl_total_stat   = cctl_total
    csfc_total_stat   = csfc_total
    nist_total_stat   = nist_total + cc_crypto_total

    # ── Sparkline data ───────────────────────────────────────────────────────
    recent_diffs = _load_recent_diffs()
    def _sp(section_key):
        counts = _section_daily_counts(recent_diffs, section_key)
        if not counts:
            return [0] * 7
        mx = max(counts) or 1
        return [round(c / mx * 100) for c in counts]

    sparklines = {
        "niap_pp":   _sp("niap_pp"),
        "niap_td":   _sp("niap_td"),
        "niap_news": _sp("niap_news"),
        "cctl":      _sp("cctl"),
        "csfc":      _sp("csfc"),
        "cc_crypto": _sp("cc_crypto"),
        "nist":      _sp("nist"),
    }

    # ── Render template ──────────────────────────────────────────────────────
    env = Environment(autoescape=False)
    tmpl = env.from_string(DASHBOARD_TEMPLATE)
    html = tmpl.render(
        diff            = diff,
        generated_at    = generated_at,
        niap_pp_total   = niap_pp_total,
        niap_td_total   = niap_td_total,
        cisco_total     = cisco_total,
        niap_news_total = niap_news_total,
        cctl_total      = cctl_total,
        csfc_total      = csfc_total,
        cc_crypto_total = cc_crypto_total,
        nist_total      = nist_total,
        alert_total     = alert_total,
        niap_total_stat = niap_total_stat,
        cctl_total_stat = cctl_total_stat,
        csfc_total_stat = csfc_total_stat,
        nist_total_stat = nist_total_stat,
        sparklines      = sparklines,
        period_start    = diff.get("period_start", ""),
        period_end      = diff.get("period_end", ""),
    )

    html_path = os.path.join(output_dir, "cc_dashboard.html")
    with open(html_path, "w", encoding="utf-8") as fh:
        fh.write(html)
    print(f"[dashboard] wrote {html_path}")

    # ── RSS feed ─────────────────────────────────────────────────────────────
    rss = _build_rss(diff, generated_at)
    rss_path = os.path.join(output_dir, "cc_pulse.rss")
    with open(rss_path, "w", encoding="utf-8") as fh:
        fh.write(rss)
    print(f"[dashboard] wrote {rss_path}")
