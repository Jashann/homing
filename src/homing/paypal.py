"""PayPal REST client for the money layer: OAuth token, Payouts, Invoicing v2 and webhooks.

Plain httpx, no PayPal SDK. Credentials come from PAYPAL_CLIENT_ID / PAYPAL_SECRET if set, otherwise from
~/.paypal_sandbox (two lines, CLIENT_ID=... and SECRET=...). Credentials are never logged, printed or put in
error messages.
"""
from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any, Iterable

import httpx

SANDBOX_BASE_URL = "https://api-m.sandbox.paypal.com"
DEFAULT_CREDENTIALS_FILE = Path.home() / ".paypal_sandbox"

PAYOUT_ITEM_EVENTS = [
    "PAYMENT.PAYOUTS-ITEM.SUCCEEDED",
    "PAYMENT.PAYOUTS-ITEM.UNCLAIMED",
    "PAYMENT.PAYOUTS-ITEM.RETURNED",
    "PAYMENT.PAYOUTS-ITEM.FAILED",
    "PAYMENT.PAYOUTS-ITEM.DENIED",
    "PAYMENT.PAYOUTS-ITEM.BLOCKED",
    "PAYMENT.PAYOUTS-ITEM.REFUNDED",
    "PAYMENT.PAYOUTS-ITEM.HELD",
    "PAYMENT.PAYOUTS-ITEM.CANCELED",
]
PAYOUT_BATCH_EVENTS = [
    "PAYMENT.PAYOUTSBATCH.SUCCESS",
    "PAYMENT.PAYOUTSBATCH.DENIED",
    "PAYMENT.PAYOUTSBATCH.PROCESSING",
]
INVOICE_EVENTS = ["INVOICING.INVOICE.PAID"]
WEBHOOK_EVENTS = PAYOUT_ITEM_EVENTS + PAYOUT_BATCH_EVENTS + INVOICE_EVENTS

# Headers PayPal sends with every webhook delivery (lower-cased).
WEBHOOK_HEADERS = {
    "auth_algo": "paypal-auth-algo",
    "cert_url": "paypal-cert-url",
    "transmission_id": "paypal-transmission-id",
    "transmission_sig": "paypal-transmission-sig",
    "transmission_time": "paypal-transmission-time",
}


# ---------------------------------------------------------------- errors


class PayPalError(Exception):
    """An error response from PayPal, with PayPal's own name, message and debug_id."""

    def __init__(self, status: int, name: str, message: str, debug_id: str | None = None,
                 details: list | None = None, links: list | None = None, method: str = "", path: str = ""):
        self.status = status
        self.name = name
        self.message = message
        self.debug_id = debug_id
        self.details = details or []
        self.links = links or []
        self.method = method
        self.path = path
        issues = "; ".join(str(d.get("issue")) for d in self.details if isinstance(d, dict) and d.get("issue"))
        super().__init__(f"{method} {path} -> HTTP {status} {name}: {message}"
                         + (f" [{issues}]" if issues else "") + f" (debug_id={debug_id})")

    @property
    def issues(self) -> list[str]:
        return [str(d.get("issue", "")) for d in self.details if isinstance(d, dict)]


class AuthError(PayPalError):
    """The token request failed (bad credentials or app not allowed)."""


class NotFoundError(PayPalError):
    pass


class DuplicateBatchError(PayPalError):
    """A payout batch with this sender_batch_id already exists. `existing_batch_id` is set when PayPal says which."""

    @property
    def existing_batch_id(self) -> str | None:
        for d in self.details:
            for link in (d.get("link") or d.get("links") or []) if isinstance(d, dict) else []:
                href = link.get("href", "") if isinstance(link, dict) else ""
                if "/payouts/" in href:
                    return href.rstrip("/").rsplit("/", 1)[-1]
        for link in self.links:
            href = link.get("href", "") if isinstance(link, dict) else ""
            if "/payouts/" in href and "payouts-item" not in href:
                return href.rstrip("/").rsplit("/", 1)[-1]
        return None


