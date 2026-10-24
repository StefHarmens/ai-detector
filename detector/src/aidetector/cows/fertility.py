"""Fertility data from AgroVision: the reports "Vruchtbaarheid MK" (cows) and
"Vruchtbaarheid JV" (young stock), fetched by the AgroVision connector next to
the herd list. Each row is linked to an animal by her Diernr, which is the
number the cows are known by, so a renumbered animal keeps following hers."""

import json
import os
import re
from pathlib import Path
from typing import Any

FILE = ".vruchtbaarheid.json"
COWS = "koeien"
HEADS = "jongvee"
GROUPS = (COWS, HEADS)
LABELS = {COWS: "Vruchtbaarheid MK", HEADS: "Vruchtbaarheid JV"}
# What each report must have besides the Diernr, in the order of the page.
FIELDS = {
    COWS: ("status", "days_in_status", "lact_days", "inseminations"),
    HEADS: ("age_months", "status", "days_in_status", "inseminations"),
}
COLUMN_NAMES = {
    "status": "Status",
    "days_in_status": "Dgn status",
    "lact_days": "Lactdgn",
    "inseminations": "#Insem",
    "age_months": "Lft mnd",
}
_HEADER_ROWS = 10
_TEXT_FIELDS = ("status",)


def _norm(value: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value or "").lower())


def _is(field: str, name: str) -> bool:
    """Whether a header, without spaces, dots and #, is this field."""
    if field == "diernr":
        return name in ("diernr", "diernummer")
    if field == "status":
        return name == "status"
    if field == "days_in_status":
        return name in ("dgnstatus", "dagenstatus") or (
            "status" in name and name.startswith(("dgn", "dagen"))
        )
    if field == "lact_days":
        # Not the lactation number ("Lact nr").
        return name.startswith("lact") and ("dgn" in name or "dag" in name)
    if field == "inseminations":
        # "#Insem", "# Ins.", "#Isem".
        return name in ("ins", "isem") or name.startswith("insem")
    if field == "age_months":
        # "Lft mnd" or "Lft mdn".
        return name.startswith("lft") or name.startswith("leeftijd")
    return False


def _number(value: Any) -> int | float | None:
    text = str(value if value is not None else "").strip().replace(",", ".")
    if not text:
        return None
    try:
        number = float(text)
    except ValueError:
        return None
    return int(number) if number.is_integer() else number


def parse(rows: list[list[Any]], group: str) -> dict[str, dict[str, Any]]:
    """The animals of a fertility report by Diernr. Raises ValueError, in
    Dutch, when a column is missing."""
    wanted = ("diernr", *FIELDS[group])
    for index, row in enumerate(rows[:_HEADER_ROWS]):
        names = [_norm(cell) for cell in row]
        if any(_is("diernr", name) for name in names):
            header, start = names, index + 1
            break
    else:
        raise ValueError("de kolom Diernr staat niet in het rapport")
    columns: dict[str, int] = {}
    for field in wanted:
        for position, name in enumerate(header):
            if position not in columns.values() and name and _is(field, name):
                columns[field] = position
                break
    missing = [COLUMN_NAMES[field] for field in wanted if field != "diernr" and field not in columns]
    if missing:
        shown = ", ".join(str(cell).strip() for cell in rows[start - 1] if str(cell or "").strip())
        raise ValueError(f"de kolom {' en '.join(missing)} ontbreekt in het rapport (kolommen: {shown})")

    animals: dict[str, dict[str, Any]] = {}
    for row in rows[start:]:
        diernr = str(row[columns["diernr"]] if columns["diernr"] < len(row) else "").strip().lstrip("#")
        if not diernr.isdigit():
            continue
        record: dict[str, Any] = {}
        for field in FIELDS[group]:
            cell = row[columns[field]] if columns[field] < len(row) else None
            if field in _TEXT_FIELDS:
                record[field] = (str(cell).strip() or None) if cell is not None else None
            else:
                record[field] = _number(cell)
        animals[str(int(diernr))] = record
    return animals


def load(directory: Path) -> dict[str, Any]:
    try:
        return json.loads((directory / FILE).read_text())
    except (FileNotFoundError, ValueError):
        return {}


def save(directory: Path, group: str, animals: dict[str, dict[str, Any]], updated: str) -> None:
    """Replaces the data of one group; the other group is kept."""
    data = load(directory)
    data[group] = {"updated": updated, "animals": animals}
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / FILE
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(data))
    os.replace(temp, path)


def lookup(data: dict[str, Any], number: str | None) -> dict[str, Any] | None:
    """The fertility of the animal with this Diernr, with her group and the
    time the report was fetched."""
    if number is None:
        return None
    for group in GROUPS:
        found = (data.get(group) or {}).get("animals", {}).get(str(number))
        if found is not None:
            return {"group": group, "updated": data[group].get("updated"), **found}
    return None
