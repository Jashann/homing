"""The version 2 additions in docs/API.md: meta, the model's visible work, the review rule, rejected claims,
notices, timeline titles and webhook confirmation. PayPal and the model are faked."""
import pytest
from fastapi.testclient import TestClient
from homing_fixtures import READINGS, FakeModel, make_samples, sample_hashes, write_ledger
from test_server import business, claim, env, upload  # noqa: F401  (the env fixture)

from homing import assist, llm
from homing.server import create_app
from homing.store import Store

HELD = ("Held by PayPal. The first payment has not been collected, so it will not be sent twice. PayPal returns "
        "uncollected payments to the sender after 30 days.")


def test_meta(env, monkeypatch):
    monkeypatch.delenv("HOMING_TEST_RECEIVERS", raising=False)
    m = env["http"].get("/api/meta").json()
    assert m["holder"] == {"name": "Larkfield Market",
                           "kind": "an online marketplace that pays its sellers through PayPal", "sample": True}
    assert m["demo"] is True
    assert m["test_receivers"] == ["sb-43y9ma53877422@personal.example.com", "sb-vgzm153858978@personal.example.com"]
    assert isinstance(m["model_available"], bool)
    monkeypatch.setenv("HOMING_MODEL", "off")
    assert env["http"].get("/api/meta").json()["model_available"] is False


def test_clean_case_shows_what_was_read_and_explains_in_polish(env):
    http = env["http"]
    c = claim(http, "B001", "Tomasz Wiśniewski", language="pl")
    c = upload(env, c["id"], "clean/id_card.png", "clean/old_statement.png")
    p = c["proof"]
    assert p["verdict"] == "APPROVED" and p["demo"] is True
    assert p["attempts"] == 1 and p["attempts_left"] == 2
    assert p["review"] == {"required": False, "reason": None, "approved_by": None, "approved_at": None}
    card = next(d for d in p["documents"] if d["file"] == "id_card.png")
    assert card["kind"] == "Identity card" and card["read"]["names"] == ["Tomasz Wiśniewski"]
    assert card["read"]["date_of_birth"] == "1979-03-14" and card["reading_source"] == "LIVE"
    assert card["signs_of_editing"] is False and card["concerns"] == []
    stmt = next(d for d in p["documents"] if d["file"] == "old_statement.png")
    assert stmt["read"]["addresses"] == ["ul. Długa 12, Kraków, 31-147, PL"]      # the issuer's address left out
    assert stmt["read"]["emails"] == ["t.wisniewski@example.com"]
    thumb = http.get(card["thumb_url"])
    assert thumb.status_code == 200 and thumb.content.startswith(b"\x89PNG")
    assert http.get(card["thumb_url"].replace(".thumb.png", ".png")).status_code == 404   # originals are not served
    e = p["explanation"]
    assert e["language"] == "pl" and e["text"] == "[Polish] explanation" and e["english"] == "English explanation"
    prompt = env["model"].prompts[-1]
    assert "ALREADY decided" in prompt and "Give the PayPal email" in prompt
    assert "1979-03-14" not in prompt and "Długa" not in prompt            # the record is never shown to it


def test_ana_marriage_certificate_then_review_with_a_reason(env):
    http, fake = env["http"], env["fake"]
    c = claim(http, "B002", "Ana Brennan", email="ana.b@example.com", former_names=["Ana Kowalski"])
    c = upload(env, c["id"], "partial/id_card.png", "partial/utility_bill.png", "partial/marriage_certificate.png")
    checks = {x["field"]: x["result"] for x in c["proof"]["checks"]}
    assert checks["name"] == "MATCH" and checks["address"] in ("PARTIAL", "MISMATCH")
    assert c["proof"]["verdict"] == "NEEDS_REVIEW" and c["proof"]["review"]["required"] is True
    assert http.post(f"/api/claims/{c['id']}/approve", json={"approver": "Claims desk", "reason": "  "}
                     ).status_code == 400
    c = http.post(f"/api/claims/{c['id']}/approve",
                  json={"approver": "Claims desk", "reason": "The bill shows her new Dublin address."}).json()
    assert c["status"] == "APPROVED" and c["proof"]["review"]["reason"] == "The bill shows her new Dublin address."
    c = http.post(f"/api/claims/{c['id']}/pay", json={"receiver": "sb-vgzm153858978@personal.example.com"}).json()
    assert c["status"] == "PAYING" and fake.payouts_created == 1


def test_edited_case_rejected_with_flag_and_no_hints(env):
    http = env["http"]
    c = claim(http, "B007", "Mohammed Al-Rashid", language="ar")
    c = upload(env, c["id"], "edited/id_card.png")
    p = c["proof"]
    assert c["status"] == "REJECTED" and p["verdict"] == "REJECTED"
    assert p["message"] == "We could not confirm this claim. Larkfield Market has been told."
    assert p["flags"] == [] and p["missing"] == [] and p["documents"] == [] and p["checks"] == []
    assert p["explanation"] is None and env["model"].prompts == []          # no model call for a rejection
    listed = business(http, c["id"])
    assert listed["status"] == "REJECTED" and listed["proof"]["missing"] == []
    assert listed["proof"]["flags"] == ["id_card.png shows signs of having been edited"]
    doc = listed["proof"]["documents"][0]
    assert doc["signs_of_editing"] is True and "typeface" in doc["concerns"][0]


