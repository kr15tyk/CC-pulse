"""Regression tests for URL-keyed EUCC/NATO diffs, baseline-reset
suppression, and the previously dead Cisco alert paths.

Background (2026-07-09/10 findings):
- diff_eucc keyed certificates on text[:80]; when ENISA changed its card
  title format, 45 old certificates re-detected as "new" and three stale
  Cisco certs got a celebration. Keys are now the record URL (href/link)
  with text as fallback, and mass re-detections are flagged baseline_reset.
- The NATO/EUCC Cisco alert blocks in flag_alerts used keyword-gated
  _add_text, but "cisco" only existed in the never-referenced
  BODY_WATCH_KEYWORDS list — so those "Tier 1" alerts never fired once.
  They are now unconditional with explicit keywords.
"""
import sys

# Configure the shared config stub (see test_cmvp_alert_filtering.py for why
# this must happen before importing differ/emailer).
_cfg = sys.modules["config"]
for _name, _value in {
    "WATCH_KEYWORDS": ["NDcPP", "CSfC", "FIPS 140-3", "TLS 1.3", "EUCC"],
    "NEWS_CATEGORY_KEYWORDS": {
        "POLICY": ["policy"], "PUBLICATION": ["publication"], "NEWS": [],
    },
    "CISCO_VENDOR_KEYWORDS": ["cisco"],
    "NDCPP_PP_KEYWORDS": ["CPP_ND"],
    "CSFC_PRODUCT_LIST_URL": "https://nsa.gov/csfc",
    "NATO_NIAPCL_URL": "https://nato.int/niapcl",
    "EUCC_REQUIREMENTS_URL": "https://enisa.eu/eucc",
    "EUCC_CERTIFICATES_URL": "https://enisa.eu/certs",
}.items():
    setattr(_cfg, _name, _value)

import differ   # noqa: E402
import emailer  # noqa: E402


def _eucc_cert(href, text):
    return {"name": text[:40], "text": text, "href": href,
            "cert_date": "2026-01-01", "description": text}


def _nato_product(link, raw):
    return {"name": raw[:40], "manufacturer": "Cisco Systems, Inc.",
            "category": "Routers", "link": link, "raw_text": raw}


# ── URL keying: display-format changes are not "new" ────────────────────────

def test_eucc_text_change_same_href_is_not_new():
    old = {"cisco_certs": [_eucc_cert("https://enisa.eu/c/1", "EUCC-3110-01 | old title")],
           "pages": {}}
    new = {"cisco_certs": [_eucc_cert("https://enisa.eu/c/1", "Cisco ASR 9000 | new title format")],
           "pages": {}}
    d = differ.diff_eucc(old, new)
    assert d["cisco_added"] == []
    assert d["cisco_removed"] == []


def test_nato_text_change_same_link_is_not_new():
    old = {"cisco_products": [_nato_product("https://nato.int/p/1", "old row text")],
           "pages": {}}
    new = {"cisco_products": [_nato_product("https://nato.int/p/1", "reformatted row text")],
           "pages": {}}
    d = differ.diff_nato(old, new)
    assert d["cisco_added"] == []


def test_eucc_genuinely_new_href_is_added():
    old = {"cisco_certs": [_eucc_cert("https://enisa.eu/c/1", "Cisco ASR 9000")],
           "pages": {}}
    new = {"cisco_certs": [_eucc_cert("https://enisa.eu/c/1", "Cisco ASR 9000"),
                           _eucc_cert("https://enisa.eu/c/2", "Cisco Nexus 9000")],
           "pages": {}}
    d = differ.diff_eucc(old, new)
    assert len(d["cisco_added"]) == 1
    assert d["cisco_added"][0]["href"] == "https://enisa.eu/c/2"
    assert d["baseline_reset"] is False


def test_record_key_falls_back_to_text_when_no_url():
    rec = {"href": "", "text": "Some certificate without a link"}
    assert differ._record_key(rec, url_field="href", text_field="text") == \
        "Some certificate without a link"


