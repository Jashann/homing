"""Live PayPal sandbox tests. Run with: uv run pytest -m live -q -s"""
import os
import time
import uuid

import pytest

from homing.paypal import (DEFAULT_CREDENTIALS_FILE, DuplicateBatchError, DuplicateInvoiceNumberError, LineItem,
                           PayPalClient, PayPalError, WEBHOOK_EVENTS, credentials_available)
from homing.settlement import CANCELLED, SENT, UNCLAIMED, Settlement, compute_fee

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(not (DEFAULT_CREDENTIALS_FILE.exists() and credentials_available()),
                       reason="no PayPal sandbox credentials file"),
]


@pytest.fixture(scope="module")
def live():
    c = PayPalClient()
    yield c
    c.close()


def wait(fn, done, timeout=120, every=3.0):
    t0 = time.time()
    while True:
        v = fn()
        if done(v) or time.time() - t0 > timeout:
            return v, time.time() - t0
        time.sleep(every)


def test_live_payout_unclaimed_then_cancel(live, tmp_path):
    s = Settlement(live, tmp_path / "live.sqlite", holder_email=f"holder-{uuid.uuid4().hex[:6]}@example.com",
                   prefix="homing-live")
    cid = f"T{uuid.uuid4().hex[:10]}"
    s.create_claim(cid, receiver=f"owner-{uuid.uuid4().hex[:8]}@example.com", amount="1.00", currency="USD",
                   note="Test reunion payout")
    c = s.send(cid)
    assert c.state == SENT and c.payout_batch_id
    print(f"\nbatch {c.payout_batch_id} sender_batch_id {c.sender_batch_id}")

    # retry with the same sender_batch_id: PayPal refuses and names the same batch, nothing new is paid
    with pytest.raises(DuplicateBatchError) as e:
        live.create_payout(receiver=c.receiver, amount=c.amount, currency=c.currency,
                           sender_batch_id=c.sender_batch_id, note=c.note)
    assert e.value.existing_batch_id == c.payout_batch_id
    assert s.send(cid).payout_batch_id == c.payout_batch_id

    c, took = wait(lambda: s.refresh(cid), lambda x: x.state != SENT)
    print(f"item {c.payout_item_id} -> {c.state} ({c.paypal_error}) after {took:.0f}s")
    assert c.state == UNCLAIMED and c.paypal_error == "RECEIVER_UNREGISTERED" and c.needs_contact
    item = live.get_payout_item(c.payout_item_id)
    assert item.fee == "0.25" and item.fee_currency == "USD"

    # cancel needs the batch to be finished (BATCH_NOT_COMPLETED before that)
    batch, took = wait(lambda: live.get_payout_batch(c.payout_batch_id), lambda b: b.status == "SUCCESS")
    print(f"batch {batch.status} after {took:.0f}s more")
    try:
        c = s.cancel(cid)
    except PayPalError as err:
        assert s.get(cid).state == UNCLAIMED and s.get(cid).cancel_requested
        if err.status >= 500:
            pytest.xfail(f"sandbox cancel returned {err.status} {err.name} debug_id={err.debug_id}")
        raise
    assert c.state == CANCELLED


def test_live_invoice_create_send(live):
    number = live.next_invoice_number()
    assert number
    fee = compute_fee("1.00")
    inv_no = f"HOMING-LIVE-{uuid.uuid4().hex[:10]}"
    inv = live.create_invoice(recipient_email=f"holder-{uuid.uuid4().hex[:6]}@example.com",
                              items=[LineItem("Owner found and paid (test)", fee)], invoice_number=inv_no,
                              note="Test fee invoice")
    assert inv.status == "DRAFT" and inv.number == inv_no
    with pytest.raises(DuplicateInvoiceNumberError):
        live.create_invoice(recipient_email="x@example.com", items=[LineItem("x", fee)], invoice_number=inv_no)
    link = live.send_invoice(inv.invoice_id)
    got = live.get_invoice(inv.invoice_id)
    print(f"\ninvoice {inv.invoice_id} {got.status} payer view {link}")
    assert got.status == "SENT" and link and "invoice/p/" in link
    assert live.find_invoice_by_number(inv_no).invoice_id == inv.invoice_id
    live.cancel_invoice(inv.invoice_id)
    assert live.get_invoice(inv.invoice_id).status == "CANCELLED"


def test_live_webhook_create_list_verify(live):
    url = f"https://example.com/homing/webhook/{uuid.uuid4().hex[:8]}"
    hook = live.create_webhook(url)
    try:
        assert {e["name"] for e in hook["event_types"]} == set(WEBHOOK_EVENTS)
        assert live.create_webhook(url)["id"] == hook["id"]
        assert hook["id"] in [w["id"] for w in live.list_webhooks()]
        forged = {"paypal-auth-algo": "SHA256withRSA",
                  "paypal-cert-url": "https://api.sandbox.paypal.com/v1/notifications/certs/CERT-0",
                  "paypal-transmission-id": str(uuid.uuid4()), "paypal-transmission-sig": "AAAA",
                  "paypal-transmission-time": "2026-11-12T00:00:00Z"}
        assert live.verify_webhook(forged, {"id": "WH-FAKE", "event_type": "PAYMENT.PAYOUTS-ITEM.SUCCEEDED"},
                                   hook["id"]) is False
    finally:
        live.delete_webhook(hook["id"])


def test_live_token_fetched_once(live):
    assert live.token_fetches == 1


SUCCESS_RECEIVER = os.environ.get("HOMING_LIVE_SUCCESS_RECEIVER", "")  # e.g. PHONE:4085550123 (a sandbox account)


@pytest.mark.skipif(not SUCCESS_RECEIVER, reason="set HOMING_LIVE_SUCCESS_RECEIVER to a registered sandbox receiver")
def test_live_claimed_invoices_once(live, tmp_path):
    rtype, receiver = SUCCESS_RECEIVER.split(":", 1)
    s = Settlement(live, tmp_path / "live.sqlite", holder_email=f"holder-{uuid.uuid4().hex[:6]}@example.com",
                   prefix="homing-live")
    cid = f"S{uuid.uuid4().hex[:10]}"
    s.create_claim(cid, receiver=receiver, receiver_type=rtype, amount="1.00", currency="USD", note="Test payout")
    s.send(cid)
    c, took = wait(lambda: s.refresh(cid), lambda x: x.state != SENT)
    print(f"\nitem {c.payout_item_id} -> {c.state} after {took:.0f}s, invoice {c.invoice_id} {c.invoice_status}")
    assert c.state == "CLAIMED" and c.invoice_status == "SENT" and c.fee == "0.10"
    first = c.invoice_id
    for _ in range(2):
        s.refresh(cid)
        s.ensure_invoice(cid)
    assert s.get(cid).invoice_id == first
    assert sum(a["action"] == "invoice_create" for a in s.audit(cid)) == 1
    live.cancel_invoice(first)
