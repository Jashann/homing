"""The check for one claim: what the claimant's documents show, compared field by field with the account record.

The model only reads (documents.py: what is printed on each file, its concerns, and whether it sees signs of
editing). Every comparison and the verdict are plain code here, using the helpers in compare.py:

    APPROVED      name, date of birth and old address all match, the documents are sound, and nothing looks
                  like an impostor
    REJECTED      a hard contradiction (a different date of birth, a different name, an edited document) or an
                  impostor pattern (the same document or the same receiving account on another claim)
    NEEDS_REVIEW  anything in between; a person at the business decides, and must write down why

A document stamped SAMPLE or SPECIMEN can never be a plain match: outside demo mode (HOMING_DEMO=1) it forces
NEEDS_REVIEW. In demo mode the bundled sample documents are accepted and the result says demo: true.

The record's own values are never copied into the result except in masked form.
"""
from __future__ import annotations

import os
import re
from datetime import date
from typing import Callable

from . import mask
from .compare import address_against_record, compare_names, compare_organisation, expired_dates
from .names import normalise

MATCH, PARTIAL, MISMATCH, MISSING = "MATCH", "PARTIAL", "MISMATCH", "MISSING"
APPROVED, NEEDS_REVIEW, REJECTED, NONE = "APPROVED", "NEEDS_REVIEW", "REJECTED", "NONE"

WEIGHTS = {"name": 0.35, "date_of_birth": 0.25, "address": 0.25, "email": 0.10, "document": 0.05}
CREDIT = {MATCH: 1.0, PARTIAL: 0.5, MISMATCH: 0.0, MISSING: 0.0}

_BIRTH = re.compile(r"birth|born|\bdob\b|date de naissance|geburt|nacimiento|urodzenia", re.I)
_ISO = re.compile(r"(\d{4})-(\d{2})-(\d{2})")
MAX_ATTEMPTS = 3   # document uploads a rejected claim may make


def demo_mode() -> bool:
    return os.environ.get("HOMING_DEMO", "").strip() == "1"


def empty_proof() -> dict:
    return {"verdict": NONE, "score": 0.0, "checks": [], "flags": [], "missing": [], "approved_by": None,
            "demo": False}


# ---- field checks -------------------------------------------------------------------------------------------

def _iso(value: str) -> str | None:
    m = _ISO.search(value or "")
    return m.group(0) if m else None


def _record_for_address(balance: dict) -> dict:
    # Regions are left out: a ledger writes "ON" where a document prints "Ontario".
    return {"owner_city": balance.get("last_city") or "", "owner_postcode": balance.get("last_postcode") or "",
            "owner_street": balance.get("last_street") or "", "owner_region": ""}


def _line(a: dict) -> str:
    return ", ".join(x for x in (a.get("street"), a.get("city"), a.get("postcode"), a.get("country")) if x)


def check_name(balance: dict, claimant: dict, read: list[dict]) -> dict:
    owner = balance["owner_name"]
    out = {"field": "name", "record_value_masked": mask.owner_display(balance) if balance.get("owner_type") ==
           "ORGANISATION" else mask.name_letters(owner), "document_value": "", "result": MISSING,
           "note": "No name could be read on the documents."}
    names = [(n, d) for d in read for n in d["reading"].get("names") or []]
    if balance.get("owner_type") == "ORGANISATION":
        texts = [n for n, _ in names] + [d["reading"].get("issuer") or "" for d in read]
        if compare_organisation(texts, owner):
            out.update(result=MATCH, document_value=owner, note="A document names the organisation on the account.")
        elif names:
            out.update(result=MISMATCH, document_value=names[0][0],
                       note="No document names the organisation on the account.")
        return out
    if not names:
        return out
    order = {"full": 2, "partial": 1, "none": 0}
    level, printed, doc = max(((compare_names(n, owner), n, d) for n, d in names), key=lambda x: order[x[0]])
    if level == "full":
        out.update(result=MATCH, document_value=printed,
                   note=f"The name on {doc['filename']} is the name on the account.")
        return out
    # A name change: a document shows both the account's name and the name the claimant uses now.
    current = claimant.get("name") or ""
    for d in read:
        ns = d["reading"].get("names") or []
        has_old = any(compare_names(n, owner) == "full" for n in ns)
        has_new = any(compare_names(n, current) == "full" for n in ns)
        if has_old and has_new:
            out.update(result=MATCH, document_value=" / ".join(ns[:2]),
                       note=f"{d['filename']} shows both the name on the account and the name used now.")
            return out
    uses_current = any(compare_names(n, current) == "full" for n, _ in names) if current else False
    former = [f for f in claimant.get("former_names") or [] if compare_names(f, owner) == "full"]
    if uses_current and (former or level == "partial"):
        out.update(result=PARTIAL, document_value=next(n for n, _ in names if compare_names(n, current) == "full"),
                   note="The documents show the name the claimant uses now"
                        + (f"; they say they were once {former[0]}" if former else "")
                        + ", but no document links it to the name on the account.")
    elif level == "partial":
        out.update(result=PARTIAL, document_value=printed, note="The name on the documents only partly matches.")
    else:
        out.update(result=MISMATCH, document_value=printed,
                   note="The name on the documents is not the name on the account.")
    return out


