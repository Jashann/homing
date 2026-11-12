from homing_fixtures import ledger

from homing.search import SURE, Query, search

LEDGER = ledger()


def top(q: Query) -> dict:
    found = search(LEDGER, q)
    assert found, f"nothing found for {q}"
    return found[0]


def test_exact_person_with_email_is_sure():
    m = top(Query("Tomasz Wisniewski", emails=["t.wisniewski@example.com"], places=[{"city": "Krakow", "country": "PL"}]))
    assert m["balance_id"] == "B001"
    assert m["probability"] >= SURE
    assert any("email" in r for r in m["reasons"])


def test_same_name_in_two_cities_is_doubtful_not_sure():
    found = search(LEDGER, Query("Wei Zhang", places=[{"city": "Toronto", "country": "CA"}]))
    ids = [m["balance_id"] for m in found]
    assert ids[:2] == ["B003", "B004"]          # the Toronto one first, but both shown
    for m in found[:2]:
        assert m["probability"] < SURE
        assert any("same name" in d for d in m["doubts"])


def test_same_name_with_nothing_else_both_doubtful():
    found = {m["balance_id"]: m for m in search(LEDGER, Query("Wei Zhang"))}
    assert {"B003", "B004"} <= set(found)
    assert all(found[b]["probability"] <= 0.6 for b in ("B003", "B004"))


def test_same_name_tied_by_email_becomes_sure():
    m = top(Query("Wei Zhang", emails=["wzhang85@example.com"]))
    assert m["balance_id"] == "B003" and m["probability"] >= SURE
    assert not any("same name" in d for d in m["doubts"])


def test_reordered_name():
    m = top(Query("An Van Nguyen", places=[{"city": "Ho Chi Minh City", "country": "VN"}]))
    assert m["balance_id"] == "B005"


def test_initials():
    m = top(Query("Jude Raymond Okafor", places=[{"city": "Lagos", "country": "NG"}]))
    assert m["balance_id"] == "B006"
    assert any("initial" in d for d in m["doubts"])


def test_transliterations():
    assert top(Query("Muhammad Alrashid", places=[{"city": "Dubai"}]))["balance_id"] == "B007"
    assert top(Query("Alexander Petrov", places=[{"city": "Berlin"}]))["balance_id"] == "B008"
    m = top(Query("Yusuf Ben Ali", places=[{"city": "Casablanca"}]))
    assert m["balance_id"] == "B009"
    assert any("another spelling" in r for r in m["reasons"])


def test_married_name_found_through_former_name():
    m = top(Query("Ana Brennan", former_names=["Ana Kowalski"], places=[{"city": "Chicago", "country": "US"}]))
    assert m["balance_id"] == "B002"
    assert any("former name" in r for r in m["reasons"])
    assert not search(LEDGER, Query("Ana Brennan", places=[{"city": "Dublin", "country": "IE"}]))


def test_organisation_by_name_and_people_never_get_organisations():
    m = top(Query("Northside Youth Football Club", places=[{"city": "Leeds"}]))
    assert m["balance_id"] == "B035" and m["owner_display"] == "Northside Youth Football Club"
    orgs = {"B035", "B036", "B037", "B041"}
    for person in ("Grace Wanjiru", "Wei Zhang", "Kenji Watanabe"):
        assert not orgs & {m["balance_id"] for m in search(LEDGER, Query(person, places=[{"city": "Toronto"}]))}


def test_results_never_show_a_full_private_name():
    for m in search(LEDGER, Query("Wei Zhang")):
        assert m["owner_display"] == "Wei Z."


def test_unknown_person_finds_nothing():
    assert search(LEDGER, Query("Zebulon Quackenbush")) == []


def test_strength_and_band_are_the_heuristic_score_in_plain_form():
    sure = top(Query("Tomasz Wisniewski", emails=["t.wisniewski@example.com"]))
    assert sure["strength"] == round(sure["probability"] * 100) and sure["band"] == "VERY_LIKELY"
    twins = {m["balance_id"]: m for m in search(LEDGER, Query("Wei Zhang", places=[{"city": "Toronto"}]))}
    assert twins["B003"]["band"] == "POSSIBLE" and isinstance(twins["B003"]["strength"], int)
    assert all(0 <= m["strength"] <= 100 and m["band"] in ("VERY_LIKELY", "POSSIBLE", "UNLIKELY")
               for m in twins.values())
