"""The verdict for the four sample cases, with the model's readings faked. The verdict is plain code."""
from datetime import date

from homing_fixtures import READINGS, ledger

from homing import verify

BAL = {b["id"]: b for b in ledger()}
TODAY = date(2026, 11, 12)


def docs(*keys):
    return [{"id": f"D{i}", "claim_id": "C1", "filename": k.split("/")[1], "sha256": k, "reading": READINGS[k]}
            for i, k in enumerate(keys)]


def checks(result):
    return {c["field"]: c for c in result["checks"]}


def test_clean_match_is_approved():
    claim = {"id": "C1", "balance_id": "B001", "claimant": {"name": "Tomasz Wiśniewski", "email": "tomasz@example.com"}}
    r = verify.assess(BAL["B001"], claim, docs("clean/id_card.png", "clean/old_statement.png"), today=TODAY,
                      demo=True)
    assert r["verdict"] == "APPROVED", r
    assert r["demo"] is True                         # sample-stamped documents were accepted, in demo mode only
    c = checks(r)
    assert c["name"]["result"] == "MATCH"
    assert c["date_of_birth"]["result"] == "MATCH"
    assert c["address"]["result"] == "MATCH"
    assert c["email"]["result"] == "MATCH"
    assert r["flags"] == [] and r["missing"] == []
    assert r["score"] >= 0.9
    # the record's own values only appear masked
    assert c["name"]["record_value_masked"] == "T····· W·········"
    assert c["date_of_birth"]["record_value_masked"] == "1979-··-··"
    assert c["address"]["record_value_masked"] == "Kraków, PL"
    assert "Długa" not in str([x["record_value_masked"] for x in r["checks"]])


def test_married_name_without_link_needs_review():
    claim = {"id": "C2", "balance_id": "B002",
             "claimant": {"name": "Ana Brennan", "email": "ana.b@example.com", "former_names": ["Ana Kowalski"]}}
    r = verify.assess(BAL["B002"], claim, docs("partial/id_card.png", "partial/utility_bill.png"), today=TODAY,
                      demo=True)
    assert r["verdict"] == "NEEDS_REVIEW", r
    c = checks(r)
    assert c["name"]["result"] == "PARTIAL"
    assert c["date_of_birth"]["result"] == "MATCH"
    assert c["address"]["result"] == "MISMATCH"
    assert any("both names" in m for m in r["missing"])
    assert any("old address" in m for m in r["missing"])
    assert r["flags"] == []


def test_impostor_with_same_name_is_rejected():
    claim = {"id": "C3", "balance_id": "B003", "claimant": {"name": "Wei Zhang", "email": "wz@example.com"}}
    r = verify.assess(BAL["B003"], claim, docs("impostor/id_card.png"), today=TODAY, demo=True)
    assert r["verdict"] == "REJECTED", r
    assert r["missing"] == []                        # a rejected claim is never told what would get it through
    assert any("date of birth" in f for f in r["flags"])
    c = checks(r)
    assert c["name"]["result"] == "MATCH"          # the name alone would have fooled a name match
    assert c["date_of_birth"]["result"] == "MISMATCH"
    assert c["address"]["result"] == "MISMATCH"
    assert r["score"] <= 0.3


def test_reused_document_and_shared_account_are_flagged():
    claim = {"id": "C9", "balance_id": "B004", "claimant": {"name": "Wei Zhang", "email": "wz@example.com"}}
    d = docs("impostor/id_card.png")
    others = [{"id": "C3", "balance_id": "B003", "receiver": None, "claimant": {"email": "wz@example.com"}}]
    used = {"impostor/id_card.png": [{"claim_id": "C3"}, {"claim_id": "C9"}]}
    r = verify.assess(BAL["B004"], claim, d, other_claims=others, docs_with_hash=lambda h: used.get(h, []),
                      today=TODAY)
    assert r["verdict"] == "REJECTED"
    assert any("same document" in f and "C3" in f for f in r["flags"])
    assert any(f.startswith("same account used on 2 claims") for f in r["flags"])


def test_edited_document_is_rejected_even_if_everything_matches():
    reading = dict(READINGS["clean/id_card.png"], signs_of_editing=True)
    d = docs("clean/id_card.png", "clean/old_statement.png")
    d[0]["reading"] = reading
    claim = {"id": "C1", "balance_id": "B001", "claimant": {"name": "Tomasz Wiśniewski"}}
    r = verify.assess(BAL["B001"], claim, d, today=TODAY)
    assert r["verdict"] == "REJECTED"
    assert any("edited" in f for f in r["flags"])


def test_model_never_decides_unread_documents_wait_for_review():
    d = docs("clean/id_card.png")
    d[0]["reading"] = {"status": "failed", "problem": "The reading model is not available on this machine."}
    claim = {"id": "C1", "balance_id": "B001", "claimant": {"name": "Tomasz Wiśniewski"}}
    r = verify.assess(BAL["B001"], claim, d, today=TODAY)
    assert r["verdict"] == "NEEDS_REVIEW"


def test_no_documents_no_verdict():
    r = verify.assess(BAL["B001"], {"id": "C1", "claimant": {"name": "x"}}, [], today=TODAY)
    assert r["verdict"] == "NONE"


def test_sample_stamped_documents_never_match_outside_demo_mode():
    claim = {"id": "C1", "balance_id": "B001", "claimant": {"name": "Tomasz Wiśniewski"}}
    d = docs("clean/id_card.png", "clean/old_statement.png")
    r = verify.assess(BAL["B001"], claim, d, today=TODAY, demo=False)
    assert r["verdict"] == "NEEDS_REVIEW" and r["demo"] is False
    doc = checks(r)["document"]
    assert doc["result"] == "PARTIAL" and "sample or specimen" in doc["note"]
    # the same documents in demo mode: accepted, and no per-row demo note
    r = verify.assess(BAL["B001"], claim, d, today=TODAY, demo=True)
    assert r["verdict"] == "APPROVED" and "sample" not in checks(r)["document"]["note"]


def test_marriage_certificate_links_the_names_but_the_address_still_needs_a_person():
    claim = {"id": "C2", "balance_id": "B002",
             "claimant": {"name": "Ana Brennan", "email": "ana.b@example.com", "former_names": ["Ana Kowalski"]}}
    r = verify.assess(BAL["B002"], claim, docs("partial/id_card.png", "partial/utility_bill.png",
                                               "partial/marriage_certificate.png"), today=TODAY, demo=True)
    c = checks(r)
    assert c["name"]["result"] == "MATCH" and "marriage_certificate.png" in c["name"]["note"]
    assert c["address"]["result"] in ("PARTIAL", "MISMATCH")
    assert r["verdict"] == "NEEDS_REVIEW" and r["flags"] == []
    assert not any("both names" in m for m in r["missing"])
    assert any("old" in m and "Chicago" in m for m in r["missing"])


def test_edited_card_is_rejected_by_code_although_every_detail_matches():
    claim = {"id": "C4", "balance_id": "B007", "claimant": {"name": "Mohammed Al-Rashid"}}
    r = verify.assess(BAL["B007"], claim, docs("edited/id_card.png"), today=TODAY, demo=True)
    c = checks(r)
    assert c["name"]["result"] == "MATCH" and c["date_of_birth"]["result"] == "MATCH"
    assert c["address"]["result"] == "MATCH"
    assert c["document"]["result"] == "MISMATCH"
    assert r["verdict"] == "REJECTED"
    assert r["flags"] == ["id_card.png shows signs of having been edited"]
    assert r["missing"] == []
