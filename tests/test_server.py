"""The API in docs/API.md, end to end, with the in-memory fake PayPal and the model readings faked."""
import json
import re

import pytest
from fake_paypal import FakePayPal
from fastapi.testclient import TestClient
from homing_fixtures import FakeModel, FakeReader, make_samples, write_ledger

from homing import assist
from homing.paypal import PayPalClient
from homing.server import create_app
from homing.settlement import Settlement
from homing.store import Store

TOTALS = {"as_of": "2026-11-04", "source_url": "https://example.org/totals", "holder_records": 2054035,
          "holder_total": "125083033.46",
          "bands": [{"label": "Under $10", "records": 1, "total": "1.00"}],
          "over_100": {"records": 230517, "total": "97306097.28"},
          "over_1000": {"records": 15928, "total": "44519772.60"},
          "property_types": [{"code": "MS17", "label": "Miscellaneous intangible property", "records": 1,
                              "total": "1.00"}]}


@pytest.fixture
def env(tmp_path):
    fake = FakePayPal()
    client = PayPalClient("test-id", "test-secret", transport=fake.transport())
    hook = client.create_webhook("https://homing.example.org/api/webhooks/paypal")
    money = Settlement(client, tmp_path / "settlement.sqlite", holder_email="holder@example.com",
                       webhook_id=hook["id"], prefix="homing", holder_name="Larkfield Market")
    samples = make_samples(tmp_path / "samples")
    reader = FakeReader(samples)
    model = FakeModel()
    totals = tmp_path / "totals.json"
    totals.write_text(json.dumps(TOTALS))
    store = Store(tmp_path / "homing.sqlite")
    saved = assist.Saved(tmp_path / "saved.json")
    app = create_app(store=store, settlement=money, reader=reader, totals_path=totals,
                     ledger_path=write_ledger(tmp_path / "ledger.json"), files_dir=tmp_path / "files", demo=True,
                     saved=saved, ask=model, payout_history_path=tmp_path / "payout_history.json")
    with TestClient(app) as http:
        yield {"http": http, "fake": fake, "money": money, "samples": samples, "reader": reader, "model": model,
               "store": store, "saved": saved, "tmp": tmp_path}
    money.close()
    client.close()
    store.close()


def claim(http, balance_id, name, email="someone@example.com", language="en", **extra):
    r = http.post("/api/claims", json={"balance_id": balance_id, "claimant": {
        "name": name, "email": email, "country": "PL", "language": language, **extra}})
    assert r.status_code == 200, r.text
    return r.json()


def business(http, claim_id):
    """The claim as the business's desk sees it (GET /api/claims)."""
    return {x["id"]: x for x in http.get("/api/claims").json()["claims"]}[claim_id]


def upload(env, claim_id, *keys):
    files = [("files[]", (k.split("/")[1], env["samples"][k].read_bytes(), "image/png")) for k in keys]
    r = env["http"].post(f"/api/claims/{claim_id}/documents", files=files)
    assert r.status_code == 200, r.text
    return r.json()


def test_totals_shape(env):
    t = env["http"].get("/api/totals").json()
    assert t["holder_records"] == 2054035 and t["holder_total"] == "125083033.46"
    assert "organisations" not in t     # no single owner of any kind is shown
    assert t["property_types"] == [{"label": "Miscellaneous intangible property", "records": 1, "total": "1.00"}]


def test_ledger_masks_emails_and_addresses(env):
    rows = env["http"].get("/api/ledger").json()["balances"]
    assert len(rows) == 41
    assert set(rows[0]) == {"id", "owner_display", "last_email_masked", "last_city", "last_country", "amount",
                            "currency", "last_activity", "status", "sent_to_state_on", "source", "paypal"}
    assert rows[0]["source"] == "Ledger" and rows[0]["paypal"] is None
    b2 = next(r for r in rows if r["id"] == "B002")
    assert b2["owner_display"] == "Ana K."
    assert b2["last_email_masked"] == "an***@ex***.com"
    text = json.dumps(rows, ensure_ascii=False)
    assert "Kowalski" not in text and "Cortland" not in text and "60647" not in text
    assert not re.search(r"[\w.]+@example\.(com|org|net)", text)
    escheated = env["http"].get("/api/ledger?status=ESCHEATED").json()["balances"]
    assert [r["id"] for r in escheated] == ["B038", "B039", "B040", "B041"]
    assert escheated[0]["sent_to_state_on"] == "2019-04-15" and b2["sent_to_state_on"] is None
    assert env["http"].get("/api/ledger?status=NOPE").status_code == 400


