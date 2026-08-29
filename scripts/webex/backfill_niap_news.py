#!/usr/bin/env python3
"""Replay one NIAP announcement from a trusted CC Pulse snapshot.

The command is dry-run by default. ``--send`` uses the same formatter and
Webex destination as the daily NIAP content notification path.
"""
from __future__ import annotations

import argparse
import copy
import json
import sys
from datetime import date
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

import config  # noqa: E402
import differ  # noqa: E402
import emailer  # noqa: E402


def snapshot_date(value: str) -> str:
    """Require canonical YYYY-MM-DD input before constructing a path."""
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("date must use YYYY-MM-DD") from exc
    if parsed.isoformat() != value:
        raise argparse.ArgumentTypeError("date must use canonical YYYY-MM-DD")
    return value


def positive_news_id(value: str) -> int:
    """Require a positive integer NIAP announcement ID."""
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("news ID must be an integer") from exc
    if parsed <= 0:
        raise argparse.ArgumentTypeError("news ID must be positive")
    return parsed


def load_niap_news_record(
    snapshot_dir: str | Path,
    selected_date: str,
    news_id: int,
) -> dict:
    """Load and annotate exactly one NIAP news record from a daily snapshot."""
    root = Path(snapshot_dir).resolve()
    snapshot_path = (root / f"{selected_date}.json").resolve()
    if snapshot_path.parent != root:
        raise ValueError("snapshot path escaped the configured snapshot directory")

    payload = json.loads(snapshot_path.read_text(encoding="utf-8"))
    records = payload.get("niap", {}).get("news", [])
    if not isinstance(records, list):
        raise ValueError("snapshot niap.news value is not a list")

    matches = [
        item for item in records
        if isinstance(item, dict) and str(item.get("id")) == str(news_id)
    ]
    if len(matches) != 1:
        raise ValueError(
            f"expected exactly one NIAP news record with ID {news_id}; "
            f"found {len(matches)}"
        )

    record = copy.deepcopy(matches[0])
    record["_category"] = differ.categorize_news(record.get("title", ""))
    record["_change_kind"] = "added"
    record["_content_type"] = "news"
    return record


def replay_news_record(
    snapshot_dir: str | Path,
    selected_date: str,
    news_id: int,
    *,
    send: bool = False,
) -> dict:
    """Validate one stored announcement and optionally send it to Webex."""
    record = load_niap_news_record(snapshot_dir, selected_date, news_id)
    title = str(record.get("title") or "NIAP content")
    print(
        f"Selected NIAP news ID {news_id} from {selected_date}: "
        f"{title[:200]!r}"
    )
    if not send:
        print("Dry run complete; no notification sent.")
        return record

    if not config.WEBEX_BOT_TOKEN or not config.WEBEX_ROOM_ID:
        raise RuntimeError("Webex bot token and room ID are required for --send")
    if not emailer.send_niap_news_webex([record]):
        raise RuntimeError("Webex did not confirm the NIAP backfill notification")
    print("NIAP backfill notification sent successfully.")
    return record


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot-dir", required=True)
    parser.add_argument("--date", required=True, type=snapshot_date)
    parser.add_argument("--news-id", required=True, type=positive_news_id)
    parser.add_argument(
        "--send",
        action="store_true",
        help="Send the selected record; omission performs validation only.",
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    replay_news_record(
        args.snapshot_dir,
        args.date,
        args.news_id,
        send=args.send,
    )


if __name__ == "__main__":
    main()
