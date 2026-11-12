"""The model's work that people see, and the saved copies used when no model is available.

Three jobs, none of which decides anything:
- read a document (documents.read_document); the verdict is then plain code (verify.assess)
- explain a verdict that code has already set, in the claimant's language, with an English copy
- draft the business's notice to an owner, in the owner's likely language, with an English copy (nothing is sent)

data/saved_readings.json holds the model's earlier output for the bundled sample documents (keyed by the file's
SHA-256), the explanations of the four sample cases (keyed by balance id and the check results), and notices for
the sample owners (keyed by balance id). `scripts/save_readings.py` writes it with the real model. When no model can
be reached these saved copies are used and marked "SAVED"; anything else gets a plain sentence saying a model is
needed.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Callable

from . import documents, llm
from .countries import language_of

SAVED_PATH = Path(__file__).resolve().parents[2] / "data" / "saved_readings.json"
MODEL = "sonnet"

NO_MODEL_READING = ("This document could not be read: no language model is set up on this server. Homing can read "
                    "its own sample documents without one, but a new document needs the claude command-line "
                    "program signed in, or ANTHROPIC_API_KEY set.")
NO_MODEL_NOTICE = ("Drafting a notice needs a language model, and none is set up on this server (sign in the claude "
                   "command-line program, or set ANTHROPIC_API_KEY).")

LANGUAGE_NAMES = {"en": "English", "pl": "Polish", "ar": "Arabic", "zh": "Chinese (simplified)", "vi": "Vietnamese",
                  "fr": "French", "de": "German", "es": "Spanish", "it": "Italian", "ja": "Japanese",
                  "pt": "Portuguese", "cs": "Czech", "tr": "Turkish", "sv": "Swedish", "nb": "Norwegian",
                  "da": "Danish", "fi": "Finnish", "nl": "Dutch", "ko": "Korean", "ru": "Russian",
                  "uk": "Ukrainian", "el": "Greek", "ro": "Romanian", "hu": "Hungarian", "th": "Thai",
                  "id": "Indonesian", "ms": "Malay", "he": "Hebrew", "fa": "Persian", "bn": "Bengali",
                  "ga": "Irish", "hi": "Hindi"}


def language_name(code: str) -> str:
    return LANGUAGE_NAMES.get((code or "en").split("-")[0].lower(), code or "English")


class Saved:
    """The saved readings file. Reads it lazily; `record` adds to it (used by scripts/save_readings.py)."""

    def __init__(self, path: Path | None = None):
        self.path = Path(path or SAVED_PATH)
        self._data: dict | None = None
        self._lock = threading.Lock()

    @property
    def data(self) -> dict:
        if self._data is None:
            try:
                self._data = json.loads(self.path.read_text())
            except (OSError, ValueError):
                self._data = {}
            for k in ("readings", "explanations", "notices"):
                self._data.setdefault(k, {})
        return self._data

    def reading(self, sha256: str) -> dict | None:
        r = self.data["readings"].get(sha256)
        return json.loads(json.dumps(r)) if r else None

    def explanation(self, balance_id: str, signature: str, language: str) -> dict | None:
        by_lang = self.data["explanations"].get(balance_id, {}).get(signature) or {}
        return by_lang.get(language) or by_lang.get("en")

    def notice(self, balance_id: str) -> dict | None:
        return self.data["notices"].get(balance_id)

    def save(self) -> None:
        with self._lock:
            self.path.write_text(json.dumps(self.data, indent=1, ensure_ascii=False, sort_keys=True) + "\n")


# ---- reading --------------------------------------------------------------------------------------------------

def make_reader(saved: Saved, live: Callable[[Path], dict] | None = None) -> Callable[[Path, str], dict]:
    """A reader for the server: the live model first; when no model can be reached, the saved reading for a known
    sample file; otherwise a failed reading with a plain sentence. Every reading carries reading_source."""
    live = live or (lambda path: documents.read_document(path, model=MODEL))

    def read(path: Path, sha256: str) -> dict:
        try:
            if llm.disabled():
                raise llm.ModelUnavailable("off")
            reading = live(Path(path))
            reading["reading_source"] = "LIVE"
            return reading
        except llm.ModelUnavailable:
            reading = saved.reading(sha256)
            if reading:
                reading["reading_source"] = "SAVED"
                return reading
            return {"status": "failed", "problem": NO_MODEL_READING, "reading_source": "NONE"}

    return read


# ---- the explanation --------------------------------------------------------------------------------------------

def signature(proof: dict) -> str:
    """What an explanation depends on: the verdict and each check's result (email aside). Two claims with the same
    signature on the same balance get the same explanation."""
    parts = [proof.get("verdict") or "NONE"] + [f"{c['field']}={c['result']}" for c in proof.get("checks") or []
                                                 if c["field"] != "email"]   # email is a soft check
    parts.append(f"flags={len(proof.get('flags') or [])}")
    return "|".join(parts)


def next_step(proof: dict, holder: str, attempts_left: int) -> str:
    """The next step, decided by code. The model only puts it into words."""
    v = proof.get("verdict")
    if v == "APPROVED":
        return "Give the PayPal email address or phone number the money should be sent to."
    if v == "NEEDS_REVIEW":
        missing = proof.get("missing") or []
        step = f"A person at {holder} will look at the claim and decide."
        if missing:
            step += " It may help to add " + "; ".join(missing) + "."
        return step
    if v == "REJECTED":
        if attempts_left > 0:
            return (f"This claim cannot be paid as it stands. {attempts_left} more document "
                    f"{'upload is' if attempts_left == 1 else 'uploads are'} allowed.")
        return "This claim is closed and cannot be paid."
    return "Add the documents."


EXPLAIN_PROMPT = """You write one short message to a person who claimed money that {holder} ({holder_kind}) holds \
for them. Plain software has ALREADY decided the result. You must not change it, question it, or promise anything \
else.

