import pytest

from fake_paypal import Crash
from homing.paypal import PayPalClient, PayPalError
from homing.settlement import (CANCELLED, CLAIMED, CREATED, FAILED, RETURNED, SENT, UNCLAIMED, Settlement,
                               SettlementError, compute_fee)


def new_claim(s, cid="C1", amount="120.00", receiver="owner@example.com"):
    return s.create_claim(cid, receiver=receiver, amount=amount, currency="USD", note="Your money is home")


def sent_claim(s, fake, cid="C1", amount="120.00"):
    new_claim(s, cid, amount)
    c = s.send(cid)
    item_id = fake.batches[c.payout_batch_id]["item_ids"][0]
    return c, item_id


def states(s, cid):
    return [(a["from_state"], a["to_state"]) for a in s.audit(cid) if a["to_state"] and a["from_state"] != a["to_state"]]


# ------------------------------------------------------------ fee

@pytest.mark.parametrize("amount,fee", [("120.00", "12.00"), ("250", "25.00"), ("1000", "25.00"),
                                        ("0.05", "0.01"), ("1.00", "0.10")])
def test_fee_ten_percent_capped(amount, fee):
    assert compute_fee(amount) == fee


def test_fee_configurable(client, tmp_path):
    s = Settlement(client, tmp_path / "x.sqlite", fee_percent=5, fee_cap=None)
    c = new_claim(s, amount="1000")
    assert s.fee_for(c) == "50.00"


# ------------------------------------------------------------ transitions

def test_create_is_idempotent(settlement):
    a = new_claim(settlement)
    b = new_claim(settlement)
    assert a.claim_id == b.claim_id and b.state == CREATED
    assert a.sender_batch_id == "homing-C1-a1"
    with pytest.raises(SettlementError):
        new_claim(settlement, amount="99")


def test_created_to_sent(settlement, fake):
    c, item_id = sent_claim(settlement, fake)
    assert c.state == SENT and c.payout_batch_id
    sent = fake.items[item_id]["payout_item"]
    assert sent["receiver"] == "owner@example.com" and sent["amount"] == {"value": "120.00", "currency": "USD"}
    assert fake.batches[c.payout_batch_id]["batch_header"]["sender_batch_header"]["sender_batch_id"] == "homing-C1-a1"


def test_refresh_pending_stays_sent(settlement, fake):
    c, item_id = sent_claim(settlement, fake)
    c = settlement.refresh("C1")
    assert c.state == SENT and c.payout_item_id == item_id and c.paypal_status == "PENDING"


def test_sent_to_claimed_on_success_and_invoice(settlement, fake):
    _, item_id = sent_claim(settlement, fake)
    fake.set_item_status(item_id, "SUCCESS")
    c = settlement.refresh("C1")
    assert c.state == CLAIMED and not c.needs_contact
    assert c.invoice_id and c.invoice_status == "SENT" and c.fee == "12.00" and c.invoice_url
    inv = fake.invoices[c.invoice_id]
    assert len(c.invoice_number) < 25 and c.invoice_number == settlement.invoice_number_for("C1")
    assert inv["items"][0]["unit_amount"]["value"] == "12.00"
    assert inv["primary_recipients"][0]["billing_info"]["email_address"] == "holder@example.com"
    assert states(settlement, "C1") == [(None, CREATED), (CREATED, SENT), (SENT, CLAIMED)]


def test_sent_to_unclaimed_needs_contact(settlement, fake):
    _, item_id = sent_claim(settlement, fake)
    fake.set_item_status(item_id, "UNCLAIMED", "RECEIVER_UNREGISTERED")
    c = settlement.refresh("C1")
    assert c.state == UNCLAIMED and c.needs_contact and c.paypal_error == "RECEIVER_UNREGISTERED"
    assert [x.claim_id for x in settlement.needs_contact()] == ["C1"]
    assert c.invoice_id is None and fake.invoices_created == 0


def test_unclaimed_then_claimed(settlement, fake):
    _, item_id = sent_claim(settlement, fake)
    fake.set_item_status(item_id, "UNCLAIMED")
    settlement.refresh("C1")
    fake.set_item_status(item_id, "SUCCESS")
    c = settlement.refresh("C1")
    assert c.state == CLAIMED and not c.needs_contact and settlement.needs_contact() == []


