"""Counts what really happened in the PayPal sandbox, from the local settlement database and the seeded history.

    uv run python scripts/live_tally.py                      # data/settlement.sqlite
    uv run python scripts/live_tally.py path/to/settlement.sqlite

The README's paragraph on what was exercised live uses exactly these numbers. Payout receivers that collected are
named (they are PayPal sandbox test accounts); made-up addresses that never collected are only counted.
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    db = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "data" / "settlement.sqlite"
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    one = lambda q, *a: con.execute(q, a).fetchone()[0]  # noqa: E731

    first, last = con.execute("select min(at), max(at) from audit").fetchone()
    sent = one("select count(*) from audit where action='send'")
    refused = con.execute("select paypal_status, count(*) from audit where action='send_error' group by 1").fetchall()
    collected = con.execute("select receiver, count(*) from attempts where final_state='CLAIMED' "
                            "group by 1 order by 2 desc").fetchall()
    uncollected = one("select count(*) from claims where state='UNCLAIMED'")
    returned = one("select count(*) from claims where state in ('RETURNED', 'CANCELLED')")
    in_flight = one("select count(*) from claims where state='SENT'")
    cancels = con.execute("select paypal_status, count(*) from audit where action='cancel_error' group by 1").fetchall()
    cancelled = one("select count(*) from audit where action='cancel' and to_state='CANCELLED'")
    resent = one("select count(*) from attempts where attempt > 1 and payout_batch_id is not null")
    inv_made = one("select count(*) from audit where action='invoice_create'")
    inv_sent = one("select count(*) from audit where action='invoice_send'")
    inv_paid = one("select count(*) from claims where invoice_status='PAID'")
    hooks = con.execute("select event_type, outcome, count(*) from webhook_events group by 1, 2 order by 1, 2"
                        ).fetchall()

    print(f"Settlement database: {db.relative_to(ROOT) if db.is_relative_to(ROOT) else db}")
    print(f"First and last entry: {first} to {last}")
    print(f"Payouts sent: {sent}")
    for status, n in refused:
        print(f"  refused by PayPal before sending ({status}): {n}")
    print(f"Payouts collected (PayPal reported SUCCESS): {sum(n for _, n in collected)}")
    for receiver, n in collected:
        print(f"  {receiver}: {n}")
    print(f"Payouts uncollected (PayPal reports UNCLAIMED): {uncollected}")
    print(f"Payouts returned or cancelled: {returned}")
    print(f"Payouts still in flight: {in_flight}")
    print(f"Cancel requests for an uncollected payout refused by PayPal: {sum(n for _, n in cancels)}"
          + (" (" + ", ".join(f"{s} {n}" for s, n in cancels) + ")" if cancels else ""))
    print(f"Cancel requests PayPal accepted: {cancelled}; payouts sent again to a new contact: {resent}")
    print(f"Fee invoices created: {inv_made}; sent: {inv_sent}; paid: {inv_paid}")
    total = sum(n for _, _, n in hooks)
    print(f"Webhook deliveries verified with PayPal and recorded: {total}")
    for etype, outcome, n in hooks:
        why = {"applied": "changed a claim", "ignored": "noted, no change",
               "unknown_claim": "not one of this server's claims", "duplicate": "repeat",
               "stale_attempt": "earlier attempt"}.get(outcome, outcome)
        print(f"  {etype}: {n} ({why})")

    history = ROOT / "data" / "payout_history.json"
    if history.exists():
        seeded = json.loads(history.read_text()).get("payouts") or []
        states = ", ".join(f"{p.get('payout_item_id')} {p.get('status_seen')}" for p in seeded)
        print(f"Seeded seller payouts in data/payout_history.json (sent outside Homing's claims): {len(seeded)}"
              + (f" ({states})" if states else ""))


if __name__ == "__main__":
    main()
