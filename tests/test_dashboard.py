"""Regression tests for dashboard history and source-health rendering inputs."""

import json

import dashboard


def test_recent_diffs_use_configured_diff_directory(tmp_path):
    old_dir = dashboard.config.DIFF_DIR
    dashboard.config.DIFF_DIR = str(tmp_path)
    try:
        payload = {"period_end": "2026-07-02T06:00:00+00:00"}
        (tmp_path / "2026-07-02_diff.json").write_text(json.dumps(payload))

        assert dashboard._load_recent_diffs() == [payload]
    finally:
        dashboard.config.DIFF_DIR = old_dir


def test_history_uses_configured_diff_directory(tmp_path):
    old_dir = dashboard.config.DIFF_DIR
    dashboard.config.DIFF_DIR = str(tmp_path)
    try:
        payload = {
            "period_end": "2026-07-02T06:00:00+00:00",
            "niap": {
                "cisco_ndcpp": {"added": []},
                "pps": {"added": [], "removed": []},
                "tds": {"added": []},
                "pcl_all": {
                    "added": [{
                        "product_id": 123,
                        "product_name": "Current product",
                        "vendor_id_name": "Example Vendor",
                        "certification_date": "2026-07-02",
                    }],
                },
                "in_evaluation": {"added": [], "removed": []},
            },
            "nato": {"cisco_added": []},
            "eucc": {"cisco_added": []},
            "csfc": {"selection_links": {}},
            "nist": {"pages": {}},
        }
        (tmp_path / "2026-07-02_diff.json").write_text(json.dumps(payload))

        history = dashboard._build_history()

        assert any(
            item["title"] == "Example Vendor — Current product"
            for item in history
        )
        assert all(item["date"] == "2026-07-02" for item in history)
    finally:
        dashboard.config.DIFF_DIR = old_dir


def test_csfc_counts_include_components_announcements_and_selections():
    diff = {
        "csfc": {
            "selection_links": {"VPN": {"changed": True}},
            "pages": {
                "apl": {"added": [{"text": "Cisco component"}], "removed": []},
                "announcements": {
                    "added": [{"text": "New announcement"}],
                    "removed": [{"text": "Old announcement"}],
                },
            },
        },
    }

    assert dashboard._section_daily_counts([diff], "csfc") == [4]


def test_rss_includes_csfc_component_and_announcement_changes():
    diff = {
        "niap": {"pps": {}, "tds": {}, "news": {}},
        "cctl_labs": {},
        "csfc": {
            "selection_links": {},
            "pages": {
                "apl": {
                    "added": [{"text": "Cisco Secure Firewall", "href": "https://example.test/component"}],
                    "removed": [],
                },
                "announcements": {
                    "added": [{"text": "5/20/25 | New guidance", "href": "https://example.test/announcement"}],
                    "removed": [],
                },
            },
        },
        "nist": {"pages": {}},
        "cc_crypto": {"pages": {}},
        "alerts": [{
            "source": "CSfC Component Selections",
            "title": "IPsec VPN Gateway",
            "kind": "updated",
            "matched_keywords": ["CSfC"],
        }],
    }

    rss = dashboard._build_rss(diff, "2026-07-02 13:00 ET")

    assert "CSfC Component Added: Cisco Secure Firewall" in rss
    assert "CSfC Announcement Added: 5/20/25 | New guidance" in rss
    assert "ALERT: CSfC Component Selections – IPsec VPN Gateway" in rss
    assert "â" not in rss


def test_dashboard_renders_revised_announcement_and_policy(tmp_path):
    dashboard.config.DIFF_DIR = str(tmp_path / "diffs")
    dashboard.config.STAGING_DIR = "docs/staging"
    dashboard.config.WATCH_KEYWORDS = []
    dashboard.config.NATO_NIAPCL_URL = "https://example.test/nato"
    dashboard.config.EUCC_REQUIREMENTS_URL = "https://example.test/eucc"
    dashboard.config.EUCC_CERTIFICATES_URL = "https://example.test/eucc-certs"
    dashboard.config.CSFC_PRODUCT_LIST_URL = "https://example.test/csfc"
    dashboard.config.CSFC_BASE = "https://example.test"
    dashboard.config.CSFC_PAGES = {"announcements": "/announcements"}
    diff = {
        "period_start": "2026-07-01T00:00:00Z",
        "period_end": "2026-07-02T00:00:00Z",
        "niap": {
            "pps": {}, "tds": {}, "cisco_ndcpp": {}, "pcl_all": {},
            "in_evaluation": {},
            "news": {
                "added": [],
                "revised": [{
                    "id": 1, "title": "Revised NIAP announcement",
                    "posted": "2026-07-02T00:00:00Z",
                }],
                "deactivated": [], "reactivated": [], "removed": [],
            },
            "events": {key: [] for key in ("added", "revised", "deactivated", "reactivated", "removed")},
            "policies": {
                "added": [{
                    "policy_id": 30, "policy_num": 30,
                    "policy_title": "Software Bill of Materials",
                    "policy_date": "2026-07-02",
                    "url": "https://example.test/policy-30.pdf",
                }],
                "revised": [], "archived": [], "reactivated": [], "removed": [],
            },
        },
        "cc_portal": {
            "news": {"added": []}, "pps": {"added": []},
            "products": {"added": []},
        },
        "cctl_labs": {},
        "csfc": {
            "pages": {},
            "selection_links": {
                "IPsec VPN Gateway": {
                    "changed": True,
                    "old_href": "",
                    "new_href": "https://example.test/ipsec-selection.pdf",
                },
            },
            "feeds": {},
        },
        "cc_crypto": {"pages": {}}, "nist": {"pages": {}},
        "nato": {"pages": {}, "cisco_added": [], "cisco_removed": []},
        "eucc": {"pages": {}, "cisco_added": [], "cisco_removed": []},
        "alerts": [
            {
                "source": "CCTL Labs", "kind": "alert",
                "title": "Our First EUCC Certificate", "url": "https://example.test/eucc-post",
                "detail": "", "matched_keywords": ["EUCC"], "tab": "intl",
            },
            {
                "source": "CSfC Component Selections", "kind": "updated",
                "title": "IPsec VPN Gateway", "url": "https://example.test/ipsec-selection.pdf",
                "detail": "NSA updated a component selection document",
                "matched_keywords": ["CSfC"], "tab": "us",
            },
        ],
    }

    dashboard.render_dashboard(diff, output_dir=str(tmp_path))
    html = (tmp_path / "cc_dashboard.html").read_text()

    assert "Revised NIAP announcement" in html
    assert "Software Bill of Materials" in html
    assert "NIAP Policy Letters" in html
    assert '<span class="card-count">2 alerts</span>' in html
    assert "2 alerts</div>" not in html
    assert "CSfC Component Selections: IPsec VPN Gateway" in html