@pytest.mark.parametrize("status,state", [("RETURNED", RETURNED), ("REFUNDED", RETURNED), ("FAILED", FAILED),
                                          ("DENIED", FAILED), ("BLOCKED", FAILED)])
def test_sent_to_other_terminal(settlement, fake, status, state):
    _, item_id = sent_claim(settlement, fake)
    fake.set_item_status(item_id, status)
    c = settlement.refresh("C1")
    assert c.state == state
    assert c.needs_contact == (state == RETURNED)
    assert c.invoice_id is None


def test_unclaimed_to_returned_after_30_days(settlement, fake):
    _, item_id = sent_claim(settlement, fake)
    fake.set_item_status(item_id, "UNCLAIMED")
    settlement.refresh("C1")
    fake.set_item_status(item_id, "RETURNED")
    c = settlement.refresh("C1")
    assert c.state == RETURNED and c.needs_contact


def test_claimed_only_on_success(settlement, fake):
    _, item_id = sent_claim(settlement, fake)
    for status in ["PENDING", "ONHOLD", "UNCLAIMED", "SOMETHING_NEW"]:
        fake.set_item_status(item_id, status)
        assert settlement.refresh("C1").state != CLAIMED
    for etype in ["PAYMENT.PAYOUTS-ITEM.HELD", "PAYMENT.PAYOUTS-ITEM.UNCLAIMED"]:
        settlement.apply_webhook(fake.item_event(item_id, etype))
        assert settlement.get("C1").state != CLAIMED


def test_cancel_unclaimed(settlement, fake):
    _, item_id = sent_claim(settlement, fake)
    fake.set_item_status(item_id, "UNCLAIMED")
    settlement.refresh("C1")
    c = settlement.cancel("C1")
    assert c.state == CANCELLED and not c.needs_contact
    actions = [a["action"] for a in settlement.audit("C1")]
    assert "cancel_requested" in actions and "cancel" in actions


def test_cancel_error_keeps_unclaimed(settlement, fake):
    _, item_id = sent_claim(settlement, fake)
    fake.set_item_status(item_id, "UNCLAIMED")
    settlement.refresh("C1")
    fake.fail_cancel = (500, "INTERNAL_SERVICE_ERROR")
    with pytest.raises(PayPalError) as e:
        settlement.cancel("C1")
    assert e.value.name == "INTERNAL_SERVICE_ERROR" and e.value.debug_id
    c = settlement.get("C1")
    assert c.state == UNCLAIMED and c.cancel_requested
    with pytest.raises(PayPalError):
        settlement.retarget("C1", receiver="new@example.com")  # no new payout while the old one is live
    assert fake.payouts_created == 1
    # PayPal later returns it on its own: it counts as our cancellation
    fake.set_item_status(item_id, "RETURNED")
    assert settlement.refresh("C1").state == CANCELLED


def test_cancel_only_when_unclaimed(settlement, fake):
    sent_claim(settlement, fake)
    with pytest.raises(SettlementError):
        settlement.cancel("C1")


def test_batch_denied_fails(settlement, fake):
    c, _ = sent_claim(settlement, fake)
    fake.batches[c.payout_batch_id]["batch_header"]["batch_status"] = "DENIED"
    assert settlement.apply_webhook(fake.batch_event(c.payout_batch_id, "PAYMENT.PAYOUTSBATCH.DENIED")) == "applied"
    assert settlement.get("C1").state == FAILED


def test_audit_carries_paypal_ids(settlement, fake):
    _, item_id = sent_claim(settlement, fake)
    fake.set_item_status(item_id, "SUCCESS")
    settlement.refresh("C1")
    rows = settlement.audit("C1")
    claimed = next(r for r in rows if r["to_state"] == CLAIMED)
    assert claimed["payout_item_id"] == item_id and claimed["payout_batch_id"] and claimed["sender_batch_id"]
    inv = next(r for r in rows if r["action"] == "invoice_create")
    assert inv["invoice_id"] == settlement.get("C1").invoice_id


# ------------------------------------------------------------ no double pay

def test_send_twice_pays_once(settlement, fake):
    new_claim(settlement)
    a = settlement.send("C1")
    b = settlement.send("C1")
    assert a.payout_batch_id == b.payout_batch_id and fake.payouts_created == 1 and fake.payout_posts == 1


