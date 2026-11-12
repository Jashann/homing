"""Plain-code comparisons between what was read on a document and the account record.

No model is asked anything here. verify.py uses these to set each check's result.
"""
from __future__ import annotations

import re
from datetime import date

from rapidfuzz import fuzz

from .names import normalise, phonetic, tokens

_EXPIRY = re.compile(r"expir|valid (until|to|thru|through)|good (until|thru)", re.I)
_ISO = re.compile(r"(\d{4})-(\d{2})-(\d{2})")


def _same_word(a: str, b: str) -> bool:
    if a == b or fuzz.ratio(a, b) >= 85:
        return True
    pa = phonetic(a)
    return len(pa) >= 3 and pa == phonetic(b)


def compare_names(printed: str, wanted: str) -> str:
    """How well a name printed on a document covers a wanted name: "full", "partial" or "none".

    Order does not matter ("SANTOS MARIA L" and "Maria Luz Santos" are the same person), a single letter is an
    initial, and a small spelling difference or a same-sounding spelling counts as the same word."""
    want, have = tokens(wanted), tokens(printed)
    if not want or not have:
        return "none"
    words = [w for w in want if len(w) > 1]
    have_words = [h for h in have if len(h) > 1]
    have_letters = {h[0] for h in have}
    by_word = [w for w in words if any(_same_word(w, h) for h in have_words)]
    by_initial = [w for w in words if w not in by_word and w[0] in {h for h in have if len(h) == 1}]
    initials_ok = all(w in have_letters for w in want if len(w) == 1)
    if not words:
        return "full" if initials_ok else "none"
    if len(by_word) + len(by_initial) == len(words) and by_word and (len(by_word) >= 2 or len(words) == 1):
        return "full"
    return "partial" if by_word else "none"


def compare_organisation(texts: list[str], organisation: str) -> bool:
    want = normalise(organisation)
    for text in texts:
        have = normalise(text)
        if want and have and (fuzz.token_set_ratio(want, have) >= 88 or (len(want) >= 5 and want in have)
                              or (len(have) >= 5 and have in want)):
            return True
    return False


def address_against_record(address: dict, record: dict) -> set[str]:
    """Which parts of the record's reported address a printed address matches: "street", "postcode", "city"."""
    hit: set[str] = set()
    city, postcode, street, region = (normalise(address.get(k) or "") for k in ("city", "postcode", "street", "region"))
    r_city, r_post, r_street, r_region = (normalise(record.get(k) or "") for k in
                                          ("owner_city", "owner_postcode", "owner_street", "owner_region"))
    if city and r_city and fuzz.ratio(city, r_city) >= 90 and (not region or not r_region or region == r_region):
        hit.add("city")
    if postcode and r_post and postcode.replace(" ", "")[:5] == r_post.replace(" ", "")[:5]:
        hit.add("postcode")
    if street and r_street and fuzz.token_set_ratio(street, r_street) >= 88:
        hit.add("street")
    return hit


def expired_dates(reading: dict, today: date) -> list[str]:
    out = []
    for d in reading.get("dates") or []:
        m = _ISO.search(d.get("value") or "")
        if m and _EXPIRY.search(d.get("label") or ""):
            try:
                if date(int(m[1]), int(m[2]), int(m[3])) < today:
                    out.append(d["value"])
            except ValueError:
                continue
    return out
