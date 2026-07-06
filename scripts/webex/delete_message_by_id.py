#!/usr/bin/env python3
"""Delete a single Webex message by its ID.

Unlike delete_failure_alerts.py (which scans the room and deletes anything
matching the failure-alert marker), this deletes one explicit message ID —
useful for removing a specific bad post.

Message ID comes from argv[1] or, failing that, the MESSAGE_ID env var, so it
works both from the "Delete Webex Message (one-time)" workflow and locally with
CC_WEBEX_BOT_TOKEN exported.
"""
import os
import sys
import urllib.request
import urllib.error

TOKEN = os.environ["CC_WEBEX_BOT_TOKEN"]


def main() -> int:
    message_id = (sys.argv[1] if len(sys.argv) > 1 else os.environ.get("MESSAGE_ID", "")).strip()
    if not message_id:
        print("No message ID provided (pass as argument or set MESSAGE_ID).")
        return 2

    req = urllib.request.Request(
        f"https://webexapis.com/v1/messages/{message_id}",
        headers={"Authorization": f"Bearer {TOKEN}"},
        method="DELETE",
    )
    try:
        with urllib.request.urlopen(req) as r:
            print(f"Deleted message {message_id} (HTTP {r.status})")
            return 0
    except urllib.error.HTTPError as e:
        print(f"Failed to delete {message_id}: HTTP {e.code} {e.read().decode()[:200]}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
