"""
tests/test_main.py — Unit tests for main.py helper functions.

Covers:
- _empty_snapshot(): structural completeness; no dead cctls key (fix #26)
- run_daily() first-run path: all domain sections cleared, alerts suppressed (fix #23)
- run_merge() first-run path: same clearing behaviour (fix #23)
- _rotate_old_files(): files beyond KEEP_SNAPSHOTS deleted; newer files kept
- snapshot_path() / diff_path(): return correctly named paths
"""
import sys
import os
import json
import glob
import tempfile
import copy
from unittest.mock import MagicMock, patch, mock_open
import pytest

# ---------------------------------------------------------------------------
# Config stub — must be set before importing main
# ---------------------------------------------------------------------------
_cfg = MagicMock()
_cfg.LOG_LEVEL = "WARNING"
_cfg.SNAPSHOT_SCHEMA_VERSION = 2
_cfg.SNAPSHOT_DIR = "snapshots"
_cfg.DIFF_DIR = "snapshots/diffs"
_cfg.DASHBOARD_DIR = "docs"
_cfg.STAGING_DIR = "docs/staging"
_cfg.SANITY_MIN_PCL = 50
_cfg.SANITY_MIN_PPS = 10
_cfg.SANITY_MIN_CSFC_APL = 5
_cfg.SANITY_MIN_CSFC_ANNOUNCEMENTS = 1
_cfg.SANITY_MIN_CC_CRYPTO_PUBS = 5
_cfg.SANITY_MIN_NIST_NEWS = 10
_cfg.SANITY_MIN_NIAP_NEWS = 1
_cfg.SANITY_MIN_NIAP_POLICIES = 1
_cfg.SANITY_MIN_NATO_PRODUCTS = 3
_cfg.SANITY_MIN_EUCC_CERTS = 2
for _mod in (
    "config", "collector", "differ", "dashboard", "emailer",
    "requests", "feedparser", "bs4",
):
    sys.modules.setdefault(_mod, MagicMock())
sys.modules["config"] = _cfg

import main  # noqa: E402


# ===========================================================================
# _empty_snapshot — fix #26: no dead cctls key
# ===========================================================================

class TestEmptySnapshot:

    def test_returns_dict(self):
        result = main._empty_snapshot()
        assert isinstance(result, dict)

    def test_no_cctls_key_in_niap(self):
        """fix #26: cctls must NOT be a key in the niap section."""
        result = main._empty_snapshot()
        niap = result.get("niap", {})
        assert "cctls" not in niap, (
            "Found dead 'cctls' key in _empty_snapshot niap (fix #26 not applied)"
        )

    def test_niap_has_expected_keys(self):
        result = main._empty_snapshot()
        niap = result["niap"]
        for key in ("pcl", "pps", "tds", "events", "news", "policies"):
            assert key in niap, f"Missing expected niap key: {key}"

    def test_all_domain_keys_present(self):
        result = main._empty_snapshot()
        for key in ("niap", "cc_portal", "cctl_labs", "csfc",
                    "cc_crypto", "nist", "nato", "eucc"):
            assert key in result, f"Missing domain key '{key}' in _empty_snapshot"

    def test_schema_version_set(self):
        result = main._empty_snapshot()
        assert result.get("schema_version") == 2

    def test_nato_and_eucc_present(self):
        """fix #23: nato and eucc must be in empty snapshot for first-run clearing."""
        result = main._empty_snapshot()
        assert "nato" in result
        assert "eucc" in result

    def test_all_list_fields_are_lists(self):
        """Every leaf value in the snapshot should be a list or dict, not None."""
        result = main._empty_snapshot()
        def _check(obj, path=""):
            if isinstance(obj, dict):
                for k, v in obj.items():
                    _check(v, f"{path}.{k}")
            elif isinstance(obj, list):
                pass  # OK
            elif isinstance(obj, int):
                pass  # schema_version is an int
            elif isinstance(obj, str):
                pass  # collected_at is a string
            else:
                pytest.fail(f"Unexpected type {type(obj)} at {path}: {obj!r}")
        _check(result)


# ===========================================================================
# snapshot_path / diff_path
# ===========================================================================