def test_search_endpoint_same_name_trap(env):
    r = env["http"].post("/api/search", json={"name": "Wei Zhang", "places": [{"city": "Toronto", "country": "CA"}]})
    matches = r.json()["matches"]
    assert [m["balance_id"] for m in matches[:2]] == ["B003", "B004"]
    assert all(m["probability"] < 0.85 and m["doubts"] for m in matches[:2])
    assert set(matches[0]) >= {"balance_id", "owner_display", "amount_band", "currency", "probability", "strength",
                               "band", "reasons", "doubts", "status", "city"}
    assert matches[0]["band"] == "POSSIBLE" and matches[0]["strength"] == round(matches[0]["probability"] * 100)
    # before a claim is opened: no exact amount, and only the city the searcher typed
    assert "amount" not in matches[0] and matches[0]["amount_band"] == "between $1,000 and $2,500"
    assert matches[1]["amount_band"] == "between $50 and $100"
    assert matches[0]["city"] == "Toronto" and matches[1]["city"] is None
    text = json.dumps(r.json(), ensure_ascii=False)
    assert "Vancouver" not in text and "1240" not in text and "57.10" not in text
    assert any("in another city" in d for d in matches[0]["doubts"])
    assert any("in Toronto" in d for d in matches[1]["doubts"])
    # once the claim is opened the exact amount is shown
    c = env["http"].post("/api/claims", json={"balance_id": "B003", "claimant": {"name": "Wei Zhang"}}).json()
    assert c["amount"] == "1240.00"


def test_clean_case_pays_and_invoices_once(env):
    http, fake = env["http"], env["fake"]
    c = claim(http, "B001", "Tomasz Wiśniewski")
    assert c["status"] == "OPEN" and c["proof"]["verdict"] == "NONE" and c["payout"]["state"] == "NONE"
    c = upload(env, c["id"], "clean/id_card.png", "clean/old_statement.png")
    assert c["proof"]["verdict"] == "APPROVED" and c["status"] == "APPROVED"
    c = http.post(f"/api/claims/{c['id']}/pay", json={"receiver": "tomasz.now@example.com",
                                                     "receiver_type": "EMAIL", "currency": "USD"}).json()
    assert c["status"] == "PAYING" and c["payout"]["state"] == "SENT"
    assert c["payout"]["receiver_masked"] == "to***@ex***.com"
    # paying again never makes a second payout
    again = http.post(f"/api/claims/{c['id']}/pay", json={"receiver": "tomasz.now@example.com"}).json()
    assert again["payout"]["batch_id"] == c["payout"]["batch_id"] and fake.payouts_created == 1
    item = fake.only_item()
    fake.set_item_status(item["payout_item_id"], "SUCCESS")
    c = http.post(f"/api/claims/{c['id']}/refresh").json()
    assert c["status"] == "PAID" and c["payout"]["state"] == "CLAIMED" and c["payout"]["fee"] == "0.25"
    assert c["invoice"]["amount"] == "8.64" and c["invoice"]["status"] == "SENT"
    states = [t["state"] for t in c["payout"]["timeline"]]
    assert states[-1] in ("CLAIMED", "INVOICE") and "CLAIMED" in states
    assert states.index("CLAIMED") > states.index("SENT")
    http.post(f"/api/claims/{c['id']}/refresh")
    assert fake.invoices_created == 1
    led = {b["id"]: b for b in http.get("/api/ledger").json()["balances"]}
    assert led["B001"]["status"] == "PAID"
    s = http.get("/api/scoreboard").json()
    assert s["paid"] == 1 and s["paid_total"] == "86.40" and s["fees_invoiced"] == "8.64"
    assert s["found"] == 1 and s["proven"] == 1 and isinstance(s["median_minutes_to_paid"], (int, float))
    # a second claim on a paid balance is refused
    assert http.post("/api/claims", json={"balance_id": "B001", "claimant": {"name": "x"}}).status_code == 409


