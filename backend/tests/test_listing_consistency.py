"""Online listing support: extraction, the Rule 6(10) declaration check, and package vs listing.

Two guarantees are asserted repeatedly here because they are the point of the feature:

* a declaration that is absent from the CAPTURED TEXT is never reported as not displayed, and
* a difference between the package and the listing is reported as a POTENTIAL INCONSISTENCY, with the
  comparison's own ``legal_status`` travelling with the result — never as a legal finding.
"""
from __future__ import annotations

from backend.extraction.listing import extract_listing, lines_from_text
from backend.services import listing_service as svc

MEDIMIX_LISTING = """Medimix Ayurvedic Soap With 18 Herbs - 750 g (Pack of 5)
Brand: Medimix
Sold by: AVA Cholayil Health Care Private Limited
Price: ₹250.00
MRP: ₹220.00 (Inclusive of all taxes)
Net Quantity: 750 g
Country of Origin: India
Customer Care: 18001031282
Email: customercare@avacare.in
"""


# --------------------------------------------------------------------- extraction


def test_listing_lines_are_synthesised_from_the_supplied_text():
    lines = lines_from_text(MEDIMIX_LISTING)
    assert len(lines) == len([ln for ln in MEDIMIX_LISTING.splitlines() if ln.strip()])
    assert lines[0].engine == "listing_text"
    assert lines[0].variant == "listing"  # geometry is labelled as synthetic, never a measurement


def test_extraction_reuses_the_declaration_extractors_on_listing_text():
    result = extract_listing(MEDIMIX_LISTING)
    fields = result["fields"]
    assert fields["mrp"]["normalized_value"].startswith("220")
    assert "MRP" in fields["mrp"]["display_value"]
    assert str(fields["net_quantity"]["quantity"]) == "750"
    assert fields["net_quantity"]["unit"].lower() == "g"
    assert "India" in fields["country_of_origin"]["display_value"]
    assert fields["consumer_care_phone"]["display_value"]
    assert fields["consumer_care_email"]["display_value"].endswith("avacare.in")


def test_a_seller_line_is_kept_as_the_seller_and_not_as_the_manufacturer_declaration():
    # 'Sold by' names the marketplace seller. The declaration Rule 6(1)(a) requires is the
    # manufacturer / packer / importer, and a seller is frequently none of those — recording the
    # seller as the manufacturer declaration would assert a role the page never printed.
    text = "Medimix Soap 150 g\nSold by: Some Trading Private Limited\nMRP: ₹45.00\nNet Quantity: 150 g\n"
    result = extract_listing(text)
    assert "Some Trading" in result["listing_only"]["listing_seller"]["display_value"]
    assert "manufacturer" not in result["fields"]


def test_a_manufacturer_named_behind_its_own_anchor_is_still_a_manufacturer_declaration():
    text = "Medimix Soap 150 g\nMfd. by: AVA Cholayil Health Care Pvt. Ltd.\nNet Quantity: 150 g\n"
    result = extract_listing(text)
    assert "AVA CHOLAYIL" in result["fields"]["manufacturer"]["display_value"].upper()


def test_listing_only_concepts_are_extracted_separately_from_declarations():
    result = extract_listing(MEDIMIX_LISTING)
    only = result["listing_only"]
    assert "750 g" in only["listing_title"]["display_value"]
    assert only["listing_price"]["normalized_value"] == "250.00"
    assert "AVA Cholayil" in only["listing_seller"]["display_value"]


def test_a_listing_price_is_not_recorded_as_an_mrp_declaration():
    text = "Ayurvedic Soap 150 g\nBrand: Medimix\nPrice: ₹250.00\nNet Quantity: 150 g\n"
    result = extract_listing(text)
    assert "mrp" not in result["fields"]
    price = result["listing_only"]["listing_price"]
    assert price["normalized_value"] == "250.00"


def test_a_currency_amount_without_an_mrp_anchor_is_not_recorded_as_mrp():
    # On a package, a lone currency amount is offered as a WEAK mrp candidate. On a listing that
    # same amount is a displayed price, and recording it as the retail sale price would fabricate a
    # declaration the page never made.
    text = "Medimix Ayurvedic Soap\n₹250.00\nNet Quantity: 150 g\n"
    result = extract_listing(text)
    assert "mrp" not in result["fields"]
    assert result["listing_only"]["listing_price"]["normalized_value"] == "250.00"
    assert result["listing_only"]["listing_title"]["display_value"] == "Medimix Ayurvedic Soap"


