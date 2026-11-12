"""Finds a person among the business's dormant balances, with a match strength and the reasons for it.

The strength (0 to 100) is a transparent heuristic: hand-set points for each thing that fits or does not, every one
named in the reasons and doubts. It is not a measured probability. "probability" (strength / 100) stays in the
output for older clients.

The name scoring is match.score (names respelled, reordered, initials, married names); this module tries every
way the typed names can be read (which word is the family name), adds what a payment ledger knows that a state
register does not (the last email), and holds back any balance whose name another balance shares in a different
city unless something the person gave ties it to them.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from rapidfuzz import fuzz

from . import mask
from .match import History, Place, score
from .names import PARTICLES, Person, normalise, phonetic, tokens

FLOOR = 0.35          # below this a balance is not shown at all
TWIN_CAP = 0.60       # a shared name with nothing tying it to the person never goes above this
SURE = 0.85
POSSIBLE = 0.50


def band(p: float) -> str:
    return "VERY_LIKELY" if p >= SURE else "POSSIBLE" if p >= POSSIBLE else "UNLIKELY"


@dataclass
class Query:
    name: str
    former_names: list[str] = field(default_factory=list)
    emails: list[str] = field(default_factory=list)
    places: list[dict] = field(default_factory=list)   # {"city", "country"}


def readings(full_name: str) -> list[tuple[Person, str]]:
    """Every sensible split of one typed name into given and family names."""
    toks = tokens(full_name)
    if not toks:
        return []
    if len(toks) == 1:
        return [(Person(given=[], family=toks), full_name)]
    out = [(Person(given=toks[:-1], family=[toks[-1]]), full_name),     # Western order
           (Person(given=toks[1:], family=[toks[0]]), full_name)]      # family name first
    if len(toks) >= 3:   # a two-word family name: "Ben Ali", "Garcia Lopez", "Oliveira Santos"
        out.append((Person(given=toks[:-2], family=[" ".join(toks[-2:])]), full_name))
    return out


def as_record(balance: dict) -> dict:
    """A ledger balance in the shape match.score reads."""
    return {"owner_name": balance["owner_name"], "owner_norm": normalise(balance["owner_name"]),
            "owner_street": balance.get("last_street") or "", "owner_city": balance.get("last_city") or "",
            "owner_region": "", "owner_postcode": balance.get("last_postcode") or "",
            "owner_country": balance.get("last_country") or "", "holder_name": "", "amount": float(balance["amount"])}


def _same_given(given: list[str], otoks: list[str]) -> tuple[str, str] | None:
    """A given name written another way across an alphabet: Muhammad / Mohammed, Yusuf / Youssef."""
    for g in given:
        for t in otoks:
            if len(g) > 2 and len(t) > 2 and g[0] == t[0] and (phonetic(g) == phonetic(t) or fuzz.ratio(g, t) >= 70):
                return g, t
    return None


_EXTRA = re.compile(r"^the record has other names \((.*)\)$")


def _refund_extras(s: float, doubts: list[str], explained: set[str]) -> tuple[float, list[str]]:
    """match.score takes 0.06 off for each unexplained word in the record (up to three); give it back for words
    this module has explained."""
    out = []
    for d in doubts:
        m = _EXTRA.match(d)
        if not m:
            out.append(d)
            continue
        extra = [w.strip() for w in m.group(1).split(",")]
        left = [w for w in extra if w not in explained]
        s += 0.06 * (min(len(extra), 3) - min(len(left), 3))
        if left:
            out.append(f"the record has other names ({', '.join(left)})")
    return s, out


def _score_one(balance: dict, q: Query) -> dict | None:
    record = as_record(balance)
    places = [Place(city=p.get("city") or "", country=p.get("country") or "") for p in q.places]
    names = [(q.name, False)] + [(n, True) for n in q.former_names if n.strip()]
    best = None
    for typed, former in names:
        for person, label in readings(typed):
            m = score(record, History(person=person, places=places))
            s, reasons, doubts = m.score, list(m.reasons), list(m.doubts)
            otoks = tokens(record["owner_norm"])
            explained = {t for t in otoks if t in PARTICLES}   # "Al", "Van", "De" are not other names
            if "no given name in the record" in doubts:
                same = _same_given(person.given_keys(), otoks)
                if same:
                    s += 0.20
                    explained.add(same[1])
                    doubts.remove("no given name in the record")
                    reasons.append(f"given name is another spelling of the same name ({same[1].title()} and "
                                   f"{same[0].title()})")
            s, doubts = _refund_extras(s, doubts, explained)
            if former and s > 0:
                reasons.append(f"matches a former name they gave ({label})")
            if best is None or s > best[0]:
                best = (s, reasons, doubts)
    s, reasons, doubts = best
    if s <= 0.15:
        return None

    tied = any("street" in r or "postcode" in r for r in reasons)
    last_email = (balance.get("last_email") or "").lower()
    given_emails = [e.strip().lower() for e in q.emails if e and e.strip()]
    if last_email and last_email in given_emails:
        s += 0.35
        tied = True
        reasons.append("an email they gave is the account's last email")
    elif last_email and any(e.split("@")[0] == last_email.split("@")[0] for e in given_emails):
        s += 0.08
        reasons.append("an email they gave has the same name part as the account's last email")
    elif given_emails:
        doubts.append("none of the emails they gave is the account's last email")

    city_hit = any("city" in r for r in reasons)
    countries = {normalise(p.get("country") or "") for p in q.places if p.get("country")}
    if not city_hit and normalise(balance.get("last_country") or "") in countries:
        s += 0.06
        reasons.append(f"they lived in the account's last country ({balance.get('last_country')})")
        doubts = [d for d in doubts if "nowhere they mentioned" not in d]
    return {"score": s, "reasons": reasons, "doubts": doubts, "tied": tied, "city_hit": city_hit}


def _score_org(balance: dict, q: Query) -> dict | None:
    """An organisation's balance: the whole name is compared, word order and small words aside."""
    want, have = normalise(q.name), normalise(balance["owner_name"])
    ratio = max(fuzz.token_set_ratio(want, have), fuzz.token_sort_ratio(want, have))
    if ratio < 85:
        return None
    s = 0.55 if ratio >= 95 else 0.40
    reasons = ["the organisation's name matches" + ("" if ratio >= 95 else " with small differences")]
    doubts: list[str] = []
    city_hit = any(normalise(p.get("city") or "") == normalise(balance.get("last_city") or "") for p in q.places)
    if city_hit:
        s += 0.20
        reasons.append(f"the account's last city is one they gave ({balance.get('last_city')})")
    elif q.places:
        doubts.append("the account's last address is nowhere they mentioned")
    tied = False
    if (balance.get("last_email") or "").lower() in [e.strip().lower() for e in q.emails]:
        s += 0.35
        tied = True
        reasons.append("an email they gave is the account's last email")
    return {"score": s, "reasons": reasons, "doubts": doubts, "tied": tied, "city_hit": city_hit}


