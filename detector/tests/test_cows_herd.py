import json
import os
from datetime import datetime, timedelta

import pytest
from openpyxl import Workbook

from aidetector.cows import service as service_module
from aidetector.cows.importer import parse_herd, read_table, sync_herd
from aidetector.cows.registry import CowRegistry
from aidetector.cows.service import CowService
from aidetector.utils.config import CowsConfig

BERTHA, KLAARTJE, ANNA = "NL000000030", "NL000000007", "NL000000051"


def excel(path, rows):
    workbook = Workbook()
    sheet = workbook.active
    for row in rows:
        sheet.append(row)
    workbook.save(path)
    return path


HEADER = ["Levensnummer", "Werknummer", "Halsbandnummer", "Naam"]


def test_excel_export_with_a_title_above_the_header(tmp_path):
    path = excel(
        tmp_path / "koeien.xlsx",
        [
            ["Dierenlijst Melkveebedrijf"],
            [],
            HEADER,
            ["NL 0000 0003 0", 4030, 30, "Bertha"],
            # A heifer: no collar, and Excel gives numbers as floats.
            [ANNA, 5101.0, None, "Anna"],
        ],
    )

    parsed = parse_herd(read_table(path))

    assert parsed.problems == []
    assert [(animal.number, animal.life_number, animal.name, animal.collar) for animal in parsed.animals] == [
        ("30", BERTHA, "Bertha", True),
        ("5101", ANNA, "Anna", False),
    ]


LELY_HEADER = ["Diernr", "Resp 1", "Levensnummer", "Gesl", "Geb dat", "Naam", "Werknummer", "Diercat", "Levnr moeder", "Aankdat"]


def test_lely_export_collar_number_for_cows_work_number_for_heifers(tmp_path):
    from datetime import date

    path = excel(
        tmp_path / "lely.xlsx",
        [
            LELY_HEADER,
            # A cow with a collar (responder): her Diernr.
            [30, 123456, "NL 0000 0003 0", "V", date(2021, 3, 1), "Bertha", 4030, "Koe", "NL 0000 0099 9", None],
            # A heifer without a collar yet: her work number, even though
            # she already has a Diernr.
            [512, None, ANNA, "V", date(2024, 5, 2), "Anna", 5101, "Pink", BERTHA, None],
            # A bull calf and a heifer calf are left out.
            [640, None, "NL000000640", "M", date(2026, 8, 1), None, 6400, "Kalf", ANNA, None],
            [641, None, "NL000000641", "V", date(2026, 8, 2), None, 6401, "Kalf", BERTHA, None],
        ],
    )

    parsed = parse_herd(read_table(path))

    assert parsed.problems == []
    assert parsed.skipped == 2
    assert [(animal.number, animal.life_number, animal.name, animal.collar) for animal in parsed.animals] == [
        ("30", BERTHA, "Bertha", True),
        ("5101", ANNA, "Anna", False),
    ]

    # Anna calves and gets a collar: the next export has her responder, so
    # her Diernr becomes her number and she keeps her history.
    registry = CowRegistry(tmp_path / "koeien")
    start = datetime(2026, 10, 2, 8, 0)
    assert sync_herd(path, registry, at=start).added == ["30 (Bertha)", "5101 (Anna)"]
    calved = excel(
        tmp_path / "lely.xlsx",
        [LELY_HEADER, [30, 123456, BERTHA, "V", None, "Bertha", 4030, "Koe", None, None],
         [512, 654321, ANNA, "V", None, "Anna", 5101, "Koe", BERTHA, None]],
    )
    later = start + timedelta(days=60)
    result = sync_herd(calved, registry, at=later)
    assert result.renumbered == ["512 (Anna)"]
    assert registry.label(ANNA, start) == "5101 (Anna)"


def herd_file(tmp_path, rows, name="koeien.xlsx"):
    return excel(tmp_path / name, [HEADER, *rows])


