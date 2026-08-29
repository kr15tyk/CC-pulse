"""
tests/test_differ.py — Unit tests for differ.py core logic.

Covers the bugs fixed in waves 1-2 and the critical diff functions:
- flag_alerts(): matched_keywords key populated correctly (fix #22)
- diff_cctl_labs(): returns {lab_name: [items]}, not {"added": [...]} (fix #22)
- merge_weekly_diffs(): nato and eucc sections present after merge (fix #23)
- _headers_changed(): all four field comparisons
- _diff_selection_hashes(): hash change detected; fetch errors skipped
- diff_niap_pps(): added/removed/sunset_changes
- diff_niap_tds(): added/removed
- diff_niap_pcl_cisco(): Cisco NDcPP filter and status transitions
- compute_diff(): end-to-end smoke test with empty snapshots
"""
import copy
import sys
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Minimal config stub — provide just what differ.py reads at import time
# ---------------------------------------------------------------------------
_cfg = MagicMock()
_cfg.WATCH_KEYWORDS = ["NDcPP", "CSfC", "FIPS 140-3", "TLS 1.3"]
_cfg.NEWS_CATEGORY_KEYWORDS = {
    "POLICY": ["policy"],
    "PUBLICATION": ["publication"],
    "NEWS": [],
}
_cfg.CISCO_VENDOR_KEYWORDS = ["cisco"]
_cfg.NDCPP_PP_KEYWORDS = ["CPP_ND"]
_cfg.CSFC_PRODUCT_LIST_URL = "https://nsa.gov/csfc"
_cfg.CSFC_BASE = "https://www.nsa.gov"
_cfg.CSFC_PAGES = {"cap_packages": "/capability-packages/"}
_cfg.NATO_NIAPCL_URL = "https://nato.int/niapcl"
_cfg.EUCC_REQUIREMENTS_URL = "https://enisa.eu/eucc"
_cfg.EUCC_CERTIFICATES_URL = "https://enisa.eu/certs"
_cfg.NIAP_BASE = "https://www.niap-ccevs.org"
_cfg.IETF_DATATRACKER_BASE = "https://datatracker.ietf.org"
_cfg.IEEE_80211_TIMELINE_URL = "https://www.ieee802.org/11/Reports/802.11_Timelines.htm"
sys.modules["config"] = _cfg

import differ  # noqa: E402 — must come after config stub


# ---------------------------------------------------------------------------
# Helpers / factories
# ---------------------------------------------------------------------------

def _pcl(pid, vendor="Cisco Systems", status="Certified", pp="CPP_ND_v2.2e"):
    return {
        "product_id": pid,
        "product_name": f"Product {pid}",
        "vendor_id_name": vendor,
        "status_sort": status,
        "protection_profiles": [{"pp_short_name": pp}],
    }


def _diff_with_alerts(alerts_list):
    """Return a minimal diff dict pre-loaded with the given alerts."""
    return {
        "alerts": alerts_list,
        "niap": {"cisco_ndcpp": {"added": []}, "pps": {"added": [], "sunset_changes": []},
                 "tds": {"added": []}, "news": {"added": []}, "events": {"added": []}},
        "cc_portal": {"news": {"added": []}, "pps": {"added": []}, "products": {"added": []}},
        "cctl_labs": {},
        "csfc": {"component_selections": {}, "pages": {}, "documents": {}, "feeds": {}},
        "cc_crypto": {"pages": {}, "doc_headers": {}},
        "nist": {"pages": {}, "doc_headers": {}, "feeds": {}},
        "nato": {"pages": {}, "cisco_added": [], "cisco_removed": []},
        "eucc": {"pages": {}, "cisco_added": [], "cisco_removed": []},
        "ietf_cnsa": {"added": [], "removed": [], "updated": [], "rfc9846_adoption": []},
        "ieee_pqc": {"added": [], "removed": [], "updated": []},
    }


def _empty_snap():
    return {
        "schema_version": 2,
        "collected_at": "2024-06-01T06:00:00+00:00",
        "niap": {"pcl": [], "pps": [], "tds": [], "events": [], "news": [], "policies": []},
        "cc_portal": {"news": [], "pps": [], "products": [], "communities": [], "pp_rss": []},
        "cctl_labs": {},
        "csfc": {"pages": {}, "component_selection_hashes": {}, "documents": {}, "feeds": {}},
        "cc_crypto": {"pages": {}, "doc_headers": {}},
        "nist": {"pages": {}, "doc_headers": {}, "feeds": {}},
        "nato": {"pages": {}, "cisco_products": []},
        "eucc": {"pages": {}, "cisco_certs": []},
        "ietf_cnsa": {"documents": []},
        "ieee_pqc": {"projects": []},
    }


# ===========================================================================
# NIAP announcement and policy revisions
# ===========================================================================

