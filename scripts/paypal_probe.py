"""Run the live PayPal sandbox path and print ids and statuses (never credentials).

    uv run python scripts/paypal_probe.py                # payout to a made-up address, cancel, fee invoice
    uv run python scripts/paypal_probe.py --to PHONE:4085550123   # pay a sandbox phone instead
"""
from __future__ import annotations

import argparse
import tempfile
import time
import uuid
from pathlib import Path

from homing.paypal import LineItem, PayPalClient, PayPalError
from homing.settlement import SENT, UNCLAIMED, Settlement, compute_fee


def stamp(t0: float) -> str:
    return f"[{time.time() - t0:5.1f}s]"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--to", default="", help="EMAIL:addr or PHONE:number (default: a made-up @example.com)")
    ap.add_argument("--amount", default="1.00")
    ap.add_argument("--currency", default="USD")
    ap.add_argument("--db", default=None, help="settlement sqlite (default: a temp file)")
    ap.add_argument("--no-cancel", action="store_true")
    args = ap.parse_args()

    rtype, receiver = "EMAIL", f"owner-{uuid.uuid4().hex[:8]}@example.com"
    if args.to:
        rtype, receiver = args.to.split(":", 1)
    holder = f"holder-{uuid.uuid4().hex[:6]}@example.com"
    db = Path(args.db) if args.db else Path(tempfile.mkdtemp()) / "probe.sqlite"

    t0 = time.time()
    client = PayPalClient()
    s = Settlement(client, db, holder_email=holder, prefix="homing-probe")
    cid = f"P{uuid.uuid4().hex[:10]}"
    s.create_claim(cid, receiver=receiver, receiver_type=rtype, amount=args.amount, currency=args.currency,
                   note="Probe reunion payout")
    c = s.send(cid)
    print(stamp(t0), "claim", cid, "state", c.state, "batch", c.payout_batch_id, "sender_batch_id", c.sender_batch_id)
    c2 = s.send(cid)
    print(stamp(t0), "send again ->", c2.state, "same batch:", c2.payout_batch_id == c.payout_batch_id)

    while c.state == SENT and time.time() - t0 < 180:
        time.sleep(3)
        c = s.refresh(cid)
        print(stamp(t0), "item", c.payout_item_id, "paypal", c.paypal_status, "state", c.state)
    item = client.get_payout_item(c.payout_item_id) if c.payout_item_id else None
    if item:
        print(stamp(t0), "item status", item.status, "error", item.error_name, "fee", item.fee, item.fee_currency)
        conv = item.raw.get("currency_conversion")
        if conv:
            print(stamp(t0), "funded by conversion", conv["from_amount"], "->", conv["to_amount"])
    print(stamp(t0), "needs new contact:", [x.claim_id for x in s.needs_contact()])

    if c.state == UNCLAIMED and not args.no_cancel:
        while client.get_payout_batch(c.payout_batch_id).status != "SUCCESS" and time.time() - t0 < 240:
            time.sleep(3)
        print(stamp(t0), "batch", client.get_payout_batch(c.payout_batch_id).status)
        try:
            c = s.cancel(cid)
            print(stamp(t0), "cancel ->", c.state, c.paypal_status)
        except PayPalError as e:
            print(stamp(t0), "cancel failed:", e.status, e.name, "debug_id", e.debug_id)

    if c.state == "CLAIMED":
        print(stamp(t0), "invoice", c.invoice_id, c.invoice_status, "fee", c.fee, c.invoice_url)
    else:
        fee = compute_fee(args.amount)
        inv = client.create_invoice(recipient_email=holder, items=[LineItem("Owner found and paid (probe)", fee)],
                                    invoice_number=f"HOMING-PROBE-{cid}", currency=args.currency)
        link = client.send_invoice(inv.invoice_id)
        got = client.get_invoice(inv.invoice_id)
        print(stamp(t0), "fee invoice", inv.invoice_id, inv.number, "fee", fee, "status", got.status, "link", link)

    print(stamp(t0), "audit:")
    for a in s.audit(cid):
        print("   ", a["action"], a["from_state"], "->", a["to_state"], a["paypal_status"] or "",
              a["payout_item_id"] or "", a["invoice_id"] or "")
    print(stamp(t0), "token fetches:", client.token_fetches, "db:", db)


if __name__ == "__main__":
    main()
