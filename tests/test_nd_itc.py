"""Tests for the ND-iTC source: NIT RFI table / Allowed-With list parsing,
diffing (including bootstrap and baseline-reset behaviour), and alerts.

Naming: the ND-iTC's Technical Decisions are "NIT RFIs" in CC Pulse to
distinguish them from NIAP TDs.
"""
import sys
from unittest.mock import MagicMock

# Configure the shared config stub before importing project modules (see
# test_alert_tiering.py for the module-cache rationale).
_cfg = sys.modules["config"]
_CONFIG_VALUES = {
    "WATCH_KEYWORDS": ["NDcPP", "CSfC", "FIPS 140-3"],
    "NEWS_CATEGORY_KEYWORDS": {
        "POLICY": ["policy"], "PUBLICATION": ["publication"], "NEWS": [],
    },
    "CISCO_VENDOR_KEYWORDS": ["cisco"],
    "NDCPP_PP_KEYWORDS": ["CPP_ND"],
    "CSFC_PRODUCT_LIST_URL": "https://nsa.gov/csfc",
    "NATO_NIAPCL_URL": "https://nato.int/niapcl",
    "EUCC_REQUIREMENTS_URL": "https://enisa.eu/eucc",
    "EUCC_CERTIFICATES_URL": "https://enisa.eu/certs",
    "ND_ITC_BASE": "https://nd-itc.github.io",
    "ND_ITC_PAGES": {
        "nit_rfis": "/TD/tech_dec.html",
        "nit_rfis_archived": "/TD/tech_dec_arch.html",
        "awl_ndcpp": "/AWL/NDcPP_allowed_with_list.html",
        "awl_fw": "/AWL/FW_allowed_with_list.html",
    },
    "ND_ITC_TD_URL": "https://nd-itc.github.io/TD/tech_dec.html",
    "ND_ITC_AWL_URLS": {
        "awl_ndcpp": "https://nd-itc.github.io/AWL/NDcPP_allowed_with_list.html",
        "awl_fw": "https://nd-itc.github.io/AWL/FW_allowed_with_list.html",
    },
    "SANITY_MIN_ND_ITC_RFIS": 5,
}
for _name, _value in _CONFIG_VALUES.items():
    setattr(_cfg, _name, _value)

import differ   # noqa: E402
import emailer  # noqa: E402

# The conftest stubs bs4 with a MagicMock before anything imports it. The
# parsers only *receive* soup objects, so tests can hand them a real one:
# restore the real package here (installed via requirements).
if isinstance(sys.modules.get("bs4"), MagicMock):
    del sys.modules["bs4"]
from bs4 import BeautifulSoup  # noqa: E402

import collector  # noqa: E402

# Other test modules (test_differ, test_collector) install their own config
# stubs; whichever imported a project module first wins its binding. Set our
# values on every bound config object so these tests pass in any run order.
for _mod in (differ, emailer, collector):
    for _name, _value in _CONFIG_VALUES.items():
        setattr(_mod.config, _name, _value)


# ── Fixtures (Asciidoctor-shaped HTML) ───────────────────────────────────────

RFI_TABLE_HTML = """
<html><body><div class="sect1"><h2>Active Technical Decisions</h2>
<div class="sectionbody"><table class="tableblock">
<tr><th>ID</th><th>Title</th><th>Reference</th><th>Publication Date</th>
<th>Impact</th><th>Status</th></tr>
<tr><td><p>RFI#202605</p></td>
<td><p><a href="/TD/2026/NITDecisionRfI202605.pdf">FCS_CKM_EXT.7 Application Note</a></p></td>
<td><p>FCS_CKM_EXT.7 App Note 14</p></td><td><p>2026-03-17</p></td>
<td><p>NDcPPv4.0, NDSDv4.0</p></td><td><p>Published</p></td></tr>
<tr><td><p>RFI#202601</p></td>
<td><p><a href="https://nd-itc.github.io/TD/2026/NITDecisionRfI202601.pdf">FIPS PUB 186-4 in NDcPPv4.0</a></p></td>
<td><p>FCS_CKM.1/AKG</p></td><td><p>2026-02-26</p></td>
<td><p>NDcPPv4.0</p></td><td><p>Published</p></td></tr>
</table></div></div></body></html>
"""

