"""How a ledger record is shown to people who may not be its owner.

Emails keep two letters of the name and of the domain; addresses keep the city and country only; a person's name
keeps the first word and the initials of the rest. An organisation's name is shown in full (it is not a private
person). Dates of birth keep the year.
"""
from __future__ import annotations

import re

_EMAIL = re.compile(r"^([^@\s]+)@([^@\s]+)$")


def email(value: str | None) -> str:
    """'ana.kowalski@example.com' -> 'an***@ex***.com'."""
    if not value:
        return ""
    m = _EMAIL.match(value.strip())
    if not m:
        return "***"
    local, domain = m.groups()
    host, _, tld = domain.rpartition(".")
    host = host or domain
    keep = 1 if len(local) <= 3 else 2
    shown = f"{local[:keep]}***@{host[:2]}***"
    return shown + (f".{tld}" if tld and host != domain else "")


def phone(value: str | None) -> str:
    digits = re.sub(r"\D", "", value or "")
    return ("+" if (value or "").strip().startswith("+") else "") + "*" * max(len(digits) - 3, 0) + digits[-3:]


def receiver(value: str | None, kind: str = "EMAIL") -> str:
    return phone(value) if (kind or "EMAIL").upper() == "PHONE" else email(value)


def word(w: str) -> str:
    return w[:1] + "·" * max(len(w) - 1, 0)


def name_letters(name: str) -> str:
    """'Tomasz Wiśniewski' -> 'T····· W········' (first letter and length of each word)."""
    return " ".join(word(w) for w in (name or "").split())


def owner_display(balance: dict) -> str:
    """'Ana Kowalski' -> 'Ana K.'; 'J. R. Okafor' -> 'J. R. O.'; organisations in full."""
    name = balance.get("owner_name") or ""
    if balance.get("owner_type") == "ORGANISATION":
        return name
    parts = name.split()
    if not parts:
        return ""
    first = parts[0]
    rest = [p[0].upper() + "." for p in parts[1:] if p.strip(".")]
    return " ".join([first, *rest])


def address(balance: dict) -> str:
    """City and country only: the street and postcode are never shown."""
    return ", ".join(x for x in (balance.get("last_city"), balance.get("last_country")) if x)


def date_of_birth(value: str | None) -> str:
    return (value[:4] + "-··-··") if value and len(value) >= 4 else ""
