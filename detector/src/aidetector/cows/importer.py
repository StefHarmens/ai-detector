import argparse
import csv
from pathlib import Path

from aidetector.cows.registry import CowRegistry, NumberTaken

# Column names as they appear in exports of herd management programs.
_LIFE_COLUMNS = ("levensnummer", "life number", "lifenumber", "i&r")
_NUMBER_COLUMNS = ("werknummer", "halsband", "diernummer", "nummer", "number")
_NAME_COLUMNS = ("naam", "name", "roepnaam")


def _column(header: list[str], names: tuple[str, ...]) -> int | None:
    lowered = [column.strip().lower() for column in header]
    for name in names:
        for index, column in enumerate(lowered):
            if name in column:
                return index
    return None


def import_cows(path: Path, registry: CowRegistry) -> tuple[int, list[str]]:
    """Adds each row of the CSV (collar number, life number, optional name) and
    returns the number added and the problems per row."""
    text = path.read_text(encoding="utf-8-sig")
    dialect = csv.Sniffer().sniff(text.splitlines()[0], delimiters=";,\t")
    rows = list(csv.reader(text.splitlines(), dialect))
    if not rows:
        return 0, ["Het bestand is leeg"]
    header = rows[0]
    life, number, name = (
        _column(header, _LIFE_COLUMNS),
        _column(header, _NUMBER_COLUMNS),
        _column(header, _NAME_COLUMNS),
    )
    if life is None or number is None or life == number:
        # No recognised header: number, life number, name.
        life, number, name = 1, 0, 2
    else:
        rows = rows[1:]

    added, problems = 0, []
    for line, row in enumerate(rows, start=1):
        if not any(cell.strip() for cell in row):
            continue
        try:
            cow_name = row[name].strip() if name is not None and name < len(row) else ""
            registry.add(row[number], row[life], cow_name or None)
            added += 1
        except NumberTaken as error:
            problems.append(
                f"Regel {line}: nummer {error.number} hoort al bij {error.holder},"
                " gebruik /wissel in Telegram"
            )
        except (ValueError, IndexError) as error:
            problems.append(f"Regel {line}: {error}")
    return added, problems


def _configured_directory() -> Path:
    from aidetector.utils.config import ChatConfig, config

    for detector in config.detectors:
        telegram = detector.exporters.telegram if detector.exporters else None
        for chat in [telegram] if isinstance(telegram, ChatConfig) else telegram or []:
            if chat.cows:
                return (
                    chat.cows.directory
                    or chat.feedback_directory / "koeien"
                ).expanduser()
    raise SystemExit(
        "Geen telegram.cows in config.json; geef de map op met --directory"
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="import-koeien",
        description="Leest koeien uit een CSV met halsbandnummer, levensnummer en"
        " (optioneel) naam, bijvoorbeeld een export van het managementprogramma.",
    )
    parser.add_argument("csv", type=Path)
    parser.add_argument(
        "--directory",
        type=Path,
        help="Map met de koeien; standaard die uit config.json",
    )
    arguments = parser.parse_args()
    directory = arguments.directory or _configured_directory()
    added, problems = import_cows(arguments.csv, CowRegistry(directory.resolve()))
    print(f"{added} koeien toegevoegd aan {directory}")
    for problem in problems:
        print(problem)
