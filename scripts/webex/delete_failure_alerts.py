#!/usr/bin/env python3
"""Delete CC Pulse workflow-failure alert messages from the Webex room.

Scans the most recent messages in the room and deletes any whose body contains
the failure-alert marker. Run manually (via the "Delete Webex Failure Message"
workflow, or locally with CC_WEBEX_BOT_TOKEN / CC_WEBEX_ROOM_ID exported) to
clean up after a benign/transient failure.

This was previously a base64 blob embedded in the workflow YAML; it lives here
in plaintext so it can be reviewed, diffed, and scanned like any other code.
"""
import json
import os
import sys
import urllib.request

TOKEN = os.environ["CC_WEBEX_BOT_TOKEN"]
ROOM_ID = os.environ["CC_WEBEX_ROOM_ID"]
MARKER = "CC Pulse \u2014 Workflow Failure"

HEADERS = {
    "Authorization": f"Bearer {TOKEN}",
    "Content-Type": "application/json",
}


def main() -> None:
    url = f"https://webexapis.com/v1/messages?roomId={ROOM_ID}&max=50"
    req = urllib.request.Request(url, headers=HEADERS, method="GET")
    with urllib.request.urlopen(req) as r:
        messages = json.loads(r.read())["items"]

    to_delete = [
        m["id"]
        for m in messages
        if MARKER in m.get("markdown", "") or MARKER in m.get("text", "")
    ]

    if not to_delete:
        print("No failure alert messages found.")
        sys.exit(0)

    for msg_id in to_delete:
        del_url = f"https://webexapis.com/v1/messages/{msg_id}"
        req = urllib.request.Request(del_url, headers=HEADERS, method="DELETE")
        with urllib.request.urlopen(req) as r:
            print(f"Deleted message {msg_id} (HTTP {r.status})")

    print(f"Done - deleted {len(to_delete)} message(s).")


if __name__ == "__main__":
    main()
