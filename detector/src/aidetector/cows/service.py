import json
import logging
import secrets
import shutil
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from threading import Lock, RLock
from typing import Any

import requests
from numpy import ndarray

from aidetector.cows.registry import (
    CowRegistry,
    NumberTaken,
    normalize_life_number,
    normalize_number,
)
from aidetector.cows.reid import Gallery, dinov2_embedder, model_file
from aidetector.cows.split import Box, CowPair, CowSplitter, yolo_cow_detector
from aidetector.media.video import get_crop, get_image
from aidetector.sources.hires import hires_detection
from aidetector.utils.config import CowsConfig, Detection

SIGHTINGS_FILE = "sprongen.jsonl"
CROPS_FOLDER = ".meldingen"
SLOT_NAMES = ("A", "B")
_MAX_BEFORE = 6

HELP = """🐄 Koeien herkennen

Bij elke melding stuur ik een foto per koe. Tik aan wie het is, dan leer ik haar kennen.

/koe 30 NL123456789 Bertha – nummer 30 hoort bij deze koe (naam mag weg)
/wissel 30 NL987654321 – nummer 30 gaat naar een andere koe, bijv. een pink
/weg 30 – de koe met nummer 30 is van het bedrijf
/koeien – alle koeien met hun nummer en aantal foto's
/overzicht 7 – sprongen per koe over de laatste 7 dagen"""


@dataclass
class Sighting:
    """The two cows of one mount, as recognised or as the farmer told."""

    id: str
    date: str
    camera: str
    event: str | None = None
    feedback: str | None = None
    alert: int | None = None
    # False when the two cows could not be told apart: both photos then show
    # the whole mount and never go into a cow folder.
    split: bool = True
    mounter: int = 0
    role_certain: bool = False
    cows: list[str | None] = field(default_factory=lambda: [None, None])
    # "auto" when recognised, "boer" when the farmer chose, None when open.
    how: list[str | None] = field(default_factory=lambda: [None, None])
    candidates: list[list[tuple[str, float]]] = field(default_factory=lambda: [[], []])
    messages: list[int | None] = field(default_factory=lambda: [None, None])
    # The farmer said the photo does not show one cow of the mount.
    bad_photo: list[bool] = field(default_factory=lambda: [False, False])
    # Open questions for a typed number, so answers still work after a restart.
    prompts: list[int | None] = field(default_factory=lambda: [None, None])
    false: bool = False

    @property
    def when(self) -> datetime:
        return datetime.fromisoformat(self.date)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Sighting":
        data = dict(data)
        data["candidates"] = [
            [(str(cow), float(score)) for cow, score in slot]
            for slot in data.get("candidates", [[], []])
        ]
        return cls(**data)


def _call(api_url: str, method: str, data: dict[str, Any], files=None) -> Any:
    response = requests.post(f"{api_url}/{method}", data=data, files=files, timeout=30)
    if response.status_code >= 400:
        raise RuntimeError(f"Telegram {method} failed: {response.text}")
    return response.json().get("result")


def mount_box(detection: Detection) -> Box | None:
    region = detection.images.crop_region
    if region is None:
        return None
    width, height = detection.images.width, detection.images.height
    return (region.x1 / width, region.y1 / height, region.x2 / width, region.y2 / height)


def event_frames(
    best_detection: Detection, detections: list[Detection]
) -> tuple[list[tuple[datetime, ndarray]], datetime]:
    """Returns the frames from just before the mount, from the 4K stream when
    there is one, and when the mount started. Only the frames used are decoded."""
    start = next(
        (detection.date for detection in detections if detection.confidence),
        best_detection.date,
    )
    items: list[tuple[datetime, Callable[[], ndarray]]]
    if best_detection.hires:
        items = [(frame.date, lambda frame=frame: frame.jpg) for frame in best_detection.hires]
    else:
        items = [
            (detection.date, lambda detection=detection: detection.images.jpg)
            for detection in detections
        ]
    items.sort(key=lambda item: item[0])
    chosen = [item for item in items if item[0] < start][-_MAX_BEFORE:]
    return [(date, decode()) for date, decode in chosen], start