def test_pay_rule_needs_review_then_human_approval(env):
    http, fake = env["http"], env["fake"]
    c = claim(http, "B002", "Ana Brennan", email="ana.b@example.com", former_names=["Ana Kowalski"])
    pay = {"receiver": "ana.b@example.com", "receiver_type": "EMAIL"}
    assert http.post(f"/api/claims/{c['id']}/pay", json=pay).status_code == 409      # no proof at all
    c = upload(env, c["id"], "partial/id_card.png", "partial/utility_bill.png")
    assert c["proof"]["verdict"] == "NEEDS_REVIEW" and c["status"] == "PROOF_READY"
    r = http.post(f"/api/claims/{c['id']}/pay", json=pay)
    assert r.status_code == 409 and "error" in r.json()
    assert fake.payouts_created == 0
    r = http.post(f"/api/claims/{c['id']}/approve", json={"approver": "Reviewer One"})
    assert r.status_code == 400 and "why you are satisfied" in r.json()["error"]
    reason = "Called her on the number on file; she moved to Dublin after marrying."
    c = http.post(f"/api/claims/{c['id']}/approve", json={"approver": "Reviewer One", "reason": reason}).json()
    assert c["status"] == "APPROVED" and c["proof"]["approved_by"] == "Reviewer One"
    assert c["proof"]["review"]["required"] is True and c["proof"]["review"]["reason"] == reason
    assert c["proof"]["review"]["approved_at"]
    log = [a for a in env["store"].audit_log(c["id"]) if a["action"] == "human_approved"]
    assert log and reason in log[0]["detail"]
    c = http.post(f"/api/claims/{c['id']}/pay", json=pay).json()
    assert c["status"] == "PAYING"
    # nobody collects it: unclaimed, then a new contact cancels the old item and pays once more
    item_id = c["payout"]["item_id"] or fake.only_item()["payout_item_id"]
    fake.set_item_status(item_id, "UNCLAIMED")
    c = http.post(f"/api/claims/{c['id']}/refresh").json()
    assert c["status"] == "NEEDS_NEW_CONTACT" and c["payout"]["state"] == "UNCLAIMED"
    assert {b["id"]: b for b in http.get("/api/ledger").json()["balances"]}["B002"]["status"] == "UNCLAIMED"
    assert http.get("/api/scoreboard").json()["unclaimed"] == 1
    paid = claim(http, "B001", "Tomasz Wiśniewski", email="tomasz@example.com")
    upload(env, paid["id"], "clean/id_card.png", "clean/old_statement.png")
    assert http.post(f"/api/claims/{paid['id']}/pay", json={"receiver": "collector@example.com"}).status_code == 200
    r = http.post(f"/api/claims/{c['id']}/new-contact", json={"receiver": "collector@example.com"})
    assert r.status_code == 409 and "same account" in r.json()["error"]
    c = http.get(f"/api/claims/{c['id']}").json()
    assert c["status"] == "NEEDS_NEW_CONTACT" and c["proof"]["flags"]     # flagged, payout still followed
    c = http.post(f"/api/claims/{c['id']}/new-contact", json={"receiver": "+353861234567",
                                                              "receiver_type": "PHONE"}).json()
    assert c["status"] == "PAYING" and c["payout"]["attempt"] == 2 and c["payout"]["receiver_masked"].endswith("567")
    assert fake.payouts_created == 3
    assert sum(1 for i in fake.items.values() if i["transaction_status"] == "RETURNED") == 1


def test_impostor_rejected_cannot_be_approved_or_paid(env):
    http = env["http"]
    c = claim(http, "B003", "Wei Zhang", email="wz@example.com")
    c = upload(env, c["id"], "impostor/id_card.png")
    assert c["proof"]["verdict"] == "REJECTED" and c["status"] == "REJECTED"
    assert c["proof"]["checks"] == [] and c["proof"]["flags"] == []          # the claimant learns nothing
    checks = {x["field"]: x for x in business(http, c["id"])["proof"]["checks"]}
    assert checks["date_of_birth"]["result"] == "MISMATCH"
    assert checks["date_of_birth"]["record_value_masked"] == "1985-··-··"
    assert http.post(f"/api/claims/{c['id']}/approve", json={"approver": "x"}).status_code == 409
    assert http.post(f"/api/claims/{c['id']}/pay", json={"receiver": "wz@example.com"}).status_code == 409
    assert http.get("/api/scoreboard").json()["rejected"] == 1
    # the same card on a second claim flags both claims
    c2 = claim(http, "B004", "Wei Zhang", email="other@example.com")
    c2 = upload(env, c2["id"], "impostor/id_card.png")
    assert any("same document" in f for f in business(http, c2["id"])["proof"]["flags"])
    assert any("same document" in f for f in business(http, c["id"])["proof"]["flags"])
    assert http.get(f"/api/claims/{c['id']}").json()["proof"]["flags"] == []


