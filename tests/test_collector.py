"""
tests/test_collector.py — Unit tests for collector.py.

Covers:
- validate_snapshot(): SanityError raised below thresholds; passes above
- _headers_changed(): delegated to differ — tested separately
- _partial_get_hash(): mocked HTTP responses
- collect_csfc() fix #24: APL soup fetched once, passed to both parsers
- _scrape_csfc_page_from_soup(): new helper introduced by fix #24
- get_html() / get_json(): retry logic and None-on-failure contract
"""
import sys
import hashlib
from unittest.mock import MagicMock, patch, call
import pytest

# ---------------------------------------------------------------------------
# Config stub
# ---------------------------------------------------------------------------
_cfg = MagicMock()
_cfg.RETRY_ATTEMPTS = 3
_cfg.RETRY_BACKOFF_BASE = 2
_cfg.SANITY_MIN_PCL = 50
_cfg.SANITY_MIN_PPS = 10
_cfg.SANITY_MIN_CSFC_APL = 5
_cfg.SANITY_MIN_CSFC_ANNOUNCEMENTS = 1
_cfg.SANITY_MIN_CC_CRYPTO_PUBS = 5
_cfg.SANITY_MIN_NIST_NEWS = 10
_cfg.CSFC_BASE = "https://www.nsa.gov"
_cfg.CSFC_PAGES = {
    "home": "/csfc/",
    "apl": "/csfc/components/",
}
_cfg.CSFC_APL_COMPONENT_KEYWORDS = {"TLS/VPN": ["tls", "vpn"]}
_cfg.CSFC_COMPONENT_SELECTIONS = {}
_cfg.CSFC_FEEDS = []
_cfg.NIAP_BASE = "https://www.niap-ccevs.org"
_cfg.NIST_CSRC_BASE = "https://csrc.nist.gov"
_cfg.EUCC_BASE = "https://certification.enisa.europa.eu"
sys.modules["config"] = _cfg
# Also stub heavy deps
for _mod in ("requests", "feedparser", "bs4", "lxml"):
    sys.modules.setdefault(_mod, MagicMock())

import collector  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _snap(pcl_count=60, pps_count=15):
    pcl = [{"product_id": i} for i in range(pcl_count)]
    pps = [{"pp_id": str(i)} for i in range(pps_count)]
    return {
        "niap": {"pcl": pcl, "pps": pps},
        "csfc": {"pages": {"apl": [], "announcements": []}, "component_selection_hashes": {}},
        "cc_crypto": {"pages": {"publications": [{"text": f"pub{i}", "href": ""} for i in range(6)]}},
        "nist": {"pages": {"news": [{"text": f"news{i}"} for i in range(12)]}},
    }


# ===========================================================================
# NIAP paginated feeds and policy records
# ===========================================================================

class TestNiapCollections:

    def test_paginated_results_follow_every_page(self):
        first = {
            "count": 3,
            "next": "http://www.niap-ccevs.org/api/items/?offset=2",
            "results": [{"id": 1}, {"id": 2}],
        }
        second = {"count": 3, "next": None, "results": [{"id": 3}]}
        with patch.object(collector, "get_json", side_effect=[first, second]) as get_json:
            items, health = collector._get_paginated_results(
                "https://www.niap-ccevs.org/api/items/"
            )

        assert [item["id"] for item in items] == [1, 2, 3]
        assert health["success"] is True
        assert health["complete"] is True
        assert health["pages"] == 2
        assert get_json.call_args_list[1].args[0].startswith("https://")

    def test_paginated_results_reject_partial_failure(self):
        first = {
            "count": 3,
            "next": "https://www.niap-ccevs.org/api/items/?offset=2",
            "results": [{"id": 1}, {"id": 2}],
        }
        with patch.object(collector, "get_json", side_effect=[first, None]):
            items, health = collector._get_paginated_results(
                "https://www.niap-ccevs.org/api/items/"
            )

        assert len(items) == 2
        assert health["success"] is False
        assert health["complete"] is False

    def test_policy_record_includes_parent_and_addendum_urls(self):
        record = {
            "policy_id": 30,
            "policy_num": 30,
            "filename": "policy-30.pdf",
            "addendums": [{"addendum_num": 1, "filename": "policy-30-add1.pdf"}],
        }

        policy = collector._policy_record(record, archived=False)

        assert policy["archived"] is False
        assert policy["url"].endswith("/Policy/policy-30.pdf")
        assert policy["addendums"][0]["url"].endswith("/Policy/policy-30-add1.pdf")

    def test_policy_document_hashes_cover_parent_and_addendum(self):
        policies = [{
            "url": "https://example.test/policy.pdf",
            "addendums": [{"url": "https://example.test/addendum.pdf"}],
        }]
        with patch.object(collector, "_hash_policy_document", side_effect=["aaa", "bbb"]):
            health = collector._attach_policy_document_hashes(policies)

        assert health["complete"] is True
        assert health["hashed_documents"] == 2
        assert policies[0]["document_sha256"] in ("aaa", "bbb")
        assert policies[0]["addendums"][0]["document_sha256"] in ("aaa", "bbb")
        assert policies[0]["document_sha256"] != policies[0]["addendums"][0]["document_sha256"]


