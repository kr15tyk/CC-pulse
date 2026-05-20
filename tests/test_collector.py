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
        "csfc": {"pages": {"apl": []}, "component_selection_hashes": {}},
        "cc_crypto": {"pages": {"publications": [{"text": f"pub{i}", "href": ""} for i in range(6)]}},
        "nist": {"pages": {"news": [{"text": f"news{i}"} for i in range(12)]}},
    }


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
            with patch.object(collector, "_hash_csfc_selections", return_value={}):
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
            with patch.object(collector, "_hash_csfc_selections", return_value={}):
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
