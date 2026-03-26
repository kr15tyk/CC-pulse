"""
main.py — Entry point for CC Pulse.

Features:
  - Structured logging (respects config.LOG_LEVEL)
  - Daily diff JSON saved to snapshots/diffs/ (decouples weekly from re-diffing)
  - Weekly job merges pre-computed daily diff files (fast, no re-diff)
  - Webex alert fired immediately after daily diff if keyword alerts found
  - Graceful handling of SanityError (rejects bad snapshot, does not overwrite)

Usage:
    python main.py             # Daily pulse check
    python main.py --weekly    # Send weekly email from stored daily diffs
    python main.py --bootstrap # Collect initial snapshot (no diff)
"""

import argparse
import glob
import json
import logging
import os
import sys
from datetime import datetime, timezone

import config

# ── Logging setup ────────────────────────────────────────────────────────────
def _setup_logging() -> None:
    level = getattr(logging, config.LOG_LEVEL.upper(), logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)-8s %(name)s — %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%SZ",
    )
    # Silence noisy third-party loggers
    for noisy in ("urllib3", "requests", "feedparser"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

log = logging.getLogger("cc_pulse.main")

# ── Lazy imports (after logging is configured) ───────────────────────────────
def _imports():
    import collector
    import differ
    import dashboard
    import emailer
    return collector, differ, dashboard, emailer

# ── Path helpers ─────────────────────────────────────────────────────────────
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

# ── Run modes ────────────────────────────────────────────────────────────────
def run_daily() -> None:
    """Collect, diff, dashboard, alert."""
    _setup_logging()
    collector, differ, dashboard, emailer = _imports()

    log.info("=" * 55)
    log.info("CC Pulse daily run — %s", datetime.now(timezone.utc).isoformat())
    log.info("=" * 55)

    # 1. Collect (may raise SanityError on bad data)
    try:
        new_snap = collector.collect_all()
    except collector.SanityError as exc:
        log.error("Snapshot rejected by sanity check: %s", exc)
        log.error("Aborting — no files written.")
        sys.exit(1)

    today_path = snapshot_path()
    _save_json(new_snap, today_path)

    # 2. Load prior snapshot for diff
    prior_path = _latest_prior_snapshot()
    if prior_path is None:
        log.warning("No prior snapshot found — diff will be empty.")
        log.warning("Run again tomorrow to get your first real diff.")
        old_snap = {"niap": {}, "cc_portal": {}, "cctl_labs": {}}
    else:
        log.info("Diffing against: %s", prior_path)
        old_snap = _load_json(prior_path)

    # 3. Compute diff and save it
    diff = differ.compute_diff(old_snap, new_snap)
    _save_json(diff, diff_path())

    # 4. Render dashboard (HTML + RSS)
    dashboard.render_dashboard(diff)

    # 5. Fire Webex alert if keyword matches found
    alerts = diff.get("alerts", [])
    if alerts:
        log.warning("%d keyword alert(s) — firing Webex notification...", len(alerts))
        emailer.send_webex_alert(alerts)
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
    weekly = differ.merge_weekly_diffs(diffs)

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


# ── Entry point ──────────────────────────────────────────────────────────────
def main() -> None:
    parser = argparse.ArgumentParser(
        description="CC Pulse — Common Criteria monitoring engine"
    )
    parser.add_argument(
        "--weekly", action="store_true",
        help="Send weekly email digest from stored daily diffs"
    )
    parser.add_argument(
        "--bootstrap", action="store_true",
        help="Collect initial snapshot only (no diff)"
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
