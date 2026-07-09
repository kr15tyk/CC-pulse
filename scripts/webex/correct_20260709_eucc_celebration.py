#!/usr/bin/env python3
"""One-off correction for the 2026-07-09 mislabeled Cisco cert celebration.

The daily run posted three EUCC certificates through the NIAP-shaped
celebration formatter, producing "Unknown product" cards branded as
"Cisco NDcPP PCL" with links to the NIAP PCL. This script:

  1. Posts the corrected celebration (source="eucc", real product names,
     ENISA certificate links) using the fixed emailer code.
  2. Posts a short correction note so the room knows what happened.
  3. Deletes the bad message (any recent bot message containing
     "Unknown product").

Post-then-delete order: if posting fails, the room still has the original
message rather than nothing.

Run locally with CC_WEBEX_BOT_TOKEN / CC_WEBEX_ROOM_ID exported:

    python scripts/webex/correct_20260709_eucc_celebration.py

Records below are copied verbatim from snapshots/diffs/2026-07-09_diff.json
(eucc.cisco_added).
"""
import json
import os
import sys
import urllib.request

# Repo root on sys.path so emailer/config import like they do from main.py
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import emailer  # noqa: E402

TOKEN = os.environ["CC_WEBEX_BOT_TOKEN"]
ROOM_ID = os.environ["CC_WEBEX_ROOM_ID"]
MARKER = "Unknown product"

HEADERS = {
    "Authorization": f"Bearer {TOKEN}",
    "Content-Type": "application/json",
}

EUCC_CERTS = [
    {
        "name": "Cisco ASR 9000 Series Aggregation Services Routers running IOS-XR 7.11",
        "href": "https://certification.enisa.europa.eu/certificates/eucc-3110-2026-2500100-01_en",
        "cert_date": "2026-04-28T12:00:00Z",
        "description": (
            "The Cisco Cisco ASR 9000 Series Aggregation Services Routers running "
            "IOS-XR 7.11, (ASR9K) is a scalable carrier-class router, which is "
            "designed for redundancy, high security and availability, packaging, "
            "power, and other requirements needed by service providers."
        ),
    },
    {
        "name": "Cisco Nexus 9000 Series Switches, software version Cisco NX-OS version 10.4(5)(M)",
        "href": "https://certification.enisa.europa.eu/certificates/eucc-3110-2025-12-2500098-01_en",
        "cert_date": "2025-12-03T12:00:00Z",
        "description": (
            "The Cisco Nexus 9K Series are data center-class switches for use as "
            "an aggregation switch in the data center. Cisco NX-OS is a "
            "Cisco-developed highly configurable proprietary operating system that "
            "provides for efficient and effective routing and switching."
        ),
    },
    {
        "name": "Cisco Intersight Virtual Appliance 1.0.9 with IMM Fabric 4.3 UCS X-Series Servers and UCS C-Series Servers",
        "href": "https://certification.enisa.europa.eu/certificates/eucc-3110-2025-09-2500093-01_en",
        "cert_date": "2025-09-14T12:00:00Z",
        "description": (
            "The certified product is Cisco Intersight Virtual Appliance 1.0.9 "
            "with IMM Fabric 4.3 UCS X-Series Servers and UCS C-Series Servers."
        ),
    },
]

CORRECTION_NOTE = (
    "ℹ️ **Correction:** this morning's celebration listed three "
    "\"Unknown product\" entries under the NIAP NDcPP PCL. Those were actually "
    "the three **EUCC certificates** above (ENISA) — a formatting bug that "
    "has been fixed. No new NIAP PCL certifications were issued on 2026-07-09."
)


def post_note() -> None:
    payload = json.dumps({"roomId": ROOM_ID, "markdown": CORRECTION_NOTE}).encode()
    req = urllib.request.Request(
        "https://webexapis.com/v1/messages",
        data=payload, headers=HEADERS, method="POST",
    )
    with urllib.request.urlopen(req) as r:
        print(f"Posted correction note (HTTP {r.status})")


def delete_bad_messages() -> None:
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
        print("No bad messages found (nothing contained the marker).")
        return
    for msg_id in to_delete:
        req = urllib.request.Request(
            f"https://webexapis.com/v1/messages/{msg_id}",
            headers=HEADERS, method="DELETE",
        )
        with urllib.request.urlopen(req) as r:
            print(f"Deleted bad message {msg_id} (HTTP {r.status})")


def main() -> None:
    # 1. Corrected celebration via the fixed formatter
    emailer.send_cisco_cert_celebration(EUCC_CERTS, source="eucc")
    print("Posted corrected EUCC celebration.")
    # 2. Correction note
    post_note()
    # 3. Remove the broken post
    delete_bad_messages()


if __name__ == "__main__":
    main()