AWL_HTML = """
<html><body>
<table><tr><td><strong>Allowed-with list version</strong></td><td>4.0r1</td></tr>
<tr><td><strong>Date</strong></td><td>30 March 2026</td></tr></table>
<div class="sect1"><h2 id="_ndcpp_v4_0">NDcPP v4.0</h2><div class="sectionbody">
<p>Object ID: PP-Module for Stateful Traffic Filter Firewalls
Object version: 2.0 (30 March 2026)
Owner: ND-iTC (ndfw-itc@ccdbinfo.org)
Notes: None</p>
<p>2. Object ID: PP-Module for LiFi Access Systems
Object version: 1.0 (24 February 2026)
Owner: NIAP (niap@niap-ccevs.org)
Notes: None</p>
</div></div>
<div class="sect1"><h2 id="_ndcpp_v3_0e">NDcPP v3.0e</h2><div class="sectionbody">
<p>Object ID: PP-Module for Virtual Private Network (VPN) Gateways
Object version: 1.3 (10 August 2023)
Owner: NIAP (niap@niap-ccevs.org)
Notes: None</p>
</div></div>
</body></html>
"""


def _soup(html):
    return BeautifulSoup(html, "html.parser")


# ── Parsers ──────────────────────────────────────────────────────────────────

def test_rfi_table_parse():
    records = collector._parse_nd_itc_rfi_table(_soup(RFI_TABLE_HTML))
    assert len(records) == 2
    r = records[0]
    assert r["rfi_id"] == "RFI#202605"
    assert r["title"] == "FCS_CKM_EXT.7 Application Note"
    assert r["href"] == "https://nd-itc.github.io/TD/2026/NITDecisionRfI202605.pdf"
    assert r["status"] == "Published"
    assert r["impact"] == "NDcPPv4.0, NDSDv4.0"
    # absolute URLs pass through untouched
    assert records[1]["href"].startswith("https://nd-itc.github.io/TD/2026/")


def test_rfi_table_parse_survives_column_reorder():
    reordered = RFI_TABLE_HTML.replace(
        "<th>ID</th><th>Title</th><th>Reference</th><th>Publication Date</th>\n<th>Impact</th><th>Status</th>",
        "<th>Status</th><th>ID</th><th>Title</th><th>Reference</th><th>Publication Date</th>\n<th>Impact</th>",
    ).replace(
        '<tr><td><p>RFI#202605</p></td>',
        '<tr><td><p>Published</p></td><td><p>RFI#202605</p></td>',
    ).replace(
        '<td><p>NDcPPv4.0, NDSDv4.0</p></td><td><p>Published</p></td></tr>',
        '<td><p>NDcPPv4.0, NDSDv4.0</p></td></tr>',
    )
    records = collector._parse_nd_itc_rfi_table(_soup(reordered))
    assert records[0]["rfi_id"] == "RFI#202605"
    assert records[0]["status"] == "Published"


def test_awl_parse():
    parsed = collector._parse_nd_itc_awl(_soup(AWL_HTML), "awl_ndcpp")
    assert parsed["awl_version"] == "4.0r1"
    assert parsed["awl_date"] == "30 March 2026"
    entries = parsed["entries"]
    assert len(entries) == 3
    by_key = {(e["section"], e["object_id"]): e for e in entries}
    fw = by_key[("NDcPP v4.0", "PP-Module for Stateful Traffic Filter Firewalls")]
    assert fw["object_version"].startswith("2.0")
    assert "ND-iTC" in fw["owner"]
    vpn = by_key[("NDcPP v3.0e", "PP-Module for Virtual Private Network (VPN) Gateways")]
    assert vpn["object_version"].startswith("1.3")
    assert all(e["list"] == "awl_ndcpp" for e in entries)


# ── Live-captured fixtures (verified against nd-itc.github.io 2026-07-10) ───
# The text below is the exact get_text(" ", strip=True)-equivalent flow of the
# live AWL pages, captured via a DOM text-node walk. If the regex parser ever
# breaks on these, the site format changed for real.

