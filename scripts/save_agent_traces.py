"""Saves one run of the desk agent over the demo state, so the Desk agent tab can show it with no model and no keys.

    uv run python scripts/save_agent_traces.py

It builds the demo state in a temporary copy (the sample ledger, the payouts imported live from PayPal, Ana's claim
approved by a person with her PayPal address, Mohammed's edited card turned down), then runs the agent with the real
model and the real PayPal lookups. The pay tool is stubbed in this script only: the code gate still runs, but no
payment is sent. Writes data/saved_agent_traces.json; the interface labels it "Saved run".
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from fastapi.testclient import TestClient  # noqa: E402

from homing import assist, llm  # noqa: E402
from homing.server import create_app, default_settlement  # noqa: E402
from homing.store import Store  # noqa: E402

OUT = ROOT / "data" / "saved_agent_traces.json"
SAMPLES = ROOT / "data" / "samples"
REASON = "Marriage certificate registered in Chicago links Kowalski to Brennan, and the date of birth matches."


def main() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="homing-traces-"))
    os.environ["HOMING_SETTLEMENT_DB"] = str(tmp / "settlement.sqlite")   # no change to the real payout records
    money = default_settlement()
    if money is None:
        sys.exit("PayPal sandbox keys are needed for the live PayPal lookups (~/.paypal_sandbox).")
    saved = assist.Saved()

    def saved_only(path):   # the sample documents' saved readings: fast, and the same every time
        raise llm.ModelUnavailable("saved readings")

    app = create_app(store=Store(tmp / "homing.sqlite"), settlement=money, demo=True, saved=saved,
                     reader=assist.make_reader(saved, live=saved_only), files_dir=tmp / "files")
    with TestClient(app) as http:
        def post(path, **kw):
            r = http.post(path, **kw)
            r.raise_for_status()
            return r.json()

        post("/api/demo/reset")
        post("/api/import/paypal")
        ana = post("/api/claims", json={"balance_id": "B002", "claimant": {
            "name": "Ana Brennan", "email": "ana.brennan@example.com", "country": "IE", "language": "en",
            "former_names": ["Ana Kowalski"]}})
        mo = post("/api/claims", json={"balance_id": "B007", "claimant": {
            "name": "Mohammed Al-Rashid", "email": "m.alrashid@example.com", "country": "AE", "language": "ar"}})
        for c, files in ((ana, ["partial/id_card.png", "partial/utility_bill.png", "partial/marriage_certificate.png"]),
                         (mo, ["edited/id_card.png"])):
            post(f"/api/claims/{c['id']}/documents",
                 files=[("files[]", (f.split("/")[1], (SAMPLES / f).read_bytes(), "image/png")) for f in files])
        post(f"/api/claims/{ana['id']}/payout-address", json={"receiver": "sb-vgzm153858978@personal.example.com"})
        post(f"/api/claims/{ana['id']}/approve", json={"approver": "Larkfield claims desk", "reason": REASON})

        out = {"label": "Saved run", "saved_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
               "note": "Recorded with the real model and live PayPal lookups; the pay tool was stubbed, so no money "
                       "was sent in this run."}
        for mode in ("queue", "brakes"):
            run = post("/api/agent/runs", json={"mode": mode, "dry_pay": True})
            while run["status"] != "done":
                time.sleep(1)
                run = http.get(f"/api/agent/runs/{run['id']}").json()
            run["label"] = "Saved run"
            out[mode] = run
            print(mode, json.dumps(run["summary"]))
    OUT.write_text(json.dumps(out, indent=1, ensure_ascii=False) + "\n")
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
