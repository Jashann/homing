"""A claimant's documents: checking what is uploaded, and reading it.

`read_document` shows one photo or PDF to a language model and returns only what is visibly printed on it, plus the
model's concerns and whether it sees signs of editing. The model never sees the account record. Every comparison
with the record, and the verdict, is plain code in verify.py. Identity numbers are never kept: the model is told to
name the kind of number only, and anything that still looks like a number is masked down to its last two
characters before the reading is returned.
"""
from __future__ import annotations

import re
from pathlib import Path

from . import llm

MAX_BYTES = 15 * 1024 * 1024

# Accepted uploads, recognised by their first bytes and not by the file name or the browser's stated type.
_SIGNATURES: list[tuple[bytes, str, str]] = [
    (b"%PDF-", "application/pdf", ".pdf"),
    (b"\xff\xd8\xff", "image/jpeg", ".jpg"),
    (b"\x89PNG\r\n\x1a\n", "image/png", ".png"),
    (b"GIF87a", "image/gif", ".gif"),
    (b"GIF89a", "image/gif", ".gif"),
]


def sniff(data: bytes) -> tuple[str, str] | None:
    """(content type, file extension) for an image or PDF we accept, or None for anything else."""
    for magic, content_type, ext in _SIGNATURES:
        if data.startswith(magic):
            return content_type, ext
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp", ".webp"
    if data[4:8] == b"ftyp" and data[8:12] in (b"heic", b"heix", b"mif1", b"heif"):
        return "image/heic", ".heic"
    if data[:4] in (b"II*\x00", b"MM\x00*"):
        return "image/tiff", ".tif"
    return None


def safe_label(filename: str) -> str:
    """The uploaded file's own name, cut down to something safe to show and store. Never used as a path."""
    name = re.sub(r"[^\w .()\-]+", "_", Path(filename or "").name).strip(" .")
    return name[:80] or "document"


READ_PROMPT = """You are reading one document that a person uploaded to support a claim for money a company \
holds for them.

Look at the file and report only what is visibly printed or written on it. Do not guess, do not complete a \
partly hidden word, and do not use outside knowledge about the person or the organisation. When something cannot \
be read, leave it out and say so in "concerns".

Return JSON with exactly these fields:
- document_type: what the document is, in a few plain words (for example "hospital staff identity badge", \
"payslip", "electricity and water bill", "passport photo page")
- names: every personal name printed on it, each exactly as printed (list of strings)
- addresses: every postal address printed on it, as objects with street, city, region (state or province), \
postcode, country (two-letter code if it is clear, else ""), and whose (one of "person", "issuer", "other"). \
Use "" for a part that is not printed.
- dates: every date printed on it, as objects with label (what the date is, in the document's own words, for \
example "pay date", "expires", "bill date") and value (YYYY-MM-DD when the full date is printed, otherwise as \
printed)
- issuer: the organisation that issued the document (employer, hospital, utility, bank, government office), as \
printed, or ""
- identifiers_present: the KINDS of identity or account numbers that appear, for example "passport number", \
"employee number", "account number", "Social Security number". Name the kind only. Never write the number itself, \
in this field or in any other field.
- legible: true if the main text can be read with confidence, false if it is blurred, too dark, cut off or too small
- concerns: plain sentences for anything a careful clerk would question: signs of editing, text in a different \
font or alignment from its surroundings, cropping that removes part of the document, missing parts, an expiry date \
that has passed, a watermark such as SAMPLE or SPECIMEN, or anything that could not be read. Empty list if none.
- emails: every email address printed on it, exactly as printed (list of strings; empty list if none)
- marked_sample: true if the document is visibly marked SAMPLE, SPECIMEN, VOID or similar
- signs_of_editing: true only if you can see a concrete sign that the document was altered after it was issued
- summary: one or two plain English sentences saying what the document shows. No identity or account numbers."""

# Runs that look like an identity or account number: a Social Security pattern, a long run of digits (with or
# without spaces or dashes between groups), or a long code that is mostly digits.
_DATE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b|\b\d{1,2}[/.]\d{1,2}[/.]\d{2,4}\b")
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_NUMBERISH = re.compile(r"\b\d{3}-\d{2}-\d{4}\b|\b(?:\d[ -]?){8,}\d\b|\b\d{7,}\b|\b[A-Z]{1,6}-?\d{4,}[A-Z0-9]*\b", re.I)


def mask_number(value: str) -> str:
    """'P1234567' -> '······67'. All but the last two characters are hidden."""
    return "·" * max(len(value) - 2, 0) + value[-2:]


