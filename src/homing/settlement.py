"""Restart-safe settlement of one owner payout per claim, stored in SQLite.

States:  CREATED -> SENT -> CLAIMED | UNCLAIMED | RETURNED | FAILED | CANCELLED
         UNCLAIMED -> CLAIMED | RETURNED | CANCELLED | FAILED

Rules
- The sender_batch_id is derived from the claim id and the attempt number, never random. A retry after a crash
  posts the same id, so PayPal either replays or refuses it ("Batch with given sender_batch_id already exists");
  the refusal names the existing batch, which is then adopted. A claim can never be paid twice.
- A new attempt (new contact) is allowed only once the previous payout is finished without paying
  (RETURNED, CANCELLED or FAILED). An UNCLAIMED payout must be cancelled first.
- A claim is closed as CLAIMED only when PayPal reports the item SUCCESS.
- UNCLAIMED and RETURNED put the claim in the "needs new contact" queue.
- Webhook events are de-duplicated by event id; the transition table ignores events that would move a claim
  backwards, so late or out-of-order deliveries cannot undo a later state.
- Every transition is appended to the audit table with the PayPal ids.
- Once CLAIMED, the business's fee invoice is created and sent exactly once (deterministic invoice number, so a
  crash between create and record is recovered by looking the number up).
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any

import httpx

from .paypal import (DuplicateBatchError, DuplicateInvoiceNumberError, LineItem, PayoutItem, PayPalClient,
                     PayPalError, money)

DEFAULT_DB = Path(__file__).resolve().parents[2] / "data" / "settlement.sqlite"

CREATED, SENT, CLAIMED, UNCLAIMED, RETURNED, FAILED, CANCELLED = (
    "CREATED", "SENT", "CLAIMED", "UNCLAIMED", "RETURNED", "FAILED", "CANCELLED")
TERMINAL = {CLAIMED, RETURNED, FAILED, CANCELLED}
NEEDS_CONTACT = {UNCLAIMED, RETURNED}
RETARGETABLE = {RETURNED, CANCELLED, FAILED}

ALLOWED = {
    CREATED: {SENT, UNCLAIMED, CLAIMED, RETURNED, FAILED, CANCELLED},
    SENT: {UNCLAIMED, CLAIMED, RETURNED, FAILED, CANCELLED},
    UNCLAIMED: {CLAIMED, RETURNED, CANCELLED, FAILED},
    CLAIMED: set(), RETURNED: set(), FAILED: set(), CANCELLED: set(),
}

# PayPal payout item transaction_status -> our state (None = still in flight)
ITEM_STATUS = {
    "SUCCESS": CLAIMED,
    "UNCLAIMED": UNCLAIMED,
    "RETURNED": RETURNED,
    "REFUNDED": RETURNED,
    "REVERSED": RETURNED,
    "CANCELED": CANCELLED,
    "CANCELLED": CANCELLED,
    "FAILED": FAILED,
    "DENIED": FAILED,
    "BLOCKED": FAILED,
    "PENDING": None,
    "ONHOLD": None,
    "HELD": None,
    "NEW": None,
}

EVENT_STATUS = {
    "PAYMENT.PAYOUTS-ITEM.SUCCEEDED": "SUCCESS",
    "PAYMENT.PAYOUTS-ITEM.UNCLAIMED": "UNCLAIMED",
    "PAYMENT.PAYOUTS-ITEM.RETURNED": "RETURNED",
    "PAYMENT.PAYOUTS-ITEM.FAILED": "FAILED",
    "PAYMENT.PAYOUTS-ITEM.DENIED": "DENIED",
    "PAYMENT.PAYOUTS-ITEM.BLOCKED": "BLOCKED",
    "PAYMENT.PAYOUTS-ITEM.REFUNDED": "REFUNDED",
    "PAYMENT.PAYOUTS-ITEM.HELD": "ONHOLD",
    "PAYMENT.PAYOUTS-ITEM.CANCELED": "CANCELED",
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS claims (
    claim_id TEXT PRIMARY KEY,
    receiver TEXT NOT NULL,
    receiver_type TEXT NOT NULL,
    amount TEXT NOT NULL,
    currency TEXT NOT NULL,
    note TEXT NOT NULL DEFAULT '',
    holder_email TEXT,
    state TEXT NOT NULL,
    attempt INTEGER NOT NULL DEFAULT 1,
    sender_batch_id TEXT NOT NULL,
    payout_batch_id TEXT,
    payout_item_id TEXT,
    paypal_status TEXT,
    paypal_error TEXT,
    cancel_requested INTEGER NOT NULL DEFAULT 0,
    needs_contact INTEGER NOT NULL DEFAULT 0,
    fee TEXT,
    invoice_number TEXT,
    invoice_id TEXT,
    invoice_status TEXT,
    invoice_url TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS attempts (
    claim_id TEXT NOT NULL,
    attempt INTEGER NOT NULL,
    receiver TEXT NOT NULL,
    sender_batch_id TEXT NOT NULL UNIQUE,
    payout_batch_id TEXT,
    payout_item_id TEXT,
    final_state TEXT,
    PRIMARY KEY (claim_id, attempt)
);
CREATE TABLE IF NOT EXISTS audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    at TEXT NOT NULL,
    claim_id TEXT NOT NULL,
    attempt INTEGER,
    action TEXT NOT NULL,
    from_state TEXT,
    to_state TEXT,
    paypal_status TEXT,
    sender_batch_id TEXT,
    payout_batch_id TEXT,
    payout_item_id TEXT,
    event_id TEXT,
    invoice_id TEXT,
    detail TEXT
);
CREATE TABLE IF NOT EXISTS webhook_events (
    event_id TEXT PRIMARY KEY,
    event_type TEXT,
    claim_id TEXT,
    outcome TEXT,
    received_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS audit_claim ON audit(claim_id, id);
CREATE INDEX IF NOT EXISTS attempts_item ON attempts(payout_item_id);
CREATE INDEX IF NOT EXISTS attempts_batch ON attempts(payout_batch_id);
"""


