"""
dashboard.py -- Renders the daily HTML dashboard and RSS feed.

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
  --bg: #0f1117;
  --card: #1a1d27;
  --border: #2a2d3a;
  --text: #e2e8f0;
  --muted: #64748b;
  --green: #22c55e;
  --amber: #f59e0b;
  --red: #ef4444;
  --blue: #3b82f6;
  --purple: #a855f7;
}
* { box-sizing: border-box; margin: 0; padding: 0; }
body { background: var(--bg); color: var(--text); font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; font-size: 14px; padding: 1rem; }
a { color: var(--blue); text-decoration: none; }
a:hover { text-decoration: underline; }

/* Header */
.site-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 1.5rem; flex-wrap: wrap; gap: 0.5rem; }
.site-title { font-size: 1.4rem; font-weight: 700; color: var(--text); }
.site-meta { font-size: 0.75rem; color: var(--muted); }

/* Alert banner */
.alert-banner { background: #7f1d1d; border: 1px solid var(--red); border-radius: 6px; padding: 0.75rem 1rem; margin-bottom: 1rem; font-weight: 600; color: #fca5a5; }

/* Trend bar */
.trend-bar { display: flex; flex-wrap: wrap; gap: 0.5rem; margin-bottom: 1.5rem; }
.stat { background: var(--card); border: 1px solid var(--border); border-radius: 8px; padding: 0.75rem 1rem; min-width: 120px; flex: 1; }
.stat a { color: inherit; text-decoration: none; display: block; }
.stat a:hover .stat-num { color: var(--blue); }
.stat-num { font-size: 1.6rem; font-weight: 700; color: var(--muted); }
.stat-num.active-num { color: var(--green); }
.stat-num.alert-num { color: var(--red); }
.stat-lbl { font-size: 0.7rem; color: var(--muted); text-transform: uppercase; letter-spacing: 0.05em; margin-top: 2px; }

/* Grid */
.grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(400px, 1fr)); gap: 1rem; }

/* Cards */
.card { background: var(--card); border: 1px solid var(--border); border-radius: 8px; overflow: hidden; }
.card-new { border-left: 3px solid var(--green); }
.card-updated { border-left: 3px solid var(--amber); }
.card-alert { border-left: 3px solid var(--red); }
.card-hdr { display: flex; align-items: center; gap: 0.5rem; padding: 0.75rem 1rem; cursor: pointer; user-select: none; }
.card-hdr:hover { background: rgba(255,255,255,0.03); }
.card-hdr > span:first-child { font-weight: 600; flex: 1; }
.card-count { font-size: 0.7rem; color: var(--muted); background: rgba(255,255,255,0.05); padding: 2px 6px; border-radius: 10px; }
.toggle-icon { font-size: 0.6rem; color: var(--muted); margin-left: 4px; }
.card-body { padding: 0.75rem 1rem; border-top: 1px solid var(--border); }
.card-body.collapsed { display: none; }

/* Sparkline */
.sparkline { display: inline-flex; align-items: flex-end; gap: 2px; height: 16px; }
.sp-bar { display: inline-block; width: 4px; background: var(--blue); border-radius: 1px; min-height: 2px; opacity: 0.6; }

/* Sub-headers */
.sub-hdr { font-size: 0.7rem; font-weight: 700; text-transform: uppercase; letter-spacing: 0.05em; padding: 0.25rem 0; margin-top: 0.5rem; margin-bottom: 0.25rem; }
.sub-new { color: var(--green); }
.sub-updated { color: var(--amber); }
.sub-removed { color: var(--red); }

/* Item rows */
.item-row { display: flex; justify-content: space-between; align-items: baseline; gap: 0.5rem; padding: 4px 0; border-bottom: 1px solid rgba(255,255,255,0.04); }
.item-row:last-child { border-bottom: none; }
.item-link { color: var(--blue); flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.item-meta { font-size: 0.7rem; color: var(--muted); white-space: nowrap; flex-shrink: 0; }
.no-change { color: var(--muted); font-size: 0.8rem; padding: 0.25rem 0; }

/* CCTL lab rows */
.lab-row { margin-bottom: 0.5rem; }
.lab-hdr { display: flex; align-items: center; gap: 0.5rem; padding: 4px 0; cursor: pointer; }
.lab-hdr:hover { color: var(--blue); }
.lab-name { font-weight: 600; flex: 1; }
.lab-cnt { font-size: 0.7rem; color: var(--muted); }
.lab-body { padding-left: 0.75rem; }
.lab-body.collapsed { display: none; }

/* CSfC CP detail */
.cp-row { padding: 4px 0; border-bottom: 1px solid rgba(255,255,255,0.04); }
.cp-row:last-child { border-bottom: none; }
.cp-detail { font-size: 0.7rem; color: var(--muted); margin-top: 2px; }
.cp-date { }

/* Alert item */
.alert-item .item-link { color: var(--red); }

/* Footer */
footer { margin-top: 2rem; padding: 1rem 0; border-top: 1px solid var(--border); display: flex; justify-content: space-between; font-size: 0.75rem; color: var(--muted); flex-wrap: wrap; gap: 0.5rem; }

/* Mobile */
@media (max-width: 600px) {
  .trend-bar { flex-direction: row; }
  .stat { min-width: 0; flex: 1 1 40%; }
  .stat-num { font-size: 1.2rem; }
  .grid { grid-template-columns: 1fr; }
}
</style>
</head>
<body>

{% if diff.alerts %}
<div class="alert-banner">Warning: {{ alert_total }} alert{% if alert_total != 1 %}s{% endif %} detected -- see Alerts section below.</div>
{% endif %}

<header class="site-header">
  <div class="site-title">CC Pulse</div>
  <div class="site-meta">{{ period_start }} to {{ period_end }} &bull; Generated {{ generated_at }}</div>
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
  <div class="stat"><a href="#sec-niap-pp">
    <div class="stat-num {% if alert_total > 0 %}alert-num{% endif %}">{{ alert_total }}</div>
    <div class="stat-lbl">Alerts</div>
  </a></div>
</div>

<div class="grid">

<!-- NIAP PPs -->
<div class="card {% if niap_pp_total > 0 %}card-new{% endif %}" id="sec-niap-pp">
  <div class="card-hdr" onclick="toggleCard(this)">
    <span>NIAP Protection Profiles</span>
    <span class="card-count">{{ niap_pp_total }} change{% if niap_pp_total != 1 %}s{% endif %}</span>
    <span class="sparkline">
      {% for v in sparklines.niap_pp %}
      <span class="sp-bar" style="height:{{ v }}%" title="{{ v }}%"></span>
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
    {% if niap_pp_total == 0 %}<p class="no-change">No changes detected.</p>{% endif %}
  </div>
</div>

<!-- NIAP TDs -->
<div class="card {% if niap_td_total > 0 %}card-new{% endif %}" id="sec-niap-td">
  <div class="card-hdr" onclick="toggleCard(this)">
    <span>NIAP Technical Decisions</span>
    <span class="card-count">{{ niap_td_total }} change{% if niap_td_total != 1 %}s{% endif %}</span>
    <span class="sparkline">
      {% for v in sparklines.niap_td %}
      <span class="sp-bar" style="height:{{ v }}%" title="{{ v }}%"></span>
      {% endfor %}
    </span>
    <span class="toggle-icon">{% if niap_td_total == 0 %}&#9658;{% else %}&#9660;{% endif %}</span>
  </div>
  <div class="card-body {% if niap_td_total == 0 %}collapsed{% endif %}">
    {% if diff.niap.tds.added %}
    <div class="sub-hdr sub-new">New TDs ({{ diff.niap.tds.added | length }})</div>
    {% for td in diff.niap.tds.added %}
    <div class="item-row">
      <span class="item-link">{{ td.identifier }}</span>
      <span class="item-meta">{{ td.pp_short_name }} &middot; {{ td.status }}</span>
    </div>
    {% endfor %}
    {% endif %}
    {% if diff.niap.tds.removed %}
    <div class="sub-hdr sub-removed">Removed TDs ({{ diff.niap.tds.removed | length }})</div>
    {% for td in diff.niap.tds.removed %}
    <div class="item-row">
      <span class="item-link">{{ td.identifier }}</span>
      <span class="item-meta">{{ td.pp_short_name }} &middot; {{ td.status }}</span>
    </div>
    {% endfor %}
    {% endif %}
    {% if niap_td_total == 0 %}<p class="no-change">No changes detected.</p>{% endif %}
  </div>
</div>

<!-- Cisco NDcPP -->
<div class="card {% if cisco_total > 0 %}card-new{% endif %}" id="sec-cisco">
  <div class="card-hdr" onclick="toggleCard(this)">
    <span>Cisco NDcPP Evaluations</span>
    <span class="card-count">{{ cisco_total }} change{% if cisco_total != 1 %}s{% endif %}</span>
    <span class="toggle-icon">{% if cisco_total == 0 %}&#9658;{% else %}&#9660;{% endif %}</span>
  </div>
  <div class="card-body {% if cisco_total == 0 %}collapsed{% endif %}">
    {% if diff.niap.cisco_ndcpp.added %}
    <div class="sub-hdr sub-new">New ({{ diff.niap.cisco_ndcpp.added | length }})</div>
    {% for item in diff.niap.cisco_ndcpp.added %}
    <div class="item-row">
      <span class="item-link">{{ item.vendor }} - {{ item.product }}</span>
      <span class="item-meta">{{ item.status }} &middot; {{ item.comp_date }}</span>
    </div>
    {% endfor %}
    {% endif %}
    {% if diff.niap.cisco_ndcpp.removed %}
    <div class="sub-hdr sub-removed">Removed ({{ diff.niap.cisco_ndcpp.removed | length }})</div>
    {% for item in diff.niap.cisco_ndcpp.removed %}
    <div class="item-row">
      <span class="item-link">{{ item.vendor }} - {{ item.product }}</span>
      <span class="item-meta">{{ item.status }}</span>
    </div>
    {% endfor %}
    {% endif %}
    {% if diff.niap.cisco_ndcpp.newly_archived %}
    <div class="sub-hdr sub-updated">Newly Archived ({{ diff.niap.cisco_ndcpp.newly_archived | length }})</div>
    {% for item in diff.niap.cisco_ndcpp.newly_archived %}
    <div class="item-row">
      <span class="item-link">{{ item.vendor }} - {{ item.product }}</span>
      <span class="item-meta">{{ item.status }}</span>
    </div>
    {% endfor %}
    {% endif %}
    {% if cisco_total == 0 %}<p class="no-change">No changes detected.</p>{% endif %}
  </div>
</div>

<!-- NIAP News -->
<div class="card {% if niap_news_total > 0 %}card-new{% endif %}" id="sec-niap-news">
  <div class="card-hdr" onclick="toggleCard(this)">
    <span>NIAP News</span>
    <span class="card-count">{{ niap_news_total }} new item{% if niap_news_total != 1 %}s{% endif %}</span>
    <span class="sparkline">
      {% for v in sparklines.niap_news %}
      <span class="sp-bar" style="height:{{ v }}%" title="{{ v }}%"></span>
      {% endfor %}
    </span>
    <span class="toggle-icon">{% if niap_news_total == 0 %}&#9658;{% else %}&#9660;{% endif %}</span>
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
    {% if niap_news_total == 0 %}<p class="no-change">No new items.</p>{% endif %}
  </div>
</div>

<!-- CCTL Lab Intel -->
<div class="card {% if cctl_total > 0 %}card-new{% endif %}" id="sec-cctl">
  <div class="card-hdr" onclick="toggleCard(this)">
    <span>CCTL Lab Intel</span>
    <span class="card-count">{{ cctl_total }} new item{% if cctl_total != 1 %}s{% endif %}</span>
    <span class="sparkline">
      {% for v in sparklines.cctl %}
      <span class="sp-bar" style="height:{{ v }}%" title="{{ v }}%"></span>
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
    {% if cctl_total == 0 %}<p class="no-change">No new items.</p>{% endif %}
  </div>
</div>

<!-- CSfC Capability Packages -->
<div class="card {% if csfc_total > 0 %}card-updated{% endif %}" id="sec-csfc">
  <div class="card-hdr" onclick="toggleCard(this)">
    <span>CSfC Capability Packages</span>
    <span class="card-count">{{ csfc_total }} update{% if csfc_total != 1 %}s{% endif %}</span>
    <span class="sparkline">
      {% for v in sparklines.csfc %}
      <span class="sp-bar" style="height:{{ v }}%" title="{{ v }}%"></span>
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
    {% if csfc_total == 0 %}<p class="no-change">No changes detected.</p>{% endif %}
  </div>
</div>

<!-- CC Crypto Docs -->
<div class="card {% if cc_crypto_total > 0 %}card-updated{% endif %}" id="sec-cc-crypto">
  <div class="card-hdr" onclick="toggleCard(this)">
    <span>CC Crypto Documentation</span>
    <span class="card-count">{{ cc_crypto_total }} update{% if cc_crypto_total != 1 %}s{% endif %}</span>
    <span class="sparkline">
      {% for v in sparklines.cc_crypto %}
      <span class="sp-bar" style="height:{{ v }}%" title="{{ v }}%"></span>
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
    {% if cc_crypto_total == 0 %}<p class="no-change">No changes detected.</p>{% endif %}
  </div>
</div>

<!-- NIST Docs -->
<div class="card {% if nist_total > 0 %}card-updated{% endif %}" id="sec-nist">
  <div class="card-hdr" onclick="toggleCard(this)">
    <span>NIST Documentation</span>
    <span class="card-count">{{ nist_total }} update{% if nist_total != 1 %}s{% endif %}</span>
    <span class="sparkline">
      {% for v in sparklines.nist %}
      <span class="sp-bar" style="height:{{ v }}%" title="{{ v }}%"></span>
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
    {% if nist_total == 0 %}<p class="no-change">No changes detected.</p>{% endif %}
  </div>
</div>

<!-- Alerts -->
{% if diff.alerts %}
<div class="card card-alert" id="sec-alerts">
  <div class="card-hdr" onclick="toggleCard(this)">
    <span>Alerts</span>
    <span class="card-count">{{ alert_total }} alert{% if alert_total != 1 %}s{% endif %}</span>
    <span class="toggle-icon">&#9660;</span>
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

</div>

<footer>
  <span>CC Pulse &middot; Auto-refreshes hourly &middot; Data from NIAP, CSfC, NIST, CC Portal</span>
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
            f"<item><title>{xml_escape('ALERT: ' + alert.get('type', ''))}</title>"
            f"<link>https://kr15tyk.github.io/CC-pulse/cc_dashboard.html</link>"
            f"<description>{xml_escape(alert.get('message', ''))}</description></item>"
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

    niap_total_stat = niap_pp_total + niap_td_total + cisco_total + niap_news_total
    cctl_total_stat = cctl_total
    csfc_total_stat = csfc_total
    nist_total_stat = nist_total + cc_crypto_total

    # Sparkline data
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
        period_start    = diff.get("period_start", ""),
        period_end      = diff.get("period_end", ""),
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

