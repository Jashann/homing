"""The claim packet: a PDF prepared for the owner to review, sign and send themselves. Nothing here files a claim.

The packet carries the record in its public form (a private owner's name is masked and there is no street) and
the owner's own details as they typed them.
"""
from __future__ import annotations

import io
import os
from datetime import datetime, timezone
from pathlib import Path
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (BaseDocTemplate, Frame, KeepTogether, PageBreak, PageTemplate, Paragraph, Spacer,
                                Table, TableStyle)

from .countries import COUNTRIES

BANNER = "Claim packet prepared for the owner to review, sign and send; not filed"

NAVY = colors.HexColor("#0a1326")
INK = colors.HexColor("#1b2438")
SOFT = colors.HexColor("#5b6680")
LINE = colors.HexColor("#d5d9e2")
PAPER = colors.HexColor("#f6f3ea")
AMBER = colors.HexColor("#b9770e")
GOLD = colors.HexColor("#f2b84b")
GREEN = colors.HexColor("#1f7a45")

_base = dict(fontName="Helvetica", textColor=INK, leading=14.5, fontSize=10.5)
S = {
    "body": ParagraphStyle("body", **_base),
    "small": ParagraphStyle("small", **{**_base, "fontSize": 9, "leading": 12.5, "textColor": SOFT}),
    "label": ParagraphStyle("label", **{**_base, "fontSize": 8.5, "leading": 11, "textColor": SOFT}),
    "value": ParagraphStyle("value", **{**_base, "fontSize": 11, "leading": 15}),
    "h1": ParagraphStyle("h1", **{**_base, "fontName": "Helvetica-Bold", "fontSize": 17, "leading": 21,
                                  "textColor": NAVY, "spaceAfter": 6}),
    "h2": ParagraphStyle("h2", **{**_base, "fontName": "Helvetica-Bold", "fontSize": 11.5, "leading": 15,
                                  "textColor": NAVY}),
    "cover_mark": ParagraphStyle("cover_mark", **{**_base, "fontName": "Helvetica-Bold", "fontSize": 15,
                                                  "textColor": NAVY}),
    "cover_title": ParagraphStyle("cover_title", **{**_base, "fontName": "Helvetica-Bold", "fontSize": 30,
                                                    "leading": 35, "textColor": NAVY}),
    "cover_sub": ParagraphStyle("cover_sub", **{**_base, "fontSize": 13, "leading": 18, "textColor": SOFT}),
    "cover_amount": ParagraphStyle("cover_amount", **{**_base, "fontName": "Helvetica-Bold", "fontSize": 40,
                                                      "leading": 46, "textColor": AMBER}),
    "status_ok": ParagraphStyle("status_ok", **{**_base, "fontName": "Helvetica-Bold", "fontSize": 9.5,
                                                "textColor": GREEN}),
    "status_need": ParagraphStyle("status_need", **{**_base, "fontName": "Helvetica-Bold", "fontSize": 9.5,
                                                    "textColor": AMBER}),
    "right": ParagraphStyle("right", **{**_base, "alignment": TA_RIGHT}),
}


# ---- fonts -----------------------------------------------------------------------------------------------------
# Text that the PDF's built-in Helvetica can draw (Western European letters) is set in Helvetica. Anything else,
# such as "Nguyễn", "Łukasz", "Марія" or "Ελένη", is set in Noto Sans, which is bundled in src/homing/fonts with
# its licence (SIL Open Font License) and embedded in the PDF. Noto Sans covers Latin, Greek and Cyrillic. For
# other scripts (Chinese, Japanese, Korean and more) a wide font from the machine is embedded when one is found:
# HOMING_WIDE_FONT names a .ttf file, else the usual places are tried. Scripts that need letters joined or
# reordered (Arabic, Hebrew, the Indic scripts) are not shaped by this PDF library and will not print correctly.
FONT_DIR = Path(__file__).resolve().parent / "fonts"
_WIDE_PLACES = ["/Library/Fonts/Arial Unicode.ttf", "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
                "/usr/share/fonts/truetype/unifont/unifont.ttf", "C:/Windows/Fonts/ARIALUNI.TTF"]
_fonts: dict[str, set[int]] = {}


def _load_fonts() -> dict[str, set[int]]:
    """Registers the embedded fonts once and returns, for each, the characters it can draw."""
    if _fonts:
        return _fonts
    for name, file in (("HomingSans", "NotoSans-Regular.ttf"), ("HomingSans-Bold", "NotoSans-Bold.ttf")):
        font = TTFont(name, str(FONT_DIR / file))
        pdfmetrics.registerFont(font)
        _fonts[name] = set(font.face.charToGlyph)
    for place in [os.environ.get("HOMING_WIDE_FONT") or ""] + _WIDE_PLACES:
        if place and os.path.isfile(place):
            try:
                font = TTFont("HomingWide", place)
                pdfmetrics.registerFont(font)
                _fonts["HomingWide"] = set(font.face.charToGlyph)
                break
            except Exception:
                continue
    return _fonts


