"""Writes data/sample_ledger.json: the uncollected seller earnings of Larkfield Market, an invented online
marketplace that pays its sellers through PayPal.

    uv run python scripts/make_ledger.py

Larkfield Market and every person and organisation here are invented for testing. No real record is used: the
California file appears in Homing only as totals and organisation kinds (scripts/paypal_totals.py). Emails use the
reserved example.com / example.org / example.net domains. The output is the same on every run.

Built-in traps for the matcher:
- B005 is stored family-name-first (Nguyen Van An); people type it the other way round.
- B006 has initials only (J. R. Okafor).
- B007, B008, B009 are transliterated names (Mohammed Al-Rashid, Aleksandr Petrov, Youssef Benali).
- B002 is a maiden name; the owner now uses a married name (demo case: partial match).
- B003 and B004 are two different people called Wei Zhang, in Toronto and in Vancouver (demo case: impostor).
- B001 is the clean demo case.
- Four balances are ESCHEATED: Larkfield Market already handed them to the State of California.
"""
from __future__ import annotations

import json
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "data" / "sample_ledger.json"

P, O = "PERSON", "ORGANISATION"
HOLDER = {"name": "Larkfield Market", "kind": "an online marketplace that pays its sellers through PayPal",
          "sample": True}

# id, type, owner name, date of birth, last email, street, city, region, postcode, country, amount, currency,
# last activity, account opened, status
ROWS = [
    ("B001", P, "Tomasz Wiśniewski", "1979-03-14", "t.wisniewski@example.com", "ul. Długa 12", "Kraków", "",
     "31-147", "PL", "86.40", "USD", "2019-06-02", "2011-09-20", "DORMANT"),
    ("B002", P, "Ana Kowalski", "1988-11-02", "ana.kowalski@example.com", "2210 W Cortland St", "Chicago", "IL",
     "60647", "US", "212.75", "USD", "2018-02-11", "2012-05-30", "DORMANT"),
    ("B003", P, "Wei Zhang", "1985-07-21", "wzhang85@example.com", "88 Harbord St", "Toronto", "ON", "M5S 1G5",
     "CA", "1240.00", "USD", "2017-10-19", "2010-01-07", "DORMANT"),
    ("B004", P, "Wei Zhang", "1992-01-09", "zhang.wei.92@example.net", "1450 W 7th Ave", "Vancouver", "BC",
     "V6H 1C1", "CA", "57.10", "USD", "2020-03-28", "2015-08-14", "DORMANT"),
    ("B005", P, "Nguyen Van An", "1983-05-30", "an.nguyen83@example.com", "45 Le Loi", "Ho Chi Minh City", "",
     "700000", "VN", "318.20", "USD", "2018-12-01", "2013-04-22", "DORMANT"),
    ("B006", P, "J. R. Okafor", "1976-09-12", "jr.okafor@example.org", "14 Awolowo Rd", "Lagos", "", "101233",
     "NG", "640.00", "USD", "2016-07-15", "2009-11-03", "DORMANT"),
    ("B007", P, "Mohammed Al-Rashid", "1981-02-25", "m.alrashid@example.com", "Al Wasl Rd 220", "Dubai", "", "",
     "AE", "1875.30", "USD", "2019-01-08", "2014-06-17", "DORMANT"),
    ("B008", P, "Aleksandr Petrov", "1990-12-04", "a.petrov90@example.net", "Torstraße 61", "Berlin", "", "10119",
     "DE", "143.65", "EUR", "2020-09-30", "2016-02-01", "DORMANT"),
    ("B009", P, "Youssef Benali", "1987-06-18", "y.benali@example.com", "17 Rue Tahar Sebti", "Casablanca", "",
     "20250", "MA", "96.00", "EUR", "2019-04-12", "2013-10-10", "DORMANT"),
    ("B010", P, "María José García López", "1972-08-27", "mj.garcia.lopez@example.com", "Calle de Toledo 84",
     "Madrid", "", "28005", "ES", "410.50", "EUR", "2017-05-21", "2008-03-15", "DORMANT"),
    ("B011", P, "Priya Raghunathan", "1991-04-03", "priya.r@example.com", "12 Besant Ave", "Chennai", "TN",
     "600090", "IN", "74.25", "USD", "2021-01-17", "2017-07-09", "DORMANT"),
    ("B012", P, "Siobhán Ní Bhriain", "1984-10-11", "siobhan.nib@example.org", "9 Nuns Island", "Galway", "",
     "H91 X2E1", "IE", "265.00", "EUR", "2018-08-05", "2012-12-12", "DORMANT"),
    ("B013", P, "Kenji Watanabe", "1969-01-20", "k.watanabe@example.net", "3-5-12 Namba", "Osaka", "",
     "542-0076", "JP", "1022.40", "USD", "2016-11-29", "2007-04-01", "DORMANT"),
    ("B014", P, "Olusegun Adeyemi", "1980-03-07", "segun.adeyemi@example.com", "22 Ring Rd", "Ibadan", "",
     "200273", "NG", "48.90", "USD", "2020-06-14", "2015-01-26", "DORMANT"),
    ("B015", P, "Lucas Oliveira Santos", "1993-09-15", "lucas.os@example.com", "Rua Augusta 1500", "São Paulo",
     "SP", "01304-001", "BR", "389.75", "USD", "2019-11-03", "2016-05-18", "DORMANT"),
    ("B016", P, "Fatima Zahra El Idrissi", "1989-12-22", "fz.elidrissi@example.org", "5 Avenue Fal Ould Oumeir",
     "Rabat", "", "10090", "MA", "152.00", "EUR", "2018-03-19", "2014-09-02", "DORMANT"),
    ("B017", P, "Hana Nováková", "1986-07-01", "hana.novakova@example.com", "Údolní 33", "Brno", "", "602 00",
     "CZ", "67.80", "EUR", "2020-12-08", "2017-03-13", "DORMANT"),
    ("B018", P, "Sipho Dlamini", "1978-05-16", "sipho.d@example.net", "40 Florida Rd", "Durban", "KZN", "4001",
     "ZA", "530.00", "USD", "2017-02-24", "2010-10-30", "DORMANT"),
    ("B019", P, "Camille Lefèvre", "1995-02-09", "camille.lefevre@example.com", "18 Rue Mercière", "Lyon", "",
     "69002", "FR", "29.95", "EUR", "2021-05-27", "2018-06-06", "DORMANT"),
    ("B020", P, "Giulia Romano", "1982-11-30", "giulia.romano@example.org", "Via Po 21", "Turin", "", "10124",
     "IT", "812.60", "EUR", "2016-09-09", "2009-02-19", "DORMANT"),
    ("B021", P, "Ahmet Yılmaz", "1975-04-28", "ahmet.yilmaz@example.com", "Kıbrıs Şehitleri Cd. 140", "Izmir",
     "", "35220", "TR", "205.40", "USD", "2018-10-16", "2011-08-08", "DORMANT"),
    ("B022", P, "Rahul Mehta", "1987-08-19", "rahul.mehta@example.net", "201 Hill Rd, Bandra West", "Mumbai",
     "MH", "400050", "IN", "1456.00", "USD", "2017-07-04", "2012-01-23", "DORMANT"),
    ("B023", P, "Sofía Hernández", "1990-06-25", "sofia.hdz@example.com", "Av. Chapultepec 480", "Guadalajara",
     "JAL", "44130", "MX", "93.15", "USD", "2020-02-02", "2016-11-11", "DORMANT"),
    ("B024", P, "Jonas Lindqvist", "1984-01-13", "jonas.lindqvist@example.org", "Svartbäcksgatan 9", "Uppsala",
     "", "753 20", "SE", "174.30", "EUR", "2019-08-21", "2013-05-05", "DORMANT"),
    ("B025", P, "Aroha Ngata", "1992-10-07", "aroha.ngata@example.com", "1120 Hinemoa St", "Rotorua", "", "3010",
     "NZ", "61.00", "USD", "2021-03-30", "2018-01-15", "DORMANT"),
    ("B026", P, "Kwame Mensah", "1979-12-02", "kwame.mensah@example.net", "7 Oxford St, Osu", "Accra", "", "",
     "GH", "720.85", "USD", "2017-04-18", "2011-06-29", "DORMANT"),
    ("B027", P, "Leila Haddad", "1985-03-23", "leila.haddad@example.com", "Rue Hamra 102", "Beirut", "", "",
     "LB", "338.00", "USD", "2018-06-27", "2012-09-14", "DORMANT"),
    ("B028", P, "Mateo Rossi", "1994-05-05", "mateo.rossi@example.org", "Av. Corrientes 2037", "Buenos Aires", "",
     "C1045", "AR", "44.40", "USD", "2021-07-12", "2019-02-20", "DORMANT"),
    ("B029", P, "Eilidh MacLeod", "1983-08-08", "eilidh.macleod@example.com", "14 Church St", "Inverness", "",
     "IV1 1EA", "GB", "189.99", "GBP", "2019-10-25", "2014-04-04", "DORMANT"),
    ("B030", P, "Pham Thi Lan", "1988-02-14", "lan.pham@example.net", "36 Hang Bac", "Hanoi", "", "100000", "VN",
     "122.70", "USD", "2020-04-09", "2015-12-01", "DORMANT"),
    ("B031", P, "Ingrid Haugen", "1971-07-29", "ingrid.haugen@example.com", "Bryggen 5", "Bergen", "", "5003",
     "NO", "960.00", "EUR", "2016-12-20", "2008-08-28", "DORMANT"),
    ("B032", P, "Tariq Mahmood", "1983-11-17", "tariq.mahmood@example.org", "65 Main Blvd, Gulberg", "Lahore",
     "", "54660", "PK", "81.60", "USD", "2020-08-01", "2016-03-30", "DORMANT"),
    ("B033", P, "Chloe Tan", "1996-03-11", "chloe.tan@example.com", "2 Tanjong Pagar Rd", "Singapore", "",
     "088453", "SG", "275.25", "USD", "2021-09-05", "2018-10-22", "DORMANT"),
    ("B034", P, "Grace Wanjiru", "1990-01-26", "grace.wanjiru@example.net", "Kimathi St 18", "Nairobi", "",
     "00100", "KE", "58.30", "USD", "2020-11-18", "2017-06-12", "DORMANT"),
    # organisations
    ("B035", O, "Northside Youth Football Club", "", "treasurer@northside-yfc.example.org", "Meanwood Rd 210",
     "Leeds", "", "LS7 2AN", "GB", "1388.00", "GBP", "2018-05-31", "2010-03-01", "DORMANT"),
    ("B036", O, "Hope Valley Animal Rescue Inc", "", "accounts@hopevalley-rescue.example.org", "4100 S Congress Ave",
     "Austin", "TX", "78745", "US", "2964.10", "USD", "2017-09-14", "2011-02-09", "DORMANT"),
    ("B037", O, "Lakeshore Community Choir Association", "", "choir@lakeshore.example.org", "120 Lake Shore Blvd W",
     "Toronto", "ON", "M8V 1A1", "CA", "415.00", "USD", "2019-03-03", "2013-09-09", "DORMANT"),
    # already handed to the State of California
    ("B038", P, "Rosa Delgado", "1966-04-12", "rosa.delgado@example.com", "1734 N Fresno St", "Fresno", "CA",
     "93703", "US", "348.20", "USD", "2015-03-17", "2006-07-01", "ESCHEATED"),
    ("B039", P, "Linh Tran", "1977-09-03", "linh.tran@example.net", "980 Story Rd", "San Jose", "CA", "95122",
     "US", "1190.00", "USD", "2014-11-05", "2005-05-25", "ESCHEATED"),
    ("B040", P, "Marcus Bell", "1981-01-30", "marcus.bell@example.org", "3301 Telegraph Ave", "Oakland", "CA",
     "94609", "US", "77.45", "USD", "2015-08-22", "2009-01-13", "ESCHEATED"),
    ("B041", O, "Riverbend Food Bank Cooperative", "", "office@riverbend-foodbank.example.org", "2600 Del Paso Blvd",
     "Sacramento", "CA", "95815", "US", "2210.65", "USD", "2014-06-30", "2004-10-18", "ESCHEATED"),
]

