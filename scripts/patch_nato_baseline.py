# One-time patch: correct the NATO NIAPCL Cisco baseline snapshot.
#
# Context: automated collection of ia.nato.int has been blocked (403) since
# early July, so the stored snapshot only ever captured page 1 of the
# Cisco-filtered product search (30 of the real 40 products) before the
# block took hold. The user manually confirmed the complete 40-product
# list on 2026-07-29 by visiting the site directly and copying the
# rendered page text/HTML themselves (no automated access to NATO was
# used to gather this data). This script appends the 10 missing products
# to both nato.pages.all_products and nato.cisco_products in today's
# stored snapshot, so this becomes the new baseline for future diffing.
#
# This is a one-off correction, not part of the regular daily pipeline.
# Delete this script and its companion workflow after running it once.
import json
import os

SNAPSHOT_PATH = os.path.join("snapshot-store", "2026-07-29.json")

NEW_PRODUCTS = [
    {
        "name": "Cisco Aggregation Services Router 9000 (ASR9K) (IOS-XR 7.11)",
        "manufacturer": "Cisco Systems",
        "category": "Network Management , Communications Encryption",
        "link": None,
        "raw_text": "Cisco Aggregation Services Router 9000 (ASR9K) (IOS-XR 7.11)",
    },
    {
        "name": "Cisco Catalyst 9000X/CX Series Switches IOS-XE 17.15",
        "manufacturer": "Cisco Systems",
        "category": "Network Management , Communications Encryption , Network Security Management",
        "link": None,
        "raw_text": "Cisco Catalyst 9000X/CX Series Switches IOS-XE 17.15",
    },
    {
        "name": "Cisco Catalyst 9200/9200L Series Switches (IOS-XE17.15)",
        "manufacturer": "Cisco Systems",
        "category": "Network Management , Communications Encryption",
        "link": None,
        "raw_text": "Cisco Catalyst 9200/9200L Series Switches (IOS-XE17.15)",
    },
    {
        "name": "Cisco Firepower 1000 Series (running ASA 9.20)",
        "manufacturer": "Cisco Systems",
        "category": "Firewall and Mailguard , Communications Encryption , Intrusion Detection and Prevention , VPN (Virtual Private Network)",
        "link": None,
        "raw_text": "Cisco Firepower 1000 Series (running ASA 9.20)",
    },
    {
        "name": "Cisco Firepower 2100 Series (running ASA 9.20)",
        "manufacturer": "Cisco Systems",
        "category": "Firewall and Mailguard , Communications Encryption , Intrusion Detection and Prevention , VPN (Virtual Private Network)",
        "link": None,
        "raw_text": "Cisco Firepower 2100 Series (running ASA 9.20)",
    },
    {
        "name": "Cisco Firepower 4100 Security Appliances (running ASA 9.20)",
        "manufacturer": "Cisco Systems",
        "category": "Firewall and Mailguard",
        "link": None,
        "raw_text": "Cisco Firepower 4100 Security Appliances (running ASA 9.20)",
    },
    {
        "name": "Cisco Firepower 9300 Security Appliances (running ASA 9.20)",
        "manufacturer": "Cisco Systems",
        "category": "Firewall and Mailguard , Communications Encryption , Intrusion Detection and Prevention , VPN (Virtual Private Network)",
        "link": None,
        "raw_text": "Cisco Firepower 9300 Security Appliances (running ASA 9.20)",
    },
    {
        "name": "Cisco Secure Firewall 3100 Series (Running ASA 9.20)",
        "manufacturer": "Cisco Systems",
        "category": "Firewall and Mailguard , Communications Encryption , VPN (Virtual Private Network)",
        "link": None,
        "raw_text": "Cisco Secure Firewall 3100 Series (Running ASA 9.20)",
    },
    {
        "name": "Cisco Secure Firewall 4200 Series (running ASA 9.20)",
        "manufacturer": "Cisco Systems",
        "category": "Firewall and Mailguard , Communications Encryption , Intrusion Detection and Prevention , VPN (Virtual Private Network)",
        "link": None,
        "raw_text": "Cisco Secure Firewall 4200 Series (running ASA 9.20)",
    },
    {
        "name": "The Cisco Catalyst C8000 Series Edge Routers (IOS-XE 17.18)",
        "manufacturer": "Cisco Systems",
        "category": "Network Management",
        "link": None,
        "raw_text": "The Cisco Catalyst C8000 Series Edge Routers (IOS-XE 17.18)",
    },
]


def main():
    with open(SNAPSHOT_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)

    nato = data.setdefault("nato", {})
    pages = nato.setdefault("pages", {})
    all_products = pages.setdefault("all_products", [])
    cisco_products = nato.setdefault("cisco_products", [])

    existing_names = {p.get("name") for p in all_products}
    added = 0
    for product in NEW_PRODUCTS:
        if product["name"] in existing_names:
            continue
        all_products.append(product)
        cisco_products.append(product)
        added += 1

    print(f"Added {added} new NATO Cisco product(s). "
          f"all_products now has {len(all_products)}, "
          f"cisco_products now has {len(cisco_products)}.")

    with open(SNAPSHOT_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
        f.write("\n")


if __name__ == "__main__":
    main()
