"""Public demo mode (HOMING_PUBLIC=1): payouts only to sandbox test addresses, a daily cap read from the settlement
database, one reset a minute, smaller uploads, and the saved desk-agent run when no model is connected."""
import json
from datetime import datetime, timezone
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from fake_paypal import FakePayPal
from homing_fixtures import FakeModel, FakeReader, make_samples, write_ledger
from homing import agent, assist
from homing.paypal import PayPalClient
from homing.server import (PUBLIC_CAP_TEXT, PUBLIC_RECEIVER_TEXT, PUBLIC_SAVED_RUN_NOTE, create_app,
                           public_receiver_allowed)
from homing.settlement import Settlement
from homing.store import Store
from test_agent import Script, run, tool
from test_server import claim, upload

SANDBOX = "sb-vgzm153858978@personal.example.com"
REASON = "Marriage certificate registered in Chicago links Kowalski to Brennan, and the date of birth matches."


@pytest.fixture
def make(tmp_path):
    made = []

    def build(cap="300", agent_ask=None):
        fake = FakePayPal()
        client = PayPalClient("test-id", "test-secret", transport=fake.transport())
        money = Settlement(client, tmp_path / "settlement.sqlite", holder_email="holder@example.com",
                           prefix="homing", holder_name="Larkfield Market")
        samples = make_samples(tmp_path / "samples")
        store = Store(tmp_path / "homing.sqlite")
        clock = {"now": 1000.0}
        script = agent_ask if agent_ask is not None else Script([])
        app = create_app(store=store, settlement=money, reader=FakeReader(samples),
                         ledger_path=write_ledger(tmp_path / "ledger.json"), files_dir=tmp_path / "files", demo=True,
                         saved=assist.Saved(tmp_path / "s.json"), ask=FakeModel(),
                         agent_ask=script or None, payout_history_path=tmp_path / "h.json",
                         saved_traces_path=tmp_path / "traces.json", public=True, public_cap=cap,
                         clock=lambda: clock["now"])
        http = TestClient(app)
        http.__enter__()
        env = {"http": http, "fake": fake, "money": money, "samples": samples, "store": store, "app": app,
               "clock": clock, "script": script}
        made.append((http, money, client, store))
        return env

    yield build
    for http, money, client, store in made:
        http.__exit__(None, None, None)
        money.close()
        client.close()
        store.close()


def approved_tomasz(env):
    c = claim(env["http"], "B001", "Tomasz Wiśniewski")          # 86.40 USD
    c = upload(env, c["id"], "clean/id_card.png", "clean/old_statement.png")
    assert c["status"] == "APPROVED"
    return c["id"]


def approved_ana(env, address=SANDBOX):
    http = env["http"]
    c = claim(http, "B002", "Ana Brennan", email="ana.b@example.com", former_names=["Ana Kowalski"])   # 212.75
    c = upload(env, c["id"], "partial/id_card.png", "partial/utility_bill.png", "partial/marriage_certificate.png")
    assert http.post(f"/api/claims/{c['id']}/payout-address", json={"receiver": address}).status_code == 200
    assert http.post(f"/api/claims/{c['id']}/approve", json={"approver": "Desk", "reason": REASON}).status_code == 200
    return c["id"]


def test_receiver_rule():
    assert public_receiver_allowed("sb-43y9ma53877422@personal.example.com")
    assert public_receiver_allowed("Tomasz.Now@Example.com")
    assert not public_receiver_allowed("someone@gmail.com")
    assert not public_receiver_allowed("x@larkfield-market.example.com")
    assert not public_receiver_allowed("x@example.com.attacker.org")
    assert not public_receiver_allowed("+353861234567")
    assert not public_receiver_allowed("@example.com")


def test_sandbox_receiver_is_paid(make):
    env = make()
    cid = approved_tomasz(env)
    r = env["http"].post(f"/api/claims/{cid}/pay", json={"receiver": SANDBOX})
    assert r.status_code == 200, r.text
    assert r.json()["payout"]["state"] == "SENT" and env["fake"].payouts_created == 1
    assert env["money"].sent_total_since(datetime(2000, 1, 1, tzinfo=timezone.utc)) == Decimal("86.40")


def test_other_receiver_is_refused_and_nothing_changes(make):
    env = make()
    cid = approved_tomasz(env)
    r = env["http"].post(f"/api/claims/{cid}/pay", json={"receiver": "tomasz@gmail.com"})
    assert r.status_code == 403 and r.json()["error"] == PUBLIC_RECEIVER_TEXT
    assert env["fake"].payouts_created == 0 and env["money"].claims() == []
    assert env["http"].get(f"/api/claims/{cid}").json()["status"] == "APPROVED"
    r = env["http"].post(f"/api/claims/{cid}/pay", json={"receiver": "+353861234567", "receiver_type": "PHONE"})
    assert r.status_code == 403
    assert env["http"].post(f"/api/claims/{cid}/pay", json={"receiver": SANDBOX}).status_code == 200


