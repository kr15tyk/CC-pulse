# One-off processor for the "NATO Cisco Baseline Update" GitHub Issue Form
# (.github/ISSUE_TEMPLATE/nato-cisco-update.yml).
#
# Triggered by .github/workflows/nato_issue_intake.yml whenever a matching
# issue is opened. Parses the manually-copied NATO NIAPCL Cisco product text
# pasted by a human, diffs it against the currently-stored baseline using the
# existing differ.diff_nato() logic, fires the normal Cisco-celebration
# Webex/email alert for any genuinely new listings (reusing emailer.py
# unchanged), updates the stored snapshot, and reports back on the issue.
#
# This script never contacts ia.nato.int itself -- all NATO-site data enters
# the system only via a human manually pasting text they copied from their
# own browser session, per the standing "no automated NATO access" rule.
from __future__ import annotations

import glob
import json
import os
import sys
from datetime import datetime, timezone

# Running as `python scripts/nato_issue_intake.py` only puts this file's own
# directory (scripts/) on sys.path, not the repo root -- add the root
# explicitly so `import config`/`differ`/`emailer` (which live at the repo
# root) work.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
import differ
import emailer

DOMAIN_KEY = "nato"


def _parse_issue_body(body: str) -> dict:
    """Split a GitHub Issue Form body into {field_label: value} pairs."""
    fields = {}
    current = None
    buf = []
    for line in body.splitlines():
        if line.startswith("### "):
            if current is not None:
                fields[current] = "\n".join(buf).strip()
            current = line[4:].strip()
            buf = []
        else:
            buf.append(line)
    if current is not None:
        fields[current] = "\n".join(buf).strip()
    return fields


def _clean(value: str) -> str:
    value = value.strip()
    return "" if value in ("", "_No response_") else value


def _parse_product_text(text: str) -> list:
    """Parse a pasted select-all-and-copy block of the Cisco NIAPCL listing.

    Assumes the site renders each product as two consecutive non-blank lines:
    the product name, followed by its category. This matches the format
    confirmed against the 2026-07-29 manual baseline capture. If the site
    layout differs, the reported-total cross-check below flags a low-
    confidence parse instead of silently committing bad data.
    """
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    products = []
    for i in range(0, len(lines) - 1, 2):
        name, category = lines[i], lines[i + 1]
        products.append({
            "name": name,
            "manufacturer": "Cisco",
            "category": category,
            "link": None,
            "raw_text": name,
        })
    return products


def _load_latest_snapshot(snapshot_dir: str):
    files = sorted(glob.glob(os.path.join(snapshot_dir, "*.json")))
    if not files:
        raise SystemExit(f"No snapshot files found in {snapshot_dir}")
    path = files[-1]
    with open(path, encoding="utf-8") as f:
        return path, json.load(f)


def _write_outputs(applied: bool, summary: str) -> None:
    with open("nato_issue_summary.txt", "w", encoding="utf-8") as f:
        f.write(summary + "\n")
    gh_output = os.environ.get("GITHUB_OUTPUT")
    if gh_output:
        with open(gh_output, "a", encoding="utf-8") as f:
            flag = "true" if applied else "false"
            f.write(f"applied={flag}\n")
    print(summary)


def main() -> None:
    issue_body = os.environ["ISSUE_BODY"]
    fields = _parse_issue_body(issue_body)

    page1 = _clean(fields.get("Page 1 product text", ""))
    page2 = _clean(fields.get("Page 2 product text (if applicable)", ""))
    reported_total = _clean(fields.get("Total product count shown on site", ""))

    parsed = _parse_product_text(page1) + _parse_product_text(page2)

    summary_lines = [f"Parsed **{len(parsed)}** product(s) from the pasted text."]

    if reported_total:
        try:
            expected = int(reported_total)
        except ValueError:
            expected = None
        if expected is not None and expected != len(parsed):
            summary_lines.append(
                f"Site reported {expected} total product(s), but parsing found "
                f"{len(parsed)}. Skipping the automatic baseline update -- please "
                f"review the pasted text for formatting issues and reopen/edit "
                f"this issue, or ask for a manual fix."
            )
            _write_outputs(applied=False, summary="\n\n".join(summary_lines))
            return

    snapshot_dir = config.SNAPSHOT_DIR
    snap_path, full_snapshot = _load_latest_snapshot(snapshot_dir)
    old_nato = full_snapshot.get(DOMAIN_KEY, {"pages": {}, "cisco_products": []})

    new_pages = dict(old_nato.get("pages", {}))
    new_pages["cisco_products"] = parsed
    new_nato = {"pages": new_pages, "cisco_products": parsed}

    diff = differ.diff_nato(old_nato, new_nato)
    cisco_added = diff["cisco_added"]
    cisco_removed = diff["cisco_removed"]
    baseline_reset = diff["baseline_reset"]

    if cisco_added and baseline_reset:
        summary_lines.append(
            f"{len(cisco_added)} product(s) looked new, but this matches the "
            f"pattern of a baseline re-key rather than genuine new listings -- "
            f"celebration alert suppressed."
        )
    elif cisco_added:
        if not config.DRY_RUN:
            emailer.send_cisco_cert_celebration(cisco_added, source="nato")
            emailer.send_cisco_cert_email(cisco_added, source="nato")
        added_lines = [f"- {p.get('name')} ({p.get('category')})" for p in cisco_added]
        summary_lines.append(
            "New Cisco NATO NIAPCL listing(s) detected -- celebration alert sent:\n"
            + "\n".join(added_lines)
        )

    if cisco_removed:
        removed_lines = [f"- {p.get('name')}" for p in cisco_removed]
        summary_lines.append(
            "Removed since last baseline:\n" + "\n".join(removed_lines)
        )

    if not cisco_added and not cisco_removed:
        summary_lines.append("No changes from the current stored baseline.")

    full_snapshot[DOMAIN_KEY] = new_nato
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    out_path = os.path.join(snapshot_dir, f"{today}.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(full_snapshot, f, indent=2, default=str)

    summary_lines.append(
        f"Baseline updated: `{out_path}` now holds {len(parsed)} Cisco product(s)."
    )
    _write_outputs(applied=True, summary="\n\n".join(summary_lines))


if __name__ == "__main__":
    main()
