"""Totals for the records PayPal reported to California, from the State Controller's public bulk files.

    uv run python scripts/paypal_totals.py --download   # fetches the four files (about 3.3 GB) into data/downloads,
                                                        # loads them into data/registers.duckdb, writes the totals
    uv run python scripts/paypal_totals.py --load a.zip b.zip ...   # the same from files you already have
    uv run python scripts/paypal_totals.py              # totals again from an existing data/registers.duckdb

The output is data/paypal_totals.json, which the app reads (it ships with the repository, so none of this is needed
to run the app).

Only totals leave this script: the record count and sum, the size bands and the split by property type. Nothing
about any single owner (no name, kind, city or amount) is written. The figures count records (one line of the
state's file each), not people or accounts. data/registers.duckdb is opened read-only for the totals.
"""
from __future__ import annotations

import csv
import io
import json
import sys
import zipfile
from pathlib import Path

import duckdb
import pyarrow as pa

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data" / "registers.duckdb"
DOWNLOADS = ROOT / "data" / "downloads"
FILES_URL = "https://claimit.ca.gov/upd-property-records/"
FILES = ["01_From_0_To_Below_10.zip", "02_From_10_To_Below_100.zip", "03_From_100_To_Below_500.zip",
         "04_From_500_To_Beyond.zip"]
OUT = ROOT / "data" / "paypal_totals.json"

AS_OF = "2026-11-04"
SOURCE = "California State Controller, unclaimed property records, file dated 2026-11-04"
SOURCE_URL = "https://www.sco.ca.gov/upd_download_property_records.html"
WHERE = "holder_name like '%PAYPAL%'"

# The state's property type codes (NAUPA codes) in plain words. Codes not listed keep the state's own wording.
PROPERTY_TYPES = {
    "MS17": "Miscellaneous intangible property", "MS02": "Commissions", "MS05": "Customer overpayments",
    "MS04": "Payments for goods and services", "CK13": "Vendor checks", "MS01": "Wages, payroll, salaries",
    "SC12": "Underlying shares", "MS11": "Refunds due", "SC13": "Funds for liquidation", "CK07": "Money orders",
    "SC09": "Cash for fractional shares", "76": "Other (outside the standard list of codes)",
}

BANDS = [("Under $10", 0, 10), ("$10 to $99", 10, 100), ("$100 to $999", 100, 1000), ("$1,000 and over", 1000, None)]

def money(x: float) -> str:
    return f"{x:.2f}"


def compute(con: duckdb.DuckDBPyConnection) -> dict:
    n, total = con.execute(f"select count(*), coalesce(sum(amount), 0) from records where {WHERE}").fetchone()
    bands = []
    for label, lo, hi in BANDS:
        cond = f"amount >= {lo}" + (f" and amount < {hi}" if hi else "")
        c, s = con.execute(f"select count(*), coalesce(sum(amount), 0) from records where {WHERE} and {cond}").fetchone()
        bands.append({"label": label, "records": int(c), "total": money(s)})
    o100 = con.execute(f"select count(*), coalesce(sum(amount),0) from records where {WHERE} and amount >= 100").fetchone()
    o1000 = con.execute(f"select count(*), coalesce(sum(amount),0) from records where {WHERE} and amount >= 1000").fetchone()
    venmo = con.execute("select count(*), coalesce(sum(amount),0) from records where holder_name like '%VENMO%'").fetchone()
    holders = con.execute(f"select holder_name, count(*) from records where {WHERE} group by 1 order by 2 desc").fetchall()

    types = []
    for raw, count, amount in con.execute(f"select coalesce(property_type, ''), count(*), coalesce(sum(amount), 0) "
                                          f"from records where {WHERE} group by 1 order by 2 desc").fetchall():
        code, _, words = raw.partition(":")
        label = PROPERTY_TYPES.get(code.strip()) or (words.strip().capitalize() if words else raw) or "Not stated"
        types.append({"code": code.strip(), "label": label, "records": int(count), "total": money(amount),
                      "share_of_records": round(count / n, 4) if n else 0})

    assert sum(b["records"] for b in bands) == n, "bands do not add up to the total"
    assert sum(t["records"] for t in types) == n, "property types do not add up to the total"
    return {
        "as_of": AS_OF,
        "source": SOURCE,
        "source_url": SOURCE_URL,
        "query": f"holder_name like '%PAYPAL%' ({', '.join(f'{h} {c:,}' for h, c in holders)})",
        "holder_records": int(n),
        "holder_total": money(total),
        "bands": bands,
        "over_100": {"records": int(o100[0]), "total": money(o100[1])},
        "over_1000": {"records": int(o1000[0]), "total": money(o1000[1])},
        "venmo": {"records": int(venmo[0]), "total": money(venmo[1])},
        "property_types": types,
    }


