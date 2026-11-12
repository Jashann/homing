"""What the California State Controller asks for when someone claims unclaimed property.

Every rule here was read on the Controller's own pages (listed in SOURCES) on 11 Nov 2026. Where this module goes
beyond the Controller's words (which property types count as securities) the comment says so. Homing uses it for
one thing: the claim packet it prepares for an owner whose balance was already handed to the state, for the owner
to review, sign and send.
"""
from __future__ import annotations

import re

NOTARY_THRESHOLD_USD = 1000.0   # "The total value of the claim is $1,000 or greater" (2 CCR 1180.60)

SOURCES = {
    "owner": ("Claim filing instructions for property owners (State Controller, rev. 4/6/2022)",
              "https://www.sco.ca.gov/Files-UPD/02eClaimFilingOwnerInstructions.pdf"),
    "business": ("Claim filing instructions for businesses (State Controller, rev. 1/11/2018)",
                 "https://sco.ca.gov/Files-UPD/02gClaimFilingBusinessInstructions.pdf"),
    "regulation": ("Claim regulations, California Code of Regulations title 2, sections 1180.59 to 1180.65",
                   "https://sco.ca.gov/Files-UPD/02cClaimFilingAmendedRegLanguageJune5.pdf"),
    "faq": ("Claim questions and answers (claimit.ca.gov)", "https://claimit.ca.gov/app/faq-claim"),
    "payment": ("Payment questions and answers (State Controller)", "https://www.sco.ca.gov/upd_faq_payment.html"),
    "handbook": ("Investigator handbook (State Controller, January 2023)",
                 "https://www.sco.ca.gov/files-upd/guide_investigator_handbook.pdf"),
}

MAIL_TO = ["State Controller's Office", "Unclaimed Property Division", "P.O. Box 942850",
           "Sacramento, CA 94250-5873", "United States of America"]
PHONE_OUTSIDE_US = "+1 916 323 2827"

# Our reading, not the Controller's list: the state file's property types that are shares or other securities
# rather than cash. The Controller's rule says only "the claim is for securities".
_SECURITIES = re.compile(
    r"SHARES OF STOCK|UNDERLYING SHARES|UNEXCH STOCK|CERT OF OWNERSHIP|MUTUAL FUND SHARES|- SECURITIES$|"
    r"SHARES EXCHANGED FOR SHARES|WARRANTS \(RIGHTS\)|- WARRANTS$|- RIGHTS$|- UNITS$|DIVIDEND REINVESTMENT|"
    r"IRA - (MUTUAL FUNDS|SECURITIES)", re.I)
_SAFE_DEPOSIT = re.compile(r"SAFE.?DEPOSIT|SAFEKEEPING|TANGIBLE PROPERTY", re.I)


def notary_rule(amount: float, property_type: str) -> dict:
    """Whether the Claim Affirmation Form must be notarised, and the reason in one sentence."""
    kind = (property_type or "").strip()
    if _SAFE_DEPOSIT.search(kind):
        why = "California requires a notarised signature on every claim for safe deposit box contents."
    elif _SECURITIES.search(kind):
        why = "California requires a notarised signature on every claim for securities."
    elif amount >= NOTARY_THRESHOLD_USD:
        why = "California requires a notarised signature when the claim is worth US$1,000 or more."
    else:
        return {"required": False, "title": "You do not need a notary for this claim",
                "detail": "This claim is for cash under US$1,000, so California does not ask for a notarised "
                          "signature. You sign the Claim Affirmation Form yourself and that is enough."}
    return {"required": True, "title": "Your signature must be notarised", "detail": why}


def _doc(key: str, title: str, detail: str, required: bool = True) -> dict:
    return {"key": key, "title": title, "detail": detail, "required": required}