class TestNiapContentDiffs:

    def test_news_revision_detected_for_existing_id(self):
        old = [{"id": 7, "title": "Policy update", "announcement": "old", "active": True}]
        new = [{"id": 7, "title": "Policy update", "announcement": "revised", "active": True,
                "moddate": "2026-07-02T12:00:00Z"}]

        result = differ.diff_niap_news(old, new)

        assert result["added"] == []
        assert len(result["revised"]) == 1
        assert result["revised"][0]["_category"] == "POLICY"

    def test_news_deactivation_and_removal_are_distinct(self):
        old = [
            {"id": 1, "title": "One", "active": True},
            {"id": 2, "title": "Two", "active": True},
        ]
        new = [{"id": 1, "title": "One", "active": False}]

        result = differ.diff_niap_news(old, new)

        assert [item["id"] for item in result["deactivated"]] == [1]
        assert [item["id"] for item in result["removed"]] == [2]

    def test_policy_addendum_filename_change_is_revision(self):
        old = [{
            "policy_id": 30, "policy_num": 30, "policy_title": "SBOM",
            "archived": False,
            "addendums": [{"addendum_num": 1, "filename": "old.pdf"}],
        }]
        new = [{
            "policy_id": 30, "policy_num": 30, "policy_title": "SBOM",
            "archived": False,
            "addendums": [{"addendum_num": 1, "filename": "new.pdf"}],
        }]

        result = differ.diff_niap_policies(old, new)

        assert len(result["revised"]) == 1
        assert result["archived"] == []

    def test_policy_pdf_hash_change_is_revision(self):
        old = [{
            "policy_num": 12, "policy_title": "Continuity",
            "archived": False, "filename": "policy-12.pdf",
            "document_sha256": "old-hash",
        }]
        new = [{
            "policy_num": 12, "policy_title": "Continuity",
            "archived": False, "filename": "policy-12.pdf",
            "document_sha256": "new-hash",
        }]

        result = differ.diff_niap_policies(old, new)

        assert len(result["revised"]) == 1

    def test_policy_active_to_archived_is_archive_transition(self):
        old = [{"policy_id": 5, "policy_num": 5, "policy_title": "Old", "archived": False}]
        new = [{"policy_id": 5, "policy_num": 5, "policy_title": "Old", "archived": True}]

        result = differ.diff_niap_policies(old, new)

        assert len(result["archived"]) == 1
        assert result["removed"] == []


# ===========================================================================
# flag_alerts — fix #22: matched_keywords key
# ===========================================================================

class TestFlagAlerts:

    def test_matched_keywords_key_populated(self):
        """fix #22: alert dicts must have matched_keywords, not keywords."""
        snap = _empty_snap()
        snap["niap"]["tds"] = {"added": [
            {"td_id": "TD001", "identifier": "TD001", "title": "NDcPP TLS 1.3 clarification",
             "removed_on": None}
        ], "removed": []}
        diff = differ.compute_diff(snap, snap)
        # Re-run with a snap that has a new TD keyword hit
        old = _empty_snap()
        new = _empty_snap()
        new["niap"]["tds"] = [{"td_id": "TD001", "identifier": "TD001",
                                "title": "NDcPP TLS 1.3 update", "removed_on": None}]
        old["niap"]["tds"] = []
        diff = differ.compute_diff(old, new)
        for alert in diff.get("alerts", []):
            assert "matched_keywords" in alert, "Alert missing matched_keywords key (fix #22)"
            assert "keywords" not in alert, "Alert has old 'keywords' key (fix #22 not applied)"

    def test_matched_keywords_is_list(self):
        """matched_keywords should always be a list, even when empty."""
        diff = differ.compute_diff(_empty_snap(), _empty_snap())
        for alert in diff.get("alerts", []):
            assert isinstance(alert["matched_keywords"], list)

    def test_no_alerts_on_identical_snapshots(self):
        snap = _empty_snap()
        diff = differ.compute_diff(snap, copy.deepcopy(snap))
        assert diff["alerts"] == []

    def test_csfc_keyword_triggers_alert(self):
        old = _empty_snap()
        new = _empty_snap()
        new["csfc"]["pages"]["home"] = [{"text": "NSA CSfC component update", "href": ""}]
        old["csfc"]["pages"]["home"] = []
        diff = differ.compute_diff(old, new)
        sources = [a["source"] for a in diff["alerts"]]
        # The page text containing "CSfC" should fire an alert
        matched_kw_flat = [kw for a in diff["alerts"] for kw in a["matched_keywords"]]
        assert any("CSfC" in kw for kw in matched_kw_flat)

    def test_removed_csfc_component_can_trigger_alert(self):
        diff = _diff_with_alerts([])
        diff["csfc"] = {
            "selection_links": {},
            "feeds": {},
            "pages": {
                "apl": {
                    "added": [],
                    "removed": [{"text": "Cisco VPN component", "href": ""}],
                },
            },
        }

        alerts = differ.flag_alerts(diff)

        assert any(alert["kind"] == "removed" for alert in alerts)

    def test_csfc_selection_change_alerts_without_keyword_match(self):
        diff = _diff_with_alerts([])
        pdf_url = "https://www.nsa.gov/new-ipsec-selection.pdf"
        diff["csfc"] = {
            "selection_links": {
                "IPsec VPN Gateway": {
                    "changed": True,
                    "old_href": "https://www.nsa.gov/old-selection.pdf",
                    "new_href": pdf_url,
                },
            },
            "feeds": {},
            "pages": {},
        }

        alerts = differ.flag_alerts(diff)

        assert len(alerts) == 1
        assert alerts[0]["source"] == "CSfC Component Selections"
        assert alerts[0]["url"] == pdf_url
        assert alerts[0]["matched_keywords"] == ["CSfC"]

    def test_cctl_keyword_alert_preserves_article_link(self):
        diff = _diff_with_alerts([])
        diff["cctl_labs"] = {
            "atsec": [{
                "title": "FIPS 140-3 certificate update",
                "link": "https://example.test/fips-update",
            }],
        }

        alerts = differ.flag_alerts(diff)

        assert len(alerts) == 1
        assert alerts[0]["url"] == "https://example.test/fips-update"


