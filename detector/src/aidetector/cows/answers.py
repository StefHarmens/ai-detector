import re
from dataclasses import dataclass

from aidetector.cows.registry import normalize_life_number, normalize_number

UNKNOWN = ("?", "-", "onbekend")
_COUNTRY = re.compile(r"^[A-Za-z]{2}")
# Most I&R life numbers have 9 digits after the country code.
_LIFE_DIGITS = 9


@dataclass
class Answer:
    # None when the farmer does not know the cow.
    number: str | None
    # Given for a cow the bot does not know yet.
    life_number: str | None = None


def parse_answers(text: str) -> list[Answer]:
    """Reads the numbers the farmer typed, in order: "30 12", "? 12", or a
    new cow with her life number, "44 NL123456789 12". A life number may be
    typed with spaces ("NL 1234 5678 9"); its digits are read until there
    are nine."""
    tokens = text.replace(",", " ").split()
    answers: list[Answer] = []
    index = 0
    while index < len(tokens):
        token = tokens[index]
        index += 1
        if token.lower() in UNKNOWN:
            answers.append(Answer(None))
            continue
        number = normalize_number(token)
        life_number = None
        if index < len(tokens) and _COUNTRY.match(tokens[index]):
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