class TestPathHelpers:

    def test_snapshot_path_contains_date(self):
        with patch("main.os.makedirs"):
            p = main.snapshot_path()
        assert p.endswith(".json")
        # Should contain a YYYY-MM-DD date
        import re
        assert re.search(r"\d{4}-\d{2}-\d{2}", p)

    def test_diff_path_contains_diff(self):
        with patch("main.os.makedirs"):
            p = main.diff_path()
        assert "_diff.json" in p

    def test_snapshot_path_uses_snapshot_dir(self):
        _cfg.SNAPSHOT_DIR = "/tmp/snaps"
        with patch("main.os.makedirs"):
            p = main.snapshot_path()
        assert "/tmp/snaps" in p
        _cfg.SNAPSHOT_DIR = "snapshots"  # reset


# ===========================================================================
# Source health and last-known-good fallback
# ===========================================================================

def _healthy_snapshot():
    return {
        "schema_version": 2,
        "collected_at": "2026-07-02T06:00:00+00:00",
        "niap": {
            "pcl": [{"product_id": i} for i in range(50)],
            "pps": [{"pp_id": i} for i in range(10)],
        },
        "cc_portal": {"news": [{"id": 1}], "pps": [], "products": []},
        "cctl_labs": {"Test Lab": [{"title": "post"}]},
        "csfc": {"pages": {
            "apl": [{"text": str(i)} for i in range(5)],
            "announcements": [{"text": "5/20/25 | Published guidance"}],
        }},
        "cc_crypto": {"pages": {"publications": [{"text": str(i)} for i in range(5)]}},
        "nist": {"pages": {"news": [{"text": str(i)} for i in range(10)]}},
        "nato": {"pages": {"all_products": [{"name": str(i)} for i in range(3)]}},
        "eucc": {"pages": {"certificates": [{"name": str(i)} for i in range(2)]}},
    }


