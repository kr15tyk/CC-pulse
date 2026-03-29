"""
main.py — Entry point for CC Pulse.

Features:
  - Structured logging (respects config.LOG_LEVEL)
  - Daily diff JSON saved to snapshots/diffs/ (decouples weekly from re-diffing)
  - Weekly job merges pre-computed daily diff files (fast, no re-diff)
  - Webex + immediate email alert fired after daily diff if keyword alerts found
  - Graceful handling of SanityError (rejects bad snapshot, does not overwrite)
  - Snapshot rotation: keeps last 30 daily snapshots + diffs (fix #4)
  - Guard against double-run overwriting today's snapshot (fix #7)

Usage:
  python main.py            # Daily pulse check
  python main.py --weekly   # Send weekly email from stored daily diffs
  python main.py --bootstrap  # Collect initial snapshot (no diff)
"""

import argparse
import copy
import glob
import json
import logging
import os
import sys
from datetime import datetime, timezone

import config

# ── Logging setup ─────────────────────────────────────────────────────────────
def _setup_logging() -> None:
    level = getattr(logging, config.LOG_LEVEL.upper(), logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)-8s %(name)s — %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%SZ",
    )
    for noisy in ("urllib3", "requests", "feedparser"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


log = logging.getLogger("cc_pulse.main")


# ── Lazy imports (after logging is configured) ────────────────────────────────
def _imports():
    import collector
    import differ
    import dashboard
    import emailer
    return collector, differ, dashboard, emailer


# ── Path helpers ──────────────────────────────────────────────────────────────
def snapshot_path(dt=None) -> str:
    if dt is None:
        dt = datetime.now(timezone.utc)
    os.makedirs(config.SNAPSHOT_DIR, exist_ok=True)
    return os.path.join(config.SNAPSHOT_DIR, dt.strftime("%Y-%m-%d") + ".json")


def diff_path(dt=None) -> str:
    if dt is None:
        dt = datetime.now(timezone.utc)
    os.makedirs(config.DIFF_DIR, exist_ok=True)
    return os.path.join(config.DIFF_DIR, dt.strftime("%Y-%m-%d") + "_diff.json")


def _load_json(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _save_json(obj: dict, path: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2, default=str)
    log.info("Saved: %s", path)


def _latest_prior_snapshot() -> str | None:
    """Return the most recent snapshot file that is NOT today's."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    pattern = os.path.join(config.SNAPSHOT_DIR, "*.json")
    files = sorted(glob.glob(pattern), reverse=True)
    for f in files:
        if today not in os.path.basename(f):
            return f
    return None


# ── Snapshot rotation (fix #4) ────────────────────────────────────────────────
KEEP_SNAPSHOTS = 30  # days


def _rotate_old_files() -> None:
    """Delete snapshot and diff files older than KEEP_SNAPSHOTS days."""
    for pattern in (
        os.path.join(config.SNAPSHOT_DIR, "*.json"),
        os.path.join(config.DIFF_DIR, "*_diff.json"),
    ):
        files = sorted(glob.glob(pattern))
        to_delete = files[:-KEEP_SNAPSHOTS] if len(files) > KEEP_SNAPSHOTS else []
        for f in to_delete:
            try:
                os.remove(f)
                log.info("[Rotate] Deleted old file: %s", f)
            except OSError as exc:
                log.warning("[Rotate] Could not delete %s: %s", f, exc)


# ── Empty baseline snapshot (fix #2) ──────────────────────────────────────────
def _empty_snapshot() -> dict:
    """Return a structurally complete empty snapshot for first-run diffing."""
    return {
        "schema_version": config.SNAPSHOT_SCHEMA_VERSION,
        "collected_at": "",
        "niap":      {"pcl": [], "pps": [], "tds": [], "cctls": [], "events": [], "news": []},
        "cc_portal": {"news": [], "pps": [], "products": [], "communities": [], "publications": [], "pp_rss": []},
        "cctl_labs": {},
        "csfc":      {"pages": {}, "capability_package_headers": {}, "feeds": {}},
        "cc_crypto": {"pages": {}, "doc_headers": {}},
        "nist":      {"pages": {}, "doc_headers": {}, "feeds": {}},
    }


# ── Run modes ─────────────────────────────────────────────────────────────────
def run_daily() -> None:
    """Collect, diff, dashboard, alert (Webex + immediate email on alerts)."""
    _setup_logging()
    collector, differ, dashboard, emailer = _imports()

    log.info("=" * 55)
    log.info("CC Pulse daily run — %s", datetime.now(timezone.utc).isoformat())
    log.info("=" * 55)

    # Guard: skip if today's snapshot already exists (fix #7)
    today_path = snapshot_path()
    if os.path.exists(today_path):
        log.warning(
            "Today's snapshot already exists at %s — skipping collection "
            "to avoid duplicate diff. Delete it manually to force a re-run.",
            today_path,
        )
        sys.exit(0)

    # 1. Collect (may raise SanityError on bad data)
    try:
        new_snap = collector.collect_all()
    except collector.SanityError as exc:
        log.error("Snapshot rejected by sanity check: %s", exc)
        log.error("Aborting — no files written.")
        sys.exit(1)
    _save_json(new_snap, today_path)

    # 2. Load prior snapshot for diff
    prior_path = _latest_prior_snapshot()
    first_run = prior_path is None
    if first_run:
        log.warning("No prior snapshot found — diff will be empty (first run). Alerts suppressed.")
        old_snap = _empty_snapshot()
    else:
        log.info("Diffing against: %s", prior_path)
        old_snap = _load_json(prior_path)

    # 3. Compute diff and save it
    diff = differ.compute_diff(old_snap, new_snap)
    # Suppress alerts on first run — every item looks "new" vs empty baseline,
    # producing hundreds of false positives. Real alerts start from run #2 onward.
    if first_run:
        log.warning("First run: suppressing all changes from diff (baseline snapshot, not a real diff).")
        # Clear all change lists — on first run every item looks "new" vs empty baseline.
        # The dashboard should show 0 changes and 0 alerts. Real diffs start from run #2.
        # Each section value may be a dict-of-lists (e.g. pps: {added:[...], removed:[...]})
        # or a plain list. Recurse one level deep to clear all lists.
        def _clear_lists(obj):
            if isinstance(obj, list):
                return []
            if isinstance(obj, dict):
                return {k: _clear_lists(v) for k, v in obj.items()}
            return obj
        for section in ("niap", "cc_portal", "cctl_labs", "csfc", "cc_crypto", "nist"):
            if section in diff:
                diff[section] = _clear_lists(diff[section])
        diff["alerts"] = []
    _save_json(diff, diff_path())

    # 4. Rotate old snapshots
    _rotate_old_files()

    # 5. Render dashboard (HTML + RSS)
    dashboard.render_dashboard(diff)

    # 6. Fire alerts if keyword matches found (Webex + immediate email, fix #5)
    alerts = diff.get("alerts", [])
    if alerts:
        log.warning("%d keyword alert(s) — firing Webex notification...", len(alerts))
        emailer.send_webex_alert(alerts)
        log.warning("Sending immediate alert email...")
        emailer.send_alert_email(alerts)
    else:
        log.info("No keyword alerts.")

    log.info("Daily run complete.")


def run_weekly() -> None:
    """Merge stored daily diff files and send weekly email digest."""
    _setup_logging()
    _, differ, _, emailer = _imports()

    log.info("Building weekly digest from stored daily diffs...")
    pattern = os.path.join(config.DIFF_DIR, "*_diff.json")
    files = sorted(glob.glob(pattern))
    if not files:
        log.error("No daily diff files found in %s.", config.DIFF_DIR)
        log.error("Run the daily job at least once first.")
        sys.exit(1)

    # Use at most the last 7 daily diffs
    window = files[-7:]
    log.info(
        "Merging %d daily diff(s): %s ... %s",
        len(window),
        os.path.basename(window[0]),
        os.path.basename(window[-1]),
    )
    diffs = [_load_json(f) for f in window]

    # Use deepcopy so merge doesn't mutate the loaded dicts (fix #3)
    weekly = differ.merge_weekly_diffs([copy.deepcopy(d) for d in diffs])
    emailer.send_weekly_email(weekly)
    emailer.send_webex_alert(weekly.get("alerts", []))
    log.info("Weekly digest sent.")


def run_bootstrap() -> None:
    """Collect the initial snapshot without producing a diff."""
    _setup_logging()
    collector, _, _, _ = _imports()

    log.info("Bootstrap mode — collecting initial snapshot (no diff).")
    try:
        snap = collector.collect_all()
    except collector.SanityError as exc:
        log.error("Bootstrap rejected by sanity check: %s", exc)
        sys.exit(1)

    path = snapshot_path()
    _save_json(snap, path)
    log.info("Bootstrap complete. Snapshot at %s", path)
    log.info("Run the daily job tomorrow to get your first diff.")


# ── Entry point ───────────────────────────────────────────────────────────────
def main() -> None:
    parser = argparse.ArgumentParser(
        description="CC Pulse — Common Criteria monitoring engine"
    )
    parser.add_argument(
        "--weekly",
        action="store_true",
        help="Send weekly email digest from stored daily diffs",
    )
    parser.add_argument(
        "--bootstrap",
        action="store_true",
        help="Collect initial snapshot only (no diff)",
    )
    args = parser.parse_args()

    if args.bootstrap:
        run_bootstrap()
    elif args.weekly:
        run_weekly()
    else:
        run_daily()


if __name__ == "__main__":
    main()