def requirements(*, amount: float, claimant_country: str, owner_is_organisation: bool, property_type: str) -> dict:
    """The documents, the signing steps and the payment facts for an owner's own claim (a person, or someone who
    signs for the organisation that owns the money)."""
    abroad = (claimant_country or "").upper() not in ("", "US")
    business = owner_is_organisation
    passport = ("A colour copy of the first and last page of your valid passport. If you have no passport, "
                "California asks for two other government identity documents, with a certified English "
                "translation of any that are not in English.")
    docs: list[dict] = []

    if business:
        docs.append(_doc("photo_id", "Photo identification of the person signing",
                         "The signer must be an officer, general partner, sole proprietor, or an employee given "
                         "signing authority by an officer. " + (passport if abroad else
                         "A copy of a current driver licence or state identity card, or a passport.")))
        docs.append(_doc("authority", "Proof that the signer may sign for the organisation",
                         "For a corporation: a corporate resolution or the latest Statement of Information, plus a "
                         "letter of authorisation if the signer is an employee. For other kinds of organisation: "
                         "the operating agreement, partnership agreement or business licence."))
        docs.append(_doc("tax_number", "Proof of the organisation's federal employer identification number",
                         "An IRS confirmation letter, or a form 1098 or 1099. California does not accept a tax "
                         "return, form SS-4 or form W-9 for this."))
        docs.append(_doc("current_address", "Proof of the organisation's current mailing address",
                         "A recent bill, bank statement or tax form that shows the organisation's name and the "
                         "address the payment should be mailed to."))
    else:
        docs.append(_doc("photo_id", "Your photo identification",
                         passport if abroad else "A copy of your current driver licence or state identity card. A "
                         "military identity card or a colour copy of the first and last page of your passport is "
                         "also accepted."))
        docs.append(_doc("tax_number", "Your Social Security number, or form W-8BEN if you have none",
                         "A Social Security card, or a payslip or form W-2 that shows the full number, or an ITIN "
                         "letter. If you are a foreign citizen with no Social Security number and no ITIN, "
                         "California asks instead for a completed and signed IRS form W-8BEN together with the "
                         "passport copy."))
        docs.append(_doc("current_address", "Proof of your current mailing address",
                         "One of: a payslip, a tax form, a mortgage statement or property tax bill, a telephone, "
                         "cable or utility bill, or a bank or credit card statement. California asks for the "
                         "original of a bill or statement. The payment is mailed to this address."))

    # Asked for but not blocking: the instructions say the Controller may contact the claimant if it is missing.
    docs.append(_doc("reported_address", "Proof of a link to the address on the record",
                     "Something that shows " + ("the organisation" if business else "you")
                     + " received mail at the address the money was reported under: an old payslip, bill, bank "
                     "statement or lease from that address, or a statement from the company that reported the "
                     "money. If you have none, California may contact you for other information.",
                     required=False))

    notary = notary_rule(amount, property_type)
    signing = [
        {"title": "Start the claim on California's own website to get the official form",
         "detail": "California does not give out blank claim forms. You search for the property on "
                   "claimit.ca.gov, choose Claim, and the site creates a claim number and a Claim Affirmation "
                   "Form in your name. Homing does not do this step for you and has not done it. Use the "
                   "property details in this packet to find the right record."},
        {"title": "Check the form against this packet",
         "detail": "The property number, the amount and the company that reported the money should match the "
                   "property details page. Write your name and current address exactly as they appear on your "
                   "identification and your proof of address."},
        {"title": "Sign it yourself",
         "detail": "Only you may sign. California in general does not accept a signature made under a power "
                   "of attorney, and Homing never signs for anyone."},
    ]
    if notary["required"]:
        detail = notary["detail"] + " Do not sign until you are in front of the notary."
        if abroad:
            detail += (" The form's notary section is written for a notary in the United States. The "
                       "Controller's pages do not say which notaries outside the United States it accepts, so "
                       f"ask the Controller before you pay for one: {PHONE_OUTSIDE_US} is its number for callers "
                       "outside the United States. The routes people abroad commonly use are a notarial "
                       "appointment at a United States embassy or consulate, or a notary in your own country "
                       "with an apostille. Neither is confirmed on the Controller's pages.")
        signing.append({"title": "Have your signature notarised", "detail": detail})
    else:
        signing.append({"title": notary["title"], "detail": notary["detail"]})
    signing += [
        {"title": "Translate what is not in English",
         "detail": "California asks for a certified English translation of any identity document in another "
                   "language, and may ask you to have foreign documents authenticated."},
        {"title": "Send the signed form and the documents yourself",
         "detail": "Mail them to the address on the last page of this packet, or use the upload link California "
                   "gives you with the claim number. Keep a copy of everything and note the claim number."},
        {"title": "Wait for California's answer",
         "detail": "California says a cash claim by the owner can take as little as 30 to 60 days, and that "
                   "claims by businesses generally take up to 180 days from the day it has every document."},
    ]
    payment = [
        "California pays by a paper cheque, which it calls a warrant, mailed to the address on the claim form. "
        "It states that there is no direct deposit.",
        "It can mail the cheque to another country, and says it may ask for more documents when it does.",
        "It pays no interest and withholds no tax. A debt to a California state or local agency can be taken "
        "out of the payment first.",
        "A State Controller's cheque is valid for one year from the day it is issued.",
    ]
    used = ["business" if business else "owner", "regulation", "faq", "payment"]
    if abroad:
        used.append("handbook")
    return {"documents": docs, "notary": notary, "signing": signing, "payment": payment, "mail_to": MAIL_TO,
            "sources": [{"title": SOURCES[k][0], "url": SOURCES[k][1]} for k in used]}
