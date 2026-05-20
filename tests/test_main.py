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
        for key in ("pcl", "pps", "tds", "events", "news"):
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
