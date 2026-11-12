"""Country names (ISO alpha-2 codes) for the claim packet, and the language a notice to an owner is written in.

LANGUAGES is a guess at the language an owner in each country most likely reads, used only to draft the business's
notice (the notice always carries an English copy). Countries not listed get English.
"""
from __future__ import annotations

_TABLE = """
AD Andorra
AE United Arab Emirates
AF Afghanistan
AG Antigua and Barbuda
AI Anguilla
AL Albania
AM Armenia
AO Angola
AR Argentina
AS American Samoa
AT Austria
AU Australia
AW Aruba
AZ Azerbaijan
BA Bosnia and Herzegovina
BB Barbados
BD Bangladesh
BE Belgium
BF Burkina Faso
BG Bulgaria
BH Bahrain
BI Burundi
BJ Benin
BM Bermuda
BN Brunei
BO Bolivia
BR Brazil
BS Bahamas
BT Bhutan
BW Botswana
BY Belarus
BZ Belize
CA Canada
CD Democratic Republic of the Congo
CF Central African Republic
CG Republic of the Congo
CH Switzerland
CI Ivory Coast
CL Chile
CM Cameroon
CN China
CO Colombia
CR Costa Rica
CU Cuba
CV Cape Verde
CW Curacao
CX Christmas Island
CY Cyprus
CZ Czechia
DE Germany
DJ Djibouti
DK Denmark
DM Dominica
DO Dominican Republic
DZ Algeria
EC Ecuador
EE Estonia
EG Egypt
ER Eritrea
ES Spain
ET Ethiopia
FI Finland
FJ Fiji
FM Micronesia
FO Faroe Islands
FR France
GA Gabon
GB United Kingdom
GD Grenada
GE Georgia
GF French Guiana
GG Guernsey
GH Ghana
GI Gibraltar
GL Greenland
GM Gambia
GN Guinea
GP Guadeloupe
GQ Equatorial Guinea
GR Greece
GT Guatemala
GU Guam
GW Guinea-Bissau
GY Guyana
HK Hong Kong
HN Honduras
HR Croatia
HT Haiti
HU Hungary
ID Indonesia
IE Ireland
IL Israel
IM Isle of Man
IN India
IO British Indian Ocean Territory
IQ Iraq
IR Iran
IS Iceland
IT Italy
JE Jersey
JM Jamaica
JO Jordan
JP Japan
KE Kenya
KG Kyrgyzstan
KH Cambodia
KI Kiribati
KM Comoros
KN Saint Kitts and Nevis
KP North Korea
KR South Korea
KW Kuwait
KY Cayman Islands
KZ Kazakhstan
LA Laos
LB Lebanon
LC Saint Lucia
LI Liechtenstein
LK Sri Lanka
LR Liberia
LS Lesotho
LT Lithuania
LU Luxembourg
LV Latvia
LY Libya
MA Morocco
MC Monaco
MD Moldova
ME Montenegro
MG Madagascar
MH Marshall Islands
MK North Macedonia
ML Mali
MM Myanmar
MN Mongolia
MO Macao
MP Northern Mariana Islands
MQ Martinique
MR Mauritania
MT Malta
MU Mauritius
MV Maldives
MW Malawi
MX Mexico
MY Malaysia
MZ Mozambique
NA Namibia
NC New Caledonia
NE Niger
NG Nigeria
NI Nicaragua
NL Netherlands
NO Norway
NP Nepal
NU Niue
NZ New Zealand
OM Oman
PA Panama
PE Peru
PF French Polynesia
PG Papua New Guinea
PH Philippines
PK Pakistan
PL Poland
PM Saint Pierre and Miquelon
PR Puerto Rico
PS Palestine
PT Portugal
PW Palau
PY Paraguay
QA Qatar
RE Reunion
RO Romania
RS Serbia
RU Russia
RW Rwanda
SA Saudi Arabia
SB Solomon Islands
SC Seychelles
SD Sudan
SE Sweden
SG Singapore
SI Slovenia
SK Slovakia
SL Sierra Leone
SM San Marino
SN Senegal
SO Somalia
SR Suriname
ST Sao Tome and Principe
SV El Salvador
SX Sint Maarten
SY Syria
SZ Eswatini
TC Turks and Caicos Islands
TD Chad
TG Togo
TH Thailand
TJ Tajikistan
TK Tokelau
TL Timor-Leste
TM Turkmenistan
TN Tunisia
TO Tonga
TR Turkey
TT Trinidad and Tobago
TW Taiwan
TZ Tanzania
UA Ukraine
UG Uganda
UM United States Minor Outlying Islands
US United States
UY Uruguay
UZ Uzbekistan
VC Saint Vincent and the Grenadines
VE Venezuela
VG British Virgin Islands
VI United States Virgin Islands
VN Vietnam
WS Samoa
YE Yemen
ZA South Africa
ZM Zambia
ZW Zimbabwe
"""

COUNTRIES: dict[str, dict] = {}
for _line in _TABLE.strip().splitlines():
    _code, _name = _line.split(" ", 1)
    COUNTRIES[_code] = {"code": _code, "name": _name}

LANGUAGES: dict[str, str] = {
    "PL": "pl", "US": "en", "CA": "en", "GB": "en", "IE": "en", "AU": "en", "NZ": "en", "NG": "en", "GH": "en",
    "KE": "en", "ZA": "en", "IN": "en", "SG": "en", "PK": "en", "VN": "vi", "AE": "ar", "LB": "ar", "SA": "ar",
    "EG": "ar", "MA": "fr", "DZ": "fr", "TN": "fr", "FR": "fr", "BE": "fr", "DE": "de", "AT": "de", "CH": "de",
    "ES": "es", "MX": "es", "AR": "es", "CO": "es", "CL": "es", "PE": "es", "IT": "it", "JP": "ja", "BR": "pt",
    "PT": "pt", "CZ": "cs", "TR": "tr", "SE": "sv", "NO": "nb", "DK": "da", "FI": "fi", "NL": "nl", "CN": "zh",
    "TW": "zh", "HK": "zh", "KR": "ko", "RU": "ru", "UA": "uk", "GR": "el", "RO": "ro", "HU": "hu", "TH": "th",
    "ID": "id", "PH": "en", "MY": "ms", "IL": "he", "IR": "fa", "BD": "bn",
}


def language_of(country: str) -> str:
    """The language code a notice to an owner in this country is written in ("pl" for PL, "en" when unsure)."""
    return LANGUAGES.get((country or "").strip().upper(), "en")