def test_crash_after_paypal_accepts_then_retry(client, fake, tmp_path):
    db = tmp_path / "s.sqlite"
    s = Settlement(client, db, holder_email="holder@example.com")
    new_claim(s)
    fake.crash_after_create = 1
    with pytest.raises(Crash):
        s.send("C1")
    assert s.get("C1").state == CREATED  # we never heard back
    s.close()
    # process restarts with a fresh client and store on the same file
    s2 = Settlement(PayPalClient("id", "secret", transport=fake.transport()), db, holder_email="holder@example.com")
    c = s2.send("C1")
    assert c.state == SENT and fake.payouts_created == 1 and fake.payout_posts == 2
    assert c.payout_batch_id == fake.by_sender_batch["homing-C1-a1"]
    assert any(a["detail"] == "adopted_existing" for a in s2.audit("C1"))


def test_duplicate_batch_for_other_payout_refused(client, fake, settlement):
    client.create_payout(receiver="someone-else@example.com", amount="5", currency="USD",
                         sender_batch_id="homing-C1-a1")
    new_claim(settlement)
    with pytest.raises(SettlementError):
        settlement.send("C1")
    assert settlement.get("C1").state == CREATED and fake.payouts_created == 1


def test_webhook_before_send_recorded_adopts_batch(settlement, fake):
    new_claim(settlement)
    fake.crash_after_create = 1
    with pytest.raises(Crash):
        settlement.send("C1")
    item_id = fake.only_item()["payout_item_id"]
    fake.set_item_status(item_id, "UNCLAIMED")
    assert settlement.apply_webhook(fake.item_event(item_id, "PAYMENT.PAYOUTS-ITEM.UNCLAIMED")) == "applied"
    c = settlement.get("C1")
    assert c.state == UNCLAIMED and c.payout_item_id == item_id and c.payout_batch_id
    assert settlement.send("C1").state == UNCLAIMED and fake.payouts_created == 1


def test_retarget_after_return_uses_new_batch_id(settlement, fake):
    _, item_id = sent_claim(settlement, fake)
    fake.set_item_status(item_id, "RETURNED")
    settlement.refresh("C1")
    c = settlement.retarget("C1", receiver="+14155550100", receiver_type="PHONE")
    assert c.state == CREATED and c.attempt == 2 and c.sender_batch_id == "homing-C1-a2"
    c = settlement.send("C1")
    assert c.state == SENT and fake.payouts_created == 2
    assert settlement.needs_contact() == []


def test_retarget_unclaimed_cancels_first(settlement, fake):
    _, item_id = sent_claim(settlement, fake)
    fake.set_item_status(item_id, "UNCLAIMED")
    settlement.refresh("C1")
    c = settlement.retarget("C1", receiver="new@example.com")
    assert fake.items[item_id]["transaction_status"] == "RETURNED"
    assert c.state == CREATED and c.attempt == 2


@pytest.mark.parametrize("status", ["PENDING", "SUCCESS"])
def test_no_retarget_while_live_or_paid(settlement, fake, status):
    _, item_id = sent_claim(settlement, fake)
    fake.set_item_status(item_id, status)
    settlement.refresh("C1")
    with pytest.raises(SettlementError):
        settlement.retarget("C1", receiver="new@example.com")
    assert fake.payouts_created == 1


def test_stale_attempt_event_ignored(settlement, fake):
    _, old_item = sent_claim(settlement, fake)
    fake.set_item_status(old_item, "RETURNED")
    settlement.refresh("C1")
    settlement.retarget("C1", receiver="new@example.com")
    settlement.send("C1")
    fake.set_item_status(old_item, "SUCCESS")
    assert settlement.apply_webhook(fake.item_event(old_item, "PAYMENT.PAYOUTS-ITEM.SUCCEEDED")) == "stale_attempt"
    assert settlement.get("C1").state == SENT
    assert any("ALERT" in (a["detail"] or "") for a in settlement.audit("C1"))


# ------------------------------------------------------------ webhooks

def test_webhook_success_claims_and_invoices(settlement, fake):
    _, item_id = sent_claim(settlement, fake)
    fake.set_item_status(item_id, "SUCCESS")
    assert settlement.apply_webhook(fake.item_event(item_id, "PAYMENT.PAYOUTS-ITEM.SUCCEEDED")) == "applied"
    c = settlement.get("C1")
    assert c.state == CLAIMED and c.invoice_status == "SENT"