class DuplicateInvoiceNumberError(PayPalError):
    pass


class WebhookUrlExistsError(PayPalError):
    pass


def _classify(status: int, body: Any, method: str, path: str) -> PayPalError:
    if not isinstance(body, dict):
        body = {}
    if "error" in body and "name" not in body:  # OAuth style error
        name = str(body.get("error"))
        message = str(body.get("error_description", ""))
        return AuthError(status, name, message, body.get("debug_id"), method=method, path=path)
    name = str(body.get("name") or f"HTTP_{status}")
    message = str(body.get("message") or "")
    details = body.get("details") or []
    links = body.get("links") or []
    debug_id = body.get("debug_id")
    text = " ".join([name, message] + [str(d.get("issue", "")) + " " + str(d.get("field", ""))
                                       for d in details if isinstance(d, dict)]).upper()
    cls = PayPalError
    if status == 401:
        cls = AuthError
    elif status == 404 or name in ("RESOURCE_NOT_FOUND", "INVALID_RESOURCE_ID"):
        cls = NotFoundError
    elif "SENDER_BATCH_ID" in text and ("ALREADY" in text or "EXIST" in text or "DUPLICATE" in text):
        cls = DuplicateBatchError
    elif name == "DUPLICATE_REQUEST_ID" and "/payouts" in path:
        cls = DuplicateBatchError
    elif "DUPLICATE_INVOICE_NUMBER" in text:
        cls = DuplicateInvoiceNumberError
    elif "WEBHOOK_URL_ALREADY_EXISTS" in text:
        cls = WebhookUrlExistsError
    return cls(status, name, message, debug_id, details, links, method, path)


# ---------------------------------------------------------------- credentials


def load_credentials(path: str | os.PathLike | None = None) -> tuple[str, str]:
    """Return (client_id, secret). Env vars win over the file."""
    cid = os.environ.get("PAYPAL_CLIENT_ID")
    sec = os.environ.get("PAYPAL_SECRET")
    if cid and sec:
        return cid.strip(), sec.strip()
    p = Path(path) if path else DEFAULT_CREDENTIALS_FILE
    values: dict[str, str] = {}
    if p.exists():
        for line in p.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            values[k.strip().upper()] = v.strip().strip('"').strip("'")
    cid = cid or values.get("CLIENT_ID") or values.get("PAYPAL_CLIENT_ID")
    sec = sec or values.get("SECRET") or values.get("PAYPAL_SECRET")
    if not cid or not sec:
        raise AuthError(0, "MISSING_CREDENTIALS",
                        f"set PAYPAL_CLIENT_ID and PAYPAL_SECRET or write CLIENT_ID= and SECRET= lines to {p}")
    return cid, sec


def credentials_available(path: str | os.PathLike | None = None) -> bool:
    try:
        load_credentials(path)
        return True
    except AuthError:
        return False


# ---------------------------------------------------------------- typed results