def check_date_of_birth(balance: dict, read: list[dict]) -> dict | None:
    if balance.get("owner_type") == "ORGANISATION" or not balance.get("date_of_birth"):
        return None
    out = {"field": "date_of_birth", "record_value_masked": mask.date_of_birth(balance["date_of_birth"]),
           "document_value": "", "result": MISSING, "note": "No document shows a date of birth."}
    seen = [(_iso(x.get("value") or ""), x.get("value"), d) for d in read for x in d["reading"].get("dates") or []
            if _BIRTH.search(x.get("label") or "")]
    seen = [s for s in seen if s[0]]
    if not seen:
        return out
    for iso, printed, d in seen:
        if iso == balance["date_of_birth"]:
            out.update(result=MATCH, document_value=printed,
                       note=f"The date of birth on {d['filename']} is the one on the account.")
            return out
    out.update(result=MISMATCH, document_value=seen[0][1],
               note=f"The date of birth on {seen[0][2]['filename']} is not the one on the account.")
    return out


def check_address(balance: dict, read: list[dict]) -> dict:
    out = {"field": "address", "record_value_masked": mask.address(balance), "document_value": "",
           "result": MISSING, "note": "No address could be read on the documents."}
    record = _record_for_address(balance)
    want_country = normalise(balance.get("last_country") or "")
    best, best_parts, best_doc = None, set(), None
    printed_any = None
    for d in read:
        for a in d["reading"].get("addresses") or []:
            if a.get("whose") == "issuer":
                continue
            printed_any = printed_any or (a, d)
            parts = address_against_record(a, record)
            country = normalise(a.get("country") or "")
            if country and want_country and country != want_country:
                parts = set()
            rank = (("street" in parts) * 4 + ("postcode" in parts) * 2 + ("city" in parts))
            if best is None or rank > (("street" in best_parts) * 4 + ("postcode" in best_parts) * 2
                                       + ("city" in best_parts)):
                best, best_parts, best_doc = a, parts, d
    if printed_any is None:
        return out
    if best_parts and ("street" in best_parts or {"postcode", "city"} <= best_parts):
        out.update(result=MATCH, document_value=_line(best),
                   note=f"{best_doc['filename']} shows the account's last address.")
    elif "city" in best_parts or "postcode" in best_parts:
        out.update(result=PARTIAL, document_value=_line(best),
                   note=f"{best_doc['filename']} shows an address in the account's last city, but not its street.")
    else:
        a, d = printed_any
        out.update(result=MISMATCH, document_value=_line(a),
                   note=f"The address on {d['filename']} is not the account's last address.")
    return out


def check_email(balance: dict, claimant: dict, read: list[dict]) -> dict:
    last = (balance.get("last_email") or "").lower()
    out = {"field": "email", "record_value_masked": mask.email(last), "document_value": "", "result": MISSING,
           "note": "No document shows the account's email."}
    printed = [(e, d) for d in read for e in d["reading"].get("emails") or []]
    for e, d in printed:
        if e.lower() == last:
            out.update(result=MATCH, document_value=e, note=f"{d['filename']} shows the account's last email.")
            return out
    if printed:
        e, d = printed[0]
        same_name = e.split("@")[0].lower() == last.split("@")[0]
        out.update(result=PARTIAL if same_name else MISMATCH, document_value=e,
                   note=("The email on the documents has the same name part as the account's, at another domain."
                         if same_name else "The email on the documents is not the account's email."))
        return out
    if (claimant.get("email") or "").lower() == last and last:
        out.update(result=MATCH, document_value=claimant["email"],
                   note="The claimant's contact email is the account's last email.")
    return out


def check_documents(docs: list[dict], today: date, demo: bool = False) -> tuple[dict, list[str]]:
    flags: list[str] = []
    notes: list[str] = []
    read = [d for d in docs if (d.get("reading") or {}).get("status") == "read"]
    result = MATCH if read else MISSING
    for d in docs:
        r = d.get("reading") or {}
        if r.get("status") != "read":
            notes.append(f"{d['filename']} could not be read.")
            result = PARTIAL if read else MISSING
            continue
        if not r.get("legible", True):
            notes.append(f"{d['filename']} is hard to read.")
            result = PARTIAL if result == MATCH else result
        if r.get("signs_of_editing"):
            flags.append(f"{d['filename']} shows signs of having been edited")
            result = MISMATCH
        for when in expired_dates(r, today):
            notes.append(f"{d['filename']} expired on {when}.")
            result = PARTIAL if result == MATCH else result
        if r.get("marked_sample") and not demo:
            notes.append(f"{d['filename']} is stamped as a sample or specimen, so it cannot prove a real claim.")
            result = PARTIAL if result == MATCH else result
    if not docs:
        note = "No documents have been added."
    else:
        note = f"{len(read)} of {len(docs)} {'document' if len(docs) == 1 else 'documents'} read." + (" " + " ".join(notes) if notes else "")
    value = ", ".join(sorted({(d.get('reading') or {}).get('document_type') or 'document' for d in read}))
    return {"field": "document", "record_value_masked": "", "document_value": value, "result": result,
            "note": note}, flags