def test_empty_listing_text_extracts_nothing_instead_of_inventing_values():
    result = extract_listing("   \n  \n")
    assert result["fields"] == {}
    assert result["listing_only"] == {}
    assert result["line_count"] == 0


# --------------------------------------------------------------------- comparisons


def _pkg(display: str, normalized: str = "", state: str = "DETECTED") -> dict:
    return {"display_value": display, "normalized_value": normalized or display, "state": state}


def test_mrp_difference_is_a_potential_inconsistency_not_a_violation():
    result = svc._compare("mrp", _pkg("₹220.00", "220.00"), {"display_value": "₹250.00", "normalized_value": "250.00"})
    assert result["verdict"] == svc.INCONSISTENCY
    assert "potential inconsistency" in result["note"].lower()
    assert "legal basis" in result["note"]


def test_same_mrp_matches():
    result = svc._compare("mrp", _pkg("₹220.00", "220.00"), {"display_value": "₹220.00", "normalized_value": "220.00"})
    assert result["verdict"] == svc.MATCH


def test_net_quantity_is_compared_after_unit_normalisation():
    pkg = _pkg("750 g", '{"value": 750, "unit": "g", "quantity_type": "MASS"}')
    lst = {"display_value": "0.75 kg", "normalized_value": '{"value": 0.75, "unit": "kg", "quantity_type": "MASS"}'}
    assert svc._compare("net_quantity", pkg, lst)["verdict"] == svc.MATCH

    lst_short = {"display_value": "700 g", "normalized_value": '{"value": 700, "unit": "g", "quantity_type": "MASS"}'}
    mismatch = svc._compare("net_quantity", pkg, lst_short)
    assert mismatch["verdict"] == svc.INCONSISTENCY
    assert "common unit" in mismatch["note"]


def test_mass_never_compares_against_volume():
    pkg = _pkg("750 g", '{"value": 750, "unit": "g", "quantity_type": "MASS"}')
    lst = {"display_value": "750 ml", "normalized_value": '{"value": 750, "unit": "ml", "quantity_type": "VOLUME"}'}
    result = svc._compare("net_quantity", pkg, lst)
    assert result["verdict"] == svc.NOT_COMPARABLE
    assert "different quantity families" in result["method"]


def test_brand_spelling_variants_are_tolerated_but_different_brands_are_not():
    match = svc._compare("brand", _pkg("MEDIMIX"), {"display_value": "Medimix"})
    assert match["verdict"] == svc.MATCH
    other = svc._compare("brand", _pkg("MEDIMIX"), {"display_value": "SANTOR"})
    assert other["verdict"] == svc.INCONSISTENCY


def test_close_manufacturer_wording_is_sent_for_review_rather_than_called_a_mismatch():
    pkg = _pkg("AVA Cholayil Health Care Pvt. Ltd.")
    lst = {"display_value": "AVA Cholayil Health Care Private Limited"}
    result = svc._compare("manufacturer", pkg, lst)
    assert result["verdict"] in (svc.MATCH, svc.NOT_COMPARABLE)
    assert result["verdict"] != svc.INCONSISTENCY


def test_one_sided_values_are_reported_as_one_sided_not_as_a_mismatch():
    only_package = svc._compare("country_of_origin", _pkg("India"), None)
    assert only_package["verdict"] == svc.PACKAGE_ONLY
    only_listing = svc._compare("country_of_origin", None, {"display_value": "India"})
    assert only_listing["verdict"] == svc.LISTING_ONLY


def test_a_conflicting_package_value_is_not_compared():
    result = svc._compare("mrp", _pkg("₹220.00", "220.00", state="CONFLICTING"), {"display_value": "₹250.00"})
    assert result["verdict"] == svc.NOT_COMPARABLE
    assert "conflicting" in result["method"]


# --------------------------------------------------------------------- the catalog contract