def money(value: Any) -> str:
    return str(Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


@dataclass
class PayoutItem:
    item_id: str
    batch_id: str | None
    status: str  # PayPal transaction_status: SUCCESS, UNCLAIMED, PENDING, RETURNED, FAILED, ...
    sender_item_id: str | None = None
    receiver: str | None = None
    amount: str | None = None
    currency: str | None = None
    fee: str | None = None
    fee_currency: str | None = None
    error_name: str | None = None
    error_message: str | None = None
    raw: dict = field(default_factory=dict, repr=False)

    @classmethod
    def from_api(cls, d: dict) -> "PayoutItem":
        item = d.get("payout_item") or {}
        amount = item.get("amount") or {}
        fee = d.get("payout_item_fee") or {}
        errors = d.get("errors") or {}
        return cls(
            item_id=d.get("payout_item_id", ""),
            batch_id=d.get("payout_batch_id"),
            status=str(d.get("transaction_status") or "").upper(),
            sender_item_id=item.get("sender_item_id"),
            receiver=item.get("receiver"),
            amount=amount.get("value"),
            currency=amount.get("currency"),
            fee=fee.get("value"),
            fee_currency=fee.get("currency"),
            error_name=errors.get("name"),
            error_message=errors.get("message"),
            raw=d,
        )


@dataclass
class PayoutBatch:
    batch_id: str
    status: str  # PENDING, PROCESSING, SUCCESS, DENIED, CANCELED
    sender_batch_id: str | None
    items: list[PayoutItem] = field(default_factory=list)
    raw: dict = field(default_factory=dict, repr=False)

    @classmethod
    def from_api(cls, d: dict) -> "PayoutBatch":
        h = d.get("batch_header") or {}
        return cls(
            batch_id=h.get("payout_batch_id", ""),
            status=str(h.get("batch_status") or "").upper(),
            sender_batch_id=(h.get("sender_batch_header") or {}).get("sender_batch_id"),
            items=[PayoutItem.from_api(i) for i in d.get("items") or []],
            raw=d,
        )


@dataclass
class Invoice:
    invoice_id: str
    status: str  # DRAFT, SENT, PAID, MARKED_AS_PAID, CANCELLED, ...
    number: str | None = None
    payer_view_url: str | None = None
    raw: dict = field(default_factory=dict, repr=False)

    @classmethod
    def from_api(cls, d: dict) -> "Invoice":
        detail = d.get("detail") or {}
        meta = detail.get("metadata") or {}
        url = meta.get("recipient_view_url")
        for link in d.get("links") or []:
            if link.get("rel") == "payer-view":
                url = url or link.get("href")
        return cls(invoice_id=d.get("id", ""), status=str(d.get("status") or "").upper(),
                   number=detail.get("invoice_number"), payer_view_url=url, raw=d)


@dataclass
class LineItem:
    name: str
    unit_amount: Any
    quantity: Any = 1
    description: str | None = None

    def to_api(self, currency: str) -> dict:
        d = {"name": self.name[:200], "quantity": str(self.quantity),
             "unit_amount": {"currency_code": currency, "value": money(self.unit_amount)}, "unit_of_measure": "QUANTITY"}
        if self.description:
            d["description"] = self.description[:1000]
        return d


# ---------------------------------------------------------------- client


class PayPalClient:
    """Thread-safe PayPal client with an in-memory token cache.

    Pass `transport` (an httpx transport, e.g. httpx.MockTransport) to run against a fake PayPal in tests.
    """

    TOKEN_MARGIN_S = 120  # refresh this long before PayPal's expiry

    def __init__(self, client_id: str | None = None, secret: str | None = None, *, base_url: str = SANDBOX_BASE_URL,
                 transport: httpx.BaseTransport | None = None, timeout: float = 30.0,
                 credentials_file: str | os.PathLike | None = None, clock=time.monotonic):
        if client_id is None or secret is None:
            client_id, secret = load_credentials(credentials_file)
        self._auth = (client_id, secret)
        self.base_url = base_url.rstrip("/")
        self._http = httpx.Client(base_url=self.base_url, transport=transport, timeout=timeout)
        self._token: str | None = None
        self._token_expires_at = 0.0
        self._lock = threading.Lock()
        self._clock = clock
        self.token_fetches = 0

    def __repr__(self) -> str:  # never show credentials
        return f"PayPalClient(base_url={self.base_url!r})"

    def close(self) -> None:
        self._http.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # ---- token

    def token(self, force: bool = False) -> str:
        with self._lock:
            if not force and self._token and self._clock() < self._token_expires_at:
                return self._token
            r = self._http.post("/v1/oauth2/token", data={"grant_type": "client_credentials"}, auth=self._auth,
                                headers={"Accept": "application/json"})
            body = _json(r)
            if r.status_code != 200 or "access_token" not in body:
                err = _classify(r.status_code, body, "POST", "/v1/oauth2/token")
                raise AuthError(err.status, err.name, err.message, err.debug_id, err.details, err.links,
                                "POST", "/v1/oauth2/token")
            self.token_fetches += 1
            self._token = body["access_token"]
            expires_in = float(body.get("expires_in", 3600))
            self._token_expires_at = self._clock() + max(expires_in - self.TOKEN_MARGIN_S, expires_in / 2)
            return self._token

    # ---- low-level request

    def request(self, method: str, path: str, *, json: Any = None, params: dict | None = None,
                headers: dict | None = None, ok: Iterable[int] = (200, 201, 202, 204)) -> dict:
        h = {"Accept": "application/json", "Content-Type": "application/json", **(headers or {})}
        for attempt in (0, 1):
            h["Authorization"] = f"Bearer {self.token(force=attempt == 1)}"
            r = self._http.request(method, path, json=json, params=params, headers=h)
            if r.status_code == 401 and attempt == 0:
                continue  # token revoked or expired early: fetch one new token and retry once
            break
        body = _json(r)
        if r.status_code not in ok:
            raise _classify(r.status_code, body, method, path)
        return body if isinstance(body, dict) else {"_body": body}

    # ---- payouts

    def create_payout(self, *, receiver: str, amount: Any, currency: str, sender_batch_id: str,
                      receiver_type: str = "EMAIL", note: str = "", sender_item_id: str | None = None,
                      email_subject: str | None = None, email_message: str | None = None,
                      request_id: str | None = None) -> PayoutBatch:
        """Create a one-item payout batch. `sender_batch_id` is the caller's idempotency key.

        PayPal-Request-Id defaults to the sender_batch_id, so a retried POST returns the original batch.
        """
        receiver_type = receiver_type.upper()
        if receiver_type not in ("EMAIL", "PHONE"):
            raise ValueError("receiver_type must be EMAIL or PHONE")
        header = {"sender_batch_id": sender_batch_id, "recipient_type": receiver_type}
        if email_subject:
            header["email_subject"] = email_subject[:255]
        if email_message:
            header["email_message"] = email_message[:1000]
        item = {"recipient_type": receiver_type, "receiver": receiver,
                "amount": {"value": money(amount), "currency": currency.upper()},
                "sender_item_id": sender_item_id or sender_batch_id}
        if note:
            item["note"] = note[:4000]
        body = {"sender_batch_header": header, "items": [item]}
        resp = self.request("POST", "/v1/payments/payouts", json=body,
                            headers={"PayPal-Request-Id": request_id or sender_batch_id})
        return PayoutBatch.from_api(resp)

    def get_payout_batch(self, batch_id: str) -> PayoutBatch:
        return PayoutBatch.from_api(self.request("GET", f"/v1/payments/payouts/{batch_id}",
                                                 params={"fields": "batch_header,items", "page_size": 100}))

    def get_payout_item(self, item_id: str) -> PayoutItem:
        return PayoutItem.from_api(self.request("GET", f"/v1/payments/payouts-item/{item_id}"))

    def cancel_payout_item(self, item_id: str) -> PayoutItem:
        """Cancel an UNCLAIMED item; the money returns to the sender."""
        return PayoutItem.from_api(self.request("POST", f"/v1/payments/payouts-item/{item_id}/cancel"))

    # ---- invoicing v2

    def next_invoice_number(self) -> str:
        return self.request("POST", "/v2/invoicing/generate-next-invoice-number")["invoice_number"]

    def create_invoice(self, *, recipient_email: str, items: list[LineItem], currency: str = "USD",
                       invoice_number: str | None = None, note: str | None = None, memo: str | None = None,
                       term: str | None = None, invoicer_email: str | None = None,
                       request_id: str | None = None) -> Invoice:
        detail: dict = {"currency_code": currency.upper()}
        if invoice_number:
            detail["invoice_number"] = invoice_number
        if note:
            detail["note"] = note[:4000]
        if memo:
            detail["memo"] = memo[:500]
        if term:
            detail["term"] = term[:4000]
        body: dict = {"detail": detail, "primary_recipients": [{"billing_info": {"email_address": recipient_email}}],
                      "items": [i.to_api(currency.upper()) for i in items]}
        if invoicer_email:
            body["invoicer"] = {"email_address": invoicer_email}
        headers = {"Prefer": "return=representation"}
        if request_id:
            headers["PayPal-Request-Id"] = request_id
        resp = self.request("POST", "/v2/invoicing/invoices", json=body, headers=headers)
        if "id" not in resp:  # minimal representation: follow the self link
            href = next((l["href"] for l in resp.get("links", []) if l.get("rel") == "self"), "")
            return self.get_invoice(href.rstrip("/").rsplit("/", 1)[-1])
        return Invoice.from_api(resp)

    def send_invoice(self, invoice_id: str, *, subject: str | None = None, note: str | None = None,
                     send_to_invoicer: bool = False, send_to_recipient: bool = True) -> str | None:
        """Send a draft invoice. Returns the payer-view link if PayPal gives one."""
        body: dict = {"send_to_invoicer": send_to_invoicer, "send_to_recipient": send_to_recipient}
        if subject:
            body["subject"] = subject[:4000]
        if note:
            body["note"] = note[:4000]
        resp = self.request("POST", f"/v2/invoicing/invoices/{invoice_id}/send", json=body)
        for link in resp.get("links", []):
            if link.get("rel") == "payer-view":
                return link.get("href")
        return resp.get("href")

    def get_invoice(self, invoice_id: str) -> Invoice:
        return Invoice.from_api(self.request("GET", f"/v2/invoicing/invoices/{invoice_id}"))

    def find_invoice_by_number(self, invoice_number: str) -> Invoice | None:
        resp = self.request("POST", "/v2/invoicing/search-invoices", params={"page_size": 10, "total_required": "true"},
                            json={"invoice_number": invoice_number})
        for d in resp.get("items", []):
            if (d.get("detail") or {}).get("invoice_number") == invoice_number:
                return self.get_invoice(d["id"])
        return None

    def cancel_invoice(self, invoice_id: str, note: str = "Cancelled") -> None:
        self.request("POST", f"/v2/invoicing/invoices/{invoice_id}/cancel",
                     json={"subject": "Invoice cancelled", "note": note, "send_to_invoicer": False,
                           "send_to_recipient": False})

    def delete_invoice(self, invoice_id: str) -> None:
        """Delete a DRAFT invoice (sent invoices must be cancelled instead)."""
        self.request("DELETE", f"/v2/invoicing/invoices/{invoice_id}")

    # ---- webhooks

    def list_webhooks(self) -> list[dict]:
        return self.request("GET", "/v1/notifications/webhooks").get("webhooks", [])

    def create_webhook(self, url: str, event_types: Iterable[str] = WEBHOOK_EVENTS) -> dict:
        """Create a webhook for `url`; if one already exists for that URL, return it instead."""
        body = {"url": url, "event_types": [{"name": e} for e in event_types]}
        try:
            return self.request("POST", "/v1/notifications/webhooks", json=body)
        except WebhookUrlExistsError:
            for w in self.list_webhooks():
                if w.get("url") == url:
                    return w
            raise

    def delete_webhook(self, webhook_id: str) -> None:
        self.request("DELETE", f"/v1/notifications/webhooks/{webhook_id}")

    def verify_webhook(self, headers: dict, event: dict, webhook_id: str) -> bool:
        """Ask PayPal whether a received event really came from PayPal for this webhook."""
        low = {k.lower(): v for k, v in headers.items()}
        missing = [h for h in WEBHOOK_HEADERS.values() if h not in low]
        if missing:
            return False
        body = {k: low[h] for k, h in WEBHOOK_HEADERS.items()}
        body.update({"webhook_id": webhook_id, "webhook_event": event})
        resp = self.request("POST", "/v1/notifications/verify-webhook-signature", json=body)
        return resp.get("verification_status") == "SUCCESS"


def _json(r: httpx.Response) -> Any:
    if not r.content:
        return {}
    try:
        return r.json()
    except ValueError:
        return {"name": f"HTTP_{r.status_code}", "message": r.text[:300]}
