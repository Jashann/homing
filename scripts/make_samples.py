"""Makes the documents for the sample claims under data/samples/ (PNG images drawn with Pillow).

    uv run python scripts/make_samples.py

clean/     Tomasz Wiśniewski claims B001: identity card and an old statement at the Kraków address -> APPROVED
partial/   Ana Brennan (once Ana Kowalski) claims B002: identity card in her married name, a bill at her new
           Dublin address, and a marriage certificate linking Ana Kowalski to Ana Brennan. The name then matches
           through the certificate, but nothing shows the Chicago address -> NEEDS_REVIEW (a person decides)
impostor/  A different Wei Zhang (Vancouver) claims B003, the Toronto Wei Zhang's balance: the date of birth
           and the address on his card are not the account's -> REJECTED
edited/    Someone claims B007 (Mohammed Al-Rashid) with an identity card whose name was pasted in: a different
           typeface on a patch that is slightly out of line. The details match the account, but the model reports
           signs of editing, and code rejects it -> REJECTED
uncollected/  J. R. Okafor claims B006: identity card and an old statement at the Lagos address -> APPROVED. Used
           to show a payout to an address nobody collects from, which PayPal then holds

Every document is for an invented person, issued by an invented organisation, and carries a DEMO DOCUMENT
footer. None imitates a real government's or company's design.
"""
from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "samples"
FONTS = ROOT / "src" / "homing" / "fonts"

INK, SOFT, LINE, PAPER, BAND = (24, 32, 52), (92, 102, 128), (205, 210, 222), (250, 248, 242), (34, 66, 120)


def font(size: int, bold: bool = False):
    return ImageFont.truetype(str(FONTS / ("NotoSans-Bold.ttf" if bold else "NotoSans-Regular.ttf")), size)


