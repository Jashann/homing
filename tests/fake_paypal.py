"""An in-memory PayPal for unit tests, plugged into httpx.MockTransport."""
from __future__ import annotations

import itertools
import json
import re
import uuid

import httpx

BASE = "https://api-m.sandbox.paypal.com"


class Crash(httpx.ReadTimeout):
    pass


class FakePayPal:
    def __init__(self, token_ttl: int = 32400):
        self.token_ttl = token_ttl
        self.tokens: set[str] = set()
        self.token_requests = 0
        self.batches: dict[str, dict] = {}
        self.items: dict[str, dict] = {}
        self.by_sender_batch: dict[str, str] = {}
        self.invoices: dict[str, dict] = {}
        self.webhooks: dict[str, dict] = {}
        self.payout_posts = 0
        self.payouts_created = 0
        self.invoices_created = 0
        self.invoice_sends = 0
        self.crash_after_create = 0      # lose the response of the next N payout creates
        self.crash_after_invoice_create = 0
        self.fail_cancel = None          # (status, name) to fail cancel with
        self.requests: list[tuple[str, str]] = []
        self._ids = itertools.count(1)
        self._inv_no = itertools.count(1)

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    # ---------------------------------------------------------- helpers for tests

    def _id(self, prefix: str) -> str:
        return f"{prefix}{next(self._ids):08d}"

    def set_item_status(self, item_id: str, status: str, error: str | None = None) -> None:
        it = self.items[item_id]
        it["transaction_status"] = status
        if error:
            it["errors"] = {"name": error, "message": error}
        batch = self.batches[it["payout_batch_id"]]
        batch["batch_header"]["batch_status"] = "SUCCESS" if status != "PENDING" else "PROCESSING"

    def only_item(self) -> dict:
        assert len(self.items) == 1, self.items
        return next(iter(self.items.values()))

    def item_event(self, item_id: str, event_type: str, event_id: str | None = None) -> dict:
        return {"id": event_id or f"WH-{uuid.uuid4().hex[:12]}", "event_type": event_type,
                "resource_type": "payouts_item", "resource": json.loads(json.dumps(self.items[item_id]))}

    def batch_event(self, batch_id: str, event_type: str, event_id: str | None = None) -> dict:
        b = self.batches[batch_id]
        return {"id": event_id or f"WH-{uuid.uuid4().hex[:12]}", "event_type": event_type,
                "resource_type": "payouts", "resource": {"batch_header": b["batch_header"]}}

    # ---------------------------------------------------------- HTTP

    def _err(self, status: int, name: str, message: str = "", details=None) -> httpx.Response:
        return httpx.Response(status, json={"name": name, "message": message or name,
                                            "debug_id": uuid.uuid4().hex[:13], "details": details or []})

    def handle(self, req: httpx.Request) -> httpx.Response:
        path, method = req.url.path, req.method
        self.requests.append((method, path))
        if path == "/v1/oauth2/token":
            self.token_requests += 1
            if not req.headers.get("authorization", "").startswith("Basic "):
                return httpx.Response(401, json={"error": "invalid_client", "error_description": "no auth"})
            tok = f"A21{uuid.uuid4().hex}"
            self.tokens.add(tok)
            return httpx.Response(200, json={"access_token": tok, "token_type": "Bearer", "expires_in": self.token_ttl})
        auth = req.headers.get("authorization", "")
        if not auth.startswith("Bearer ") or auth[7:] not in self.tokens:
            return httpx.Response(401, json={"error": "invalid_token", "error_description": "expired"})
        body = json.loads(req.content) if req.content else {}

        if path == "/v1/payments/payouts" and method == "POST":
            self.payout_posts += 1
            sbid = body["sender_batch_header"]["sender_batch_id"]
            if sbid in self.by_sender_batch:
                bid = self.by_sender_batch[sbid]
                return self._err(400, "USER_BUSINESS_ERROR", "User business error.", [{
                    "field": "SENDER_BATCH_ID", "location": "body",
                    "issue": "Batch with given sender_batch_id already exists",
                    "link": [{"href": f"https://api.sandbox.paypal.com/v1/payments/payouts/{bid}", "rel": "self",
                              "method": "GET"}]}])
            bid, iid = self._id("B"), self._id("I")
            header = {"payout_batch_id": bid, "batch_status": "PENDING",
                      "sender_batch_header": body["sender_batch_header"]}
            item_in = body["items"][0]
            self.items[iid] = {"payout_item_id": iid, "payout_batch_id": bid, "transaction_status": "PENDING",
                               "payout_item_fee": {"currency": "USD", "value": "0.25"},
                               "payout_item": {k: item_in[k] for k in item_in}}
            self.batches[bid] = {"batch_header": header, "item_ids": [iid]}
            self.by_sender_batch[sbid] = bid
            self.payouts_created += 1
            if self.crash_after_create:
                self.crash_after_create -= 1
                raise Crash("connection lost after PayPal accepted the batch")
            return httpx.Response(201, json={"batch_header": header})
        m = re.fullmatch(r"/v1/payments/payouts/(\w+)", path)
        if m and method == "GET":
            b = self.batches.get(m.group(1))
            if not b:
                return self._err(404, "INVALID_RESOURCE_ID")
            return httpx.Response(200, json={"batch_header": b["batch_header"],
                                             "items": [self.items[i] for i in b["item_ids"]]})
        m = re.fullmatch(r"/v1/payments/payouts-item/(\w+)(/cancel)?", path)
        if m:
            it = self.items.get(m.group(1))
            if not it:
                return self._err(404, "INVALID_RESOURCE_ID")
            if m.group(2):
                if self.fail_cancel:
                    return self._err(*self.fail_cancel)
                if it["transaction_status"] != "UNCLAIMED":
                    return self._err(400, "ITEM_ALREADY_CANCELLED" if it["transaction_status"] == "RETURNED"
                                     else "ITEM_CANNOT_BE_CANCELLED")
                it["transaction_status"] = "RETURNED"
            return httpx.Response(200, json=it)

        if path == "/v2/invoicing/generate-next-invoice-number":
            return httpx.Response(200, json={"invoice_number": f"{next(self._inv_no):04d}"})
        if path == "/v2/invoicing/invoices" and method == "POST":
            number = body["detail"].get("invoice_number") or f"{next(self._inv_no):04d}"
            if len(number) >= 25:  # what the sandbox does
                return self._err(400, "INVALID_REQUEST", details=[{"issue": "INVALID_STRING_MAX_LENGTH",
                                                                   "field": "/detail/invoice_number"}])
            if any(i["detail"]["invoice_number"] == number for i in self.invoices.values()):
                return self._err(422, "UNPROCESSABLE_ENTITY", details=[{"issue": "DUPLICATE_INVOICE_NUMBER",
                                                                       "field": "/detail/invoice_number"}])
            iid = f"INV2-{uuid.uuid4().hex[:16].upper()}"
            body["detail"]["invoice_number"] = number
            inv = {"id": iid, "status": "DRAFT", **body}
            inv["detail"]["metadata"] = {"recipient_view_url": f"https://www.sandbox.paypal.com/invoice/p/#{iid}"}
            self.invoices[iid] = inv
            self.invoices_created += 1
            if self.crash_after_invoice_create:
                self.crash_after_invoice_create -= 1
                raise Crash("connection lost after PayPal created the invoice")
            return httpx.Response(201, json=inv)
        if path == "/v2/invoicing/search-invoices":
            found = [i for i in self.invoices.values() if i["detail"]["invoice_number"] == body.get("invoice_number")]
            return httpx.Response(200, json={"items": found, "total_items": len(found)})
        m = re.fullmatch(r"/v2/invoicing/invoices/([\w-]+)(/send)?", path)
        if m:
            inv = self.invoices.get(m.group(1))
            if not inv:
                return self._err(404, "RESOURCE_NOT_FOUND")
            if method == "DELETE":
                if inv["status"] != "DRAFT":
                    return self._err(422, "UNPROCESSABLE_ENTITY")
                del self.invoices[m.group(1)]
                return httpx.Response(204)
            if m.group(2):
                if inv["status"] != "DRAFT":
                    return self._err(422, "UNPROCESSABLE_ENTITY", details=[{"issue": "INVOICE_ALREADY_SENT"}])
                inv["status"] = "SENT"
                self.invoice_sends += 1
                return httpx.Response(200, json={"links": [{"rel": "payer-view", "method": "GET",
                    "href": inv["detail"]["metadata"]["recipient_view_url"]}]})
            return httpx.Response(200, json=inv)

        if path == "/v1/notifications/webhooks":
            if method == "GET":
                return httpx.Response(200, json={"webhooks": list(self.webhooks.values())})
            if any(w["url"] == body["url"] for w in self.webhooks.values()):
                return self._err(400, "WEBHOOK_URL_ALREADY_EXISTS")
            wid = self._id("WH")
            self.webhooks[wid] = {"id": wid, **body}
            return httpx.Response(201, json=self.webhooks[wid])
        m = re.fullmatch(r"/v1/notifications/webhooks/(\w+)", path)
        if m and method == "DELETE":
            self.webhooks.pop(m.group(1), None)
            return httpx.Response(204)
        if path == "/v1/notifications/verify-webhook-signature":
            ok = body.get("transmission_sig") == "good" and body.get("webhook_id") in self.webhooks
            return httpx.Response(200, json={"verification_status": "SUCCESS" if ok else "FAILURE"})
        return self._err(404, "NOT_FOUND", f"{method} {path}")
