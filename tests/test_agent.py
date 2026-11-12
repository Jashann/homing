"""The desk agent: the loop, its tools and the code gate on pay_claim, with fake PayPal and a fake model."""
import json
import time

import pytest
from fastapi.testclient import TestClient

from fake_paypal import FakePayPal
from homing_fixtures import FakeModel, FakeReader, make_samples, write_ledger
from homing import agent, assist
from homing.paypal import PayPalClient
from homing.server import create_app
from homing.settlement import Settlement
from homing.store import Store
from test_server import claim, upload

ADDRESS = "sb-vgzm153858978@personal.example.com"
REASON = "Marriage certificate registered in Chicago links Kowalski to Brennan, and the date of birth matches."


class Script:
    """A fake model: answers from a list of replies (strings or dicts), recording each prompt."""

    def __init__(self, replies, match=None):
        self.replies, self.prompts, self.match = list(replies), [], match

    def __call__(self, prompt):
        if self.match and self.match not in prompt.split("TASK:")[-1].split("What you have done")[0]:
            return json.dumps({"done": True, "summary": "not this task"})
        self.prompts.append(prompt)
        r = self.replies.pop(0) if self.replies else {"done": True, "summary": "nothing left"}
        return r if isinstance(r, str) else json.dumps(r)


def tool(name, args):
    return {"say": f"Calling {name}.", "tool": name, "args": args}


@pytest.fixture
def desk(tmp_path):
    fake = FakePayPal()
    client = PayPalClient("test-id", "test-secret", transport=fake.transport())
    money = Settlement(client, tmp_path / "settlement.sqlite", holder_email="holder@example.com", prefix="homing",
                       holder_name="Larkfield Market")
    samples = make_samples(tmp_path / "samples")
    store = Store(tmp_path / "homing.sqlite")
    script = Script([])
    app = create_app(store=store, settlement=money, reader=FakeReader(samples), ledger_path=write_ledger(
        tmp_path / "ledger.json"), files_dir=tmp_path / "files", demo=True, saved=assist.Saved(tmp_path / "s.json"),
        ask=FakeModel(), agent_ask=script, payout_history_path=tmp_path / "h.json",
        saved_traces_path=tmp_path / "traces.json")
    with TestClient(app) as http:
        yield {"http": http, "fake": fake, "money": money, "samples": samples, "script": script, "store": store,
               "app": app}
    money.close()
    client.close()
    store.close()


def run(desk, replies, mode="queue", only=None, dry_pay=False):
    desk["script"].replies[:] = replies
    desk["script"].match = only
    r = desk["http"].post("/api/agent/runs", json={"mode": mode, "dry_pay": dry_pay})
    assert r.status_code == 200, r.text
    rid = r.json()["id"]
    for _ in range(200):
        out = desk["http"].get(f"/api/agent/runs/{rid}").json()
        if out["status"] == "done":
            return out
        time.sleep(0.05)
    raise AssertionError("run did not finish")


def ana(desk, approve=True, address=True):
    http = desk["http"]
    c = claim(http, "B002", "Ana Brennan", former_names=["Ana Kowalski"])
    c = upload(desk, c["id"], "partial/id_card.png", "partial/utility_bill.png", "partial/marriage_certificate.png")
    assert c["proof"]["verdict"] == "NEEDS_REVIEW"
    if address:
        r = http.post(f"/api/claims/{c['id']}/payout-address", json={"receiver": ADDRESS})
        assert r.status_code == 200 and r.json()["payout_address"] == ADDRESS
    if approve:
        assert http.post(f"/api/claims/{c['id']}/approve", json={"approver": "Desk", "reason": REASON}).status_code == 200
    return c["id"]


def edited(desk):
    c = claim(desk["http"], "B007", "Mohammed Al-Rashid")
    c = upload(desk, c["id"], "edited/id_card.png")
    assert c["status"] == "REJECTED"
    return c["id"]


# ---- the loop

def tools_for(fn=lambda **k: ("ok", {})):
    return {"t": agent.Tool("t", "x", "test", fn)}


