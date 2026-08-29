import json
import urllib.error
from unittest.mock import patch

import pytest

from scripts.webex import backfill_niap_news


def _write_snapshot(tmp_path, records):
    snapshot = tmp_path / "2026-08-28.json"
    snapshot.write_text(
        json.dumps({"niap": {"news": records}}),
        encoding="utf-8",
    )
    return snapshot


def test_loads_and_annotates_exact_news_id(tmp_path):
    _write_snapshot(tmp_path, [
        {"id": 3428, "title": "Earlier announcement"},
        {
            "id": 3429,
            "title": "NIAP's Second Quarter 2026 Progress Report",
            "posted": "2026-08-28T12:42:46.729000Z",
        },
    ])

    with patch.object(
        backfill_niap_news.differ.config,
        "NEWS_CATEGORY_KEYWORDS",
        {"PUBLICATION": ["progress report"], "NEWS": []},
    ):
        record = backfill_niap_news.load_niap_news_record(
            tmp_path,
            "2026-08-28",
            3429,
        )

    assert record["id"] == 3429
    assert record["_category"] == "PUBLICATION"
    assert record["_change_kind"] == "added"
    assert record["_content_type"] == "news"


def test_missing_or_duplicate_news_id_is_rejected(tmp_path):
    _write_snapshot(tmp_path, [{"id": 3429}, {"id": 3429}])

    with pytest.raises(ValueError, match="expected exactly one"):
        backfill_niap_news.load_niap_news_record(
            tmp_path,
            "2026-08-28",
            3429,
        )


def test_dry_run_never_sends(tmp_path):
    _write_snapshot(tmp_path, [{"id": 3429, "title": "Q2 report"}])

    with patch.object(backfill_niap_news.emailer, "send_niap_news_webex") as send:
        record = backfill_niap_news.replay_news_record(
            tmp_path,
            "2026-08-28",
            3429,
        )

    send.assert_not_called()
    assert record["id"] == 3429


def test_send_requires_webex_confirmation(tmp_path):
    _write_snapshot(tmp_path, [{"id": 3429, "title": "Q2 report"}])

    with (
        patch.object(backfill_niap_news.config, "WEBEX_BOT_TOKEN", "token"),
        patch.object(backfill_niap_news.config, "WEBEX_ROOM_ID", "room"),
        patch.object(
            backfill_niap_news.emailer,
            "send_niap_news_webex",
            return_value=False,
        ),
        pytest.raises(RuntimeError, match="did not confirm"),
    ):
        backfill_niap_news.replay_news_record(
            tmp_path,
            "2026-08-28",
            3429,
            send=True,
        )


def test_niap_sender_returns_delivery_status():
    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

    record = {
        "id": 3429,
        "title": "Q2 report",
        "_change_kind": "added",
        "_content_type": "news",
    }
    with (
        patch.object(backfill_niap_news.emailer.config, "WEBEX_BOT_TOKEN", "token"),
        patch.object(backfill_niap_news.emailer.config, "WEBEX_ROOM_ID", "room"),
        patch.object(
            backfill_niap_news.emailer.urllib.request,
            "urlopen",
            return_value=Response(),
        ),
    ):
        assert backfill_niap_news.emailer.send_niap_news_webex([record]) is True

    with (
        patch.object(backfill_niap_news.emailer.config, "WEBEX_BOT_TOKEN", "token"),
        patch.object(backfill_niap_news.emailer.config, "WEBEX_ROOM_ID", "room"),
        patch.object(
            backfill_niap_news.emailer.urllib.request,
            "urlopen",
            side_effect=urllib.error.URLError("offline"),
        ),
    ):
        assert backfill_niap_news.emailer.send_niap_news_webex([record]) is False
