import json
from unittest.mock import patch

import pytest

from scripts.webex import backfill_source_record


def _write_snapshot(tmp_path):
    payload = {
        "cc_portal": {
            "pps": [{
                "id": "BSI-CC-PP-0105-V4-2026",
                "title": "SMAERS Protection Profile",
                "link": "https://www.commoncriteriaportal.org/pp.pdf",
                "scheme": "DE",
                "issue_date": "2026-06-01",
            }],
            "products": [{
                "id": "2026.0123",
                "title": "Secure Router",
                "vendor": "Vendor A",
                "certificate_date": "2026-08-01",
                "link": "https://www.commoncriteriaportal.org/report.pdf",
            }],
        },
        "csfc": {"pages": {"apl": [{
            "href": "https://www.niap-ccevs.org/products/international-product/2026.1096",
            "type": "IPsec\u00a0 VPN Client",
            "text": "Ad Noctem Connect",
        }] }},
    }
    snapshot = tmp_path / "2026-08-29.json"
    snapshot.write_text(json.dumps(payload), encoding="utf-8")
    return snapshot


@pytest.mark.parametrize(
    ("kind", "selector", "expected_title"),
    [
        ("cc-portal-pp", "BSI-CC-PP-0105-V4-2026", "SMAERS Protection Profile"),
        ("cc-portal-product", "2026.0123", "Secure Router"),
        ("csfc-component", "2026.1096|IPsec VPN Client", "Ad Noctem Connect"),
    ],
)
def test_loads_exact_supported_record(tmp_path, kind, selector, expected_title):
    _write_snapshot(tmp_path)

    record = backfill_source_record.load_source_record(
        tmp_path, "2026-08-29", kind, selector
    )
    alert = backfill_source_record.build_alert(kind, record)

    assert alert["title"] == expected_title
    assert alert["url"].startswith("https://")


def test_missing_or_duplicate_selector_is_rejected(tmp_path):
    snapshot = _write_snapshot(tmp_path)
    payload = json.loads(snapshot.read_text())
    payload["cc_portal"]["products"].append(
        dict(payload["cc_portal"]["products"][0])
    )
    snapshot.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="expected exactly one"):
        backfill_source_record.load_source_record(
            tmp_path, "2026-08-29", "cc-portal-product", "2026.0123"
        )


def test_dry_run_never_sends(tmp_path):
    _write_snapshot(tmp_path)

    with patch.object(backfill_source_record.emailer, "send_webex_alert") as send:
        alert = backfill_source_record.replay_source_record(
            tmp_path,
            "2026-08-29",
            "csfc-component",
            "2026.1096|IPsec VPN Client",
        )

    send.assert_not_called()
    assert alert["source"] == "CSfC Components List"


def test_send_requires_confirmed_webex_delivery(tmp_path):
    _write_snapshot(tmp_path)

    with (
        patch.object(backfill_source_record.config, "WEBEX_BOT_TOKEN", "token"),
        patch.object(backfill_source_record.config, "WEBEX_ROOM_ID", "room"),
        patch.object(
            backfill_source_record.emailer,
            "send_webex_alert",
            return_value=False,
        ),
        pytest.raises(RuntimeError, match="did not confirm"),
    ):
        backfill_source_record.replay_source_record(
            tmp_path,
            "2026-08-29",
            "cc-portal-pp",
            "BSI-CC-PP-0105-V4-2026",
            send=True,
        )


def test_http_url_from_snapshot_is_not_rendered_as_clickable(tmp_path):
    snapshot = _write_snapshot(tmp_path)
    payload = json.loads(snapshot.read_text())
    payload["cc_portal"]["products"][0]["link"] = "http://unsafe.test/report.pdf"
    snapshot.write_text(json.dumps(payload), encoding="utf-8")

    record = backfill_source_record.load_source_record(
        tmp_path, "2026-08-29", "cc-portal-product", "2026.0123"
    )

    assert backfill_source_record.build_alert("cc-portal-product", record)["url"] == ""
