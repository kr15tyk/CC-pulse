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
        "nato":      {"pages": {}, "cisco_added": [], "cisco_removed": []},
        "eucc":      {"pages": {}, "cisco_added": [], "cisco_removed": []},
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

    # 6. Fire alerts if keyword matches found (Webex + webhook + immediate email, fix #5)
    alerts = diff.get("alerts", [])
    if alerts:
        log.warning("%d keyword alert(s) — firing notifications...", len(alerts))
        emailer.send_webex_alert(alerts)
        emailer.send_webhook_alert(alerts)   # Teams / generic webhook (fix #21)
        log.warning("Sending immediate alert email...")
        emailer.send_alert_email(alerts)
    else:
        log.info("No keyword alerts.")

    # 7. Cisco NDcPP PCL celebration — fires separately from keyword alerts
    new_cisco_certs = diff.get("niap", {}).get("cisco_ndcpp", {}).get("added", [])
    if new_cisco_certs:
        log.info("%d new Cisco NDcPP certification(s) — sending celebration...", len(new_cisco_certs))
        emailer.send_cisco_cert_celebration(new_cisco_certs)
        emailer.send_cisco_cert_email(new_cisco_certs)


    # 8. Cisco CSfC APL alert — check keyword alerts tagged to CSfC containing Cisco
    new_cisco_csfc_alerts = [
        a for a in diff.get("alerts", [])
        if "CSfC" in a.get("source", "") and
           any(kw in a.get("title", "").lower() for kw in config.CISCO_VENDOR_KEYWORDS)
    ]
    if new_cisco_csfc_alerts:
        log.info("%d new Cisco CSfC alert(s) — sending celebration...", len(new_cisco_csfc_alerts))
        emailer.send_cisco_cert_celebration(new_cisco_csfc_alerts)
        emailer.send_cisco_cert_email(new_cisco_csfc_alerts)

    # 9. Cisco NATO NIAPCL celebration
    new_cisco_nato = diff.get("nato", {}).get("cisco_added", [])
    if new_cisco_nato:
        log.info("%d new Cisco NATO NIAPCL listing(s) — sending celebration...", len(new_cisco_nato))
        emailer.send_cisco_cert_celebration(new_cisco_nato)
        emailer.send_cisco_cert_email(new_cisco_nato)

    # 10. Cisco EUCC celebration
    new_cisco_eucc = diff.get("eucc", {}).get("cisco_added", [])
    if new_cisco_eucc:
        log.info("%d new Cisco EUCC certification(s) — sending celebration...", len(new_cisco_eucc))
        emailer.send_cisco_cert_celebration(new_cisco_eucc)
        emailer.send_cisco_cert_email(new_cisco_eucc)

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
    emailer.send_webhook_alert(weekly.get("alerts", []))   # Teams / generic webhook (fix #21)
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
def run_redash() -> None:
    """Re-render the dashboard HTML from the latest stored diff file.

    This is useful when the dashboard template has changed but no new diff
    has been produced today (e.g. the daily run already ran and produced no
    new data).  It loads the most recent diff from snapshots/diffs/ and
    passes it through dashboard.render_dashboard() without touching the
    collector or differ.
    """
    _imports()
    import dashboard as dash_mod

    # Find the latest diff file
    import glob as _glob
    diffs = sorted(_glob.glob(os.path.join(config.DIFF_DIR, "*_diff.json")))
    if not diffs:
        log.error("No diff files found in %s -- run daily mode first.", config.DIFF_DIR)
        sys.exit(1)

    latest = diffs[-1]
    log.info("[Redash] Loading diff from %s", latest)
    diff = _load_json(latest)
    dash_mod.render_dashboard(diff, output_dir=config.DASHBOARD_DIR)
    log.info("[Redash] Dashboard re-rendered from %s", latest)


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
    parser.add_argument(
        "--readme",
        action="store_true",
        help="Post the pinned README/info message to Webex (then pin it manually)",
    )
    parser.add_argument(
        "--redash",
        action="store_true",
        help="Re-render dashboard from the latest stored diff (no collection)",
    )
    args = parser.parse_args()

    if args.readme:
        _setup_logging()
        _, _, _, emailer = _imports()
        emailer.send_readme_message()
    elif args.redash:
        run_redash()
    elif args.bootstrap:
        run_bootstrap()
    elif args.weekly:
        run_weekly()
    else:
        run_daily()


if __name__ == "__main__":
    main()