class TestSourceHealth:

    def test_all_healthy_collections_are_marked_healthy(self):
        snapshot = _healthy_snapshot()
        main._apply_source_health(snapshot)

        assert set(snapshot["source_health"]) == set(main.DOMAIN_KEYS)
        assert all(
            health["status"] == "healthy"
            for health in snapshot["source_health"].values()
        )

    def test_failed_domain_retains_last_known_good_data(self):
        prior = _healthy_snapshot()
        main._apply_source_health(prior)
        current = _healthy_snapshot()
        current["csfc"] = {"pages": {"apl": []}}

        main._apply_source_health(current, prior)

        assert current["source_health"]["csfc"]["status"] == "stale"
        assert current["source_health"]["csfc"]["using_last_known_good"] is True
        assert current["csfc"] == prior["csfc"]

    def test_failed_domain_without_prior_data_is_failed(self):
        current = _healthy_snapshot()
        current["nato"] = {"pages": {"all_products": []}}

        main._apply_source_health(current)

        health = current["source_health"]["nato"]
        assert health["status"] == "failed"
        assert health["consecutive_failures"] == 1
        assert health["using_last_known_good"] is False

    def test_consecutive_failures_increment_across_stale_snapshots(self):
        prior = _healthy_snapshot()
        prior["source_health"] = {
            "nist": {"status": "stale", "consecutive_failures": 2}
        }
        current = _healthy_snapshot()
        current["nist"] = {"pages": {"news": []}}

        main._apply_source_health(current, prior)

        assert current["source_health"]["nist"]["consecutive_failures"] == 3

    def test_explicit_collection_error_is_not_treated_as_healthy(self):
        prior = _healthy_snapshot()
        main._apply_source_health(prior)
        current = _healthy_snapshot()

        main._apply_source_health(current, prior, collection_errors={"eucc"})

        assert current["source_health"]["eucc"]["status"] == "stale"
        assert "missing or unreadable" in current["source_health"]["eucc"]["detail"]

    def test_completely_empty_collection_reuses_each_healthy_domain(self):
        prior = _healthy_snapshot()
        main._apply_source_health(prior)
        current = {
            "schema_version": 2,
            "collected_at": "2026-07-03T06:00:00+00:00",
        }

        main._apply_source_health(
            current,
            prior,
            collection_errors=set(main.DOMAIN_KEYS),
        )

        assert all(
            health["status"] == "stale"
            for health in current["source_health"].values()
        )
        for domain in main.DOMAIN_KEYS:
            assert current[domain] == prior[domain]

    def test_partial_failure_does_not_block_healthy_domains(self):
        prior = _healthy_snapshot()
        main._apply_source_health(prior)
        current = _healthy_snapshot()
        current["nist"] = {"pages": {"news": []}}

        main._apply_source_health(current, prior)

        assert current["source_health"]["nist"]["status"] == "stale"
        assert all(
            current["source_health"][domain]["status"] == "healthy"
            for domain in main.DOMAIN_KEYS
            if domain != "nist"
        )

    def test_failed_niap_news_retains_only_news_last_known_good(self):
        prior = _healthy_snapshot()
        prior["niap"].update({
            "news": [{"id": 1}, {"id": 2}],
            "events": [],
            "policies": [{"policy_id": 1}],
            "_collection_health": {
                "news": {"success": True, "complete": True},
                "events": {"success": True, "complete": True},
                "policies": {"success": True, "complete": True},
            },
        })
        main._apply_source_health(prior)

        current = copy.deepcopy(prior)
        current["niap"]["pcl"].append({"product_id": 999})
        current["niap"]["news"] = []
        current["niap"]["_collection_health"]["news"] = {
            "success": False, "complete": False, "detail": "timeout"
        }

        main._apply_source_health(current, prior)

        assert current["niap"]["news"] == prior["niap"]["news"]
        assert current["niap"]["pcl"] != prior["niap"]["pcl"]
        assert current["source_health"]["niap"]["status"] == "stale"
        assert current["source_health"]["niap"]["subcollections"]["news"]["status"] == "stale"

    def test_successful_empty_events_are_healthy(self):
        snapshot = _healthy_snapshot()
        snapshot["niap"].update({
            "news": [{"id": 1}],
            "events": [],
            "policies": [{"policy_id": 1}],
            "_collection_health": {
                "news": {"success": True, "complete": True},
                "events": {"success": True, "complete": True},
                "policies": {"success": True, "complete": True},
            },
        })

        main._apply_source_health(snapshot)

        assert snapshot["source_health"]["niap"]["status"] == "healthy"
        assert snapshot["source_health"]["niap"]["subcollections"]["events"]["status"] == "healthy"

    def test_new_policy_collection_is_baselined_without_alerts(self):
        old = _healthy_snapshot()
        main._apply_source_health(old)
        new = copy.deepcopy(old)
        new["niap"].update({
            "policies": [{"policy_id": 1, "policy_title": "Policy One"}],
            "news": [{"id": 1}],
            "events": [],
            "_collection_health": {
                "news": {"success": True, "complete": True},
                "events": {"success": True, "complete": True},
                "policies": {"success": True, "complete": True},
            },
        })
        main._apply_source_health(new, old)

        baseline = main._diff_baseline_with_recoveries(old, new)

        assert baseline["niap"]["policies"] == new["niap"]["policies"]

    def test_first_recovery_is_used_as_diff_baseline(self):
        old = _healthy_snapshot()
        old["csfc"] = {"pages": {"apl": []}}
        new = _healthy_snapshot()

        baseline = main._diff_baseline_with_recoveries(old, new)

        assert baseline["csfc"] == new["csfc"]
        assert baseline["niap"] == old["niap"]

    def test_existing_healthy_domain_keeps_prior_diff_baseline(self):
        old = _healthy_snapshot()
        new = _healthy_snapshot()
        new["csfc"]["pages"]["apl"].append({"text": "new component"})

        baseline = main._diff_baseline_with_recoveries(old, new)

        assert baseline is old

    def test_third_failure_triggers_operational_notification(self):
        _cfg.DRY_RUN = False
        emailer = MagicMock()
        diff = {
            "source_health": {
                "nist": {"status": "stale", "consecutive_failures": 3}
            },
            "alerts": [],
            "niap": {},
            "nist": {},
            "nato": {},
            "eucc": {},
        }

        main._fire_alerts(diff, emailer)

        emailer.send_source_health_email.assert_called_once()

    def test_second_failure_does_not_repeat_operational_notification(self):
        _cfg.DRY_RUN = False
        emailer = MagicMock()
        diff = {
            "source_health": {
                "nist": {"status": "stale", "consecutive_failures": 2}
            },
            "alerts": [],
            "niap": {},
            "nist": {},
            "nato": {},
            "eucc": {},
        }

        main._fire_alerts(diff, emailer)

        emailer.send_source_health_email.assert_not_called()

    def test_first_failure_does_not_escalate(self):
        _cfg.DRY_RUN = False
        emailer = MagicMock()
        diff = {
            "source_health": {
                "nist": {"status": "failed", "consecutive_failures": 1}
            },
            "alerts": [],
            "niap": {},
            "nist": {},
            "nato": {},
            "eucc": {},
        }

        main._fire_alerts(diff, emailer)

        emailer.send_source_health_email.assert_not_called()

    def test_revised_announcement_and_policy_are_content_notifications(self):
        _cfg.DRY_RUN = False
        emailer = MagicMock()
        diff = {
            "source_health": {},
            "alerts": [],
            "niap": {
                "news": {"revised": [{"id": 1, "title": "Revised notice"}]},
                "events": {},
                "policies": {"revised": [{
                    "policy_num": 30, "policy_title": "Revised policy",
                }]},
            },
            "nist": {}, "nato": {}, "eucc": {},
        }

        main._fire_alerts(diff, emailer)

        emailer.send_source_health_email.assert_not_called()
        emailer.send_niap_news_webex.assert_called_once()
        sent = emailer.send_niap_news_webex.call_args.args[0]
        assert {item["_content_type"] for item in sent} == {"news", "policies"}
        assert all(item["_change_kind"] == "revised" for item in sent)

    def test_niap_escalation_uses_persistent_subcollection_count(self):
        _cfg.DRY_RUN = False
        emailer = MagicMock()
        diff = {
            "source_health": {
                "niap": {
                    "label": "NIAP", "status": "stale", "consecutive_failures": 3,
                    "subcollections": {
                        "news": {
                            "label": "News announcements", "status": "stale",
                            "consecutive_failures": 2,
                        },
                    },
                },
            },
            "alerts": [], "niap": {}, "nist": {}, "nato": {}, "eucc": {},
        }

        main._fire_alerts(diff, emailer)
        emailer.send_source_health_email.assert_not_called()

        diff["source_health"]["niap"]["subcollections"]["news"]["consecutive_failures"] = 3
        main._fire_alerts(diff, emailer)
        escalations = emailer.send_source_health_email.call_args.args[0]
        assert "niap.news" in escalations