# ===========================================================================
# diff_cctl_labs — fix #22: returns {lab: [items]}, not {"added": [...]}
# ===========================================================================

class TestDiffCctlLabs:

    def test_returns_lab_keyed_dict(self):
        """fix #22: diff_cctl_labs must return {lab_name: [items]}, not {'added': [...]}."""
        old = {"LabA": [{"id": "1", "title": "Post 1", "link": "http://a.com/1"}]}
        new = {
            "LabA": [
                {"id": "1", "title": "Post 1", "link": "http://a.com/1"},
                {"id": "2", "title": "Post 2", "link": "http://a.com/2"},
            ]
        }
        result = differ.diff_cctl_labs(old, new)
        assert "added" not in result, "diff_cctl_labs returned {'added': ...} instead of {lab: [...]}"
        assert "LabA" in result
        assert len(result["LabA"]) == 1
        assert result["LabA"][0]["id"] == "2"

    def test_no_new_items_returns_empty_dict(self):
        old = {"LabA": [{"id": "1", "title": "Post 1", "link": ""}]}
        new = {"LabA": [{"id": "1", "title": "Post 1", "link": ""}]}
        result = differ.diff_cctl_labs(old, new)
        assert result == {}

    def test_new_lab_added(self):
        old = {}
        new = {"NewLab": [{"id": "x", "title": "First post", "link": ""}]}
        result = differ.diff_cctl_labs(old, new)
        assert "NewLab" in result
        assert len(result["NewLab"]) == 1

    def test_deduplicates_by_id(self):
        item = {"id": "abc", "title": "Same", "link": ""}
        result = differ.diff_cctl_labs({"LabA": [item]}, {"LabA": [item]})
        assert result == {}

    def test_falls_back_to_title_when_no_id(self):
        old = {"LabA": [{"title": "Old post", "link": ""}]}
        new = {"LabA": [{"title": "Old post", "link": ""}, {"title": "New post", "link": ""}]}
        result = differ.diff_cctl_labs(old, new)
        assert "LabA" in result
        assert result["LabA"][0]["title"] == "New post"


# ===========================================================================
# merge_weekly_diffs — fix #23: nato and eucc sections present
# ===========================================================================

class TestMergeWeeklyDiffs:

    def _make_diff(self, cisco_nato=None, cisco_eucc=None, alerts=None):
        return {
            "niap": {
                "pps": {"added": [], "removed": [], "sunset_changes": [], "status_changes": []},
                "tds": {"added": [], "removed": []},
                "cisco_ndcpp": {"added": [], "removed": [], "newly_archived": []},
                "news": {"added": []},
                "events": {"added": []},
            },
            "cc_portal": {"news": {"added": []}, "pps": {"added": []}, "products": {"added": []}},
            "cctl_labs": {},
            "csfc": {"feeds": {}, "pages": {}, "component_selections": {}},
            "cc_crypto": {"pages": {}, "doc_headers": {}},
            "nist": {"pages": {}, "doc_headers": {}, "feeds": {}},
            "nato": {
                "pages": {},
                "cisco_added": cisco_nato or [],
                "cisco_removed": [],
            },
            "eucc": {
                "pages": {},
                "cisco_added": cisco_eucc or [],
                "cisco_removed": [],
            },
            "alerts": alerts or [],
        }

    def test_nato_section_present_in_merged(self):
        """fix #23: nato must survive merge_weekly_diffs."""
        d1 = self._make_diff()
        d2 = self._make_diff()
        result = differ.merge_weekly_diffs([d1, d2])
        assert "nato" in result, "nato section missing from weekly merge (fix #23)"

    def test_eucc_section_present_in_merged(self):
        """fix #23: eucc must survive merge_weekly_diffs."""
        d1 = self._make_diff()
        d2 = self._make_diff()
        result = differ.merge_weekly_diffs([d1, d2])
        assert "eucc" in result, "eucc section missing from weekly merge (fix #23)"

    def test_cisco_nato_additions_merged(self):
        """fix #23: cisco_added from multiple days are combined, deduplicated."""
        prod_a = {"name": "CiscoA", "raw_text": "CiscoA product", "link": ""}
        prod_b = {"name": "CiscoB", "raw_text": "CiscoB product", "link": ""}
        d1 = self._make_diff(cisco_nato=[prod_a])
        d2 = self._make_diff(cisco_nato=[prod_b])
        result = differ.merge_weekly_diffs([d1, d2])
        assert len(result["nato"]["cisco_added"]) == 2

    def test_cisco_eucc_additions_merged(self):
        """fix #23: eucc cisco_added merged across days."""
        c1 = {"name": "EuccA", "text": "EuccA cert", "href": ""}
        c2 = {"name": "EuccB", "text": "EuccB cert", "href": ""}
        d1 = self._make_diff(cisco_eucc=[c1])
        d2 = self._make_diff(cisco_eucc=[c2])
        result = differ.merge_weekly_diffs([d1, d2])
        assert len(result["eucc"]["cisco_added"]) == 2

    def test_alerts_deduplicated_by_source_title_keywords(self):
        """Duplicate alerts (same source+title+keywords) appear only once."""
        alert = {"source": "NIAP PP", "title": "NDcPP update", "matched_keywords": ["NDcPP"]}
        d1 = self._make_diff(alerts=[alert])
        d2 = self._make_diff(alerts=[copy.deepcopy(alert)])
        result = differ.merge_weekly_diffs([d1, d2])
        assert len(result["alerts"]) == 1

    def test_empty_list_returns_empty_dict(self):
        assert differ.merge_weekly_diffs([]) == {}

    def test_single_diff_passes_through(self):
        d = self._make_diff()
        result = differ.merge_weekly_diffs([d])
        assert "nato" in result
        assert "eucc" in result


