import pytest

from aidetector.cows import fertility

COWS = [
    ["Vruchtbaarheid MK"],
    ["Diernr", "Naam", "Status", "Dgn status", "Lactdgn", "#Insem"],
    ["8", "Bertha", "Drachtig", "112", "187", "2"],
    ["12", "Anna", "Inseminatie", "3", "95", ""],
    [None, None, None, None, None, None],
    ["totaal", "", "", "", "", ""],
]
HEIFERS = [
    ["Diernr", "Lft mdn", "Status", "Dgn status", "#Insem"],
    ["8835", "16,5", "Tochtig", "1", "0"],
]


def test_cows_status_days_lactation_days_and_inseminations():
    animals = fertility.parse(COWS, fertility.COWS)

    assert animals == {
        "8": {"status": "Drachtig", "days_in_status": 112, "lact_days": 187, "inseminations": 2},
        "12": {"status": "Inseminatie", "days_in_status": 3, "lact_days": 95, "inseminations": None},
    }


def test_young_stock_has_age_in_months_and_no_lactation():
    animals = fertility.parse(HEIFERS, fertility.HEADS)

    assert animals == {
        "8835": {"age_months": 16.5, "status": "Tochtig", "days_in_status": 1, "inseminations": 0}
    }


@pytest.mark.parametrize("header", [["Diernr", "Status", "Dgn status", "Lactdgn", "# insem"],
                                    ["DIERNR", "STATUS", "DGN. STATUS", "LACT DGN", "#INSEM"],
                                    ["Diernr", "Status", "Dgn status", "Lactdgn", "#Isem"]])
def test_headers_are_matched_without_caring_for_spaces_dots_and_case(header):
    animals = fertility.parse([header, ["1", "Leeg", "5", "40", "1"]], fertility.COWS)

    assert animals["1"]["lact_days"] == 40 and animals["1"]["inseminations"] == 1


def test_a_missing_column_is_named():
    with pytest.raises(ValueError, match="Lactdgn ontbreekt"):
        fertility.parse([["Diernr", "Status", "Dgn status", "#Insem"], ["1", "a", "1", "1"]], fertility.COWS)
    with pytest.raises(ValueError, match="Diernr"):
        fertility.parse([["Naam", "Status"]], fertility.COWS)


def test_status_is_not_mistaken_for_days_in_status():
    # "Dgn status" comes first; "Status" is still the text column.
    animals = fertility.parse(
        [["Diernr", "Dgn status", "Status", "Lactdgn", "#Insem"], ["1", "9", "Drachtig", "50", "3"]],
        fertility.COWS,
    )

    assert animals["1"] == {"status": "Drachtig", "days_in_status": 9, "lact_days": 50, "inseminations": 3}


def test_store_keeps_both_groups_and_links_by_diernr(tmp_path):
    fertility.save(tmp_path, fertility.COWS, fertility.parse(COWS, fertility.COWS), "2026-10-24T03:30:00")
    fertility.save(tmp_path, fertility.HEADS, fertility.parse(HEIFERS, fertility.HEADS), "2026-10-24T03:31:00")

    data = fertility.load(tmp_path)

    assert fertility.lookup(data, "8")["status"] == "Drachtig"
    assert fertility.lookup(data, "8")["group"] == "koeien"
    assert fertility.lookup(data, "8835")["age_months"] == 16.5
    assert fertility.lookup(data, "999") is None and fertility.lookup(data, None) is None
    # Fetching one group again replaces only that group.
    fertility.save(tmp_path, fertility.COWS, {}, "2026-10-25T03:30:00")
    assert fertility.lookup(fertility.load(tmp_path), "8") is None
    assert fertility.lookup(fertility.load(tmp_path), "8835") is not None