def test_the_herd_list_is_leading(tmp_path):
    registry = CowRegistry(tmp_path / "koeien")
    first = herd_file(tmp_path, [[BERTHA, 4030, 30, "Bertha"], [KLAARTJE, 4007, 7, None], [ANNA, 5101, None, "Anna"]])
    start = datetime(2026, 10, 2, 8, 0)

    result = sync_herd(first, registry, at=start)
    assert result.added == ["30 (Bertha)", "7", "5101 (Anna)"]

    # Bertha and Klaartje swap collars, Anna calves and gets collar 31, and
    # Klaartje gets her name.
    later = start + timedelta(days=30)
    second = herd_file(tmp_path, [[BERTHA, 4030, 7, "Bertha"], [KLAARTJE, 4007, 30, "Klaartje"], [ANNA, 5101, 31, "Anna"]])
    result = sync_herd(second, registry, at=later)

    assert result.problems == []
    assert sorted(result.renumbered) == ["30 (Klaartje)", "31 (Anna)", "7 (Bertha)"]
    assert registry.cow_with_number("30", later) == KLAARTJE
    # History stays: before the swap 30 was Bertha.
    assert registry.cow_with_number("30", start) == BERTHA


def test_animals_missing_from_the_list_are_archived_and_can_return(tmp_path):
    registry = CowRegistry(tmp_path / "koeien")
    cows = [[f"NL0000001{index:02d}", 4000 + index, index, None] for index in range(10)]
    sync_herd(herd_file(tmp_path, cows), registry)

    result = sync_herd(herd_file(tmp_path, cows[1:]), registry)
    assert result.archived == ["0"]
    assert registry.cow("NL000000100").archived is not None

    result = sync_herd(herd_file(tmp_path, cows), registry)
    assert result.returned == ["0"]


def test_a_partial_export_archives_nobody(tmp_path):
    registry = CowRegistry(tmp_path / "koeien")
    cows = [[f"NL0000001{index:02d}", 4000 + index, index, None] for index in range(10)]
    sync_herd(herd_file(tmp_path, cows), registry)

    result = sync_herd(herd_file(tmp_path, cows[:3]), registry)

    assert result.archived == []
    assert "7 van de 10 dieren staan niet in de lijst" in result.problems[0]
    assert len(registry.active_cows()) == 10


def test_doubles_in_the_list_are_reported(tmp_path):
    registry = CowRegistry(tmp_path / "koeien")
    path = herd_file(tmp_path, [[BERTHA, 1, 30, None], [KLAARTJE, 2, 30, None], [BERTHA, 3, 31, None]])

    result = sync_herd(path, registry)

    assert result.added == ["30"]
    assert result.problems == [
        f"Regel 3: nummer 30 staat ook bij {BERTHA}",
        f"Regel 4: {BERTHA} staat er twee keer in",
    ]


class Telegram:
    def __init__(self):
        self.texts = []

    def post(self, url, data=None, files=None, timeout=None):
        self.texts.append((data or {}).get("text"))

        class Response:
            status_code = 200
            text = ""

            def json(self):
                return {"ok": True, "result": {"message_id": 1}}

        return Response()


@pytest.fixture
def telegram(monkeypatch):
    fake = Telegram()
    monkeypatch.setattr(service_module.requests, "post", fake.post)
    return fake


def test_the_service_follows_the_file_and_tells_the_farmer_once(tmp_path, telegram, monkeypatch):
    # No watcher thread in the test; check_herd is called by hand.
    monkeypatch.setattr(service_module.Thread, "start", lambda self: None)
    path = herd_file(tmp_path, [[BERTHA, 4030, 30, "Bertha"], [ANNA, 5101, None, "Anna"]])
    service = CowService("token", "chat", tmp_path / "koeien", CowsConfig(herd_file=path))
    modified = os.path.getmtime(path)

    # A file that was saved a moment ago may still be being written.
    assert service.check_herd(now=modified + 1) is None
    result = service.check_herd(now=modified + 60)
    assert result is not None and result.added == ["30 (Bertha)", "5101 (Anna)"]
    assert telegram.texts[-1].splitlines() == [
        "📋 Koeienlijst bijgewerkt: 2 nieuw.",
        "Nieuw: 30 (Bertha), 5101 (Anna)",
    ]

    # Unchanged file: nothing happens, also not after a restart.
    assert service.check_herd(now=modified + 120) is None
    restarted = CowService("token", "other-chat", tmp_path / "koeien", CowsConfig(herd_file=path))
    assert restarted.check_herd(now=modified + 120) is None
    assert json.loads((tmp_path / "koeien" / ".koeienlijst.json").read_text())["modified"] == modified

    # A missing file is reported once.
    path.unlink()
    assert service.check_herd() is None
    assert service.check_herd() is None
    assert [text for text in telegram.texts if text.startswith("⚠️")] == [
        f"⚠️ De koeienlijst {path} is niet gevonden."
    ]