def test_rejected_claim_gets_three_document_attempts(env):
    http = env["http"]
    c = claim(http, "B003", "Wei Zhang")
    for n in (1, 2, 3):
        c = upload(env, c["id"], "impostor/id_card.png")
        assert c["status"] == "REJECTED" and c["proof"]["attempts"] == n
        assert c["proof"]["attempts_left"] == 3 - n and c["proof"]["missing"] == []
    files = [("files[]", ("id_card.png", env["samples"]["impostor/id_card.png"].read_bytes(), "image/png"))]
    r = http.post(f"/api/claims/{c['id']}/documents", files=files)
    assert r.status_code == 409 and "closed" in r.json()["error"]


def test_timeline_titles_and_webhook_confirmation(env):
    http, fake = env["http"], env["fake"]
    c = claim(http, "B001", "Tomasz Wiśniewski")
    upload(env, c["id"], "clean/id_card.png", "clean/old_statement.png")
    http.post(f"/api/claims/{c['id']}/pay", json={"receiver": "tomasz.now@example.com"})
    item = fake.only_item()
    fake.set_item_status(item["payout_item_id"], "SUCCESS")
    event = fake.item_event(item["payout_item_id"], "PAYMENT.PAYOUTS-ITEM.SUCCEEDED", event_id="WH-9")
    headers = {"paypal-auth-algo": "SHA256withRSA", "paypal-cert-url": "https://example.org/cert",
               "paypal-transmission-id": "t9", "paypal-transmission-time": "2026-11-12T00:00:00Z",
               "paypal-transmission-sig": "good"}
    assert http.post("/api/webhooks/paypal", json=event, headers=headers).status_code == 200
    c = http.get(f"/api/claims/{c['id']}").json()
    titles = [t["title"] for t in c["payout"]["timeline"]]
    assert titles == ["Prepared", "Sent through PayPal", "Collected", "Fee invoice created",
                      "Fee invoice sent to Larkfield Market"]
    collected = c["payout"]["timeline"][2]
    assert collected["heard_by"] == "WEBHOOK" and "Confirmed by PayPal webhook." in collected["note"]
    assert c["payout"]["confirmed_by"] == "WEBHOOK"
    assert "Larkfield Market" in c["payout"]["timeline"][3]["note"]
    log = [a for a in env["store"].audit_log(c["id"]) if a["action"] == "paypal_webhook"]
    assert len(log) == 1 and "confirmed by PayPal webhook" in log[0]["detail"]


def test_polling_is_reported_as_polling(env):
    http, fake = env["http"], env["fake"]
    c = claim(http, "B001", "Tomasz Wiśniewski")
    upload(env, c["id"], "clean/id_card.png", "clean/old_statement.png")
    c = http.post(f"/api/claims/{c['id']}/pay", json={"receiver": "tomasz.now@example.com"}).json()
    fake.set_item_status(fake.only_item()["payout_item_id"], "SUCCESS")
    c = http.post(f"/api/claims/{c['id']}/refresh").json()
    assert c["payout"]["confirmed_by"] == "POLLING"
    assert not [a for a in env["store"].audit_log(c["id"]) if a["action"] == "paypal_webhook"]


def test_notice_in_the_owners_language(env, monkeypatch):
    http = env["http"]
    n = http.post("/api/balances/B001/notice").json()
    assert n["language"] == "pl" and n["subject"].startswith("[Polish]") and n["english"] == "English body"
    assert n["source"] == "LIVE"
    prompt = env["model"].prompts[-1]
    assert "Larkfield Market" in prompt and "$86.40" in prompt and "t.wisniewski" not in prompt
    assert http.post("/api/balances/B002/notice").json()["language"] == "en"
    assert http.post("/api/balances/B038/notice").status_code == 409
    assert http.post("/api/balances/NOPE/notice").status_code == 404
    # no model: the saved notice, else a plain 503
    monkeypatch.setenv("HOMING_MODEL", "off")
    env["saved"].data["notices"]["B001"] = {"language": "pl", "subject": "s", "body": "b", "english": "e",
                                            "english_subject": "es"}
    assert http.post("/api/balances/B001/notice").json()["source"] == "SAVED"
    r = http.post("/api/balances/B004/notice")
    assert r.status_code == 503 and "language model" in r.json()["error"]


def test_state_claim_packet_wording(env):
    http = env["http"]
    r = http.post("/api/claims", json={"balance_id": "B038", "claimant": {"name": "Rosa"}})
    assert r.status_code == 409 and "claim packet for the owner to review, sign and send" in r.json()["error"]
    assert "state's claim form" not in r.json()["error"]