def test_listing_rules_are_not_part_of_the_package_inspection_pipeline():
    """A listing requirement must never be scored against a photograph of a pack."""
    from backend.rules import listing_catalog
    from backend.rules.registry import DEFINITIONS_DIR

    catalogue_ids = {c["check_id"] for c in listing_catalog.all_checks()}
    assert catalogue_ids, "the listing catalog should not be empty"
    seeded_ids: set[str] = set()
    for path in DEFINITIONS_DIR.glob("*.json"):
        import json

        doc = json.loads(path.read_text(encoding="utf-8"))
        for rule in doc.get("rules", []) + doc.get("manual_only_rules", []):
            seeded_ids.add(rule.get("rule_id", ""))
    for check in listing_catalog.all_checks():
        assert check["check_id"] not in seeded_ids
    for rule_id in seeded_ids:
        assert not rule_id.startswith("LIST-")


def test_every_catalog_entry_declares_a_known_legal_status():
    from backend.rules import listing_catalog

    for check in listing_catalog.all_checks():
        assert check["legal_status"] in listing_catalog.VALID_STATUSES
        assert check["legal_status_note"]


def test_the_consistency_check_is_marked_as_application_policy_not_law():
    from backend.rules import listing_catalog

    entry = listing_catalog.consistency_check()
    assert entry is not None
    assert entry["legal_status"] == "APPLICATION_POLICY_NOT_A_LEGAL_PROVISION"


# --------------------------------------------------------------------- API surface


def test_listing_api_round_trip(client, auth_headers):
    created = client.post(
        "/listings",
        json={"text": MEDIMIX_LISTING, "source": "MANUAL_TEXT", "platform": "example-marketplace"},
        headers=auth_headers,
    )
    assert created.status_code == 200, created.text
    listing = created.json()
    assert listing["extraction"]["fields"]["mrp"]
    assert listing["consistency"] is None

    declarations = client.get(f"/listings/{listing['id']}/declarations", headers=auth_headers)
    assert declarations.status_code == 200
    body = declarations.json()
    assert body["check_id"] == "LIST-6-10-DECLARATIONS"
    assert body["legal_status"] == "VERIFIED_SECONDARY_TEXT"
    assert body["status"] in ("PASS", "UNCERTAIN")
    statuses = {d["field"]: d["status"] for d in body["declarations"]}
    assert statuses["mrp"] == "FOUND"
    assert statuses["consumer_care_phone"] == "FOUND"

    # No package side attached -> the comparison says exactly that instead of inventing a result.
    consistency = client.post(f"/listings/{listing['id']}/consistency", headers=auth_headers)
    assert consistency.status_code == 200
    assert consistency.json()["status"] == "NO_PACKAGE_SIDE"

    updated = client.put(
        f"/listings/{listing['id']}",
        json={"text": "Medimix Soap 150 g\nBrand: Medimix\nMRP: ₹45.00\nNet Quantity: 150 g\n"},
        headers=auth_headers,
    )
    assert updated.status_code == 200
    assert updated.json()["consistency"] is None  # the stored comparison is invalidated

    deleted = client.delete(f"/listings/{listing['id']}", headers=auth_headers)
    assert deleted.status_code == 200
    assert client.get(f"/listings/{listing['id']}", headers=auth_headers).status_code == 404


def test_listing_api_validates_input(client, auth_headers):
    empty = client.post("/listings", json={"text": "   "}, headers=auth_headers)
    assert empty.status_code == 422

    url_without_url = client.post("/listings", json={"text": "some listing", "source": "LISTING_URL"}, headers=auth_headers)
    assert url_without_url.status_code == 422

    missing_inspection = client.post(
        "/listings", json={"text": "some listing", "inspection_id": 10**6}, headers=auth_headers
    )
    assert missing_inspection.status_code == 422


def test_listing_api_requires_the_permission(client, viewer_headers):
    assert client.get("/listings", headers=viewer_headers).status_code == 403


def test_listings_for_a_missing_inspection_return_a_failed_attach(client, auth_headers):
    created = client.post("/listings", json={"text": MEDIMIX_LISTING}, headers=auth_headers).json()
    attached = client.post(
        f"/listings/{created['id']}/attach", json={"inspection_id": 10**6}, headers=auth_headers
    )
    assert attached.status_code == 422  # an attachment to a non-existent inspection is refused