# ── Baseline reset detection & suppression ───────────────────────────────────

def _mass_rekeyed_eucc():
    """Certificates page where every item re-registers as new (format change:
    old items had no hrefs captured, new ones do — keys don't overlap)."""
    old_page = [{"text": f"EUCC-{i} | 2025 | desc", "href": f"https://enisa.eu/old/{i}"}
                for i in range(20)]
    new_page = [{"text": f"Product {i} | 2025 | desc", "href": f"https://enisa.eu/new/{i}"}
                for i in range(20)]
    old = {"pages": {"certificates": old_page},
           "cisco_certs": [_eucc_cert("https://enisa.eu/old/3", "EUCC-3 | 2025")]}
    new = {"pages": {"certificates": new_page},
           "cisco_certs": [_eucc_cert("https://enisa.eu/new/3", "Cisco Thing | 2025")]}
    return old, new


def test_eucc_mass_rekey_sets_baseline_reset():
    old, new = _mass_rekeyed_eucc()
    d = differ.diff_eucc(old, new)
    assert d["baseline_reset"] is True
    assert len(d["cisco_added"]) == 1  # data kept for the dashboard


def test_baseline_reset_suppresses_eucc_cisco_alerts():
    old, new = _mass_rekeyed_eucc()
    diff = {"eucc": differ.diff_eucc(old, new)}
    alerts = differ.flag_alerts(diff)
    assert not [a for a in alerts if a["source"] == "EUCC Certificates"]


def test_nato_mass_rekey_sets_baseline_reset():
    old = {"cisco_products": [_nato_product(f"https://nato.int/old/{i}", f"row {i}")
                              for i in range(8)], "pages": {}}
    new = {"cisco_products": [_nato_product(f"https://nato.int/new/{i}", f"row {i}")
                              for i in range(8)], "pages": {}}
    d = differ.diff_nato(old, new)
    assert d["baseline_reset"] is True


def test_small_addition_is_not_baseline_reset():
    assert differ._is_baseline_reset(old_count=45, added_count=3, new_count=48) is False
    assert differ._is_baseline_reset(old_count=0, added_count=45, new_count=45) is False  # bootstrap


# ── Previously dead Cisco alert paths now fire, tiered correctly ─────────────

def test_eucc_cisco_added_fires_tier1_alert():
    diff = {"eucc": {
        "pages": {}, "baseline_reset": False,
        "cisco_added": [_eucc_cert("https://enisa.eu/c/9", "Cisco ASR 9000 Series")],
        "cisco_removed": [],
    }}
    alerts = [a for a in differ.flag_alerts(diff) if a["source"] == "EUCC Certificates"]
    assert len(alerts) == 1
    assert alerts[0]["url"] == "https://enisa.eu/c/9"
    assert emailer._alert_tier(alerts[0]) == 1


def test_nato_cisco_added_fires_tier1_alert():
    diff = {"nato": {
        "pages": {}, "baseline_reset": False,
        "cisco_added": [_nato_product("https://nato.int/p/9", "Cisco Catalyst 8000")],
        "cisco_removed": [],
    }}
    alerts = [a for a in differ.flag_alerts(diff) if a["source"] == "NATO NIAPCL"]
    assert len(alerts) == 1
    assert alerts[0]["url"] == "https://nato.int/p/9"
    assert emailer._alert_tier(alerts[0]) == 1


def test_cisco_cert_not_double_alerted_via_page_loop():
    """A Cisco cert on the certificates page must alert once (Cisco path),
    not again via the general page-added loop."""
    cert = _eucc_cert("https://enisa.eu/c/9", "Cisco ASR 9000 Series EUCC cert")
    diff = {"eucc": {
        "pages": {"certificates": {"added": [cert]}},
        "baseline_reset": False,
        "cisco_added": [cert],
        "cisco_removed": [],
    }}
    alerts = [a for a in differ.flag_alerts(diff) if a["source"] == "EUCC Certificates"]
    assert len(alerts) == 1
