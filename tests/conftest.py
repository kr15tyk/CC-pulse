import sys
from unittest.mock import MagicMock

# ---------------------------------------------------------------------------
# Stub out heavy third-party imports so tests run without installing every
# dependency.  conftest.py is loaded by pytest before any test module, so
# these stubs are in place before any `import differ` / `import collector`
# statement executes.
# ---------------------------------------------------------------------------
for _mod in (
    "config",
    "requests",
    "feedparser",
    "bs4",
    "bs4.BeautifulSoup",
):
    sys.modules.setdefault(_mod, MagicMock())

# ---------------------------------------------------------------------------
# Shared fixtures used across test_differ.py, test_collector.py,
# test_main.py so every test file can just `from conftest import *` or rely
# on pytest's automatic fixture injection.
# ---------------------------------------------------------------------------
import pytest


# -- Minimal snapshot factory ------------------------------------------------

def _pcl_product(product_id, vendor, status="Certified", pp_short="CPP_ND_v2.2e"):
    return {
        "product_id": product_id,
        "product_name": f"Product {product_id}",
        "vendor_id_name": vendor,
        "status_sort": status,
        "certification_date": "2024-01-01",
        "sunset_date": "2027-01-01",
        "assigned_lab_name": "Test Lab",
        "submitting_country_id_name": "USA",
        "protection_profiles": [{"pp_short_name": pp_short}],
    }


def _make_snapshot(
    pcl=None, pps=None, tds=None, news=None,
    nato_products=None, eucc_certs=None,
):
    """Return a minimal but structurally complete CC Pulse snapshot dict."""
    return {
        "schema_version": 2,
        "collected_at": "2024-06-01T06:00:00+00:00",
        "niap": {
            "pcl": pcl or [],
            "pps": pps or [],
            "tds": tds or [],
            "events": [],
            "news": news or [],
            "policies": [],
        },
        "cc_portal": {"news": [], "pps": [], "products": [], "communities": [], "publications": [], "pp_rss": []},
        "cctl_labs": {},
        "csfc": {"pages": {}, "component_selection_hashes": {}, "documents": {}, "feeds": {}},
        "cc_crypto": {"pages": {}, "doc_headers": {}},
        "nist": {"pages": {}, "doc_headers": {}, "feeds": {}},
        "nato": {"pages": {}, "cisco_products": nato_products or []},
        "eucc": {"pages": {}, "cisco_certs": eucc_certs or []},
        "ietf_cnsa": {"documents": []},
        "ieee_pqc": {"projects": []},
    }


@pytest.fixture
def empty_snapshot():
    return _make_snapshot()


@pytest.fixture
def base_snapshot():
    """A snapshot with one certified Cisco NDcPP product."""
    return _make_snapshot(
        pcl=[_pcl_product(1, "Cisco Systems", "Certified")],
        pps=[{"pp_id": "101", "pp_short_name": "CPP_ND_v2.2e",
              "pp_name": "NDcPP v2.2e", "sunset_date": "2026-01-01", "status": "Active"}],
        tds=[{"td_id": "TD0123", "identifier": "TD0123",
              "title": "TLS 1.3 clarification", "removed_on": None}],
        news=[{"id": "n1", "title": "NIAP Policy Update", "url": "https://niap.example.com/n1"}],
    )


@pytest.fixture
def pcl_product_factory():
    return _pcl_product