def _footer(d: ImageDraw.ImageDraw, w: int, h: int) -> None:
    d.text((w // 2, h - 28), "DEMO DOCUMENT · INVENTED PERSON · NOT VALID FOR ANY PURPOSE", fill=SOFT,
           font=font(18, True), anchor="mm")


def id_card(path: Path, *, issuer: str, name: str, born: str, expires: str, number_kind: str, number: str,
            address: list[str] | None = None) -> None:
    w, h = 1100, 700
    im = Image.new("RGB", (w, h), PAPER)
    d = ImageDraw.Draw(im)
    d.rounded_rectangle((10, 10, w - 10, h - 10), radius=28, outline=LINE, width=4)
    d.rectangle((14, 14, w - 14, 110), fill=BAND)
    d.text((50, 62), issuer, fill=(255, 255, 255), font=font(36, True), anchor="lm")
    d.text((w - 50, 62), "IDENTITY CARD", fill=(255, 255, 255), font=font(26, True), anchor="rm")
    d.rounded_rectangle((50, 150, 320, 480), radius=16, fill=(222, 226, 236))
    d.ellipse((125, 190, 245, 310), fill=(180, 188, 205))
    d.rounded_rectangle((95, 320, 275, 470), radius=60, fill=(180, 188, 205))
    rows = [("Name", name), ("Date of birth", born), ("Expires", expires), (number_kind, number)]
    if address:
        rows.append(("Address", address[0]))
    y = 160
    for label, value in rows:
        d.text((370, y), label.upper(), fill=SOFT, font=font(20, True))
        d.text((370, y + 26), value, fill=INK, font=font(34, True if label == "Name" else False))
        y += 78
    for line in (address or [])[1:]:
        d.text((370, y - 10), line, fill=INK, font=font(34))
        y += 44
    _footer(d, w, h)
    im.save(path)


def edited_id_card(path: Path, *, issuer: str, real_name: str, pasted_name: str, born: str, expires: str,
                   number_kind: str, number: str, address: list[str]) -> None:
    """An identity card where the name was covered with a pasted patch: another typeface, scaled up from a small
    image (so it is blurred and blocky), on a slightly lighter background, a few pixels out of line and tilted."""
    id_card(path, issuer=issuer, name=real_name, born=born, expires=expires, number_kind=number_kind, number=number,
            address=address)
    im = Image.open(path).convert("RGB")
    small = Image.new("RGB", (200, 15), (255, 255, 252))
    ImageDraw.Draw(small).text((2, 0), pasted_name, fill=(10, 10, 10), font=ImageFont.load_default(14))
    patch = small.resize((small.width * 3, small.height * 3), Image.NEAREST).rotate(0.8, expand=True,
                                                                                   fillcolor=(255, 255, 252))
    ImageDraw.Draw(patch).rectangle((0, 0, patch.width - 1, patch.height - 1), outline=(226, 224, 214), width=2)
    im.paste(patch, (378, 181))
    im.save(path)


def letter(path: Path, *, issuer: str, issuer_address: str, title: str, to: list[str], date_label: str,
           date_value: str, body: list[tuple[str, str]], email: str | None = None) -> None:
    w, h = 1240, 1600
    im = Image.new("RGB", (w, h), (255, 255, 255))
    d = ImageDraw.Draw(im)
    d.rectangle((0, 0, w, 140), fill=BAND)
    d.text((70, 58), issuer, fill=(255, 255, 255), font=font(44, True), anchor="lm")
    d.text((70, 108), issuer_address, fill=(220, 228, 245), font=font(22), anchor="lm")
    y = 220
    for i, line in enumerate(to):
        d.text((70, y), line, fill=INK, font=font(30, i == 0))
        y += 42
    if email:
        d.text((70, y), email, fill=INK, font=font(28))
        y += 42
    d.text((w - 70, 220), f"{date_label}: {date_value}", fill=INK, font=font(28), anchor="ra")
    y += 60
    d.text((70, y), title, fill=INK, font=font(40, True))
    y += 80
    d.line((70, y, w - 70, y), fill=LINE, width=3)
    y += 30
    for label, value in body:
        d.text((70, y), label, fill=INK, font=font(28))
        d.text((w - 70, y), value, fill=INK, font=font(28), anchor="ra")
        y += 52
        d.line((70, y - 10, w - 70, y - 10), fill=LINE, width=1)
    _footer(d, w, h)
    im.save(path)


def build(out: Path = OUT) -> list[Path]:
    made: list[Path] = []
    for case in ("clean", "partial", "impostor", "edited", "uncollected"):
        (out / case).mkdir(parents=True, exist_ok=True)

    p = out / "clean" / "id_card.png"
    id_card(p, issuer="Demo Identity Office", name="Tomasz Wiśniewski", born="1979-03-14", expires="2031-05-01",
            number_kind="Card number", number="DEM 482915")
    made.append(p)
    p = out / "clean" / "old_statement.png"
    letter(p, issuer="Vistula Demo Savings", issuer_address="Rynek Główny 1, 31-042 Kraków, Poland",
           title="Account statement", to=["Tomasz Wiśniewski", "ul. Długa 12", "31-147 Kraków", "Poland"],
           email="t.wisniewski@example.com", date_label="Statement date", date_value="2019-05-31",
           body=[("Opening balance", "PLN 1,204.18"), ("Card payments", "PLN -388.40"),
                 ("Transfer received", "PLN 2,950.00"), ("Closing balance", "PLN 3,765.78")])
    made.append(p)

    p = out / "partial" / "id_card.png"
    id_card(p, issuer="Demo Identity Office", name="Ana Brennan", born="1988-11-02", expires="2030-08-19",
            number_kind="Card number", number="DEM 771204")
    made.append(p)
    p = out / "partial" / "utility_bill.png"
    letter(p, issuer="Liffey Demo Energy", issuer_address="1 Grand Canal Quay, Dublin 2, Ireland",
           title="Electricity bill", to=["Ana Brennan", "14 Pembroke Road", "Dublin 4, D04 X2R7", "Ireland"],
           date_label="Bill date", date_value="2026-10-03",
           body=[("Previous balance", "EUR 0.00"), ("Electricity used, 412 kWh", "EUR 118.62"),
                 ("Standing charge", "EUR 21.40"), ("Amount due", "EUR 140.02")])
    made.append(p)

    p = out / "partial" / "marriage_certificate.png"
    letter(p, issuer="Demo Civil Registry", issuer_address="Registry Office, 1 Demo Plaza, Chicago IL",
           title="Certificate of marriage", to=["Ana Kowalski", "and", "Liam Brennan"],
           date_label="Date of marriage", date_value="2019-06-15",
           body=[("Bride, name before marriage", "Ana Kowalski"), ("Bride, name after marriage", "Ana Brennan"),
                 ("Groom", "Liam Brennan"), ("Place of marriage", "Chicago, Illinois, United States"),
                 ("Registered", "2019-06-17")])
    made.append(p)

    p = out / "edited" / "id_card.png"
    edited_id_card(p, issuer="Demo Identity Office", real_name="Samir Haddad", pasted_name="Mohammed Al-Rashid",
                   born="1981-02-25", expires="2031-09-30", number_kind="Card number", number="DEM 556120",
                   address=["Al Wasl Rd 220", "Dubai"])
    made.append(p)

    p = out / "impostor" / "id_card.png"
    id_card(p, issuer="Demo Identity Office", name="Wei Zhang", born="1992-01-09", expires="2032-02-11",
            number_kind="Card number", number="DEM 309877",
            address=["1450 W 7th Ave", "Vancouver BC V6H 1C1"])
    made.append(p)

    p = out / "uncollected" / "id_card.png"
    id_card(p, issuer="Demo Identity Office", name="J. R. Okafor", born="1976-09-12", expires="2032-04-30",
            number_kind="Card number", number="DEM 640518")
    made.append(p)
    p = out / "uncollected" / "old_statement.png"
    letter(p, issuer="Lagoon Demo Bank", issuer_address="2 Marina Road, Lagos Island, Nigeria",
           title="Account statement", to=["J. R. Okafor", "14 Awolowo Rd", "Ikoyi, Lagos 101233", "Nigeria"],
           email="jr.okafor@example.org", date_label="Statement date", date_value="2016-06-30",
           body=[("Opening balance", "NGN 84,210.00"), ("Card payments", "NGN -12,400.00"),
                 ("Transfer received", "NGN 150,000.00"), ("Closing balance", "NGN 221,810.00")])
    made.append(p)
    return made


def main() -> None:
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else OUT
    for p in build(out):
        print("wrote", p)


if __name__ == "__main__":
    main()
