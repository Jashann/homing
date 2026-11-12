"""Shared test data for the Homing tests: the sample ledger, the four sample cases' documents, and what a model
reading each of them returns (so no test calls a model)."""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _script(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def ledger() -> list[dict]:
    return _script("make_ledger").build()["balances"]


def write_ledger(path: Path) -> Path:
    path.write_text(json.dumps(_script("make_ledger").build(), ensure_ascii=False))
    return path


def make_samples(out: Path) -> dict[str, Path]:
    _script("make_samples").build(out)
    return {f"{p.parent.name}/{p.name}": p for p in out.rglob("*.png")}


def _r(names, *, dates=(), addresses=(), emails=(), issuer="", document_type="document", **extra) -> dict:
    return {"status": "read", "document_type": document_type, "names": list(names), "addresses": list(addresses),
            "dates": list(dates), "issuer": issuer, "identifiers_present": [], "legible": True, "concerns": [],
            "marked_sample": True, "signs_of_editing": False, "summary": "", "emails": list(emails), **extra}


def _a(street, city, postcode, country, region="", whose="person"):
    return {"street": street, "city": city, "region": region, "postcode": postcode, "country": country,
            "whose": whose}


READINGS = {
    "clean/id_card.png": _r(["Tomasz Wiśniewski"], document_type="identity card", issuer="Demo Identity Office",
                            dates=[{"label": "Date of birth", "value": "1979-03-14"},
                                   {"label": "Expires", "value": "2031-05-01"}]),
    "clean/old_statement.png": _r(["Tomasz Wiśniewski"], document_type="bank statement",
                                  issuer="Vistula Demo Savings", emails=["t.wisniewski@example.com"],
                                  dates=[{"label": "Statement date", "value": "2019-05-31"}],
                                  addresses=[_a("ul. Długa 12", "Kraków", "31-147", "PL"),
                                             _a("Rynek Główny 1", "Kraków", "31-042", "PL", whose="issuer")]),
    "partial/id_card.png": _r(["Ana Brennan"], document_type="identity card", issuer="Demo Identity Office",
                              dates=[{"label": "Date of birth", "value": "1988-11-02"},
                                     {"label": "Expires", "value": "2030-08-19"}]),
    "partial/utility_bill.png": _r(["Ana Brennan"], document_type="electricity bill", issuer="Liffey Demo Energy",
                                   dates=[{"label": "Bill date", "value": "2026-10-03"}],
                                   addresses=[_a("14 Pembroke Road", "Dublin 4", "D04 X2R7", "IE")]),
    "partial/marriage_certificate.png": _r(["Ana Kowalski", "Liam Brennan", "Ana Brennan"],
                                           document_type="certificate of marriage", issuer="Demo Civil Registry",
                                           dates=[{"label": "Date of marriage", "value": "2019-06-15"},
                                                  {"label": "Registered", "value": "2019-06-17"}],
                                           addresses=[_a("", "Chicago", "", "US", region="IL", whose="other")]),
    "edited/id_card.png": _r(["Mohammed Al-Rashid"], document_type="identity card", issuer="Demo Identity Office",
                             dates=[{"label": "Date of birth", "value": "1981-02-25"},
                                    {"label": "Expires", "value": "2031-09-30"}],
                             addresses=[_a("Al Wasl Rd 220", "Dubai", "", "AE")], signs_of_editing=True,
                             concerns=["The name is in a different typeface from the rest of the card, on a patch "
                                       "that is slightly out of line."]),
    "uncollected/id_card.png": _r(["J. R. Okafor"], document_type="identity card", issuer="Demo Identity Office",
                                  dates=[{"label": "Date of birth", "value": "1976-09-12"},
                                         {"label": "Expires", "value": "2032-04-30"}]),
    "uncollected/old_statement.png": _r(["J. R. Okafor"], document_type="bank statement", issuer="Lagoon Demo Bank",
                                        emails=["jr.okafor@example.org"],
                                        dates=[{"label": "Statement date", "value": "2016-06-30"}],
                                        addresses=[_a("14 Awolowo Rd", "Lagos", "101233", "NG"),
                                                   _a("2 Marina Road", "Lagos", "", "NG", whose="issuer")]),
    "impostor/id_card.png": _r(["Wei Zhang"], document_type="identity card", issuer="Demo Identity Office",
                               dates=[{"label": "Date of birth", "value": "1992-01-09"},
                                      {"label": "Expires", "value": "2032-02-11"}],
                               addresses=[_a("1450 W 7th Ave", "Vancouver", "V6H 1C1", "CA", region="BC")]),
}


class FakeReader:
    """Stands in for the model: returns the stored reading for a known sample file, recognised by its bytes."""

    def __init__(self, samples: dict[str, Path]):
        self.by_hash = {hashlib.sha256(p.read_bytes()).hexdigest(): READINGS[key] for key, p in samples.items()}
        self.calls = 0

    def __call__(self, path, sha256=None):
        self.calls += 1
        reading = self.by_hash.get(hashlib.sha256(Path(path).read_bytes()).hexdigest())
        reading = json.loads(json.dumps(reading)) if reading else {"status": "unreadable", "legible": False}
        reading["reading_source"] = "LIVE"
        return reading


class FakeModel:
    """Stands in for the model's writing (explanations and notices). Answers in the language the prompt asks for."""

    def __init__(self):
        self.prompts: list[str] = []

    def __call__(self, prompt: str) -> dict:
        self.prompts.append(prompt)
        import re
        lang = re.search(r"\bin (\w+(?: \(\w+\))?)[.,]", prompt.split("Rules:")[-1])
        lang = lang.group(1) if lang else "English"
        if '"subject"' in prompt:
            return {"subject": f"[{lang}] Your earnings are waiting", "body": f"[{lang}] body",
                    "english_subject": "Your earnings are waiting", "english": "English body"}
        return {"text": f"[{lang}] explanation", "english": "English explanation"}


def sample_hashes(samples: dict[str, Path]) -> dict[str, str]:
    return {key: hashlib.sha256(p.read_bytes()).hexdigest() for key, p in samples.items()}
