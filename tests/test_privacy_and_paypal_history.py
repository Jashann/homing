"""What a rejected claimant is told, what the model's remarks show and to whom, search privacy, the business's own
PayPal payout history as a source of dormant balances, PayPal's record of a payout, and the notice link. PayPal and
the model are faked."""
import hashlib
import json

from homing_fixtures import READINGS
from test_server import business, claim, env, upload  # noqa: F401  (the env fixture)

HELD = ("Held by PayPal. The first payment has not been collected, so it will not be sent twice. PayPal returns "
        "uncollected payments to the sender after 30 days.")
REJECTED = "We could not confirm this claim. Larkfield Market has been told."


def set_reading(env, key, **changes):
    """Changes what the fake model reads on one sample file."""
    digest = hashlib.sha256(env["samples"][key].read_bytes()).hexdigest()
    env["reader"].by_hash[digest] = {**json.loads(json.dumps(READINGS[key])), **changes}


# ---- 1. a rejected claim reveals nothing to the claimant -------------------------------------------------------

def test_rejected_impostor_sees_only_the_fixed_sentence(env):
    http = env["http"]
    c = claim(http, "B003", "Wei Zhang", email="wz@example.com")
    c = upload(env, c["id"], "impostor/id_card.png")
    for seen in (c, http.get(f"/api/claims/{c['id']}").json(), http.post(f"/api/claims/{c['id']}/refresh").json()):
        p = seen["proof"]
        assert seen["status"] == "REJECTED" and p["verdict"] == "REJECTED"
        assert p["message"] == REJECTED and p["attempts_left"] == 2
        assert p["checks"] == [] and p["flags"] == [] and p["missing"] == [] and p["documents"] == []
        assert p["explanation"] is None and p["score"] == 0.0
        text = json.dumps(seen, ensure_ascii=False)
        for secret in ("1985", "Toronto", "MISMATCH", "1992", "Vancouver", "date of birth"):
            assert secret not in text, secret
    assert env["model"].prompts == []      # the model is not asked to explain a rejection
    full = business(http, c["id"])["proof"]
    checks = {x["field"]: x for x in full["checks"]}
    assert checks["date_of_birth"]["result"] == "MISMATCH" and checks["date_of_birth"]["record_value_masked"] == "1985-··-··"
    assert "the date of birth on the documents is not the one on the account" in full["flags"]
    assert full["documents"][0]["read"]["names"] == ["Wei Zhang"] and full["message"] is None


def test_rejected_edited_card_hides_the_models_concerns_from_the_claimant(env):
    http = env["http"]
    c = claim(http, "B007", "Mohammed Al-Rashid", language="ar")
    c = upload(env, c["id"], "edited/id_card.png")
    text = json.dumps(c, ensure_ascii=False)
    assert "typeface" not in text and "edited" not in text and c["proof"]["message"] == REJECTED
    doc = business(http, c["id"])["proof"]["documents"][0]
    assert doc["signs_of_editing"] is True and "typeface" in doc["concerns"][0]
    # a second attempt is still offered, and still reveals nothing
    c = upload(env, c["id"], "edited/id_card.png")
    assert c["proof"]["attempts_left"] == 1 and c["proof"]["checks"] == []


def test_rejected_claim_shows_the_claimant_no_amount_but_the_business_still_sees_it(env):
    http = env["http"]
    c = claim(http, "B003", "Wei Zhang", email="wz@example.com")
    assert c["amount"] == "1240.00"        # an open claim shows its balance
    c = upload(env, c["id"], "impostor/id_card.png")
    for seen in (c, http.get(f"/api/claims/{c['id']}").json(), http.post(f"/api/claims/{c['id']}/refresh").json()):
        assert seen["status"] == "REJECTED" and seen["amount"] is None
        text = json.dumps(seen, ensure_ascii=False)
        assert "1240" not in text and "1,240" not in text
    assert business(http, c["id"])["amount"] == "1240.00"   # the business's desk still shows it


# ---- 2. sample-only remarks dropped in demo mode; notes only for the business -----------------------------------

def test_demo_mode_drops_remarks_that_only_say_the_document_is_a_sample(env):
    http = env["http"]
    set_reading(env, "clean/id_card.png", document_type="identity card (marked as a demo document)", concerns=[
        "The footer reads 'DEMO DOCUMENT · INVENTED PERSON · NOT VALID FOR ANY PURPOSE', so it is not a genuine ID.",
        "The photo area shows a generic placeholder silhouette, not a photograph of a person.",
        "The issuer name is 'Demo Identity Office', which does not identify a real issuing authority.",
        "It is a specimen.",
        "No postal address is printed on the card."])
    c = claim(http, "B001", "Tomasz Wiśniewski", language="pl")
    c = upload(env, c["id"], "clean/id_card.png", "clean/old_statement.png")
    assert c["proof"]["verdict"] == "APPROVED"
    assert all(d["concerns"] == [] for d in c["proof"]["documents"])      # claimant: no notes at all
    card = next(d for d in business(http, c["id"])["proof"]["documents"] if d["file"] == "id_card.png")
    assert card["concerns"] == ["No postal address is printed on the card."]
    assert card["kind"] == "Identity card"