class SettlementError(RuntimeError):
    pass


@dataclass
class Claim:
    claim_id: str
    receiver: str
    receiver_type: str
    amount: str
    currency: str
    note: str
    holder_email: str | None
    state: str
    attempt: int
    sender_batch_id: str
    payout_batch_id: str | None
    payout_item_id: str | None
    paypal_status: str | None
    paypal_error: str | None
    cancel_requested: bool
    needs_contact: bool
    fee: str | None
    invoice_number: str | None
    invoice_id: str | None
    invoice_status: str | None
    invoice_url: str | None
    created_at: str
    updated_at: str

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "Claim":
        d = dict(row)
        d["cancel_requested"] = bool(d["cancel_requested"])
        d["needs_contact"] = bool(d["needs_contact"])
        return cls(**d)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def compute_fee(amount: Any, percent: Any = "10", cap: Any = "25") -> str:
    fee = Decimal(str(amount)) * Decimal(str(percent)) / Decimal(100)
    if cap is not None:
        fee = min(fee, Decimal(str(cap)))
    return str(fee.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


class Settlement:
    def __init__(self, client: PayPalClient, db_path: str | os.PathLike | None = None, *,
                 fee_percent: Any = "10", fee_cap: Any = "25", holder_email: str | None = None,
                 webhook_id: str | None = None, prefix: str = "homing", auto_invoice: bool = True,
                 holder_name: str = "the business", invoice_client: PayPalClient | None = None):
        self.client = client
        # The business's PayPal account sends the payouts. Homing bills its fee from its own PayPal account when one
        # is configured; otherwise the same account is used for both.
        self.invoice_client = invoice_client or client
        self.db_path = Path(db_path or os.environ.get("HOMING_SETTLEMENT_DB") or DEFAULT_DB)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.fee_percent = Decimal(str(fee_percent))
        self.fee_cap = None if fee_cap is None else Decimal(str(fee_cap))
        self.holder_email = holder_email
        self.holder_name = holder_name
        self.webhook_id = webhook_id
        self.prefix = prefix
        self.auto_invoice = auto_invoice
        self._local = threading.local()
        self._conn().executescript(SCHEMA)

    # ------------------------------------------------------------ storage

    def _conn(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self.db_path, isolation_level=None, timeout=30)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=FULL")
            self._local.conn = conn
        return conn

    @contextmanager
    def _tx(self):
        conn = self._conn()
        conn.execute("BEGIN IMMEDIATE")
        try:
            yield conn
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise

    def close(self) -> None:
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            conn.close()
            self._local.conn = None

    def _row(self, conn, claim_id: str) -> Claim:
        row = conn.execute("SELECT * FROM claims WHERE claim_id=?", (claim_id,)).fetchone()
        if row is None:
            raise KeyError(f"no claim {claim_id!r}")
        return Claim.from_row(row)

    def _audit(self, conn, claim: Claim, action: str, *, from_state=None, to_state=None, paypal_status=None,
               event_id=None, invoice_id=None, detail: Any = None, batch_id=None, item_id=None,
               sender_batch_id=None, attempt=None) -> None:
        conn.execute(
            "INSERT INTO audit(at, claim_id, attempt, action, from_state, to_state, paypal_status, sender_batch_id,"
            " payout_batch_id, payout_item_id, event_id, invoice_id, detail) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (_now(), claim.claim_id, attempt or claim.attempt, action, from_state, to_state, paypal_status,
             sender_batch_id or claim.sender_batch_id, batch_id or claim.payout_batch_id,
             item_id or claim.payout_item_id, event_id, invoice_id or claim.invoice_id,
             None if detail is None else (detail if isinstance(detail, str) else json.dumps(detail, default=str))))

    def sender_batch_id_for(self, claim_id: str, attempt: int = 1) -> str:
        return f"{self.prefix}-{claim_id}-a{attempt}"

    # ------------------------------------------------------------ reads

    def get(self, claim_id: str) -> Claim:
        return self._row(self._conn(), claim_id)

    def claims(self, state: str | None = None) -> list[Claim]:
        q, args = "SELECT * FROM claims", ()
        if state:
            q, args = q + " WHERE state=?", (state,)
        return [Claim.from_row(r) for r in self._conn().execute(q + " ORDER BY created_at", args)]

    def needs_contact(self) -> list[Claim]:
        return [Claim.from_row(r) for r in self._conn().execute(
            "SELECT * FROM claims WHERE needs_contact=1 ORDER BY updated_at")]

    def attempts(self, claim_id: str) -> list[dict]:
        return [dict(r) for r in self._conn().execute("SELECT * FROM attempts WHERE claim_id=? ORDER BY attempt",
                                                       (claim_id,))]

    def audit(self, claim_id: str) -> list[dict]:
        return [dict(r) for r in self._conn().execute("SELECT * FROM audit WHERE claim_id=? ORDER BY id", (claim_id,))]

    def sent_total_since(self, since: datetime) -> Decimal:
        """The sum of every payout sent to PayPal at or after `since` (one per send, so a retry to a new contact
        counts again). Amounts are added as they are, whatever their currency."""
        start = since.astimezone(timezone.utc).isoformat(timespec="milliseconds")
        rows = self._conn().execute(
            "SELECT c.amount FROM audit a JOIN claims c ON c.claim_id = a.claim_id "
            "WHERE a.action = 'send' AND a.at >= ?", (start,)).fetchall()
        return sum((Decimal(str(r[0])) for r in rows), Decimal("0"))

    # ------------------------------------------------------------ create / send

    def create_claim(self, claim_id: str, *, receiver: str, amount: Any, currency: str = "USD",
                     receiver_type: str = "EMAIL", note: str = "", holder_email: str | None = None) -> Claim:
        """Register a claim to pay. Calling again with the same id returns the existing claim unchanged."""
        receiver_type = receiver_type.upper()
        if receiver_type not in ("EMAIL", "PHONE"):
            raise ValueError("receiver_type must be EMAIL or PHONE")
        if Decimal(str(amount)) <= 0:
            raise ValueError("amount must be positive")
        with self._tx() as c:
            row = c.execute("SELECT * FROM claims WHERE claim_id=?", (claim_id,)).fetchone()
            if row is not None:
                existing = Claim.from_row(row)
                if existing.amount != money(amount) or existing.currency != currency.upper():
                    raise SettlementError(f"claim {claim_id} already exists with a different amount")
                return existing
            now = _now()
            sbid = self.sender_batch_id_for(claim_id, 1)
            c.execute(
                "INSERT INTO claims(claim_id, receiver, receiver_type, amount, currency, note, holder_email, state,"
                " attempt, sender_batch_id, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (claim_id, receiver, receiver_type, money(amount), currency.upper(), note,
                 holder_email or self.holder_email, CREATED, 1, sbid, now, now))
            c.execute("INSERT INTO attempts(claim_id, attempt, receiver, sender_batch_id) VALUES (?,?,?,?)",
                      (claim_id, 1, receiver, sbid))
            claim = self._row(c, claim_id)
            self._audit(c, claim, "create", to_state=CREATED, detail={"amount": claim.amount,
                                                                       "currency": claim.currency})
            return claim

    def send(self, claim_id: str) -> Claim:
        """Send the payout for the current attempt. Safe to call any number of times, including after a crash."""
        claim = self.get(claim_id)
        if claim.state != CREATED:
            return claim
        try:
            batch = self.client.create_payout(
                receiver=claim.receiver, receiver_type=claim.receiver_type, amount=claim.amount,
                currency=claim.currency, sender_batch_id=claim.sender_batch_id,
                sender_item_id=claim.sender_batch_id, note=claim.note,
                email_subject="You have money waiting", email_message=claim.note or None)
            batch_id, how = batch.batch_id, "created"
        except DuplicateBatchError as e:
            batch_id, how = e.existing_batch_id, "adopted_existing"
            if not batch_id:
                with self._tx() as c:
                    self._audit(c, self._row(c, claim_id), "send_error", paypal_status=e.name,
                                detail={"error": e.name, "debug_id": e.debug_id, "note": "duplicate batch, id unknown"})
                raise
            existing = self.client.get_payout_batch(batch_id)
            self._check_same_payout(claim, existing.items[0] if existing.items else None)
        except PayPalError as e:
            with self._tx() as c:
                self._audit(c, self._row(c, claim_id), "send_error", paypal_status=e.name,
                            detail={"status": e.status, "error": e.name, "message": e.message,
                                    "debug_id": e.debug_id})
            raise
        with self._tx() as c:
            claim = self._row(c, claim_id)
            if claim.state != CREATED:
                return claim
            c.execute("UPDATE claims SET state=?, payout_batch_id=?, updated_at=? WHERE claim_id=?",
                      (SENT, batch_id, _now(), claim_id))
            c.execute("UPDATE attempts SET payout_batch_id=? WHERE claim_id=? AND attempt=?",
                      (batch_id, claim_id, claim.attempt))
            self._audit(c, claim, "send", from_state=CREATED, to_state=SENT, batch_id=batch_id, detail=how)
        return self.get(claim_id)

    def _check_same_payout(self, claim: Claim, item: PayoutItem | None) -> None:
        if item is None:
            return
        same = (item.receiver or "").lower() == claim.receiver.lower() and \
            money(item.amount or 0) == claim.amount and (item.currency or "").upper() == claim.currency
        if not same:
            raise SettlementError(
                f"sender_batch_id {claim.sender_batch_id} already belongs to a different payout at PayPal; "
                "refusing to adopt it")

    # ------------------------------------------------------------ status

    def refresh(self, claim_id: str) -> Claim:
        """Poll PayPal for the current attempt and apply what it says."""
        claim = self.get(claim_id)
        if claim.state == CREATED or not claim.payout_batch_id:
            return claim
        if claim.state not in TERMINAL:
            if claim.payout_item_id:
                item = self.client.get_payout_item(claim.payout_item_id)
                batch_status = None
            else:
                batch = self.client.get_payout_batch(claim.payout_batch_id)
                batch_status = batch.status
                item = batch.items[0] if batch.items else None
            with self._tx() as c:
                if item is not None:
                    self._apply_item(c, claim_id, item.status, source="refresh", batch_id=item.batch_id,
                                     item_id=item.item_id, error=item.error_name)
                elif batch_status == "DENIED":
                    self._transition(c, claim_id, FAILED, "refresh", paypal_status="BATCH_DENIED")
        self._after(claim_id)
        return self.get(claim_id)

    def refresh_open(self) -> list[Claim]:
        out = []
        for claim in self.claims():
            if claim.state in (SENT, UNCLAIMED) or (claim.state == CLAIMED and claim.invoice_status != "SENT"
                                                    and claim.invoice_status != "PAID"):
                out.append(self.refresh(claim.claim_id))
        return out

    def _apply_item(self, c, claim_id: str, paypal_status: str, *, source: str, batch_id=None, item_id=None,
                    event_id=None, error=None) -> bool:
        claim = self._row(c, claim_id)
        status = (paypal_status or "").upper()
        if item_id and not claim.payout_item_id:
            c.execute("UPDATE claims SET payout_item_id=?, payout_batch_id=COALESCE(payout_batch_id, ?) "
                      "WHERE claim_id=?", (item_id, batch_id, claim_id))
            c.execute("UPDATE attempts SET payout_item_id=?, payout_batch_id=COALESCE(payout_batch_id, ?) "
                      "WHERE claim_id=? AND attempt=?", (item_id, batch_id, claim_id, claim.attempt))
            claim = self._row(c, claim_id)
        if status not in ITEM_STATUS:
            self._audit(c, claim, source, paypal_status=status, event_id=event_id, detail="unknown status, ignored")
            return False
        target = ITEM_STATUS[status]
        if target == RETURNED and claim.cancel_requested:
            target = CANCELLED
        c.execute("UPDATE claims SET paypal_status=?, paypal_error=COALESCE(?, paypal_error), updated_at=? "
                  "WHERE claim_id=?", (status, error, _now(), claim_id))
        if target is None:
            if claim.state == CREATED:
                return self._transition(c, claim_id, SENT, source, paypal_status=status, event_id=event_id)
            return False
        return self._transition(c, claim_id, target, source, paypal_status=status, event_id=event_id, error=error)

    def _transition(self, c, claim_id: str, target: str, source: str, *, paypal_status=None, event_id=None,
                    error=None) -> bool:
        claim = self._row(c, claim_id)
        if target == claim.state:
            return False
        if target not in ALLOWED[claim.state]:
            self._audit(c, claim, source, from_state=claim.state, to_state=claim.state, paypal_status=paypal_status,
                        event_id=event_id, detail=f"ignored: {claim.state} -> {target} not allowed")
            return False
        needs = 1 if target in NEEDS_CONTACT else 0
        c.execute("UPDATE claims SET state=?, needs_contact=?, updated_at=? WHERE claim_id=?",
                  (target, needs, _now(), claim_id))
        if target in TERMINAL:
            c.execute("UPDATE attempts SET final_state=? WHERE claim_id=? AND attempt=?",
                      (target, claim_id, claim.attempt))
        self._audit(c, claim, source, from_state=claim.state, to_state=target, paypal_status=paypal_status,
                    event_id=event_id, detail={"error": error} if error else None)
        return True

    def _after(self, claim_id: str) -> None:
        """Invoice once CLAIMED. An invoice failure never undoes or hides the payout; it is audited and retried."""
        if self.auto_invoice and self.get(claim_id).state == CLAIMED:
            try:
                self.ensure_invoice(claim_id)
            except (PayPalError, httpx.HTTPError) as e:
                detail = {"error": getattr(e, "name", type(e).__name__), "debug_id": getattr(e, "debug_id", None),
                          "issues": getattr(e, "issues", None)}
                with self._tx() as c:
                    self._audit(c, self._row(c, claim_id), "invoice_error", detail=detail)

    # ------------------------------------------------------------ webhooks

    def handle_webhook(self, headers: dict, event: dict) -> str:
        """Verify a delivery with PayPal, then apply it. Returns the outcome."""
        if not self.webhook_id:
            raise SettlementError("webhook_id is not set")
        if not self.client.verify_webhook(headers, event, self.webhook_id):
            return "rejected"
        return self.apply_webhook(event)

    def apply_webhook(self, event: dict) -> str:
        """Apply a verified PayPal event. Duplicate deliveries and out-of-order events are safe.

        Returns one of: applied, duplicate, ignored, unknown_claim, stale_attempt.
        """
        event_id = event.get("id") or ""
        etype = event.get("event_type") or ""
        resource = event.get("resource") or {}
        if not event_id:
            raise SettlementError("event has no id")
        with self._tx() as c:
            if c.execute("SELECT 1 FROM webhook_events WHERE event_id=?", (event_id,)).fetchone():
                return "duplicate"
            outcome, claim_id = self._apply_event(c, event_id, etype, resource)
            c.execute("INSERT INTO webhook_events(event_id, event_type, claim_id, outcome, received_at) "
                      "VALUES (?,?,?,?,?)", (event_id, etype, claim_id, outcome, _now()))
        if claim_id:
            if etype == "PAYMENT.PAYOUTSBATCH.SUCCESS" and self.get(claim_id).state in (SENT, CREATED):
                try:
                    self.refresh(claim_id)
                except PayPalError:
                    pass  # the poller will pick it up
            else:
                self._after(claim_id)
        return outcome

    def event_claim(self, event_id: str) -> tuple[str | None, str | None]:
        """(claim id, outcome) recorded for a webhook delivery, or (None, None)."""
        row = self._conn().execute("SELECT claim_id, outcome FROM webhook_events WHERE event_id=?",
                                   (event_id,)).fetchone()
        return (row["claim_id"], row["outcome"]) if row else (None, None)

    def _find_attempt(self, c, *, item_id=None, batch_id=None, sender_batch_id=None):
        for col, val in (("payout_item_id", item_id), ("sender_batch_id", sender_batch_id),
                         ("payout_batch_id", batch_id)):
            if val:
                row = c.execute(f"SELECT * FROM attempts WHERE {col}=?", (val,)).fetchone()
                if row:
                    return row
        return None

    def _apply_event(self, c, event_id: str, etype: str, resource: dict) -> tuple[str, str | None]:
        if etype.startswith("INVOICING.INVOICE."):
            inv_id = resource.get("id") or (resource.get("invoice") or {}).get("id")
            row = c.execute("SELECT claim_id FROM claims WHERE invoice_id=?", (inv_id,)).fetchone()
            if not row:
                return "unknown_claim", None
            claim = self._row(c, row["claim_id"])
            if etype == "INVOICING.INVOICE.PAID" and claim.invoice_status != "PAID":
                c.execute("UPDATE claims SET invoice_status='PAID', updated_at=? WHERE claim_id=?",
                          (_now(), claim.claim_id))
                self._audit(c, claim, "webhook", paypal_status="INVOICE_PAID", event_id=event_id, invoice_id=inv_id)
                return "applied", claim.claim_id
            return "ignored", claim.claim_id

        if etype.startswith("PAYMENT.PAYOUTSBATCH."):
            header = resource.get("batch_header") or {}
            batch_id = header.get("payout_batch_id")
            sbid = (header.get("sender_batch_header") or {}).get("sender_batch_id")
            att = self._find_attempt(c, batch_id=batch_id, sender_batch_id=sbid)
            if att is None:
                return "unknown_claim", None
            claim = self._row(c, att["claim_id"])
            if att["attempt"] != claim.attempt:
                self._audit(c, claim, "webhook", paypal_status=etype, event_id=event_id, batch_id=batch_id,
                            attempt=att["attempt"], detail="event for an earlier attempt, ignored")
                return "stale_attempt", claim.claim_id
            if batch_id and not claim.payout_batch_id:
                c.execute("UPDATE claims SET payout_batch_id=? WHERE claim_id=?", (batch_id, claim.claim_id))
            if etype == "PAYMENT.PAYOUTSBATCH.DENIED":
                changed = self._transition(c, claim.claim_id, FAILED, "webhook", paypal_status="BATCH_DENIED",
                                           event_id=event_id)
                return ("applied" if changed else "ignored"), claim.claim_id
            self._audit(c, claim, "webhook", paypal_status=header.get("batch_status") or etype, event_id=event_id,
                        batch_id=batch_id, detail="batch event noted")
            return "ignored", claim.claim_id

        if etype.startswith("PAYMENT.PAYOUTS-ITEM."):
            item_id = resource.get("payout_item_id")
            batch_id = resource.get("payout_batch_id")
            sbid = (resource.get("payout_item") or {}).get("sender_item_id")
            att = self._find_attempt(c, item_id=item_id, sender_batch_id=sbid, batch_id=batch_id)
            if att is None:
                return "unknown_claim", None
            claim = self._row(c, att["claim_id"])
            status = EVENT_STATUS.get(etype) or resource.get("transaction_status") or ""
            if att["attempt"] != claim.attempt:
                alert = " ALERT: an earlier attempt reports SUCCESS" if status == "SUCCESS" else ""
                self._audit(c, claim, "webhook", paypal_status=status, event_id=event_id, batch_id=batch_id,
                            item_id=item_id, attempt=att["attempt"], detail="event for an earlier attempt" + alert)
                return "stale_attempt", claim.claim_id
            if claim.state == CREATED and batch_id and not claim.payout_batch_id:
                # PayPal knows a batch we never recorded (crash after POST): adopt it
                c.execute("UPDATE claims SET payout_batch_id=? WHERE claim_id=?", (batch_id, claim.claim_id))
            error = (resource.get("errors") or {}).get("name")
            changed = self._apply_item(c, claim.claim_id, status, source="webhook", batch_id=batch_id,
                                       item_id=item_id, event_id=event_id, error=error)
            return ("applied" if changed else "ignored"), claim.claim_id

        return "ignored", None

    # ------------------------------------------------------------ cancel / new contact

    def cancel(self, claim_id: str) -> Claim:
        """Cancel an UNCLAIMED payout so the money comes back and a new contact can be tried."""
        claim = self.get(claim_id)
        if claim.state != UNCLAIMED:
            raise SettlementError(f"only an UNCLAIMED payout can be cancelled (claim is {claim.state})")
        if not claim.payout_item_id:
            claim = self.refresh(claim_id)
        with self._tx() as c:
            c.execute("UPDATE claims SET cancel_requested=1 WHERE claim_id=?", (claim_id,))
            self._audit(c, self._row(c, claim_id), "cancel_requested", from_state=UNCLAIMED)
        try:
            item = self.client.cancel_payout_item(claim.payout_item_id)
        except PayPalError as e:
            with self._tx() as c:
                self._audit(c, self._row(c, claim_id), "cancel_error", paypal_status=e.name,
                            detail={"status": e.status, "error": e.name, "debug_id": e.debug_id})
            raise
        with self._tx() as c:
            self._apply_item(c, claim_id, item.status, source="cancel", batch_id=item.batch_id, item_id=item.item_id)
        return self.get(claim_id)

    def retarget(self, claim_id: str, *, receiver: str, receiver_type: str = "EMAIL") -> Claim:
        """Start a new attempt to a new contact. Only allowed when the previous payout did not pay."""
        claim = self.get(claim_id)
        if claim.state == UNCLAIMED:
            claim = self.cancel(claim_id)
        if claim.state not in RETARGETABLE:
            raise SettlementError(f"cannot pay a new contact while the claim is {claim.state}")
        with self._tx() as c:
            claim = self._row(c, claim_id)
            if claim.state not in RETARGETABLE:
                raise SettlementError(f"cannot pay a new contact while the claim is {claim.state}")
            attempt = claim.attempt + 1
            sbid = self.sender_batch_id_for(claim_id, attempt)
            c.execute("INSERT INTO attempts(claim_id, attempt, receiver, sender_batch_id) VALUES (?,?,?,?)",
                      (claim_id, attempt, receiver, sbid))
            c.execute("UPDATE claims SET receiver=?, receiver_type=?, attempt=?, sender_batch_id=?, state=?,"
                      " payout_batch_id=NULL, payout_item_id=NULL, paypal_status=NULL, paypal_error=NULL,"
                      " cancel_requested=0, needs_contact=0, updated_at=? WHERE claim_id=?",
                      (receiver, receiver_type.upper(), attempt, sbid, CREATED, _now(), claim_id))
            self._audit(c, claim, "retarget", from_state=claim.state, to_state=CREATED, sender_batch_id=sbid,
                        attempt=attempt, batch_id="", item_id="", detail={"previous_attempt": claim.attempt})
        return self.get(claim_id)

    # ------------------------------------------------------------ business fee invoice

    def fee_for(self, claim: Claim) -> str:
        return compute_fee(claim.amount, self.fee_percent, self.fee_cap)

    def invoice_number_for(self, claim_id: str) -> str:
        # PayPal caps invoice numbers below 25 characters, so use a stable digest of the claim id
        return "HF-" + hashlib.sha256(f"{self.prefix}:{claim_id}".encode()).hexdigest()[:16].upper()

    def ensure_invoice(self, claim_id: str) -> Claim:
        """Create and send the business's fee invoice for a CLAIMED claim, exactly once."""
        claim = self.get(claim_id)
        if claim.state != CLAIMED or claim.invoice_status in ("SENT", "PAID", "MARKED_AS_PAID"):
            return claim
        if not claim.holder_email:
            with self._tx() as c:
                self._audit(c, self._row(c, claim_id), "invoice_skipped", detail="no business email")
            return claim
        fee = self.fee_for(claim)
        number = self.invoice_number_for(claim_id)
        if not claim.invoice_id:
            try:
                inv = self.invoice_client.create_invoice(
                    recipient_email=claim.holder_email, currency=claim.currency, invoice_number=number,
                    request_id=number,
                    items=[LineItem(f"Uncollected earnings paid to their owner, claim {claim_id}", fee,
                                    description=f"{self.fee_percent}% of {claim.amount} {claim.currency}"
                                                + (f", capped at {self.fee_cap}" if self.fee_cap is not None else ""))],
                    note=f"Homing's fee to {self.holder_name} for finding the owner of claim {claim_id}, checking "
                         f"their documents and paying them through PayPal Payouts (payout item "
                         f"{claim.payout_item_id}). The owner pays nothing.")
                how = "created"
            except DuplicateInvoiceNumberError:
                inv = self.invoice_client.find_invoice_by_number(number)
                if inv is None:
                    raise
                how = "adopted_existing"
            with self._tx() as c:
                cur = self._row(c, claim_id)
                if cur.invoice_id:
                    claim = cur
                else:
                    c.execute("UPDATE claims SET fee=?, invoice_number=?, invoice_id=?, invoice_status=?,"
                              " invoice_url=?, updated_at=? WHERE claim_id=?",
                              (fee, number, inv.invoice_id, inv.status, inv.payer_view_url, _now(), claim_id))
                    claim = self._row(c, claim_id)
                    self._audit(c, claim, "invoice_create", paypal_status=inv.status, invoice_id=inv.invoice_id,
                                detail={"fee": fee, "number": number, "how": how})
        if claim.invoice_status in ("SENT", "PAID", "MARKED_AS_PAID", "UNPAID"):
            return claim
        status, url = "SENT", claim.invoice_url
        try:
            url = self.invoice_client.send_invoice(claim.invoice_id, subject=f"Homing fee for claim {claim_id}") or url
        except PayPalError as e:
            if "INVOICE_ALREADY_SENT" not in e.issues:
                raise
        inv = self.invoice_client.get_invoice(claim.invoice_id)
        status = inv.status or status
        url = inv.payer_view_url or url
        with self._tx() as c:
            c.execute("UPDATE claims SET invoice_status=?, invoice_url=?, updated_at=? WHERE claim_id=?",
                      (status, url, _now(), claim_id))
            self._audit(c, self._row(c, claim_id), "invoice_send", paypal_status=status, invoice_id=claim.invoice_id)
        return self.get(claim_id)