# ===========================================================================
# _headers_changed
# ===========================================================================

class TestHeadersChanged:

    def test_etag_change_detected(self):
        assert differ._headers_changed({"etag": "abc"}, {"etag": "xyz"})

    def test_last_modified_change_detected(self):
        assert differ._headers_changed(
            {"last_modified": "Mon, 01 Jan 2024 00:00:00 GMT"},
            {"last_modified": "Tue, 02 Jan 2024 00:00:00 GMT"},
        )

    def test_content_length_change_detected(self):
        assert differ._headers_changed({"content_length": "1000"}, {"content_length": "1001"})

    def test_partial_hash_change_detected(self):
        assert differ._headers_changed({"partial_hash": "aaa"}, {"partial_hash": "bbb"})

    def test_identical_headers_not_changed(self):
        h = {"etag": "abc", "last_modified": "Mon", "content_length": "100", "partial_hash": ""}
        assert not differ._headers_changed(h, h.copy())

    def test_both_empty_not_changed(self):
        assert not differ._headers_changed({}, {})

    def test_only_new_side_has_value(self):
        """A field appearing where none existed before counts as a change."""
        assert differ._headers_changed({}, {"etag": "newval"})

    def test_only_old_side_has_value(self):
        assert differ._headers_changed({"etag": "oldval"}, {})


# ===========================================================================
# _diff_selection_links
# ===========================================================================

class TestDiffSelectionLinks:

    def test_versioned_link_change_detected(self):
        old = {"MyDoc": "https://x.com/doc.pdf?ver=1"}
        new = {"MyDoc": "https://x.com/doc.pdf?ver=2"}
        result = differ._diff_selection_links(old, new)
        assert "MyDoc" in result
        assert result["MyDoc"]["changed"] is True
        assert result["MyDoc"]["old_href"].endswith("ver=1")
        assert result["MyDoc"]["new_href"].endswith("ver=2")

    def test_same_link_not_flagged(self):
        link = "https://x.com/doc.pdf?ver=1"
        result = differ._diff_selection_links({"Doc": link}, {"Doc": link})
        assert result == {}

    def test_removed_link_detected(self):
        result = differ._diff_selection_links(
            {"Doc": "https://x.com/doc.pdf"}, {}
        )
        assert result["Doc"]["old_href"] == "https://x.com/doc.pdf"
        assert result["Doc"]["new_href"] == ""

    def test_both_missing_not_flagged(self):
        result = differ._diff_selection_links({}, {})
        assert result == {}

    def test_new_link_detected(self):
        result = differ._diff_selection_links(
            {}, {"NewDoc": "https://x.com/new.pdf?ver=1"}
        )
        assert result["NewDoc"]["old_href"] == ""
        assert result["NewDoc"]["new_href"].endswith("ver=1")


# ===========================================================================
# diff_niap_pps
# ===========================================================================

class TestDiffNiapPps:

    def _pp(self, pp_id, name="NDcPP", sunset="2026-01-01", status="Active"):
        return {"pp_id": str(pp_id), "pp_short_name": name,
                "pp_name": f"Full {name}", "sunset_date": sunset, "status": status}

    def test_new_pp_detected(self):
        result = differ.diff_niap_pps([], [self._pp(1)])
        assert len(result["added"]) == 1
        assert result["added"][0]["pp_id"] == "1"

    def test_removed_pp_detected(self):
        result = differ.diff_niap_pps([self._pp(1)], [])
        assert len(result["removed"]) == 1

    def test_unchanged_pp_not_in_added(self):
        pp = self._pp(1)
        result = differ.diff_niap_pps([pp], [pp.copy()])
        assert result["added"] == []
        assert result["removed"] == []

    def test_sunset_change_detected(self):
        old = self._pp(1, sunset="2026-01-01")
        new = self._pp(1, sunset="2025-06-01")
        result = differ.diff_niap_pps([old], [new])
        assert len(result["sunset_changes"]) == 1
        assert result["sunset_changes"][0]["new_sunset"] == "2025-06-01"