ESCHEAT_DATES = {"B038": "2019-04-15", "B039": "2018-04-16", "B040": "2019-04-15", "B041": "2018-04-16"}


def build() -> dict:
    balances = []
    for (bid, kind, name, dob, email, street, city, region, postcode, country, amount, currency, last, opened,
         status) in ROWS:
        b = {"id": bid, "owner_type": kind, "owner_name": name, "date_of_birth": dob or None, "last_email": email,
             "last_street": street, "last_city": city, "last_region": region, "last_postcode": postcode,
             "last_country": country, "amount": amount, "currency": currency, "last_activity": last,
             "account_opened": opened, "status": status, "escheat": None}
        if status == "ESCHEATED":
            b["escheat"] = {"state": "CA", "reported_on": ESCHEAT_DATES[bid],
                            "record_id": f"SAMPLE-{bid}", "property_type": "Seller earnings not collected",
                            "holder_name": "LARKFIELD MARKET (SAMPLE)"}
        balances.append(b)
    return {"note": "Invented test ledger: seller earnings nobody collected at an invented marketplace. No real person "
                    "or organisation.",
            "holder": HOLDER, "balances": balances}


def main() -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(build(), indent=2, ensure_ascii=False) + "\n")
    print(f"wrote {OUT} ({len(ROWS)} balances)")


if __name__ == "__main__":
    main()
