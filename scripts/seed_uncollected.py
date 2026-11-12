"""Gives Larkfield Market a real PayPal history of uncollected seller payouts, in the PayPal sandbox.

    uv run python scripts/seed_uncollected.py

From the business's own sandbox app (~/.paypal_sandbox, the account that pays Larkfield's sellers) it sends three
small payouts, $18.50, $27.30 and $42.00, to three made-up seller addresses at example.com. Nobody owns those
addresses, so nobody can collect the money, and PayPal holds each payout as UNCLAIMED until it returns it to the
sender 30 days later. That is the way seller earnings go uncollected at a marketplace that pays through PayPal.

The batch ids go to data/payout_history.json with the sellers' display names (invented). POST /api/import/paypal
reads that file, asks PayPal for each batch, and adds every UNCLAIMED or RETURNED item to the dormant balances.

Run it once. A second run sends nothing: payouts already in the file are only checked again, and each payout has a
fixed sender_batch_id, so PayPal would refuse a repeat anyway. Credentials are never printed.
"""
from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from homing.paypal import DuplicateBatchError, PayPalClient  # noqa: E402

OUT = ROOT / "data" / "payout_history.json"
WAIT_S = 900

# Invented sellers. The addresses are at example.com, which is reserved and has no PayPal accounts.
SELLERS = [
    {"seller": "Elena Marchetti", "city": "Bologna", "country": "IT",
     "receiver": "elena.marchetti.ceramics@example.com", "amount": "18.50"},
    {"seller": "Samuel Owusu", "city": "Kumasi", "country": "GH",
     "receiver": "samuel.owusu.prints@example.com", "amount": "27.30"},
    {"seller": "Freya Lindahl", "city": "Malmö", "country": "SE",
     "receiver": "freya.lindahl.knits@example.com", "amount": "42.00"},
]


def load() -> dict:
    if OUT.exists():
        return json.loads(OUT.read_text())
    return {"note": "Seller payouts that Larkfield Market (invented) sent from its PayPal sandbox account to made-up "
                    "seller addresses at example.com. Nobody can collect them, so PayPal holds them as UNCLAIMED. "
                    "Written by scripts/seed_uncollected.py; read by POST /api/import/paypal.",
            "business": "Larkfield Market", "payouts": []}


def save(history: dict) -> None:
    OUT.write_text(json.dumps(history, indent=2, ensure_ascii=False) + "\n")


def main() -> None:
    client = PayPalClient()
    history = load()
    have = {p["sender_batch_id"]: p for p in history["payouts"]}
    for i, s in enumerate(SELLERS, 1):
        sbid = f"larkfield-sellers-2026-11-12-{i}"
        if sbid in have:
            print(f"{s['seller']}: already sent (batch {have[sbid]['payout_batch_id']}), nothing sent")
            continue
        note = "Your seller earnings from Larkfield Market."
        try:
            batch = client.create_payout(receiver=s["receiver"], amount=s["amount"], currency="USD",
                                         sender_batch_id=sbid, note=note,
                                         email_subject="You have a payout from Larkfield Market",
                                         email_message=note)
            batch_id = batch.batch_id
        except DuplicateBatchError as e:   # sent before but not written down: adopt it, send nothing
            batch_id = e.existing_batch_id
            if not batch_id:
                raise
        entry = {**s, "currency": "USD", "sender_batch_id": sbid, "payout_batch_id": batch_id,
                 "sent_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
        history["payouts"].append(entry)
        have[sbid] = entry
        save(history)
        print(f"{s['seller']}: sent ${s['amount']} to {s['receiver']}, PayPal batch {batch_id}")

    end = time.time() + WAIT_S
    while True:
        states = {}
        for p in history["payouts"]:
            b = client.get_payout_batch(p["payout_batch_id"])
            item = b.items[0] if b.items else None
            states[p["payout_batch_id"]] = item.status if item else b.status
            if item:
                p["payout_item_id"] = item.item_id
                p["status_seen"] = item.status
        save(history)
        print("  " + ", ".join(f"{k}: {v}" for k, v in states.items()), flush=True)
        if all(v in ("UNCLAIMED", "RETURNED") for v in states.values()):
            print("PayPal reports every seeded payout as uncollected.")
            return
        if time.time() > end:
            sys.exit("PayPal has not reported every payout as UNCLAIMED yet; run the script again later "
                     "(it sends nothing new).")
        time.sleep(15)


if __name__ == "__main__":
    main()