# ===========================================================================
# diff_niap_tds
# ===========================================================================

class TestDiffNiapTds:

    def _td(self, td_id, title="TLS clarification", removed_on=None):
        return {"td_id": str(td_id), "identifier": f"TD{td_id:04d}",
                "title": title, "removed_on": removed_on}

    def test_new_td_detected(self):
        result = differ.diff_niap_tds([], [self._td(1)])
        assert len(result["added"]) == 1

    def test_existing_td_not_re_added(self):
        td = self._td(1)
        result = differ.diff_niap_tds([td], [td.copy()])
        assert result["added"] == []

    def test_td_removed_on_transition(self):
        old = self._td(1, removed_on=None)
        new = self._td(1, removed_on="2024-06-01")
        result = differ.diff_niap_tds([old], [new])
        assert len(result["removed"]) == 1


# ===========================================================================
# diff_niap_pcl_cisco (is_cisco_ndcpp filter)
# ===========================================================================

class TestDiffNiapPclCisco:

    def test_new_cisco_ndcpp_detected(self):
        new_prod = _pcl(1)
        result = differ.diff_niap_pcl_cisco([], [new_prod])
        assert len(result["added"]) == 1

    def test_non_cisco_vendor_excluded(self):
        non_cisco = _pcl(2, vendor="Palo Alto Networks")
        result = differ.diff_niap_pcl_cisco([], [non_cisco])
        assert result["added"] == []

    def test_non_ndcpp_pp_excluded(self):
        non_ndcpp = _pcl(3, pp="PP_MDF_v3.3")
        result = differ.diff_niap_pcl_cisco([], [non_ndcpp])
        assert result["added"] == []

    def test_in_progress_to_certified_transition(self):
        old = _pcl(4, status="In Progress")
        new = _pcl(4, status="Certified")
        result = differ.diff_niap_pcl_cisco([old], [new])
        assert len(result["added"]) == 1

    def test_certified_to_archived_transition(self):
        old = _pcl(5, status="Certified")
        new = _pcl(5, status="Archived")
        result = differ.diff_niap_pcl_cisco([old], [new])
        assert len(result["newly_archived"]) == 1
        assert result["added"] == []

    def test_removed_cisco_product(self):
        prod = _pcl(6)
        result = differ.diff_niap_pcl_cisco([prod], [])
        assert len(result["removed"]) == 1


# ===========================================================================
# compute_diff — smoke test (end-to-end, empty snapshots)
# ===========================================================================

class TestComputeDiff:

    def test_smoke_empty_snapshots(self):
        """compute_diff should not raise on two structurally identical empty snapshots."""
        snap = _empty_snap()
        diff = differ.compute_diff(snap, copy.deepcopy(snap))
        assert "niap" in diff
        assert "alerts" in diff
        assert diff["alerts"] == []

    def test_all_top_level_keys_present(self):
        snap = _empty_snap()
        diff = differ.compute_diff(snap, copy.deepcopy(snap))
        for key in ("niap", "cc_portal", "cctl_labs", "csfc", "cc_crypto", "nato", "eucc", "ietf_cnsa", "ieee_pqc", "alerts"):
            assert key in diff, f"Missing key '{key}' in compute_diff output"

    def test_nato_and_eucc_in_diff_output(self):
        """fix #23: compute_diff must include nato and eucc at top level."""
        snap = _empty_snap()
        diff = differ.compute_diff(snap, copy.deepcopy(snap))
        assert "nato" in diff
        assert "eucc" in diff

    def test_source_health_is_carried_into_diff(self):
        old = _empty_snap()
        new = copy.deepcopy(old)
        new["source_health"] = {
            "nist": {"status": "stale", "consecutive_failures": 3}
        }

        diff = differ.compute_diff(old, new)

        assert diff["source_health"] == new["source_health"]