Result: {verdict_words}
What the software compared (field, result, note):
{checks}
Flags: {flags}
Next step (decided by the software; say it, do not change it): {next_step}

Rules:
- Two to four short, plain sentences: what the result is, the main reason, and the next step.
- Do not quote dates of birth, addresses or email addresses.
- If the result is rejected, do not say which document or detail would make it pass, and do not suggest how to \
change a document.
- Write "text" in {language_name}, and "english" as an English version of the same message.

Return JSON: {{"text": "...", "english": "..."}}"""

VERDICT_WORDS = {"APPROVED": "approved: the documents match the account, so it can be paid",
                 "NEEDS_REVIEW": "a person at the business must review it before anything is paid",
                 "REJECTED": "rejected: it cannot be paid",
                 "NONE": "no documents have been checked yet"}

PLAIN = {"APPROVED": "Your documents match the account record, so this claim can be paid.",
         "NEEDS_REVIEW": "Some details could not be matched to the account record, so a person will review the "
                         "claim before anything is paid.",
         "REJECTED": "The documents do not match the account record, so this claim cannot be paid."}


def explain(balance: dict, claim: dict, proof: dict, *, holder: dict, attempts_left: int, saved: Saved,
            ask: Callable[..., dict] | None = None) -> dict | None:
    """The explanation of a verdict code has set: {"language", "text", "english", "source"}."""
    verdict = proof.get("verdict")
    if verdict not in PLAIN:
        return None
    language = ((claim.get("claimant") or {}).get("language") or "en").split("-")[0].lower()
    step = next_step(proof, holder["name"], attempts_left)
    # Nothing that is merely missing is mentioned: for a rejected claim it would be a hint, and the email is only
    # ever a soft check.
    shown = [c for c in proof.get("checks") or []
             if c["result"] != "MISSING" or (verdict == "NEEDS_REVIEW" and c["field"] != "email")]
    checks = "\n".join(f"- {c['field'].replace('_', ' ')}: {c['result']} ({c['note']})" for c in shown) or "- none"
    prompt = EXPLAIN_PROMPT.format(holder=holder["name"], holder_kind=holder["kind"],
                                   verdict_words=VERDICT_WORDS[verdict], checks=checks,
                                   flags="; ".join(proof.get("flags") or []) or "none", next_step=step,
                                   language_name=language_name(language))
    ask = ask or (lambda p: llm.ask_json(p, model=MODEL))
    try:
        if llm.disabled():
            raise llm.ModelUnavailable("off")
        out = ask(prompt)
        text, english = str(out.get("text") or "").strip(), str(out.get("english") or "").strip()
        if text and english:
            return {"language": language, "text": text, "english": english, "source": "LIVE"}
    except Exception:
        pass
    keep = saved.explanation(balance["id"], signature(proof), language)
    if keep:
        return {**keep, "source": "SAVED"}
    english = PLAIN[verdict] + " " + step
    return {"language": "en", "text": english, "english": english, "source": "PLAIN"}


# ---- the notice to an owner ----------------------------------------------------------------------------------

NOTICE_PROMPT = """Draft a short notice from {holder} ({holder_kind}) to one of its sellers whose earnings were never \
collected. Nothing will be sent automatically; a person at {holder} will read it first.

Facts you may use:
- Seller: {owner}
- Earnings waiting: {amount}
- Last activity on the account: {last_activity}
- The seller can claim the money on the Homing page linked in this notice: they search for their name, add a photo \
of an identity document, and the money is paid to their PayPal account. The seller pays nothing.
- Where the link to the Homing page goes, write exactly {{{{HOMING_LINK}}}} (Homing puts its address there).
- If nobody claims it, the money may later have to be handed to the state as unclaimed property.

Rules:
- Write the notice in {language_name}. Also give an English copy.
- Plain and friendly, under 120 words. No threats, no deadlines that are not in the facts.
- Never ask for a password, a card number, a bank login or an identity number.
Return JSON: {{"subject": "...", "body": "...", "english_subject": "...", "english": "..."}}"""


def notice(balance: dict, *, holder: dict, saved: Saved, ask: Callable[..., dict] | None = None) -> dict:
    """{"language", "subject", "body", "english", "english_subject", "source"}. Raises llm.ModelUnavailable with
    NO_MODEL_NOTICE when there is no model and no saved copy."""
    language = language_of(balance.get("last_country") or "")
    amount = (f"${float(balance['amount']):,.2f}" if balance["currency"] == "USD"
              else f"{float(balance['amount']):,.2f} {balance['currency']}")
    prompt = NOTICE_PROMPT.format(holder=holder["name"], holder_kind=holder["kind"], owner=balance["owner_name"],
                                  amount=amount, last_activity=(balance.get("last_activity") or "")[:7],
                                  language_name=language_name(language))
    ask = ask or (lambda p: llm.ask_json(p, model=MODEL))
    try:
        if llm.disabled():
            raise llm.ModelUnavailable("off")
        out = ask(prompt)
        got = {k: str(out.get(k) or "").strip() for k in ("subject", "body", "english_subject", "english")}
        if got["subject"] and got["body"] and got["english"]:
            return {"language": language, **got, "source": "LIVE"}
    except Exception:
        pass
    keep = saved.notice(balance["id"])
    if keep:
        return {**keep, "source": "SAVED"}
    raise llm.ModelUnavailable(NO_MODEL_NOTICE)