def test_csfc_counts_include_updated_bucket():
    diff = {
        "csfc": {
            "selection_links": {},
            "pages": {
                "apl": {
                    "added": [],
                    "removed": [],
                    "updated": [{"text": "Component listing revised"}],
                },
            },
        },
    }

    assert dashboard._section_daily_counts([diff], "csfc") == [1]

def test_history_includes_csfc_component_updated_entries(tmp_path):
    old_dir = dashboard.config.DIFF_DIR
    dashboard.config.DIFF_DIR = str(tmp_path)
    try:
        payload = {
            "period_end": "2026-07-02T06:00:00+00:00",
            "niap": {
                "cisco_ndcpp": {"added": []},
                "pps": {"added": [], "removed": []},
                "tds": {"added": []},
                "pcl_all": {"added": []},
                "in_evaluation": {"added": [], "removed": []},
            },
            "nato": {"cisco_added": []},
            "eucc": {"cisco_added": []},
            "csfc": {
                "selection_links": {},
                "pages": {
                    "apl": {
                        "added": [],
                        "removed": [],
                        "updated": [{
                            "text": "Cisco Secure Firewall listing revised",
                            "href": "https://example.test/component",
                        }],
                    },
                },
            },
            "nist": {"pages": {}},
        }
        (tmp_path / "2026-07-02_diff.json").write_text(json.dumps(payload))

        history = dashboard._build_history()

        updated_entries = [
            h for h in history
            if h["category"] == "csfc_change" and h["kind"] == "updated"
        ]
        assert len(updated_entries) == 1
        assert "updated" in updated_entries[0]["title"].lower()
        assert "Cisco Secure Firewall" in updated_entries[0]["title"]
    finally:
        dashboard.config.DIFF_DIR = old_dir

def test_dashboard_renders_csfc_component_updated_item(tmp_path):
    dashboard.config.DIFF_DIR = str(tmp_path / "diffs")
    dashboard.config.STAGING_DIR = "docs/staging"
    dashboard.config.WATCH_KEYWORDS = []
    dashboard.config.NATO_NIAPCL_URL = "https://example.test/nato"
    dashboard.config.EUCC_REQUIREMENTS_URL = "https://example.test/eucc"
    dashboard.config.EUCC_CERTIFICATES_URL = "https://example.test/eucc-certs"
    dashboard.config.CSFC_PRODUCT_LIST_URL = "https://example.test/csfc"
    dashboard.config.CSFC_BASE = "https://example.test"
    dashboard.config.CSFC_PAGES = {"announcements": "/announcements"}
    diff = {
        "period_start": "2026-07-01T00:00:00Z",
        "period_end": "2026-07-02T00:00:00Z",
        "niap": {
            "pps": {}, "tds": {}, "cisco_ndcpp": {}, "pcl_all": {},
            "in_evaluation": {},
            "news": {key: [] for key in ("added", "revised", "deactivated", "reactivated", "removed")},
            "events": {key: [] for key in ("added", "revised", "deactivated", "reactivated", "removed")},
            "policies": {key: [] for key in ("added", "revised", "archived", "reactivated", "removed")},
        },
        "cc_portal": {"news": {"added": []}, "pps": {"added": []}, "products": {"added": []}},
        "cctl_labs": {},
        "csfc": {
            "pages": {
                "apl": {
                    "added": [],
                    "removed": [],
                    "updated": [{
                        "text": "Cisco Secure Firewall listing revised",
                        "href": "https://example.test/component",
                    }],
                },
            },
            "selection_links": {},
            "feeds": {},
        },
        "cc_crypto": {"pages": {}}, "nist": {"pages": {}},
        "nato": {"pages": {}, "cisco_added": [], "cisco_removed": []},
        "eucc": {"pages": {}, "cisco_added": [], "cisco_removed": []},
        "alerts": [],
    }

    dashboard.render_dashboard(diff, output_dir=str(tmp_path))
    html = (tmp_path / "cc_dashboard.html").read_text()

    assert "Cisco Secure Firewall listing revised" in html
    assert "component updated" in html