class TestDiffNiapPclAll:
    """pcl_all reports certifications only; in-evaluation listings belong to
    diff_niap_in_evaluation. Previously a new In Progress listing appeared in
    both, showing duplicate dashboard entries (and labeling a non-cert as a
    'New Certification')."""

    def test_new_in_progress_listing_is_not_a_new_cert(self):
        new_prod = _pcl(1, vendor="Other Corp", status="In Progress")
        result = differ.diff_niap_pcl_all([], [new_prod])
        assert result["added"] == []
        # ...but it IS a new in-evaluation posting
        ie = differ.diff_niap_in_evaluation([], [new_prod])
        assert len(ie["added"]) == 1

    def test_new_certified_listing_is_added(self):
        result = differ.diff_niap_pcl_all([], [_pcl(1, status="Certified")])
        assert len(result["added"]) == 1

    def test_in_progress_to_certified_transition_is_added(self):
        old = [_pcl(1, status="In Progress")]
        new = [_pcl(1, status="Certified")]
        result = differ.diff_niap_pcl_all(old, new)
        assert len(result["added"]) == 1
        # and it leaves the in-evaluation set
        ie = differ.diff_niap_in_evaluation(old, new)
        assert len(ie["removed"]) == 1

    def test_departing_in_evaluation_listing_is_not_a_removed_cert(self):
        old = [_pcl(1, status="In Progress"), _pcl(2, status="Certified")]
        result = differ.diff_niap_pcl_all(old, [_pcl(2, status="Certified")])
        assert result["removed"] == []
        ie = differ.diff_niap_in_evaluation(old, [_pcl(2, status="Certified")])
        assert len(ie["removed"]) == 1

    def test_departing_certified_product_is_removed(self):
        old = [_pcl(1, status="Certified")]
        result = differ.diff_niap_pcl_all(old, [])
        assert len(result["removed"]) == 1

    def test_certified_to_archived_still_detected(self):
        old = [_pcl(1, status="Certified")]
        new = [_pcl(1, status="Archived")]
        result = differ.diff_niap_pcl_all(old, new)
        assert len(result["newly_archived"]) == 1
        assert result["added"] == []



# ===========================================================================
# _diff_csfc_apl -- link-keyed diff (avoids false removed+added on reformats)
# ===========================================================================

class TestDiffCsfcApl:

    def test_new_link_is_added(self):
        result = differ._diff_csfc_apl([], [{"href": "https://x/1", "text": "New component"}])
        assert len(result["added"]) == 1
        assert result["removed"] == []
        assert result["updated"] == []

    def test_missing_link_is_removed(self):
        old = [{"href": "https://x/1", "text": "Old component"}]
        result = differ._diff_csfc_apl(old, [])
        assert len(result["removed"]) == 1
        assert result["added"] == []
        assert result["updated"] == []

    def test_unchanged_entry_is_not_reported(self):
        item = {"href": "https://x/1", "text": "Stable component"}
        result = differ._diff_csfc_apl([item], [dict(item)])
        assert result == {"added": [], "removed": [], "updated": []}

    def test_text_reformat_on_same_link_is_updated_not_removed_added(self):
        """The bug this helper fixes: a cosmetic listing edit (cert-date tweak,
        description reformat) must not appear as a spurious removed+added pair."""
        old = [{"href": "https://x/1", "text": "Cisco ASA 5500, VID 12345 -- certified Jan 2024"}]
        new = [{"href": "https://x/1", "text": "Cisco ASA 5500 (VID 12345), cert. Jan 2024"}]
        result = differ._diff_csfc_apl(old, new)
        assert result["added"] == []
        assert result["removed"] == []
        assert len(result["updated"]) == 1
        assert result["updated"][0]["_old_text"] == old[0]["text"]
        assert result["updated"][0]["text"] == new[0]["text"]

    def test_falls_back_to_text_prefix_when_no_href(self):
        """Records without a stable href fall back to keying on the text prefix."""
        old = [{"text": "Component without a link"}]
        new = [{"text": "Component without a link"}]
        result = differ._diff_csfc_apl(old, new)
        assert result == {"added": [], "removed": [], "updated": []}

    def test_different_link_same_text_prefix_is_added_and_removed(self):
        """Two different products should never collapse into a single 'updated'."""
        old = [{"href": "https://x/1", "text": "Component A"}]
        new = [{"href": "https://x/2", "text": "Component A"}]
        result = differ._diff_csfc_apl(old, new)
        assert len(result["added"]) == 1
        assert len(result["removed"]) == 1
        assert result["updated"] == []


# ===========================================================================
# diff_csfc -- routes the "apl" page through _diff_csfc_apl(); other CSfC
# pages keep the generic text-prefix diff
# ===========================================================================

class TestDiffCsfc:

    def test_apl_reformat_is_updated_not_removed_added(self):
        old_csfc = {"pages": {"apl": [{"href": "https://x/1", "text": "Old wording"}]}}
        new_csfc = {"pages": {"apl": [{"href": "https://x/1", "text": "New wording"}]}}
        result = differ.diff_csfc(old_csfc, new_csfc)
        apl = result["pages"]["apl"]
        assert apl["added"] == []
        assert apl["removed"] == []
        assert len(apl["updated"]) == 1

    def test_non_apl_page_reformat_still_uses_generic_text_diff(self):
        """Only the apl page gets link-keyed diffing; other CSfC pages are
        unaffected by this change and keep their existing text-prefix behaviour."""
        old_csfc = {"pages": {"announcements": [{"href": "https://x/a", "text": "Old wording"}]}}
        new_csfc = {"pages": {"announcements": [{"href": "https://x/a", "text": "New wording"}]}}
        result = differ.diff_csfc(old_csfc, new_csfc)
        announcements = result["pages"]["announcements"]
        assert len(announcements["added"]) == 1
        assert len(announcements["removed"]) == 1
        assert "updated" not in announcements

    def test_apl_key_absent_when_no_changes(self):
        old_csfc = {"pages": {"apl": [{"href": "https://x/1", "text": "Same"}]}}
        new_csfc = {"pages": {"apl": [{"href": "https://x/1", "text": "Same"}]}}
        result = differ.diff_csfc(old_csfc, new_csfc)
        assert "apl" not in result["pages"]


