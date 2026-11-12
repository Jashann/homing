"""Scores one ledger record against what a person says about themselves, naming every point added or taken off."""
from __future__ import annotations

from dataclasses import dataclass, field

from rapidfuzz import fuzz

from .names import Person, is_organisation, normalise, phonetic, tokens


@dataclass
class Place:
    """Somewhere the person lived or worked."""
    city: str = ""
    region: str = ""
    postcode: str = ""
    country: str = ""
    street: str = ""


@dataclass
class History:
    person: Person
    places: list[Place] = field(default_factory=list)


@dataclass
class Match:
    record: dict
    score: float
    reasons: list[str]
    doubts: list[str]


def score(record: dict, history: History) -> Match:
    """A transparent score from 0 to 1. Every point added or removed is named."""
    p = history.person
    owner = record.get("owner_norm") or normalise(record.get("owner_name", ""))
    otoks = [t for t in tokens(owner)]
    reasons: list[str] = []
    doubts: list[str] = []
    s = 0.0

    fam_best = max((fuzz.ratio(k.replace(" ", ""), t) for k in p.family_keys() for t in otoks), default=0)
    fam_joined = max((fuzz.partial_ratio(k, owner) for k in p.family_keys()), default=0)
    fam = max(fam_best, fam_joined)
    said = {phonetic(k) for k in p.family_keys()} & {phonetic(t) for t in otoks if len(t) > 1}
    if fam >= 95:
        s += 0.30; reasons.append("family name matches exactly")
    elif fam >= 85:
        s += 0.22; reasons.append("family name matches with a spelling difference")
    elif said:
        s += 0.20; reasons.append("family name is spelled differently but sounds the same")
    else:
        doubts.append("family name is only a loose match")

    given = p.given_keys()
    full = [g for g in given if g in otoks]
    initials = [g for g in given if any(len(t) == 1 and t == g[0] for t in otoks)]
    close = [g for g in given if any(len(t) > 1 and fuzz.ratio(g, t) >= 85 for t in otoks)]
    if len(full) >= 2:
        s += 0.30; reasons.append(f"two given names match ({', '.join(sorted(full))})")
    elif full or close:
        s += 0.22; reasons.append(f"given name matches ({', '.join(sorted(full or close))})")
        if initials:
            s += 0.04; reasons.append("a second given name matches by initial")
    elif initials:
        s += 0.08; reasons.append("given name matches by initial only"); doubts.append("only an initial for the given name")
    else:
        doubts.append("no given name in the record")

    fam_phon = {phonetic(k) for k in p.family_keys()}
    extra = [t for t in otoks if len(t) > 1 and t not in given and phonetic(t) not in fam_phon
             and all(fuzz.ratio(t, k.replace(" ", "")) < 85 for k in p.family_keys())
             and all(fuzz.ratio(t, g) < 85 for g in given)]
    if extra:
        s -= 0.06 * min(len(extra), 3); doubts.append(f"the record has other names ({', '.join(extra[:3])})")

    rc, rr, rz = (normalise(record.get(k) or "") for k in ("owner_city", "owner_region", "owner_postcode"))
    rstreet = normalise(record.get("owner_street") or "")
    place_hit = False
    for pl in history.places:
        city, region, zc, street = normalise(pl.city), normalise(pl.region), normalise(pl.postcode), normalise(pl.street)
        if street and rstreet and fuzz.token_set_ratio(street, rstreet) >= 88:
            s += 0.30; reasons.append(f"the record's street is an address they gave ({record.get('owner_street')})"); place_hit = True; break
        if zc and rz and rz[:5] == zc[:5]:
            s += 0.20; reasons.append(f"the record's postcode is one they lived in ({rz[:5]})"); place_hit = True; break
        if city and rc and fuzz.ratio(city, rc) >= 90 and (not region or not rr or region == rr):
            s += 0.16; reasons.append(f"the record's city is one they lived in ({record.get('owner_city')})"); place_hit = True; break
    if not place_hit and history.places:
        doubts.append("the record's address is nowhere they mentioned")

    if is_organisation(record.get("owner_name", "")) and not is_organisation(" ".join(p.family)):
        s -= 0.25; doubts.append("the owner looks like an organisation")
    return Match(record=record, score=max(0.0, min(1.0, round(s, 3))), reasons=reasons, doubts=doubts)
