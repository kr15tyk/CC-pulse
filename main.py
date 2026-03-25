"""
main.py - Entry point for CC Pulse. Orchestrates collection, diffing, dashboard, and email.

Usage:
    python main.py              # Run daily pulse check
    python main.py --weekly     # Force send weekly email digest
    python main.py --bootstrap  # Collect initial snapshot (no diff)
"""

import argparse
import glob
import json
import os
import sys
from datetime import datetime, timezone

import config
import collector
import differ
import dashboard
import emailer


def snapshot_path(dt=None):
    if dt is None:
        dt = datetime.now(timezone.utc)
    os.makedirs(config.SNAPSHOT_DIR, exist_ok=True)
    return os.path.join(config.SNAPSHOT_DIR, dt.strftime("%Y-%m-%d") + ".json")


def load_snapshot(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_snapshot(snap, path):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(snap, f, indent=2, default=str)
    print(f"[Main] Snapshot saved: {path}")


def latest_prior_snapshot():
    """Return the most recent snapshot file that is not today's."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    pattern = os.path.join(config.SNAPSHOT_DIR, "*.json")
    files = sorted(glob.glob(pattern), reverse=True)
    for f in files:
        if today not in f:
            return f
    return None


def run_daily():
    print("=" * 60)
    print(f"[Main] CC Pulse daily run - {datetime.now(timezone.utc).isoformat()}")
    print("=" * 60)

    # 1. Collect today's snapshot
    new_snap = collector.collect_all()
    today_path = snapshot_path()
    save_snapshot(new_snap, today_path)

    # 2. Find prior snapshot for diff
    prior_path = latest_prior_snapshot()
    if prior_path is None:
        print("[Main] No prior snapshot found. Dashboard will show empty diff.")
        print("[Main] Run again tomorrow to get your first diff.")
        old_snap = {"niap": {}, "cc_portal": {}, "cctl_labs": {}}
    else:
        print(f"[Main] Diffing against: {prior_path}")
        old_snap = load_snapshot(prior_path)

    # 3. Compute diff
    diff = differ.compute_diff(old_snap, new_snap)

    # 4. Render dashboard
    dashboard.render_dashboard(diff)

    print("[Main] Daily run complete.")
    return diff


def run_weekly():
    print("[Main] Building weekly digest...")
    pattern = os.path.join(config.SNAPSHOT_DIR, "*.json")
    files   = sorted(glob.glob(pattern))

    if len(files) < 2:
        print("[Main] Not enough snapshots for weekly diff. Run daily first.")
        return

    # Use last 7 snapshots (or fewer)
    window = files[-8:]
    diffs  = []
    for i in range(1, len(window)):
        old = load_snapshot(window[i - 1])
        new = load_snapshot(window[i])
        diffs.append(differ.compute_diff(old, new))

    weekly = differ.merge_weekly_diffs(diffs)
    emailer.send_weekly_email(weekly)
    print("[Main] Weekly email sent.")


def run_bootstrap():
    print("[Main] Bootstrap: collecting initial snapshot (no diff).")
    snap = collector.collect_all()
    path = snapshot_path()
    save_snapshot(snap, path)
    print(f"[Main] Bootstrap complete. Snapshot at {path}")


def main():
    parser = argparse.ArgumentParser(description="CC Pulse - Common Criteria monitoring engine")
    parser.add_argument("--weekly",    action="store_true", help="Send weekly email digest")
    parser.add_argument("--bootstrap", action="store_true", help="Collect initial snapshot only")
    args = parser.parse_args()

    if args.bootstrap:
        run_bootstrap()
    elif args.weekly:
        run_weekly()
    else:
        run_daily()


if __name__ == "__main__":
    main()
