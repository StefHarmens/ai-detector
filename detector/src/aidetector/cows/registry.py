import json
import re
import shutil
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, time
from pathlib import Path
from threading import RLock

# Country code and 9 to 12 digits, e.g. NL123456789. The check digit is not
# verified: the rule differs per country.
_LIFE_NUMBER = re.compile(r"^[A-Z]{2}\d{9,12}$")

UNKNOWN_FOLDER = "onbekend"
ARCHIVE_FOLDER = "archief"
REGISTRY_FILE = "koeien.json"

# A moment in time; a date means the start of that day.
Moment = datetime | date | None


def moment(value: Moment) -> datetime:
    if value is None:
        return datetime.now()
    if isinstance(value, datetime):
        return value
    return datetime.combine(value, time.min)


def normalize_life_number(value: str) -> str:
    """Returns the I&R life number without spaces, dots or dashes, e.g.
    "nl 1234.5678-9" becomes "NL123456789"."""
    life_number = re.sub(r"[\s.\-]", "", value).upper()
    if not _LIFE_NUMBER.match(life_number):
        raise ValueError(
            f"{value!r} is geen levensnummer, verwacht bijvoorbeeld NL123456789"
        )
    return life_number


def normalize_number(value: str) -> str:
    number = value.strip().lstrip("#")
    if not number.isdigit():
        raise ValueError(f"{value!r} is geen halsbandnummer")
    return str(int(number))


@dataclass
class Cow:
    life_number: str
    name: str | None = None
    archived: str | None = None


@dataclass
class NumberPeriod:
    """Collar numbers are given to a new cow once the old one leaves, so a
    number belongs to a cow only between two moments. A number can change on
    the day of a mount, so the moments have a time."""

    number: str
    cow: str
    start: str
    end: str | None = None

    def covers(self, at: datetime) -> bool:
        return datetime.fromisoformat(self.start) <= at and (
            self.end is None or at < datetime.fromisoformat(self.end)
        )


@dataclass
class RegistryData:
    cows: dict[str, Cow] = field(default_factory=dict)
    numbers: list[NumberPeriod] = field(default_factory=list)