def test_loop_runs_tools_and_records_steps():
    seen = []
    tools = tools_for(lambda x: (seen.append(x) or f"got {x}", {"x": x}))
    model = Script([tool("t", {"x": 1}), tool("t", {"x": 2}), {"done": True, "summary": "all done"}])
    rec = agent.run_task("do it", tools, ask=model)
    assert seen == [1, 2] and rec["status"] == "done" and rec["summary"] == "all done"
    assert [s["outcome"] for s in rec["steps"]] == ["allowed", "allowed"]
    assert all(s["say"] and s["started_at"] and s["ended_at"] for s in rec["steps"])
    assert "t({\"x\": 1}) -> got 1" in model.prompts[1]   # the result goes back to the model


def test_bad_json_is_retried_once_then_stops():
    model = Script(["not json at all", tool("t", {"x": 1}), {"done": True, "summary": "ok"}])
    rec = agent.run_task("do it", tools_for(lambda x: ("fine", {})), ask=model)
    assert rec["status"] == "done" and len(rec["steps"]) == 1
    assert "could not be used" in model.prompts[1]
    model = Script(["nope", '{"tool": "missing", "args": {}}'])
    rec = agent.run_task("do it", tools_for(), ask=model)
    assert rec["status"] == "error" and rec["steps"][-1]["outcome"] == "error"


def test_json_in_a_fence_is_read():
    model = Script(['Sure:\n```json\n{"say": "a", "tool": "t", "args": {"x": 3}}\n```', {"done": True}])
    rec = agent.run_task("do it", tools_for(lambda x: (str(x), {})), ask=model)
    assert rec["steps"][0]["result"] == "3"


def test_step_cap():
    model = Script([tool("t", {"x": i}) for i in range(20)])
    rec = agent.run_task("loop", tools_for(lambda x: ("again", {})), ask=model, max_steps=8)
    assert rec["status"] == "stopped" and len(rec["steps"]) == 8


def test_refusal_is_a_result_not_a_crash():
    def no(**k):
        raise agent.Refused("not allowed")
    model = Script([tool("t", {}), {"done": True, "summary": "stopped"}])
    rec = agent.run_task("x", tools_for(no), ask=model)
    assert rec["steps"][0]["outcome"] == "refused" and rec["steps"][0]["result"] == "Refused by code: not allowed"
    assert "Refused by code: not allowed" in model.prompts[1]


# ---- the tools through the API

def test_paypal_tools_ask_paypal_live_and_hold_and_notice(desk):
    fake, http = desk["fake"], desk["http"]
    money = desk["money"]
    batch = money.client.create_payout(receiver="seller@example.com", amount="18.50", currency="USD",
                                       sender_batch_id="seed-1")
    bid = batch.batch_id
    item_id = fake.only_item()["payout_item_id"]
    fake.set_item_status(item_id, "UNCLAIMED")
    hist = {"payouts": [{"seller": "Elena Marchetti", "city": "Bologna", "country": "IT", "payout_batch_id": bid}]}
    from pathlib import Path
    Path(http.app.state.store.path).parent.joinpath("h.json").write_text(json.dumps(hist))
    assert http.post("/api/import/paypal").json()["added"] == 1
    bal = f"PP-{item_id}"
    returns = http.get("/api/ledger").json()["balances"]
    back = [b for b in returns if b["id"] == bal][0]["paypal"]["returns_on"]
    out = run(desk, [tool("paypal_payout_item", {"item_id": item_id}), tool("paypal_batch", {"batch_id": bid}),
                     tool("hold", {"balance_id": bal, "until": "2026-11-13", "reason": "too short"}),
                     tool("hold", {"balance_id": bal, "until": back, "reason": "PayPal still holds it"}),
                     tool("draft_notice", {"balance_id": bal}), {"done": True, "summary": "held"}], only="Balance PP-")
    task = [t for t in out["tasks"] if t["kind"] == "paypal"][0]
    s = task["steps"]
    assert "UNCLAIMED" in s[0]["result"] and s[0]["outcome"] == "allowed"
    assert bid in s[1]["result"]
    assert s[2]["outcome"] == "refused" and "never paid twice" in s[2]["result"]
    assert s[3]["outcome"] == "allowed" and desk["store"].balance(bal)["hold"]["until"] == back
    assert s[4]["outcome"] == "allowed" and "not sent" in s[4]["result"]
    assert out["summary"]["held"] == 1 and out["summary"]["refused"] == 1