def mask_numbers(text: str) -> str:
    """Hides anything in a sentence that looks like an identity or account number. Dates are left alone."""
    kept: list[str] = []

    def keep(m: re.Match) -> str:
        kept.append(m.group())
        return f"\x00{len(kept) - 1}\x00"

    text = _DATE.sub(keep, text or "")
    text = _NUMBERISH.sub(lambda m: mask_number(m.group()), text)
    return re.sub(r"\x00(\d+)\x00", lambda m: kept[int(m.group(1))], text)


def _words(value, limit: int = 200) -> str:
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        return ""
    return " ".join(str(value).split())[:limit]


def _list(value) -> list:
    return value if isinstance(value, list) else [] if value in (None, "") else [value]


def clean_reading(raw: dict) -> dict:
    """The model's answer in a fixed shape, with every number-like run masked. Nothing else is trusted from it."""
    names = [mask_numbers(_words(n, 120)) for n in _list(raw.get("names")) if _words(n)]
    addresses = []
    for a in _list(raw.get("addresses")):
        if isinstance(a, dict):
            address = {k: _words(a.get(k) or "", 120) for k in ("street", "city", "region", "postcode", "country")}
            address["whose"] = _words(a.get("whose") or "", 12).lower()
            if any(address[k] for k in ("street", "city", "postcode")):
                addresses.append(address)
    dates = []
    for d in _list(raw.get("dates")):
        if isinstance(d, dict) and _words(d.get("value")):
            dates.append({"label": _words(d.get("label") or "", 60), "value": _words(d.get("value"), 40)})
        elif _words(d):
            dates.append({"label": "", "value": _words(d, 40)})
    reading = {
        "status": "read",
        "document_type": mask_numbers(_words(raw.get("document_type") or "", 80)),
        "names": names[:10],
        "addresses": addresses[:6],
        "dates": dates[:12],
        "issuer": mask_numbers(_words(raw.get("issuer") or "", 120)),
        "identifiers_present": [mask_numbers(_words(i, 60)) for i in _list(raw.get("identifiers_present"))
                                if _words(i)][:10],
        "legible": raw.get("legible") is not False,
        "concerns": [mask_numbers(_words(c, 300)) for c in _list(raw.get("concerns")) if _words(c)][:10],
        "marked_sample": raw.get("marked_sample") is True,
        "signs_of_editing": raw.get("signs_of_editing") is True,
        "summary": mask_numbers(_words(raw.get("summary") or "", 500)),
        "emails": sorted({e.lower() for e in _EMAIL.findall(" ".join(str(x) for x in _list(raw.get("emails"))))})[:6],
    }
    if not (reading["names"] or reading["addresses"] or reading["issuer"] or reading["dates"]):
        reading["status"] = "unreadable"
        reading["legible"] = False
    return reading


def read_document(path: str | Path, model: str = "sonnet") -> dict:
    """Reads the image or PDF at `path` with a model and returns what is visible on it.

        {
          "status": "read",                  # "unreadable" when nothing could be made out,
                                             # "failed" when the model could not be reached (with "problem"),
                                             # "not_read" when there is no file to read
          "document_type": "payslip",
          "names": ["..."],                  # exactly as printed
          "addresses": [{"street", "city", "region", "postcode", "country", "whose"}],
          "dates": [{"label": "pay date", "value": "2014-03-28"}],
          "issuer": "...", "emails": ["..."],
          "identifiers_present": ["employee number"],   # kinds only, never the number
          "legible": true,
          "concerns": ["..."],               # signs of editing, cropping, missing parts, expiry, sample stamps
          "marked_sample": false, "signs_of_editing": false,
          "summary": "...",
        }

    Raises llm.ModelUnavailable when no model can be reached, so the caller can fall back to a saved reading.
    Callers must treat every key except "status" as optional.
    """
    path = Path(path)
    if not path.is_file():
        return {"status": "not_read"}
    failure: dict = {"status": "failed", "problem": "The document could not be read this time."}
    for _ in range(2):   # a reading that fails for a passing reason is tried once more
        try:
            raw = llm.ask_json(READ_PROMPT, files=[str(path)], model=model)
        except llm.ModelUnavailable:
            raise
        except Exception:   # the answer was not JSON, or the model program stopped
            continue
        if isinstance(raw, dict):
            return clean_reading(raw)
    return failure


def make_thumbnail(source: Path, target: Path, width: int = 480) -> bool:
    """A small PNG of an uploaded photo for the interface. PDFs and unreadable images get none (False)."""
    try:
        from PIL import Image
        with Image.open(source) as im:
            im = im.convert("RGB")
            im.thumbnail((width, width * 2))
            im.save(target, "PNG", optimize=True)
        return True
    except Exception:
        return False