LIVE_NDCPP_V40_FLOW = (
    "cPP to which this list applies: collaborative Protection Profile for "
    "Network Devices Latest definitive cPP version: v4.0, 25 November 2025 "
    "Allowed PP-Modules: 1. Object ID: PP-Module for Stateful Traffic Filter "
    "Firewalls Object version: 2.0 (30 March 2026) Owner: ND-iTC "
    "( ndfw-itc@ccdbinfo.org ) Notes: None 2. Object ID: PP-Module for LiFi "
    "Access Systems Object version: 1.0 (24 February 2026) Owner: NIAP "
    "( niap@niap-ccevs.org ) Notes: None"
)

LIVE_FW_V20_FLOW = (
    "PP-Module to which this list applies : PP-Module for Stateful Traffic "
    "Filter Firewalls Allowed Packages: No objects of this type are currently "
    "allowed with the PP-Module. Allowed PP-Modules: 1. Object ID: PP-Module "
    "for LiFi Access Systems Object version: 1.0 (24 February 2026) Owner: "
    "NIAP ( niap@niap-ccevs.org ) Notes: None"
)


def test_awl_parse_live_captured_ndcpp_flow():
    html = (
        '<html><body><p>Allowed-with list version 4.0r1 Date 30 March 2026</p>'
        '<div class="sect1"><h2>NDcPP v4.0</h2><div class="sectionbody"><p>'
        + LIVE_NDCPP_V40_FLOW + '</p></div></div></body></html>'
    )
    parsed = collector._parse_nd_itc_awl(_soup(html), "awl_ndcpp")
    assert parsed["awl_version"] == "4.0r1"
    assert parsed["awl_date"] == "30 March 2026"
    entries = parsed["entries"]
    assert len(entries) == 2
    assert entries[0]["object_id"] == "PP-Module for Stateful Traffic Filter Firewalls"
    assert entries[0]["object_version"] == "2.0 (30 March 2026)"
    assert entries[1]["object_id"] == "PP-Module for LiFi Access Systems"


def test_awl_parse_live_captured_fw_flow_ignores_no_objects_noise():
    html = (
        '<html><body><p>Allowed-with list version 2.0 Date 30 March 2026</p>'
        '<div class="sect1"><h2>FW Module 2.0</h2><div class="sectionbody"><p>'
        + LIVE_FW_V20_FLOW + '</p></div></div></body></html>'
    )
    parsed = collector._parse_nd_itc_awl(_soup(html), "awl_fw")
    assert parsed["awl_version"] == "2.0"
    assert len(parsed["entries"]) == 1
    assert parsed["entries"][0]["object_id"] == "PP-Module for LiFi Access Systems"
    assert parsed["entries"][0]["section"] == "FW Module 2.0"


def test_rfi_parse_skips_empty_archive_row():
    """The live archived-TD page is a header plus one empty row."""
    html = RFI_TABLE_HTML.replace(
        "</table>", '<tr><td class="tableblock"></td></tr></table>')
    records = collector._parse_nd_itc_rfi_table(_soup(html))
    assert len(records) == 2  # empty row contributes nothing


# ── Diff ─────────────────────────────────────────────────────────────────────

def _rfi(rid, status="Published", **kw):
    return {"rfi_id": rid, "title": f"Title {rid}", "href": f"https://x/{rid}.pdf",
            "reference": "FCS_CKM.1", "publication_date": "2026-01-01",
            "impact": "NDcPPv4.0", "status": status, "archived": False, **kw}


def _awl_entry(oid, version="1.0", section="NDcPP v4.0", lst="awl_ndcpp"):
    return {"list": lst, "section": section, "object_id": oid,
            "object_version": version, "owner": "NIAP", "notes": "",
            "text": f"{section} | {oid} | {version}"}


def _nd(rfis=None, archived=None, awl=None, meta=None):
    return {"nit_rfis": rfis or [], "nit_rfis_archived": archived or [],
            "awl_entries": awl or [], "awl_meta": meta or []}


def test_bootstrap_reports_nothing():
    """First sight of the source must not fire 30 'new RFI' notifications."""
    new = _nd(rfis=[_rfi(f"RFI#{i}") for i in range(30)],
              awl=[_awl_entry("Mod A"), _awl_entry("Mod B")])
    d = differ.diff_nd_itc({}, new)
    assert d["nit_rfis"]["added"] == []
    assert d["awl"]["added"] == []
    assert differ.flag_alerts({"nd_itc": d}) == []