class CowService:
    """Recognises the two cows of each alerted mount, asks the farmer via
    Telegram when unsure and counts the mounts per cow."""

    logger = logging.getLogger("CowService")

    def __init__(
        self,
        token: str,
        chat: str,
        directory: Path,
        config: CowsConfig,
        splitter: CowSplitter | None = None,
        gallery: Gallery | None = None,
    ):
        self.api_url = f"https://api.telegram.org/bot{token}"
        self.chat = chat
        self.config = config
        self.directory = directory
        self.registry = CowRegistry(directory)
        self.sightings_path = directory / SIGHTINGS_FILE
        self.lock = RLock()
        self.model_lock = Lock()
        self.splitter = splitter
        self.gallery = gallery
        self.sightings = self._load()
        # Prompt message ID → (sighting, slot) while the farmer types a number.
        self.prompts: dict[int, tuple[str, int]] = {
            prompt: (sighting.id, slot)
            for sighting in self.sightings.values()
            for slot, prompt in enumerate(sighting.prompts)
            if prompt is not None
        }
        # Pending /wissel questions.
        self.switches: dict[str, tuple[str, str, str | None]] = {}
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="cows")

    # Recognition

    def submit(
        self,
        best_detection: Detection,
        detections: list[Detection],
        alert: int,
        event: str | None,
        feedback: str | None,
    ) -> None:
        def task():
            try:
                self.identify(best_detection, detections, alert, event, feedback)
            except Exception:
                self.logger.exception("Failed to recognise the cows of a mount")

        self.executor.submit(task)

    def identify(
        self,
        best_detection: Detection,
        detections: list[Detection],
        alert: int | None,
        event: str | None,
        feedback: str | None,
    ) -> Sighting | None:
        box = mount_box(best_detection)
        if box is None:
            return None
        splitter, gallery = self._models()
        frames, start = event_frames(best_detection, detections)
        pair = splitter.split(frames, box, start) if frames else None
        sighting = Sighting(
            id=secrets.token_hex(4),
            date=start.isoformat(),
            camera=best_detection.camera or best_detection.source or "",
            event=event,
            feedback=feedback,
            alert=alert,
        )
        crops = self._crops(sighting, pair, best_detection)
        if pair is not None:
            embeddings = [gallery.match(crop)[1] for crop in crops]
            sighting.candidates = [
                [(cow, round(score, 3)) for cow, score in scores[: self.config.candidates]]
                for scores in embeddings
            ]
            self._accept(sighting, embeddings)
        folder = self.directory / CROPS_FOLDER / sighting.id
        folder.mkdir(parents=True, exist_ok=True)
        for slot, crop in enumerate(crops):
            (folder / f"{SLOT_NAMES[slot]}.jpg").write_bytes(get_image(crop, 95))
        with self.lock:
            self.sightings[sighting.id] = sighting
            self._store(sighting)
        self._send(sighting)
        return sighting

    def _models(self) -> tuple[CowSplitter, Gallery]:
        with self.model_lock:
            if self.splitter is None:
                self.splitter = CowSplitter(
                    yolo_cow_detector(
                        self.config.segment_model, self.config.segment_confidence
                    )
                )
            if self.gallery is None:
                self.gallery = Gallery(
                    self.registry,
                    dinov2_embedder(model_file(self.config.reid_model, self.directory)),
                )
            return self.splitter, self.gallery

    def _crops(
        self, sighting: Sighting, pair: CowPair | None, best_detection: Detection
    ) -> list[ndarray]:
        if pair is not None:
            sighting.split = True
            sighting.mounter = pair.mounter
            sighting.role_certain = pair.certain
            return pair.crops
        # Both photos show the whole mount; the farmer picks the cow per role.
        sighting.split = False
        sighting.mounter = 0
        sighting.role_certain = True
        detection = hires_detection(best_detection) or best_detection
        crop = get_crop(detection, aspect_ratio=None, padding=0.3, plot=False)
        if crop is None:
            crop = detection.images.jpg
        return [crop, crop]

    def _accept(
        self, sighting: Sighting, scores: list[list[tuple[str, float]]]
    ) -> None:
        """Fills in a cow without asking only when she clearly looks most like
        one cow that already has enough photos."""
        picks: list[str | None] = []
        for slot_scores in scores:
            pick = None
            if slot_scores:
                cow, score = slot_scores[0]
                runner_up = slot_scores[1][1] if len(slot_scores) > 1 else 0.0
                if (
                    score >= self.config.accept_score
                    and score - runner_up >= self.config.accept_margin
                    and len(self.registry.photos(cow)) >= self.config.min_photos
                ):
                    pick = cow
            picks.append(pick)
        if picks[0] is not None and picks[0] == picks[1]:
            # One cow cannot mount herself.
            picks = [None, None]
        for slot, pick in enumerate(picks):
            if pick is not None:
                sighting.cows[slot] = pick
                sighting.how[slot] = "auto"

    # Telegram messages

    def _send(self, sighting: Sighting) -> None:
        folder = self.directory / CROPS_FOLDER / sighting.id
        for slot in range(2):
            data = {
                "chat_id": self.chat,
                "caption": self.caption(sighting, slot),
                "reply_markup": self.keyboard(sighting, slot),
                "disable_notification": "true",
            }
            if sighting.alert is not None:
                data["reply_to_message_id"] = str(sighting.alert)
                data["allow_sending_without_reply"] = "true"
            photo = (folder / f"{SLOT_NAMES[slot]}.jpg").read_bytes()
            result = _call(
                self.api_url,
                "sendPhoto",
                data,
                files={"photo": (f"{sighting.id}_{SLOT_NAMES[slot]}.jpg", photo, "image/jpeg")},
            )
            with self.lock:
                sighting.messages[slot] = int(result["message_id"])
                self._store(sighting)

    def _role(self, sighting: Sighting, slot: int) -> str:
        role = "sprong" if slot == sighting.mounter else "werd besprongen"
        return role if sighting.role_certain else f"{role} (gok)"

    def caption(self, sighting: Sighting, slot: int) -> str:
        at = sighting.when
        if sighting.split:
            title = f"🐄 Koe {SLOT_NAMES[slot]} {self._role(sighting, slot)}"
        else:
            title = (
                "🐄 Welke koe sprong?"
                if slot == sighting.mounter
                else "🐄 Welke koe werd besprongen?"
            )
        cow, how = sighting.cows[slot], sighting.how[slot]
        if how == "auto":
            score = dict(sighting.candidates[slot]).get(cow or "", 0)
            status = f"✅ {self.registry.label(cow, at)} · herkend ({score:.0%})"
        elif how == "boer" and sighting.bad_photo[slot]:
            status = "🚫 Foto klopt niet"
        elif how == "boer":
            status = (
                f"✅ {self.registry.label(cow, at)}" if cow else "❔ Onbekend"
            )
        elif sighting.candidates[slot]:
            status = "Wie is dit? Tik een nummer aan."
        else:
            status = "Wie is dit? Tik op ✏️ en typ het nummer."
        return f"{title}\n{status}"

    def keyboard(self, sighting: Sighting, slot: int) -> str:
        at = sighting.when
        prefix = f"cow:{sighting.id}:{slot}"
        chosen = sighting.cows[slot]
        rows = []
        candidates = [
            {
                "text": f"{'✅ ' if cow == chosen else ''}{self.registry.label(cow, at)}"
                f" · {score:.0%}",
                "callback_data": f"{prefix}:c{index}",
            }
            for index, (cow, score) in enumerate(sighting.candidates[slot])
            if self.registry.cow(cow) is not None
        ]
        if candidates:
            rows.append(candidates)
        rows.append(
            [
                {"text": "✏️ Ander nummer", "callback_data": f"{prefix}:n"},
                {"text": "❔ Onbekend", "callback_data": f"{prefix}:u"},
            ]
        )
        if sighting.split:
            rows.append(
                [
                    {"text": "🔄 Andersom (wie sprong)", "callback_data": f"{prefix}:s"},
                    {"text": "🚫 Foto klopt niet", "callback_data": f"{prefix}:x"},
                ]
            )
        return json.dumps({"inline_keyboard": rows})

    def _refresh(self, sighting: Sighting) -> None:
        for slot, message in enumerate(sighting.messages):
            if message is None:
                continue
            try:
                _call(
                    self.api_url,
                    "editMessageCaption",
                    {
                        "chat_id": self.chat,
                        "message_id": str(message),
                        "caption": self.caption(sighting, slot),
                        "reply_markup": self.keyboard(sighting, slot),
                    },
                )
            except RuntimeError as error:
                # Telegram refuses an edit that changes nothing.
                if "not modified" not in str(error):
                    raise

    # Farmer input

    def handle_callback(self, data: str) -> str:
        """Handles a button and returns the short text to show the farmer."""
        if data.startswith("cowswitch:"):
            return self._answer_switch(data)
        _, sighting_id, slot_text, action = data.split(":")
        slot = int(slot_text)
        with self.lock:
            sighting = self.sightings.get(sighting_id)
        if sighting is None or slot not in (0, 1):
            return "Deze melding is niet meer te vinden."
        if action == "s":
            with self.lock:
                sighting.mounter = 1 - sighting.mounter
                sighting.role_certain = True
                self._store(sighting)
            self._refresh(sighting)
            return "Rollen omgedraaid"
        if action == "u":
            self._choose(sighting, slot, None)
            return "Opgeslagen als onbekend"
        if action == "x":
            self._choose(sighting, slot, None, file=False)
            return "Opgeslagen, deze foto gebruik ik niet"
        if action == "n":
            self._ask_number(sighting, slot)
            return "Typ het nummer als antwoord"
        if action.startswith("c"):
            index = int(action[1:])
            if index >= len(sighting.candidates[slot]):
                return "Deze keuze bestaat niet meer."
            cow = sighting.candidates[slot][index][0]
            self._choose(sighting, slot, cow)
            return f"Opgeslagen als {self.registry.label(cow, sighting.when)}"
        raise ValueError(f"Unknown cow action {action}")

    def _ask_number(self, sighting: Sighting, slot: int) -> None:
        name = f"koe {SLOT_NAMES[slot]}" if sighting.split else (
            "de koe die sprong" if slot == sighting.mounter else "de koe die werd besprongen"
        )
        result = _call(
            self.api_url,
            "sendMessage",
            {
                "chat_id": self.chat,
                "text": f"Typ het halsbandnummer van {name}.\n"
                "Nieuwe koe? Typ nummer en levensnummer, bijv. 30 NL123456789",
                "reply_to_message_id": str(sighting.messages[slot] or ""),
                "allow_sending_without_reply": "true",
                "reply_markup": json.dumps(
                    {"force_reply": True, "input_field_placeholder": "30"}
                ),
            },
        )
        with self.lock:
            self.prompts[int(result["message_id"])] = (sighting.id, slot)
            sighting.prompts[slot] = int(result["message_id"])
            self._store(sighting)

    def _choose(
        self, sighting: Sighting, slot: int, cow: str | None, file: bool = True
    ) -> None:
        """Stores the farmer's choice and files the photo in the cow's folder,
        which is what the recognition learns from. A photo that does not show
        one cow of the mount (file=False) is never filed."""
        with self.lock:
            filed = sighting.how[slot] == "boer" and not sighting.bad_photo[slot]
            previous = sighting.cows[slot]
            sighting.cows[slot] = cow
            sighting.how[slot] = "boer"
            sighting.bad_photo[slot] = not file
            self._store(sighting)
        if sighting.split:
            name = f"{sighting.when:%Y-%m-%dT%H-%M-%S}_{sighting.id}_{SLOT_NAMES[slot]}.jpg"
            if filed:
                # The farmer changed their mind: take the photo from the old folder.
                (self.registry.folder(previous) / name).unlink(missing_ok=True)
            source = self.directory / CROPS_FOLDER / sighting.id / f"{SLOT_NAMES[slot]}.jpg"
            if file and source.is_file():
                folder = self.registry.folder(cow)
                folder.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, folder / name)
        self._refresh(sighting)

    def handle_message(self, message: dict[str, Any]) -> str | None:
        """Handles a typed command or an answer to a number question and
        returns the reply, or None when the message is not for the cows."""
        text = str(message.get("text") or "").strip()
        reply_to = (message.get("reply_to_message") or {}).get("message_id")
        prompt_id = int(reply_to) if reply_to is not None else None
        with self.lock:
            prompt = self.prompts.get(prompt_id) if prompt_id is not None else None
        if prompt_id is not None and prompt is not None:
            return self._answer_number(prompt_id, prompt, text)
        if not text.startswith("/"):
            return None
        command, *arguments = text.split()
        command = command.split("@")[0].lower()
        try:
            if command == "/koe":
                return self._command_add(arguments)
            if command == "/wissel":
                return self._command_switch(arguments)
            if command == "/weg":
                return self._command_gone(arguments)
            if command == "/koeien":
                return self._command_list()
            if command == "/overzicht":
                if arguments and not arguments[0].isdigit():
                    return "Gebruik: /overzicht 7 (aantal dagen)"
                days = int(arguments[0]) if arguments else 7
                end = datetime.now()
                overview = self.overview_text(end - timedelta(days=days), end)
                return f"🐄 Sprongen per koe, laatste {days} dagen" + (
                    overview or "\n\nNog geen herkende koeien."
                )
            if command in ("/help", "/start"):
                return HELP
        except ValueError as error:
            return f"⚠️ {error}"
        return None

    def _answer_number(self, prompt_id: int, prompt: tuple[str, int], text: str) -> str:
        sighting_id, slot = prompt
        with self.lock:
            sighting = self.sightings.get(sighting_id)
        if sighting is None:
            return "Deze melding is niet meer te vinden."
        parts = text.split()
        if not parts:
            return "Typ een nummer, bijv. 30"
        try:
            number = normalize_number(parts[0])
            at = sighting.when
            if len(parts) > 1:
                cow = self.registry.add(number, "".join(parts[1:]), at=at).life_number
            else:
                cow = self.registry.cow_with_number(number, at) or self.registry.cow_with_number(number)
                if cow is None:
                    return (
                        f"Nummer {number} ken ik nog niet. Antwoord met nummer en "
                        f"levensnummer, bijv. {number} NL123456789"
                    )
        except NumberTaken as error:
            return (
                f"Nummer {error.number} hoort al bij {error.holder}. "
                f"Is het een andere koe? Stuur /wissel {error.number} <levensnummer>"
            )
        except ValueError as error:
            return f"⚠️ {error}"
        with self.lock:
            self.prompts.pop(prompt_id, None)
            sighting.prompts[slot] = None
        self._choose(sighting, slot, cow)
        return f"Opgeslagen: {self.registry.label(cow, sighting.when)}"

    def _command_add(self, arguments: list[str]) -> str:
        if len(arguments) < 2:
            return "Gebruik: /koe 30 NL123456789 (naam mag erachter)"
        number, life_number, name = self._number_cow_name(arguments)
        try:
            cow = self.registry.add(number, life_number, name)
        except NumberTaken as error:
            return (
                f"Nummer {error.number} hoort al bij {error.holder}. "
                f"Gaat het nummer naar een andere koe? Stuur /wissel {number} {life_number}"
            )
        return f"✅ Nummer {normalize_number(number)} is nu {self.registry.label(cow.life_number)} · {cow.life_number}"

    def _command_switch(self, arguments: list[str]) -> str | None:
        if len(arguments) < 2:
            return "Gebruik: /wissel 30 NL987654321"
        number, life_number, name = self._number_cow_name(arguments)
        life_number = normalize_life_number(life_number)
        number = normalize_number(number)
        old = self.registry.cow_with_number(number)
        if old is None or old == life_number:
            self.registry.add(number, life_number, name)
            return f"✅ Nummer {number} is nu {life_number}"
        token = secrets.token_hex(4)
        with self.lock:
            self.switches[token] = (number, life_number, name)
        _call(
            self.api_url,
            "sendMessage",
            {
                "chat_id": self.chat,
                "text": f"Nummer {number} was {self.registry.label(old)} · {old}.\n"
                f"Is die koe van het bedrijf? Dan archiveer ik haar foto's en begint "
                f"{life_number} met een lege map.",
                "reply_markup": json.dumps(
                    {
                        "inline_keyboard": [
                            [
                                {
                                    "text": "Ja, oude koe is weg",
                                    "callback_data": f"cowswitch:{token}:y",
                                }
                            ],
                            [
                                {
                                    "text": "Nee, alleen halsband gewisseld",
                                    "callback_data": f"cowswitch:{token}:n",
                                }
                            ],
                        ]
                    }
                ),
            },
        )
        return None

    def _answer_switch(self, data: str) -> str:
        _, token, answer = data.split(":")
        with self.lock:
            switch = self.switches.pop(token, None)
        if switch is None:
            return "Deze vraag is verlopen, stuur /wissel opnieuw."
        number, life_number, name = switch
        old = self.registry.switch(number, life_number, answer == "y", name)
        self._send_text(
            f"✅ Nummer {number} is nu {life_number}."
            + (
                f" {old} is gearchiveerd."
                if old and answer == "y"
                else f" {old} heeft nu geen nummer, geef haar er een met /koe."
                if old
                else ""
            )
        )
        return "Opgeslagen"

    def _command_gone(self, arguments: list[str]) -> str:
        if not arguments:
            return "Gebruik: /weg 30 of /weg NL123456789"
        value = "".join(arguments)
        cow = (
            self.registry.cow_with_number(value)
            if value.lstrip("#").isdigit()
            else normalize_life_number(value)
        )
        if cow is None or self.registry.cow(cow) is None:
            return f"Geen koe gevonden voor {value}"
        label = self.registry.label(cow)
        self.registry.archive(cow)
        return f"✅ {label} · {cow} is gearchiveerd, haar sprongen blijven bewaard."

    def _command_list(self) -> str:
        cows = self.registry.active_cows()
        if not cows:
            return "Nog geen koeien. Voeg ze toe met /koe 30 NL123456789"

        def sort_key(cow) -> tuple[int, str]:
            number = self.registry.number_of(cow.life_number)
            return (int(number) if number else 10**9, cow.life_number)

        def photos(cow) -> str:
            amount = len(self.registry.photos(cow.life_number))
            return f"{amount} {'foto' if amount == 1 else 'foto' + chr(39) + 's'}"

        lines = [
            f"• {self.registry.label(cow.life_number)} · {cow.life_number} · {photos(cow)}"
            for cow in sorted(cows, key=sort_key)
        ]
        return f"🐄 {len(cows)} koeien\n\n" + "\n".join(lines)

    @staticmethod
    def _number_cow_name(arguments: list[str]) -> tuple[str, str, str | None]:
        """Parses "30 NL 1234 5678 9 Bertha": the life number may contain spaces."""
        number, rest = arguments[0], arguments[1:]
        life_parts: list[str] = []
        for part in rest:
            is_country = not life_parts and len(part) == 2 and part.isalpha()
            if not (is_country or any(character.isdigit() for character in part)):
                break
            life_parts.append(part)
        name = " ".join(rest[len(life_parts) :]) or None
        return number, "".join(life_parts), name

    def _send_text(self, text: str) -> None:
        _call(self.api_url, "sendMessage", {"chat_id": self.chat, "text": text})

    def feedback(self, feedback_id: str, label: str) -> None:
        """A mount marked as wrong no longer counts; the cow photos stay, since
        who the cows are is still right."""
        with self.lock:
            for sighting in self.sightings.values():
                if sighting.feedback == feedback_id:
                    sighting.false = label == "bad"
                    self._store(sighting)

    # Overview

    def overview_text(self, start: datetime, end: datetime) -> str:
        """Lines per cow for the summary: how often she was mounted, which
        points at heat, and how often she jumped herself."""
        with self.lock:
            sightings = [
                sighting
                for sighting in self.sightings.values()
                if not sighting.false and start <= sighting.when < end
            ]
        if not sightings:
            return ""
        counts: dict[str, list[int]] = {}
        labels: dict[str, str] = {}
        unknown = 0
        for sighting in sightings:
            for slot, cow in enumerate(sighting.cows):
                if cow is None:
                    unknown += 1
                    continue
                counts.setdefault(cow, [0, 0])[0 if slot != sighting.mounter else 1] += 1
                labels.setdefault(cow, self.registry.label(cow, sighting.when))
        lines = []
        for cow, (mounted, mounting) in sorted(
            counts.items(), key=lambda item: (-item[1][0], -item[1][1], labels[item[0]])
        ):
            parts = []
            if mounted:
                parts.append(f"{mounted}× besprongen")
            if mounting:
                parts.append(f"{mounting}× gesprongen")
            lines.append(f"{'🔥' if mounted else '•'} {labels[cow]}: {', '.join(parts)}")
        text = "\n\nPer koe (🔥 = besprongen, mogelijk tochtig):\n" + "\n".join(lines)
        if unknown:
            text += f"\n• Niet herkend: {unknown} {'koe' if unknown == 1 else 'koeien'}"
        return text

    # Storage

    def _store(self, sighting: Sighting) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        with self.sightings_path.open("a") as file:
            file.write(json.dumps(asdict(sighting)) + "\n")

    def _load(self) -> dict[str, Sighting]:
        if not self.sightings_path.is_file():
            return {}
        sightings: dict[str, Sighting] = {}
        for line in self.sightings_path.read_text().splitlines():
            try:
                sighting = Sighting.from_dict(json.loads(line))
            except (ValueError, KeyError, TypeError):
                self.logger.warning("Skipping invalid cow sighting: %s", line)
                continue
            sightings[sighting.id] = sighting
        # Keep one line per sighting, the last state.
        self.sightings_path.write_text(
            "".join(json.dumps(asdict(sighting)) + "\n" for sighting in sightings.values())
        )
        return sightings


_cow_services: dict[tuple[str, str], CowService] = {}
_cow_services_lock = Lock()


def get_cow_service(
    token: str, chat: str, feedback_directory: Path, config: CowsConfig
) -> CowService:
    directory = (
        config.directory.expanduser().resolve()
        if config.directory
        else feedback_directory.expanduser().resolve() / "koeien"
    )
    key = (token, str(chat))
    with _cow_services_lock:
        if key not in _cow_services:
            _cow_services[key] = CowService(token, str(chat), directory, config)
        return _cow_services[key]
