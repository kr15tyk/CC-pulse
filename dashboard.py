"""
dashboard.py -- Renders the daily HTML dashboard and RSS feed.

Features:
  - Keyword alert banner (red, top of page)
  - Trend summary stats panel with anchor links (NIAP + CSfC + CC Crypto + NIST)
  - Collapsible cards (empty sections auto-collapsed)
  - Color-coded section headers (green=new, amber=updated, red=removed/alert)
  - Clickable source links on all items
  - CSfC CP entries show content-length change when dates unavailabl
  - 7-day activity sparkline per section
  - Responsive mobile layou
  - CCTL Lab Intel: compact per-lab counts with expand
  - RSS feed (cc_pulse.rss) with items from all domains
  - Structured logging
"""
import glob
import json
import logging
import os
from datetime import datetime, timezone
from xml.sax.saxutils import escape as xml_escape

from jinja2 import Environment

import config

log = logging.getLogger(__name__)


def _load_recent_diffs(n: int = 7) -> list:
    """Load the most recent N daily diff files for sparkline data."""
    pattern = os.path.join("snapshots", "diffs", "*_diff.json")
    paths = sorted(glob.glob(pattern))[-n:]
    diffs = []
    for p in paths:
        try:
            with open(p, encoding="utf-8") as fh:
                diffs.append(json.load(fh))
        except Exception:
            pass
    return diffs


def _section_daily_counts(diffs: list, section_key: str) -> list:
    """Return per-section change counts for the last N days (for sparkline)."""
    counts = []
    for d in diffs:
        n = 0
        if section_key == "niap_pp":
            pps = d.get("niap", {}).get("pps", {})
            n = (len(pps.get("added", [])) + len(pps.get("removed", [])) +
                 len(pps.get("sunset_changes", [])) + len(pps.get("status_changes", [])))
        elif section_key == "niap_td":
            tds = d.get("niap", {}).get("tds", {})
            n = len(tds.get("added", [])) + len(tds.get("removed", []))
        elif section_key == "niap_news":
            n = len(d.get("niap", {}).get("news", {}).get("added", []))
        elif section_key == "cctl":
            n = sum(len(v) for v in d.get("cctl_labs", {}).values() if v)
        elif section_key == "csfc":
            n = sum(1 for cp in d.get("csfc", {}).get("component_selections", {}).values()
                    if cp.get("changed"))
        elif section_key == "cc_crypto":
            n = sum(1 for doc in d.get("cc_crypto", {}).get("doc_headers", {}).values()
                    if doc.get("changed"))
        elif section_key == "nist":
            n = sum(1 for doc in d.get("nist", {}).get("doc_headers", {}).values()
                    if doc.get("changed"))
        elif section_key == "pcl_all":
            pa = d.get("niap", {}).get("pcl_all", {})
            n = (len(pa.get("added", [])) + len(pa.get("removed", [])) +
                 len(pa.get("newly_archived", [])))
        elif section_key == "in_eval":
            ie = d.get("niap", {}).get("in_evaluation", {})
            n = len(ie.get("added", [])) + len(ie.get("removed", []))
        counts.append(n)
    return counts