# ===========================================================================
# validate_snapshot
# ===========================================================================

class TestValidateSnapshot:

    def test_passes_above_thresholds(self):
        """No exception when PCL and PPs are above minimum thresholds."""
        snap = _snap(pcl_count=60, pps_count=15)
        collector.validate_snapshot(snap)  # should not raise

    def test_raises_on_low_pcl(self):
        snap = _snap(pcl_count=10, pps_count=15)  # below SANITY_MIN_PCL=50
        with pytest.raises(collector.SanityError, match="PCL"):
            collector.validate_snapshot(snap)

    def test_raises_on_low_pps(self):
        snap = _snap(pcl_count=60, pps_count=2)  # below SANITY_MIN_PPS=10
        with pytest.raises(collector.SanityError, match="PPs"):
            collector.validate_snapshot(snap)

    def test_raises_on_empty_niap(self):
        snap = _snap(pcl_count=0, pps_count=0)
        with pytest.raises(collector.SanityError):
            collector.validate_snapshot(snap)

    def test_exactly_at_threshold_passes(self):
        snap = _snap(pcl_count=50, pps_count=10)  # exactly at minimums
        collector.validate_snapshot(snap)  # should not raise

    def test_one_below_threshold_raises(self):
        snap = _snap(pcl_count=49, pps_count=10)
        with pytest.raises(collector.SanityError):
            collector.validate_snapshot(snap)


# ===========================================================================
# _partial_get_hash
# ===========================================================================

class TestPartialGetHash:

    def _mock_response(self, status_code, content):
        mock_resp = MagicMock()
        mock_resp.status_code = status_code
        mock_resp.iter_content.return_value = [content]
        return mock_resp

    def test_returns_md5_on_206(self):
        content = b"A" * 2048
        expected = hashlib.md5(content).hexdigest()
        mock_resp = self._mock_response(206, content)
        with patch.object(collector.SESSION, "get", return_value=mock_resp):
            result = collector._partial_get_hash("https://example.com/doc.pdf")
        assert result == expected

    def test_returns_md5_on_200(self):
        content = b"B" * 512
        expected = hashlib.md5(content).hexdigest()
        mock_resp = self._mock_response(200, content)
        with patch.object(collector.SESSION, "get", return_value=mock_resp):
            result = collector._partial_get_hash("https://example.com/doc.pdf")
        assert result == expected

    def test_returns_empty_on_403(self):
        mock_resp = self._mock_response(403, b"")
        with patch.object(collector.SESSION, "get", return_value=mock_resp):
            result = collector._partial_get_hash("https://example.com/doc.pdf")
        assert result == ""

    def test_returns_empty_on_exception(self):
        with patch.object(collector.SESSION, "get", side_effect=Exception("timeout")):
            result = collector._partial_get_hash("https://example.com/doc.pdf")
        assert result == ""


# ===========================================================================
# get_html — retry contract: returns None on all failures
# ===========================================================================