def test_same_receiver_on_two_owners_is_refused_at_payment(env):
    http = env["http"]
    a = claim(http, "B001", "Tomasz Wiśniewski", email="t1@example.com")
    upload(env, a["id"], "clean/id_card.png", "clean/old_statement.png")
    assert http.post(f"/api/claims/{a['id']}/pay", json={"receiver": "collector@example.com"}).status_code == 200
    b = claim(http, "B002", "Ana Brennan", email="ana@example.com", former_names=["Ana Kowalski"])
    upload(env, b["id"], "partial/id_card.png", "partial/utility_bill.png")
    http.post(f"/api/claims/{b['id']}/approve", json={"approver": "Reviewer", "reason": "Name change explained."})
    r = http.post(f"/api/claims/{b['id']}/pay", json={"receiver": "collector@example.com"})
    assert r.status_code == 409 and "same account" in r.json()["error"]
    assert http.get(f"/api/claims/{b['id']}").json()["status"] == "REJECTED"


def test_webhook_verified_and_idempotent(env):
    http, fake = env["http"], env["fake"]
    c = claim(http, "B001", "Tomasz Wiśniewski")
    upload(env, c["id"], "clean/id_card.png", "clean/old_statement.png")
    http.post(f"/api/claims/{c['id']}/pay", json={"receiver": "tomasz.now@example.com"})
    item = fake.only_item()
    fake.set_item_status(item["payout_item_id"], "SUCCESS")
    event = fake.item_event(item["payout_item_id"], "PAYMENT.PAYOUTS-ITEM.SUCCEEDED", event_id="WH-1")
    headers = {"paypal-auth-algo": "SHA256withRSA", "paypal-cert-url": "https://example.org/cert",
               "paypal-transmission-id": "t1", "paypal-transmission-time": "2026-11-12T00:00:00Z"}
    bad = http.post("/api/webhooks/paypal", json=event, headers={**headers, "paypal-transmission-sig": "forged"})
    assert bad.status_code == 400
    assert http.get(f"/api/claims/{c['id']}").json()["status"] == "PAYING"
    good = http.post("/api/webhooks/paypal", json=event, headers={**headers, "paypal-transmission-sig": "good"})
    assert good.status_code == 200 and good.json()["ok"] is True
    again = http.post("/api/webhooks/paypal", json=event, headers={**headers, "paypal-transmission-sig": "good"})
    assert again.json()["outcome"] == "duplicate"
    c = http.get(f"/api/claims/{c['id']}").json()
    assert c["status"] == "PAID" and c["invoice"] is not None
    assert fake.invoices_created == 1


def test_state_claim_only_for_escheated(env):
    http = env["http"]
    r = http.post("/api/state-claim/B038")
    assert r.status_code == 200
    body = r.json()
    assert body["pdf_url"].startswith("/api/files/state-claims/") and body["steps"]
    pdf = http.get(body["pdf_url"])
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF")
    assert http.post("/api/state-claim/B001").status_code == 409
    assert http.post("/api/claims", json={"balance_id": "B038", "claimant": {"name": "Rosa"}}).status_code == 409
    assert http.get("/api/files/state-claims/..%2F..%2Fhoming.sqlite").status_code == 404


def test_errors_are_plain_json(env):
    http = env["http"]
    r = http.get("/api/claims/nope")
    assert r.status_code == 404 and set(r.json()) == {"error"}
    r = http.post("/api/claims", json={"balance_id": "B001"})
    assert r.status_code == 422 and "error" in r.json()
    files = [("files", ("notes.txt", b"hello", "text/plain"))]
    c = claim(http, "B005", "An Van Nguyen")
    assert http.post(f"/api/claims/{c['id']}/documents", files=files).status_code == 415


def test_demo_reset_reloads_ledger(env):
    http = env["http"]
    c = claim(http, "B010", "Maria Jose Garcia Lopez")
    assert {b["id"]: b for b in http.get("/api/ledger").json()["balances"]}["B010"]["status"] == "MATCHED"
    assert http.post("/api/demo/reset").json()["ok"] is True
    assert {b["id"]: b for b in http.get("/api/ledger").json()["balances"]}["B010"]["status"] == "DORMANT"
    assert http.get("/api/claims").json()["claims"] == []
    assert http.get("/api/scoreboard").json()["found"] == 0