class CowRegistry:
    """Keeps the cows by I&R life number, which collar number each had when,
    and one photo folder per cow."""

    def __init__(self, directory: Path):
        self.directory = directory
        self.path = directory / REGISTRY_FILE
        self.lock = RLock()
        self.data = self._load()

    def cow(self, life_number: str) -> Cow | None:
        return self.data.cows.get(life_number)

    def active_cows(self) -> list[Cow]:
        return [cow for cow in self.data.cows.values() if cow.archived is None]

    def number_of(self, life_number: str, at: Moment = None) -> str | None:
        at = moment(at)
        return next(
            (
                period.number
                for period in self.data.numbers
                if period.cow == life_number and period.covers(at)
            ),
            None,
        )

    def cow_with_number(self, number: str, at: Moment = None) -> str | None:
        at = moment(at)
        number = normalize_number(number)
        return next(
            (
                period.cow
                for period in self.data.numbers
                if period.number == number and period.covers(at)
            ),
            None,
        )

    def label(self, life_number: str | None, at: Moment = None) -> str:
        """The name the farmer knows a cow by: her collar number at that moment,
        or her number now for a mount from before she was added. A cow that left
        is marked, since her number may be worn by another cow now."""
        if life_number is None:
            return "onbekend"
        cow = self.cow(life_number)
        number = self.number_of(life_number, at) or self.number_of(life_number)
        label = number or life_number
        if cow and cow.name:
            label += f" ({cow.name})"
        if cow and cow.archived:
            label += " · weg"
        return label

    def add(
        self, number: str, life_number: str, name: str | None = None, at: Moment = None
    ) -> Cow:
        """Gives the collar number to a cow. Fails when another cow still wears
        the number; that needs switch() so the farmer decides about the old cow."""
        at = moment(at)
        number = normalize_number(number)
        life_number = normalize_life_number(life_number)
        with self.lock:
            holder = self.cow_with_number(number, at)
            if holder is not None and holder != life_number:
                raise NumberTaken(number, holder)
            cow = self._ensure_cow(life_number, name)
            if holder is None:
                self._end_numbers(life_number, at)
                self.data.numbers.append(NumberPeriod(number, life_number, at.isoformat(timespec="seconds")))
            self._save()
            return cow

    def switch(
        self,
        number: str,
        life_number: str,
        old_cow_left: bool,
        name: str | None = None,
        at: Moment = None,
    ) -> str | None:
        """Gives the collar number to another cow and returns the cow that wore
        it. When she left the farm she is archived; otherwise (collars swapped)
        she only loses the number."""
        at = moment(at)
        number = normalize_number(number)
        life_number = normalize_life_number(life_number)
        with self.lock:
            old = self.cow_with_number(number, at)
            if old == life_number:
                self._ensure_cow(life_number, name)
                self._save()
                return None
            if old is not None:
                self._end_number(number, at)
                if old_cow_left:
                    self.archive(old, at)
            self._ensure_cow(life_number, name)
            self._end_numbers(life_number, at)
            self.data.numbers.append(NumberPeriod(number, life_number, at.isoformat(timespec="seconds")))
            self._save()
            return old

    def archive(self, life_number: str, at: Moment = None) -> None:
        """Marks a cow as gone: she keeps her history but her photos no longer
        take part in recognition."""
        at = moment(at)
        with self.lock:
            cow = self.data.cows.get(life_number)
            if cow is None:
                raise KeyError(life_number)
            self._end_numbers(life_number, at)
            cow.archived = at.date().isoformat()
            folder = self.directory / life_number
            if folder.is_dir():
                archive = self.directory / ARCHIVE_FOLDER / life_number
                archive.parent.mkdir(parents=True, exist_ok=True)
                if archive.exists():
                    for photo in folder.iterdir():
                        shutil.move(str(photo), archive / photo.name)
                    folder.rmdir()
                else:
                    shutil.move(str(folder), archive)
            self._save()

    def folder(self, life_number: str | None) -> Path:
        if life_number is None:
            return self.directory / UNKNOWN_FOLDER
        cow = self.cow(life_number)
        if cow is not None and cow.archived:
            return self.directory / ARCHIVE_FOLDER / life_number
        return self.directory / life_number

    def photos(self, life_number: str) -> list[Path]:
        folder = self.folder(life_number)
        if not folder.is_dir():
            return []
        return sorted(folder.glob("*.jpg"))

    def _ensure_cow(self, life_number: str, name: str | None) -> Cow:
        cow = self.data.cows.get(life_number)
        if cow is None:
            cow = self.data.cows[life_number] = Cow(life_number, name)
        elif name:
            cow.name = name
        if cow.archived:
            # A cow that comes back (e.g. from the young stock barn).
            cow.archived = None
            archive = self.directory / ARCHIVE_FOLDER / life_number
            if archive.is_dir() and not (self.directory / life_number).exists():
                shutil.move(str(archive), self.directory / life_number)
        return cow

    def _end_number(self, number: str, at: datetime) -> None:
        for period in self.data.numbers:
            if period.number == number and period.covers(at):
                period.end = at.isoformat(timespec="seconds")

    def _end_numbers(self, life_number: str, at: datetime) -> None:
        for period in self.data.numbers:
            if period.cow == life_number and period.covers(at):
                period.end = at.isoformat(timespec="seconds")
        # A number given and taken back at the same moment never covered one.
        self.data.numbers = [
            period
            for period in self.data.numbers
            if period.end is None
            or datetime.fromisoformat(period.end) > datetime.fromisoformat(period.start)
        ]

    def _load(self) -> RegistryData:
        if not self.path.is_file():
            return RegistryData()
        raw = json.loads(self.path.read_text())
        return RegistryData(
            cows={
                life_number: Cow(**cow) for life_number, cow in raw.get("cows", {}).items()
            },
            numbers=[NumberPeriod(**period) for period in raw.get("numbers", [])],
        )

    def _save(self) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(
                {
                    "cows": {
                        life_number: asdict(cow)
                        for life_number, cow in self.data.cows.items()
                    },
                    "numbers": [asdict(period) for period in self.data.numbers],
                },
                indent=2,
            )
        )
        temporary.replace(self.path)


class NumberTaken(ValueError):
    def __init__(self, number: str, holder: str):
        super().__init__(f"Nummer {number} hoort bij {holder}")
        self.number = number
        self.holder = holder
