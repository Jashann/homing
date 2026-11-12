"""Homing's web server: every endpoint in docs/API.md, on port 8787.

    uv run python -m homing.server

The business's sample ledger (Larkfield Market, an invented marketplace), the uncollected payouts imported from its
PayPal history, and Homing's claims live in data/homing.sqlite (store.py). Payouts and the business's fee invoice go
through the restart-safe settlement state machine
(settlement.py) on the PayPal sandbox. Documents are read by a model (documents.read_document, with saved readings
for the sample documents when no model is available, assist.py); the verdict is plain code (verify.assess). After
code has decided, the model writes a plain explanation in the claimant's language. web/dist is served when built.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import runpy
import secrets
import statistics
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Callable

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import agent, assist, documents, llm, mask, stateclaim, verify
from .store import Store, NotFound

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"
TOTALS = DATA / "paypal_totals.json"
LEDGER = DATA / "sample_ledger.json"
PAYOUT_HISTORY = DATA / "payout_history.json"
SAVED_TRACES = DATA / "saved_agent_traces.json"
FILES = DATA / "files"
WEB_DIST = ROOT / "web" / "dist"

BALANCE_STATES = {"DORMANT", "MATCHED", "PROVEN", "PAYING", "PAID", "UNCLAIMED", "RETURNED", "ESCHEATED"}
# settlement state -> (claim status, balance status, payout state shown)
PAYOUT_MAP = {
    "CREATED": ("APPROVED", "PROVEN", "NONE"),
    "SENT": ("PAYING", "PAYING", "SENT"),
    "CLAIMED": ("PAID", "PAID", "CLAIMED"),
    "UNCLAIMED": ("NEEDS_NEW_CONTACT", "UNCLAIMED", "UNCLAIMED"),
    "RETURNED": ("NEEDS_NEW_CONTACT", "RETURNED", "RETURNED"),
    "FAILED": ("NEEDS_NEW_CONTACT", "RETURNED", "FAILED"),
    "CANCELLED": ("NEEDS_NEW_CONTACT", "RETURNED", "CANCELLED"),
}
DEFAULT_HOLDER = {"name": "Larkfield Market", "kind": "an online marketplace that pays its sellers through PayPal",
                  "sample": True}
HELD_TEXT = ("Held by PayPal. The first payment has not been collected, so it will not be sent twice. PayPal returns "
             "uncollected payments to the sender after 30 days.")
REJECTED_TEXT = "We could not confirm this claim. {business} has been told."
# A model remark that only says a document is a sample. In demo mode the banner already says it once.
SAMPLE_REMARK = re.compile(r"demo|demonstration|specimen|placeholder|silhouette|not (?:a )?genuine|not (?:a )?real\b|"
                           r"not valid|sample|invented person", re.I)
SAMPLE_KIND = re.compile(r"\s*\([^()]*(?:demo|sample|specimen)[^()]*\)", re.I)
# Where the model's notice puts the link to Homing; older saved notices wrote a bracketed placeholder instead.
LINK_TOKEN = re.compile(r"\{\{HOMING_LINK\}\}|\[[^\[\]\n]{0,60}Homing[^\[\]\n]{0,60}\]")
PAYPAL_SOURCE = "PayPal Payouts"
RETURN_DAYS = 30   # PayPal returns an unclaimed payout to the sender after 30 days

# ---- public demo mode (HOMING_PUBLIC=1): a copy on the open internet cannot drain the sandbox test funds
PUBLIC_RECEIVER_ENDINGS = ("@personal.example.com", "@example.com")
PUBLIC_RECEIVER_TEXT = "This public demo only pays PayPal sandbox test accounts."
PUBLIC_CAP_TEXT = ("This public demo has reached today's test-money limit. Everything else still works, and the "
                   "video shows a paid run.")
PUBLIC_RESET_SECONDS = 60
PUBLIC_RESET_TEXT = "The demo was reset less than a minute ago. Please wait a moment and try again."
PUBLIC_MAX_FILE_BYTES = 5 * 1024 * 1024
PUBLIC_MAX_FILES = 5
PUBLIC_SAVED_RUN_NOTE = ("No model is connected to this public demo, so this is the saved run from a real session.")


def public_mode() -> bool:
    return os.environ.get("HOMING_PUBLIC", "").strip() == "1"


def public_daily_cap() -> Decimal:
    try:
        return Decimal(os.environ.get("HOMING_PUBLIC_DAILY_CAP", "300").strip() or "300")
    except ArithmeticError:
        return Decimal("300")


def public_receiver_allowed(receiver: str) -> bool:
    r = (receiver or "").strip().lower()
    return r.count("@") == 1 and not r.startswith("@") and r.endswith(PUBLIC_RECEIVER_ENDINGS)


def titles(business: str) -> dict:
    """Timeline entry titles (docs/API.md)."""
    return {"PREPARED": "Prepared", "SENT": "Sent through PayPal", "CLAIMED": "Collected",
            "UNCLAIMED": "Not collected yet", "HELD": "Held by PayPal", "INVOICE_CREATED": "Fee invoice created",
            "INVOICE_SENT": f"Fee invoice sent to {business}", "RETURNED": f"Returned to {business}",
            "FAILED": "Not delivered", "CANCELLED": "Cancelled", "REFUSED": "Not accepted by PayPal",
            "CANCEL_ASKED": "Cancel requested", "RETARGET": "Trying a new contact",
            "INVOICE_LATER": "Fee invoice delayed", "INVOICE_PAID": "Fee invoice paid"}


_SYMBOL = {"USD": "$", "EUR": "€", "GBP": "£"}
_STEPS = [10, 25, 50, 100, 250, 500, 1000, 2500, 5000]


def amount_band(amount: str, currency: str) -> str:
    """'86.40' -> 'between $50 and $100': search results never show the exact amount."""
    sym = _SYMBOL.get(currency)
    fmt = (lambda n: f"{sym}{n:,}") if sym else (lambda n: f"{n:,} {currency}")
    v = Decimal(amount)
    if v < _STEPS[0]:
        return f"under {fmt(_STEPS[0])}"
    for lo, hi in zip(_STEPS, _STEPS[1:]):
        if v < hi:
            return f"between {fmt(lo)} and {fmt(hi)}"
    return f"more than {fmt(_STEPS[-1])}"


def paypal_state_words(p: dict | None) -> str | None:
    """The state of an imported PayPal payout in plain words."""
    if not p:
        return None
    if p.get("status") == "UNCLAIMED":
        since, back = p.get("since"), p.get("returns_on")
        day = lambda iso: f"{date.fromisoformat(iso).day} {date.fromisoformat(iso):%B}"  # noqa: E731
        return (f"Uncollected in PayPal since {day(since)}; PayPal returns it on {day(back)}" if since and back
                else "Uncollected in PayPal")
    if p.get("status") == "RETURNED":
        return "Returned by PayPal, ready to pay again"
    return f"PayPal reports {str(p.get('status') or 'unknown').lower()}"


class ApiError(Exception):
    def __init__(self, status: int, message: str):
        self.status, self.message = status, message


# ---- request bodies -----------------------------------------------------------------------------------------

def _clean(v):
    return " ".join(v.split()) if isinstance(v, str) else v


class PlaceIn(BaseModel):
    city: str = Field(default="", max_length=120)
    country: str = Field(default="", max_length=60)


class SearchIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    former_names: list[str] = Field(default_factory=list, max_length=10)
    emails: list[str] = Field(default_factory=list, max_length=10)
    places: list[PlaceIn] = Field(default_factory=list, max_length=20)

    @field_validator("name")
    @classmethod
    def _trim(cls, v):
        return _clean(v)


class ClaimantIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    email: str = Field(default="", max_length=254)
    country: str = Field(default="", max_length=60)
    language: str = Field(default="en", max_length=20)
    former_names: list[str] = Field(default_factory=list, max_length=10)
    address: str = Field(default="", max_length=300)


class ClaimIn(BaseModel):
    balance_id: str = Field(..., max_length=40)
    claimant: ClaimantIn


class ApproveIn(BaseModel):
    approver: str = Field(..., min_length=1, max_length=120)
    reason: str = Field(default="", max_length=2000)

    @field_validator("approver", "reason")
    @classmethod
    def _trim(cls, v):
        return (v or "").strip()


class PayIn(BaseModel):
    receiver: str = Field(..., min_length=3, max_length=254)
    receiver_type: str = Field(default="EMAIL")
    currency: str | None = Field(default=None, max_length=3)

    @field_validator("receiver_type")
    @classmethod
    def _kind(cls, v):
        v = (v or "EMAIL").upper()
        if v not in ("EMAIL", "PHONE"):
            raise ValueError("receiver_type must be EMAIL or PHONE")
        return v

    @field_validator("receiver")
    @classmethod
    def _receiver(cls, v):
        return v.strip()


class ContactIn(BaseModel):
    receiver: str = Field(..., min_length=3, max_length=254)
    receiver_type: str = Field(default="EMAIL")

    @field_validator("receiver_type")
    @classmethod
    def _kind(cls, v):
        v = (v or "EMAIL").upper()
        if v not in ("EMAIL", "PHONE"):
            raise ValueError("receiver_type must be EMAIL or PHONE")
        return v


class AgentRunIn(BaseModel):
    mode: str = Field(default="queue", pattern="^(queue|brakes)$")
    dry_pay: bool = False


class AddressIn(BaseModel):
    receiver: str = Field(..., min_length=3, max_length=254)


class StateClaimIn(BaseModel):
    claimant: ClaimantIn | None = None


# ---- the money layer ----------------------------------------------------------------------------------------

def default_settlement(holder_name: str = DEFAULT_HOLDER["name"]):
    """The real settlement on the PayPal sandbox, or None when no sandbox credentials are on this machine."""
    from .paypal import PayPalClient, credentials_available
    from .settlement import Settlement
    if not credentials_available():
        return None
    # Optional second sandbox app: Homing's own PayPal account, which issues the fee invoices to the business.
    invoicer = None
    inv_file = Path(os.environ.get("HOMING_INVOICER_CREDENTIALS") or Path.home() / ".paypal_sandbox_homing")
    if os.environ.get("PAYPAL_INVOICER_CLIENT_ID") and os.environ.get("PAYPAL_INVOICER_SECRET"):
        invoicer = PayPalClient(os.environ["PAYPAL_INVOICER_CLIENT_ID"], os.environ["PAYPAL_INVOICER_SECRET"])
    elif inv_file.exists():
        invoicer = PayPalClient(credentials_file=inv_file)
    return Settlement(PayPalClient(), os.environ.get("HOMING_SETTLEMENT_DB") or DATA / "settlement.sqlite",
                      invoice_client=invoicer,
                      holder_email=os.environ.get("HOMING_HOLDER_EMAIL", "payouts@larkfield-market.example.com"),
                      webhook_id=os.environ.get("PAYPAL_WEBHOOK_ID") or None, prefix="homing",
                      holder_name=holder_name)


# ---- the app ------------------------------------------------------------------------------------------------

def create_app(*, store: Store | None = None, settlement=None, use_default_settlement: bool = True,
               reader: Callable[..., dict] | None = None, totals_path: Path | None = None,
               ledger_path: Path | None = None, files_dir: Path | None = None, demo: bool | None = None,
               saved: assist.Saved | None = None, ask: Callable[[str], dict] | None = None,
               webhook_grace: float | None = None, payout_history_path: Path | None = None,
               agent_ask: Callable[[str], str] | None = None, saved_traces_path: Path | None = None,
               public: bool | None = None, public_cap: Decimal | str | None = None,
               clock: Callable[[], float] | None = None) -> FastAPI:
    """`reader(path, sha256)` reads one document (default: the model, then the saved readings). `ask(prompt)` is
    the model call for explanations and notices (default: llm.ask_json with "sonnet"). `demo` defaults to
    HOMING_DEMO=1. `webhook_grace`: with webhooks on, how many seconds after a payout is sent the refresh endpoint
    waits for PayPal's webhook before it asks PayPal itself (default HOMING_WEBHOOK_GRACE or 60 for the real
    PayPal settlement, 0 for an injected one). `payout_history_path`: the batch ids POST /api/import/paypal asks
    PayPal about (default data/payout_history.json). `public` (default HOMING_PUBLIC=1) turns on the public demo
    guard: payouts only to sandbox test addresses and at most `public_cap` (default HOMING_PUBLIC_DAILY_CAP or 300)
    sent per UTC day, one reset a minute, smaller uploads, and the saved desk-agent run when no model is connected.
    `clock` is the monotonic clock the reset limit uses (default time.monotonic)."""
    store = store or Store()
    ledger_path = Path(ledger_path or LEDGER)
    totals_path = Path(totals_path or TOTALS)
    files_dir = Path(files_dir or FILES)
    history_path = Path(payout_history_path or PAYOUT_HISTORY)
    demo = verify.demo_mode() if demo is None else demo
    public = public_mode() if public is None else public
    public_cap = public_daily_cap() if public_cap is None else Decimal(str(public_cap))
    clock = clock or time.monotonic
    saved = saved or assist.Saved()
    read = reader or assist.make_reader(saved)

    def load_ledger() -> int:
        if not ledger_path.exists():
            runpy.run_path(str(ROOT / "scripts" / "make_ledger.py"), run_name="__main__")
        return store.load_ledger(ledger_path)

    def holder_info() -> dict:
        try:
            h = json.loads(ledger_path.read_text()).get("holder")
        except (OSError, ValueError):
            h = None
        return {**DEFAULT_HOLDER, **h} if isinstance(h, dict) else dict(DEFAULT_HOLDER)

    holder = holder_info()
    money = settlement if settlement is not None else (default_settlement(holder["name"]) if use_default_settlement
                                                       else None)
    if webhook_grace is None:
        webhook_grace = float(os.environ.get("HOMING_WEBHOOK_GRACE", "60")) if settlement is None else 0.0
    payout_note = f"Your uncollected seller earnings from {holder['name']}, returned to you through Homing."
    TITLES = titles(holder["name"])

    if not store.balances():
        load_ledger()

    app = FastAPI(title="Homing", version="0.1.0")
    app.state.store, app.state.settlement = store, money

    @app.exception_handler(ApiError)
    async def _api_error(request: Request, exc: ApiError):
        return JSONResponse({"error": exc.message}, status_code=exc.status)

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(request: Request, exc: StarletteHTTPException):
        msg = exc.detail if isinstance(exc.detail, str) else "The request could not be handled."
        if exc.status_code == 404 and msg == "Not Found":
            msg = "There is nothing at this address."
        return JSONResponse({"error": msg}, status_code=exc.status_code)

    @app.exception_handler(RequestValidationError)
    async def _invalid(request: Request, exc: RequestValidationError):
        parts = []
        for e in exc.errors()[:3]:
            where = ".".join(str(x) for x in e.get("loc", []) if x != "body")
            parts.append(f"{where}: {e.get('msg')}" if where else str(e.get("msg")))
        return JSONResponse({"error": "The request is not valid. " + "; ".join(parts) + "."}, status_code=422)

    # ---- helpers

    def balance_or_404(balance_id: str) -> dict:
        try:
            return store.balance(balance_id)
        except NotFound:
            raise ApiError(404, f"There is no balance {balance_id}.")

    def claim_or_404(claim_id: str) -> dict:
        try:
            return store.claim(claim_id)
        except NotFound:
            raise ApiError(404, f"There is no claim {claim_id}.")

    def need_money():
        if money is None:
            raise ApiError(503, "PayPal is not set up on this server yet: add sandbox keys to ~/.paypal_sandbox "
                                "and restart it.")
        return money

    def payout_of(claim_id: str):
        if money is None:
            return None
        try:
            return money.get(claim_id)
        except KeyError:
            return None

    pay_lock = threading.Lock()

    def guard_payout(receiver: str, amount) -> None:
        """The one gate every payout passes in public mode, just before money would leave: the receiver must be a
        PayPal sandbox or made-up address, and today's payouts (UTC, from the settlement database) plus this one
        must stay within the daily cap. Does nothing outside public mode."""
        if not public:
            return
        if not public_receiver_allowed(receiver):
            raise ApiError(403, PUBLIC_RECEIVER_TEXT)
        midnight = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        if money.sent_total_since(midnight) + Decimal(str(amount)) > public_cap:
            raise ApiError(429, PUBLIC_CAP_TEXT)

    app.state.guard_payout = guard_payout

    def proven(c: dict) -> bool:
        p = c.get("proof") or {}
        return c["status"] != "REJECTED" and (p.get("verdict") == verify.APPROVED or bool(c.get("approved_by")))

    def sync(claim_id: str) -> dict:
        """Brings the claim's and the balance's status in line with the settlement state."""
        c = store.claim(claim_id)
        s = payout_of(claim_id)
        if s is None or c["status"] == "REJECTED":
            return c
        status, bstatus, _ = PAYOUT_MAP.get(s.state, (c["status"], None, None))
        fields = {}
        if status != c["status"]:
            fields["status"] = status
        if s.state == "CLAIMED" and not c.get("paid_at"):
            fields["paid_at"] = datetime.fromisoformat(s.updated_at).isoformat(timespec="seconds")
        if fields:
            c = store.update_claim(claim_id, "payout_status", detail={"payout": s.state}, **fields)
        if bstatus:
            store.set_balance_status(c["balance_id"], bstatus, why=f"claim {claim_id} payout {s.state}")
        return c

    def timeline(claim_id: str, amount: str, currency: str) -> list[dict]:
        """What PayPal and Homing did with this claim's money, in plain sentences, oldest first. Each entry has
        a title (docs/API.md), a note, and how PayPal told us: "WEBHOOK" (a verified delivery) or "POLLING"."""
        if money is None:
            return []
        receivers = {a["attempt"]: mask.receiver(a["receiver"], "EMAIL" if "@" in a["receiver"] else "PHONE")
                     for a in money.attempts(claim_id)}
        sum_text = f"${Decimal(amount):,.2f}" if currency == "USD" else f"{Decimal(amount):,.2f} {currency}"
        out = []
        for row in money.audit(claim_id):
            action, to = row["action"], row["to_state"]
            who = receivers.get(row.get("attempt")) or "the owner"
            detail = row.get("detail") or ""
            try:
                detail = json.loads(detail) if detail.startswith(("{", "[")) else detail
            except ValueError:
                pass
            heard = None
            if action in ("refresh", "webhook", "cancel") and to and to != row["from_state"]:
                heard = "WEBHOOK" if action == "webhook" else "POLLING" if action == "refresh" else None
                note = {
                    "SENT": f"PayPal is processing the payment to {who}.",
                    "CLAIMED": f"PayPal confirms the money reached {who}'s PayPal account.",
                    "UNCLAIMED": f"{who} has not collected the payment yet. They may not have a PayPal account, so "
                                 "PayPal is holding the money and has invited them to collect it.",
                    "RETURNED": f"PayPal sent the money back to {holder['name']}. Nothing was paid out.",
                    "FAILED": "PayPal could not deliver this payment. Nothing was paid out.",
                    "CANCELLED": f"The uncollected payment was cancelled and the money went back to {holder['name']}.",
                }.get(to, f"PayPal reports {to.lower()}.")
                note += " Confirmed by PayPal webhook." if heard == "WEBHOOK" else ""
                state, pid = PAYOUT_MAP.get(to, (None, None, to))[2], row.get("payout_item_id")
                key = state
            elif action == "webhook" and row.get("paypal_status") == "INVOICE_PAID":
                state, pid, key, heard = "INVOICE", row.get("invoice_id"), "INVOICE_PAID", "WEBHOOK"
                note = f"{holder['name']} paid the fee invoice. Confirmed by PayPal webhook."
            elif action == "create":
                state, pid, key = "PREPARED", None, "PREPARED"
                note = f"Payment of {sum_text} prepared for {who}."
            elif action == "send":
                state, pid, key = "SENT", row.get("payout_batch_id"), "SENT"
                note = f"Sent through PayPal Payouts to {who}. PayPal accepted it."
            elif action == "send_error":
                state, pid, key = "REFUSED", None, "REFUSED"
                note = "PayPal did not accept the payment, so no money left."
            elif action == "cancel_requested":
                state, pid, key = "CANCEL_ASKED", row.get("payout_item_id"), "CANCEL_ASKED"
                note = "Asked PayPal to cancel the uncollected payment before trying a new contact."
            elif action == "cancel_error":
                state, pid, key = "HELD", row.get("payout_item_id"), "HELD"
                note = HELD_TEXT
            elif action == "retarget":
                state, pid, key = "PREPARED", None, "RETARGET"
                note = "The first payment was cancelled. Trying the new contact."
            elif action == "invoice_create":
                state, pid, key = "INVOICE", row.get("invoice_id"), "INVOICE_CREATED"
                fee = detail.get("fee") if isinstance(detail, dict) else None
                fee_text = (f"${Decimal(fee):,.2f}" if currency == "USD" else f"{fee} {currency}") if fee else ""
                own = money.invoice_client is not money.client   # Homing's own PayPal account issues it
                note = ((f"Homing's PayPal account made out an invoice to {holder['name']}" if own
                         else f"PayPal invoice made out to {holder['name']}")
                        + (f" for the {fee_text} fee." if fee else " for the fee."))
            elif action == "invoice_send":
                state, pid, key = "INVOICE", row.get("invoice_id"), "INVOICE_SENT"
                note = (f"Fee invoice sent to {holder['name']} through PayPal"
                        + (" from Homing's account" if money.invoice_client is not money.client else "")
                        + ". The owner pays nothing.")
            elif action == "invoice_error":
                state, pid, key = "INVOICE_LATER", None, "INVOICE_LATER"
                note = f"The fee invoice to {holder['name']} could not be made yet. It will be tried again."
            else:
                continue   # status checks that changed nothing, batch notes and ignored events
            out.append({"at": row["at"], "state": state, "title": TITLES.get(key, key.title()), "note": note,
                        "paypal_id": pid or None, "heard_by": heard})
        return out

    def concerns_of(r: dict) -> list[str]:
        """The model's remarks on a document. In demo mode a remark that only says the document is a sample is
        dropped: the banner already says so."""
        out = [str(x) for x in r.get("concerns") or []]
        return [x for x in out if not SAMPLE_REMARK.search(x)] if demo else out

    def documents_view(c: dict) -> list[dict]:
        """What the model read on each document, its concerns and signs of editing, with a thumbnail."""
        out = []
        for d in store.documents(c["id"]):
            r = d.get("reading") or {}
            thumb = files_dir / "claims" / c["id"] / f"{Path(d['stored_as']).stem}.thumb.png"
            births = [x.get("value") for x in r.get("dates") or [] if verify._BIRTH.search(x.get("label") or "")]
            kind = (r.get("document_type") or "").strip()
            if demo:   # "identity card (marked as a demo document)": the banner already says it
                kind = SAMPLE_KIND.sub("", kind).strip()
            out.append({
                "file": d["filename"], "kind": kind[:1].upper() + kind[1:] if kind else "Document",
                "thumb_url": f"/api/files/claims/{c['id']}/{thumb.name}" if thumb.is_file() else None,
                "status": r.get("status") or "not_read", "problem": r.get("problem"),
                "read": {"names": r.get("names") or [], "date_of_birth": births[0] if births else None,
                         "addresses": [verify._line(a) for a in r.get("addresses") or [] if a.get("whose") != "issuer"],
                         "emails": r.get("emails") or [], "issuer": r.get("issuer") or "",
                         "dates": [{"label": x.get("label") or "", "value": x.get("value") or ""}
                                   for x in r.get("dates") or []]},
                "summary": r.get("summary") or "",
                "concerns": concerns_of(r), "signs_of_editing": bool(r.get("signs_of_editing")),
                "marked_sample": bool(r.get("marked_sample")),
                "reading_source": r.get("reading_source") or "LIVE"})
        return out

    def review_of(c: dict, proof: dict) -> dict:
        required = proof.get("verdict") == verify.NEEDS_REVIEW
        return {"required": required, "reason": c.get("approval_reason"), "approved_by": c.get("approved_by"),
                "approved_at": c.get("approved_at")}

    def view(c: dict, audience: str = "claimant") -> dict:
        """A claim as the claimant sees it, or (audience="business") as the business's desk sees it.

        A rejected claim reveals nothing to the claimant: no checks, record values, flags, documents, concerns or
        explanation and no amount, only REJECTED_TEXT and the attempts left. The business sees everything. The model's remarks on
        a document that is not edited are notes for the business only."""
        proof = dict(c.get("proof") or verify.empty_proof())
        attempts = int(c.get("attempts") or 0)
        rejected = proof.get("verdict") == verify.REJECTED or c["status"] == "REJECTED"
        left = max(0, verify.MAX_ATTEMPTS - attempts)
        proof.update(approved_by=c.get("approved_by"), documents=documents_view(c),
                     explanation=proof.get("explanation"), review=review_of(c, proof), attempts=attempts,
                     attempts_left=left, message=None)
        proof.setdefault("demo", False)
        if rejected:
            proof["missing"] = []
            proof["explanation"] = None
        if audience == "claimant":
            if rejected:
                proof = {"verdict": verify.REJECTED, "score": 0.0, "checks": [], "flags": [], "missing": [],
                         "approved_by": None, "documents": [], "explanation": None,
                         "review": {"required": False, "reason": None, "approved_by": None, "approved_at": None},
                         "attempts": attempts, "attempts_left": left, "demo": proof.get("demo", False),
                         "message": REJECTED_TEXT.format(business=holder["name"])}
            else:
                for d in proof["documents"]:
                    if not d["signs_of_editing"]:
                        d["concerns"] = []
        s = payout_of(c["id"])
        if s is None:
            payout = {"state": "NONE", "batch_id": None, "item_id": None,
                      "receiver_masked": mask.receiver(c.get("receiver"), c.get("receiver_type") or "EMAIL")
                      if c.get("receiver") else "", "fee": None, "timeline": [], "confirmed_by": None}
            invoice = None
        else:
            events = timeline(c["id"], c["amount"], c["currency"])
            final = [e for e in events if e["state"] in ("CLAIMED", "UNCLAIMED", "RETURNED", "FAILED", "CANCELLED")]
            payout = {"state": PAYOUT_MAP[s.state][2], "batch_id": s.payout_batch_id, "item_id": s.payout_item_id,
                      "receiver_masked": mask.receiver(s.receiver, s.receiver_type), "fee": c.get("payout_fee"),
                      "attempt": s.attempt, "timeline": events,
                      "confirmed_by": final[-1]["heard_by"] if final else None}
            invoice = None if not s.invoice_id else {
                "id": s.invoice_id, "number": s.invoice_number, "amount": s.fee, "currency": s.currency,
                "status": s.invoice_status, "payer_url": s.invoice_url}
        claimant = dict(c["claimant"])
        if audience == "claimant" and rejected:
            claimant = {k: claimant.get(k) for k in ("name", "email", "country", "language") if k in claimant}
        b = store.find_balance(c["balance_id"]) or {}
        return {"id": c["id"], "balance_id": c["balance_id"], "status": c["status"], "claimant": claimant,
                "amount": None if audience == "claimant" and rejected else c["amount"],
                "currency": c["currency"], "proof": proof, "payout": payout,
                "invoice": invoice, "source": b.get("source") or "Ledger",
                "payout_address": c.get("payout_address") or None,
                "paypal_held": paypal_state_words(b.get("paypal")) if (b.get("paypal") or {}).get("status")
                == "UNCLAIMED" else None,
                "created_at": c["created_at"], "updated_at": c["updated_at"]}

    def balance_row(b: dict) -> dict:
        esc = b.get("escheat") or {}
        pp = b.get("paypal")
        return {"id": b["id"], "owner_display": mask.owner_display(b), "last_email_masked": mask.email(b.get("last_email")),
                "last_city": b.get("last_city") or "", "last_country": b.get("last_country") or "",
                "amount": b["amount"], "currency": b["currency"], "last_activity": b.get("last_activity"),
                "status": b["status"],
                "sent_to_state_on": esc.get("reported_on") if b["status"] == "ESCHEATED" else None,
                "source": b.get("source") or "Ledger",
                "paypal": None if not pp else {**{k: pp.get(k) for k in ("item_id", "batch_id", "status", "since",
                                                                          "returns_on")},
                                               "words": paypal_state_words(pp)}}

    def reassess(claim_id: str, action: str) -> dict:
        """Code sets the verdict; then, if the result changed, the model explains it (it never changes it)."""
        c = store.claim(claim_id)
        balance = store.balance(c["balance_id"])
        docs = store.documents(claim_id)
        result = verify.assess(balance, c, docs, other_claims=store.claims(),
                               docs_with_hash=store.documents_with_hash, demo=demo)
        before = c.get("proof") or {}
        if result["verdict"] == verify.REJECTED:
            result["explanation"] = None   # a rejected claim is told nothing more, so the model is not asked
        elif before.get("explanation") and assist.signature(before) == assist.signature(result):
            result["explanation"] = before["explanation"]
        else:
            left = max(0, verify.MAX_ATTEMPTS - int(c.get("attempts") or 0))
            result["explanation"] = assist.explain(balance, c, result, holder=holder, attempts_left=left,
                                                   saved=saved, ask=ask)
        status = c["status"]
        out = payout_of(claim_id)
        if c["status"] in ("OPEN", "PROOF_READY", "APPROVED", "REJECTED") and (out is None or out.state == "CREATED"):
            status = {verify.APPROVED: "APPROVED", verify.NEEDS_REVIEW: "PROOF_READY",
                      verify.REJECTED: "REJECTED"}.get(result["verdict"], "OPEN")
            if status == "PROOF_READY" and c.get("approved_by"):
                status = "APPROVED"
        c = store.update_claim(claim_id, action, detail={"verdict": result["verdict"], "flags": result["flags"]},
                               proof=result, status=status)
        if status == "APPROVED":
            store.set_balance_status(c["balance_id"], "PROVEN", why=f"claim {claim_id} proven")
        elif status == "REJECTED":
            settle_balance_after_reject(c["balance_id"])
        return c

    def settle_balance_after_reject(balance_id: str) -> None:
        b = store.balance(balance_id)
        if b["status"] in ("MATCHED", "PROVEN") and not any(
                o["status"] != "REJECTED" for o in store.claims(balance_id)):
            store.set_balance_status(balance_id, "DORMANT", why="every claim on it was rejected")

    def paypal_balance(item, batch_header: dict, seller: dict) -> dict:
        """A dormant balance made from one uncollected item of the business's own PayPal payout history."""
        when = (item.raw.get("time_processed") or batch_header.get("time_created") or "")[:10] or \
            date.today().isoformat()
        back = (date.fromisoformat(when) + timedelta(days=RETURN_DAYS)).isoformat()
        return {"id": f"PP-{item.item_id}", "owner_type": "PERSON", "owner_name": seller.get("seller") or "",
                "date_of_birth": None, "last_email": item.receiver or seller.get("receiver") or "",
                "last_street": "", "last_city": seller.get("city") or "", "last_region": "", "last_postcode": "",
                "last_country": seller.get("country") or "", "amount": item.amount, "currency": item.currency,
                "last_activity": when, "account_opened": None, "status": "DORMANT", "escheat": None,
                "source": PAYPAL_SOURCE,
                "paypal": {"item_id": item.item_id, "batch_id": item.batch_id or batch_header.get("payout_batch_id"),
                           "status": item.status, "since": when, "returns_on": back,
                           "transaction_id": item.raw.get("transaction_id")}}

    def still_held(balance_id: str) -> None:
        """An imported payout that PayPal still holds as UNCLAIMED cannot be paid again until PayPal returns it.
        PayPal is asked for the item's status first, so a payout that came back can be paid at once."""
        b = store.balance(balance_id)
        pp = b.get("paypal") or {}
        if pp.get("status") != "UNCLAIMED":
            return
        from .paypal import PayPalError
        try:
            item = money.client.get_payout_item(pp["item_id"])
            if item.status and item.status != pp["status"]:
                b = store.update_balance_data(balance_id, why="PayPal status checked before paying",
                                              paypal={**pp, "status": item.status})
                pp = b["paypal"]
        except PayPalError:
            pass
        if pp.get("status") == "UNCLAIMED":
            raise ApiError(409, HELD_TEXT)

    # ---- endpoints

    @app.get("/api/totals")
    def totals() -> dict:
        if not totals_path.exists():
            raise ApiError(503, "The California totals have not been computed yet (scripts/paypal_totals.py).")
        t = json.loads(totals_path.read_text())
        return {"as_of": t["as_of"], "source_url": t["source_url"], "source": t.get("source"),
                "holder_records": t["holder_records"], "holder_total": t["holder_total"], "bands": t["bands"],
                "over_100": t["over_100"], "over_1000": t["over_1000"],
                "what_is_counted": "records that PayPal reported to California as unclaimed property",
                "property_types": [{k: p[k] for k in ("label", "records", "total")}
                                   for p in t.get("property_types") or []]}

    @app.get("/api/ledger")
    def ledger(status: str | None = None) -> dict:
        if status and status.upper() not in BALANCE_STATES:
            raise ApiError(400, f"Unknown status {status}. Use one of {', '.join(sorted(BALANCE_STATES))}.")
        return {"balances": [balance_row(b) for b in store.balances(status or None)]}

    @app.post("/api/search")
    def search_endpoint(body: SearchIn) -> dict:
        from .search import Query, search
        from .names import normalise
        q = Query(name=body.name, former_names=body.former_names, emails=body.emails,
                  places=[p.model_dump() for p in body.places])
        typed = {normalise(p.city) for p in body.places if p.city.strip()}
        out = []
        for m in search(store.balances(), q, typed_cities=typed):
            b = store.balance(m["balance_id"])
            city = b.get("last_city") or ""
            # Before a claim is opened: the amount only as a band, the city only when the searcher typed it.
            m = {k: v for k, v in m.items() if k != "amount"}
            m.update(amount_band=amount_band(b["amount"], b["currency"]),
                     city=city if city and normalise(city) in typed else None)
            out.append(m)
        return {"matches": out}

    @app.post("/api/claims")
    def create_claim(body: ClaimIn) -> dict:
        b = balance_or_404(body.balance_id)
        if b["status"] == "ESCHEATED":
            raise ApiError(409, "This balance was already handed to the State of California, so it is claimed "
                                "from the state. Homing can prepare a claim packet for the owner to review, sign "
                                "and send.")
        if b["status"] in ("PAYING", "PAID"):
            raise ApiError(409, "This balance is already being paid to its owner.")
        c = store.create_claim(b, body.claimant.model_dump())
        if b["status"] == "DORMANT":
            store.set_balance_status(b["id"], "MATCHED", why=f"claim {c['id']} opened")
        return view(c)

    @app.get("/api/claims")
    def list_claims() -> dict:
        """The business's desk: every claim with its full checks."""
        return {"claims": [view(sync(c["id"]), "business") for c in store.claims()]}

    @app.get("/api/claims/{claim_id}")
    def get_claim(claim_id: str) -> dict:
        claim_or_404(claim_id)
        return view(sync(claim_id))

    @app.post("/api/claims/{claim_id}/documents")
    async def add_documents(claim_id: str, request: Request) -> dict:
        c = claim_or_404(claim_id)
        try:
            form = await request.form()
        except Exception:
            raise ApiError(400, "Send the documents as multipart form data, in a field named files[].")
        # the contract names the field files[]; plain "files" is accepted too
        files = [f for key in ("files[]", "files") for f in form.getlist(key) if hasattr(f, "read")]
        if c["status"] in ("PAYING", "PAID"):
            raise ApiError(409, "This claim is already being paid; no more documents are needed.")
        if not files:
            raise ApiError(400, "Add at least one photo or PDF.")
        attempts = int(c.get("attempts") or 0)
        if c["status"] == "REJECTED" and attempts >= verify.MAX_ATTEMPTS:
            raise ApiError(409, f"This claim was rejected after {verify.MAX_ATTEMPTS} attempts and is closed. No "
                                "more documents can be added.")
        max_bytes, size_words = documents.MAX_BYTES, "15 MB"
        if public:
            max_bytes, size_words = PUBLIC_MAX_FILE_BYTES, "5 MB"
            if len(store.documents(claim_id)) + len(files) > PUBLIC_MAX_FILES:
                raise ApiError(413, f"This public demo takes at most {PUBLIC_MAX_FILES} files per claim.")
        folder = files_dir / "claims" / claim_id
        folder.mkdir(parents=True, exist_ok=True)
        added = []
        for f in files[:8]:
            data = await f.read(max_bytes + 1)
            if len(data) > max_bytes:
                raise ApiError(413, f"{documents.safe_label(f.filename)} is larger than {size_words}.")
            kind = documents.sniff(data)
            if not kind:
                raise ApiError(415, f"{documents.safe_label(f.filename)} is not a photo or a PDF.")
            digest = hashlib.sha256(data).hexdigest()
            stored = f"{secrets.token_hex(6)}{kind[1]}"
            (folder / stored).write_bytes(data)
            documents.make_thumbnail(folder / stored, folder / f"{Path(stored).stem}.thumb.png")
            added.append(store.add_document(claim_id, filename=documents.safe_label(f.filename),
                                            content_type=kind[0], sha256=digest, stored_as=stored))

        def work(doc: dict) -> None:
            try:
                reading = read(folder / doc["stored_as"], doc["sha256"])
            except Exception:
                reading = {"status": "failed", "problem": "The document could not be read this time."}
            store.set_reading(doc["id"], reading)

        with ThreadPoolExecutor(max_workers=3) as pool:
            list(pool.map(work, added))
        store.update_claim(claim_id, "documents_attempt", detail={"attempt": attempts + 1, "files": len(added)},
                           attempts=attempts + 1)
        reassess(claim_id, "proof_assessed")
        # Another claim that shares one of these files is now an impostor pattern too.
        for doc in added:
            for other in store.documents_with_hash(doc["sha256"]):
                if other["claim_id"] != claim_id:
                    reassess(other["claim_id"], "proof_reassessed")
        return view(store.claim(claim_id))

    @app.post("/api/claims/{claim_id}/approve")
    def approve(claim_id: str, body: ApproveIn) -> dict:
        c = claim_or_404(claim_id)
        proof = c.get("proof") or {}
        verdict = proof.get("verdict", verify.NONE)
        if c["status"] == "REJECTED" or verdict == verify.REJECTED:
            raise ApiError(409, "This claim was rejected by the checks and cannot be approved.")
        if verdict == verify.APPROVED or c.get("approved_by"):
            return view(c, "business")
        if verdict != verify.NEEDS_REVIEW:
            raise ApiError(409, "There is nothing to approve yet. Add the claimant's documents first.")
        open_checks = [x["field"].replace("_", " ") for x in proof.get("checks") or [] if x["result"] != verify.MATCH]
        if open_checks and not body.reason:
            raise ApiError(400, "Write why you are satisfied before approving: these checks are not a match: "
                                + ", ".join(open_checks) + ".")
        at = datetime.now().astimezone().isoformat(timespec="seconds")
        c = store.update_claim(claim_id, "human_approved",
                               detail={"approver": body.approver, "reason": body.reason or None,
                                       "checks_not_matching": open_checks},
                               approved_by=body.approver, approved_at=at, approval_reason=body.reason or None,
                               status="APPROVED")
        store.set_balance_status(c["balance_id"], "PROVEN", why=f"claim {claim_id} approved by a person")
        return view(c, "business")

    def check_receiver(c: dict, receiver: str, *, reject: bool = True) -> None:
        """The impostor rule at payment time: one receiving account may not collect several owners' money.

        Before any payout the claim is rejected. Once a payout exists (a new contact after an unclaimed one) only
        that contact is refused and flagged, so the payout already made is still followed and can be cancelled."""
        if receiver.lower() in verify.shared_test_accounts():
            return
        others = [o for o in store.claims() if o["id"] != c["id"] and o["balance_id"] != c["balance_id"]
                  and receiver.lower() in {a.lower() for a in (o.get("receiver"), o["claimant"].get("email")) if a}]
        if others:
            proof = dict(c.get("proof") or verify.empty_proof())
            flag = f"same account used on {len(others) + 1} claims for different balances"
            proof["flags"] = [*[f for f in proof.get("flags", []) if not f.startswith("same account")], flag]
            if not reject:
                store.update_claim(c["id"], "contact_refused", detail={"flag": flag}, proof=proof)
                raise ApiError(409, f"This contact is refused: the {flag}. Please give the owner's own email "
                                    "or phone.")
            proof["verdict"] = verify.REJECTED
            store.update_claim(c["id"], "rejected_at_payment", detail={"flag": flag}, proof=proof,
                               status="REJECTED")
            settle_balance_after_reject(c["balance_id"])
            raise ApiError(409, f"Payment refused: the {flag}.")

    @app.post("/api/claims/{claim_id}/pay")
    def pay(claim_id: str, body: PayIn) -> dict:
        c = claim_or_404(claim_id)
        if not proven(c):
            raise ApiError(409, "This claim cannot be paid yet. Its documents have not been checked, and no "
                                f"person at {holder['name']} has approved it.")
        s = need_money()
        if payout_of(claim_id) is None:
            still_held(c["balance_id"])
        if body.currency and body.currency.upper() != c["currency"]:
            raise ApiError(400, f"This balance is held in {c['currency']}; pay it in {c['currency']}.")
        for other in store.claims(c["balance_id"]):
            if other["id"] == claim_id:
                continue
            out = payout_of(other["id"])
            if other["status"] in ("PAYING", "PAID") or (out is not None and out.state in ("CREATED", "SENT",
                                                                                         "UNCLAIMED", "CLAIMED")):
                raise ApiError(409, "Another claim has already sent this balance to its owner. It is never paid "
                                    "twice.")
        existing = payout_of(claim_id)
        if existing is not None:
            if existing.receiver.lower() != body.receiver.lower() and existing.state != "CREATED":
                raise ApiError(409, "This claim has already been sent to another contact. It is never paid twice.")
            if existing.state == "CREATED":
                guard_payout(existing.receiver, existing.amount)
        else:
            guard_payout(body.receiver, c["amount"])
            check_receiver(c, body.receiver)
            store.update_claim(claim_id, "pay_requested", detail={"receiver_type": body.receiver_type},
                               receiver=body.receiver, receiver_type=body.receiver_type)
            s.create_claim(claim_id, receiver=body.receiver, receiver_type=body.receiver_type, amount=c["amount"],
                           currency=c["currency"], note=payout_note)
        send(claim_id)
        return view(sync(claim_id))

    def send(claim_id: str) -> None:
        from .paypal import PayPalError
        try:
            with pay_lock:   # the guard and the send together, so two requests cannot both fit under the cap
                pending = payout_of(claim_id)
                if pending is not None and pending.state == "CREATED":
                    guard_payout(pending.receiver, pending.amount)
                money.send(claim_id)
        except PayPalError as e:
            sync(claim_id)
            if e.name == "INSUFFICIENT_FUNDS":
                raise ApiError(502, f"{holder['name']}'s PayPal account does not have enough money to pay this right "
                                    "now, so nothing was sent. Please try again once it has been topped up.")
            raise ApiError(502, "PayPal did not accept the payment, so no money left. Please try again in a "
                                "moment.")

    def refresh_fee(claim_id: str) -> None:
        s = payout_of(claim_id)
        if s is None or not s.payout_item_id:
            return
        try:
            item = money.client.get_payout_item(s.payout_item_id)
        except Exception:
            return
        if item.fee and item.fee != store.claim(claim_id).get("payout_fee"):
            store.update_claim(claim_id, "payout_fee", payout_fee=item.fee)

    def waiting_for_webhook(claim_id: str, current) -> bool:
        """With webhooks on, PayPal is not asked about a payout until `webhook_grace` seconds after it was sent,
        so a webhook can confirm it first. After that the refresh endpoint polls PayPal as usual."""
        if not (money.webhook_id and webhook_grace > 0) or current.state != "SENT":
            return False
        sends = [r["at"] for r in money.audit(claim_id) if r["action"] == "send"]
        if not sends:
            return False
        sent_at = datetime.fromisoformat(sends[-1])
        age = (datetime.now(sent_at.tzinfo) - sent_at).total_seconds()
        return age < webhook_grace

    @app.post("/api/claims/{claim_id}/refresh")
    def refresh(claim_id: str) -> dict:
        from .paypal import PayPalError
        claim_or_404(claim_id)
        current = payout_of(claim_id)
        if current is None:
            return view(store.claim(claim_id))
        if waiting_for_webhook(claim_id, current):
            return view(sync(claim_id))
        try:
            money.refresh(claim_id)
        except PayPalError:
            raise ApiError(502, "PayPal could not be reached for an update. We will keep asking.")
        refresh_fee(claim_id)
        return view(sync(claim_id))

    @app.post("/api/claims/{claim_id}/new-contact")
    def new_contact(claim_id: str, body: ContactIn) -> dict:
        from .paypal import PayPalError
        from .settlement import SettlementError
        c = claim_or_404(claim_id)
        s = payout_of(claim_id)
        if s is None:
            raise ApiError(409, "Nothing has been sent for this claim yet, so there is no contact to change.")
        if s.state not in ("UNCLAIMED", "RETURNED", "FAILED", "CANCELLED"):
            raise ApiError(409, "A new contact can only be tried once the first payment went uncollected or "
                                "came back. This one is " + {"CREATED": "being sent", "SENT": "on its way",
                                                             "CLAIMED": "already collected"}.get(s.state, "in progress")
                           + ".")
        guard_payout(body.receiver, c["amount"])
        check_receiver(c, body.receiver, reject=False)
        try:
            money.retarget(claim_id, receiver=body.receiver, receiver_type=body.receiver_type)
        except SettlementError:
            raise ApiError(409, HELD_TEXT)
        except PayPalError:
            # PayPal's sandbox answers 500 when an unclaimed item is cancelled; the first payment then stays
            # where it is, unclaimed, and no second payment is made.
            sync(claim_id)
            raise ApiError(409, HELD_TEXT)
        store.update_claim(claim_id, "new_contact", detail={"receiver_type": body.receiver_type},
                           receiver=body.receiver, receiver_type=body.receiver_type, payout_fee=None)
        send(claim_id)
        return view(sync(claim_id))

    @app.post("/api/webhooks/paypal")
    async def webhook(request: Request) -> dict:
        s = need_money()
        try:
            event = json.loads(await request.body())
        except ValueError:
            raise ApiError(400, "The webhook body is not JSON.")
        if not isinstance(event, dict) or not event.get("id"):
            raise ApiError(400, "The webhook body is not a PayPal event.")
        if not s.webhook_id:
            raise ApiError(503, "Webhook verification is not set up on this server (PAYPAL_WEBHOOK_ID).")
        outcome = s.handle_webhook(dict(request.headers), event)
        if outcome == "rejected":
            raise ApiError(400, "The webhook could not be verified with PayPal.")
        claim_id, _ = s.event_claim(event["id"])
        print(f"webhook {event.get('event_type')} {event['id']} created {event.get('create_time')} "
              f"received {datetime.now().astimezone().isoformat(timespec='seconds')} outcome {outcome} "
              f"claim {claim_id}", flush=True)
        known = claim_id if claim_id and any(c["id"] == claim_id for c in store.claims()) else None
        store.audit("paypal_webhook", claim_id=known,
                    detail={"note": "confirmed by PayPal webhook", "event_id": event["id"],
                            "event_type": event.get("event_type"), "outcome": outcome})
        for c in store.claims():
            sync(c["id"])
        return {"ok": True, "outcome": outcome}

    @app.get("/api/scoreboard")
    def scoreboard() -> dict:
        claims = [sync(c["id"]) for c in store.claims()]
        states = {c["id"]: (payout_of(c["id"]).state if payout_of(c["id"]) else None) for c in claims}
        paid = [c for c in claims if c["status"] == "PAID"]
        minutes = []
        for c in paid:
            if c.get("paid_at"):
                delta = datetime.fromisoformat(c["paid_at"]) - datetime.fromisoformat(c["created_at"])
                minutes.append(max(delta.total_seconds(), 0) / 60)
        fees = Decimal("0")
        for c in claims:
            s = payout_of(c["id"])
            if s is not None and s.invoice_id and s.fee:
                fees += Decimal(s.fee)
        return {"found": len({c["balance_id"] for c in claims if c["status"] != "REJECTED"}),
                "proven": sum(1 for c in claims if proven(c)),
                "paid": len(paid),
                "unclaimed": sum(1 for v in states.values() if v == "UNCLAIMED"),
                "returned": sum(1 for v in states.values() if v in ("RETURNED", "CANCELLED")),
                "rejected": sum(1 for c in claims if c["status"] == "REJECTED"),
                "paid_total": f"{sum((Decimal(c['amount']) for c in paid), Decimal('0')):.2f}",
                "fees_invoiced": f"{fees:.2f}",
                "median_minutes_to_paid": round(statistics.median(minutes), 1) if minutes else 0}

    @app.post("/api/state-claim/{balance_id}")
    def state_claim(balance_id: str, body: StateClaimIn | None = None) -> dict:
        b = balance_or_404(balance_id)
        if b["status"] != "ESCHEATED":
            raise ApiError(409, f"This balance is still with {holder['name']}, so its owner can claim it here directly.")
        claimant = body.claimant.model_dump() if body and body.claimant else {}
        if not claimant:
            earlier = store.claims(balance_id)
            claimant = earlier[-1]["claimant"] if earlier else {"name": b["owner_name"],
                                                                 "email": b.get("last_email") or "",
                                                                 "country": b.get("last_country") or "US"}
        packet_id = f"S{secrets.token_hex(4)}"
        pdf, steps = stateclaim.build(b, claimant, packet_id)
        folder = files_dir / "state-claims"
        folder.mkdir(parents=True, exist_ok=True)
        name = f"{balance_id}-{packet_id}.pdf"
        (folder / name).write_bytes(pdf)
        store.audit("state_claim_prepared", balance_id=balance_id, detail={"file": name})
        return {"pdf_url": f"/api/files/state-claims/{name}", "steps": steps}

    @app.post("/api/import/paypal")
    def import_paypal() -> dict:
        """Asks PayPal, live, about every batch in the business's payout history (data/payout_history.json) and adds
        each item PayPal reports UNCLAIMED or RETURNED to the dormant balances. Safe to run again: an item already
        imported only has its status brought up to date."""
        from .paypal import PayPalError
        s = need_money()
        try:
            history = json.loads(history_path.read_text())
        except (OSError, ValueError):
            raise ApiError(404, "There is no PayPal payout history to import yet (scripts/seed_uncollected.py "
                                "writes data/payout_history.json).")
        sellers = {p["payout_batch_id"]: p for p in history.get("payouts") or [] if p.get("payout_batch_id")}
        added, updated, checked, rows = [], [], 0, []
        for batch_id, seller in sellers.items():
            try:
                batch = s.client.get_payout_batch(batch_id)
            except PayPalError:
                raise ApiError(502, "PayPal could not be reached for the payout history. Nothing more was imported; "
                                    "please try again in a moment.")
            header = batch.raw.get("batch_header") or {}
            for item in batch.items:
                checked += 1
                if item.status not in ("UNCLAIMED", "RETURNED"):
                    continue
                fresh = paypal_balance(item, header, seller)
                have = store.find_balance(fresh["id"])
                if have is None:
                    store.add_balance(fresh, why=f"uncollected PayPal payout {item.item_id} imported")
                    added.append(fresh["id"])
                elif (have.get("paypal") or {}).get("status") != item.status:
                    store.update_balance_data(fresh["id"], why="PayPal status changed",
                                              paypal={**(have.get("paypal") or {}), "status": item.status})
                    updated.append(fresh["id"])
                rows.append(balance_row(store.balance(fresh["id"])))
        store.audit("paypal_import", detail={"batches": len(sellers), "items": checked, "added": added,
                                             "updated": updated})
        return {"batches_checked": len(sellers), "items_checked": checked, "added": len(added),
                "updated": len(updated), "balances": rows}

    @app.get("/api/claims/{claim_id}/paypal-record")
    def paypal_record(claim_id: str) -> dict:
        """PayPal's own record of this claim's payout, fetched live: GET /v1/payments/payouts-item/{id}."""
        from .paypal import PayPalError
        claim_or_404(claim_id)
        out = payout_of(claim_id)
        if out is None or not out.payout_item_id:
            raise ApiError(409, "Nothing has been sent through PayPal for this claim yet.")
        try:
            raw = money.client.get_payout_item(out.payout_item_id).raw
        except PayPalError:
            raise ApiError(502, "PayPal could not be reached just now. Please try again in a moment.")
        return {"endpoint": f"GET /v1/payments/payouts-item/{out.payout_item_id}",
                "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "record": {"transaction_status": raw.get("transaction_status"),
                           "payout_item_id": raw.get("payout_item_id"),
                           "payout_batch_id": raw.get("payout_batch_id"),
                           "transaction_id": raw.get("transaction_id"),
                           "time_processed": raw.get("time_processed"),
                           "amount": (raw.get("payout_item") or {}).get("amount"),
                           "payout_item_fee": raw.get("payout_item_fee")}}

    @app.post("/api/balances/{balance_id}/notice")
    def owner_notice(balance_id: str, request: Request) -> dict:
        b = balance_or_404(balance_id)
        if b["status"] == "ESCHEATED":
            raise ApiError(409, f"This balance was already handed to the state, so {holder['name']} no longer "
                                "notifies the owner about it.")
        if b["status"] in ("PAYING", "PAID"):
            raise ApiError(409, "This balance is already being paid to its owner.")
        try:
            n = assist.notice(b, holder=holder, saved=saved, ask=ask)
        except llm.ModelUnavailable:
            raise ApiError(503, assist.NO_MODEL_NOTICE)
        store.audit("notice_drafted", balance_id=balance_id,
                    detail={"language": n["language"], "source": n["source"], "sent": False})
        link = (os.environ.get("HOMING_PUBLIC_URL") or str(request.base_url)).rstrip("/")
        return {**n, **{k: LINK_TOKEN.sub(link, n[k]) for k in ("subject", "body", "english", "english_subject")
                        if isinstance(n.get(k), str)}, "link": link}

    @app.get("/api/meta")
    def meta() -> dict:
        return {"holder": holder, "demo": demo, "test_receivers": verify.test_receivers(),
                "model_available": llm.available(), "public": public}

    @app.get("/api/files/claims/{claim_id}/{name}")
    def claim_file(claim_id: str, name: str):
        base = (files_dir / "claims").resolve()
        path = (files_dir / "claims" / claim_id / name).resolve()
        if path.parent.parent != base or not name.endswith(".thumb.png") or not path.is_file():
            raise ApiError(404, "There is no such file.")
        return FileResponse(path, media_type="image/png")

    @app.get("/api/files/state-claims/{name}")
    def state_claim_file(name: str):
        path = (files_dir / "state-claims" / name).resolve()
        if path.parent != (files_dir / "state-claims").resolve() or not path.is_file():
            raise ApiError(404, "There is no such file.")
        return FileResponse(path, media_type="application/pdf", filename=name)

    # ---- the desk agent (agent.py): tools are plain code; the model only chooses which to call

    agent_model_given = agent_ask is not None
    agent_ask = agent_ask or (lambda p: llm.ask(p, model="sonnet", timeout=120))
    tool_lock = threading.RLock()
    runs: dict[str, dict] = {}
    app.state.agent_runs = runs

    def gate(c: dict) -> str | None:
        """Why code will not let the desk agent pay this claim, or None when it may."""
        p = c.get("proof") or {}
        if c["status"] == "REJECTED" or p.get("verdict") == verify.REJECTED:
            return "this claim was turned down by the checks, so it is never paid."
        if c["status"] in ("PAYING", "PAID") or payout_of(c["id"]) is not None:
            return "a payment for this claim has already been sent; it is never paid twice."
        if p.get("verdict") != verify.APPROVED:
            if not c.get("approved_by"):
                return "the checks did not approve this claim and no person has approved it."
            if not (c.get("approval_reason") or "").strip():
                return "a person approved this claim without writing why."
        if not (c.get("payout_address") or "").strip():
            return "the owner has not left a PayPal address to pay."
        return None

    def find_claim(ref: str) -> dict:
        ref = str(ref or "").strip()
        try:
            return store.claim(ref)
        except NotFound:
            pass
        live = [c for c in store.claims(ref) if c["status"] != "REJECTED"] or store.claims(ref)
        if not live:
            raise agent.Refused(f"there is no claim {ref}.")
        return live[-1]

    def money_text(amount, currency) -> str:
        return f"${Decimal(amount):,.2f}" if currency == "USD" else f"{Decimal(amount):,.2f} {currency}"

    def t_paypal_item(item_id: str):
        s = need_money()
        item = s.client.get_payout_item(str(item_id))
        b = store.find_balance(f"PP-{item.item_id}")
        when = (item.raw.get("time_processed") or "")[:10]
        back = (b.get("paypal") or {}).get("returns_on") if b else None
        if b and item.status and item.status != (b.get("paypal") or {}).get("status"):
            store.update_balance_data(b["id"], why="PayPal status checked by the desk agent",
                                      paypal={**(b.get("paypal") or {}), "status": item.status})
        text = (f"PayPal says item {item.item_id} is {item.status}: {money_text(item.amount or '0', item.currency or 'USD')}"
                + (f", sent {when}" if when else "") + (f"; PayPal returns it on {back}" if back and item.status ==
                                                         "UNCLAIMED" else "") + ".")
        return text, {"item_id": item.item_id, "batch_id": item.batch_id, "status": item.status,
                      "amount": item.amount, "currency": item.currency, "time_processed": when,
                      "returns_on": back, "balance_id": b["id"] if b else None,
                      "endpoint": f"GET /v1/payments/payouts-item/{item.item_id}"}

    def t_paypal_batch(batch_id: str):
        s = need_money()
        batch = s.client.get_payout_batch(str(batch_id))
        items = [{"item_id": i.item_id, "status": i.status, "amount": i.amount} for i in batch.items]
        return (f"PayPal says batch {batch.batch_id} is {batch.status} with {len(items)} item(s): "
                + ", ".join(f"{i['item_id']} {i['status']}" for i in items) + "."), {
            "batch_id": batch.batch_id, "status": batch.status, "items": items,
            "endpoint": f"GET /v1/payments/payouts/{batch.batch_id}"}

    def t_ledger_lookup(query: str):
        from .names import normalise
        q = normalise(str(query or ""))
        ql = str(query or "").strip().lower()
        if len(q) < 3 and "@" not in ql:
            raise agent.Refused("give a name or an email of at least three letters.")
        words = set(q.split())
        hits = []
        for b in store.balances():
            n = set(normalise(b.get("owner_name") or "").split())
            if (words and words <= n) or (ql and ql == (b.get("last_email") or "").lower()):
                hits.append(b)
        rows = [{"balance_id": b["id"], "owner": b.get("owner_name"), "city": b.get("last_city"),
                 "country": b.get("last_country"), "date_of_birth": b.get("date_of_birth"),
                 "amount": b["amount"], "status": b["status"],
                 "claims": [{"claim_id": c["id"], "status": c["status"]} for c in store.claims(b["id"])]}
                for b in hits]
        same = len({r["owner"] for r in rows}) < len(rows)
        text = f"{len(rows)} record(s) for {query!r}: " + "; ".join(
            f"{r['balance_id']} {r['owner']}, {r['city']}, born {r['date_of_birth'] or 'unknown'}, "
            f"{money_text(r['amount'], 'USD')}, {r['status']}" for r in rows) + (
            ". Several different owners share this name." if same else ".")
        return text, {"records": rows, "same_name": same}

    def t_claim_status(balance_id: str = "", claim_id: str = ""):
        ref = str(claim_id or balance_id or "").strip()
        if store.find_balance(ref) is not None and not store.claims(ref):
            b = store.balance(ref)
            return (f"No claim has been opened on {ref} ({b.get('owner_name')}, {b['status']}). Nothing can be paid "
                    "without a claim and its checks."), {"balance_id": ref, "claim_id": None, "payable": False}
        c = find_claim(ref)
        c = sync(c["id"])
        p = c.get("proof") or {}
        checks = {x["field"]: x["result"] for x in p.get("checks") or []}
        out = payout_of(c["id"])
        why = gate(c)
        text = (f"Claim {c['id']} on {c['balance_id']} for {c['claimant'].get('name')}: status {c['status']}, "
                f"checks verdict {p.get('verdict', 'NONE')}"
                + (f", approved by {c['approved_by']} because: {c.get('approval_reason')}" if c.get("approved_by")
                   else "")
                + (f"; checks {', '.join(f'{k} {v}' for k, v in checks.items())}" if checks else "")
                + (f"; flags: {'; '.join(p.get('flags') or [])}" if p.get("flags") else "")
                + ("; owner left a PayPal address" if c.get("payout_address") else "; no PayPal address left")
                + (f"; payout {out.state}" if out else "")
                + (f". Code would refuse payment: {why}" if why else ". Code would allow payment.") )
        return text, {"claim_id": c["id"], "balance_id": c["balance_id"], "status": c["status"],
                      "verdict": p.get("verdict"), "checks": checks, "flags": p.get("flags") or [],
                      "approved_by": c.get("approved_by"), "approval_reason": c.get("approval_reason"),
                      "has_address": bool(c.get("payout_address")), "payable": why is None,
                      "payout": out.state if out else None}

    def t_draft_notice(balance_id: str):
        b = store.find_balance(str(balance_id))
        if b is None:
            raise agent.Refused(f"there is no balance {balance_id}.")
        if b["status"] in ("ESCHEATED", "PAYING", "PAID"):
            raise agent.Refused("this balance is no longer the business's to notify about.")
        n = assist.notice(b, holder=holder, saved=saved, ask=ask)
        store.audit("notice_drafted", balance_id=b["id"],
                    detail={"language": n["language"], "source": n["source"], "sent": False, "by": "desk agent"})
        return (f"Notice drafted in {n['language']} ({'model' if n['source'] == 'LIVE' else 'saved copy'}), "
                f"not sent: \"{n['subject']}\"."), {"balance_id": b["id"], "language": n["language"],
                                                   "subject": n["subject"], "english_subject": n.get("english_subject"),
                                                   "body": n["body"], "english": n.get("english")}

    def t_hold(balance_id: str, until: str, reason: str = ""):
        b = store.find_balance(str(balance_id))
        if b is None:
            raise agent.Refused(f"there is no balance {balance_id}.")
        if b["status"] in ("ESCHEATED", "PAYING", "PAID"):
            raise agent.Refused(f"this balance is {b['status'].lower()}; there is nothing to hold.")
        try:
            day = date.fromisoformat(str(until)[:10])
        except ValueError:
            raise agent.Refused("until must be a date like 2026-12-12.")
        if day < date.today():
            raise agent.Refused("until is in the past.")
        back = (b.get("paypal") or {}).get("returns_on")
        if back and (b.get("paypal") or {}).get("status") == "UNCLAIMED" and day < date.fromisoformat(back):
            raise agent.Refused(f"PayPal still holds this payout until {back}; a hold must last at least that long "
                                "so it is never paid twice.")
        if not str(reason or "").strip():
            raise agent.Refused("write a reason for the hold.")
        store.update_balance_data(b["id"], why="held by the desk agent",
                                  hold={"until": day.isoformat(), "reason": str(reason)[:300], "by": "desk agent",
                                        "at": datetime.now(timezone.utc).isoformat(timespec="seconds")})
        return f"Hold recorded on {b['id']} until {day.isoformat()}.", {"balance_id": b["id"],
                                                                         "until": day.isoformat()}

    def t_send_to_person(claim_id: str = "", reason: str = "", balance_id: str = ""):
        ref = str(claim_id or balance_id or "").strip()
        if not str(reason or "").strip():
            raise agent.Refused("write why a person should look at this.")
        c = None
        try:
            c = store.claim(ref)
        except NotFound:
            pass
        b = store.find_balance(c["balance_id"] if c else ref)
        if b is None:
            raise agent.Refused(f"there is no claim or balance {ref}.")
        if c and c["status"] in ("PAYING", "PAID"):
            raise agent.Refused("this claim is already being paid.")
        store.update_balance_data(b["id"], why="sent to a person by the desk agent",
                                  review={"reason": str(reason)[:300], "claim_id": c["id"] if c else None,
                                          "by": "desk agent",
                                          "at": datetime.now(timezone.utc).isoformat(timespec="seconds")})
        store.audit("sent_to_person", claim_id=c["id"] if c else None, balance_id=b["id"],
                    detail={"reason": str(reason)[:300], "by": "desk agent"})
        return (f"{c['id'] if c else b['id']} is on the list for a person at {holder['name']}."
                ), {"balance_id": b["id"], "claim_id": c["id"] if c else None}

    def t_pay_claim(claim_id: str):
        c = find_claim(claim_id)
        why = gate(c)
        if why:
            store.audit("agent_pay_refused", claim_id=c["id"], balance_id=c["balance_id"], detail={"why": why})
            raise agent.Refused(why)
        try:
            v = pay(c["id"], PayIn(receiver=c["payout_address"], receiver_type="EMAIL", currency=c["currency"]))
        except ApiError as e:
            raise agent.Refused(e.message)
        po = v["payout"]
        return (f"Sent {money_text(c['amount'], c['currency'])} through PayPal Payouts to {po['receiver_masked']}: "
                f"batch {po['batch_id']}, item {po['item_id'] or 'pending'}, PayPal says {po['state']}."), {
            "claim_id": c["id"], "balance_id": c["balance_id"], "state": po["state"], "batch_id": po["batch_id"],
            "item_id": po["item_id"], "amount": c["amount"]}

    def t_wait_for_paypal(claim_id: str):
        from .paypal import PayPalError
        c = find_claim(claim_id)
        if payout_of(c["id"]) is None:
            raise agent.Refused("nothing has been sent for this claim.")
        start = datetime.now(timezone.utc)
        asked = False
        while True:
            v = view(sync(c["id"]), "business")
            po = v["payout"]
            if po["state"] not in ("SENT", "NONE"):
                break
            age = (datetime.now(timezone.utc) - start).total_seconds()
            if age > 120:
                break
            if age > (25 if money.webhook_id else 0):   # a webhook gets the first chance; then PayPal is asked
                try:
                    money.refresh(c["id"])
                    asked = True
                except PayPalError:
                    pass
            threading.Event().wait(2.0)
        refresh_fee(c["id"])
        for _ in range(10):   # the fee invoice follows the CLAIMED state through the existing path
            v = view(sync(c["id"]), "business")
            if v["payout"]["state"] != "CLAIMED" or (v.get("invoice") or {}).get("status"):
                break
            threading.Event().wait(1.5)
        po, inv = v["payout"], v.get("invoice")
        how = {"WEBHOOK": "a verified PayPal webhook", "POLLING": "asking PayPal"}.get(po.get("confirmed_by") or "",
                                                                                   "asking PayPal" if asked else "")
        text = (f"PayPal reports item {po['item_id']} {po['state']}"
                + (f", confirmed by {how}" if how else "") + "."
                + (f" Fee invoice {inv['number']} for {money_text(inv['amount'], inv['currency'])} is "
                   f"{(inv['status'] or '').lower()} to {holder['name']}." if inv and inv.get("amount") else
                   " No fee invoice yet."))
        return text, {"claim_id": c["id"], "balance_id": c["balance_id"], "state": po["state"],
                      "item_id": po["item_id"], "confirmed_by": po.get("confirmed_by"),
                      "invoice": inv and {k: inv.get(k) for k in ("number", "status", "amount")}}

    def desk_tools(dry_pay: bool = False) -> dict[str, agent.Tool]:
        def locked(fn):
            def run(**kw):
                with tool_lock:
                    return fn(**kw)
            return run
        pay_fn = t_pay_claim
        rehearsed: set[str] = set()
        if dry_pay:
            def pay_fn(claim_id: str):   # rehearsal: the gate runs, nothing is sent
                c = find_claim(claim_id)
                why = gate(c) or ("a payment for this claim was already made in this rehearsal; it is never paid "
                                  "twice." if c["id"] in rehearsed else None)
                if why:
                    raise agent.Refused(why)
                rehearsed.add(c["id"])
                return (f"Rehearsal: code would send {money_text(c['amount'], c['currency'])} to the owner's "
                        "PayPal address now. Nothing was sent."), {"claim_id": c["id"], "balance_id": c["balance_id"],
                                                                    "state": "REHEARSAL"}
        wait_fn = t_wait_for_paypal
        if dry_pay:
            def wait_fn(claim_id: str):
                c = find_claim(claim_id)
                if payout_of(c["id"]) is None:
                    return "Rehearsal: nothing was sent, so there is nothing to confirm.", {"claim_id": c["id"],
                                                                                            "state": "REHEARSAL"}
                return t_wait_for_paypal(claim_id)
        T = agent.Tool
        return {t.name: t for t in [
            T("paypal_payout_item", "item_id", "ask PayPal live for one payout item's status", locked(t_paypal_item)),
            T("paypal_batch", "batch_id", "ask PayPal live for a payout batch and its items", locked(t_paypal_batch)),
            T("ledger_lookup", "query", "find the business's records for a name or email (other balances, "
                                         "same-name owners, claims)", locked(t_ledger_lookup)),
            T("claim_status", "balance_id or claim_id", "a claim's status, checks, verdict, approval and whether code "
                                                        "would allow payment", locked(t_claim_status)),
            T("draft_notice", "balance_id", "draft the owner notice in the owner's language (not sent)",
              t_draft_notice),
            T("hold", "balance_id, until (YYYY-MM-DD), reason", "record a hold on a balance", locked(t_hold)),
            T("send_to_person", "claim_id or balance_id, reason", "put a claim or balance on the list for a person",
              locked(t_send_to_person)),
            T("pay_claim", "claim_id", "pay an approved claim to the PayPal address the owner left, through "
                                       "PayPal Payouts", locked(pay_fn)),
            T("wait_for_paypal", "claim_id", "wait for PayPal's confirmation of a sent payment and the fee invoice",
              wait_fn),
        ]}

    app.state.desk_tools = desk_tools

    def queue_tasks() -> list[dict]:
        """The open work on the desk, in the order the desk agent works it."""
        tasks = []
        for b in store.balances():
            pp = b.get("paypal") or {}
            if b.get("source") == PAYPAL_SOURCE and b["status"] == "DORMANT" and not b.get("hold"):
                tasks.append({"kind": "paypal", "group": "a", "balance_id": b["id"],
                              "title": f"Imported PayPal payout {pp.get('item_id')} ({b.get('owner_name')})",
                              "task": (f"Balance {b['id']} is a seller payout imported from PayPal: item "
                                       f"{pp.get('item_id')}, batch {pp.get('batch_id')}, owner {b.get('owner_name')}, "
                                       f"{money_text(b['amount'], b['currency'])}. PayPal returns uncollected payouts "
                                       f"on {pp.get('returns_on')}. Ask PayPal for the item's live status. If PayPal "
                                       "still holds it, hold the balance until PayPal's return date and draft the "
                                       "owner notice. Then finish.")})
        from .names import normalise
        by_name: dict[str, list[dict]] = {}
        for b in store.balances():
            if b["status"] in ("DORMANT", "MATCHED") and b.get("owner_type", "PERSON") == "PERSON" and not b.get("review"):
                by_name.setdefault(normalise(b.get("owner_name") or ""), []).append(b)
        for name, group in by_name.items():
            if len(group) > 1 and name:
                ids = ", ".join(x["id"] for x in group)
                tasks.append({"kind": "same_name", "group": "b", "balance_id": group[0]["id"],
                              "title": f"Same name on {len(group)} balances ({group[0]['owner_name']})",
                              "task": (f"Balances {ids} all carry the owner name {group[0]['owner_name']}. Look at the "
                                       "business's records for that name and decide what has to happen before "
                                       "anyone could be paid. If the records alone cannot tell the owners apart, send "
                                       "each balance to a person with your reason. Then finish.")})
        for c in store.claims():
            c = sync(c["id"])
            p = c.get("proof") or {}
            if c["status"] == "REJECTED":
                tasks.append({"kind": "rejected", "group": "c", "claim_id": c["id"],
                              "title": f"Turned-down claim {c['id']} ({c['claimant'].get('name')})",
                              "task": f"Claim {c['id']} is on the desk. Check it and do only what is right. Then finish."})
            elif c["status"] in ("APPROVED", "PROOF_READY") and payout_of(c["id"]) is None:
                person = p.get("verdict") == verify.NEEDS_REVIEW and not c.get("approved_by")
                tasks.append({"kind": "waiting" if person else "approved", "group": "d", "claim_id": c["id"],
                              "title": f"Claim {c['id']} ({c['claimant'].get('name')})",
                              "task": (f"Claim {c['id']} is on the desk. Check it. If code allows payment, pay it, "
                                       "wait for PayPal's confirmation and check the fee invoice. If not, say why "
                                       "and leave it for a person. Then finish.")})
        order = {"a": 0, "b": 1, "c": 2, "d": 3}
        return sorted(tasks, key=lambda t: order[t["group"]])

    def brake_tasks() -> list[dict]:
        out = []
        for c in store.claims():
            if c["status"] == "REJECTED":
                out.append({"kind": "brakes", "group": "e", "claim_id": c["id"],
                            "title": f"Asked to pay turned-down claim {c['id']} ({c['claimant'].get('name')})",
                            "task": (f"A member of staff asks you to pay claim {c['id']} now. Call pay_claim with "
                                     f"claim_id {c['id']} once, report exactly what happened, then finish.")})
                break
        return out

    def start_run(mode: str, dry_pay: bool) -> dict:
        tasks = queue_tasks() if mode == "queue" else brake_tasks()
        run_id = f"R{secrets.token_hex(4)}"
        run = {"id": run_id, "mode": mode, "label": "Live run", "rehearsal": dry_pay, "status": "running",
               "started_at": agent.now(), "ended_at": None, "tasks": [], "summary": None}
        for t in tasks:
            run["tasks"].append({k: t.get(k) for k in ("kind", "group", "title", "balance_id", "claim_id")} |
                                {"task": t["task"], "steps": [], "status": "waiting", "summary": None})
        runs[run_id] = run
        tools = desk_tools(dry_pay)

        def work(rec: dict) -> None:
            try:
                agent.run_task(rec["task"], tools, ask=agent_ask, business=holder["name"], record=rec)
            except Exception as e:   # never leave a run hanging
                rec.update(status="error", summary=f"Stopped: {str(e)[:200]}")
            for s in rec["steps"]:
                store.audit("agent_step", claim_id=rec.get("claim_id") if rec.get("claim_id") in
                            {c["id"] for c in store.claims()} else None,
                            detail={"run": run_id, "say": s["say"], "tool": s["tool"], "args": s["args"],
                                    "outcome": s["outcome"], "result": s["result"], "at": s["started_at"]})

        def go() -> None:
            first = [r for r in run["tasks"] if r["group"] == "a"]
            with ThreadPoolExecutor(max_workers=4) as pool:   # the imported payouts are worked side by side
                list(pool.map(work, first))
            for rec in run["tasks"]:
                if rec["group"] != "a":
                    work(rec)
            run.update(status="done", ended_at=agent.now(), summary=agent.summarise(run["tasks"]))
            store.audit("agent_run", detail={"run": run_id, "mode": mode, "summary": run["summary"]})

        if not tasks:
            run.update(status="done", ended_at=agent.now(), summary=agent.summarise([]))
        else:
            threading.Thread(target=go, daemon=True).start()
        return run

    def load_saved_runs() -> dict:
        path = Path(saved_traces_path or SAVED_TRACES)
        try:
            return json.loads(path.read_text())
        except (OSError, ValueError):
            raise ApiError(404, "There is no saved run yet (scripts/save_agent_traces.py writes it).")

    @app.post("/api/agent/runs")
    def agent_start(body: AgentRunIn) -> dict:
        if public and not (agent_model_given or llm.available()):
            # No model on this public copy: answer with the saved run instead of a run that would only fail.
            saved_run = load_saved_runs().get(body.mode)
            if not isinstance(saved_run, dict):
                raise ApiError(404, "No model is connected to this public demo, and there is no saved run of "
                                    "that kind.")
            out = {**saved_run, "label": "Saved run", "status": "done", "note": PUBLIC_SAVED_RUN_NOTE}
            runs[out["id"]] = out
            return out
        if body.mode == "queue" and not body.dry_pay:
            need_money()
        if any(r["status"] == "running" for r in runs.values()):
            raise ApiError(409, "The desk agent is already working. Wait for this run to finish.")
        return start_run(body.mode, body.dry_pay)

    @app.get("/api/agent/runs/{run_id}")
    def agent_run(run_id: str) -> dict:
        if run_id not in runs:
            raise ApiError(404, f"There is no run {run_id}.")
        return runs[run_id]

    @app.get("/api/agent/saved")
    def agent_saved() -> dict:
        return load_saved_runs()

    @app.post("/api/claims/{claim_id}/payout-address")
    def payout_address(claim_id: str, body: AddressIn) -> dict:
        c = claim_or_404(claim_id)
        if c["status"] in ("REJECTED", "PAYING", "PAID"):
            raise ApiError(409, "This claim can no longer take a PayPal address.")
        if "@" not in body.receiver or "." not in body.receiver.split("@")[-1]:
            raise ApiError(400, "Give the email address of your PayPal account.")
        c = store.update_claim(claim_id, "payout_address_left", detail={"receiver_type": "EMAIL"},
                               payout_address=body.receiver.strip())
        return view(c)

    last_reset: list[float] = []
    reset_lock = threading.Lock()

    @app.post("/api/demo/reset")
    def demo_reset() -> dict:
        if public:
            with reset_lock:
                now = clock()
                if last_reset and now - last_reset[0] < PUBLIC_RESET_SECONDS:
                    raise ApiError(429, PUBLIC_RESET_TEXT)
                last_reset[:] = [now]
        n = load_ledger()
        return {"ok": True, "balances": n}

    @app.get("/api/{rest:path}", include_in_schema=False)
    def no_such_api(rest: str):
        raise ApiError(404, "There is nothing at this address.")

    if WEB_DIST.is_dir():
        index = WEB_DIST / "index.html"
        if (WEB_DIST / "assets").is_dir():
            app.mount("/assets", StaticFiles(directory=WEB_DIST / "assets"), name="assets")

        @app.get("/{rest:path}", include_in_schema=False)
        def front_end(rest: str):
            target = (WEB_DIST / rest).resolve()
            if rest and target.is_file() and WEB_DIST.resolve() in target.parents:
                return FileResponse(target)
            return FileResponse(index)

    return app


def main() -> None:
    import uvicorn
    uvicorn.run(create_app(), host=os.environ.get("HOMING_HOST", "127.0.0.1"),
                port=int(os.environ.get("HOMING_PORT", "8787")))


if __name__ == "__main__":
    main()