def test_new_contact_goes_through_the_guard(make):
    env = make()
    http, fake = env["http"], env["fake"]
    cid = approved_tomasz(env)
    c = http.post(f"/api/claims/{cid}/pay", json={"receiver": SANDBOX}).json()
    fake.set_item_status(c["payout"]["item_id"] or fake.only_item()["payout_item_id"], "UNCLAIMED")
    assert http.post(f"/api/claims/{cid}/refresh").json()["status"] == "NEEDS_NEW_CONTACT"
    r = http.post(f"/api/claims/{cid}/new-contact", json={"receiver": "tomasz@gmail.com"})
    assert r.status_code == 403 and r.json()["error"] == PUBLIC_RECEIVER_TEXT
    assert fake.payouts_created == 1 and env["money"].get(cid).attempt == 1


def test_daily_cap_counts_todays_payouts(make):
    env = make(cap="250")
    http = env["http"]
    t = approved_tomasz(env)
    assert http.post(f"/api/claims/{t}/pay", json={"receiver": SANDBOX}).status_code == 200      # 86.40
    a = approved_ana(env, address="sb-43y9ma53877422@personal.example.com")
    r = http.post(f"/api/claims/{a}/pay", json={"receiver": "sb-43y9ma53877422@personal.example.com"})
    assert r.status_code == 429 and r.json()["error"] == PUBLIC_CAP_TEXT                         # 86.40 + 212.75
    assert env["fake"].payouts_created == 1


def test_cap_backstop_at_send_time(make):
    """A payout created earlier but not yet sent is still checked against the cap when it is sent."""
    env = make(cap="50")
    cid = approved_tomasz(env)
    env["money"].create_claim(cid, receiver=SANDBOX, amount="86.40", currency="USD")
    r = env["http"].post(f"/api/claims/{cid}/pay", json={"receiver": SANDBOX})
    assert r.status_code == 429 and env["fake"].payouts_created == 0


def test_agent_pay_claim_goes_through_the_same_guard(make):
    env = make()
    cid = approved_ana(env, address="ana.brennan@gmail.com")
    out = run(env, [tool("pay_claim", {"claim_id": cid}), {"done": True, "summary": "refused"}], only=cid)
    s = [t for t in out["tasks"] if t.get("claim_id") == cid][0]["steps"]
    assert s[0]["outcome"] == "refused" and s[0]["result"].endswith(PUBLIC_RECEIVER_TEXT)
    assert env["fake"].payouts_created == 0


def test_agent_pay_claim_respects_the_cap(make):
    env = make(cap="100")
    cid = approved_ana(env)
    pay_claim = env["app"].state.desk_tools()["pay_claim"]
    with pytest.raises(agent.Refused) as e:
        pay_claim.fn(claim_id=cid)
    assert str(e.value) == PUBLIC_CAP_TEXT and env["fake"].payouts_created == 0


def test_reset_once_a_minute(make):
    env = make()
    http, clock = env["http"], env["clock"]
    assert http.post("/api/demo/reset").status_code == 200
    r = http.post("/api/demo/reset")
    assert r.status_code == 429 and "less than a minute" in r.json()["error"]
    clock["now"] += 61
    assert http.post("/api/demo/reset").status_code == 200


def test_upload_limits(make):
    env = make()
    http = env["http"]
    c = claim(http, "B001", "Tomasz Wiśniewski")
    big = ("files[]", ("big.png", b"\x89PNG\r\n\x1a\n" + b"0" * (5 * 1024 * 1024), "image/png"))
    r = http.post(f"/api/claims/{c['id']}/documents", files=[big])
    assert r.status_code == 413 and "5 MB" in r.json()["error"]
    png = env["samples"]["clean/id_card.png"].read_bytes()
    six = [("files[]", (f"p{i}.png", png, "image/png")) for i in range(6)]
    r = http.post(f"/api/claims/{c['id']}/documents", files=six)
    assert r.status_code == 413 and "at most 5 files" in r.json()["error"]


def test_meta_reports_public(make):
    m = make()["http"].get("/api/meta").json()
    assert m["public"] is True and isinstance(m["model_available"], bool)


def test_no_model_answers_with_the_saved_run(make, tmp_path, monkeypatch):
    monkeypatch.setenv("HOMING_MODEL", "off")
    env = make(agent_ask=False)                  # no injected model, and the real one is turned off
    (tmp_path / "traces.json").write_text(json.dumps({"label": "Saved run", "queue": {
        "id": "Rsaved", "mode": "queue", "label": "Saved run", "status": "done", "tasks": [], "summary": None}}))
    r = env["http"].post("/api/agent/runs", json={"mode": "queue"})
    assert r.status_code == 200
    out = r.json()
    assert out["label"] == "Saved run" and out["status"] == "done" and out["note"] == PUBLIC_SAVED_RUN_NOTE
    assert env["http"].get(f"/api/agent/runs/{out['id']}").json()["label"] == "Saved run"
    assert env["fake"].payouts_created == 0