# ===========================================================================
# flag_alerts -- CSfC "updated" bucket (this feature)
# ===========================================================================

class TestFlagAlertsCsfcUpdated:

    def test_updated_csfc_apl_item_triggers_alert(self):
        diff = _diff_with_alerts([])
        diff["csfc"] = {
            "selection_links": {},
            "feeds": {},
            "pages": {
                "apl": {
                    "added": [],
                    "removed": [],
                    "updated": [{"text": "CSfC listing revised: Cisco Secure Firewall", "href": "https://example.test/item"}],
                },
            },
        }

        alerts = differ.flag_alerts(diff)

        updated_alerts = [a for a in alerts if a["kind"] == "updated" and a["source"] == "CSfC Components List"]
        assert len(updated_alerts) == 1
        assert updated_alerts[0]["url"] == "https://example.test/item"

    def test_updated_item_url_falls_back_to_page_url_when_no_href(self):
        diff = _diff_with_alerts([])
        diff["csfc"] = {
            "selection_links": {},
            "feeds": {},
            "pages": {
                "apl": {
                    "added": [],
                    "removed": [],
                    "updated": [{"text": "CSfC listing revised: Cisco Secure Firewall"}],
                },
            },
        }

        alerts = differ.flag_alerts(diff)

        updated_alerts = [a for a in alerts if a["kind"] == "updated" and a["source"] == "CSfC Components List"]
        assert len(updated_alerts) == 1
        assert updated_alerts[0]["url"] == _cfg.CSFC_PRODUCT_LIST_URL

    def test_missing_updated_key_does_not_break_flag_alerts(self):
        """Backward compatibility: page diffs produced before this feature have
        no 'updated' key at all. flag_alerts() must not raise on them."""
        diff = _diff_with_alerts([])
        diff["csfc"] = {
            "selection_links": {},
            "feeds": {},
            "pages": {
                "apl": {
                    "added": [{"text": "New CSfC component added", "href": ""}],
                    "removed": [],
                },
            },
        }

        alerts = differ.flag_alerts(diff)  # should not raise

        assert any(a["kind"] == "new_cert" for a in alerts)
        assert not any(a["kind"] == "updated" for a in alerts)


# ===========================================================================
# merge_weekly_diffs -- CSfC APL "updated" bucket merge behaviour
# ===========================================================================

class TestMergeWeeklyDiffsCsfcUpdated:

    def _make_diff_with_apl_updated(self, updated_items=None):
        return {
            "niap": {
                "pps": {"added": [], "removed": [], "sunset_changes": [], "status_changes": []},
                "tds": {"added": [], "removed": []},
                "cisco_ndcpp": {"added": [], "removed": [], "newly_archived": []},
                "news": {"added": []},
                "events": {"added": []},
            },
            "cc_portal": {"news": {"added": []}, "pps": {"added": []}, "products": {"added": []}},
            "cctl_labs": {},
            "csfc": {
                "feeds": {},
                "pages": {"apl": {"added": [], "removed": [], "updated": updated_items or []}},
                "selection_links": {},
            },
            "cc_crypto": {"pages": {}, "doc_headers": {}},
            "nist": {"pages": {}, "doc_headers": {}, "feeds": {}},
            "nato": {"pages": {}, "cisco_added": [], "cisco_removed": []},
            "eucc": {"pages": {}, "cisco_added": [], "cisco_removed": []},
            "alerts": [],
        }

    def test_updated_bucket_merged_across_days(self):
        item_a = {"href": "https://x/1", "text": "Component A revised"}
        item_b = {"href": "https://x/2", "text": "Component B revised"}
        d1 = self._make_diff_with_apl_updated([item_a])
        d2 = self._make_diff_with_apl_updated([item_b])

        result = differ.merge_weekly_diffs([d1, d2])

        assert len(result["csfc"]["pages"]["apl"]["updated"]) == 2

    def test_missing_updated_key_in_daily_diff_does_not_break_merge(self):
        """Backward compatibility: daily diffs produced before this feature have
        no 'updated' key. merge_weekly_diffs() must not raise, and must not
        fabricate an 'updated' key that was never present."""
        d1 = self._make_diff_with_apl_updated()
        d1["csfc"]["pages"]["apl"] = {"added": [{"href": "https://x/1", "text": "New"}], "removed": []}
        d2 = self._make_diff_with_apl_updated()
        d2["csfc"]["pages"] = {}

        result = differ.merge_weekly_diffs([d1, d2])  # should not raise

        assert "updated" not in result["csfc"]["pages"]["apl"]


