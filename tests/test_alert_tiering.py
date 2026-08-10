"""Regression tests for alert tier labeling (emailer._alert_tier).

Policy (from the 2026-07-09 double-alert incident): broad standards
identifiers such as "FIPS 140-3" tier as 2 (standards), not 1 (Cisco);
only genuine Cisco keyword matches are tier 1.

The CMVP MIP filtering tests that used to live here were removed along
with the NIST/CMVP domain itself.
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

import emailer  # noqa: E402 — must come after config stub


def test_fips_140_3_alone_is_tier2_not_cisco():
    alert = {"matched_keywords": ["FIPS 140-3"]}
    assert emailer._alert_tier(alert) == 2


def test_cisco_keyword_is_tier1():
    alert = {"matched_keywords": ["cisco"]}
    assert emailer._alert_tier(alert) == 1