class TestGetHtml:

    def test_returns_none_on_all_failures(self):
        with patch.object(collector, "_fetch_with_retry", return_value=None):
            result = collector.get_html("https://example.com")
        assert result is None

    def test_returns_soup_on_success(self):
        mock_soup = MagicMock()
        with patch.object(collector, "_fetch_with_retry", return_value=mock_soup):
            result = collector.get_html("https://example.com")
        assert result is mock_soup

    def test_retries_on_failure(self):
        """_fetch_with_retry called exactly once per get_html call."""
        with patch.object(collector, "_fetch_with_retry", return_value=None) as mock_retry:
            collector.get_html("https://example.com")
        mock_retry.assert_called_once()

    def test_passes_source_specific_timeout_to_fetcher(self):
        with patch.object(collector, "_fetch_with_retry", return_value=None) as mock_retry:
            collector.get_html("https://example.com/slow", timeout=120)

        assert mock_retry.call_args.kwargs["timeout"] == 120

    def test_nsa_403_uses_chrome_impersonation_fallback(self):
        blocked = MagicMock()
        blocked.status_code = 403
        browser_response = MagicMock()
        browser_response.text = "<html><main>CSfC content</main></html>"

        with patch.object(collector.SESSION, "get", return_value=blocked):
            with patch.object(
                collector.curl_requests, "get", return_value=browser_response
            ) as fallback:
                collector._do_get_html(_cfg.CSFC_BASE + "/components-list/")

        fallback.assert_called_once()
        assert fallback.call_args.kwargs["impersonate"] == "chrome"
        browser_response.raise_for_status.assert_called_once()

# ===========================================================================
# NIST collector regressions
# ===========================================================================

class TestInternationalCollectors:

    def test_nist_news_uses_result_cards_below_body_section(self):
        titles = []
        for text, href in (
            ("First cryptography update", "/News/2026/first"),
            ("Second cryptography update", "/News/2026/second"),
        ):
            link = MagicMock()
            link.get_text.return_value = text
            link.get.return_value = href
            title = MagicMock()
            title.find.return_value = link
            titles.append(title)
        soup = MagicMock()
        soup.select.return_value = titles

        with patch.object(collector, "get_html", return_value=soup):
            items = collector._scrape_nist_page("/news")

        assert [item["text"] for item in items] == [
            "First cryptography update",
            "Second cryptography update",
        ]
        assert items[0]["href"] == "https://csrc.nist.gov/News/2026/first"

# ===========================================================================
# collect_csfc — fix #24: APL page fetched once, soup reused
# ===========================================================================

class TestCollectCsfc:

    def test_apl_soup_fetched_once(self):
        """fix #24: get_html must be called exactly once for the APL page."""
        mock_soup = MagicMock()
        mock_soup.find.return_value = None
        mock_soup.find_all.return_value = []

        call_count = {"n": 0}

        def _mock_get_html(url):
            call_count["n"] += 1
            return mock_soup

        with patch.object(collector, "get_html", side_effect=_mock_get_html):
            with patch.object(collector, "get_rss", return_value=[]):
                collector.collect_csfc()

        # One get_html call per page key (home + apl = 2 total, but apl only once)
        # The important assertion: apl URL is NOT fetched twice
        apl_url = _cfg.CSFC_BASE + _cfg.CSFC_PAGES["apl"]
        # call_count should equal number of CSFC_PAGES (2), not 3+ (double fetch bug)
        assert call_count["n"] == len(_cfg.CSFC_PAGES), (
            f"get_html called {call_count['n']} times for {len(_cfg.CSFC_PAGES)} pages "
            f"— APL page was fetched more than once (fix #24 not applied)"
        )

    def test_apl_structured_populated(self):
        """apl_structured key must be present in the return value."""
        mock_soup = MagicMock()
        mock_soup.find.return_value = None
        mock_soup.find_all.return_value = []

        with patch.object(collector, "get_html", return_value=mock_soup):
            with patch.object(collector, "get_rss", return_value=[]):
                result = collector.collect_csfc()

        assert "apl_structured" in result

    def test_scrape_csfc_page_from_soup_exists(self):
        """fix #24: _scrape_csfc_page_from_soup helper must exist."""
        assert hasattr(collector, "_scrape_csfc_page_from_soup"), (
            "_scrape_csfc_page_from_soup missing — fix #24 not applied"
        )

    def test_scrape_csfc_page_from_soup_returns_list(self):
        mock_soup = MagicMock()
        mock_soup.find.return_value = None
        mock_soup.find_all.return_value = []
        result = collector._scrape_csfc_page_from_soup(mock_soup)
        assert isinstance(result, list)

    def test_scrape_csfc_page_from_soup_handles_none(self):
        result = collector._scrape_csfc_page_from_soup(None)
        assert result == []


