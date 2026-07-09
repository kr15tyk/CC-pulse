"""Regression tests for multi-source Cisco cert notifications.

main._fire_alerts() routes NIAP PCL, CSfC alert, NATO NIAPCL, and EUCC records
through send_cisco_cert_email()/send_cisco_cert_celebration(). Each source
scrapes a different record shape; the 2026-07-09 EUCC email rendered
"Unknown product" with NIAP links because the formatters only understood NIAP
keys. These tests pin the normalizer so every source yields a product name and
a link to the actual certification page.
"""
import emailer


NIAP = {
    "product_id": 11111,
    "product_name": "Cisco Catalyst 9300 Series Switches",
    "vendor_id_name": "Cisco Systems, Inc.",
    "certification_date": "2026-07-01T00:00:00",
    "sunset_date": "2028-07-01T00:00:00",
    "assigned_lab_name": "Gossamer Security Solutions",
    "submitting_country_id_name": "USA",
    "protection_profiles": [{"pp_short_name": "CPP_ND_V3.0E"}],
}

EUCC = {
    "name": "Cisco ASR 9000 Series Aggregation Services Routers running IOS-XR 7.11",
    "text": "EUCC-3110-2026-2500100-01 | 8 July 2026 | The product evaluated is...",
    "href": "https://certification.enisa.europa.eu/certificates/eucc-3110-2026-2500100-01_en",
    "cert_date": "2026-07-08",
    "description": "The product evaluated is the Cisco ASR 9000 Series.",
}

NATO = {
    "name": "Cisco Catalyst 8000 (Cat8K) Series Edge Routers (IOS-XE 17.15)",
    "manufacturer": "Cisco Systems, Inc.",
    "category": "Routers",
    "link": "https://www.ia.nato.int/niapc/Product/Cisco-Catalyst-8000_900",
    "raw_text": "Cisco Catalyst 8000 | Cisco Systems, Inc. | Routers",
}

CSFC_ALERT = {
    "source": "NSA CSfC",
    "kind": "new_cert",
    "title": "Cisco Catalyst 9300 added to CSfC Components List",
    "url": "https://www.nsa.gov/Resources/Commercial-Solutions-for-Classified-Program/Components-List/",
    "detail": "",
    "matched_keywords": ["cisco"],
    "tab": "us",
}


def test_normalize_niap_links_product_page():
    r = emailer._normalize_cert_record(NIAP, "niap")
    assert r["product_name"] == NIAP["product_name"]
    assert r["url"] == "https://www.niap-ccevs.org/products/11111"
    assert r["pp_names"] == "CPP_ND_V3.0E"
    assert r["cert_date"] == "2026-07-01"


def test_normalize_eucc_uses_name_and_href():
    r = emailer._normalize_cert_record(EUCC, "eucc")
    assert r["product_name"] == EUCC["name"]
    assert r["url"] == EUCC["href"]
    assert r["cert_date"] == "2026-07-08"
    assert "Unknown" not in r["product_name"]


def test_normalize_nato_uses_name_and_link():
    r = emailer._normalize_cert_record(NATO, "nato")
    assert r["product_name"] == NATO["name"]
    assert r["url"] == NATO["link"]
    assert r["vendor"] == "Cisco Systems, Inc."


def test_normalize_csfc_uses_title_and_url():
    r = emailer._normalize_cert_record(CSFC_ALERT, "csfc")
    assert r["product_name"] == CSFC_ALERT["title"]
    assert r["url"] == CSFC_ALERT["url"]


def test_webex_block_omits_empty_rows_for_eucc():
    block = emailer._format_cisco_cert_block(EUCC, "eucc")
    assert EUCC["name"] in block
    assert EUCC["href"] in block
    assert "Evaluating lab" not in block          # EUCC records have no lab
    assert "Unknown product" not in block


def test_email_uses_source_registry_not_niap(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        emailer, "_send_email",
        lambda subject, html: captured.update(subject=subject, html=html),
    )
    emailer.send_cisco_cert_email([EUCC], source="eucc")
    assert "EUCC" in captured["subject"]
    assert EUCC["href"] in captured["html"]
    assert EUCC["name"] in captured["html"]
    assert "niap-ccevs.org/products" not in captured["html"]
    assert "NIAP" not in captured["subject"]


def test_email_has_no_registry_button(monkeypatch):
    """The footer 'View Cisco PCL' button was removed — there is no such thing
    as a Cisco PCL, and every product block already links to its own
    certification page. Only the dashboard button remains."""
    captured = {}
    monkeypatch.setattr(
        emailer, "_send_email",
        lambda subject, html: captured.update(subject=subject, html=html),
    )
    emailer.send_cisco_cert_email([NIAP], source="niap")
    assert "View Cisco PCL" not in captured["html"]
    assert "Full Dashboard" in captured["html"]
    # Generic (unfiltered) PCL list URL appears nowhere; only the product page
    assert captured["html"].count("niap-ccevs.org/products") == \
        captured["html"].count("niap-ccevs.org/products/11111")


def test_email_niap_shape_unchanged(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        emailer, "_send_email",
        lambda subject, html: captured.update(subject=subject, html=html),
    )
    emailer.send_cisco_cert_email([NIAP], source="niap")
    assert "NDcPP" in captured["subject"]
    assert "https://www.niap-ccevs.org/products/11111" in captured["html"]
    assert "CPP_ND_V3.0E" in captured["html"]
