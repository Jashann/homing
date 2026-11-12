"""Name handling: how one person's name shows up differently across borders and decades."""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

_PUNCT = re.compile(r"[^A-Z0-9 ]+")
_SPACE = re.compile(r"\s+")
# Words that carry no identity in an owner field.
NOISE = {"MR", "MRS", "MS", "DR", "JR", "SR", "II", "III", "IV", "EST", "ESTATE", "OF", "THE", "AND", "OR",
         "TTEE", "TRUSTEE", "TRUST", "CUST", "UTMA", "JT", "TEN", "DEC", "DECD", "DECEASED", "FBO", "ETAL"}
# Particles that are written joined or apart depending on the clerk.
PARTICLES = {"DE", "DEL", "DELA", "DELOS", "LA", "LOS", "LAS", "VAN", "VON", "DER", "DEN", "DI", "DA", "DOS", "BIN",
             "BINTI", "AL", "EL", "ST", "SAN", "SANTA", "MC", "MAC", "O"}
ORG_WORDS = {"INC", "LLC", "LLP", "LP", "CORP", "CORPORATION", "CO", "COMPANY", "LTD", "LIMITED", "BANK",
             "UNIVERSITY", "COLLEGE", "CITY", "COUNTY", "STATE", "DEPT", "DEPARTMENT", "ASSOCIATION", "ASSN",
             "FOUNDATION", "CHURCH", "HOSPITAL", "SCHOOL", "DISTRICT", "GROUP", "PARTNERS", "SERVICES", "SA", "AG",
             "GMBH", "PLC", "PTY", "KK", "SDN", "BHD", "NV", "BV", "SPA", "SRL", "FUND", "INSURANCE", "AUTHORITY"}

def normalise(text: str) -> str:
    """Upper-case ASCII with single spaces: 'Núñez-O'Brien, María' -> 'NUNEZ OBRIEN MARIA'."""
    if not text:
        return ""
    t = unicodedata.normalize("NFKD", text)
    t = "".join(c for c in t if not unicodedata.combining(c)).upper().replace("'", "").replace("’", "")
    return _SPACE.sub(" ", _PUNCT.sub(" ", t)).strip()


def tokens(text: str) -> list[str]:
    return [t for t in normalise(text).split() if t not in NOISE]


def is_organisation(name: str) -> bool:
    return any(t in ORG_WORDS for t in normalise(name).split())


@dataclass
class Person:
    """Everything a claimant has ever been called."""
    given: list[str]                      # given names in any order of use
    family: list[str]                     # every family name used: birth, married, both halves of a double surname
    aliases: list[str] = field(default_factory=list)   # full strings exactly as once written on a document

    def family_keys(self) -> set[str]:
        """Family-name strings to compare with a record, including joined and split particles."""
        keys: set[str] = set()
        for f in self.family:
            toks = tokens(f)
            if not toks:
                continue
            keys.add(" ".join(toks))
            keys.add("".join(toks))
            core = [t for t in toks if t not in PARTICLES]
            if core:
                keys.add(core[-1])
        for a in self.aliases:
            toks = tokens(a)
            if toks:
                keys.add(toks[-1]); keys.add(toks[0])
        return {k for k in keys if len(k) >= 2}

    def given_keys(self) -> set[str]:
        out: set[str] = set()
        for g in self.given:
            out.update(tokens(g))
        return out


_PHON_STEPS = [("PH", "F"), ("CK", "K"), ("KH", "K"), ("GH", "G"), ("SCH", "S"), ("SH", "S"), ("CH", "S"),
               ("TS", "S"), ("TZ", "S"), ("ZH", "S"), ("Q", "K"), ("C", "K"), ("X", "S"), ("Z", "S"), ("J", "S"),
               ("V", "B"), ("W", "B"), ("Y", "I"), ("D", "T"), ("G", "K"), ("M", "N"), ("H", "")]


_PHON_LETTERS = "BFKLNPRSTAEIOU"


def phonetic(token: str) -> str:
    """A rough said-aloud key: 'VILLANUEVA' and 'BILLANUEBA' share one, so do 'ESTEPHANO' and 'ESTEFANO'."""
    t = re.sub(r"[^A-Z]", "", normalise(token))
    if not t:
        return ""
    for a, b in _PHON_STEPS:
        t = t.replace(a, b)
    t = t[:1] + re.sub(r"[AEIOU]", "", t[1:])
    for _ in range(2):          # collapse doubled letters
        for c in _PHON_LETTERS:
            t = t.replace(c + c, c)
    return t