def _twins(balance: dict, ledger: list[dict]) -> list[dict]:
    """Other balances that carry the same name in a different city."""
    mine = sorted(tokens(balance["owner_name"]))
    return [b for b in ledger if b["id"] != balance["id"] and sorted(tokens(b["owner_name"])) == mine
            and normalise(b.get("last_city") or "") != normalise(balance.get("last_city") or "")]


def search(ledger: list[dict], q: Query, *, floor: float = FLOOR, top: int = 10,
           typed_cities: set[str] | None = None) -> list[dict]:
    """Candidate balances for one person, strongest first. Each has strength, band, reasons and doubts.

    Another balance with the same name is named by its city only when the person typed that city
    (`typed_cities`, normalised; by default every city in the query)."""
    if typed_cities is None:
        typed_cities = {normalise(p.get("city") or "") for p in q.places if (p.get("city") or "").strip()}
    if not tokens(q.name) and not q.former_names:
        return []
    out = []
    for b in ledger:
        r = _score_org(b, q) if b.get("owner_type") == "ORGANISATION" else _score_one(b, q)
        if r is None:
            continue
        p = r["score"]
        doubts = r["doubts"]
        twins = _twins(b, ledger)
        if twins and not r["tied"]:
            named = sorted({t.get("last_city") for t in twins if normalise(t.get("last_city") or "") in typed_cities})
            where = ", ".join(named) if named else "another city"
            if p > TWIN_CAP:
                p = TWIN_CAP
            doubts.append(f"another dormant balance carries the same name, in {where}; nothing they gave "
                          "(an email, a street or a postcode) ties this one to them"
                          + (" beyond the city" if r["city_hit"] else ""))
        p = round(max(0.0, min(0.99, p)), 2)
        if p < floor:
            continue
        out.append({"balance_id": b["id"], "owner_display": mask.owner_display(b), "amount": b["amount"],
                    "currency": b["currency"], "probability": p, "strength": int(round(p * 100)), "band": band(p),
                    "reasons": r["reasons"], "doubts": doubts, "status": b["status"],
                    "sent_to_state_on": (b.get("escheat") or {}).get("reported_on")
                    if b["status"] == "ESCHEATED" else None})
    out.sort(key=lambda m: (-m["probability"], m["balance_id"]))
    return out[:top]
