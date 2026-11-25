#!/usr/bin/env python3
"""Replay one CC Portal or CSfC record from a trusted daily snapshot.

The command is dry-run by default. ``--send`` posts exactly one selected
record through the normal CC Pulse keyword-alert Webex formatter.
"""
from __future__ import annotations

import argparse
import copy
import json
import re
import sys
from datetime import date
from pathlib import Path
from urllib.parse import urlsplit


REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

import config  # noqa: E402
import emailer  # noqa: E402


MAX_SNAPSHOT_BYTES = 50 * 1024 * 1024
RECORD_KINDS = ("cc-portal-pp", "cc-portal-product", "csfc-component")


def snapshot_date(value: str) -> str:
    """Require canonical YYYY-MM-DD input before constructing a path."""
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("date must use YYYY-MM-DD") from exc
    if parsed.isoformat() != value:
        raise argparse.ArgumentTypeError("date must use canonical YYYY-MM-DD")
    return value


def record_selector(value: str) -> str:
    """Reject control characters and unbounded selectors from workflow input."""
    if not value or len(value) > 200 or any(ord(char) < 32 for char in value):
        raise argparse.ArgumentTypeError(
            "record selector must contain 1-200 printable characters"
        )
    return value


def _normalized_type(value) -> str:
    return re.sub(r"\s+", " ", str(value or "").replace("\xa0", " ")).strip().casefold()


def _csfc_selector(record: dict) -> str:
    href = str(record.get("href") or "")
    match = re.search(
        r"/products/(?:international-product/)?([0-9]+(?:\.[0-9]+)*)(?:/|$)",
        urlsplit(href).path,
        flags=re.IGNORECASE,
    )
    product_id = match.group(1) if match else ""
    component_type = _normalized_type(record.get("type"))
    return f"{product_id}|{component_type}" if product_id and component_type else ""


def _record_identity(kind: str, record: dict) -> str:
    if kind == "cc-portal-pp":
        return str(record.get("id") or record.get("pp_id") or "").strip()
    if kind == "cc-portal-product":
        return str(record.get("id") or record.get("product_id") or "").strip()
    if kind == "csfc-component":
        return _csfc_selector(record)
    raise ValueError(f"unsupported record kind: {kind}")


def _records_for_kind(payload: dict, kind: str) -> list:
    if kind == "cc-portal-pp":
        records = payload.get("cc_portal", {}).get("pps", [])
    elif kind == "cc-portal-product":
        records = payload.get("cc_portal", {}).get("products", [])
    elif kind == "csfc-component":
        records = payload.get("csfc", {}).get("pages", {}).get("apl", [])
    else:
        raise ValueError(f"unsupported record kind: {kind}")
    if not isinstance(records, list):
        raise ValueError(f"snapshot collection for {kind} is not a list")
    return records


def load_source_record(
    snapshot_dir: str | Path,
    selected_date: str,
    kind: str,
    selector: str,
) -> dict:
    """Load exactly one source record from a bounded daily snapshot."""
    if kind not in RECORD_KINDS:
        raise ValueError(f"unsupported record kind: {kind}")
    root = Path(snapshot_dir).resolve()
    snapshot_path = (root / f"{selected_date}.json").resolve()
    if snapshot_path.parent != root:
        raise ValueError("snapshot path escaped the configured snapshot directory")
    if snapshot_path.stat().st_size > MAX_SNAPSHOT_BYTES:
        raise ValueError("snapshot exceeds the replay size limit")

    payload = json.loads(snapshot_path.read_text(encoding="utf-8"))
    records = _records_for_kind(payload, kind)
    normalized_selector = (
        selector.split("|", 1)[0] + "|" + _normalized_type(selector.split("|", 1)[1])
        if kind == "csfc-component" and "|" in selector else selector
    )
    matches = [
        record for record in records
        if isinstance(record, dict)
        and _record_identity(kind, record) == normalized_selector
    ]
    if len(matches) != 1:
        raise ValueError(
            f"expected exactly one {kind} record with selector {selector!r}; "
            f"found {len(matches)}"
        )
    return copy.deepcopy(matches[0])


def _https_url(value) -> str:
    url = str(value or "").strip()
    parsed = urlsplit(url)
    return url if parsed.scheme == "https" and bool(parsed.hostname) else ""


def build_alert(kind: str, record: dict) -> dict:
    """Convert one trusted snapshot record to the normal alert contract."""
    if kind == "cc-portal-pp":
        return {
            "source": "CC Portal Protection Profiles",
            "kind": "new",
            "title": str(record.get("title") or record.get("text") or record.get("id")),
            "url": _https_url(record.get("link")),
            "detail": " · ".join(filter(None, [
                str(record.get("id") or ""),
                str(record.get("scheme") or ""),
                str(record.get("issue_date") or ""),
            ])),
            "matched_keywords": ["Protection Profile"],
            "tab": "intl",
        }
    if kind == "cc-portal-product":
        return {
            "source": "CC Portal Certified Products",
            "kind": "new_cert",
            "title": str(record.get("title") or record.get("name") or record.get("id")),
            "url": _https_url(record.get("link") or record.get("certificate_link")),
            "detail": " · ".join(filter(None, [
                str(record.get("vendor") or ""),
                str(record.get("certificate_date") or ""),
                str(record.get("scheme") or ""),
            ])),
            "matched_keywords": ["Common Criteria"],
            "tab": "intl",
        }
    if kind == "csfc-component":
        return {
            "source": "CSfC Components List",
            "kind": "new",
            "title": str(record.get("text") or "CSfC component"),
            "url": _https_url(record.get("href")),
            "detail": str(record.get("type") or ""),
            "matched_keywords": ["CSfC"],
            "tab": "us",
        }
    raise ValueError(f"unsupported record kind: {kind}")


def replay_source_record(
    snapshot_dir: str | Path,
    selected_date: str,
    kind: str,
    selector: str,
    *,
    send: bool = False,
) -> dict:
    """Validate one stored record and optionally send one Webex alert."""
    record = load_source_record(snapshot_dir, selected_date, kind, selector)
    alert = build_alert(kind, record)
    print(
        f"Selected {kind} record {selector!r} from {selected_date}: "
        f"{alert['title'][:200]!r}"
    )
    print(json.dumps(alert, indent=2, ensure_ascii=False))
    if not send:
        print("Dry run complete; no notification sent.")
        return alert

    if not config.WEBEX_BOT_TOKEN or not config.WEBEX_ROOM_ID:
        raise RuntimeError("Webex bot token and room ID are required for --send")
    if not emailer.send_webex_alert([alert]):
        raise RuntimeError("Webex did not confirm the source-record backfill")
    print("Source-record backfill sent successfully.")
    return alert


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot-dir", required=True)
    parser.add_argument("--date", required=True, type=snapshot_date)
    parser.add_argument("--kind", required=True, choices=RECORD_KINDS)
    parser.add_argument("--selector", required=True, type=record_selector)
    parser.add_argument(
        "--send",
        action="store_true",
        help="Send the selected record; omission performs validation only.",
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    replay_source_record(
        args.snapshot_dir,
        args.date,
        args.kind,
        args.selector,
        send=args.send,
    )


if __name__ == "__main__":
    main()