# ===========================================================================
# _scrape_csfc_page_from_soup — content extraction
# ===========================================================================

class TestScrapeCsfcPageFromSoup:

    def _make_soup(self, paragraphs):
        """Build a minimal BeautifulSoup-like mock with <p> tags."""
        from unittest.mock import MagicMock
        mock_soup = MagicMock()
        mock_tags = []
        for text in paragraphs:
            tag = MagicMock()
            tag.get_text.return_value = text
            tag.find.return_value = None
            mock_tags.append(tag)
        mock_soup.find.return_value = None
        mock_soup.find_all.return_value = mock_tags
        return mock_soup

    def test_items_extracted_from_long_text(self):
        soup = self._make_soup(["Cisco VPN Gateway component listed here"])
        result = collector._scrape_csfc_page_from_soup(soup)
        assert len(result) >= 1
        assert any("Cisco" in item["text"] for item in result)

    def test_short_text_filtered_out(self):
        """Text shorter than 15 chars should be ignored."""
        soup = self._make_soup(["OK", "Hi", "Short"])
        result = collector._scrape_csfc_page_from_soup(soup)
        assert result == []

    def test_deduplication(self):
        long_text = "Cisco VPN Gateway listed component entry"
        soup = self._make_soup([long_text, long_text])
        result = collector._scrape_csfc_page_from_soup(soup)
        assert len(result) == 1

    def test_announcement_table_row_is_extracted(self):
        date_cell = MagicMock()
        date_cell.get_text.return_value = "5/20/25"
        notice_cell = MagicMock()
        notice_cell.get_text.return_value = "New CSfC guidance has been published"
        row = MagicMock()
        row.name = "tr"
        row.find_all.return_value = [date_cell, notice_cell]
        row.find.return_value = None
        content = MagicMock()
        content.find_all.return_value = [row]
        soup = MagicMock()
        soup.find.return_value = content

        result = collector._scrape_csfc_page_from_soup(soup)

        assert result == [{
            "text": "5/20/25 | New CSfC guidance has been published",
            "href": "",
        }]


class TestParseCsfcAplStructured:

    def test_all_component_tables_are_parsed(self):
        def make_table(category, vendor, model):
            headers = []
            for value in ("Vendor", "Model", "Version"):
                header = MagicMock()
                header.get_text.return_value = value
                headers.append(header)
            header_row = MagicMock()
            vendor_cell = MagicMock()
            vendor_cell.get_text.return_value = vendor
            model_cell = MagicMock()
            model_cell.get_text.return_value = model
            version_cell = MagicMock()
            version_cell.get_text.return_value = "1.0"
            data_row = MagicMock()
            data_row.find_all.return_value = [vendor_cell, model_cell, version_cell]
            data_row.find.return_value = None
            heading = MagicMock()
            heading.get_text.return_value = category
            table = MagicMock()
            table.find_all.side_effect = lambda name: (
                headers if name == "th" else [header_row, data_row]
            )
            table.find_previous.return_value = heading
            return table

        soup = MagicMock()
        soup.find_all.return_value = [
            make_table("VPN Gateway", "Cisco", "Secure Firewall"),
            make_table("WLAN", "Example", "Wireless Controller"),
        ]

        result = collector._parse_csfc_apl_structured(soup)

        assert len(result) == 2
        assert result[0]["vendor"] == "Cisco"
        assert result[0]["type"] == "VPN Gateway"
        assert result[1]["name"] == "Wireless Controller"


class TestScrapeCsfcAnnouncements:

    def test_only_dated_announcement_rows_are_returned(self):
        header_row = MagicMock()
        header_row.find_all.return_value = []
        date_cell = MagicMock()
        date_cell.get_text.return_value = "5/20/25"
        notice_cell = MagicMock()
        notice_cell.get_text.return_value = "New CSfC guidance has been published"
        row = MagicMock()
        row.find_all.return_value = [date_cell, notice_cell]
        row.find.return_value = None
        table = MagicMock()
        table.get_text.return_value = "CSfC Announcements"
        table.find_all.return_value = [header_row, row]
        main_content = MagicMock()
        main_content.find_all.return_value = [table]
        soup = MagicMock()
        soup.find.side_effect = lambda name, *args, **kwargs: (
            main_content if name == "main" else None
        )

        result = collector._scrape_csfc_announcements(soup)

        assert result == [{
            "text": "5/20/25 | New CSfC guidance has been published",
            "href": "",
        }]


