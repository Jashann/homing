"""Runs the real model over the bundled samples and saves what it wrote in data/saved_readings.json.

    uv run python scripts/save_readings.py            # needs the claude program signed in, or ANTHROPIC_API_KEY
    uv run python scripts/save_readings.py --keep-readings --keep-notices   # only the explanations again
    uv run python scripts/save_readings.py --keep-readings --keep-explanations   # only the notices again

What is saved, so the four sample cases work on a machine with no model:
- the reading of every document under data/samples/ (keyed by the file's SHA-256)
- the explanation of each sample case's result, in the case's language and in English (keyed by balance id and the
  check results; the partial case is saved both before and after the marriage certificate is added). A rejected
  claim gets no explanation: the claimant is told only that it could not be confirmed.
- the business's notice to every sample owner whose balance is still with the business (keyed by balance id)

The verdicts are not saved: code works them out again from the saved readings.
"""
from __future__ import annotations

import hashlib
import json
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fastapi.testclient import TestClient

from homing import assist, documents, llm
from homing.server import create_app
from homing.store import Store

ROOT = Path(__file__).resolve().parents[1]
SAMPLES = ROOT / "data" / "samples"
LEDGER = ROOT / "data" / "sample_ledger.json"

# (balance, claimant, language, document steps); each step is one upload
CASES = [
    ("B001", {"name": "Tomasz Wiśniewski", "email": "t.wisniewski@example.com", "country": "PL"}, "pl",
     [["clean/id_card.png", "clean/old_statement.png"]]),
    ("B002", {"name": "Ana Brennan", "email": "ana.brennan@example.com", "country": "IE",
              "former_names": ["Ana Kowalski"]}, "en",
     [["partial/id_card.png", "partial/utility_bill.png"], ["partial/marriage_certificate.png"]]),
    ("B003", {"name": "Wei Zhang", "email": "wei.zhang.van@example.com", "country": "CA"}, "zh",
     [["impostor/id_card.png"]]),
    ("B007", {"name": "Mohammed Al-Rashid", "email": "m.rashid.claims@example.com", "country": "AE"}, "ar",
     [["edited/id_card.png"]]),
    ("B006", {"name": "J. R. Okafor", "email": "jr.okafor@example.org", "country": "NG"}, "en",
     [["uncollected/id_card.png", "uncollected/old_statement.png"]]),
]


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    if not llm.available():
        sys.exit("No model: sign in the claude program or set ANTHROPIC_API_KEY.")
    saved = assist.Saved()
    files = sorted(SAMPLES.rglob("*.png"))
    keep_readings = "--keep-readings" in sys.argv     # keep the saved readings; read only files without one
    readings = {sha(p): saved.data["readings"][sha(p)] for p in files
                if keep_readings and sha(p) in saved.data["readings"]}

    def read(p: Path) -> None:
        r = documents.read_document(p, model=assist.MODEL)
        if r.get("status") != "read":
            sys.exit(f"{p} could not be read: {r}")
        readings[sha(p)] = r
        print(f"read {p.relative_to(SAMPLES)}: editing={r['signs_of_editing']} sample={r['marked_sample']}",
              file=sys.stderr)

    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(read, [p for p in files if sha(p) not in readings]))
    saved.data["readings"] = readings

    # the explanations: run each case through the server, reading from what was just saved
    tmp = Path(tempfile.mkdtemp())
    reader = lambda path, digest: {**json.loads(json.dumps(readings[digest])), "reading_source": "LIVE"}  # noqa: E731
    explanations: dict = {}
    keep_explanations = "--keep-explanations" in sys.argv and saved.data["explanations"]
    for balance_id, who, language, steps in ([] if keep_explanations else CASES):
        for lang in dict.fromkeys([language, "en"]):
            store = Store(tmp / f"{balance_id}-{lang}.sqlite")
            app = create_app(store=store, settlement=None, use_default_settlement=False, reader=reader, demo=True,
                             saved=assist.Saved(tmp / "none.json"), ledger_path=LEDGER, files_dir=tmp / "files")
            with TestClient(app) as http:
                c = http.post("/api/claims", json={"balance_id": balance_id,
                                                   "claimant": {**who, "language": lang}}).json()
                for step in steps:
                    up = [("files[]", (Path(k).name, (SAMPLES / k).read_bytes(), "image/png")) for k in step]
                    c = http.post(f"/api/claims/{c['id']}/documents", files=up).json()
                    if c["proof"]["verdict"] == "REJECTED":
                        print(f"{balance_id} {lang}: REJECTED, no explanation", file=sys.stderr)
                        continue
                    e = c["proof"]["explanation"]
                    if e["source"] != "LIVE":
                        sys.exit(f"the model did not write the explanation for {balance_id} ({lang})")
                    explanations.setdefault(balance_id, {}).setdefault(assist.signature(c["proof"]), {})[lang] = {
                        k: e[k] for k in ("language", "text", "english")}
                    print(f"{balance_id} {lang}: {c['proof']['verdict']}", file=sys.stderr)
            store.close()
    if keep_explanations:
        explanations = {b: {sig: v for sig, v in by.items() if not sig.startswith("REJECTED")}
                        for b, by in saved.data["explanations"].items()}
        explanations = {b: by for b, by in explanations.items() if by}
    saved.data["explanations"] = explanations

    ledger = json.loads(LEDGER.read_text())
    holder = {**ledger["holder"]}
    open_balances = [b for b in ledger["balances"] if b["status"] != "ESCHEATED"]
    notices = {}

    def draft(b: dict) -> None:
        n = assist.notice(b, holder=holder, saved=assist.Saved(tmp / "none.json"))
        notices[b["id"]] = {k: n[k] for k in ("language", "subject", "body", "english_subject", "english")}
        print(f"notice {b['id']} ({n['language']})", file=sys.stderr)

    if "--keep-notices" in sys.argv and saved.data["notices"]:
        notices = saved.data["notices"]
    else:
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(draft, open_balances))
    saved.data["notices"] = notices
    saved.save()
    print(f"wrote {saved.path}: {len(readings)} readings, {len(explanations)} cases, {len(notices)} notices",
          file=sys.stderr)


if __name__ == "__main__":
    main()
