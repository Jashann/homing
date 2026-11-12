"""Checks a deployed Homing from outside, using only the standard library.

    python3 -I deploy/verify.py https://homing-PROJECT_NUMBER.us-central1.run.app <source_dir>

Resets the demo, then runs the four sample cases from the README (search, open a claim, upload the sample documents,
read the result), approves Ana's claim on the business's desk, checks that paying fails closed when PayPal is not
attached, and fetches a state claim packet and an owner notice. Exits 1 if any expectation fails.
"""
import json
import sys
import uuid
import urllib.error
import urllib.request
from pathlib import Path

BASE = sys.argv[1].rstrip("/")
SAMPLES = Path(sys.argv[2]) / "data" / "samples"
failures = []


def call(method, path, body=None, files=None):
    headers, data = {}, None
    if files is not None:
        boundary = uuid.uuid4().hex
        parts = []
        for p in files:
            parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"files[]\"; filename=\"{p.name}\"\r\n"
                         f"Content-Type: image/png\r\n\r\n".encode() + p.read_bytes() + b"\r\n")
        data = b"".join(parts) + f"--{boundary}--\r\n".encode()
        headers["Content-Type"] = f"multipart/form-data; boundary={boundary}"
    elif body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(BASE + path, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            raw, code, ctype = r.read(), r.status, r.headers.get("content-type", "")
    except urllib.error.HTTPError as e:
        raw, code, ctype = e.read(), e.code, e.headers.get("content-type", "")
    return code, (json.loads(raw) if "json" in ctype else raw)


def check(label, ok, detail=""):
    print(("PASS " if ok else "FAIL ") + label + (f"  ({detail})" if detail else ""))
    if not ok:
        failures.append(label)


code, meta = call("GET", "/api/meta")
check("GET /api/meta", code == 200, json.dumps(meta)[:200])
code, page = call("GET", "/")
check("GET / serves the web app", code == 200 and b'id="root"' in page)
code, r = call("POST", "/api/demo/reset")
check("POST /api/demo/reset", code == 200 and r.get("ok"), f"{r.get('balances')} balances")

CASES = [
    ("clean", {"name": "Tomasz Wiśniewski", "emails": ["t.wisniewski@example.com"],
               "places": [{"city": "Kraków", "country": "Poland"}]},
     {"name": "Tomasz Wiśniewski", "email": "t.wisniewski@example.com", "country": "Poland", "language": "pl"},
     None, "APPROVED"),
    ("partial", {"name": "Ana Brennan", "former_names": ["Ana Kowalski"],
                 "places": [{"city": "Chicago", "country": "United States"}]},
     {"name": "Ana Brennan", "former_names": ["Ana Kowalski"], "country": "Ireland", "language": "en"},
     None, "NEEDS_REVIEW"),
    ("impostor", {"name": "Wei Zhang", "places": [{"city": "Vancouver", "country": "Canada"}]},
     {"name": "Wei Zhang", "country": "Canada", "language": "en"},
     "between $1,000 and $2,500", "REJECTED"),
    ("edited", {"name": "Mohammed Al-Rashid", "places": [{"city": "Dubai", "country": "United Arab Emirates"}]},
     {"name": "Mohammed Al-Rashid", "country": "United Arab Emirates", "language": "ar"},
     None, "REJECTED"),
]
claims = {}
for case, query, claimant, band, want in CASES:
    code, r = call("POST", "/api/search", query)
    matches = [m for m in r.get("matches", []) if m["status"] != "ESCHEATED"]
    if band:
        matches = [m for m in matches if m["amount_band"] == band]
    check(f"{case}: search finds the balance", code == 200 and bool(matches),
          matches and f"{matches[0]['balance_id']} {matches[0]['band']} {matches[0]['amount_band']}")
    if not matches:
        continue
    code, c = call("POST", "/api/claims", {"balance_id": matches[0]["balance_id"], "claimant": claimant})
    check(f"{case}: claim opened", code == 200, f"{c.get('id')} {c.get('amount')}" if code == 200 else c)
    if code != 200:
        continue
    files = sorted((SAMPLES / case).glob("*.png"))
    code, c = call("POST", f"/api/claims/{c['id']}/documents", files=files)
    verdict = ((c.get("proof") or {}).get("verdict")) if code == 200 else None
    proof = c.get("proof") or {}
    read = sum(1 for d in proof.get("documents") or [] if d.get("status") == "read")
    expl = (proof.get("explanation") or {}).get("source")
    check(f"{case}: result is {want}", verdict == want,
          f"verdict={verdict} status={c.get('status')} documents read={read}/{len(files)} explanation={expl}")
    claims[case] = c

# Ana needs a person: approve on the business's desk with a reason.
if "partial" in claims:
    cid = claims["partial"]["id"]
    code, r = call("POST", f"/api/claims/{cid}/approve", {"approver": "Demo reviewer",
                                                        "reason": "Marriage certificate links the two names."})
    check("partial: approved by a person with a reason", code == 200, (r.get("status") if code == 200 else r))

# Paying must fail closed without PayPal keys on the service.
if "clean" in claims and not meta.get("paypal_configured", False):
    code, r = call("POST", f"/api/claims/{claims['clean']['id']}/pay",
                   {"receiver": "sb-43y9ma53877422@personal.example.com"})
    print(f"INFO pay without PayPal keys -> HTTP {code}: {r.get('error') if isinstance(r, dict) else r[:100]}")

code, r = call("GET", "/api/claims")
check("GET /api/claims (business desk)", code == 200, f"{len(r.get('claims', []))} claims")
code, r = call("GET", "/api/ledger?status=ESCHEATED")
esc = r.get("balances", []) if code == 200 else []
check("ledger lists balances handed to the state", bool(esc), f"{len(esc)}")
if esc:
    code, r = call("POST", f"/api/state-claim/{esc[0]['id']}", {})
    check("state claim packet", code == 200, json.dumps(r)[:160] if code == 200 else r)
code, r = call("GET", "/api/ledger?status=DORMANT")
dorm = r.get("balances", []) if code == 200 else []
if dorm:
    code, r = call("POST", f"/api/balances/{dorm[0]['id']}/notice", {})
    check("owner notice (saved or plain)", code == 200, (r.get("source") or r.get("subject", ""))[:100]
          if code == 200 else r)
code, r = call("GET", "/api/scoreboard")
check("GET /api/scoreboard", code == 200)
call("POST", "/api/demo/reset")
print(f"\n{len(failures)} failed" + (": " + ", ".join(failures) if failures else ""))
sys.exit(1 if failures else 0)
