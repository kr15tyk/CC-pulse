#!/usr/bin/env python3
"""Post a one-off maintenance notice to the CC Pulse Webex room.

Run manually (via the "Post Webex Maintenance Notice" workflow, or locally with
CC_WEBEX_BOT_TOKEN / CC_WEBEX_ROOM_ID exported) after a known-benign failure
alert so the room isn't left thinking something is broken.

This was previously a base64 blob embedded in the workflow YAML; it lives here
in plaintext so it can be reviewed, diffed, and scanned like any other code.
"""
import json
import os
import urllib.request

TOKEN = os.environ["CC_WEBEX_BOT_TOKEN"]
ROOM_ID = os.environ["CC_WEBEX_ROOM_ID"]

MSG = (
    "\U0001F527 **Maintenance Notice**\n"
    "The failure alert above was generated during routine maintenance on CC "
    "Pulse. No action is needed \u2014 the issue has been resolved and the tool "
    "is running normally. Apologies for the noise!"
)


def main() -> None:
    payload = json.dumps({"roomId": ROOM_ID, "markdown": MSG}).encode()
    req = urllib.request.Request(
        "https://webexapis.com/v1/messages",
        data=payload,
        headers={
            "Authorization": f"Bearer {TOKEN}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req) as r:
        body = json.loads(r.read())
        print(f"Posted message ID: {body['id']}")
        print(f"HTTP {r.status}")


if __name__ == "__main__":
    main()