def download() -> list[Path]:
    import httpx
    DOWNLOADS.mkdir(parents=True, exist_ok=True)
    out = []
    for name in FILES:
        path = DOWNLOADS / name
        if not path.exists():
            print(f"downloading {FILES_URL}{name}", file=sys.stderr)
            part = path.with_suffix(".part")
            with httpx.stream("GET", FILES_URL + name, timeout=120, follow_redirects=True) as r:
                r.raise_for_status()
                with part.open("wb") as f:
                    for chunk in r.iter_bytes(1 << 20):
                        f.write(chunk)
            part.rename(path)
        out.append(path)
    return out


COLUMNS = ["record_id", "owner_name", "owner_city", "amount", "holder_name", "property_type"]


def _flush(con: duckdb.DuckDBPyConnection, rows: list[tuple]) -> int:
    if not rows:
        return 0
    cols = list(zip(*rows))
    types = [pa.string(), pa.string(), pa.string(), pa.float64(), pa.string(), pa.string()]
    con.register("batch_table", pa.table({n: pa.array(c, type=t) for n, c, t in zip(COLUMNS, cols, types)}))
    con.execute("insert into records select * from batch_table")
    con.unregister("batch_table")
    return len(rows)


def _amount(text: str) -> float:
    try:
        return float(text or 0)
    except ValueError:
        return 0.0


def load(zips: list[Path], batch: int = 200_000) -> None:
    """Loads the state's zipped CSV files into a fresh data/registers.duckdb (only the columns used here)."""
    if DB.exists() or DB.is_symlink():
        DB.unlink()
    con = duckdb.connect(str(DB))
    con.execute("create table records (record_id varchar, owner_name varchar, owner_city varchar, amount double, "
                "holder_name varchar, property_type varchar)")
    for zip_path in zips:
        total = 0
        with zipfile.ZipFile(zip_path) as z:
            for member in z.namelist():
                with z.open(member) as raw:
                    rows: list[tuple] = []
                    for r in csv.DictReader(io.TextIOWrapper(raw, encoding="latin-1", newline="")):
                        rows.append((r.get("PROPERTY_ID"), (r.get("OWNER_NAME") or "").strip(),
                                     (r.get("OWNER_CITY") or "").strip(), _amount(r.get("CURRENT_CASH_BALANCE")),
                                     (r.get("HOLDER_NAME") or "").strip(), (r.get("PROPERTY_TYPE") or "").strip()))
                        if len(rows) >= batch:
                            total += _flush(con, rows)
                            rows = []
                    total += _flush(con, rows)
        print(f"loading {Path(zip_path).name}: {total:,} rows", file=sys.stderr)
    con.close()


def main() -> None:
    args = sys.argv[1:]
    if args[:1] == ["--download"]:
        load(download())
    elif args[:1] == ["--load"]:
        load([Path(a) for a in args[1:]])
    con = duckdb.connect(str(DB), read_only=True)
    out = compute(con)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, indent=2) + "\n")
    print(f"{out['holder_records']:,} records, ${float(out['holder_total']):,.2f}", file=sys.stderr)
    print(f"wrote {OUT}", file=sys.stderr)


if __name__ == "__main__":
    main()