@pytest.fixture
def no_model_app(tmp_path, monkeypatch):
    """The real reader with no model on the machine: saved readings for the sample documents only."""
    monkeypatch.setenv("HOMING_MODEL", "off")
    samples = make_samples(tmp_path / "samples")
    hashes = sample_hashes(samples)
    saved = assist.Saved(tmp_path / "saved.json")
    saved.data["readings"] = {hashes[k]: READINGS[k] for k in READINGS}
    saved.data["explanations"]["B001"] = {}
    store = Store(tmp_path / "homing.sqlite")
    app = create_app(store=store, settlement=None, use_default_settlement=False, saved=saved, demo=True,
                     ledger_path=write_ledger(tmp_path / "ledger.json"), files_dir=tmp_path / "files")
    with TestClient(app) as http:
        yield {"http": http, "samples": samples, "saved": saved}
    store.close()


def test_saved_readings_when_no_model(no_model_app):
    ctx = no_model_app
    http = ctx["http"]
    c = claim(http, "B001", "Tomasz Wiśniewski", language="pl")
    c = upload(ctx, c["id"], "clean/id_card.png", "clean/old_statement.png")
    assert c["proof"]["verdict"] == "APPROVED"
    assert {d["reading_source"] for d in c["proof"]["documents"]} == {"SAVED"}
    # no saved explanation for this result: a plain English sentence from code
    assert c["proof"]["explanation"]["language"] == "en" and c["proof"]["explanation"]["source"] == "PLAIN"
    other = claim(http, "B010", "María José García López", email="mj@example.org")
    files = [("files[]", ("photo.png", ctx["samples"]["clean/id_card.png"].read_bytes()[:-12] + b"x" * 12,
                          "image/png"))]
    o = http.post(f"/api/claims/{other['id']}/documents", files=files).json()
    doc = o["proof"]["documents"][0]
    assert doc["status"] == "failed" and "no language model is set up" in doc["problem"]
    assert o["proof"]["verdict"] == "NEEDS_REVIEW"


def test_saved_explanation_used_for_a_known_result(no_model_app):
    ctx = no_model_app
    http = ctx["http"]
    c = claim(http, "B001", "Tomasz Wiśniewski", language="pl")
    first = upload(ctx, c["id"], "clean/id_card.png", "clean/old_statement.png")
    sig = assist.signature(first["proof"])
    ctx["saved"].data["explanations"]["B001"] = {sig: {"pl": {"language": "pl", "text": "Tak.", "english": "Yes."}}}
    assert http.post("/api/demo/reset").status_code == 200      # so the same documents are not a reuse
    c2 = claim(http, "B001", "Tomasz Wiśniewski", language="pl")
    c2 = upload(ctx, c2["id"], "clean/id_card.png", "clean/old_statement.png")
    assert c2["proof"]["explanation"] == {"language": "pl", "text": "Tak.", "english": "Yes.", "source": "SAVED"}


def test_outside_demo_mode_sample_documents_need_a_person(tmp_path):
    samples = make_samples(tmp_path / "samples")
    from homing_fixtures import FakeReader
    store = Store(tmp_path / "homing.sqlite")
    app = create_app(store=store, settlement=None, use_default_settlement=False, reader=FakeReader(samples),
                     demo=False, saved=assist.Saved(tmp_path / "s.json"), ask=FakeModel(),
                     ledger_path=write_ledger(tmp_path / "ledger.json"), files_dir=tmp_path / "files")
    with TestClient(app) as http:
        ctx = {"http": http, "samples": samples}
        assert http.get("/api/meta").json()["demo"] is False
        c = claim(http, "B001", "Tomasz Wiśniewski")
        c = upload(ctx, c["id"], "clean/id_card.png", "clean/old_statement.png")
        assert c["proof"]["verdict"] == "NEEDS_REVIEW" and c["proof"]["demo"] is False
        doc = {x["field"]: x for x in c["proof"]["checks"]}["document"]
        assert doc["result"] == "PARTIAL" and "sample or specimen" in doc["note"]
        assert http.post(f"/api/claims/{c['id']}/approve", json={"approver": "x"}).status_code == 400
    store.close()


def test_reader_falls_back_only_when_the_model_is_unavailable(tmp_path):
    saved = assist.Saved(tmp_path / "s.json")
    saved.data["readings"]["abc"] = {"status": "read", "names": ["X"]}

    def down(path):
        raise llm.ModelUnavailable("signed out")

    read = assist.make_reader(saved, live=down)
    assert read(tmp_path / "f.png", "abc")["reading_source"] == "SAVED"
    assert read(tmp_path / "f.png", "zzz")["reading_source"] == "NONE"
    up = assist.make_reader(saved, live=lambda path: {"status": "read", "names": ["Live"]})
    assert up(tmp_path / "f.png", "abc") == {"status": "read", "names": ["Live"], "reading_source": "LIVE"}