# -- Dashboard HTML template --------------------------------------------------
DASHBOARD_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta http-equiv="refresh" content="3600">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>CC Pulse Dashboard</title>
<style>
  @import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap');
  :root {
    --bg:#f4f6f9; --nav-bg:#0d1f3e; --card:#ffffff; --card-border:#dde3ed; --panel:#edf1f7;
    --border:#dde3ed; --text:#1a2d4e; --text-light:#4a5f7a; --muted:#6b82a0;
    --primary:#049fd9; --primary-dark:#0376a8; --accent:#049fd9;
    --green:#00875a; --amber:#d97706; --red:#c0392b; --alert-color:#d97706;
    --font:'Inter',-apple-system,BlinkMacSystemFont,sans-serif;
    --gradient-hero:linear-gradient(135deg,#0d1f3e 0%,#0d2b5e 50%,#1a3566 100%);
    --shadow-sm:0 1px 3px rgba(0,0,0,0.08),0 1px 2px rgba(0,0,0,0.05);
    --shadow-md:0 4px 12px rgba(0,0,0,0.08),0 2px 4px rgba(0,0,0,0.05);
  }
  *{box-sizing:border-box;margin:0;padding:0}
  body{background:var(--bg);color:var(--text);font-family:var(--font);font-size:14px;line-height:1.6}
  a{color:var(--primary);text-decoration:none} a:hover{color:var(--primary-dark);text-decoration:underline}
  .site-nav{background:var(--nav-bg);padding:0 1.5rem;display:flex;align-items:center;gap:1rem;height:44px;position:sticky;top:0;z-index:200;box-shadow:0 1px 4px rgba(0,0,0,0.2)}
  .nav-logo{display:flex;align-items:center;gap:.75rem;text-decoration:none}
  .nav-logo img{height:32px;width:auto}
  .nav-spacer{flex:1} .nav-meta{font-size:.75rem;color:rgba(255,255,255,0.55)}
  .hero-banner{background:var(--gradient-hero);padding:2rem 2rem 1.5rem;text-align:center;position:relative;overflow:hidden}
  .hero-banner::before{content:'';position:absolute;inset:0;background:radial-gradient(ellipse at 60% 40%,rgba(4,159,217,.12) 0%,transparent 65%),radial-gradient(ellipse at 25% 75%,rgba(94,79,195,.10) 0%,transparent 60%);pointer-events:none}
  .hero-inner{position:relative;z-index:1;max-width:900px;margin:0 auto}
  .hero-wordmark{display:block;font-size:3.6rem;font-weight:800;color:#fff;letter-spacing:-.02em;line-height:1;text-align:center;font-family:var(--font)}
  .hero-wordmark-accent{background:linear-gradient(135deg,#00c6ff,#049fd9);-webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text}
  .hero-sub-label{display:block;width:100%;text-align:center;font-size:1.15rem;font-weight:800;letter-spacing:.42em;text-transform:uppercase;background:linear-gradient(90deg,#1a55e0,#00d4ff);-webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text;margin:.24rem 0 0 0;line-height:1;padding-left:.42em}
  .hero-sub{font-size:1.05rem;color:rgba(255,255,255,.65)}
  .sticky-top{position:sticky;top:44px;z-index:100;background:var(--card);border-bottom:1px solid var(--border);padding:.75rem 2rem;box-shadow:var(--shadow-sm)}
  .trend-bar{display:flex;flex-wrap:wrap;gap:.75rem}
  .stat{background:var(--card);border:1px solid var(--card-border);border-radius:8px;padding:.75rem 1.25rem;min-width:120px;flex:1;transition:border-color .2s,box-shadow .2s;box-shadow:var(--shadow-sm)}
  .stat:hover{border-color:var(--primary);box-shadow:0 4px 12px rgba(4,159,217,.12)}
  .stat a{color:inherit;text-decoration:none;display:block}
  .stat-num{font-size:1.8rem;font-weight:700;color:#b0bec5;line-height:1;margin-bottom:.2rem}
  .stat-num.active-num{color:var(--primary)} .stat-num.alert-num{color:var(--alert-color)}
  .stat.has-data{border-color:rgba(4,159,217,.3)} .stat.has-alert{border-color:rgba(217,119,6,.3)}
  .stat-lbl,.stat-label{font-size:.68rem;color:var(--muted);text-transform:uppercase;letter-spacing:.1em;font-weight:600}
  .main-content{max-width:1200px;margin:0 auto;padding:2rem}
  .alert-banner{background:rgba(217,119,6,.06);border:1px solid rgba(217,119,6,.3);border-radius:8px;padding:.75rem 1.1rem;margin-bottom:1.5rem;font-weight:600;color:var(--alert-color);font-size:.875rem}
  .alert-banner::before{content:"⚠ "}
  .ctrl-bar{font-size:.75rem;color:var(--muted);margin-bottom:1rem;display:flex;gap:.6rem;align-items:center;flex-wrap:wrap}
  .ctrl-btn{background:var(--card);border:1px solid var(--border);color:var(--text-light);font-family:var(--font);font-size:.72rem;padding:4px 12px;cursor:pointer;border-radius:6px;transition:all .15s;font-weight:500}
  .ctrl-btn:hover{border-color:var(--primary);color:var(--primary);background:rgba(4,159,217,.05)}
  .ctrl-hint{font-size:.65rem;color:var(--muted);opacity:.6}
  .search-row{display:flex;gap:.6rem;align-items:center;flex-wrap:wrap;margin-top:.5rem;margin-bottom:1.5rem}
  .search-input{flex:1;min-width:180px;max-width:340px;background:var(--card);border:1px solid var(--border);color:var(--text);font-family:var(--font);font-size:.8rem;padding:6px 12px;outline:none;border-radius:6px;transition:border-color .2s,box-shadow .2s}
  .search-input:focus{border-color:var(--primary);box-shadow:0 0 0 3px rgba(4,159,217,.12)}
  .search-input::placeholder{color:var(--muted)}
  .search-clear{background:none;border:none;color:var(--muted);font-family:var(--font);font-size:.8rem;cursor:pointer;padding:0 4px} .search-clear:hover{color:var(--primary)}
  .filter-chips{display:flex;gap:.35rem;flex-wrap:wrap;align-items:center}
  .chip{background:none;border:1px solid var(--border);color:var(--muted);font-family:var(--font);font-size:.65rem;padding:3px 10px;cursor:pointer;white-space:nowrap;border-radius:100px;transition:all .15s;font-weight:600}
  .chip:hover{border-color:var(--primary);color:var(--primary)} .chip.active{border-color:var(--primary);color:var(--primary);background:rgba(4,159,217,.08)}
  .chip.chip-alert.active{border-color:var(--alert-color);color:var(--alert-color);background:rgba(217,119,6,.08)}
  .search-count{font-size:.65rem;color:var(--muted);white-space:nowrap} mark.sh{background:rgba(217,119,6,.2);color:var(--text);border-radius:2px;padding:0 2px}
  .search-hidden{display:none!important} .no-results-msg{color:var(--muted);font-size:.8rem;padding:.5rem 0;display:none;font-style:italic}
  .section-group{margin-bottom:2rem}
  .section-label{font-size:.65rem;font-weight:700;text-transform:uppercase;letter-spacing:.15em;color:var(--primary);padding:0 0 .6rem 0;border-bottom:2px solid var(--border);margin-bottom:.75rem;display:flex;align-items:center;gap:.5rem}
  .section-label::before{content:'';display:inline-block;width:3px;height:13px;background:var(--primary);border-radius:2px}
  .section-group .card{margin-bottom:.6rem}
  .card{background:var(--card);border:1px solid var(--border);border-radius:10px;overflow:hidden;box-shadow:var(--shadow-sm);transition:box-shadow .2s,border-color .2s}
  .card:hover{box-shadow:var(--shadow-md)}
  .card-new{border-left:3px solid var(--green)} .card-updated{border-left:3px solid var(--primary)} .card-alert{border-left:3px solid var(--alert-color)}
  .card-hdr{display:flex;align-items:center;gap:.75rem;padding:.85rem 1.25rem;cursor:pointer;user-select:none;transition:background .15s}
  .card-hdr:hover{background:var(--panel)} .card-hdr>span:first-child{font-weight:600;flex:1;color:var(--text);font-size:.9rem}
  .card-count{font-size:.68rem;color:var(--muted);background:var(--panel);padding:2px 9px;border-radius:100px;border:1px solid var(--border);font-weight:600}
  .toggle-icon{font-size:.6rem;color:var(--muted);margin-left:4px}
  .card-body{padding:1rem 1.25rem;border-top:1px solid var(--border)} .card-body.collapsed{display:none}
  .sub-hdr{font-size:.65rem;font-weight:700;text-transform:uppercase;letter-spacing:.1em;padding:.5rem 0 .3rem;margin-top:.25rem}
  .sub-new{color:var(--green)} .sub-removed{color:var(--red)} .sub-updated{color:var(--primary)}
  .item-row{display:flex;justify-content:space-between;align-items:baseline;gap:.5rem;padding:5px 0;border-bottom:1px solid var(--border)} .item-row:last-child{border-bottom:none}
  .item-link{color:var(--primary);flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:.85rem}
  .item-link::before{content:"→ ";color:var(--muted);font-size:.75rem}
  .item-meta{font-size:.7rem;color:var(--muted);white-space:nowrap;flex-shrink:0}
  .item-sub{font-size:.72rem;color:var(--muted);padding:1px 0 4px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;padding-left:1.4em}
  .no-change{color:var(--muted);font-size:.8rem;padding:.5rem 0;font-style:italic}
  .alert-item .item-link{color:var(--alert-color)} .alert-item .item-link::before{content:"⚡ ";color:var(--alert-color)} .alert-item .item-meta{color:var(--alert-color);opacity:.85}
  .alert-dismissed{opacity:.3} .alert-dismissed .item-link{text-decoration:line-through}
  .dismiss-btn{background:none;border:1px solid var(--border);color:var(--muted);font-family:var(--font);font-size:.62rem;padding:2px 7px;cursor:pointer;margin-left:auto;white-space:nowrap;border-radius:4px;transition:all .15s}
  .dismiss-btn:hover{border-color:var(--primary);color:var(--primary)} .dismiss-btn.seen{border-color:var(--primary);color:var(--primary)}
  .alert-monitoring{font-size:.65rem;color:var(--muted);margin-bottom:.75rem;padding-bottom:.5rem;border-bottom:1px solid var(--border);word-break:break-word}
  .alert-monitoring::before{content:"Monitoring: ";font-weight:700;color:var(--text-light)}
  .lab-row{margin-bottom:.5rem} .lab-hdr{display:flex;align-items:center;gap:.5rem;padding:5px 0;cursor:pointer;transition:color .15s} .lab-hdr:hover{color:var(--primary)}
  .lab-name{font-weight:600;flex:1;font-size:.85rem} .lab-cnt{font-size:.7rem;color:var(--muted)} .lab-body{padding-left:.75rem} .lab-body.collapsed{display:none}
  .cp-row{padding:6px 0;border-bottom:1px solid var(--border)} .cp-row:last-child{border-bottom:none} .cp-name{color:var(--primary);font-weight:600}
  .cp-meta{font-size:.7rem;color:var(--muted);margin-top:2px} .cp-detail{font-size:.72rem;color:var(--muted)} .cp-date{font-size:.7rem}
  .sparkline{display:inline-flex;align-items:flex-end;gap:2px;height:16px;margin-right:6px;vertical-align:middle}
  .sparkline span,.sp-bar{display:inline-block;width:4px;background:var(--border);border-radius:2px;min-height:2px}
  .last-active{font-size:.65rem;color:var(--muted);opacity:.6;margin-left:.4rem} .card.zero-hidden{display:none}
  footer,.site-footer{margin-top:3rem;padding:1.5rem 2rem;border-top:1px solid var(--border);font-size:.75rem;color:var(--muted);display:flex;justify-content:space-between;align-items:center;gap:1rem;flex-wrap:wrap;background:var(--card)}
  @media(max-width:768px){.hero-banner{padding:2rem 1rem}.hero-title{font-size:1.6rem}.hero-wordmark{font-size:2.4rem}.main-content{padding:1rem}.sticky-top{padding:.5rem 1rem;top:0}.site-nav{padding:0 1rem}.trend-bar{flex-direction:column}.stat{min-width:unset}}
/* ── Regional tab navigation ── */
        .tab-nav{display:flex;gap:.5rem;flex-wrap:wrap;margin-bottom:1.5rem;border-bottom:2px solid var(--border);padding-bottom:.5rem}
        .tab-btn{background:none;border:1px solid var(--border);color:var(--text-light);font-family:var(--font);font-size:.8rem;padding:6px 16px;cursor:pointer;border-radius:8px 8px 0 0;transition:all .2s;font-weight:500;margin-bottom:-2px}
        .tab-btn:hover{border-color:var(--primary);color:var(--primary);background:rgba(4,159,217,.05)}
        .tab-btn.active{border-color:var(--primary);color:var(--primary);background:rgba(4,159,217,.08);border-bottom:2px solid var(--bg)}
        .section-group[data-tab]{display:none}
        .section-group[data-tab].tab-active{display:block}
        /* US sections don't have data-tab — they are always shown when tab-us is active */
        .tab-us-hidden{display:none}
</style>
</head>
<body>

<nav class="site-nav">
  <a href="#" class="nav-logo">
    <img src="cc_pulse_logo.png" alt="CC-Pulse Logo">
  </a>
  <span class="nav-spacer"></span>
  <span class="nav-meta">Generated {{ generated_at }}</span>
</nav>

<div class="hero-banner">
  <div class="hero-inner">
    <span class="hero-wordmark">CC<span class="hero-wordmark-accent"> Pulse</span></span>
    <span class="hero-sub-label">Dashboard</span>
    
  </div>
</div>

<div class="sticky-top">
  <div class="trend-bar">
    <div class="stat"><a href="#sec-niap-pp">
      <div class="stat-num {% if niap_total_stat > 0 %}active-num{% endif %}">{{ niap_total_stat }}</div>
      <div class="stat-lbl">NIAP Changes</div>
    </a></div>
    <div class="stat"><a href="#sec-cctl">
      <div class="stat-num {% if cctl_total_stat > 0 %}active-num{% endif %}">{{ cctl_total_stat }}</div>
      <div class="stat-lbl">CCTL Items</div>
    </a></div>
    <div class="stat"><a href="#sec-csfc">
      <div class="stat-num {% if csfc_total_stat > 0 %}active-num{% endif %}">{{ csfc_total_stat }}</div>
      <div class="stat-lbl">CSfC Selection Updates</div>
    </a></div>
    <div class="stat"><a href="#sec-nist">
      <div class="stat-num {% if nist_total_stat > 0 %}active-num{% endif %}">{{ nist_total_stat }}</div>
      <div class="stat-lbl">NIST Doc Updates</div>
    </a></div>
    <div class="stat"><a href="#sec-cc-portal">
      <div class="stat-num {% if cc_portal_total_stat > 0 %}active-num{% endif %}">{{ cc_portal_total_stat }}</div>
      <div class="stat-lbl">CC Portal</div>
    </a></div>
    <div class="stat"><a href="#sec-alerts">
      <div class="stat-num {% if alert_total > 0 %}alert-num{% endif %}">{{ alert_total }}</div>
      <div class="stat-lbl">Alerts</div>
    </a></div>
  </div>
</div><!-- /sticky-top -->
<div class="main-content">
<!-- Source health summary -->
<div class="ctrl-bar">
  <button class="ctrl-btn" onclick="expandAll()" title="Expand all cards [E]">[E] Expand All</button>
  <button class="ctrl-btn" onclick="collapseAll()" title="Collapse all cards [C]">[C] Collapse All</button>
  <button class="ctrl-btn" id="filter-btn" onclick="toggleZeroFilter()" title="Toggle zero-change cards [F]">[F] Hide Empty</button>
  <span class="ctrl-hint">keys: E=expand  C=collapse  F=filter  /=search  1/2/3=tabs</span>
</div>
<div class="search-row">
  <input id="search-input" class="search-input" type="search" placeholder="Search items…"
    oninput="liveSearch()" title="Search across all cards [/]" autocomplete="off" spellcheck="false">
  <button class="search-clear" onclick="clearSearch()" title="Clear search">✕</button>
  <div class="filter-chips">
    <span style="font-size:.65rem;color:var(--muted);opacity:.6;">kind:</span>
    <button class="chip active" data-kind="all"     onclick="setKindFilter(this)">All</button>
    <button class="chip"        data-kind="new"     onclick="setKindFilter(this)">New</button>
    <button class="chip"        data-kind="removed" onclick="setKindFilter(this)">Removed</button>
    <button class="chip"        data-kind="updated" onclick="setKindFilter(this)">Updated</button>
    <button class="chip"        data-kind="archived" onclick="setKindFilter(this)">Archived</button>
    <button class="chip chip-alert" data-kind="alert" onclick="setKindFilter(this)">Alert</button>
  </div>
  <span id="search-count" class="search-count"></span>
</div>
<!-- Regional tab navigation -->
      <div class="tab-nav" id="region-tabs">
        <button class="tab-btn active" data-tab="us" onclick="switchTab(this)">&#127482;&#127480; US (NIAP / CSfC / NIST)</button>
        <button class="tab-btn" data-tab="nato" onclick="switchTab(this)">&#127758; NATO NIAPCL</button>
        <button class="tab-btn" data-tab="eu" onclick="switchTab(this)">&#127466;&#127482; EU (EUCC / ENISA)</button>
      </div>
      
<!-- Alerts -->
{% if diff.alerts %}
<div class="section-group">
<div class="card card-alert" id="sec-alerts">
  <div class="card-hdr" onclick="toggleCard(this)">
    <span>Alerts</span>
    <span class="card-count">{{ alert_total }} alert{% if alert_total != 1 %}s</div>
{% endif %}</span>
    <span class="toggle-icon">&#9660;</span>
  </div>
  <div class="card-body">
    <div class="alert-monitoring">{{ watch_keywords | join(', ') }}</div>
    {% for alert in diff.alerts %}
        <div class="item-row alert-item" data-alert-key="{{ alert.source }}-{{ alert.title | replace(' ', '_') }}">
          {% if alert.url %}
          <a class="item-link" href="{{ alert.url }}" target="_blank">{{ alert.source }}: {{ alert.title }}</a>
          {% else %}
          <span class="item-link">{{ alert.source }}: {{ alert.title }}</span>
          {% endif %}
          <span class="item-meta">{{ alert.kind }} &middot; {{ alert.matched_keywords | join(', ') }}</span>
          {% if alert.detail %}<div class="item-sub">{{ alert.detail }}</div>{% endif %}
          <button class="dismiss-btn" onclick="dismissAlert(this)" title="Mark as seen">&#10003; Seen</button>
        </div>
        {% endfor %}
  </div>
</div>
{% endif %}

<div class="grid">

<div class="section-group">
  <div class="section-label">NIAP</div>
  <!-- Cisco NDcPP -->
<div class="card {% if cisco_total > 0 %}card-new{% endif %}" id="sec-cisco" data-has-changes="{{ cisco_total }}">
  <div class="card-hdr" onclick="toggleCard(this)">
    <span>Cisco NDcPP Certifications</span>
    <span class="card-count">{{ cisco_total }} change{% if cisco_total != 1 %}s{% endif %}</span>
    <span class="toggle-icon">{% if cisco_total == 0 %}&#9658;{% else %}&#9660;{% endif %}</span>
  </div>
  <div class="card-body {% if cisco_total == 0 %}collapsed{% endif %}">
    {% if diff.niap.cisco_ndcpp.added %}
    <div class="sub-hdr sub-new">New Certifications ({{ diff.niap.cisco_ndcpp.added | length }})</div>
    {% for item in diff.niap.cisco_ndcpp.added %}
    <div class="item-row" data-source="cisco" data-kind="new">
      <span class="item-link">{{ item.vendor_id_name }} &mdash; {{ item.product_name }}</span>
      <span class="item-meta">{% if item.certification_date %}{{ item.certification_date[:10] }}{% endif %}</span>
    </div>
    {% if item.protection_profiles %}
    <div class="item-sub">{{ item.protection_profiles | map(attribute='pp_short_name') | join(', ') }}</div>
    {% endif %}
    {% endfor %}
    {% endif %}
    {% if diff.niap.cisco_ndcpp.removed %}
    <div class="sub-hdr sub-removed">Removed ({{ diff.niap.cisco_ndcpp.removed | length }})</div>
    {% for item in diff.niap.cisco_ndcpp.removed %}
    <div class="item-row" data-source="cisco" data-kind="removed">
      <span class="item-link">{{ item.vendor_id_name }} &mdash; {{ item.product_name }}</span>
      <span class="item-meta">{{ item.status_sort }}</span>
    </div>
    {% endfor %}
    {% endif %}
    {% if diff.niap.cisco_ndcpp.newly_archived %}
    <div class="sub-hdr sub-updated">Newly Archived ({{ diff.niap.cisco_ndcpp.newly_archived | length }})</div>
    {% for item in diff.niap.cisco_ndcpp.newly_archived %}
    <div class="item-row" data-source="cisco" data-kind="archived">
      <span class="item-link">{{ item.vendor_id_name }} &mdash; {{ item.product_name }}</span>
      <span class="item-meta">Archived</span>
    </div>
    {% if item.protection_profiles %}
    <div class="item-sub">{{ item.protection_profiles | map(attribute='pp_short_name') | join(', ') }}</div>
    {% endif %}
    {% endfor %}
    {% endif %}
    {% if cisco_total == 0 %}<p class="no-change">No changes detected.{% if last_active.pcl_all %} <span class="last-active">(last: {{ last_active.pcl_all }})</span>{% endif %}</p>{% endif %}
  </div>
</div>

  <!-- NIAP CCTL Registry -->
<div class="card {% if cctl_registry_total > 0 %}card-updated{% endif %}" id="sec-cctl-registry" data-has-changes="{{ cctl_registry_total }}">
  <div class="card-hdr" onclick="toggleCard(this)">
    <span>NIAP CCTL Registry</span>
    <span class="card-count">{{ cctl_registry_total }} change{% if cctl_registry_total != 1 %}s{% endif %}</span>
    <span class="toggle-icon">{% if cctl_registry_total == 0 %}&#9658;{% else %}&#9660;{% endif %}</span>
  </div>
  <div class="card-body {% if cctl_registry_total == 0 %}collapsed{% endif %}">
    {% if diff.niap.cctls.added %}
    <div class="sub-hdr sub-new">New Labs ({{ diff.niap.cctls.added | length }})</div>
    {% for lab in diff.niap.cctls.added %}
    <div class="item-row" data-source="cctl-registry" data-kind="new">
      <a class="item-link" href="{{ lab.cctl_url or '#' }}" target="_blank">{{ lab.cctl_name }}</a>
      <span class="item-meta">{{ lab.city }}{% if lab.state_id %}, {{ lab.state_id }}{% endif %}</span>
    </div>
    {% endfor %}
    {% endif %}
    {% if diff.niap.cctls.removed %}
    <div class="sub-hdr sub-removed">Removed Labs ({{ diff.niap.cctls.removed | length }})</div>
    {% for lab in diff.niap.cctls.removed %}
    <div class="item-row" data-source="cctl-registry" data-kind="removed">
      <a class="item-link" href="{{ lab.cctl_url or '#' }}" target="_blank">{{ lab.cctl_name }}</a>
      <span class="item-meta">{{ lab.city }}{% if lab.state_id %}, {{ lab.state_id }}{% endif %}</span>
    </div>
    {% endfor %}
    {% endif %}
    {% if diff.niap.cctls.status_changes %}
    <div class="sub-hdr sub-updated">Status Changes ({{ diff.niap.cctls.status_changes | length }})</div>
    {% for lab in diff.niap.cctls.status_changes %}
    <div class="item-row" data-source="cctl-registry" data-kind="updated">
      <a class="item-link" href="{{ lab.cctl_url or '#' }}" target="_blank">{{ lab.cctl_name }}</a>
      <span class="item-meta">{{ lab.old_status }} &#8594; {{ lab.new_status }}</span>
    </div>
    {% endfor %}
    {% endif %}
    {% if cctl_registry_total == 0 %}<p class="no-change">No registry changes.</p>{% endif %}
  </div>
</div>

  <!-- NIAP In-Evaluation -->
<div class="card {% if in_eval_total > 0 %}card-new{% endif %}" id="sec-niap-inevaluation" data-has-changes="{{ in_eval_total }}">
  <div class="card-hdr" onclick="toggleCard(this)">
    <span>NIAP In-Evaluation Products</span>
    <span class="card-count">{% if in_eval_total > 0 %}{{ in_eval_total }} change{% if in_eval_total != 1 %}s{% endif %}{% else %}{{ in_eval_current }} active{% endif %}</span>
    <span class="sparkline">
      {% for v in sparklines.in_eval %}
      <span class="sp-bar" style="height:{{ v }}%" title="{{ sp_counts.in_eval[loop.index0] }} change{% if sp_counts.in_eval[loop.index0] != 1 %}s{% endif %}"></span>
      {% endfor %}
    </span>
  </div>
  <div class="card-body {% if in_eval_total == 0 %}collapsed{% endif %}">
    {% if diff.niap.in_evaluation.added %}
    <div class="sub-hdr sub-new">Newly In Evaluation ({{ diff.niap.in_evaluation.added | length }})</div>
    {% for item in diff.niap.in_evaluation.added %}
    <div class="item-row" data-source="niap-inevaluation" data-kind="new">
      <span class="item-link">{{ item.vendor_id_name or "" }} -- {{ item.product_name or "" }}</span>
      <span class="item-meta">{% if item.assigned_lab_name %}{{ item.assigned_lab_name }}{% endif %}</span>
    </div>
    {% if item.tech_types %}
    <div class="item-sub">{{ item.tech_types | join(", ") }}</div>
    {% endif %}
    {% endfor %}
    {% endif %}
    {% if diff.niap.in_evaluation.removed %}
    <div class="sub-hdr sub-removed">Left Evaluation ({{ diff.niap.in_evaluation.removed | length }})</div>
    {% for item in diff.niap.in_evaluation.removed %}
    <div class="item-row" data-source="niap-inevaluation" data-kind="removed">
      <span class="item-link">{{ item.vendor_id_name or "" }} -- {{ item.product_name or "" }}</span>
      <span class="item-meta">Completed or Withdrawn</span>
    </div>
    {% endfor %}
    {% endif %}
    {% if in_eval_total == 0 %}
    <p class="no-change">No evaluation changes detected. {{ in_eval_current }} product{% if in_eval_current != 1 %}s{% endif %} currently in evaluation.</p>
    {% endif %}
  </div>
</div>

  <!-- NIAP News -->
<div class="card {% if niap_news_total > 0 or diff.niap.events.added %}card-new{% endif %}" id="sec-niap-news" data-has-changes="{{ niap_news_total }}">
  <div class="card-hdr" onclick="toggleCard(this)">
    <span>NIAP News &amp; Announcements</span>
    <span class="card-count">{{ niap_news_total }} new item{% if niap_news_total != 1 %}s{% endif %}</span>
    <span class="sparkline">
      {% for v in sparklines.niap_news %}
      <span class="sp-bar" style="height:{{ v }}%" title="{{ sp_counts.niap_news[loop.index0] }} change{% if sp_counts.niap_news[loop.index0] != 1 %}s{% endif %}"></span>
      {% endfor %}
    </span>
    <span class="toggle-icon">{% if niap_news_total == 0 and not diff.niap.events.added %}&#9658;{% else %}&#9660;{% endif %}</span>
  </div>
  <div class="card-body {% if niap_news_total == 0 and not diff.niap.events.added %}collapsed{% endif %}">
    {% if diff.niap.news.added %}
    {% for item in diff.niap.news.added %}
    <div class="item-row" data-source="niap-news" data-kind="new">
      <a class="item-link" href="{{ item.link or item.url or '#' }}" target="_blank">{{ item.title }}</a>
      <span class="item-meta">{{ item.date }}</span>
    </div>
    {% endfor %}
    {% endif %}
    {% if diff.niap.events.added %}
    <div class="sub-hdr sub-new">Events ({{ diff.niap.events.added | length }})</div>
    {% for ev in diff.niap.events.added %}
    <div class="item-row" data-source="niap-news" data-kind="new">
      <span class="item-link">{{ ev.title or ev.name or ev }}</span>
      <span class="item-meta">{{ ev.date or ev.start_date or '' }}</span>
    </div>
    {% endfor %}
    {% endif %}
    {% set policy_items = diff.niap.news.added | selectattr("_category", "equalto", "POLICY") | list %}
    {% if policy_items %}
    <div class="sub-hdr sub-updated">Policy Letters &amp; Updates ({{ policy_items | length }})</div>
    {% for item in policy_items %}
    <div class="item-row" data-source="niap-news" data-kind="new">
      {% if item.link %}<a class="item-link" href="{{ item.link }}" target="_blank">{{ item.title }}</a>
      {% else %}<span class="item-link">{{ item.title }}</span>{% endif %}
      <span class="item-meta">{% if item.posted %}{{ item.posted[:10] }}{% elif item.date %}{{ item.date[:10] }}{% endif %}</span>
    </div>
    {% endfor %}
    {% endif %}
    {% if niap_news_total == 0 and not diff.niap.events.added %}<p class="no-change">No new items.</p>{% endif %}
  </div>
</div>

  <!-- NIAP PCL -- All Certifications -->
<div class="card {% if pcl_all_total > 0 %}card-new{% endif %}" id="sec-niap-pcl" data-has-changes="{{ pcl_all_total }}">
  <div class="card-hdr" onclick="toggleCard(this)">
    <span>NIAP PCL -- All Certifications</span>
    <span class="card-count">{{ pcl_all_total }} change{% if pcl_all_total != 1 %}s{% endif %}</span>
    <span class="sparkline">
      {% for v in sparklines.pcl_all %}
      <span class="sp-bar" style="height:{{ v }}%" title="{{ sp_counts.pcl_all[loop.index0] }} change{% if sp_counts.pcl_all[loop.index0] != 1 %}s{% endif %}"></span>
      {% endfor %}
    </span>
  </div>
  <div class="card-body {% if pcl_all_total == 0 %}collapsed{% endif %}">
    {% if diff.niap.pcl_all.added %}
    <div class="sub-hdr sub-new">New Certifications ({{ diff.niap.pcl_all.added | length }})</div>
    {% for item in diff.niap.pcl_all.added %}
    <div class="item-row" data-source="niap-pcl" data-kind="new">
      <span class="item-link">{{ item.vendor_id_name }} -- {{ item.product_name }}</span>
      <span class="item-meta">{% if item.certification_date %}{{ item.certification_date[:10] }}{% endif %}</span>
    </div>
    {% if item.protection_profiles %}
    <div class="item-sub">{{ item.protection_profiles | map(attribute="pp_short_name") | join(", ") }}</div>
    {% endif %}
    {% endfor %}
    {% endif %}
    {% if diff.niap.pcl_all.newly_archived %}
    <div class="sub-hdr sub-updated">Newly Archived ({{ diff.niap.pcl_all.newly_archived | length }})</div>
    {% for item in diff.niap.pcl_all.newly_archived %}
    <div class="item-row" data-source="niap-pcl" data-kind="archived">
      <span class="item-link">{{ item.vendor_id_name }} -- {{ item.product_name }}</span>
      <span class="item-meta">Archived</span>
    </div>
    {% if item.protection_profiles %}
    <div class="item-sub">{{ item.protection_profiles | map(attribute="pp_short_name") | join(", ") }}</div>
    {% endif %}
    {% endfor %}
    {% endif %}
    {% if diff.niap.pcl_all.removed %}
    <div class="sub-hdr sub-removed">Removed ({{ diff.niap.pcl_all.removed | length }})</div>
    {% for item in diff.niap.pcl_all.removed %}
    <div class="item-row" data-source="niap-pcl" data-kind="removed">
      <span class="item-link">{{ item.vendor_id_name }} -- {{ item.product_name }}</span>
      <span class="item-meta">{{ item.status_sort }}</span>
    </div>
    {% endfor %}
    {% endif %}
    {% if pcl_all_total == 0 %}<p class="no-change">No changes detected.{% if last_active.pcl_all %} <span class="last-active">(last: {{ last_active.pcl_all }})</span>{% endif %}</p>{% endif %}
  </div>
</div>

  <!-- NIAP PPs -->
<div class="card {% if niap_pp_total > 0 %}card-new{% endif %}" id="sec-niap-pp" data-has-changes="{{ niap_pp_total }}">
  <div class="card-hdr" onclick="toggleCard(this)">
    <span>NIAP Protection Profiles</span>
    <span class="card-count">{{ niap_pp_total }} change{% if niap_pp_total != 1 %}s{% endif %}</span>
    <span class="sparkline">
      {% for v in sparklines.niap_pp %}
      <span class="sp-bar" style="height:{{ v }}%" title="{{ sp_counts.niap_pp[loop.index0] }} change{% if sp_counts.niap_pp[loop.index0] != 1 %}s{% endif %}"></span>
      {% endfor %}
    </span>
    <span class="toggle-icon">{% if niap_pp_total == 0 %}&#9658;{% else %}&#9660;{% endif %}</span>
  </div>
  <div class="card-body {% if niap_pp_total == 0 %}collapsed{% endif %}">
    {% if diff.niap.pps.added %}
    <div class="sub-hdr sub-new">New PPs ({{ diff.niap.pps.added | length }})</div>
    {% for pp in diff.niap.pps.added %}
    <div class="item-row" data-source="niap-pp" data-kind="new">
      <a class="item-link" href="https://www.niap-ccevs.org/Profile/PP.cfm?id={{ pp.pp_id }}" target="_blank">{{ pp.pp_short_name }}</a>
      <span class="item-meta">{{ pp.tech_type }} &middot; {{ pp.pp_date }}</span>
    </div>
    {% endfor %}
    {% endif %}
    {% if diff.niap.pps.removed %}
    <div class="sub-hdr sub-removed">Removed PPs ({{ diff.niap.pps.removed | length }})</div>
    {% for pp in diff.niap.pps.removed %}
    <div class="item-row" data-source="niap-pp" data-kind="removed">
      <a class="item-link" href="https://www.niap-ccevs.org/Profile/PP.cfm?id={{ pp.pp_id }}" target="_blank">{{ pp.pp_short_name }}</a>
      <span class="item-meta">{{ pp.tech_type }} &middot; {{ pp.pp_date }}</span>
    </div>
    {% endfor %}
    {% endif %}
    {% if diff.niap.pps.sunset_changes %}
    <div class="sub-hdr sub-updated">Sunset Changes ({{ diff.niap.pps.sunset_changes | length }})</div>
    {% for pp in diff.niap.pps.sunset_changes %}
    <div class="item-row" data-source="niap-pp" data-kind="updated">
      <a class="item-link" href="https://www.niap-ccevs.org/Profile/PP.cfm?id={{ pp.pp_id }}" target="_blank">{{ pp.pp_short_name }}</a>
      <span class="item-meta">{{ pp.tech_type }} &middot; Sunset: {{ pp.sunset_date }}</span>
    </div>
    {% endfor %}
    {% endif %}
    {% if diff.niap.pps.status_changes %}
    <div class="sub-hdr sub-updated">Status Changes ({{ diff.niap.pps.status_changes | length }})</div>
    {% for pp in diff.niap.pps.status_changes %}
    <div class="item-row" data-source="niap-pp" data-kind="updated">
      <a class="item-link" href="https://www.niap-ccevs.org/Profile/PP.cfm?id={{ pp.pp_id }}" target="_blank">{{ pp.pp_short_name }}</a>
      <span class="item-meta">{{ pp.tech_type }} &middot; {{ pp.status }}</span>
    </div>
    {% endfor %}
    {% endif %}
    {% if niap_pp_total == 0 %}<p class="no-change">No changes detected.{% if last_active.niap_pp %} <span class="last-active">(last: {{ last_active.niap_pp }})</span>{% endif %}</p>{% endif %}
  </div>
</div>

  <!-- NIAP TDs -->
<div class="card {% if niap_td_total > 0 %}card-new{% endif %}" id="sec-niap-td" data-has-changes="{{ niap_td_total }}">
  <div class="card-hdr" onclick="toggleCard(this)">
    <span>NIAP Technical Decisions</span>
    <span class="card-count">{{ niap_td_total }} change{% if niap_td_total != 1 %}s{% endif %}</span>
    <span class="sparkline">
      {% for v in sparklines.niap_td %}
      <span class="sp-bar" style="height:{{ v }}%" title="{{ sp_counts.niap_td[loop.index0] }} change{% if sp_counts.niap_td[loop.index0] != 1 %}s{% endif %}"></span>
      {% endfor %}
    </span>
    <span class="toggle-icon">{% if niap_td_total == 0 %}&#9658;{% else %}&#9660;{% endif %}</span>
  </div>
  <div class="card-body {% if niap_td_total == 0 %}collapsed{% endif %}">
    {% if diff.niap.tds.added %}
    <div class="sub-hdr sub-new">New TDs ({{ diff.niap.tds.added | length }})</div>
    {% for td in diff.niap.tds.added %}
    <div class="item-row" data-source="niap-td" data-kind="new">
      <span class="item-link">{{ td.identifier }} &mdash; {{ td.title }}</span>
      <span class="item-meta">{% if td.publication_date %}{{ td.publication_date[:10] }}{% endif %}</span>
    </div>
    {% if td.protection_profile %}
    <div class="item-sub">Applies to: {% for pp in td.protection_profile[:3] %}{{ pp.pp_short_name }}{% if not loop.last %}, {% endif %}{% endfor %}{% if td.protection_profile | length > 3 %} +{{ (td.protection_profile | length) - 3 }} more{% endif %}</div>
    {% endif %}
    {% endfor %}
    {% endif %}
    {% if diff.niap.tds.removed %}
    <div class="sub-hdr sub-removed">Removed TDs ({{ diff.niap.tds.removed | length }})</div>
    {% for td in diff.niap.tds.removed %}
    <div class="item-row" data-source="niap-td" data-kind="removed">
      <span class="item-link">{{ td.identifier }} &mdash; {{ td.title }}</span>
      <span class="item-meta">{% if td.removed_on %}Removed: {{ td.removed_on[:10] }}{% endif %}</span>
    </div>
    {% if td.protection_profile %}
    <div class="item-sub">Was for: {% for pp in td.protection_profile[:3] %}{{ pp.pp_short_name }}{% if not loop.last %}, {% endif %}{% endfor %}</div>
    {% endif %}
    {% endfor %}
    {% endif %}
    {% if niap_td_total == 0 %}<p class="no-change">No changes detected.{% if last_active.niap_td %} <span class="last-active">(last: {{ last_active.niap_td }})</span>{% endif %}</p>{% endif %}
  </div>
</div>
</div>

<div class="section-group">
  <div class="section-label">CCTL</div>
  <!-- CCTL Lab Intel -->
<div class="card {% if cctl_total > 0 %}card-new{% endif %}" id="sec-cctl" data-has-changes="{{ cctl_total }}">
  <div class="card-hdr" onclick="toggleCard(this)">
    <span>CCTL Lab Intel</span>
    <span class="card-count">{{ cctl_total }} new item{% if cctl_total != 1 %}s{% endif %}</span>
    <span class="sparkline">
      {% for v in sparklines.cctl %}
      <span class="sp-bar" style="height:{{ v }}%" title="{{ sp_counts.cctl[loop.index0] }} change{% if sp_counts.cctl[loop.index0] != 1 %}s{% endif %}"></span>
      {% endfor %}
    </span>
    <span class="toggle-icon">{% if cctl_total == 0 %}&#9658;{% else %}&#9660;{% endif %}</span>
  </div>
  <div class="card-body {% if cctl_total == 0 %}collapsed{% endif %}">
    {% for lab_name, lab_items in diff.cctl_labs.items() %}
    {% if lab_items %}
    <div class="lab-row">
      <div class="lab-hdr" onclick="toggleLab(this)">
        <span class="lab-name">{{ lab_name }}</span>
        <span class="lab-cnt">{{ lab_items | length }} item{% if lab_items | length != 1 %}s{% endif %}</span>
        <span class="toggle-icon">&#9658;</span>
      </div>
      <div class="lab-body collapsed">
        {% for item in lab_items %}
        <div class="item-row" data-source="cctl" data-kind="new">
          <a class="item-link" href="{{ item.link }}" target="_blank">{{ item.title }}</a>
          <span class="item-meta">{{ item.published }}</span>
        </div>
        {% endfor %}
      </div>
    </div>
    {% endif %}
    {% endfor %}
    {% if cctl_total == 0 %}<p class="no-change">No new items.{% if last_active.cctl %} <span class="last-active">(last: {{ last_active.cctl }})</span>{% endif %}</p>{% endif %}
  </div>
</div>
</div>

<div class="section-group">
  <div class="section-label">CSfC</div>
  <!-- CSfC Component Selections -->
<div class="card {% if csfc_total > 0 %}card-updated{% endif %}" id="sec-csfc" data-has-changes="{{ csfc_total }}">
  <div class="card-hdr" onclick="toggleCard(this)">
    <span>CSfC Component Selections</span>
    <span class="card-count">{{ csfc_total }} update{% if csfc_total != 1 %}s{% endif %}</span>
    <span class="sparkline">
      {% for v in sparklines.csfc %}
      <span class="sp-bar" style="height:{{ v }}%" title="{{ sp_counts.csfc[loop.index0] }} change{% if sp_counts.csfc[loop.index0] != 1 %}s{% endif %}"></span>
      {% endfor %}
    </span>
    <span class="toggle-icon">{% if csfc_total == 0 %}&#9658;{% else %}&#9660;{% endif %}</span>
  </div>
  <div class="card-body {% if csfc_total == 0 %}collapsed{% endif %}">
    {% for cp_name, cp in diff.csfc.component_selections.items() %}
    {% if cp.changed %}
    <div class="cp-row" data-source="csfc" data-kind="updated">
      <a class="item-link" href="{{ cp.url }}" target="_blank">{{ cp_name }}</a>
      <div class="cp-detail">
        <span class="cp-date">Content hash changed</span>
      </div>
    </div>
    {% endif %}
    {% endfor %}
    {% if csfc_total == 0 %}<p class="no-change">No changes detected.{% if last_active.csfc %} <span class="last-active">(last: {{ last_active.csfc }})</span>{% endif %}</p>{% endif %}
  </div>
</div>
</div>

<div class="section-group">
  <div class="section-label">Documentation</div>
  <!-- CC Crypto Docs -->
<div class="card {% if cc_crypto_total > 0 %}card-updated{% endif %}" id="sec-cc-crypto" data-has-changes="{{ cc_crypto_total }}">
  <div class="card-hdr" onclick="toggleCard(this)">
    <span>CC Crypto Documentation</span>
    <span class="card-count">{{ cc_crypto_total }} update{% if cc_crypto_total != 1 %}s{% endif %}</span>
    <span class="sparkline">
      {% for v in sparklines.cc_crypto %}
      <span class="sp-bar" style="height:{{ v }}%" title="{{ sp_counts.cc_crypto[loop.index0] }} change{% if sp_counts.cc_crypto[loop.index0] != 1 %}s{% endif %}"></span>
      {% endfor %}
    </span>
    <span class="toggle-icon">{% if cc_crypto_total == 0 %}&#9658;{% else %}&#9660;{% endif %}</span>
  </div>
  <div class="card-body {% if cc_crypto_total == 0 %}collapsed{% endif %}">
    {% for doc_name, doc in diff.cc_crypto.doc_headers.items() %}
    {% if doc.changed %}
    <div class="item-row" data-source="cc-crypto" data-kind="updated">
      <a class="item-link" href="{{ doc.url }}" target="_blank">{{ doc_name }}</a>
      <span class="item-meta">
        {% if doc.old_last_modified and doc.new_last_modified %}
        {{ doc.old_last_modified }} &#8594; {{ doc.new_last_modified }}
        {% else %}Header changed{% endif %}
      </span>
    </div>
    {% endif %}
    {% endfor %}
    {% if cc_crypto_total == 0 %}<p class="no-change">No changes detected.{% if last_active.cc_crypto %} <span class="last-active">(last: {{ last_active.cc_crypto }})</span>{% endif %}</p>{% endif %}
  </div>
</div>

  <!-- NIST Docs -->
<div class="card {% if nist_total > 0 %}card-updated{% endif %}" id="sec-nist" data-has-changes="{{ nist_total }}">
  <div class="card-hdr" onclick="toggleCard(this)">
    <span>NIST Documentation</span>
    <span class="card-count">{{ nist_total }} update{% if nist_total != 1 %}s{% endif %}</span>
    <span class="sparkline">
      {% for v in sparklines.nist %}
      <span class="sp-bar" style="height:{{ v }}%" title="{{ sp_counts.nist[loop.index0] }} change{% if sp_counts.nist[loop.index0] != 1 %}s{% endif %}"></span>
      {% endfor %}
    </span>
    <span class="toggle-icon">{% if nist_total == 0 %}&#9658;{% else %}&#9660;{% endif %}</span>
  </div>
  <div class="card-body {% if nist_total == 0 %}collapsed{% endif %}">
    {% for doc_name, doc in diff.nist.doc_headers.items() %}
    {% if doc.changed %}
    <div class="item-row" data-source="nist" data-kind="updated">
      <a class="item-link" href="{{ doc.url }}" target="_blank">{{ doc_name }}</a>
      <span class="item-meta">
        {% if doc.old_last_modified and doc.new_last_modified %}
        {{ doc.old_last_modified }} &#8594; {{ doc.new_last_modified }}
        {% else %}Header changed{% endif %}
      </span>
    </div>
    {% endif %}
    {% endfor %}
    {% if nist_total == 0 %}<p class="no-change">No changes detected.{% if last_active.nist %} <span class="last-active">(last: {{ last_active.nist }})</span>{% endif %}</p>{% endif %}
  </div>
</div>
</div>

<div class="section-group">
  <div class="section-label">CC Portal</div>
  <!-- CC Portal (international) -->
<div class="card {% if cc_portal_total > 0 %}card-new{% endif %}" id="sec-cc-portal" data-has-changes="{{ cc_portal_total }}">
  <div class="card-hdr" onclick="toggleCard(this)">
    <span>CC Portal (International)</span>
    <span class="card-count">{{ cc_portal_total }} new item{% if cc_portal_total != 1 %}s{% endif %}</span>
    <span class="sparkline">
      {% for v in sparklines.cc_portal %}
      <span class="sp-bar" style="height:{{ v }}%" title="{{ sp_counts.cc_portal[loop.index0] }} change{% if sp_counts.cc_portal[loop.index0] != 1 %}s{% endif %}"></span>
      {% endfor %}
    </span>
    <span class="toggle-icon">{% if cc_portal_total == 0 %}&#9658;{% else %}&#9660;{% endif %}</span>
  </div>
  <div class="card-body {% if cc_portal_total == 0 %}collapsed{% endif %}">
    {% if diff.cc_portal.news.added %}
    <div class="sub-hdr sub-new">News ({{ diff.cc_portal.news.added | length }})</div>
    {% for item in diff.cc_portal.news.added %}
    <div class="item-row" data-source="cc-portal" data-kind="new">
      <a class="item-link" href="{{ item.link or item.url or 'https://www.commoncriteriaportal.org/' }}" target="_blank">{{ item.title or item.text or item }}</a>
      <span class="item-meta">{{ item.date or '' }}</span>
    </div>
    {% endfor %}
    {% endif %}
    {% if diff.cc_portal.pps.added %}
    <div class="sub-hdr sub-new">New International PPs ({{ diff.cc_portal.pps.added | length }})</div>
    {% for pp in diff.cc_portal.pps.added %}
    <div class="item-row" data-source="cc-portal" data-kind="new">
      <a class="item-link" href="{{ pp.link or 'https://www.commoncriteriaportal.org/pps/' }}" target="_blank">{{ pp.title or pp.text or pp }}</a>
      <span class="item-meta"></span>
    </div>
    {% endfor %}
    {% endif %}
    {% if diff.cc_portal.products.added %}
    <div class="sub-hdr sub-new">New Certified Products ({{ diff.cc_portal.products.added | length }})</div>
    {% for prod in diff.cc_portal.products.added %}
    <div class="item-row" data-source="cc-portal" data-kind="new">
      <span class="item-link">{{ prod }}</span>
      <span class="item-meta"></span>
    </div>
    {% endfor %}
    {% endif %}
    {% if cc_portal_total == 0 %}<p class="no-change">No new international items.{% if last_active.cc_portal %} <span class="last-active">(last: {{ last_active.cc_portal }})</span>{% endif %}</p>{% endif %}
  </div>
</div>
</div>



      <div class="section-group" id="tab-pane-nato" data-tab="nato">
        <div class="section-label">NATO NIAPCL</div>
        <!-- NATO NIAPCL -->
        <div class="card {% if nato_total > 0 %}card-new{% endif %}" id="sec-nato" data-has-changes="{{ nato_total }}">
          <div class="card-hdr" onclick="toggleCard(this)">
            <span>NATO NIAPCL Products</span>
            <span class="card-count">{{ nato_total }} change{% if nato_total != 1 %}s{% endif %}</span>
            <span class="toggle-icon">{% if nato_total == 0 %}&#9658;{% else %}&#9660;{% endif %}</span>
          </div>
          <div class="card-body {% if nato_total == 0 %}collapsed{% endif %}">
            {% if diff.nato.cisco_added %}
            <div class="sub-hdr sub-new">New Cisco NATO Listings ({{ diff.nato.cisco_added | length }})</div>
            {% for item in diff.nato.cisco_added %}
            <div class="item-row" data-source="nato" data-kind="new">
              <a class="item-link" href="{{ item.link or config_nato_url }}" target="_blank">{{ item.name or item.raw_text }}</a>
              <span class="item-meta">{{ item.manufacturer or '' }}</span>
            </div>
            {% endfor %}
            {% endif %}
            {% if diff.nato.pages %}
            {% for page_key, page_diff in diff.nato.pages.items() %}
            {% if page_diff.added %}
            <div class="sub-hdr sub-new">{{ page_key }} — New Items ({{ page_diff.added | length }})</div>
            {% for item in page_diff.added %}
            <div class="item-row" data-source="nato" data-kind="new">
              <a class="item-link" href="{{ item.link or item.href or '#' }}" target="_blank">{{ item.name or item.text or item.raw_text or item }}</a>
              <span class="item-meta"></span>
            </div>
            {% endfor %}
            {% endif %}
            {% endfor %}
            {% endif %}
            {% if nato_total == 0 %}<p class="no-change">No NATO NIAPCL changes detected.</p>{% endif %}
          </div>
        </div>
      </div>

      <div class="section-group" id="tab-pane-eu" data-tab="eu">
        <div class="section-label">EU (EUCC / ENISA)</div>
        <!-- EUCC Requirements -->
        <div class="card {% if eucc_req_total > 0 %}card-updated{% endif %}" id="sec-eucc-req" data-has-changes="{{ eucc_req_total }}">
          <div class="card-hdr" onclick="toggleCard(this)">
            <span>EUCC Requirements &amp; Scheme Updates</span>
            <span class="card-count">{{ eucc_req_total }} change{% if eucc_req_total != 1 %}s{% endif %}</span>
            <span class="toggle-icon">{% if eucc_req_total == 0 %}&#9658;{% else %}&#9660;{% endif %}</span>
          </div>
          <div class="card-body {% if eucc_req_total == 0 %}collapsed{% endif %}">
            {% for item in diff.eucc.pages.requirements.added if diff.eucc.pages and diff.eucc.pages.requirements %}
            <div class="item-row" data-source="eucc" data-kind="updated">
              <a class="item-link" href="{{ item.href or '#' }}" target="_blank">{{ item.text or item.name or item }}</a>
              <span class="item-meta"></span>
            </div>
            {% endfor %}
            {% if eucc_req_total == 0 %}<p class="no-change">No EUCC requirement changes detected.</p>{% endif %}
          </div>
        </div>
        <!-- EUCC Certificates -->
        <div class="card {% if eucc_cert_total > 0 %}card-new{% endif %}" id="sec-eucc-certs" data-has-changes="{{ eucc_cert_total }}">
          <div class="card-hdr" onclick="toggleCard(this)">
            <span>EUCC Certified Products</span>
            <span class="card-count">{{ eucc_cert_total }} new{% if eucc_cert_total != 1 %} items{% else %} item{% endif %}</span>
            <span class="toggle-icon">{% if eucc_cert_total == 0 %}&#9658;{% else %}&#9660;{% endif %}</span>
          </div>
          <div class="card-body {% if eucc_cert_total == 0 %}collapsed{% endif %}">
            {% if diff.eucc.cisco_added %}
            <div class="sub-hdr sub-new">New Cisco EUCC Certifications ({{ diff.eucc.cisco_added | length }})</div>
            {% for item in diff.eucc.cisco_added %}
            <div class="item-row" data-source="eucc" data-kind="new">
              <a class="item-link" href="{{ item.href or '#' }}" target="_blank">{{ item.name or item.text }}</a>
              <span class="item-meta"></span>
            </div>
            {% endfor %}
            {% endif %}
            {% if diff.eucc.pages and diff.eucc.pages.certificates %}
            {% for item in diff.eucc.pages.certificates.added %}
            <div class="item-row" data-source="eucc" data-kind="new">
              <a class="item-link" href="{{ item.href or '#' }}" target="_blank">{{ item.name or item.text or item }}</a>
              <span class="item-meta"></span>
            </div>
            {% endfor %}
            {% endif %}
            {% if eucc_cert_total == 0 %}<p class="no-change">No new EUCC certified products.</p>{% endif %}
          </div>
        </div>
      </div>

</div><!-- /main-content -->
<footer>
  <span>CC Pulse &middot; Auto-refreshes daily (06:00 UTC) &middot; NIAP, CSfC, NIST, CC Portal, NATO NIAPCL, EUCC / ENISA</span>
  <span>Last run: {{ generated_at }}</span>
</footer>

<script>
function toggleCard(h){const b=h.nextElementSibling,i=h.querySelector('.toggle-icon'),c=b.classList.toggle('collapsed');if(i)i.textContent=c?'\u25ba':'\u25bc'}
function expandAll(){document.querySelectorAll('.card-body').forEach(b=>{b.classList.remove('collapsed');const i=b.previousElementSibling.querySelector('.toggle-icon');if(i)i.textContent='\u25bc'})}
function collapseAll(){document.querySelectorAll('.card-body').forEach(b=>{b.classList.add('collapsed');const i=b.previousElementSibling.querySelector('.toggle-icon');if(i)i.textContent='\u25ba'})}
let _zeroHidden=false;
function toggleZeroFilter(){_zeroHidden=!_zeroHidden;document.querySelectorAll('.card[data-has-changes]').forEach(c=>{const n=parseInt(c.dataset.hasChanges,10)||0;if(n===0)c.classList.toggle('zero-hidden',_zeroHidden)});const b=document.getElementById('filter-btn');if(b)b.textContent=_zeroHidden?'[F] Show All':'[F] Hide Empty';_reconcileGroupVisibility()}
function dismissAlert(btn){const r=btn.closest('.item-row');if(r)r.classList.add('alert-dismissed')}
function initAlertDismiss(){const stored=JSON.parse(sessionStorage.getItem('dismissed')||'[]');stored.forEach(k=>{const el=document.querySelector('[data-alert-key="'+k+'"]');if(el)el.classList.add('alert-dismissed')});document.querySelectorAll('.dismiss-btn').forEach(btn=>{btn.addEventListener('click',()=>{const r=btn.closest('[data-alert-key]');if(!r)return;const d=JSON.parse(sessionStorage.getItem('dismissed')||'[]');d.push(r.dataset.alertKey);sessionStorage.setItem('dismissed',JSON.stringify(d))})})}
let _activeKind='all';
function liveSearch(){const raw=(document.getElementById('search-input').value||'').trim(),term=raw.toLowerCase();document.querySelectorAll('mark.sh').forEach(m=>m.replaceWith(document.createTextNode(m.textContent)));const rows=document.querySelectorAll('.item-row,.cp-row');let vis=0;rows.forEach(row=>{const km=_activeKind==='all'||(_activeKind==='alert'&&row.closest('#sec-alerts'))||(row.dataset.kind===_activeKind);if(!km){row.classList.add('search-hidden');return}if(!term){row.classList.remove('search-hidden');vis++;return}const le=row.querySelector('.item-link'),me=row.querySelector('.item-meta'),se2=row.querySelector('.item-sub,.cp-detail'),hay=[le?le.textContent:'',me?me.textContent:'',se2?se2.textContent:''].join(' ').toLowerCase();if(hay.includes(term)){row.classList.remove('search-hidden');vis++;if(le)_highlight(le,raw)}else row.classList.add('search-hidden')});document.querySelectorAll('.card').forEach(card=>{const vr=card.querySelectorAll('.item-row:not(.search-hidden),.cp-row:not(.search-hidden)');let msg=card.querySelector('.no-results-msg');if(!msg){msg=document.createElement('p');msg.className='no-results-msg';msg.textContent='No matching items.';const body=card.querySelector('.card-body');if(body)body.appendChild(msg)}msg.style.display=(term||_activeKind!=='all')&&vr.length===0?'block':'none'});const ce=document.getElementById('search-count');if(ce)ce.textContent=(term||_activeKind!=='all')?vis+' result'+(vis!==1?'s':''):'';_reconcileGroupVisibility()}
function _highlight(el,term){const w=document.createTreeWalker(el,NodeFilter.SHOW_TEXT),nodes=[];let n;while((n=w.nextNode()))nodes.push(n);const lt=term.toLowerCase();nodes.forEach(tn=>{const i=tn.nodeValue.toLowerCase().indexOf(lt);if(i===-1)return;const b=document.createTextNode(tn.nodeValue.slice(0,i)),m=document.createElement('mark');m.className='sh';m.textContent=tn.nodeValue.slice(i,i+term.length);const a=document.createTextNode(tn.nodeValue.slice(i+term.length));tn.parentNode.replaceChild(a,tn);tn.parentNode.insertBefore(m,a);tn.parentNode.insertBefore(b,m)})}
function setKindFilter(btn){document.querySelectorAll('.chip').forEach(c=>c.classList.remove('active'));btn.classList.add('active');_activeKind=btn.dataset.kind;liveSearch()}
function clearSearch(){const inp=document.getElementById('search-input');if(inp)inp.value='';document.querySelectorAll('.chip').forEach(c=>c.classList.remove('active'));const ac=document.querySelector('.chip[data-kind="all"]');if(ac)ac.classList.add('active');_activeKind='all';liveSearch()}
function _reconcileGroupVisibility(){document.querySelectorAll('.section-group').forEach(g=>{const hv=[...g.querySelectorAll('.card')].some(c=>!c.classList.contains('zero-hidden')&&getComputedStyle(c).display!=='none');g.style.display=hv?'':' none'})}
document.addEventListener('keydown',e=>{const t=document.activeElement.tagName;if(t==='INPUT'||t==='TEXTAREA'){if(e.key==='Escape'){clearSearch();document.activeElement.blur()}return}if(e.key==='e'||e.key==='E')expandAll();if(e.key==='c'||e.key==='C')collapseAll();if(e.key==='f'||e.key==='F')toggleZeroFilter();if(e.key==='1'){const b=document.querySelector('.tab-btn[data-tab="us"]');if(b)switchTab(b)}if(e.key==='2'){const b=document.querySelector('.tab-btn[data-tab="nato"]');if(b)switchTab(b)}if(e.key==='3'){const b=document.querySelector('.tab-btn[data-tab="eu"]');if(b)switchTab(b)}if(e.key==='/'){e.preventDefault();const inp=document.getElementById('search-input');if(inp)inp.focus()}});
document.addEventListener('DOMContentLoaded',initAlertDismiss);
function switchTab(btn) {
            document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            const tab = btn.dataset.tab;
            // Show/hide NATO and EU tab panes
            document.querySelectorAll('.section-group[data-tab]').forEach(p => {
                p.classList.toggle('tab-active', p.dataset.tab === tab);
            });
            // Show/hide US sections (those without data-tab attribute, inside .grid)
            document.querySelectorAll('.grid > .section-group:not([data-tab])').forEach(p => {
                p.classList.toggle('tab-us-hidden', tab !== 'us');
            });
        }
  
</script>
</body>
</html>
"""


# -- RSS feed builder ---------------------------------------------------------
def _build_rss(diff: dict, generated_at: str) -> str:
    """Return an RSS 2.0 XML string summarising the diff."""
    items_xml = []

    for pp in diff.get("niap", {}).get("pps", {}).get("added", []):
        title = f"New PP: {pp.get('pp_short_name', '')}"
        link  = f"https://www.niap-ccevs.org/Profile/PP.cfm?id={pp.get('pp_id', '')}"
        items_xml.append(
            f"<item><title>{xml_escape(title)}</title><link>{link}</link>"
            f"<description>{xml_escape(pp.get('pp_name', ''))}</description></item>"
        )

    for pp in diff.get("niap", {}).get("pps", {}).get("removed", []):
        title = f"Removed PP: {pp.get('pp_short_name', '')}"
        link  = f"https://www.niap-ccevs.org/Profile/PP.cfm?id={pp.get('pp_id', '')}"
        items_xml.append(
            f"<item><title>{xml_escape(title)}</title><link>{link}</link>"
            f"<description>{xml_escape(pp.get('pp_name', ''))}</description></item>"
        )

    for td in diff.get("niap", {}).get("tds", {}).get("added", []):
        title = f"New TD: {td.get('identifier', '')}"
        items_xml.append(
            f"<item><title>{xml_escape(title)}</title>"
            f"<link>https://www.niap-ccevs.org/</link>"
            f"<description>{xml_escape(td.get('pp_short_name', ''))}</description></item>"
        )

    for item in diff.get("niap", {}).get("news", {}).get("added", []):
        title = item.get("title", "NIAP News")
        link  = item.get("link") or item.get("url") or "https://www.niap-ccevs.org/"
        items_xml.append(
            f"<item><title>{xml_escape(title)}</title><link>{link}</link>"
            f"<description>{xml_escape(item.get('date', ''))}</description></item>"
        )

    for lab, lab_items in diff.get("cctl_labs", {}).items():
        for it in (lab_items or []):
            title = f"[{lab}] {it.get('title', '')}"
            link  = it.get("link", "#")
            items_xml.append(
                f"<item><title>{xml_escape(title)}</title><link>{link}</link>"
                f"<description>{xml_escape(it.get('summary', '')[:200])}</description></item>"
            )

    for cp_name, cp in diff.get("csfc", {}).get("component_selections", {}).items():
        if cp.get("changed"):
            link = cp.get("url", "https://www.nsa.gov/Resources/Commercial-Solutions-for-Classified-Program/Components-List/")
            items_xml.append(
                f"<item><title>{xml_escape('CSfC Selection Updated: ' + cp_name)}</title>"
                f"<link>{link}</link>"
                f"<description>Component selection document updated.</description></item>"
            )

    for doc_name, doc in diff.get("nist", {}).get("doc_headers", {}).items():
        if doc.get("changed"):
            link = doc.get("url", "https://csrc.nist.gov/")
            items_xml.append(
                f"<item><title>{xml_escape('NIST Doc Updated: ' + doc_name)}</title>"
                f"<link>{link}</link>"
                f"<description>Document header updated.</description></item>"
            )

    for doc_name, doc in diff.get("cc_crypto", {}).get("doc_headers", {}).items():
        if doc.get("changed"):
            link = doc.get("url", "https://www.commoncriteriaportal.org/")
            items_xml.append(
                f"<item><title>{xml_escape('CC Crypto Doc Updated: ' + doc_name)}</title>"
                f"<link>{link}</link>"
                f"<description>Document header updated.</description></item>"
            )

    for alert in diff.get("alerts", []):
        items_xml.append(
            f"<item><title>{xml_escape('ALERT: ' + alert.get('source', '') + ' â ' + alert.get('title', ''))}</title>"
            f"<link>https://kr15tyk.github.io/CC-pulse/cc_dashboard.html</link>"
            f"<description>{xml_escape('Kind: ' + alert.get('kind', '') + ' | Keywords: ' + ', '.join(alert.get('matched_keywords', [])))}</description></item>"
        )

    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<rss version="2.0"><channel>\n'
        f'<title>CC Pulse</title>\n'
        f'<link>https://kr15tyk.github.io/CC-pulse/cc_dashboard.html</link>\n'
        f'<description>Common Criteria monitoring - {generated_at}</description>\n'
        + "\n".join(items_xml)
        + "\n</channel></rss>\n"
    )
    return xml


# -- Main render entry point --------------------------------------------------
def render_dashboard(diff: dict, output_dir: str = "docs") -> None:
    """Render the HTML dashboard and RSS feed from a diff dict."""
    os.makedirs(output_dir, exist_ok=True)
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    # Compute totals
    niap         = diff.get("niap", {})
    pps          = niap.get("pps", {})
    tds          = niap.get("tds", {})
    cisco        = niap.get("cisco_ndcpp", {})
    pcl_all      = niap.get("pcl_all", {})
    in_eval      = niap.get("in_evaluation", {})
    news         = niap.get("news", {})

    niap_pp_total   = (len(pps.get("added", [])) + len(pps.get("removed", [])) +
                       len(pps.get("sunset_changes", [])) + len(pps.get("status_changes", [])))
    niap_td_total   = len(tds.get("added", [])) + len(tds.get("removed", []))
    cisco_total     = (len(cisco.get("added", [])) + len(cisco.get("removed", [])) +
                       len(cisco.get("newly_archived", [])))
    pcl_all_total   = (len(pcl_all.get("added", [])) + len(pcl_all.get("removed", [])) +
                       len(pcl_all.get("newly_archived", [])))
    in_eval_added   = len(in_eval.get("added", []))
    in_eval_removed = len(in_eval.get("removed", []))
    in_eval_total   = in_eval_added + in_eval_removed
    in_eval_current = in_eval.get("current_count", 0)
    niap_news_total = len(news.get("added", []))

    cctl_total      = sum(len(v) for v in diff.get("cctl_labs", {}).values() if v)
    csfc_total      = sum(1 for cp in diff.get("csfc", {}).get("component_selections", {}).values()
                          if cp.get("changed"))
    cc_crypto_total = sum(1 for d in diff.get("cc_crypto", {}).get("doc_headers", {}).values()
                          if d.get("changed"))
    nist_total      = sum(1 for d in diff.get("nist", {}).get("doc_headers", {}).values()
                          if d.get("changed"))
    alert_total     = len(diff.get("alerts", []))

    niap_total_stat = niap_pp_total + niap_td_total + cisco_total + pcl_all_total + in_eval_total + niap_news_total
    cctl_total_stat = cctl_total
    csfc_total_stat = csfc_total
    nist_total_stat = nist_total + cc_crypto_total

    # CCTL registry changes
    cctls_diff          = niap.get("cctls", {})
    cctl_registry_total = (len(cctls_diff.get("added", [])) +
                           len(cctls_diff.get("removed", [])) +
                           len(cctls_diff.get("status_changes", [])))

    # CC Portal totals
    ccp                 = diff.get("cc_portal", {})
    cc_portal_news_n    = len(ccp.get("news", {}).get("added", []))
    cc_portal_pps_n     = len(ccp.get("pps", {}).get("added", []))
    cc_portal_prod_n    = len(ccp.get("products", {}).get("added", []))
    cc_portal_total     = cc_portal_news_n + cc_portal_pps_n + cc_portal_prod_n
    cc_portal_total_stat = cc_portal_total

    # NATO NIAPCL totals
    nato_diff = diff.get("nato", {})
    nato_total = (
        len(nato_diff.get("cisco_added", [])) +
        len(nato_diff.get("cisco_removed", [])) +
        sum(len(v.get("added", [])) for v in nato_diff.get("pages", {}).values())
    )

    # EUCC totals
    eucc_diff = diff.get("eucc", {})
    eucc_req_total  = len(eucc_diff.get("pages", {}).get("requirements", {}).get("added", []))
    eucc_cert_total = (
        len(eucc_diff.get("cisco_added", [])) +
        len(eucc_diff.get("pages", {}).get("certificates", {}).get("added", []))
    )
    eucc_total = eucc_req_total + eucc_cert_total

    # Sparkline data
    recent_diffs = _load_recent_diffs()
    def _sp(section_key):
        counts = _section_daily_counts(recent_diffs, section_key)
        if not counts:
            return [0] * 7
        mx = max(counts) or 1
        return [round(c / mx * 100) for c in counts]

    def _sp_counts(section_key):
        counts = _section_daily_counts(recent_diffs, section_key)
        if not counts:
            return [0] * 7
        return counts

    sparklines = {
        "niap_pp":   _sp("niap_pp"),
        "niap_td":   _sp("niap_td"),
        "niap_news": _sp("niap_news"),
        "cctl":      _sp("cctl"),
        "csfc":      _sp("csfc"),
        "cc_crypto": _sp("cc_crypto"),
        "nist":      _sp("nist"),
        "cc_portal": _sp("cc_portal"),
        "pcl_all":   _sp("pcl_all"),
        "in_eval":   _sp("in_eval"),
    }

    sp_counts = {
        "niap_pp":   _sp_counts("niap_pp"),
        "niap_td":   _sp_counts("niap_td"),
        "niap_news": _sp_counts("niap_news"),
        "cctl":      _sp_counts("cctl"),
        "csfc":      _sp_counts("csfc"),
        "cc_crypto": _sp_counts("cc_crypto"),
        "nist":      _sp_counts("nist"),
        "cc_portal": _sp_counts("cc_portal"),
        "pcl_all":   _sp_counts("pcl_all"),
        "in_eval":   _sp_counts("in_eval"),
    }

    # Last-active dates: find most recent diff where each section had changes
    def _last_active(section_key):
        for d in reversed(recent_diffs):
            counts = _section_daily_counts([d], section_key)
            if counts and counts[0] > 0:
                return d.get("period_end", "")[:10]
        return None
    last_active = {k: _last_active(k) for k in [
        "niap_pp","niap_td","niap_news","cctl","csfc",
        "cc_crypto","nist","cc_portal","pcl_all","in_eval"
    ]}

    # Render template
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
        cctl_registry_total  = cctl_registry_total,
        cc_portal_total      = cc_portal_total,
        cc_portal_total_stat = cc_portal_total_stat,
        period_start         = diff.get("period_start", ""),
        pcl_all_total        = pcl_all_total,
        in_eval_added        = in_eval_added,
        in_eval_removed      = in_eval_removed,
        in_eval_total        = in_eval_total,
        in_eval_current      = in_eval_current,
        period_end           = diff.get("period_end", ""),
        sp_counts            = sp_counts,
        last_active          = last_active,
        watch_keywords       = config.WATCH_KEYWORDS,
        nato_total           = nato_total,
        eucc_total           = eucc_total,
        eucc_req_total       = eucc_req_total,
        eucc_cert_total      = eucc_cert_total,
        config_nato_url      = config.NATO_NIAPCL_URL,
        config_eucc_req_url  = config.EUCC_REQUIREMENTS_URL,
        config_eucc_cert_url = config.EUCC_CERTIFICATES_URL,
    )

    html_path = os.path.join(output_dir, "cc_dashboard.html")
    with open(html_path, "w", encoding="utf-8") as fh:
        fh.write(html)
    log.info("Dashboard written to %s", html_path)

    rss = _build_rss(diff, generated_at)
    rss_path = os.path.join(output_dir, "cc_pulse.rss")
    with open(rss_path, "w", encoding="utf-8") as fh:
        fh.write(rss)
    log.info("RSS feed written to %s", rss_path)



