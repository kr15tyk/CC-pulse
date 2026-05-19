"""Unit tests for the _describe_change() lookup function in emailer.py."""
import pytest
from emailer import _describe_change


class TestSpecificMatches:
    """Specific (kind, source_prefix) lookups should return the right blurb."""

    def test_new_cert_niap(self):
        result = _describe_change("new_cert", "NIAP Validated Products List")
        assert "NIAP" in result

    def test_new_cert_csfc(self):
        result = _describe_change("new_cert", "CSfC APL")
        assert "CSfC" in result or "Classified" in result

    def test_niap_pp_beats_niap(self):
        """More-specific prefix "NIAP PP" must win over "NIAP" for PP sources."""
        pp_result   = _describe_change("new", "NIAP PP Extra")
        niap_result = _describe_change("new", "NIAP Validated")
        assert "Protection Profile" in pp_result
        assert pp_result != niap_result

    def test_removed_nato(self):
        result = _describe_change("removed", "NATO NIAPCL")
        assert "NATO" in result

    def test_sunset_niap_pp(self):
        result = _describe_change("sunset", "NIAP PP 2.1")
        assert "sunsetted" in result.lower() or "sunset" in result.lower()

    def test_post_cctl(self):
        result = _describe_change("post", "CCTL Labs")
        assert "CCTL" in result or "Testing Laboratory" in result


class TestGenericFallback:
    """Unknown source should fall back to _GENERIC_DESCRIPTIONS."""

    def test_known_kind_unknown_source(self):
        result = _describe_change("updated", "SomeUnknownSource")
        assert result == "An existing item has been revised or updated."

    def test_new_cert_unknown_source(self):
        result = _describe_change("new_cert", "SomeUnknownSource")
        assert result == "A new product certification has been detected."


class TestCatchAllFallback:
    """Completely unknown kind+source should return the catch-all string."""

    def test_unknown_kind_and_source(self):
        result = _describe_change("totally_unknown_kind", "NoSuchSource")
        assert result == "A change was detected on the monitored source."

    def test_empty_strings(self):
        result = _describe_change("", "")
        assert isinstance(result, str)
        assert len(result) > 0


class TestEdgeCases:
    """Guard against None inputs."""

    def test_source_none_does_not_raise(self):
        try:
            result = _describe_change("updated", None)
            assert isinstance(result, str)
        except AttributeError:
            pytest.fail("_describe_change raised AttributeError on source=None")

    def test_kind_none_does_not_raise(self):
        try:
            result = _describe_change(None, "NIAP")
            assert isinstance(result, str)
        except Exception as exc:
            pytest.fail(f"_describe_change raised {exc} on kind=None")