def test_new_rfi_and_status_change_detected():
    old = _nd(rfis=[_rfi("RFI#1"), _rfi("RFI#2", status="Draft")])
    new = _nd(rfis=[_rfi("RFI#1"), _rfi("RFI#2", status="Published"), _rfi("RFI#3")])
    d = differ.diff_nd_itc(old, new)
    assert [r["rfi_id"] for r in d["nit_rfis"]["added"]] == ["RFI#3"]
    assert d["nit_rfis"]["status_changes"][0]["old_status"] == "Draft"
    assert d["nit_rfis"]["status_changes"][0]["new_status"] == "Published"


def test_rfi_revision_detected():
    old = _nd(rfis=[_rfi("RFI#1")])
    new = _nd(rfis=[{**_rfi("RFI#1"), "href": "https://x/RFI#1v2.pdf"}])
    d = differ.diff_nd_itc(old, new)
    assert len(d["nit_rfis"]["revised"]) == 1
    assert d["nit_rfis"]["status_changes"] == []


def test_active_to_archived_transition():
    old = _nd(rfis=[_rfi("RFI#1"), _rfi("RFI#2")])
    new = _nd(rfis=[_rfi("RFI#2")],
              archived=[{**_rfi("RFI#1"), "archived": True}])
    d = differ.diff_nd_itc(old, new)
    assert [r["rfi_id"] for r in d["nit_rfis"]["newly_archived"]] == ["RFI#1"]


def test_awl_version_change_and_list_update():
    old = _nd(rfis=[_rfi("RFI#1")],
              awl=[_awl_entry("Mod FW", "1.4")],
              meta=[{"list": "awl_ndcpp", "awl_version": "4.0r1", "awl_date": ""}])
    new = _nd(rfis=[_rfi("RFI#1")],
              awl=[_awl_entry("Mod FW", "2.0")],
              meta=[{"list": "awl_ndcpp", "awl_version": "4.0r2", "awl_date": ""}])
    d = differ.diff_nd_itc(old, new)
    vc = d["awl"]["version_changes"]
    assert len(vc) == 1 and vc[0]["old_version"] == "1.4" and vc[0]["new_version"] == "2.0"
    lu = d["awl"]["list_updates"]
    assert len(lu) == 1 and lu[0]["old_awl_version"] == "4.0r1"


def test_empty_collection_is_failure_not_mass_removal():
    old = _nd(rfis=[_rfi(f"RFI#{i}") for i in range(10)], awl=[_awl_entry("M")])
    d = differ.diff_nd_itc(old, _nd())
    assert d["collection_failure"] is True
    assert differ.flag_alerts({"nd_itc": d}) == []


def test_mass_rekey_is_baseline_reset():
    old = _nd(rfis=[_rfi(f"RFI#A{i}") for i in range(10)])
    new = _nd(rfis=[_rfi(f"RFI#B{i}") for i in range(10)])
    d = differ.diff_nd_itc(old, new)
    assert d["baseline_reset"] is True
    assert differ.flag_alerts({"nd_itc": d}) == []


# ── Alerts ───────────────────────────────────────────────────────────────────

def test_alerts_fire_tier1_with_distinct_naming():
    old = _nd(rfis=[_rfi("RFI#1")], awl=[_awl_entry("Mod FW", "1.4")])
    new = _nd(rfis=[_rfi("RFI#1"), _rfi("RFI#2")],
              awl=[_awl_entry("Mod FW", "2.0"), _awl_entry("Mod LiFi")])
    alerts = differ.flag_alerts({"nd_itc": differ.diff_nd_itc(old, new)})
    sources = {a["source"] for a in alerts}
    assert sources == {"ND-iTC NIT RFI", "ND-iTC Allowed-With"}
    # never labeled as NIAP TDs
    assert not any("NIAP" in a["source"] for a in alerts)
    for a in alerts:
        assert emailer._alert_tier(a) == 1
    rfi_alert = next(a for a in alerts if a["source"] == "ND-iTC NIT RFI")
    assert rfi_alert["url"] == "https://x/RFI#2.pdf"
    awl_new = next(a for a in alerts if a["kind"] == "new"
                   and a["source"] == "ND-iTC Allowed-With")
    assert awl_new["url"].endswith("NDcPP_allowed_with_list.html")