def test_new_contact_when_paypal_will_not_cancel_fails_safely(env):
    http, fake = env["http"], env["fake"]
    c = claim(http, "B001", "Tomasz Wiśniewski")
    upload(env, c["id"], "clean/id_card.png", "clean/old_statement.png")
    c = http.post(f"/api/claims/{c['id']}/pay", json={"receiver": "nobody-here@example.com"}).json()
    fake.set_item_status(c["payout"]["item_id"] or fake.only_item()["payout_item_id"], "UNCLAIMED")
    c = http.post(f"/api/claims/{c['id']}/refresh").json()
    assert c["payout"]["state"] == "UNCLAIMED"
    fake.fail_cancel = (500, "INTERNAL_SERVICE_ERROR")
    r = http.post(f"/api/claims/{c['id']}/new-contact", json={"receiver": "tomasz.now@example.com"})
    assert r.status_code == 409
    assert r.json()["error"] == ("Held by PayPal. The first payment has not been collected, so it will not be sent "
                                 "twice. PayPal returns uncollected payments to the sender after 30 days.")
    assert fake.payouts_created == 1
    c = http.get(f"/api/claims/{c['id']}").json()
    assert c["status"] == "NEEDS_NEW_CONTACT" and c["payout"]["state"] == "UNCLAIMED"
    assert "HELD" in [t["state"] for t in c["payout"]["timeline"]]
    held = next(t for t in c["payout"]["timeline"] if t["state"] == "HELD")
    assert held["title"] == "Held by PayPal" and held["note"].startswith("Held by PayPal.")
    assert [t["title"] for t in c["payout"]["timeline"]][:3] == ["Prepared", "Sent through PayPal",
                                                                 "Not collected yet"]


def test_refresh_waits_for_the_webhook_then_polls(env, tmp_path):
    """With webhooks on and a grace period, refresh does not ask PayPal at first; the webhook confirms the payout.
    Once the grace period is over, refresh asks PayPal itself, so a missing webhook still ends the claim."""
    fake, money = env["fake"], env["money"]
    app = create_app(store=env["store"], settlement=money, reader=env["reader"],
                     totals_path=env["tmp"] / "totals.json", files_dir=env["tmp"] / "files", demo=True,
                     saved=env["saved"], ask=env["model"], webhook_grace=3600)
    with TestClient(app) as http:
        c = claim(http, "B001", "Tomasz Wiśniewski")
        upload({**env, "http": http}, c["id"], "clean/id_card.png", "clean/old_statement.png")
        c = http.post(f"/api/claims/{c['id']}/pay", json={"receiver": "tomasz.now@example.com"}).json()
        item = fake.only_item()
        fake.set_item_status(item["payout_item_id"], "SUCCESS")
        polls = len([r for r in fake.requests if "payouts" in r[1] and r[0] == "GET"])
        c = http.post(f"/api/claims/{c['id']}/refresh").json()
        assert c["payout"]["state"] == "SENT"          # PayPal was not asked during the grace period
        assert len([r for r in fake.requests if "payouts" in r[1] and r[0] == "GET"]) == polls
        assert money.apply_webhook(fake.item_event(item["payout_item_id"], "PAYMENT.PAYOUTS-ITEM.SUCCEEDED")) == "applied"
        c = http.get(f"/api/claims/{c['id']}").json()
        assert c["status"] == "PAID" and c["payout"]["confirmed_by"] == "WEBHOOK"
        assert [t["title"] for t in c["payout"]["timeline"] if t["heard_by"] == "WEBHOOK"] == ["Collected"]

    app = create_app(store=env["store"], settlement=money, reader=env["reader"],
                     totals_path=env["tmp"] / "totals.json", files_dir=env["tmp"] / "files", demo=True,
                     saved=env["saved"], ask=env["model"], webhook_grace=0.001)
    with TestClient(app) as http:
        c = claim(http, "B002", "Ana Brennan", email="ana@example.com")
        env["store"].update_claim(c["id"], "test", approved_by="t", status="APPROVED")
        c = http.post(f"/api/claims/{c['id']}/pay", json={"receiver": "ana.now@example.com"}).json()
        item = [i for i in fake.items.values() if i["payout_item"]["receiver"] == "ana.now@example.com"][0]
        fake.set_item_status(item["payout_item_id"], "SUCCESS")
        c = http.post(f"/api/claims/{c['id']}/refresh").json()
        assert c["status"] == "PAID" and c["payout"]["confirmed_by"] == "POLLING"
