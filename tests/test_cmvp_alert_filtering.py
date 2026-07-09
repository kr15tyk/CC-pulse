"""Regression tests for CMVP MIP alert filtering and tier labeling.

The 2026-07-09 alert email reported 22 alerts for 11 events: every CMVP MIP
status change fired twice (once via the raw page-text scan, once via the
structured differ), and the page-text copies were mislabeled "Cisco relevant"
because every MIP row contains "FIPS 140-3", which sat in
_CISCO_RELEVANT_KEYWORDS. Policy now: MIP alerts fire for Cisco modules only
(the dashboard still shows all vendors), and broad standards identifiers
tier as 2 (standards/NIST), not 1 (Cisco).
"""
import sys

# This file sorts alphabetically before test_differ.py, so it may be the
# first to import differ/emailer. Configure the shared config stub (installed
# by conftest.py) with the same values test_differ.py's stub uses BEFORE the
# import — otherwise differ binds a bare MagicMock whose default __iter__ is
# empty, silently disabling every keyword check for the rest of the session.
_cfg = sys.modules["config"]
for _name, _value in {
    "WATCH_KEYWORDS": ["NDcPP", "CSfC", "FIPS 140-3", "TLS 1.3"],
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

import differ   # noqa: E402 — must come after config stub
import emailer  # noqa: E402


def _diff_with(cmvp_mip=None, nist_pages=None):
    return {
        "nist": {
            "cmvp_mip": cmvp_mip or {"added": [], "removed": [], "status_changes": []},
            "pages": nist_pages or {},
            "feeds": {},
        },
    }


CISCO_MIP = {
    "Module Name": "CiscoSSL FIPS Provider",
    "Vendor": "Cisco Systems Inc",
    "Status": "Comment Resolution - CMVP",
    "old_status": "Review",
    "new_status": "Comment Resolution - CMVP",
}

OTHER_MIP = {
    "Module Name": "TASS Crypto Engine",
    "Vendor": "Beijing JN TASS Technology Co., Ltd.",
    "Status": "Pending Review",
    "old_status": "Pending Resubmission",
    "new_status": "Pending Review",
}


def test_non_cisco_mip_changes_do_not_alert():
    diff = _diff_with(cmvp_mip={
        "added": [OTHER_MIP], "removed": [], "status_changes": [OTHER_MIP],
    })
    alerts = differ.flag_alerts(diff)
    assert not [a for a in alerts if a["source"] == "NIST CMVP MIP"]


def test_cisco_mip_status_change_alerts_as_tier1():
    diff = _diff_with(cmvp_mip={
        "added": [], "removed": [], "status_changes": [CISCO_MIP],
    })
    alerts = [
        a for a in differ.flag_alerts(diff)
        if a["source"] == "NIST CMVP MIP"
    ]
    assert len(alerts) == 1
    a = alerts[0]
    assert "CiscoSSL FIPS Provider" in a["title"]
    assert "Comment Resolution - CMVP" in a["detail"]
    assert emailer._alert_tier(a) == 1  # "cisco" keyword → Cisco-relevant


def test_cmvp_mip_page_text_changes_do_not_double_alert():
    """Row-text churn on the MIP page must not produce 'NIST: cmvp_mip'
    publication alerts — the structured differ owns that page."""
    diff = _diff_with(nist_pages={
        "cmvp_mip": {"added": [
            {"text": "CiscoSSL FIPS Provider | Cisco Systems Inc | FIPS 140-3 | Review"},
            {"text": "TASS Crypto Engine | Beijing JN TASS | FIPS 140-3 | Pending Review"},
        ]},
    })
    alerts = differ.flag_alerts(diff)
    assert not [a for a in alerts if a["source"] == "NIST: cmvp_mip"]


def test_fips_140_3_alone_is_tier2_not_cisco():
    alert = {"matched_keywords": ["FIPS 140-3"]}
    assert emailer._alert_tier(alert) == 2


def test_cisco_keyword_is_tier1():
    alert = {"matched_keywords": ["cisco"]}
    assert emailer._alert_tier(alert) == 1