# ---- 3. wording: the business by name, never "the holder" -------------------------------------------------------

def test_claimant_facing_text_names_the_business(env):
    http, fake = env["http"], env["fake"]
    c = claim(http, "B001", "Tomasz Wiśniewski")
    upload(env, c["id"], "clean/id_card.png", "clean/old_statement.png")
    http.post(f"/api/claims/{c['id']}/pay", json={"receiver": "tomasz.now@example.com"})
    fake.set_item_status(fake.only_item()["payout_item_id"], "SUCCESS")
    c = http.post(f"/api/claims/{c['id']}/refresh").json()
    text = json.dumps(c["payout"]["timeline"], ensure_ascii=False)
    assert "holder" not in text.lower() and "Fee invoice sent to Larkfield Market" in text
    r = http.post("/api/claims", json={"balance_id": "B007", "claimant": {"name": "M"}}).json()
    refused = http.post(f"/api/claims/{r['id']}/pay", json={"receiver": "m@example.com"}).json()["error"]
    assert "Larkfield Market" in refused and "holder" not in refused


# ---- 6. the business's PayPal payout history ---------------------------------------------------------------------

def seed_history(env):
    """Three payouts from the business's (fake) PayPal account; two are never collected, one was."""
    client, fake = env["money"].client, env["fake"]
    sellers = [("Elena Marchetti", "Bologna", "IT", "elena.m@example.com", "18.50"),
               ("Samuel Owusu", "Kumasi", "GH", "samuel.o@example.com", "27.30"),
               ("Freya Lindahl", "Malmö", "SE", "freya.l@example.com", "42.00")]
    payouts = []
    for i, (name, city, country, receiver, amount) in enumerate(sellers, 1):
        b = client.create_payout(receiver=receiver, amount=amount, currency="USD", sender_batch_id=f"seed-{i}")
        item_id = fake.batches[b.batch_id]["item_ids"][0]
        fake.items[item_id]["time_processed"] = "2026-11-12T08:10:07Z"
        fake.set_item_status(item_id, ["UNCLAIMED", "RETURNED", "SUCCESS"][i - 1])
        payouts.append({"seller": name, "city": city, "country": country, "receiver": receiver, "amount": amount,
                        "currency": "USD", "payout_batch_id": b.batch_id, "payout_item_id": item_id})
    (env["tmp"] / "payout_history.json").write_text(json.dumps({"payouts": payouts}))
    return payouts


def test_import_uncollected_payouts_from_paypal_is_idempotent(env):
    http = env["http"]
    assert http.post("/api/import/paypal").status_code == 404          # no history yet
    payouts = seed_history(env)
    r = http.post("/api/import/paypal").json()
    assert r["batches_checked"] == 3 and r["items_checked"] == 3 and r["added"] == 2 and r["updated"] == 0
    rows = {b["id"]: b for b in http.get("/api/ledger").json()["balances"]}
    assert len(rows) == 43
    elena = rows[f"PP-{payouts[0]['payout_item_id']}"]
    assert elena["source"] == "PayPal Payouts" and elena["amount"] == "18.50" and elena["status"] == "DORMANT"
    assert elena["last_activity"] == "2026-11-12" and elena["last_email_masked"] == "el***@ex***.com"
    assert elena["paypal"] == {"item_id": payouts[0]["payout_item_id"], "batch_id": payouts[0]["payout_batch_id"],
                               "status": "UNCLAIMED", "since": "2026-11-12", "returns_on": "2026-12-12",
                               "words": "Uncollected in PayPal since 12 November; PayPal returns it on 12 December"}
    samuel = rows[f"PP-{payouts[1]['payout_item_id']}"]
    assert samuel["paypal"]["words"] == "Returned by PayPal, ready to pay again"
    assert f"PP-{payouts[2]['payout_item_id']}" not in rows             # collected: nothing to do
    again = http.post("/api/import/paypal").json()
    assert again["added"] == 0 and again["updated"] == 0 and len(http.get("/api/ledger").json()["balances"]) == 43
    # PayPal returns Elena's payout: the next import says so
    env["fake"].set_item_status(payouts[0]["payout_item_id"], "RETURNED")
    assert http.post("/api/import/paypal").json()["updated"] == 1
    rows = {b["id"]: b for b in http.get("/api/ledger").json()["balances"]}
    assert rows[elena["id"]]["paypal"]["status"] == "RETURNED"