def test_duplicate_webhook(settlement, fake):
    _, item_id = sent_claim(settlement, fake)
    fake.set_item_status(item_id, "UNCLAIMED")
    ev = fake.item_event(item_id, "PAYMENT.PAYOUTS-ITEM.UNCLAIMED", "WH-dup")
    assert settlement.apply_webhook(ev) == "applied"
    n = len(settlement.audit("C1"))
    assert settlement.apply_webhook(ev) == "duplicate"
    assert settlement.apply_webhook(ev) == "duplicate"
    assert len(settlement.audit("C1")) == n and settlement.get("C1").state == UNCLAIMED


def test_duplicate_success_webhook_invoices_once(settlement, fake):
    _, item_id = sent_claim(settlement, fake)
    fake.set_item_status(item_id, "SUCCESS")
    ev = fake.item_event(item_id, "PAYMENT.PAYOUTS-ITEM.SUCCEEDED", "WH-s")
    settlement.apply_webhook(ev)
    settlement.apply_webhook(ev)
    settlement.apply_webhook(fake.item_event(item_id, "PAYMENT.PAYOUTS-ITEM.SUCCEEDED"))  # redelivered, new id
    assert fake.invoices_created == 1 and fake.invoice_sends == 1


def test_out_of_order_unclaimed_after_success(settlement, fake):
    _, item_id = sent_claim(settlement, fake)
    fake.set_item_status(item_id, "UNCLAIMED")
    late = fake.item_event(item_id, "PAYMENT.PAYOUTS-ITEM.UNCLAIMED")
    fake.set_item_status(item_id, "SUCCESS")
    settlement.apply_webhook(fake.item_event(item_id, "PAYMENT.PAYOUTS-ITEM.SUCCEEDED"))
    assert settlement.apply_webhook(late) == "ignored"
    c = settlement.get("C1")
    assert c.state == CLAIMED and not c.needs_contact


def test_out_of_order_held_after_unclaimed(settlement, fake):
    _, item_id = sent_claim(settlement, fake)
    settlement.apply_webhook(fake.item_event(item_id, "PAYMENT.PAYOUTS-ITEM.UNCLAIMED"))
    assert settlement.apply_webhook(fake.item_event(item_id, "PAYMENT.PAYOUTS-ITEM.HELD")) == "ignored"
    assert settlement.get("C1").state == UNCLAIMED


def test_out_of_order_unclaimed_after_returned(settlement, fake):
    _, item_id = sent_claim(settlement, fake)
    settlement.apply_webhook(fake.item_event(item_id, "PAYMENT.PAYOUTS-ITEM.RETURNED"))
    assert settlement.apply_webhook(fake.item_event(item_id, "PAYMENT.PAYOUTS-ITEM.UNCLAIMED")) == "ignored"
    assert settlement.apply_webhook(fake.item_event(item_id, "PAYMENT.PAYOUTS-ITEM.SUCCEEDED")) == "ignored"
    c = settlement.get("C1")
    assert c.state == RETURNED and c.invoice_id is None and fake.invoices_created == 0


def test_canceled_event(settlement, fake):
    _, item_id = sent_claim(settlement, fake)
    settlement.apply_webhook(fake.item_event(item_id, "PAYMENT.PAYOUTS-ITEM.UNCLAIMED"))
    settlement.apply_webhook(fake.item_event(item_id, "PAYMENT.PAYOUTS-ITEM.CANCELED"))
    assert settlement.get("C1").state == CANCELLED


def test_batch_success_event_polls_item(settlement, fake):
    c, item_id = sent_claim(settlement, fake)
    fake.set_item_status(item_id, "UNCLAIMED")
    assert settlement.apply_webhook(fake.batch_event(c.payout_batch_id, "PAYMENT.PAYOUTSBATCH.SUCCESS")) == "ignored"
    assert settlement.get("C1").state == UNCLAIMED


def test_unknown_event(settlement, fake):
    assert settlement.apply_webhook({"id": "WH-x", "event_type": "PAYMENT.PAYOUTS-ITEM.SUCCEEDED",
                                     "resource": {"payout_item_id": "ZZZ"}}) == "unknown_claim"


