import argparse
import csv
from dataclasses import dataclass, field
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


@dataclass
class ImportResult:
    added: int = 0
    problems: list[str] = field(default_factory=list)
    # How many animals got their collar number, and how many their work number.
    by_collar: int = 0
    by_work: int = 0

    def summary(self) -> str:
        """The first line of the reply to the farmer."""
        animals = "dier" if self.added == 1 else "dieren"
        if self.by_collar and self.by_work:
            return (
                f"✅ {self.added} {animals} ingelezen: {self.by_collar} op halsbandnummer, "
                f"{self.by_work} op werknummer (pinken zonder halsband)."
            )
        if self.by_work:
            return f"✅ {self.added} {animals} ingelezen op werknummer."
        return f"✅ {self.added} {'koe' if self.added == 1 else 'koeien'} ingelezen."


def import_cows(path: Path, registry: CowRegistry) -> ImportResult:
    """Adds each row of the CSV (collar or work number, life number, optional
    name) and returns the number added and the problems per row."""
    text = path.read_text(encoding="utf-8-sig")
    dialect = csv.Sniffer().sniff(text.splitlines()[0], delimiters=";,\t")
    rows = list(csv.reader(text.splitlines(), dialect))
    if not rows:
        return ImportResult(problems=["Het bestand is leeg"])
    header = rows[0]
    life = _column(header, _LIFE_COLUMNS, set())
    taken = {life} if life is not None else set()
    # (column, whether it holds collar numbers)
    numbers: list[tuple[int, bool]] = []
    for kind, names in enumerate(_NUMBER_COLUMNS):
        index = _column(header, names, taken)
        if index is not None:
            numbers.append((index, kind == 0))
            taken.add(index)
    name = _column(header, _NAME_COLUMNS, taken)
    if life is None or not numbers:
        # No recognised header: number, life number, name.
        life, numbers, name = 1, [(0, True)], 2
    else:
        rows = rows[1:]

    result = ImportResult()
    for line, row in enumerate(rows, start=1):
        if not any(cell.strip() for cell in row):
            continue
        number, collar = next(
            ((_cell(row, index), collar) for index, collar in numbers if _cell(row, index)),
            ("", True),
        )
        try:
            registry.add(number, _cell(row, life), _cell(row, name) or None)
        except NumberTaken as error:
            result.problems.append(
                f"Regel {line}: nummer {error.number} hoort al bij {error.holder},"
                " gebruik /wissel in Telegram"
            )
            continue
        except ValueError as error:
            result.problems.append(f"Regel {line}: {error}")
            continue
        result.added += 1
        if collar:
            result.by_collar += 1
        else:
            result.by_work += 1
    return result


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
    result = import_cows(arguments.csv, CowRegistry(directory.resolve()))
    print(f"{result.summary().removeprefix('✅ ')} Map: {directory}")
    for problem in result.problems:
        print(problem)