def _text(value, bold: bool = False) -> str:
    """Safe for a Paragraph, with the font switched for any text Helvetica cannot draw."""
    text = str(value if value not in (None, "") else "Not stated")
    try:
        text.encode("cp1252")
        return escape(text)
    except UnicodeEncodeError:
        pass
    fonts = _load_fonts()
    main = "HomingSans-Bold" if bold else "HomingSans"
    runs: list[tuple[str, str]] = []
    for ch in text:
        if ch.isspace() or ord(ch) in fonts[main]:
            font = main
        elif ord(ch) in fonts.get("HomingWide", ()):
            font = "HomingWide"
        else:
            font, ch = main, "?"
        if runs and runs[-1][0] == font:
            runs[-1] = (font, runs[-1][1] + ch)
        else:
            runs.append((font, ch))
    return "".join(f'<font name="{font}">{escape(part)}</font>' for font, part in runs)


def _p(value, style: str = "body") -> Paragraph:
    return Paragraph(_text(value, bold=S[style].fontName.endswith("Bold")), S[style])


def _money(amount, currency: str = "USD") -> str:
    return f"{'US$' if currency == 'USD' else currency + ' '}{float(amount or 0):,.2f}"


def _plain_type(raw: str) -> str:
    text = (raw or "").split(":", 1)[-1].strip()
    if text.lower().startswith("non standard naupa code for"):
        text = text.split("-", 1)[-1].strip()
    return text.capitalize() if text.isupper() else text


def _facts(rows: list[tuple[str, str]]) -> Table:
    table = Table([[_p(label, "label"), _p(value, "value")] for label, value in rows], colWidths=[52 * mm, 118 * mm])
    table.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LINEBELOW", (0, 0), (-1, -2), 0.5, LINE),
                               ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                               ("LEFTPADDING", (0, 0), (-1, -1), 0)]))
    return table


def _box(flowables: list, background=PAPER, border=LINE) -> Table:
    table = Table([[flowables]], colWidths=[170 * mm])
    table.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), background), ("BOX", (0, 0), (-1, -1), 0.75, border),
                               ("LEFTPADDING", (0, 0), (-1, -1), 12), ("RIGHTPADDING", (0, 0), (-1, -1), 12),
                               ("TOPPADDING", (0, 0), (-1, -1), 10), ("BOTTOMPADDING", (0, 0), (-1, -1), 10)]))
    return table