class TestCnsaPqcMonitoringDiffs:

    def test_niap_pp_metadata_revision_detected(self):
        old = [{"pp_id": "511", "pp_short_name": "PKG_X509_v1.0", "pp_date": "2025-01-01"}]
        new = [{"pp_id": "511", "pp_short_name": "PKG_X509_v1.0", "pp_date": "2026-01-01"}]

        result = differ.diff_niap_pps(old, new)

        assert result["revised"][0]["changed_fields"] == ["pp_date"]

    def test_niap_pp_hash_change_detected_but_hash_rollout_is_not(self):
        base = {"pp_id": "511", "pp_short_name": "PKG_X509_v1.0"}
        rollout = differ.diff_niap_pps(
            [base], [{**base, "document_sha256": "new", "cnsa_markers": []}]
        )
        changed = differ.diff_niap_pps(
            [{**base, "document_sha256": "old", "cnsa_markers": ["ML-KEM"]}],
            [{**base, "document_sha256": "new", "cnsa_markers": ["ML-KEM", "ML-DSA"]}],
        )

        assert rollout["content_changes"] == []
        assert changed["content_changes"][0]["hash_changed"] is True
        assert changed["content_changes"][0]["markers_changed"] is True

    def test_niap_pdf_file_id_change_is_a_version_signal(self):
        old = [{"pp_id": "524", "pp_short_name": "CPP_ND_V4.0", "document_file_id": 100}]
        new = [{"pp_id": "524", "pp_short_name": "CPP_ND_V4.0", "document_file_id": 101}]

        result = differ.diff_niap_pps(old, new)

        assert result["content_changes"][0]["file_changed"] is True

    def test_csfc_same_prefix_suffix_change_is_updated(self):
        prefix = "A" * 120
        result = differ._diff_pages_with_updates(
            {"cap_packages": [{"text": prefix + " old", "href": "https://x/doc"}]},
            {"cap_packages": [{"text": prefix + " new", "href": "https://x/doc"}]},
        )

        assert result["cap_packages"]["added"] == []
        assert result["cap_packages"]["removed"] == []
        assert len(result["cap_packages"]["updated"]) == 1

    def test_csfc_document_hash_change_and_rollout_behavior(self):
        rollout = differ._diff_named_documents(
            {"campus_wlan": {"url": "https://x/doc"}},
            {"campus_wlan": {"url": "https://x/doc", "sha256": "new"}},
        )
        changed = differ._diff_named_documents(
            {"campus_wlan": {"url": "https://x/doc", "sha256": "old"}},
            {"campus_wlan": {"url": "https://x/doc", "sha256": "new"}},
        )

        assert rollout["updated"] == []
        assert changed["updated"][0]["hash_changed"] is True

    def test_new_csfc_document_collection_is_baselined_without_alerts(self):
        result = differ.diff_csfc(
            {"pages": {}, "selection_links": {}, "feeds": {}},
            {
                "pages": {},
                "selection_links": {},
                "feeds": {},
                "documents": {
                    "campus_wlan": {
                        "label": "Campus WLAN Capability Package",
                        "url": "https://example.test/campus-wlan.pdf",
                        "sha256": "first-rollout-hash",
                    },
                },
            },
        )

        assert result["documents"] == {
            "added": [],
            "removed": [],
            "updated": [],
        }

    def test_existing_empty_csfc_document_collection_reports_addition(self):
        result = differ.diff_csfc(
            {
                "pages": {},
                "selection_links": {},
                "feeds": {},
                "documents": {},
            },
            {
                "pages": {},
                "selection_links": {},
                "feeds": {},
                "documents": {
                    "campus_wlan": {
                        "label": "Campus WLAN Capability Package",
                        "url": "https://example.test/campus-wlan.pdf",
                        "sha256": "new-hash",
                    },
                },
            },
        )

        assert result["documents"]["added"][0]["key"] == "campus_wlan"

    def test_tls_profile_rfc9846_adoption_is_semantic_event(self):
        old = {"documents": [{
            "name": "draft-becker-cnsa2-tls-profile",
            "references_rfc8446": True,
            "references_rfc9846": False,
            "content_sha256": "old",
        }]}
        new = {"documents": [{
            "name": "draft-becker-cnsa2-tls-profile",
            "title": "CNSA 2.0 TLS Profile",
            "document_url": "https://datatracker.ietf.org/doc/draft-becker-cnsa2-tls-profile/",
            "references_rfc8446": False,
            "references_rfc9846": True,
            "content_sha256": "new",
        }]}

        result = differ.diff_ietf_cnsa(old, new)
        alerts = differ.flag_alerts({
            **_diff_with_alerts([]),
            "ietf_cnsa": result,
        })

        assert result["rfc9846_adoption"][0]["replaced_rfc8446"] is True
        assert any(alert["kind"] == "rfc_transition" for alert in alerts)

    def test_ieee_draft_milestone_change_detected(self):
        old = {"projects": [{
            "project": "P802.11bt", "draft": "D1.0",
            "timeline_sha256": "old", "status_text": "Initial ballot",
        }]}
        new = {"projects": [{
            "project": "P802.11bt", "draft": "D2.0",
            "timeline_sha256": "new", "status_text": "Recirculation ballot",
        }]}

        result = differ.diff_ieee_pqc(old, new)

        assert result["updated"][0]["old_draft"] == "D1.0"
        assert "draft" in result["updated"][0]["changed_fields"]
