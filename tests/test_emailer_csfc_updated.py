"""Regression tests for the CSfC Components List "updated" notification path.

CC Pulse previously diffed the CSfC Components List purely by text prefix, so
a cosmetic listing edit (a cert-date tweak, a description reformat) with the
underlying product/VID unchanged showed up as a confusing removed+added pair
in the weekly digest. This "updated" category reports those cosmetic edits
distinctly instead. These tests pin the emailer-facing behaviour: the
plain-language blurb and the rendered email row for CSfC Components List
"updated" items.
"""
import emailer

def test_describe_change_csfc_components_list_updated():
    result = emailer._describe_change("updated", "CSfC Components List")
    assert "revised" in result.lower() or "updated" in result.lower()
    assert "not a new product" in result.lower()

def test_describe_change_does_not_use_apl_abbreviation():
    """The CSfC Components List used to be labelled "APL" in user-facing
    text; that abbreviation has been dropped in favour of the NSA's own
    name for the list."""
    result = emailer._describe_change("updated", "CSfC Components List")
    assert "(APL)" not in result

def test_build_email_html_renders_csfc_updated_row():
    weekly_diff = {
        "csfc": {
            "pages": {
                "apl": {
                    "added": [],
                    "removed": [],
                    "updated": [{
                        "text": "Cisco Secure Firewall listing revised",
                        "href": "https://example.test/component",
                    }],
                },
            },
        },
    }
    html = emailer.build_email_html(weekly_diff)
    assert "Cisco Secure Firewall listing revised" in html
    assert "https://example.test/component" in html
    assert "Components List-UPD" in html

def test_build_email_html_csfc_section_heading_uses_components_list():
    """The section heading previously read "CSfC — Capability Packages & APL"."""
    weekly_diff = {
        "csfc": {
            "pages": {
                "apl": {
                    "added": [{"text": "New component", "href": "https://example.test/new"}],
                    "removed": [],
                },
            },
        },
    }
    html = emailer.build_email_html(weekly_diff)
    assert "Capability Packages" in html
    assert "& APL" not in html
    assert "Components List" in html
