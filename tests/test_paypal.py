import httpx
import pytest

from homing import paypal
from homing.paypal import (AuthError, DuplicateBatchError, LineItem, NotFoundError, PayPalClient, PayPalError,
                           WEBHOOK_EVENTS, load_credentials)


def test_token_is_cached(client, fake):
    for _ in range(5):
        client.create_payout(receiver="a@example.com", amount=1, currency="USD", sender_batch_id=f"b{_}")
    assert fake.token_requests == 1


def test_token_refreshed_before_expiry(fake):
    now = [0.0]
    c = PayPalClient("id", "secret", transport=fake.transport(), clock=lambda: now[0])
    c.list_webhooks()
    now[0] = 32400 - 60  # inside PayPal's lifetime, but past our refresh margin
    c.list_webhooks()
    assert fake.token_requests == 2
    now[0] += 10
    c.list_webhooks()
    assert fake.token_requests == 2


def test_revoked_token_retried_once(client, fake):
    client.list_webhooks()
    fake.tokens.clear()
    client.list_webhooks()
    assert fake.token_requests == 2


def test_bad_credentials_raise_auth_error():
    def handler(req):
        return httpx.Response(401, json={"error": "invalid_client", "error_description": "Client Authentication failed"})
    c = PayPalClient("id", "secret", transport=httpx.MockTransport(handler))
    with pytest.raises(AuthError) as e:
        c.token()
    assert e.value.name == "invalid_client"
    assert "secret" not in str(e.value)


def test_credentials_env_override(tmp_path, monkeypatch):
    f = tmp_path / "creds"
    f.write_text("CLIENT_ID=file-id\nSECRET=file-secret\n")
    monkeypatch.delenv("PAYPAL_CLIENT_ID", raising=False)
    monkeypatch.delenv("PAYPAL_SECRET", raising=False)
    assert load_credentials(f) == ("file-id", "file-secret")
    monkeypatch.setenv("PAYPAL_CLIENT_ID", "env-id")
    monkeypatch.setenv("PAYPAL_SECRET", "env-secret")
    assert load_credentials(f) == ("env-id", "env-secret")
    monkeypatch.delenv("PAYPAL_CLIENT_ID")
    monkeypatch.delenv("PAYPAL_SECRET")
    with pytest.raises(AuthError):
        load_credentials(tmp_path / "missing")


def test_repr_hides_credentials(client):
    assert "test-secret" not in repr(client) and "test-id" not in repr(client)


def test_payout_lifecycle(client, fake):
    b = client.create_payout(receiver="+14155550100", receiver_type="PHONE", amount="1", currency="usd",
                             sender_batch_id="sb-1", note="hi")
    assert b.status == "PENDING" and b.batch_id
    item_id = fake.batches[b.batch_id]["item_ids"][0]
    sent = fake.items[item_id]["payout_item"]
    assert sent["recipient_type"] == "PHONE" and sent["amount"] == {"value": "1.00", "currency": "USD"}
    fake.set_item_status(item_id, "UNCLAIMED", "RECEIVER_UNREGISTERED")
    got = client.get_payout_batch(b.batch_id)
    assert got.status == "SUCCESS" and got.items[0].status == "UNCLAIMED"
    assert got.items[0].error_name == "RECEIVER_UNREGISTERED" and got.items[0].fee == "0.25"
    assert client.get_payout_item(item_id).sender_item_id == "sb-1"
    assert client.cancel_payout_item(item_id).status == "RETURNED"
    with pytest.raises(PayPalError) as e:
        client.cancel_payout_item(item_id)
    assert e.value.name == "ITEM_ALREADY_CANCELLED" and e.value.debug_id


def test_duplicate_batch_error_names_existing_batch(client):
    b = client.create_payout(receiver="a@example.com", amount=1, currency="USD", sender_batch_id="same")
    with pytest.raises(DuplicateBatchError) as e:
        client.create_payout(receiver="a@example.com", amount=1, currency="USD", sender_batch_id="same")
    assert e.value.existing_batch_id == b.batch_id
    assert e.value.name == "USER_BUSINESS_ERROR" and e.value.status == 400


def test_not_found_is_typed(client):
    with pytest.raises(NotFoundError):
        client.get_payout_item("NOPE")


def test_invoice_flow(client):
    assert client.next_invoice_number() == "0001"
    inv = client.create_invoice(recipient_email="holder@example.com", items=[LineItem("Fee", "2.5")],
                                invoice_number="X-1")
    assert inv.status == "DRAFT" and inv.number == "X-1" and inv.payer_view_url
    link = client.send_invoice(inv.invoice_id)
    assert link and inv.invoice_id in link
    assert client.get_invoice(inv.invoice_id).status == "SENT"
    assert client.find_invoice_by_number("X-1").invoice_id == inv.invoice_id
    with pytest.raises(paypal.DuplicateInvoiceNumberError):
        client.create_invoice(recipient_email="h@example.com", items=[LineItem("Fee", 1)], invoice_number="X-1")


def test_webhooks(client):
    w = client.create_webhook("https://example.com/hook")
    assert {e["name"] for e in w["event_types"]} == set(WEBHOOK_EVENTS)
    assert client.create_webhook("https://example.com/hook")["id"] == w["id"]
    assert [x["id"] for x in client.list_webhooks()] == [w["id"]]
    headers = {"PAYPAL-AUTH-ALGO": "SHA256withRSA", "PAYPAL-CERT-URL": "https://x", "PAYPAL-TRANSMISSION-ID": "t",
               "PAYPAL-TRANSMISSION-SIG": "good", "PAYPAL-TRANSMISSION-TIME": "2026-11-12T00:00:00Z"}
    assert client.verify_webhook(headers, {"id": "WH-1"}, w["id"]) is True
    assert client.verify_webhook({**headers, "PAYPAL-TRANSMISSION-SIG": "forged"}, {"id": "WH-1"}, w["id"]) is False
    assert client.verify_webhook({}, {"id": "WH-1"}, w["id"]) is False
    client.delete_webhook(w["id"])
    assert client.list_webhooks() == []


def test_webhook_event_list_is_complete():
    for name in ["SUCCEEDED", "UNCLAIMED", "RETURNED", "FAILED", "DENIED", "BLOCKED", "REFUNDED", "HELD", "CANCELED"]:
        assert f"PAYMENT.PAYOUTS-ITEM.{name}" in WEBHOOK_EVENTS
    for name in ["SUCCESS", "DENIED", "PROCESSING"]:
        assert f"PAYMENT.PAYOUTSBATCH.{name}" in WEBHOOK_EVENTS
    assert "INVOICING.INVOICE.PAID" in WEBHOOK_EVENTS
