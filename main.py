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
- Matrix merge mode: assemble full snapshot from per-domain partial JSONs (issue #20)

Usage:
  python main.py                  # Daily pulse check
  python main.py --weekly         # Send weekly email from stored daily diffs
  python main.py --bootstrap      # Collect initial snapshot (no diff)
  python main.py --merge          # Assemble snapshot from snapshots/partial/*.json then diff/alert
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

# ── Logging setup ─────────────────────────────────────────────────────────────────────────────
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

# ── Lazy imports (after logging is configured) ────────────────────────────────────────────
def _imports():
    import collector
    import differ
    import dashboard
    import emailer
    return collector, differ, dashboard, emailer

# ── Path helpers ───────────────────────────────────────────────────────────────────────────────
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

# ── Snapshot rotation (fix #4) ──────────────────────────────────────────────────────────────────────
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

# ── Empty baseline snapshot (fix #2) ──────────────────────────────────────────────────────────────────
def _empty_snapshot() -> dict:
    """Return a structurally complete empty snapshot for first-run diffing."""
    return {
        "schema_version": config.SNAPSHOT_SCHEMA_VERSION,
        "collected_at": "",
        "niap": {"pcl": [], "pps": [], "tds": [], "events": [], "news": []},  # fix #26: removed dead "cctls" key
        "cc_portal": {"news": [], "pps": [], "products": [], "communities": [], "publications": [], "pp_rss": []},
        "cctl_labs": {},
        "csfc":      {"pages": {}, "capability_package_headers": {}, "feeds": {}},
        "cc_crypto": {"pages": {}},
        "nist":      {"pages": {}, "cmvp_mip": {"added": [], "removed": [], "status_changes": []}, "feeds": {}},
        "nato":      {"pages": {}, "cisco_added": [], "cisco_removed": []},
        "eucc":      {"pages": {}, "cisco_added": [], "cisco_removed": []},
    }

# ── Shared alert-firing logic ──────────────────────────────────────────────────────────────────────────
def _fire_alerts(diff: dict, emailer) -> None:
    """Send all notifications derived from a completed diff.

    Shared between run_daily() and run_merge() to avoid duplication.
    """
    if config.DRY_RUN:
        alerts = diff.get("alerts", [])
        log.info("[DRY RUN] Notifications suppressed. Would have fired: %d alert(s), %d new TD(s), %d new Cisco cert(s).",
                 len(alerts),
                 len(diff.get("niap", {}).get("tds", {}).get("added", [])),
                 len(diff.get("niap", {}).get("cisco_ndcpp", {}).get("added", [])))
        return
    alerts = diff.get("alerts", [])
    if alerts:
        log.warning("%d keyword alert(s) — firing notifications...", len(alerts))
        emailer.send_webex_alert(alerts)
        emailer.send_webhook_alert(alerts)
        log.warning("Sending immediate alert email...")
        emailer.send_alert_email(alerts)
    else:
        log.info("No keyword alerts.")

    new_tds = diff.get("niap", {}).get("tds", {}).get("added", [])
    if new_tds:
        log.info("%d new NIAP TD(s) — sending Webex notification...", len(new_tds))
        emailer.send_new_tds_webex(new_tds)

    new_cisco_certs = diff.get("niap", {}).get("cisco_ndcpp", {}).get("added", [])
    if new_cisco_certs:
        log.info("%d new Cisco NDcPP certification(s) — sending celebration...", len(new_cisco_certs))
        emailer.send_cisco_cert_celebration(new_cisco_certs)
        emailer.send_cisco_cert_email(new_cisco_certs)

    new_cisco_csfc_alerts = [
        a for a in diff.get("alerts", [])
        if "CSfC" in a.get("source", "") and
        any(kw in a.get("title", "").lower() for kw in config.CISCO_VENDOR_KEYWORDS)
    ]
    if new_cisco_csfc_alerts:
        log.info("%d new Cisco CSfC alert(s) — sending celebration...", len(new_cisco_csfc_alerts))
        emailer.send_cisco_cert_celebration(new_cisco_csfc_alerts)
        emailer.send_cisco_cert_email(new_cisco_csfc_alerts)

    new_cisco_nato = diff.get("nato", {}).get("cisco_added", [])
    if new_cisco_nato:
        log.info("%d new Cisco NATO NIAPCL listing(s) — sending celebration...", len(new_cisco_nato))
        emailer.send_cisco_cert_celebration(new_cisco_nato)
        emailer.send_cisco_cert_email(new_cisco_nato)

    new_cisco_eucc = diff.get("eucc", {}).get("cisco_added", [])
    if new_cisco_eucc:
        log.info("%d new Cisco EUCC certification(s) — sending celebration...", len(new_cisco_eucc))
        emailer.send_cisco_cert_celebration(new_cisco_eucc)
        emailer.send_cisco_cert_email(new_cisco_eucc)

    # -- NIAP PP changes (new PPs, sunset changes) --------------------------------
    new_pps = diff.get("niap", {}).get("pps", {}).get("added", [])
    pp_sunsets = diff.get("niap", {}).get("pps", {}).get("sunset_changes", [])
    if new_pps or pp_sunsets:
        log.info(
            "%d new PP(s), %d PP sunset change(s) — sending Webex notification...",
            len(new_pps), len(pp_sunsets),
        )
        emailer.send_new_pps_webex(new_pps, pp_sunsets)

    # -- NIAP News items (new announcements) -----------------------------------
    new_news = diff.get("niap", {}).get("news", {}).get("added", [])
    if new_news:
        log.info("%d new NIAP news item(s) — sending Webex notification...", len(new_news))
        emailer.send_niap_news_webex(new_news)

    # -- NIST CMVP MIP changes (fix #27) ------------------------------------------
    cmvp_mip = diff.get("nist", {}).get("cmvp_mip", {})
    if cmvp_mip.get("added") or cmvp_mip.get("status_changes"):
        log.info(
            "%d CMVP MIP addition(s), %d status change(s) — sending Webex notification...",
            len(cmvp_mip.get("added", [])), len(cmvp_mip.get("status_changes", [])),
        )
        emailer.send_nist_cmvp_webex(cmvp_mip)

        # -- Daily status heartbeat (always fires unless DRY_RUN) ----------------
    emailer.send_daily_status_email(diff)

# ── Run modes ─────────────────────────────────────────────────────────────────────────────────────
def run_daily(output_dir: str = None) -> None:
    """Collect, diff, dashboard, alert (Webex + immediate email on alerts)."""
    _setup_logging()
    collector, differ, dashboard, emailer = _imports()

    log.info("=" * 55)
    log.info("CC Pulse daily run — %s", datetime.now(timezone.utc).isoformat())
    log.info("=" * 55)

    today_path = snapshot_path()
    if os.path.exists(today_path):
        log.warning(
            "Today's snapshot already exists at %s — skipping collection "
            "to avoid duplicate diff. Delete it manually to force a re-run.",
            today_path,
        )
        sys.exit(0)

    try:
        new_snap = collector.collect_all()
    except collector.SanityError as exc:
        log.error("Snapshot rejected by sanity check: %s", exc)
        log.error("Aborting — no files written.")
        sys.exit(1)
    _save_json(new_snap, today_path)

    prior_path = _latest_prior_snapshot()
    first_run = prior_path is None
    if first_run:
        log.warning("No prior snapshot found — diff will be empty (first run). Alerts suppressed.")
        old_snap = _empty_snapshot()
    else:
        log.info("Diffing against: %s", prior_path)
        old_snap = _load_json(prior_path)

    diff = differ.compute_diff(old_snap, new_snap)
    if first_run:
        log.warning("First run: suppressing all changes from diff (baseline snapshot, not a real diff).")
        def _clear_lists(obj):
            if isinstance(obj, list):
                return []
            if isinstance(obj, dict):
                return {k: _clear_lists(v) for k, v in obj.items()}
            return obj
        for section in ("niap", "cc_portal", "cctl_labs", "csfc", "cc_crypto", "nist", "nato", "eucc"):  # fix #23
            if section in diff:
                diff[section] = _clear_lists(diff[section])
        diff["alerts"] = []
    _save_json(diff, diff_path())

    _rotate_old_files()
    dashboard.render_dashboard(diff, output_dir=output_dir or config.DASHBOARD_DIR)
    _fire_alerts(diff, emailer)
    log.info("Daily run complete.")


def run_merge(partial_dir: str = "snapshots/partial", output_dir: str = None) -> None:
    """Assemble a full snapshot from per-domain partial JSONs, then diff and alert.

    This is the merge step for the GitHub Actions matrix workflow (issue #20).
    Each matrix job runs `python collector.py --domain <name>` which writes
    `snapshots/partial/<name>.json`.  Once all matrix jobs complete, the
    downstream merge-and-notify job calls `python main.py --merge` to:

    1. Read every <partial_dir>/<domain>.json file.
    2. Assemble them into a full snapshot (same structure as collect_all()).
    3. Validate with collector.validate_snapshot().
    4. Write the full snapshot to snapshots/YYYY-MM-DD.json.
    5. Diff, render dashboard, fire alerts.

    Missing domain files are tolerated with an empty fallback so a single
    slow/failed matrix job does not block the whole pipeline.  The sanity
    check in step 3 still catches critically broken snapshots.
    """
    _setup_logging()
    collector, differ, dashboard, emailer = _imports()

    log.info("=" * 55)
    log.info("CC Pulse merge run — %s", datetime.now(timezone.utc).isoformat())
    log.info("=" * 55)

    today_path = snapshot_path()
    if os.path.exists(today_path):
        log.warning(
            "Today's snapshot already exists at %s — skipping merge "
            "to avoid duplicate diff.",
            today_path,
        )
        sys.exit(0)

    domains = list(collector.DOMAIN_COLLECTORS.keys())
    domain_data: dict = {}
    missing: list = []
    for domain in domains:
        path = os.path.join(partial_dir, f"{domain}.json")
        if os.path.exists(path):
            try:
                domain_data[domain] = _load_json(path)
                log.info("[Merge] Loaded partial: %s (%d bytes)", path, os.path.getsize(path))
            except Exception as exc:
                log.error("[Merge] Failed to load %s: %s — using empty fallback.", path, exc)
                domain_data[domain] = {}
                missing.append(domain)
        else:
            log.warning("[Merge] Partial file not found: %s — using empty fallback.", path)
            domain_data[domain] = {}
            missing.append(domain)

    if missing:
        log.warning("[Merge] %d domain(s) missing/failed: %s", len(missing), missing)

    new_snap = {
        "schema_version": config.SNAPSHOT_SCHEMA_VERSION,
        "collected_at": datetime.now(timezone.utc).isoformat(),
        "niap":      domain_data.get("niap", {}),
        "cc_portal": domain_data.get("cc_portal", {}),
        "cctl_labs": domain_data.get("cctl_labs", {}),
        "csfc":      domain_data.get("csfc", {}),
        "cc_crypto": domain_data.get("cc_crypto", {}),
        "nist":      domain_data.get("nist", {}),
        "nato":      domain_data.get("nato", {}),
        "eucc":      domain_data.get("eucc", {}),
    }

    try:
        collector.validate_snapshot(new_snap)
    except collector.SanityError as exc:
        log.error("[Merge] Snapshot failed sanity check: %s", exc)
        log.error("[Merge] Aborting — no files written.")
        sys.exit(1)

    _save_json(new_snap, today_path)

    prior_path = _latest_prior_snapshot()
    first_run = prior_path is None
    if first_run:
        log.warning("[Merge] No prior snapshot — diff suppressed (first run).")
        old_snap = _empty_snapshot()
    else:
        log.info("[Merge] Diffing against: %s", prior_path)
        old_snap = _load_json(prior_path)

    diff = differ.compute_diff(old_snap, new_snap)
    if first_run:
        def _clear_lists(obj):  # fix #23
            if isinstance(obj, list):
                return []
            if isinstance(obj, dict):
                return {k: _clear_lists(v) for k, v in obj.items()}
            return obj
        for section in ("niap", "cc_portal", "cctl_labs", "csfc", "cc_crypto", "nist", "nato", "eucc"):
            if section in diff:
                diff[section] = _clear_lists(diff[section])
        diff["alerts"] = []
    _save_json(diff, diff_path())

    _rotate_old_files()
    dashboard.render_dashboard(diff, output_dir=output_dir or config.DASHBOARD_DIR)
    _fire_alerts(diff, emailer)
    log.info("[Merge] Merge run complete.")


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

    window = files[-7:]
    log.info(
        "Merging %d daily diff(s): %s ... %s",
        len(window),
        os.path.basename(window[0]),
        os.path.basename(window[-1]),
    )
    diffs = [_load_json(f) for f in window]

    weekly = differ.merge_weekly_diffs([copy.deepcopy(d) for d in diffs])
    if config.DRY_RUN:
        log.info("[DRY RUN] Weekly notifications suppressed. %d alert(s) in weekly digest.",
                 len(weekly.get("alerts", [])))
    else:
        emailer.send_weekly_email(weekly)
        emailer.send_webex_alert(weekly.get("alerts", []))
        emailer.send_webhook_alert(weekly.get("alerts", []))
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

def run_redash(output_dir: str = None) -> None:
    """Re-render the dashboard HTML from the latest stored diff file."""
    _imports()
    import dashboard as dash_mod
    import glob as _glob

    diffs = sorted(_glob.glob(os.path.join(config.DIFF_DIR, "*_diff.json")))
    if not diffs:
        log.error("No diff files found in %s -- run daily mode first.", config.DIFF_DIR)
        sys.exit(1)

    dest = output_dir or config.DASHBOARD_DIR
    latest = diffs[-1]
    log.info("[Redash] Loading diff from %s", latest)
    diff = _load_json(latest)
    dash_mod.render_dashboard(diff, output_dir=dest)
    log.info("[Redash] Dashboard re-rendered to %s from %s", dest, latest)

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
    parser.add_argument(
        "--staging",
        action="store_true",
        help="Re-render dashboard from the latest stored diff into docs/staging/ (private test dashboard)",
    )
    parser.add_argument(
        "--merge",
        action="store_true",
        help=(
            "Assemble full snapshot from snapshots/partial/*.json (written by "
            "matrix collect jobs), then diff, render dashboard, and send alerts. "
            "Used by the merge-and-notify Actions job."
        ),
    )
    args = parser.parse_args()

    if args.readme:
        _setup_logging()
        _, _, _, emailer = _imports()
        emailer.send_readme_message()
    elif args.staging:
        run_redash(output_dir=config.STAGING_DIR)
    elif args.redash:
        run_redash()
    elif args.bootstrap:
        run_bootstrap()
    elif args.weekly:
        run_weekly()
    elif args.merge:
        run_merge()
    else:
        run_daily()

if __name__ == "__main__":
    main()