# ---- impostor patterns --------------------------------------------------------------------------------------

DEFAULT_TEST_ACCOUNTS = "sb-43y9ma53877422@personal.example.com,sb-vgzm153858978@personal.example.com"


def test_receivers() -> list[str]:
    """PayPal sandbox personal accounts that demo owners may be paid to (HOMING_TEST_RECEIVERS, comma separated),
    in order. The sandbox gives a developer only a few such accounts, so the rule "one receiving account never
    collects two owners' money" is not applied to them. It applies to every other address."""
    raw = os.environ.get("HOMING_TEST_RECEIVERS", DEFAULT_TEST_ACCOUNTS)
    out: list[str] = []
    for a in raw.split(","):
        a = a.strip().lower()
        if a and a not in out:
            out.append(a)
    return out


def shared_test_accounts() -> set[str]:
    return set(test_receivers())


def impostor_flags(claim: dict, docs: list[dict], other_claims: list[dict],
                   docs_with_hash: Callable[[str], list[dict]]) -> list[str]:
    """Patterns that point to someone claiming money that is not theirs."""
    flags: list[str] = []
    for d in docs:
        others = sorted({o["claim_id"] for o in docs_with_hash(d["sha256"]) if o["claim_id"] != claim["id"]})
        if others:
            flags.append(f"the same document ({d['filename']}) was also used on claim {', '.join(others)}")
    accounts = {a.lower() for a in (claim.get("receiver"), (claim.get("claimant") or {}).get("email")) if a}
    accounts -= shared_test_accounts()
    for account in sorted(accounts):
        using = [o for o in other_claims if o["id"] != claim["id"] and o["balance_id"] != claim["balance_id"] and
                 account in {a.lower() for a in (o.get("receiver"), (o.get("claimant") or {}).get("email")) if a}]
        if using:
            flags.append(f"same account used on {len(using) + 1} claims for different balances")
            break
    return flags


# ---- the verdict --------------------------------------------------------------------------------------------

def assess(balance: dict, claim: dict, docs: list[dict], *, other_claims: list[dict] | None = None,
           docs_with_hash: Callable[[str], list[dict]] | None = None, today: date | None = None,
           demo: bool | None = None) -> dict:
    today = today or date.today()
    demo = demo_mode() if demo is None else demo
    claimant = claim.get("claimant") or {}
    read = [d for d in docs if (d.get("reading") or {}).get("status") == "read"]
    name = check_name(balance, claimant, read)
    dob = check_date_of_birth(balance, read)
    address = check_address(balance, read)
    email = check_email(balance, claimant, read)
    document, doc_flags = check_documents(docs, today, demo)
    checks = [c for c in (name, dob, address, email, document) if c]
    flags = doc_flags + impostor_flags(claim, docs, other_claims or [], docs_with_hash or (lambda h: []))

    total = sum(WEIGHTS[c["field"]] for c in checks)
    score = round(sum(WEIGHTS[c["field"]] * CREDIT[c["result"]] for c in checks) / total, 2) if total else 0.0

    missing: list[str] = []
    if not docs:
        verdict = NONE
    else:
        hard = [c for c in checks if c["result"] == MISMATCH and c["field"] in ("name", "date_of_birth", "document")]
        for c in hard:
            if c["field"] == "name":
                flags.append("the name on the documents is not the name on the account")
            elif c["field"] == "date_of_birth":
                flags.append("the date of birth on the documents is not the one on the account")
        if flags or hard:
            verdict = REJECTED
            score = min(score, 0.3)
        elif (name["result"] == MATCH and (dob is None or dob["result"] == MATCH) and address["result"] == MATCH
              and document["result"] == MATCH):
            verdict = APPROVED
        else:
            verdict = NEEDS_REVIEW
        if verdict == NEEDS_REVIEW:   # a rejected claim is never told what would get it through
            if name["result"] == PARTIAL:
                missing.append("a document showing both names, such as a marriage certificate")
            elif name["result"] == MISSING:
                missing.append("an identity document showing the name on the account")
            if dob is not None and dob["result"] == MISSING:
                missing.append("an identity document showing the date of birth")
            if address["result"] in (MISSING, MISMATCH):
                missing.append(f"a document showing the old address in {balance.get('last_city') or 'the city on the account'}")
            elif address["result"] == PARTIAL:
                missing.append(f"a document showing the old street address in {balance.get('last_city')}")
            if document["result"] in (PARTIAL, MISSING):
                missing.append("a clear, complete and current copy of each document")
    samples = any((d.get("reading") or {}).get("marked_sample") for d in read)
    return {"verdict": verdict, "score": score, "checks": checks, "flags": flags, "missing": missing,
            "approved_by": None, "demo": bool(demo and samples)}