def test_ledger_lookup_and_send_to_person(desk):
    out = run(desk, [tool("ledger_lookup", {"query": "Wei Zhang"}),
                     tool("send_to_person", {"balance_id": "B003", "reason": "two owners share the name"}),
                     tool("send_to_person", {"balance_id": "B004", "reason": ""}),
                     {"done": True, "summary": "sent"}], only="Wei Zhang")
    task = [t for t in out["tasks"] if t["kind"] == "same_name"][0]
    s = task["steps"]
    assert "B003" in s[0]["result"] and "B004" in s[0]["result"] and s[0]["data"]["same_name"]
    assert s[1]["outcome"] == "allowed" and desk["store"].balance("B003")["review"]["by"] == "desk agent"
    assert s[2]["outcome"] == "refused"


def test_pay_refused_for_turned_down_claim(desk):
    cid = edited(desk)
    out = run(desk, [tool("pay_claim", {"claim_id": cid}), {"done": True, "summary": "refused"}], mode="brakes")
    s = out["tasks"][0]["steps"][0]
    assert s["outcome"] == "refused" and "turned down" in s["result"]
    assert desk["money"].claims() == [] and desk["fake"].payouts_created == 0
    assert out["summary"]["refused"] == 1 and out["summary"]["paid"] == 0


def test_pay_refused_without_person_or_address(desk):
    cid = ana(desk, approve=False, address=True)
    out = run(desk, [tool("claim_status", {"claim_id": cid}), tool("pay_claim", {"claim_id": cid}),
                     {"done": True, "summary": "left for a person"}], only=cid)
    s = [t for t in out["tasks"] if t.get("claim_id") == cid][0]["steps"]
    assert "Code would refuse payment" in s[0]["result"]
    assert s[1]["outcome"] == "refused" and "no person has approved" in s[1]["result"]
    assert desk["money"].claims() == []


def test_pay_refused_without_address(desk):
    cid = ana(desk, approve=True, address=False)
    out = run(desk, [tool("pay_claim", {"claim_id": cid}), {"done": True, "summary": "no address"}], only=cid)
    s = [t for t in out["tasks"] if t.get("claim_id") == cid][0]["steps"]
    assert s[0]["outcome"] == "refused" and "PayPal address" in s[0]["result"]


def test_pay_allowed_for_approved_claim_with_address(desk):
    cid = ana(desk)
    fake = desk["fake"]
    replies = [tool("claim_status", {"claim_id": cid}), tool("pay_claim", {"claim_id": cid})]
    out = run(desk, replies + [tool("pay_claim", {"claim_id": cid}), {"done": True, "summary": "paid"}], only=cid)
    s = [t for t in out["tasks"] if t.get("claim_id") == cid][0]["steps"]
    assert s[0]["data"]["payable"] is True
    assert s[1]["outcome"] == "allowed" and "Sent $212.75" in s[1]["result"]
    st = desk["money"].get(cid)
    assert st.receiver == ADDRESS and st.amount == "212.75"
    assert s[2]["outcome"] == "refused" and "never paid twice" in s[2]["result"]   # second call: still once
    assert fake.payouts_created == 1


def test_dry_pay_sends_nothing(desk):
    cid = ana(desk)
    out = run(desk, [tool("pay_claim", {"claim_id": cid}), {"done": True, "summary": "rehearsed"}], dry_pay=True, only=cid)
    s = [t for t in out["tasks"] if t.get("claim_id") == cid][0]["steps"]
    assert s[0]["outcome"] == "allowed" and "Nothing was sent" in s[0]["result"]
    assert desk["money"].claims() == []


def test_wait_for_paypal_confirms_and_invoices(desk):
    cid = ana(desk)
    run(desk, [tool("pay_claim", {"claim_id": cid}), {"done": True}], only=cid)
    desk["money"].refresh(cid)
    desk["fake"].set_item_status(desk["money"].get(cid).payout_item_id, "SUCCESS")
    wait = desk["app"].state.desk_tools()["wait_for_paypal"]
    text, data = wait.fn(claim_id=cid)
    assert data["state"] == "CLAIMED" and data["confirmed_by"] == "POLLING"
    assert data["invoice"]["status"] == "SENT" and "Fee invoice" in text
    assert agent.summarise([{"steps": [{"tool": "wait_for_paypal", "outcome": "allowed", "data": data}]}])["paid"] == 1


def test_saved_traces_endpoint(desk, tmp_path):
    assert desk["http"].get("/api/agent/saved").status_code == 404
    (tmp_path / "traces.json").write_text(json.dumps({"label": "Saved run", "queue": {"tasks": []}}))
    assert desk["http"].get("/api/agent/saved").json()["label"] == "Saved run"