def test_imported_unclaimed_balance_can_be_claimed_but_not_paid_until_paypal_returns_it(env):
    http, fake = env["http"], env["fake"]
    payouts = seed_history(env)
    http.post("/api/import/paypal")
    m = http.post("/api/search", json={"name": "Elena Marchetti", "places": [{"city": "Bologna", "country": "IT"}]}
                  ).json()["matches"]
    assert m[0]["balance_id"] == f"PP-{payouts[0]['payout_item_id']}" and m[0]["amount_band"] == "between $10 and $25"
    assert m[0]["city"] == "Bologna"
    set_reading(env, "clean/id_card.png", names=["Elena Marchetti"], dates=[],
                addresses=[{"street": "Via Zamboni 33", "city": "Bologna", "region": "", "postcode": "40126",
                            "country": "IT", "whose": "person"}])
    c = claim(http, m[0]["balance_id"], "Elena Marchetti", email="elena.now@example.com")
    assert c["amount"] == "18.50" and c["source"] == "PayPal Payouts"
    assert c["paypal_held"].startswith("Uncollected in PayPal since 12 November")
    c = upload(env, c["id"], "clean/id_card.png")
    assert c["proof"]["verdict"] == "NEEDS_REVIEW"
    http.post(f"/api/claims/{c['id']}/approve", json={"approver": "Claims desk", "reason": "Card and city match."})
    sent_before = fake.payouts_created
    r = http.post(f"/api/claims/{c['id']}/pay", json={"receiver": "elena.now@example.com"})
    assert r.status_code == 409 and r.json()["error"] == HELD and fake.payouts_created == sent_before
    # PayPal returns it: Homing asks PayPal before paying, so it can now be paid home
    fake.set_item_status(payouts[0]["payout_item_id"], "RETURNED")
    r = http.post(f"/api/claims/{c['id']}/pay", json={"receiver": "elena.now@example.com"})
    assert r.status_code == 200 and r.json()["payout"]["state"] == "SENT" and fake.payouts_created == sent_before + 1
    assert r.json()["paypal_held"] is None


# ---- 7. PayPal's own record of a payout ------------------------------------------------------------------------

def test_paypal_record_is_fetched_live(env):
    http, fake = env["http"], env["fake"]
    c = claim(http, "B001", "Tomasz Wiśniewski")
    upload(env, c["id"], "clean/id_card.png", "clean/old_statement.png")
    assert http.get(f"/api/claims/{c['id']}/paypal-record").status_code == 409
    http.post(f"/api/claims/{c['id']}/pay", json={"receiver": "tomasz.now@example.com"})
    item = fake.only_item()
    item.update(transaction_id="9TX12345", time_processed="2026-11-12T09:00:01Z")
    fake.set_item_status(item["payout_item_id"], "SUCCESS")
    http.post(f"/api/claims/{c['id']}/refresh")
    asked = len(fake.requests)
    r = http.get(f"/api/claims/{c['id']}/paypal-record").json()
    assert ("GET", f"/v1/payments/payouts-item/{item['payout_item_id']}") in fake.requests[asked:]
    assert r["endpoint"] == f"GET /v1/payments/payouts-item/{item['payout_item_id']}"
    assert r["record"] == {"transaction_status": "SUCCESS", "payout_item_id": item["payout_item_id"],
                           "payout_batch_id": item["payout_batch_id"], "transaction_id": "9TX12345",
                           "time_processed": "2026-11-12T09:00:01Z",
                           "amount": {"value": "86.40", "currency": "USD"},
                           "payout_item_fee": {"currency": "USD", "value": "0.25"}}


# ---- 9. the notice links to Homing's own address ----------------------------------------------------------------

def test_notice_link_is_the_apps_own_address(env, monkeypatch):
    http = env["http"]
    monkeypatch.setenv("HOMING_MODEL", "off")
    env["saved"].data["notices"]["B001"] = {
        "language": "pl", "subject": "s", "body": "Wejdź na: {{HOMING_LINK}}.", "english_subject": "s",
        "english": "Go to the Homing page: [link to Homing page]."}
    n = http.post("/api/balances/B001/notice").json()
    assert n["body"] == "Wejdź na: http://testserver." and n["english"] == "Go to the Homing page: http://testserver."
    monkeypatch.setenv("HOMING_PUBLIC_URL", "https://homing.example.org/")
    n = http.post("/api/balances/B001/notice").json()
    assert n["link"] == "https://homing.example.org" and "https://homing.example.org." in n["english"]
    assert "[" not in n["english"] and "{{" not in n["body"]


def test_okafor_sample_documents_are_a_clean_match(env):
    http = env["http"]
    c = claim(http, "B006", "J. R. Okafor", email="jr.okafor@example.org")
    c = upload(env, c["id"], "uncollected/id_card.png", "uncollected/old_statement.png")
    assert c["proof"]["verdict"] == "APPROVED" and c["amount"] == "640.00"
