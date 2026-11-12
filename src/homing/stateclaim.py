"""For a balance the business already handed to the State of California: a claim packet (packet.py) prepared for the
owner to review, sign and send. Nothing is filed.

The packet carries the record in its public form (a private owner's name as first letters only, no street) and
marks it as a sample record, because the sample ledger is invented.
"""
from __future__ import annotations

from . import california, mask, packet


def public_record(balance: dict) -> dict:
    esc = balance.get("escheat") or {}
    org = balance.get("owner_type") == "ORGANISATION"
    return {"source": "SAMPLE", "record_id": esc.get("record_id") or f"SAMPLE-{balance['id']}",
            "amount": float(balance["amount"]), "currency": balance["currency"],
            "holder_name": esc.get("holder_name") or "",
            "owner": balance["owner_name"] if org else mask.name_letters(balance["owner_name"]),
            "owner_city": balance.get("last_city") or "", "owner_region": balance.get("last_region") or "",
            "owner_country": balance.get("last_country") or "",
            "property_type": esc.get("property_type") or "Account balance", "owner_is_organisation": org}


def checklist(claim: dict) -> dict:
    """What California asks for in this claim's case (the shape packet.build expects)."""
    record, claimant = claim["record"], claim["claimant"]
    case = california.requirements(amount=float(record.get("amount") or 0),
                                   claimant_country=claimant.get("country") or "",
                                   owner_is_organisation=bool(record.get("owner_is_organisation")),
                                   property_type=record.get("property_type") or "")
    items = [{**need, "status": "still needed", "files": []} for need in case["documents"]]
    required = [i for i in items if i["required"]]
    return {"items": items, "required": len(required), "attached": 0, "complete": not required,
            "missing": [i["title"] for i in required], "notary": case["notary"], "signing": case["signing"],
            "payment": case["payment"], "mail_to": case["mail_to"], "sources": case["sources"]}


def build(balance: dict, claimant: dict, packet_id: str) -> tuple[bytes, list[str]]:
    """(PDF bytes, the steps in order) for one escheated balance."""
    claim = {"id": packet_id, "record": public_record(balance),
             "claimant": {"name": claimant.get("name") or balance["owner_name"],
                          "address": claimant.get("address") or "",
                          "country": (claimant.get("country") or balance.get("last_country") or "US").upper(),
                          "email": claimant.get("email") or ""}}
    need = checklist(claim)
    pdf = packet.build(claim, need)
    steps = [s["title"] for s in need["signing"]]
    mail = "Mail it to: " + ", ".join(need["mail_to"])
    wait = next((i for i, t in enumerate(steps) if t.lower().startswith("wait")), None)
    steps.insert(wait, mail) if wait is not None else steps.append(mail)
    return pdf, steps
