"""Totals for payments businesses owed to people, across every holder, from California's unclaimed property file.

    uv run python scripts/payee_totals.py   # needs data/registers.duckdb (see scripts/paypal_totals.py to build it)

The output is data/payee_totals.json. It counts the records whose property type is a payment a business owed a payee
(wages, commissions, vendor and accounts payable checks, customer refunds and credits, royalties, expense and other
payee checks), for all holders together. Bank deposits, insurance, securities, safe deposit, utility deposits, money
orders, pensions, court and trust funds and gift certificates are left out.

The amount is the file's CURRENT_CASH_BALANCE (the same field and filter paypal_totals.py uses), in US dollars,
summed in cents so the totals do not drift with floating-point order.
The state's file also has CASH_REPORTED, the amount the holder sent; the two differ once claims are paid or
securities are sold. data/registers.duckdb keeps only the current balance, and a blank or unreadable value was
loaded as 0, so the column has no nulls.

Only totals leave this script: records and sum per property type and the grand total. Nothing about any single
record, owner, holder or city is written. The figures count records (one line of the state's file each), not people
or accounts. data/registers.duckdb is opened read-only.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data" / "registers.duckdb"
OUT = ROOT / "data" / "payee_totals.json"

AS_OF = "2026-11-04"
SOURCE = "California State Controller, unclaimed property records, file dated 2026-11-04"
SOURCE_URL = "https://www.sco.ca.gov/upd_download_property_records.html"
CHECK = "holder_name like '%PAYPAL%'"
CHECK_EXPECTED = (2_054_035, "125083033.46")

# Property type codes that are a payment a business owed a payee. Codes of two digits are the state's older
# (non-standard) codes, which the file still carries for some records.
PAYEE_CODES = [
    "MS01",  # wages, payroll, salaries
    "81",    # salaries/wages (older code)
    "MS02",  # commissions
    "59",    # commissions (older code)
    "MS04",  # payments for goods and services
    "MS08",  # accounts payable
    "CK13",  # vendor checks
    "85",    # vendor payments (older code)
    "CK10",  # expense checks
    "MS16",  # miscellaneous outstanding checks
    "CK14",  # checks written off to income
    "MS05",  # customer overpayments
    "MS07",  # unrefunded overcharges
    "MS09",  # credit balances, accounts receivable
    "61",    # credit balances (older code)
    "MS10",  # discounts due
    "MS11",  # refunds due
    "79",    # refunds (older code)
    "CK12",  # credit checks or memos
    "UT03",  # utility refunds or rebates
    "72",    # layaway deposits (older code)
    "MI02",  # royalties
    "MI03",  # overriding royalties
    "MI08",  # shut-in royalties
    "MI09",  # minimum royalties
    "80",    # royalties/residuals (older code)
]


def money(x: float) -> str:
    return f"{x:.2f}"


def compute(con: duckdb.DuckDBPyConnection) -> dict:
    rows = con.execute("select coalesce(property_type, ''), count(*), coalesce(sum(cast(amount as decimal(18, 2))), 0), "
                       "count(*) - count(amount) from records group by 1").fetchall()
    by_code: dict[str, tuple[str, int, float]] = {}
    nulls = 0
    for raw, count, amount, null_count in rows:
        code, _, words = raw.partition(":")
        code = code.strip().upper()
        nulls += null_count
        prev = by_code.get(code)
        if prev:  # the file spells one code in two cases (CT05 and ct05); merge them
            by_code[code] = (prev[0], prev[1] + count, prev[2] + amount)
        else:
            by_code[code] = (words.strip(), count, amount)

    missing = [c for c in PAYEE_CODES if c not in by_code]
    assert not missing, f"codes not in the file: {missing}"
    types = [{"code": c, "description": by_code[c][0], "records": int(by_code[c][1]), "total": money(float(by_code[c][2]))}
             for c in PAYEE_CODES]
    types.sort(key=lambda t: -float(t["total"]))
    n = sum(t["records"] for t in types)
    total = sum(by_code[c][2] for c in PAYEE_CODES)
    all_n, all_total = con.execute("select count(*), coalesce(sum(cast(amount as decimal(18, 2))), 0) from records").fetchone()

    check_n, check_total = con.execute(f"select count(*), coalesce(sum(cast(amount as decimal(18, 2))), 0) from records where {CHECK}").fetchone()
    check_ok = (int(check_n), money(float(check_total))) == CHECK_EXPECTED
    assert check_ok, f"PayPal check failed: {check_n} records, {money(float(check_total))}"

    return {
        "as_of": AS_OF,
        "source": SOURCE,
        "source_url": SOURCE_URL,
        "amount_field": "CURRENT_CASH_BALANCE (USD); blank values loaded as 0, no nulls" + (f", {nulls} nulls" if nulls else ""),
        "holders": "all",
        "payee_records": int(n),
        "payee_total": money(float(total)),
        "file_records": int(all_n),
        "file_total": money(float(all_total)),
        "share_of_file_total": round(float(total / all_total), 4) if all_total else 0,
        "property_types": types,
        "check": {"query": CHECK, "records": int(check_n), "total": money(float(check_total)), "matches_readme": check_ok},
    }


def main() -> None:
    con = duckdb.connect(str(DB), read_only=True)
    out = compute(con)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, indent=2) + "\n")
    print(f"{out['payee_records']:,} records, ${float(out['payee_total']):,.2f}", file=sys.stderr)
    print(f"wrote {OUT}", file=sys.stderr)


if __name__ == "__main__":
    main()