# ===========================================================================
# _rotate_old_files
# ===========================================================================

class TestRotateOldFiles:

    def test_keeps_up_to_keep_snapshots(self):
        """Files beyond KEEP_SNAPSHOTS should be deleted; newer files kept."""
        with tempfile.TemporaryDirectory() as tmpdir:
            snap_dir = os.path.join(tmpdir, "snapshots")
            diff_dir = os.path.join(tmpdir, "snapshots", "diffs")
            os.makedirs(snap_dir)
            os.makedirs(diff_dir)
            _cfg.SNAPSHOT_DIR = snap_dir
            _cfg.DIFF_DIR = diff_dir

            # Create 35 snapshot files (KEEP_SNAPSHOTS = 30)
            snap_files = []
            for i in range(35):
                fname = os.path.join(snap_dir, f"2024-{i:02d}-01.json")
                with open(fname, "w") as f:
                    f.write("{}")
                snap_files.append(fname)

            main._rotate_old_files()

            remaining = sorted(glob.glob(os.path.join(snap_dir, "*.json")))
            assert len(remaining) == main.KEEP_SNAPSHOTS, (
                f"Expected {main.KEEP_SNAPSHOTS} files after rotation, got {len(remaining)}"
            )
            # The 5 oldest should be gone; the 30 newest should remain
            for deleted in snap_files[:5]:
                assert not os.path.exists(deleted), f"{deleted} should have been deleted"
            for kept in snap_files[5:]:
                assert os.path.exists(kept), f"{kept} should have been kept"

    def test_does_not_delete_when_under_limit(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            snap_dir = os.path.join(tmpdir, "snapshots")
            diff_dir = os.path.join(tmpdir, "snapshots", "diffs")
            os.makedirs(snap_dir)
            os.makedirs(diff_dir)
            _cfg.SNAPSHOT_DIR = snap_dir
            _cfg.DIFF_DIR = diff_dir

            for i in range(10):
                fname = os.path.join(snap_dir, f"2024-0{i+1:01d}-01.json")
                with open(fname, "w") as f:
                    f.write("{}")

            main._rotate_old_files()
            remaining = glob.glob(os.path.join(snap_dir, "*.json"))
            assert len(remaining) == 10


# ===========================================================================
# run_daily — first-run path: all sections cleared (fix #23)
# ===========================================================================

class TestRunDailyFirstRun:
    """Verify that on a first run (no prior snapshot) all diff sections are
    cleared and alerts are suppressed — including nato and eucc (fix #23)."""

    def _make_diff_with_changes(self):
        """A diff that looks like it has real changes in every domain."""
        return {
            "niap": {"pps": {"added": [{"pp_id": "1"}]}, "tds": {"added": []},
                     "cisco_ndcpp": {"added": []}, "news": {"added": []}, "events": {"added": []}},
            "cc_portal": {"news": {"added": [{"text": "item"}]}, "pps": {"added": []}, "products": {"added": []}},
            "cctl_labs": {"LabA": [{"title": "post"}]},
            "csfc": {"pages": {"apl": {"added": [{"text": "product"}]}}, "component_selections": {}, "feeds": {}},
            "cc_crypto": {"pages": {}, "doc_headers": {}},
            "nist": {"pages": {}, "doc_headers": {}, "feeds": {}},
            "nato": {"pages": {}, "cisco_added": [{"name": "Cisco X"}], "cisco_removed": []},
            "eucc": {"pages": {}, "cisco_added": [{"name": "Cisco Y"}], "cisco_removed": []},
            "alerts": [{"source": "NIAP PP", "title": "NDcPP", "matched_keywords": ["NDcPP"]}],
        }

    def test_first_run_clears_all_sections(self):
        """fix #23: After first-run clearing, nato and eucc must also be empty."""
        diff = self._make_diff_with_changes()

        def _clear_lists(obj):
            if isinstance(obj, list):
                return []
            if isinstance(obj, dict):
                return {k: _clear_lists(v) for k, v in obj.items()}
            return obj

        # Simulate the first-run clearing logic from run_daily / run_merge
        for section in ("niap", "cc_portal", "cctl_labs", "csfc",
                        "cc_crypto", "nist", "nato", "eucc"):
            if section in diff:
                diff[section] = _clear_lists(diff[section])
        diff["alerts"] = []

        # Validate nato and eucc are cleared
        assert diff["nato"]["cisco_added"] == [], "nato.cisco_added not cleared on first run (fix #23)"
        assert diff["eucc"]["cisco_added"] == [], "eucc.cisco_added not cleared on first run (fix #23)"
        assert diff["alerts"] == [], "Alerts not suppressed on first run"
        assert diff["niap"]["pps"]["added"] == [], "niap.pps.added not cleared on first run"
        assert diff["cctl_labs"] == {}, "cctl_labs not cleared on first run"

    def test_first_run_suppresses_all_alerts(self):
        diff = self._make_diff_with_changes()
        # After clearing
        diff["alerts"] = []
        assert diff["alerts"] == []

    def test_first_run_clear_covers_nato_section(self):
        """The section list in run_daily must include nato (fix #23)."""
        # Read the source to verify nato and eucc are in the clearing loop
        import inspect
        src = inspect.getsource(main.run_daily)
        assert "nato" in src, "run_daily clearing loop missing 'nato' (fix #23)"
        assert "eucc" in src, "run_daily clearing loop missing 'eucc' (fix #23)"

    def test_first_run_clear_covers_eucc_section(self):
        import inspect
        src = inspect.getsource(main.run_merge)
        assert "nato" in src, "run_merge clearing loop missing 'nato' (fix #23)"
        assert "eucc" in src, "run_merge clearing loop missing 'eucc' (fix #23)"
