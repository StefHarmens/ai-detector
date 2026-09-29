import re
from dataclasses import dataclass

from aidetector.cows.registry import normalize_life_number, normalize_number

UNKNOWN = ("?", "-", "onbekend")
# A life number starts with a country code: "NL123456789", or "NL 1234 ...".
_COUNTRY = re.compile(r"^[A-Za-z]{2}\d*$")
# Most I&R life numbers have 9 digits after the country code.
_LIFE_DIGITS = 9


@dataclass
class Answer:
    # The number the farmer calls her by; None for a name or an unknown cow.
    number: str | None
    # Given for a cow the bot does not know yet.
    life_number: str | None = None
    # Heifers are often known by name.
    name: str | None = None

    @property
    def unknown(self) -> bool:
        return self.number is None and self.name is None


def _starts_life_number(tokens: list[str], index: int) -> bool:
    if index >= len(tokens) or not _COUNTRY.match(tokens[index]):
        return False
    token = tokens[index]
    # "Jo" is a name, "NL" followed by digits is a life number.
    return any(character.isdigit() for character in token) or (
        index + 1 < len(tokens) and tokens[index + 1].isdigit()
    )


def parse_answers(text: str) -> list[Answer]:
    """Reads the cows the farmer typed, in order: numbers ("30 12"), names
    ("Anna 12"), "?" for unknown, or a new cow with her life number,
    "44 NL123456789 12". A life number may be typed with spaces
    ("NL 1234 5678 9"); its digits are read until there are nine."""
    tokens = text.replace(",", " ").split()
    answers: list[Answer] = []
    index = 0
    while index < len(tokens):
        token = tokens[index]
        index += 1
        if token.lower() in UNKNOWN:
            answers.append(Answer(None))
            continue
        if not token.lstrip("#").isdigit():
            if any(character.isdigit() for character in token):
                raise ValueError(f"{token!r} is geen nummer of naam")
            answers.append(Answer(None, name=token))
            continue
        number = normalize_number(token)
        life_number = None
        if _starts_life_number(tokens, index):
            parts = [tokens[index]]
            digits = sum(character.isdigit() for character in tokens[index])
            index += 1
            while digits < _LIFE_DIGITS and index < len(tokens) and tokens[index].isdigit():
                parts.append(tokens[index])
                digits += len(tokens[index])
                index += 1
            life_number = normalize_life_number("".join(parts))
        answers.append(Answer(number, life_number))
    return answers
