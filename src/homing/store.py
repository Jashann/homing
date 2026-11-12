"""Homing's own records, in SQLite (data/homing.sqlite).

Tables
- balances: the business's dormant ledger, plus balances imported from its PayPal payout history (one JSON row per balance, plus its status)
- claims:   one claim by one claimant on one balance, its check result, its document attempts and its approval
            (who approved, when, and the reason they wrote)
- proof:    every document added to a claim: file hash, where it is stored, and what the model read on it
- audit:    every change, in order, with who or what made it

Payout state lives in the settlement database (settlement.py); a claim only stores what it needs to find it.
"""
from __future__ import annotations

import json
import os
import secrets
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB = ROOT / "data" / "homing.sqlite"
DEFAULT_LEDGER = ROOT / "data" / "sample_ledger.json"

SCHEMA = """
CREATE TABLE IF NOT EXISTS balances (
    id TEXT PRIMARY KEY,
    data TEXT NOT NULL,
    status TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS claims (
    id TEXT PRIMARY KEY,
    balance_id TEXT NOT NULL,
    status TEXT NOT NULL,
    claimant TEXT NOT NULL,
    amount TEXT NOT NULL,
    currency TEXT NOT NULL,
    proof TEXT,
    approved_by TEXT,
    approved_at TEXT,
    approval_reason TEXT,
    attempts INTEGER NOT NULL DEFAULT 0,
    receiver TEXT,
    receiver_type TEXT,
    payout_fee TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    paid_at TEXT
);
CREATE TABLE IF NOT EXISTS proof (
    id TEXT PRIMARY KEY,
    claim_id TEXT NOT NULL,
    filename TEXT NOT NULL,
    content_type TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    stored_as TEXT NOT NULL,
    reading TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    at TEXT NOT NULL,
    claim_id TEXT,
    balance_id TEXT,
    action TEXT NOT NULL,
    detail TEXT
);
CREATE INDEX IF NOT EXISTS claims_balance ON claims(balance_id);
CREATE INDEX IF NOT EXISTS proof_claim ON proof(claim_id);
CREATE INDEX IF NOT EXISTS proof_hash ON proof(sha256);
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class NotFound(KeyError):
    pass


class Store:
    def __init__(self, path: str | Path | None = None):
        self.path = Path(path or os.environ.get("HOMING_DB") or DEFAULT_DB)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.conn = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None, timeout=30)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(SCHEMA)
        have = {r["name"] for r in self.conn.execute("PRAGMA table_info(claims)")}
        for col, kind in (("approved_at", "TEXT"), ("approval_reason", "TEXT"),
                          ("attempts", "INTEGER NOT NULL DEFAULT 0"), ("payout_address", "TEXT")):
            if col not in have:   # a database made by an earlier version
                self.conn.execute(f"ALTER TABLE claims ADD COLUMN {col} {kind}")

    def close(self) -> None:
        self.conn.close()

    # ------------------------------------------------------------------ ledger

    def load_ledger(self, path: str | Path | None = None, *, wipe_claims: bool = True) -> int:
        """Replaces the balances with the sample ledger. With wipe_claims, claims, proof and audit go too, so the
        demo starts clean. (Payouts already made stay in the settlement database; new claim ids never reuse old
        ones, so an old payout can never be mistaken for a new claim's.)"""
        data = json.loads(Path(path or DEFAULT_LEDGER).read_text())
        with self.lock:
            self.conn.execute("BEGIN IMMEDIATE")
            try:
                self.conn.execute("DELETE FROM balances")
                if wipe_claims:
                    for t in ("claims", "proof", "audit"):
                        self.conn.execute(f"DELETE FROM {t}")
                for b in data["balances"]:
                    self.conn.execute("INSERT INTO balances(id, data, status, updated_at) VALUES (?,?,?,?)",
                                      (b["id"], json.dumps(b, ensure_ascii=False), b["status"], now()))
                self.conn.execute("COMMIT")
            except BaseException:
                self.conn.execute("ROLLBACK")
                raise
        self.audit("ledger_loaded", detail={"balances": len(data["balances"])})
        return len(data["balances"])

    def balances(self, status: str | None = None) -> list[dict]:
        q, args = "SELECT data, status FROM balances", ()
        if status:
            q, args = q + " WHERE status=?", (status.upper(),)
        with self.lock:
            rows = self.conn.execute(q + " ORDER BY id", args).fetchall()
        return [{**json.loads(r["data"]), "status": r["status"]} for r in rows]

    def balance(self, balance_id: str) -> dict:
        with self.lock:
            r = self.conn.execute("SELECT data, status FROM balances WHERE id=?", (balance_id,)).fetchone()
        if r is None:
            raise NotFound(balance_id)
        return {**json.loads(r["data"]), "status": r["status"]}

    def find_balance(self, balance_id: str) -> dict | None:
        try:
            return self.balance(balance_id)
        except NotFound:
            return None

    def add_balance(self, balance: dict, why: str = "") -> None:
        """Adds one balance (an uncollected PayPal payout found in the business's PayPal history)."""
        with self.lock:
            self.conn.execute("INSERT INTO balances(id, data, status, updated_at) VALUES (?,?,?,?)",
                              (balance["id"], json.dumps(balance, ensure_ascii=False), balance["status"], now()))
        self.audit("balance_added", balance_id=balance["id"], detail={"why": why})

    def update_balance_data(self, balance_id: str, why: str = "", **fields) -> dict:
        """Changes stored fields of one balance (not its status)."""
        with self.lock:
            b = self.balance(balance_id)
            data = {k: v for k, v in b.items() if k != "status"}
            data.update(fields)
            self.conn.execute("UPDATE balances SET data=?, updated_at=? WHERE id=?",
                              (json.dumps(data, ensure_ascii=False), now(), balance_id))
        self.audit("balance_updated", balance_id=balance_id, detail={"why": why, "fields": sorted(fields)})
        return self.balance(balance_id)

    def set_balance_status(self, balance_id: str, status: str, why: str = "") -> None:
        with self.lock:
            old = self.balance(balance_id)["status"]
            if old == status:
                return
            self.conn.execute("UPDATE balances SET status=?, updated_at=? WHERE id=?", (status, now(), balance_id))
        self.audit("balance_status", balance_id=balance_id, detail={"from": old, "to": status, "why": why})

    # ------------------------------------------------------------------ claims

    def create_claim(self, balance: dict, claimant: dict) -> dict:
        claim_id = "C" + secrets.token_hex(4)   # short: it ends up inside PayPal batch and invoice numbers
        t = now()
        with self.lock:
            self.conn.execute(
                "INSERT INTO claims(id, balance_id, status, claimant, amount, currency, created_at, updated_at)"
                " VALUES (?,?,?,?,?,?,?,?)",
                (claim_id, balance["id"], "OPEN", json.dumps(claimant, ensure_ascii=False), balance["amount"],
                 balance["currency"], t, t))
        self.audit("claim_created", claim_id=claim_id, balance_id=balance["id"],
                   detail={"claimant_country": claimant.get("country")})
        return self.claim(claim_id)

    def claim(self, claim_id: str) -> dict:
        with self.lock:
            r = self.conn.execute("SELECT * FROM claims WHERE id=?", (claim_id,)).fetchone()
        if r is None:
            raise NotFound(claim_id)
        return self._claim(r)

    def claims(self, balance_id: str | None = None) -> list[dict]:
        q, args = "SELECT * FROM claims", ()
        if balance_id:
            q, args = q + " WHERE balance_id=?", (balance_id,)
        with self.lock:
            rows = self.conn.execute(q + " ORDER BY created_at, id", args).fetchall()
        return [self._claim(r) for r in rows]

    @staticmethod
    def _claim(r: sqlite3.Row) -> dict:
        d = dict(r)
        d["claimant"] = json.loads(d["claimant"])
        d["proof"] = json.loads(d["proof"]) if d["proof"] else None
        return d

    def update_claim(self, claim_id: str, action: str, detail: Any = None, **fields) -> dict:
        allowed = {"status", "proof", "approved_by", "approved_at", "approval_reason", "attempts", "receiver",
                   "receiver_type", "payout_fee", "paid_at", "payout_address"}
        bad = set(fields) - allowed
        if bad:
            raise ValueError(f"cannot set {bad}")
        if "proof" in fields and fields["proof"] is not None:
            fields["proof"] = json.dumps(fields["proof"], ensure_ascii=False)
        with self.lock:
            before = self.claim(claim_id)
            if fields:
                sets = ", ".join(f"{k}=?" for k in fields) + ", updated_at=?"
                self.conn.execute(f"UPDATE claims SET {sets} WHERE id=?", (*fields.values(), now(), claim_id))
        after = self.claim(claim_id)
        info = {"from": before["status"], "to": after["status"]} if before["status"] != after["status"] else {}
        if detail is not None:
            info["detail"] = detail
        self.audit(action, claim_id=claim_id, balance_id=after["balance_id"], detail=info or None)
        return after

    # ------------------------------------------------------------------ proof documents

    def add_document(self, claim_id: str, *, filename: str, content_type: str, sha256: str, stored_as: str) -> dict:
        doc_id = "D" + secrets.token_hex(4)
        with self.lock:
            self.conn.execute(
                "INSERT INTO proof(id, claim_id, filename, content_type, sha256, stored_as, created_at)"
                " VALUES (?,?,?,?,?,?,?)", (doc_id, claim_id, filename, content_type, sha256, stored_as, now()))
        self.audit("document_added", claim_id=claim_id, detail={"document": doc_id, "sha256": sha256[:12]})
        return self.document(doc_id)

    def set_reading(self, doc_id: str, reading: dict) -> None:
        with self.lock:
            self.conn.execute("UPDATE proof SET reading=? WHERE id=?", (json.dumps(reading, ensure_ascii=False), doc_id))

    def document(self, doc_id: str) -> dict:
        with self.lock:
            r = self.conn.execute("SELECT * FROM proof WHERE id=?", (doc_id,)).fetchone()
        if r is None:
            raise NotFound(doc_id)
        return self._doc(r)

    def documents(self, claim_id: str) -> list[dict]:
        with self.lock:
            rows = self.conn.execute("SELECT * FROM proof WHERE claim_id=? ORDER BY created_at, id",
                                     (claim_id,)).fetchall()
        return [self._doc(r) for r in rows]

    def documents_with_hash(self, sha256: str) -> list[dict]:
        with self.lock:
            rows = self.conn.execute("SELECT * FROM proof WHERE sha256=?", (sha256,)).fetchall()
        return [self._doc(r) for r in rows]

    @staticmethod
    def _doc(r: sqlite3.Row) -> dict:
        d = dict(r)
        d["reading"] = json.loads(d["reading"]) if d["reading"] else None
        return d

    # ------------------------------------------------------------------ audit

    def audit(self, action: str, *, claim_id: str | None = None, balance_id: str | None = None,
              detail: Any = None) -> None:
        text = None if detail is None else (detail if isinstance(detail, str) else json.dumps(detail, default=str,
                                                                                               ensure_ascii=False))
        with self.lock:
            self.conn.execute("INSERT INTO audit(at, claim_id, balance_id, action, detail) VALUES (?,?,?,?,?)",
                              (now(), claim_id, balance_id, action, text))

    def audit_log(self, claim_id: str | None = None) -> list[dict]:
        q, args = "SELECT * FROM audit", ()
        if claim_id:
            q, args = q + " WHERE claim_id=?", (claim_id,)
        with self.lock:
            return [dict(r) for r in self.conn.execute(q + " ORDER BY id", args)]
