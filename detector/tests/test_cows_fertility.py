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
                                    ["Diernr", "Status", "Dgn status", "Lactdgn", "#Isem"],
                                    ["Diernr", "Status", "Dgn status", "Lactdgn", "# Ins."]])
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


# The farm's report for young stock, as AgroVision exports it.
FARM_HEIFERS = [
    ["Diernr", "Werknr", "Geb dat", "Lft mnd", "Status", "Dgn status", "Tocht dat", "Dekdatum", "Dekinfo",
     "# Ins.", "Stier", "Verw klfdt", "Koe prod. type"],
    ["64", "8684", "20-09-24", "25,00", "Drachtig+", "270,00", "02-11-25", "27-01-26", "Inseminatie",
     "2,00", "CHIPTUNER-RED", "03-11-26", "Melkvee"],
]


def test_the_farms_young_stock_report_with_decimal_commas():
    animals = fertility.parse(FARM_HEIFERS, fertility.HEADS)

    assert animals == {
        "64": {"age_months": 25, "status": "Drachtig+", "days_in_status": 270, "inseminations": 2}
    }


def test_the_lactation_number_is_not_the_lactation_days():
    animals = fertility.parse(
        [["Diernr", "Lact nr", "Status", "Dgn status", "Lactdgn", "# Ins."], ["5", "3", "Leeg", "10", "88", "1"]],
        fertility.COWS,
    )

    assert animals["5"]["lact_days"] == 88


# The farm's report for cows, as AgroVision exports it.
FARM_COWS = [
    ["Diernr", "Werknr", "Lact nr", "Lact dgn", "LW", "Kg melk (dag)", "Celget", "Klf dat", "Status", "Dgn status",
     "Tocht dat", "Dekdatum", "# Ins.", "Stier", "Drg dat", "Dgn drg", "Vw TKT", "Vw drg", "Verw klfdt",
     "Koe prod. type"],
    ["45", "7933", "8,00", "0,00", "0,00", "0,00", "0,00", "24-10-26", "Vers", "0,00", "", "", "0,00", "", "",
     "0,00", "0,00", "", "", "Melkvee"],
    ["46", "7940", "2,00", "187,00", "31,0", "28,5", "120,00", "20-04-26", "Drachtig", "112,00", "", "", "2,00", "",
     "", "0,00", "0,00", "", "", "Melkvee"],
]


def test_the_farms_cow_report_takes_lactation_days_not_the_lactation_number():
    animals = fertility.parse(FARM_COWS, fertility.COWS)

    assert animals == {
        "45": {"status": "Vers", "days_in_status": 0, "lact_days": 0, "inseminations": 0},
        "46": {"status": "Drachtig", "days_in_status": 112, "lact_days": 187, "inseminations": 2},
    }
