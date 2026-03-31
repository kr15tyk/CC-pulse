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
            n = sum(1 for cp in d.get("csfc", {}).get("capability_packages", {}).values()
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
:root {
  --bg:     #000000;
  --card:   #0a0a0a;
  --border: #1a4a1a;
  --text:   #00cc00;
  --muted:  #006600;
  --green:  #00ff41;
  --amber:  #aaff00;
  --red:    #ff3300;
  --blue:   #00cc00;
  --purple: #00cc00;
  --font:   "Courier New", Courier, monospace;
}
* { box-sizing: border-box; margin: 0; padding: 0; }
body { background: var(--bg); color: var(--text); font-family: var(--font); font-size: 13px; padding: 1rem; line-height: 1.5; }
a { color: var(--green); text-decoration: none; }
a:hover { color: var(--amber); text-decoration: underline; }

/* Sticky top bar */
.sticky-top { position: sticky; top: 0; z-index: 100; background: var(--bg); padding-bottom: 0.5rem; border-bottom: 1px solid var(--border); margin-bottom: 1rem; }
/* Header */
.site-header { display: flex; justify-content: space-between; align-items: center; padding: 0.5rem 0 0.3rem; flex-wrap: wrap; gap: 0.5rem; }
.site-title { font-size: 1.4rem; font-weight: 700; color: var(--green); letter-spacing: 0.05em; }
.site-title::before { content: "> "; color: var(--muted); }
.site-title::after { content: "_"; color: var(--green); animation: blink 1s step-end infinite; }
@keyframes blink { 0%,100%{opacity:1} 50%{opacity:0} }
.site-meta { font-size: 0.75rem; color: var(--muted); }

/* Alert banner */
.alert-banner { background: #0a0a00; border: 1px solid var(--amber); padding: 0.6rem 1rem; margin-bottom: 1rem; font-weight: 700; color: var(--amber); font-family: var(--font); }
.alert-banner::before { content: "[!] "; }

/* Trend bar */
.trend-bar { display: flex; flex-wrap: wrap; gap: 0.5rem; margin-bottom: 1.5rem; }
.stat { background: var(--card); border: 1px solid var(--border); padding: 0.75rem 1rem; min-width: 120px; flex: 1; }
.stat a { color: inherit; text-decoration: none; display: block; }
.stat a:hover .stat-num { color: var(--amber); }
.stat-num { font-size: 1.6rem; font-weight: 700; color: var(--muted); font-family: var(--font); }
.stat-num.active, .stat-num.active-num { color: var(--green); }
.stat-num.alert,  .stat-num.alert-num  { color: var(--red); }
.stat.has-data { border-color: var(--green); }
.stat.has-alert { border-color: var(--red); }
.stat-label { font-size: 0.6rem; color: var(--muted); text-transform: uppercase; letter-spacing: 0.12em; margin-top: 2px; }

/* Sparkline */
.sparkline { display: inline-flex; align-items: flex-end; gap: 2px; height: 14px; margin-right: 6px; vertical-align: middle; }
.sparkline span { display: inline-block; width: 4px; background: var(--muted); }

/* Section groups */
.section-group { margin-bottom: 1.5rem; }
.section-label { font-size: 0.6rem; font-weight: 700; text-transform: uppercase; letter-spacing: 0.15em; color: var(--muted); padding: 0.25rem 0 0.5rem 0.25rem; border-bottom: 1px solid var(--border); margin-bottom: 0.5rem; }
.section-label::before { content: "-- "; }
.section-label::after  { content: " --"; }
.section-group .card { margin-bottom: 0.5rem; }

/* Cards */
.card { background: var(--card); border: 1px solid var(--border); overflow: hidden; }
.card-new     { border-left: 3px solid var(--green); }
.card-updated { border-left: 3px solid var(--amber); }
.card-alert   { border-left: 3px solid var(--red); }
.card-hdr { display: flex; align-items: center; gap: 0.5rem; padding: 0.6rem 1rem; cursor: pointer; user-select: none; }
.card-hdr:hover { background: rgba(0,204,0,0.05); }
.card-hdr > span:first-child { font-weight: 700; flex: 1; color: var(--text); }
.card-count { font-size: 0.7rem; color: var(--muted); background: rgba(0,204,0,0.07); padding: 2px 6px; font-family: var(--font); }
.toggle-icon { font-size: 0.6rem; color: var(--muted); margin-left: 4px; }
.card-body { padding: 0.75rem 1rem; border-top: 1px solid var(--border); }
.card-body.collapsed { display: none; }

/* Item rows */
.item-row { display: flex; justify-content: space-between; align-items: baseline; gap: 0.5rem; padding: 4px 0; border-bottom: 1px solid rgba(0,100,0,0.2); }
.item-row:last-child { border-bottom: none; }
.item-link { color: var(--green); flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.item-link::before { content: "> "; color: var(--muted); }
.item-meta { font-size: 0.7rem; color: var(--muted); white-space: nowrap; flex-shrink: 0; }
.no-change { color: var(--muted); font-size: 0.8rem; padding: 0.25rem 0; }
.no-change::before { content: "-- "; }
.item-sub { font-size: 0.72rem; color: var(--muted); padding: 1px 0 4px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; padding-left: 1.4em; }

/* Alert item rows */
.alert-item .item-link { color: var(--red); }
.alert-item .item-link::before { content: "[!] "; color: var(--red); }
.alert-item .item-meta { color: var(--amber); }
.alert-dismissed { opacity: 0.35; }
.alert-dismissed .item-link { text-decoration: line-through; }
.dismiss-btn { background: none; border: 1px solid var(--muted); color: var(--muted); font-family: var(--font); font-size: 0.62rem; padding: 1px 5px; cursor: pointer; margin-left: auto; white-space: nowrap; }
.dismiss-btn:hover { border-color: var(--green); color: var(--green); }
.dismiss-btn.seen { border-color: var(--green); color: var(--green); }
.alert-monitoring { font-size: 0.62rem; color: var(--muted); margin-bottom: 0.5rem; padding-bottom: 0.4rem; border-bottom: 1px solid rgba(0,100,0,0.2); word-break: break-word; }
.alert-monitoring::before { content: "Monitoring: "; color: var(--muted); }

/* CCTL lab rows */
.lab-row { margin-bottom: 0.5rem; }
.lab-hdr { display: flex; align-items: center; gap: 0.5rem; padding: 4px 0; cursor: pointer; }
.lab-hdr:hover { color: var(--amber); }
.lab-name { font-weight: 700; flex: 1; }
.lab-cnt { font-size: 0.7rem; color: var(--muted); }
.lab-body { padding-left: 0.75rem; }
.lab-body.collapsed { display: none; }

/* CSfC CP detail */
.cp-row { padding: 4px 0; border-bottom: 1px solid rgba(0,100,0,0.2); }
.cp-row:last-child { border-bottom: none; }
.cp-name { color: var(--green); font-weight: 700; }
.cp-meta { font-size: 0.7rem; color: var(--muted); margin-top: 2px; }

/* Toggle controls */
.ctrl-bar { font-size: 0.72rem; color: var(--muted); margin-bottom: 1rem; display: flex; gap: 1rem; align-items: center; }
.ctrl-btn { background: none; border: 1px solid var(--border); color: var(--muted); font-family: var(--font); font-size: 0.72rem; padding: 2px 8px; cursor: pointer; }
.ctrl-btn:hover { border-color: var(--green); color: var(--green); }
.ctrl-hint { font-size: 0.65rem; color: var(--muted); opacity: 0.6; }
/* Last-active label */
.last-active { font-size: 0.65rem; color: var(--muted); opacity: 0.7; margin-left: 0.5rem; }
/* Card hidden when zero-filter is on */
.card.zero-hidden { display: none; }
/* Footer */
.site-footer { margin-top: 2rem; padding-top: 0.75rem; border-top: 1px solid var(--border); font-size: 0.72rem; color: var(--muted); text-align: center; }

/* Source Health summary */
.source-health {
  background: var(--card);
  border: 1px solid var(--border);
  padding: 0.75rem 1rem;
  margin-bottom: 1.5rem;
}
.sh-title {
  font-size: 0.6rem;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.15em;
  color: var(--muted);
  margin-bottom: 0.5rem;
}
.sh-title::before { content: "-- "; }
.sh-title::after  { content: " --"; }
.sh-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 3px 0;
  border-bottom: 1px solid rgba(0,100,0,0.15);
}
.sh-row:last-of-type { border-bottom: none; }
.sh-source { font-size: 0.78rem; color: var(--text); }
.sh-source::before { content: "  "; }
.sh-badge {
  font-size: 0.68rem;
  padding: 2px 8px;
  font-weight: 700;
  font-family: var(--font);
}
.sh-ok    { color: var(--green); }
.sh-idle  { color: var(--muted); }
.sh-alert { color: var(--red);   }
.sh-footer {
  font-size: 0.7rem;
  color: var(--muted);
  margin-top: 0.5rem;
  padding-top: 0.25rem;
  border-top: 1px solid rgba(0,100,0,0.15);
}

@media (max-width: 600px) {
  .trend-bar { flex-direction: column; }
  .stat { min-width: unset; }
}
</style>
</head>
<body>

{% if diff.alerts %}
<div class="alert-banner">{{ alert_total }} keyword alert{% if alert_total != 1 %}s{% endif %} &mdash; see Alerts section below.</div>
{% endif %}

<div class="sticky-top">
<header class="site-header">
  <div class="site-title">CC Pulse</div>
  <div class="site-meta">{{ period_start[:10] }} → {{ period_end[:10] }} &middot; Generated {{ generated_at }}</div>
</header>
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
    <div class="stat-lbl">CSfC CP Updates</div>
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
<!-- Source health summary -->
<div class="source-health">
  <div class="sh-title">Source Health</div>
  <div class="sh-row">
    <span class="sh-source">NIAP API</span>
    <span class="sh-badge {% if niap_total_stat > 0 %}sh-ok{% else %}sh-idle{% endif %}">
      {% if niap_total_stat > 0 %}{{ niap_total_stat }} change{% if niap_total_stat != 1 %}s{% endif %}{% else %}no changes{% endif %}
    </span>
  </div>
  <div class="sh-row">
    <span class="sh-source">CSfC / NSA</span>
    <span class="sh-badge {% if csfc_total_stat > 0 %}sh-ok{% else %}sh-idle{% endif %}">
      {% if csfc_total_stat > 0 %}{{ csfc_total_stat }} update{% if csfc_total_stat != 1 %}s{% endif %}{% else %}no changes{% endif %}
    </span>
  </div>
  <div class="sh-row">
    <span class="sh-source">NIST CSRC</span>
    <span class="sh-badge {% if nist_total_stat > 0 %}sh-ok{% else %}sh-idle{% endif %}">
      {% if nist_total_stat > 0 %}{{ nist_total_stat }} update{% if nist_total_stat != 1 %}s{% endif %}{% else %}no changes{% endif %}
    </span>
  </div>
  <div class="sh-row">
    <span class="sh-source">CC Portal</span>
    <span class="sh-badge {% if cc_portal_total_stat > 0 %}sh-ok{% else %}sh-idle{% endif %}">
      {% if cc_portal_total_stat > 0 %}{{ cc_portal_total_stat }} new item{% if cc_portal_total_stat != 1 %}s{% endif %}{% else %}no changes{% endif %}
    </span>
  </div>
  <div class="sh-row">
    <span class="sh-source">CCTL Labs</span>
    <span class="sh-badge {% if cctl_total_stat > 0 %}sh-ok{% else %}sh-idle{% endif %}">
      {% if cctl_total_stat > 0 %}{{ cctl_total_stat }} new item{% if cctl_total_stat != 1 %}s{% endif %}{% else %}no changes{% endif %}
    </span>
  </div>
  <div class="sh-row">
    <span class="sh-source">Keyword Alerts</span>
    <span class="sh-badge {% if alert_total > 0 %}sh-alert{% else %}sh-idle{% endif %}">
      {% if alert_total > 0 %}{{ alert_total }} match{% if alert_total != 1 %}es{% endif %}{% else %}none{% endif %}
    </span>
  </div>
  <div class="sh-footer">Period: {{ period_start[:10] if period_start else '—' }} → {{ period_end[:10] if period_end else '—' }}</div>
  <div class="sh-footer">All sources last polled: {{ period_end[:10] if period_end else '—' }} (daily 06:00 UTC)</div>
</div>
<div class="ctrl-bar">
  <button class="ctrl-btn" onclick="expandAll()" title="Expand all cards [E]">[E] Expand All</button>
  <button class="ctrl-btn" onclick="collapseAll()" title="Collapse all cards [C]">[C] Collapse All</button>
  <button class="ctrl-btn" id="filter-btn" onclick="toggleZeroFilter()" title="Toggle zero-change cards [F]">[F] Hide Empty</button>
  <span class="ctrl-hint">keyboard: E=expand C=collapse F=filter</span>
</div>
<!-- Alerts -->
{% if diff.alerts %}
<div class="section-group">
  <div class="section-label">Alerts</div>
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
    <div class="item-row">
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
    <div class="item-row">
      <span class="item-link">{{ item.vendor_id_name }} &mdash; {{ item.product_name }}</span>
      <span class="item-meta">{{ item.status_sort }}</span>
    </div>
    {% endfor %}
    {% endif %}
    {% if diff.niap.cisco_ndcpp.newly_archived %}
    <div class="sub-hdr sub-updated">Newly Archived ({{ diff.niap.cisco_ndcpp.newly_archived | length }})</div>
    {% for item in diff.niap.cisco_ndcpp.newly_archived %}
    <div class="item-row">
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
    <div class="item-row">
      <a class="item-link" href="{{ lab.cctl_url or '#' }}" target="_blank">{{ lab.cctl_name }}</a>
      <span class="item-meta">{{ lab.city }}{% if lab.state_id %}, {{ lab.state_id }}{% endif %}</span>
    </div>
    {% endfor %}
    {% endif %}
    {% if diff.niap.cctls.removed %}
    <div class="sub-hdr sub-removed">Removed Labs ({{ diff.niap.cctls.removed | length }})</div>
    {% for lab in diff.niap.cctls.removed %}
    <div class="item-row">
      <a class="item-link" href="{{ lab.cctl_url or '#' }}" target="_blank">{{ lab.cctl_name }}</a>
      <span class="item-meta">{{ lab.city }}{% if lab.state_id %}, {{ lab.state_id }}{% endif %}</span>
    </div>
    {% endfor %}
    {% endif %}
    {% if diff.niap.cctls.status_changes %}
    <div class="sub-hdr sub-updated">Status Changes ({{ diff.niap.cctls.status_changes | length }})</div>
    {% for lab in diff.niap.cctls.status_changes %}
    <div class="item-row">
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
    <div class="item-row">
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
    <div class="item-row">
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
    <div class="item-row">
      <a class="item-link" href="{{ item.link or item.url or '#' }}" target="_blank">{{ item.title }}</a>
      <span class="item-meta">{{ item.date }}</span>
    </div>
    {% endfor %}
    {% endif %}
    {% if diff.niap.events.added %}
    <div class="sub-hdr sub-new">Events ({{ diff.niap.events.added | length }})</div>
    {% for ev in diff.niap.events.added %}
    <div class="item-row">
      <span class="item-link">{{ ev.title or ev.name or ev }}</span>
      <span class="item-meta">{{ ev.date or ev.start_date or '' }}</span>
    </div>
    {% endfor %}
    {% endif %}
    {% set policy_items = diff.niap.news.added | selectattr("_category", "equalto", "POLICY") | list %}
    {% if policy_items %}
    <div class="sub-hdr sub-updated">Policy Letters &amp; Updates ({{ policy_items | length }})</div>
    {% for item in policy_items %}
    <div class="item-row">
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
    <div class="item-row">
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
    <div class="item-row">
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
    <div class="item-row">
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
    <div class="item-row">
      <a class="item-link" href="https://www.niap-ccevs.org/Profile/PP.cfm?id={{ pp.pp_id }}" target="_blank">{{ pp.pp_short_name }}</a>
      <span class="item-meta">{{ pp.tech_type }} &middot; {{ pp.pp_date }}</span>
    </div>
    {% endfor %}
    {% endif %}
    {% if diff.niap.pps.removed %}
    <div class="sub-hdr sub-removed">Removed PPs ({{ diff.niap.pps.removed | length }})</div>
    {% for pp in diff.niap.pps.removed %}
    <div class="item-row">
      <a class="item-link" href="https://www.niap-ccevs.org/Profile/PP.cfm?id={{ pp.pp_id }}" target="_blank">{{ pp.pp_short_name }}</a>
      <span class="item-meta">{{ pp.tech_type }} &middot; {{ pp.pp_date }}</span>
    </div>
    {% endfor %}
    {% endif %}
    {% if diff.niap.pps.sunset_changes %}
    <div class="sub-hdr sub-updated">Sunset Changes ({{ diff.niap.pps.sunset_changes | length }})</div>
    {% for pp in diff.niap.pps.sunset_changes %}
    <div class="item-row">
      <a class="item-link" href="https://www.niap-ccevs.org/Profile/PP.cfm?id={{ pp.pp_id }}" target="_blank">{{ pp.pp_short_name }}</a>
      <span class="item-meta">{{ pp.tech_type }} &middot; Sunset: {{ pp.sunset_date }}</span>
    </div>
    {% endfor %}
    {% endif %}
    {% if diff.niap.pps.status_changes %}
    <div class="sub-hdr sub-updated">Status Changes ({{ diff.niap.pps.status_changes | length }})</div>
    {% for pp in diff.niap.pps.status_changes %}
    <div class="item-row">
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
    <div class="item-row">
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
    <div class="item-row">
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
        <div class="item-row">
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
  <!-- CSfC Capability Packages -->
<div class="card {% if csfc_total > 0 %}card-updated{% endif %}" id="sec-csfc" data-has-changes="{{ csfc_total }}">
  <div class="card-hdr" onclick="toggleCard(this)">
    <span>CSfC Capability Packages</span>
    <span class="card-count">{{ csfc_total }} update{% if csfc_total != 1 %}s{% endif %}</span>
    <span class="sparkline">
      {% for v in sparklines.csfc %}
      <span class="sp-bar" style="height:{{ v }}%" title="{{ sp_counts.csfc[loop.index0] }} change{% if sp_counts.csfc[loop.index0] != 1 %}s{% endif %}"></span>
      {% endfor %}
    </span>
    <span class="toggle-icon">{% if csfc_total == 0 %}&#9658;{% else %}&#9660;{% endif %}</span>
  </div>
  <div class="card-body {% if csfc_total == 0 %}collapsed{% endif %}">
    {% for cp_name, cp in diff.csfc.capability_packages.items() %}
    {% if cp.changed %}
    <div class="cp-row">
      <a class="item-link" href="{{ cp.url }}" target="_blank">{{ cp_name }}</a>
      <div class="cp-detail">
        {% if cp.old_last_modified and cp.new_last_modified %}
        <span class="cp-date">{{ cp.old_last_modified }} &#8594; {{ cp.new_last_modified }}</span>
        {% elif cp.old_content_length is defined and cp.new_content_length is defined %}
        <span class="cp-date">Size: {{ cp.old_content_length }} &#8594; {{ cp.new_content_length }} bytes</span>
        {% else %}
        <span class="cp-date">Content changed</span>
        {% endif %}
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
    <div class="item-row">
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
    <div class="item-row">
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
    <div class="item-row">
      <a class="item-link" href="{{ item.link or item.url or 'https://www.commoncriteriaportal.org/' }}" target="_blank">{{ item.title or item.text or item }}</a>
      <span class="item-meta">{{ item.date or '' }}</span>
    </div>
    {% endfor %}
    {% endif %}
    {% if diff.cc_portal.pps.added %}
    <div class="sub-hdr sub-new">New International PPs ({{ diff.cc_portal.pps.added | length }})</div>
    {% for pp in diff.cc_portal.pps.added %}
    <div class="item-row">
      <a class="item-link" href="{{ pp.link or 'https://www.commoncriteriaportal.org/pps/' }}" target="_blank">{{ pp.title or pp.text or pp }}</a>
      <span class="item-meta"></span>
    </div>
    {% endfor %}
    {% endif %}
    {% if diff.cc_portal.products.added %}
    <div class="sub-hdr sub-new">New Certified Products ({{ diff.cc_portal.products.added | length }})</div>
    {% for prod in diff.cc_portal.products.added %}
    <div class="item-row">
      <span class="item-link">{{ prod }}</span>
      <span class="item-meta"></span>
    </div>
    {% endfor %}
    {% endif %}
    {% if cc_portal_total == 0 %}<p class="no-change">No new international items.{% if last_active.cc_portal %} <span class="last-active">(last: {{ last_active.cc_portal }})</span>{% endif %}</p>{% endif %}
  </div>
</div>
</div>


<footer>
  <span>CC Pulse &middot; Auto-refreshes daily (06:00 UTC) &middot; Data from NIAP, CSfC, NIST, CC Portal</span>
  <span>Last run: {{ generated_at }}</span>
</footer>

<script>
// Card toggle
function toggleCard(hdr) {
  var body = hdr.nextElementSibling;
  var icon = hdr.querySelector('.toggle-icon');
  if (body.classList.contains('collapsed')) {
    body.classList.remove('collapsed');
    if (icon) icon.textContent = '▼';
  } else {
    body.classList.add('collapsed');
    if (icon) icon.textContent = '►';
  }
}
function expandAll() {
  document.querySelectorAll('.card-body.collapsed').forEach(function(b) {
    b.classList.remove('collapsed');
    var icon = b.previousElementSibling && b.previousElementSibling.querySelector('.toggle-icon');
    if (icon) icon.textContent = '\u25bc';
  });
}
function collapseAll() {
  document.querySelectorAll('.card-body:not(.collapsed)').forEach(function(b) {
    b.classList.add('collapsed');
    var icon = b.previousElementSibling && b.previousElementSibling.querySelector('.toggle-icon');
    if (icon) icon.textContent = '\u25b6';
  });
}

// Filter zero-change cards
var zeroFilterOn = false;
function toggleZeroFilter() {
  zeroFilterOn = !zeroFilterOn;
  document.querySelectorAll('.card[data-has-changes]').forEach(function(card) {
    var n = parseInt(card.getAttribute('data-has-changes') || '0', 10);
    if (zeroFilterOn && n === 0) {
      card.classList.add('zero-hidden');
    } else {
      card.classList.remove('zero-hidden');
    }
  });
  var btn = document.getElementById('filter-btn');
  if (btn) btn.textContent = zeroFilterOn ? '[F] Show All' : '[F] Hide Empty';
}

// Keyboard shortcuts: E=expand, C=collapse, F=filter
document.addEventListener('keydown', function(e) {
  if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return;
  if (e.key === 'e' || e.key === 'E') expandAll();
  if (e.key === 'c' || e.key === 'C') collapseAll();
  if (e.key === 'f' || e.key === 'F') toggleZeroFilter();
});

// Highlight active stat tiles
document.querySelectorAll('.stat').forEach(function(stat) {
  var num = stat.querySelector('.stat-num');
  if (!num) return;
  var val = parseInt(num.textContent.trim(), 10);
  if (isNaN(val) || val === 0) return;
  if (num.classList.contains('alert-num')) {
    stat.classList.add('has-alert');
  } else {
    stat.classList.add('has-data');
  }
});
// Alert dismiss (localStorage persistence)
function dismissAlert(btn) {
  var row = btn.closest('.item-row');
  var key = 'cpulse_seen_' + (row.dataset.alertKey || '');
  if (row.classList.contains('alert-dismissed')) {
    row.classList.remove('alert-dismissed');
    btn.classList.remove('seen');
    btn.textContent = '\u2713 Seen';
    localStorage.removeItem(key);
  } else {
    row.classList.add('alert-dismissed');
    btn.classList.add('seen');
    btn.textContent = '\u2713 Seen';
    localStorage.setItem(key, '1');
  }
}
(function initAlertDismiss() {
  document.querySelectorAll('.item-row[data-alert-key]').forEach(function(row) {
    var key = 'cpulse_seen_' + row.dataset.alertKey;
    if (localStorage.getItem(key)) {
      row.classList.add('alert-dismissed');
      var btn = row.querySelector('.dismiss-btn');
      if (btn) { btn.classList.add('seen'); btn.textContent = '\u2713 Seen'; }
    }
  });
})();
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

    for cp_name, cp in diff.get("csfc", {}).get("capability_packages", {}).items():
        if cp.get("changed"):
            link = cp.get("url", "https://www.nsa.gov/")
            items_xml.append(
                f"<item><title>{xml_escape('CSfC CP Updated: ' + cp_name)}</title>"
                f"<link>{link}</link>"
                f"<description>Capability package updated.</description></item>"
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
            f"<item><title>{xml_escape('ALERT: ' + alert.get('source', '') + ' – ' + alert.get('title', ''))}</title>"
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
    csfc_total      = sum(1 for cp in diff.get("csfc", {}).get("capability_packages", {}).values()
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



