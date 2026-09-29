from datetime import date

import pytest

from aidetector.cows.registry import (
    CowRegistry,
    NumberTaken,
    normalize_life_number,
)

OLD = "NL123456789"
PINK = "NL987654321"
SWITCH_DAY = date(2026, 10, 5)


def test_life_number_is_written_without_spaces():
    assert normalize_life_number("nl 1234.5678-9") == OLD
    with pytest.raises(ValueError, match="levensnummer"):
        normalize_life_number("30")


def test_number_given_to_a_pink_keeps_the_old_cows_history(tmp_path):
    registry = CowRegistry(tmp_path)
    registry.add("30", OLD, "Bertha", day=date(2025, 3, 1))
    (tmp_path / OLD).mkdir()
    (tmp_path / OLD / "photo.jpg").write_bytes(b"jpg")

    old = registry.switch("30", PINK, old_cow_left=True, day=SWITCH_DAY)

    assert old == OLD
    assert registry.cow_with_number("30", date(2026, 9, 1)) == OLD
    assert registry.cow_with_number("30", SWITCH_DAY) == PINK
    assert registry.label(OLD, date(2026, 9, 1)) == "30 (Bertha)"
    # The old cow is archived: her photos no longer take part in recognition.
    assert [cow.life_number for cow in registry.active_cows()] == [PINK]
    assert (tmp_path / "archief" / OLD / "photo.jpg").is_file()
    assert registry.photos(OLD) == [tmp_path / "archief" / OLD / "photo.jpg"]
    # The registry survives a restart.
    assert CowRegistry(tmp_path).cow_with_number("30", SWITCH_DAY) == PINK


def test_swapped_collars_keep_both_cows_active(tmp_path):
    registry = CowRegistry(tmp_path)
    registry.add("30", OLD, day=date(2025, 3, 1))

    registry.switch("30", PINK, old_cow_left=False, day=SWITCH_DAY)

    assert {cow.life_number for cow in registry.active_cows()} == {OLD, PINK}
    assert registry.number_of(OLD, SWITCH_DAY) is None
    registry.add("31", OLD, day=SWITCH_DAY)
    assert registry.number_of(OLD, SWITCH_DAY) == "31"


def test_a_number_worn_by_another_cow_needs_a_switch(tmp_path):
    registry = CowRegistry(tmp_path)
    registry.add("30", OLD)

    with pytest.raises(NumberTaken) as error:
        registry.add("30", PINK)
    assert error.value.holder == OLD


def test_a_cow_gets_one_number_at_a_time(tmp_path):
    registry = CowRegistry(tmp_path)
    registry.add("30", OLD, day=date(2025, 3, 1))
    registry.add("12", OLD, day=SWITCH_DAY)

    assert registry.cow_with_number("30", SWITCH_DAY) is None
    assert registry.number_of(OLD, SWITCH_DAY) == "12"


def test_an_archived_cow_that_returns_gets_her_photos_back(tmp_path):
    registry = CowRegistry(tmp_path)
    registry.add("30", OLD, day=date(2025, 3, 1))
    (tmp_path / OLD).mkdir()
    (tmp_path / OLD / "photo.jpg").write_bytes(b"jpg")
    registry.archive(OLD, SWITCH_DAY)

    registry.add("44", OLD, day=date(2026, 11, 1))

    assert registry.cow(OLD).archived is None
    assert (tmp_path / OLD / "photo.jpg").is_file()


def test_import_reads_a_herd_program_export(tmp_path):
    from aidetector.cows.importer import import_cows

    export = tmp_path / "export.csv"
    export.write_text(
        "Werknummer;Levensnummer;Naam\n"
        "30;NL 1234 5678 9;Bertha\n"
        "12;NL987654321;\n"
        "31;NL123456789;Dubbel\n"
        "7;geen nummer;\n"
    )
    registry = CowRegistry(tmp_path / "koeien")

    added, problems = import_cows(export, registry)

    assert added == 3
    assert registry.label("NL123456789") == "31 (Dubbel)"
    assert registry.cow_with_number("12") == PINK
    assert problems == [
        "Regel 4: 'geen nummer' is geen levensnummer, verwacht bijvoorbeeld NL123456789"
    ]


def test_import_without_header_uses_number_life_number_name(tmp_path):
    from aidetector.cows.importer import import_cows

    export = tmp_path / "export.csv"
    export.write_text("30,NL123456789,Bertha\n12,NL987654321\n")
    registry = CowRegistry(tmp_path / "koeien")

    assert import_cows(export, registry) == (2, [])
    assert registry.label(OLD) == "30 (Bertha)"