def build(claim: dict, need: dict, compress: bool = True) -> bytes:
    """The whole packet as PDF bytes. `need` is `stateclaim.checklist(claim)`. `compress=False` leaves the page text
    readable in the file, which the tests use to check what the packet says."""
    record, claimant = claim["record"], claim["claimant"]
    made = datetime.now(timezone.utc)
    made_text = f"{made.day} {made.strftime('%B %Y, %H:%M')} UTC"
    country = COUNTRIES.get(claimant.get("country") or "", {}).get("name") or claimant.get("country")
    organisation = bool(record.get("owner_is_organisation"))
    amount = _money(record.get("amount"), record.get("currency") or "USD")
    sample = record.get("source") == "SAMPLE"   # a made-up record in the sample ledger, never a real property
    banner = BANNER + ("; sample record, not a real property" if sample else "")
    number_label = "Sample record number" if sample else "California property number"

    def page(canvas, doc):
        width, height = A4
        canvas.saveState()
        canvas.setFillColor(GOLD)
        canvas.rect(0, height - 11 * mm, width, 11 * mm, stroke=0, fill=1)
        canvas.setFillColor(NAVY)
        canvas.setFont("Helvetica-Bold", 9.5)
        canvas.drawCentredString(width / 2, height - 7.2 * mm, banner.upper())
        canvas.setFillColor(SOFT)
        canvas.setFont("Helvetica", 8)
        canvas.drawString(20 * mm, 11 * mm, f"Homing claim {claim['id']}   |   prepared {made_text}   |   not filed")
        canvas.drawRightString(width - 20 * mm, 11 * mm, f"Page {doc.page}")
        canvas.restoreState()

    buffer = io.BytesIO()
    doc = BaseDocTemplate(buffer, pagesize=A4, leftMargin=20 * mm, rightMargin=20 * mm, topMargin=22 * mm,
                          bottomMargin=20 * mm, title=f"Homing claim packet {claim['id']}", author="Homing",
                          subject=BANNER, pageCompression=1 if compress else 0)
    doc.addPageTemplates(PageTemplate(id="all", onPage=page, frames=[
        Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height, id="main", leftPadding=0, rightPadding=0)]))
    story: list = []

    # ---- cover sheet -------------------------------------------------------------------------------------
    story += [Spacer(1, 10 * mm), _p("Homing", "cover_mark"), Spacer(1, 22 * mm), _p("Claim packet", "cover_title"),
              Spacer(1, 3 * mm),
              _p("Sample. A made-up record in Homing's sample ledger, prepared under the California State "
                 "Controller's rules. No such property exists." if sample else
                 "Unclaimed property held by the California State Controller", "cover_sub"), Spacer(1, 14 * mm),
              _p("Amount on the sample record" if sample else "Amount on the public record", "label"), _p(amount, "cover_amount"), Spacer(1, 10 * mm),
              _facts([("Claimant", claimant["name"]),
                      ("Claiming as", "A person signing for the organisation that owns it" if organisation
                       else "The owner"),
                      ("Reported by", record.get("holder_name")),
                      (number_label, record.get("record_id")),
                      ("Homing claim number", claim["id"]),
                      ("Prepared", made_text),
                      ("Documents California asks for", f"{need['required']} required, listed in the checklist")]),
              Spacer(1, 10 * mm),
              _box([_p("This packet has not been filed", "h2"), Spacer(1, 3),
                    _p("Homing prepared it for you to review, sign and send. It has not been sent to California "
                       "or to anyone else, and Homing never signs for anyone. You create the claim on "
                       "California's own website, sign California's Claim Affirmation Form, and send it with the "
                       "documents listed inside.")]),
              Spacer(1, 6 * mm),
              _p("Inside: the property details from the record, your details as you typed them, the checklist of "
                 "documents California asks for in this case, a signing and notary guide, and where to send the "
                 "claim.", "small"),
              PageBreak()]

    # ---- property and claimant ---------------------------------------------------------------------------
    place = ", ".join(x for x in (record.get("owner_city"), record.get("owner_region"), record.get("owner_country")) if x)
    story += [_p("Property details", "h1"),
              _p("Taken from Homing's sample ledger of made-up records. The owner, the business that held the money and the amount "
                 "are invented." if sample else
                 "Taken from the public file the California State Controller publishes.", "small"),
              Spacer(1, 4 * mm),
              _facts([(number_label, record.get("record_id")),
                      ("Amount", amount),
                      ("Kind of money", _plain_type(record.get("property_type"))),
                      ("Reported by", record.get("holder_name")),
                      ("Owner as shown here", record.get("owner")),
                      ("Last known place", place)])]
    if not organisation:
        story += [Spacer(1, 2 * mm),
                  _p("Homing shows only the first letter of each word of a private owner's name, and no street. "
                     "California's own form will show the name in full once the claim is created.", "small")]
    story += [Spacer(1, 9 * mm), _p("Claimant's details", "h1"),
              _p("As you typed them. Check them against your identification before you sign.", "small"),
              Spacer(1, 4 * mm),
              _facts([("Name used now", claimant["name"]),
                      ("Current address", claimant["address"]),
                      ("Country", country),
                      ("Email", claimant["email"]),
                      ("Relationship to the owner", "Signs for the organisation" if organisation
                       else "The owner (self)")]),
              PageBreak()]

    # ---- checklist ---------------------------------------------------------------------------------------
    story += [_p("Document checklist", "h1"),
              _p("What California asks for in this case. Add a copy of each document when you send the signed "
                 "claim.", "body"),
              Spacer(1, 5 * mm)]
    rows = [[_p("Needed", "label"), _p("Document", "label"), _p("What California asks for", "label")]]
    for item in need["items"]:
        status = _p("Required" if item["required"] else "If you have it",
                    "status_need" if item["required"] else "small")
        rows.append([status, [_p(item["title"], "h2")], _p(item["detail"], "small")])
    table = Table(rows, colWidths=[26 * mm, 52 * mm, 92 * mm], repeatRows=1)
    table.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LINEBELOW", (0, 0), (-1, -1), 0.5, LINE),
                               ("LINEABOVE", (0, 0), (-1, 0), 0.5, LINE), ("BACKGROUND", (0, 0), (-1, 0), PAPER),
                               ("TOPPADDING", (0, 0), (-1, -1), 7), ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
                               ("LEFTPADDING", (0, 0), (-1, -1), 5), ("RIGHTPADDING", (0, 0), (-1, -1), 5)]))
    story += [table, PageBreak()]

    # ---- signing and notary guide ------------------------------------------------------------------------
    story += [_p("How to sign and send this claim", "h1"),
              _p("Written for a claimant living outside the United States." if (claimant.get("country") or "US") != "US"
                 else "Written for the claimant.", "small"),
              Spacer(1, 4 * mm)]
    for number, step in enumerate(need["signing"], 1):
        story.append(KeepTogether([_p(f"{number}. {step['title']}", "h2"), Spacer(1, 2), _p(step["detail"]),
                                   Spacer(1, 4 * mm)]))
    story.append(KeepTogether([
        _p("How California pays", "h1"), *[x for line in need["payment"] for x in (_p(line), Spacer(1, 2 * mm))],
        Spacer(1, 4 * mm)]))
    story.append(KeepTogether([
        _p("Where to send the signed claim", "h1"), _box([_p(line, "value") for line in need["mail_to"]]),
        Spacer(1, 6 * mm)]))
    story.append(KeepTogether([
        _p("Where these rules come from", "h2"), Spacer(1, 2),
        _p("Read on the California State Controller's own pages on 11 November 2026. Rules change; the "
           "Controller's pages are the authority.", "small"), Spacer(1, 2),
        *[Paragraph(f"{_text(s['title'])}<br/><font color='#5b6680'>{_text(s['url'])}</font>", S["small"])
          for s in need["sources"]]]))

    doc.build(story)
    return buffer.getvalue()