def test_handle_webhook_verifies(client, fake, tmp_path):
    hook = client.create_webhook("https://example.com/hook")
    s = Settlement(client, tmp_path / "s.sqlite", holder_email="holder@example.com", webhook_id=hook["id"])
    _, item_id = sent_claim(s, fake)
    fake.set_item_status(item_id, "SUCCESS")
    ev = fake.item_event(item_id, "PAYMENT.PAYOUTS-ITEM.SUCCEEDED")
    headers = {"paypal-auth-algo": "a", "paypal-cert-url": "c", "paypal-transmission-id": "t",
               "paypal-transmission-time": "x", "paypal-transmission-sig": "forged"}
    assert s.handle_webhook(headers, ev) == "rejected"
    assert s.get("C1").state == SENT
    assert s.handle_webhook({**headers, "paypal-transmission-sig": "good"}, ev) == "applied"
    assert s.get("C1").state == CLAIMED


def test_invoice_paid_webhook(settlement, fake):
    _, item_id = sent_claim(settlement, fake)
    fake.set_item_status(item_id, "SUCCESS")
    c = settlement.refresh("C1")
    ev = {"id": "WH-inv", "event_type": "INVOICING.INVOICE.PAID", "resource": {"invoice": {"id": c.invoice_id}}}
    assert settlement.apply_webhook(ev) == "applied"
    assert settlement.get("C1").invoice_status == "PAID"
    assert settlement.apply_webhook(ev) == "duplicate"


# ------------------------------------------------------------ invoice exactly once

def test_invoice_once_across_refreshes_and_restart(client, fake, tmp_path):
    db = tmp_path / "s.sqlite"
    s = Settlement(client, db, holder_email="holder@example.com")
    _, item_id = sent_claim(s, fake)
    fake.set_item_status(item_id, "SUCCESS")
    for _ in range(3):
        s.refresh("C1")
        s.ensure_invoice("C1")
    s.refresh_open()
    s.close()
    s2 = Settlement(client, db, holder_email="holder@example.com")
    s2.refresh("C1")
    s2.ensure_invoice("C1")
    assert fake.invoices_created == 1 and fake.invoice_sends == 1


def test_invoice_crash_after_create_recovers_without_second_invoice(settlement, fake):
    _, item_id = sent_claim(settlement, fake)
    fake.set_item_status(item_id, "SUCCESS")
    fake.crash_after_invoice_create = 1
    c = settlement.refresh("C1")  # the invoice failure is audited, the payout result still stands
    assert c.state == CLAIMED and c.invoice_id is None
    assert any(a["action"] == "invoice_error" for a in settlement.audit("C1"))
    c = settlement.ensure_invoice("C1")
    assert c.invoice_status == "SENT" and fake.invoices_created == 1 and fake.invoice_sends == 1
    assert any("adopted_existing" in (a["detail"] or "") for a in settlement.audit("C1"))


def test_invoice_crash_after_send_does_not_resend(settlement, fake):
    _, item_id = sent_claim(settlement, fake)
    fake.set_item_status(item_id, "SUCCESS")
    settlement.auto_invoice = False
    settlement.refresh("C1")
    settlement.ensure_invoice("C1")
    # simulate losing the "SENT" write: the row still says DRAFT
    settlement._conn().execute("UPDATE claims SET invoice_status='DRAFT' WHERE claim_id='C1'")
    c = settlement.ensure_invoice("C1")
    assert c.invoice_status == "SENT" and fake.invoice_sends == 1 and fake.invoices_created == 1


def test_no_invoice_without_holder_email(client, fake, tmp_path):
    s = Settlement(client, tmp_path / "s.sqlite")
    _, item_id = sent_claim(s, fake)
    fake.set_item_status(item_id, "SUCCESS")
    c = s.refresh("C1")
    assert c.state == CLAIMED and c.invoice_id is None and fake.invoices_created == 0


def test_db_path_env_override(client, tmp_path, monkeypatch):
    monkeypatch.setenv("HOMING_SETTLEMENT_DB", str(tmp_path / "env.sqlite"))
    s = Settlement(client)
    assert s.db_path == tmp_path / "env.sqlite" and s.db_path.exists()