class TestEuccCardParser:
    """ENISA moved to a Drupal 11 ecl-card grid (~2026-05); the old table/generic
    scrape silently produced garbage. These tests use real BeautifulSoup to
    exercise the actual selectors, not mocks."""

    @staticmethod
    def _soup(html):
        import sys
        import importlib
        stub = sys.modules.pop("bs4", None)
        try:
            bs4 = importlib.import_module("bs4")
            return bs4.BeautifulSoup(html, "html.parser")
        finally:
            if stub is not None:
                sys.modules["bs4"] = stub

    CARD = (
        '<article class="ecl-card"><div class="ecl-card__body"><div class="ecl-content-block">'
        '<div class="ecl-content-block__primary-meta-container"><div class="ecl-content-block__primary-meta-item">'
        '<time datetime="{dt}T12:00:00Z">{label}</time></div></div>'
        '<div class="ecl-content-block__title"><a href="/certificates/{cid_l}_en" '
        'class="ecl-link" data-ecl-title-link>{cid}</a></div>'
        '<div class="ecl-content-block__description">{desc}</div>'
        '<div class="ecl-content-block__list-container"><dl class="ecl-description-list">'
        '<dt class="ecl-description-list__term">Certification Scheme</dt>'
        '<dd class="ecl-description-list__definition">(UE) 2024/482 - EUCC</dd></dl></div>'
        '</div></div></article>'
    )

    def _page(self, cards):
        body = "".join(
            self.CARD.format(dt=dt, label=label, cid=cid, cid_l=cid.lower(), desc=desc)
            for dt, label, cid, desc in cards
        )
        return self._soup(f'<main>{body}</main>').find("main")

    def test_parses_cards_into_records(self):
        content = self._page([
            ("2025-10-16", "16 October 2025", "EUCC-3090-2025-0000000005-00002",
             "The product evaluated is a microcontroller developed by SAMSUNG."),
        ])
        items = collector._parse_eucc_cards(content)
        assert len(items) == 1
        rec = items[0]
        assert rec["name"] == "EUCC-3090-2025-0000000005-00002"
        assert rec["cert_date"] == "2025-10-16T12:00:00Z"
        assert rec["href"] == (
            "https://certification.enisa.europa.eu"
            "/certificates/eucc-3090-2025-0000000005-00002_en"
        )
        assert "SAMSUNG" in rec["description"]

    def test_nbsp_is_normalized(self):
        content = self._page([
            ("2025-12-03", "3 December 2025", "EUCC-3110-2025-2500098-01-00000",
             "The Cisco Nexus 9K Series.&nbsp;Cisco NX-OS is proprietary.&nbsp;"),
        ])
        desc = collector._parse_eucc_cards(content)[0]["description"]
        assert "\xa0" not in desc
        assert "  " not in desc

    def test_cisco_content_survives_for_keyword_filter(self):
        content = self._page([
            ("2025-09-14", "14 September 2025", "EUCC-3110-2025-0002500093-00001",
             "The certified product is Cisco Intersight Virtual Appliance."),
        ])
        rec = collector._parse_eucc_cards(content)[0]
        assert "cisco" in (rec["name"] + rec["text"]).lower()

    def test_no_cards_returns_empty(self):
        content = self._soup(
            "<main><p>random 39 date fragments 14 November 2025</p></main>"
        ).find("main")
        assert collector._parse_eucc_cards(content) == []

    def test_certificates_page_does_not_fall_back_to_generic(self):
        # A page with no cards but plenty of long <p>/<li> text must return empty
        # for the certificates key, so the sanity floor fires instead of garbage.
        soup = self._soup(
            "<main><p>" + "EU Cybersecurity Certificates boilerplate " * 5 + "</p>"
            "<li>14 November 2025</li><li>17 December 2025</li></main>"
        )
        with patch.object(collector, "get_html", return_value=soup):
            items = collector._scrape_eucc_page("/certificates_en", "certificates")
        assert items == []
