import argparse
import csv
from pathlib import Path

from aidetector.cows.registry import CowRegistry, NumberTaken

# Column names as they appear in exports of herd management programs.
_LIFE_COLUMNS = ("levensnummer", "life number", "lifenumber", "i&r")
# The farmer calls a cow by her collar number; a heifer without a collar by
# her work number. The first column with a value in a row is used.
_NUMBER_COLUMNS = (
    ("halsband", "responder", "collar"),
    ("werknummer", "diernummer"),
    ("nummer", "number"),
)
_NAME_COLUMNS = ("naam", "name", "roepnaam")


def _column(header: list[str], names: tuple[str, ...], skip: set[int]) -> int | None:
    lowered = [column.strip().lower() for column in header]
    for name in names:
        for index, column in enumerate(lowered):
            if index not in skip and name in column:
                return index
    return None


def _cell(row: list[str], index: int | None) -> str:
    return row[index].strip() if index is not None and index < len(row) else ""


def import_cows(path: Path, registry: CowRegistry) -> tuple[int, list[str]]:
    """Adds each row of the CSV (collar or work number, life number, optional
    name) and returns the number added and the problems per row."""
    text = path.read_text(encoding="utf-8-sig")
    dialect = csv.Sniffer().sniff(text.splitlines()[0], delimiters=";,\t")
    rows = list(csv.reader(text.splitlines(), dialect))
    if not rows:
        return 0, ["Het bestand is leeg"]
    header = rows[0]
    life = _column(header, _LIFE_COLUMNS, set())
    taken = {life} if life is not None else set()
    numbers: list[int] = []
    for names in _NUMBER_COLUMNS:
        index = _column(header, names, taken)
        if index is not None:
            numbers.append(index)
            taken.add(index)
    name = _column(header, _NAME_COLUMNS, taken)
    if life is None or not numbers:
        # No recognised header: number, life number, name.
        life, numbers, name = 1, [0], 2
    else:
        rows = rows[1:]

    added, problems = 0, []
    for line, row in enumerate(rows, start=1):
        if not any(cell.strip() for cell in row):
            continue
        try:
            number = next((_cell(row, index) for index in numbers if _cell(row, index)), "")
            registry.add(number, _cell(row, life), _cell(row, name) or None)
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
        description="Leest koeien en pinken uit een CSV met halsband- of werknummer,"
        " levensnummer en (optioneel) naam, bijvoorbeeld een export van het"
        " managementprogramma.",
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
