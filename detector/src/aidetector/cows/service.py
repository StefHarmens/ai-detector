import json
import logging
import re
import secrets
import shutil
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime, timedelta
from pathlib import Path
from threading import Event, Lock, RLock, Thread
from typing import Any

import cv2
import requests
from numpy import ndarray

from aidetector.cows.answers import Answer, parse_answers
from aidetector.cows.importer import EXCEL_SUFFIXES, SyncResult, import_cows, sync_herd
from aidetector.cows.photos import control_image, side_by_side
from aidetector.cows.registry import (
    NumberTaken,
    get_registry,
    normalize_life_number,
    normalize_number,
)
from aidetector.cows.reid import Gallery, dinov2_embedder, model_file
from aidetector.cows.split import (
    Box,
    CowPair,
    CowSplitter,
    crop_box,
    yolo_cow_detector,
)
from aidetector.media.video import get_image, telegram_photo
from aidetector.sources.hires import hires_detection
from aidetector.utils.config import CowsConfig, Detection

# One file of mounts per chat: each chat's overview counts its own camera(s).
SIGHTINGS_FILE = "sprongen-{chat}.jsonl"
CROPS_FOLDER = ".meldingen"
FRAMES_FOLDER = "beelden"
VIDEO_FILE = "video.mp4"
IMPORT_FOLDER = ".import"
HERD_STATE_FILE = ".koeienlijst.json"
_HERD_CHECK_SECONDS = 30
# A herd list that changed this recently may still be being saved.
_HERD_SETTLE_SECONDS = 5
SLOT_NAMES = ("A", "B")
# Frames kept per side of the jump, to look back at a mount later.
_KEEP_FRAMES = 3
_FRAMES_DAYS = 14
_SIDE_FRAMES = 6
_MESSAGE_LIMIT = 4000

HELP = """🐄 Koeien herkennen

Bij elke sprong stuur ik één foto met de twee koeien. Tik het goede nummer aan, of antwoord op de foto met de nummers of namen: eerst A (of wie sprong), dan B, bijv. 30 12 of Anna 12.
• Onbekend? Typ een vraagteken: ? 12
• Een nieuwe koe? Typ nummer en levensnummer: 44 NL123456789 12
• Iets fout? Antwoord nog eens met de goede nummers.

Van elke koe die je aantikt leer ik haar vachtpatroon. Heeft een koe 5 foto's en herken ik haar duidelijk, dan vul ik haar zelf in.

Koeien beheren
Het nummer is het halsbandnummer, of het werknummer bij een pink zonder halsband. Krijgt de pink een halsband, geef haar dan dat nummer met /koe; haar sprongen en foto's blijven bij haar.
/koe 30 NL123456789 Bertha – nummer 30 hoort bij deze koe (naam mag weg)
/wissel 30 NL987654321 – nummer 30 gaat naar een andere koe, bijv. een pink
/weg 30 – de koe met nummer 30 (of naam) is van het bedrijf
/koeien – alle koeien met hun nummer en aantal foto's
/overzicht 7 – sprongen per koe over de laatste 7 dagen

Alle koeien in één keer: stuur mij de export uit het managementprogramma als Excel- of CSV-bestand (kolommen zoals Levensnummer, Werknummer, Halsbandnummer, Naam). Staat de lijst in de instellingen (herd_file), dan lees ik hem zelf opnieuw zodra hij verandert."""

COMMANDS = [
    ("koeien", "Alle koeien met nummer en foto's"),
    ("overzicht", "Sprongen per koe, bijv. /overzicht 7"),
    ("koe", "Nummer of werknummer koppelen: /koe 30 NL123456789 Naam"),
    ("wissel", "Nummer naar andere koe: /wissel 30 NL987654321"),
    ("weg", "Koe is van het bedrijf: /weg 30"),
    ("help", "Uitleg over het herkennen"),
]


@dataclass
class Sighting:
    """The two cows of one mount, as recognised or as the farmer told."""

    id: str
    date: str
    camera: str
    event: str | None = None
    feedback: str | None = None
    alert: int | None = None
    # False when the two cows could not be told apart: the photos then show
    # the jump and never go into a cow folder, and slot 0 is the mounter.
    split: bool = True
    mounter: int = 0
    role_certain: bool = False
    cows: list[str | None] = field(default_factory=lambda: [None, None])
    # "auto" when recognised, "boer" when the farmer chose, None when open.
    how: list[str | None] = field(default_factory=lambda: [None, None])
    candidates: list[list[tuple[str, float]]] = field(default_factory=lambda: [[], []])
    # The farmer said the photo does not show one cow of the mount.
    bad_photo: list[bool] = field(default_factory=lambda: [False, False])
    message: int | None = None
    # The open question for typed numbers, so answers work after a restart.
    prompt: int | None = None
    false: bool = False
    # The farmer said the two photos are not the two cows of the mount: they
    # never go into a cow folder, but the cows can still be filled in.
    split_wrong: bool = False

    @property
    def when(self) -> datetime:
        return datetime.fromisoformat(self.date)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Sighting":
        known = {item.name for item in fields(cls)}
        data = {key: value for key, value in data.items() if key in known}
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


@dataclass
class EventFrames:
    # Decoded frames just before and just after the jump.
    frames: list[tuple[datetime, ndarray]]
    # The same frames as stored JPEG, to keep without encoding again.
    jpegs: list[tuple[datetime, bytes]]
    start: datetime
    end: datetime


def event_frames(best_detection: Detection, detections: list[Detection]) -> EventFrames:
    """Returns the frames from just before and just after the jump, from the
    4K stream when there is one. Only the frames used are decoded."""
    confident = [detection.date for detection in detections if detection.confidence]
    start = confident[0] if confident else best_detection.date
    end = confident[-1] if confident else best_detection.date
    items: list[tuple[datetime, bytes, Callable[[], ndarray]]]
    if best_detection.hires:
        items = [
            (frame.date, frame.jpeg, lambda frame=frame: frame.jpg)
            for frame in best_detection.hires
        ]
    else:
        items = [
            (detection.date, detection.images.jpeg, lambda detection=detection: detection.images.jpg)
            for detection in detections
        ]
    items.sort(key=lambda item: item[0])
    chosen = [item for item in items if item[0] < start][-_SIDE_FRAMES:] + [
        item for item in items if item[0] > end
    ][:_SIDE_FRAMES]
    return EventFrames(
        frames=[(date, decode()) for date, _, decode in chosen],
        jpegs=[(date, jpeg) for date, jpeg, _ in chosen],
        start=start,
        end=end,
    )


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
        self.file_url = f"https://api.telegram.org/file/bot{token}"
        self.chat = chat
        self.config = config
        self.directory = directory
        # Shared with the other chats that use this folder.
        self.registry = get_registry(directory)
        safe_chat = re.sub(r"[^A-Za-z0-9_-]", "_", str(chat))
        self.sightings_path = directory / SIGHTINGS_FILE.format(chat=safe_chat)
        self.lock = RLock()
        self.model_lock = Lock()
        self.splitter = splitter
        self.gallery = gallery
        self.sightings = self._load()
        # Message ID of a cow photo or a number question → its sighting.
        self.replies: dict[int, str] = {
            message: sighting.id
            for sighting in self.sightings.values()
            for message in (sighting.message, sighting.prompt)
            if message is not None
        }
        # Pending /wissel questions.
        self.switches: dict[str, tuple[str, str, str | None]] = {}
        # Files a mount as good or bad like the Telegram buttons below the
        # alert; set by the Telegram exporter.
        self.classify: Callable[[str, str], None] | None = None
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="cows")
        self.cleaned = 0.0
        self.stop_event = Event()
        self.herd_missing = False
        if config.herd_file is not None:
            Thread(target=self._watch_herd, name="koeienlijst", daemon=True).start()

    # Herd list

    def _watch_herd(self) -> None:
        while not self.stop_event.is_set():
            try:
                self.check_herd()
            except Exception:
                self.logger.exception("Failed to read the herd list")
            self.stop_event.wait(_HERD_CHECK_SECONDS)

    def check_herd(self, now: float | None = None) -> SyncResult | None:
        """Makes the register follow the herd list when the file changed, and
        tells the farmer what changed. The version read is remembered in the
        folder, so a restart does not repeat the message and chats that share
        the folder read it once."""
        assert self.config.herd_file is not None
        path = self.config.herd_file.expanduser()
        try:
            modified = path.stat().st_mtime
        except FileNotFoundError:
            if not self.herd_missing:
                self.logger.warning("Herd list %s not found", path)
                self._send_text(f"⚠️ De koeienlijst {path} is niet gevonden.")
                self.herd_missing = True
            return None
        self.herd_missing = False
        now = time.time() if now is None else now
        state_path = self.directory / HERD_STATE_FILE
        with self.registry.lock:
            try:
                state = json.loads(state_path.read_text())
            except (FileNotFoundError, ValueError):
                state = {}
            if state.get("file") == str(path) and state.get("modified") == modified:
                return None
            if now - modified < _HERD_SETTLE_SECONDS:
                return None
            result = sync_herd(path, self.registry, categories=self.config.herd_categories)
            self.directory.mkdir(parents=True, exist_ok=True)
            state_path.write_text(json.dumps({"file": str(path), "modified": modified}))
        self.logger.info("%s", result.summary())
        if result.changed or result.problems:
            lines = [result.summary()]
            for label, names in (
                ("Nieuw", result.added),
                ("Terug", result.returned),
                ("Ander nummer", result.renumbered),
                ("Weg", result.archived),
            ):
                if names:
                    shown = ", ".join(names[:10]) + (f" en nog {len(names) - 10}" if len(names) > 10 else "")
                    lines.append(f"{label}: {shown}")
            if result.added and result.skipped:
                lines.append(f"Overgeslagen: {result.skipped} andere dieren (kalveren, mannelijk)")
            lines += result.problems[:10]
            self._send_text("\n".join(lines))
        return result

    # Recognition

    def submit(
        self,
        best_detection: Detection,
        detections: list[Detection],
        alert: int,
        event: str | None,
        feedback: str | None,
        video: bytes | None = None,
    ) -> None:
        def task():
            try:
                self.identify(best_detection, detections, alert, event, feedback, video)
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
        video: bytes | None = None,
    ) -> Sighting | None:
        box = mount_box(best_detection)
        if box is None:
            return None
        splitter, gallery = self._models()
        frames = event_frames(best_detection, detections)
        pair = (
            splitter.split(frames.frames, box, frames.start, frames.end)
            if frames.frames
            else None
        )
        sighting = Sighting(
            id=secrets.token_hex(4),
            date=frames.start.isoformat(),
            camera=best_detection.camera or best_detection.source or "",
            event=event,
            feedback=feedback,
            alert=alert,
        )
        folder = self.directory / CROPS_FOLDER / sighting.id
        folder.mkdir(parents=True, exist_ok=True)
        jump = (hires_detection(best_detection) or best_detection).images.jpg
        if pair is not None:
            self._recognise(sighting, pair, gallery)
            photos = pair.crops
            for slot, masked in enumerate(pair.masked):
                (folder / f"{SLOT_NAMES[slot]}_koe.jpg").write_bytes(get_image(masked, 95))
        else:
            photos = self._jump_photos(sighting, jump, box, splitter)
        for slot, photo in enumerate(photos):
            (folder / f"{SLOT_NAMES[slot]}.jpg").write_bytes(get_image(photo, 95))
        if video:
            # The alert's own video, to look back at the mount on the web page.
            (folder / VIDEO_FILE).write_bytes(video)
        self._keep_frames(folder, frames, jump, box)
        with self.lock:
            self.sightings[sighting.id] = sighting
            self._store(sighting)
        if self.config.telegram:
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

    def _recognise(self, sighting: Sighting, pair: CowPair, gallery: Gallery) -> None:
        sighting.split = True
        sighting.mounter = pair.mounter
        sighting.role_certain = pair.certain
        # The cow folders hold masked photos too, so the barn does not count.
        scores = [gallery.match(masked)[1] for masked in pair.masked]
        sighting.candidates = [
            [(cow, round(score, 3)) for cow, score in slot[: self.config.candidates]]
            for slot in scores
        ]
        self._accept(sighting, scores)

    @staticmethod
    def _jump_photos(
        sighting: Sighting, image: ndarray, box: Box, splitter: CowSplitter
    ) -> list[ndarray]:
        """Photos of the jump, one per role, for the farmer to answer; both
        cows are on them, so they never go into a cow folder."""
        sighting.split = False
        sighting.mounter = 0
        sighting.role_certain = True
        # The mount box fits the mounter; the cow below needs a wider view.
        return [
            crop_box(image, box, padding=0.15),
            crop_box(image, splitter.mounted_region(image, box), padding=0.1),
        ]

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

    def _keep_frames(
        self, folder: Path, frames: EventFrames, jump: ndarray, box: Box
    ) -> None:
        """Keeps a few frames around the jump and the jump with its box drawn,
        to see later why a mount was split the way it was."""
        try:
            (folder / "controle.jpg").write_bytes(get_image(control_image(jump, box), 85))
            before = [item for item in frames.jpegs if item[0] < frames.start][-_KEEP_FRAMES:]
            after = [item for item in frames.jpegs if item[0] > frames.end][:_KEEP_FRAMES]
            if before or after:
                kept = folder / FRAMES_FOLDER
                kept.mkdir(exist_ok=True)
                for date, jpeg in before + after:
                    offset = (date - frames.start).total_seconds()
                    (kept / f"{offset:+06.1f}s.jpg").write_bytes(jpeg)
            self._clean_frames()
        except OSError:
            self.logger.warning("Could not keep the frames of a mount", exc_info=True)

    def _clean_frames(self) -> None:
        """Frames take the most room, so they go after two weeks; the photos
        of the cows stay."""
        if time.time() - self.cleaned < 24 * 3600:
            return
        self.cleaned = time.time()
        oldest = time.time() - _FRAMES_DAYS * 24 * 3600
        for kept in (self.directory / CROPS_FOLDER).glob(f"*/{FRAMES_FOLDER}"):
            if kept.stat().st_mtime < oldest:
                shutil.rmtree(kept, ignore_errors=True)
        oldest_video = time.time() - self.config.video_days * 24 * 3600
        for video in (self.directory / CROPS_FOLDER).glob(f"*/{VIDEO_FILE}"):
            if video.stat().st_mtime < oldest_video:
                video.unlink(missing_ok=True)

    # Telegram message

    def _names(self, sighting: Sighting) -> tuple[str, str]:
        if sighting.split:
            return SLOT_NAMES
        return ("Sprong", "Besprongen")

    def _send(self, sighting: Sighting) -> None:
        folder = self.directory / CROPS_FOLDER / sighting.id
        labels = SLOT_NAMES if sighting.split else ("SPRONG", "BESPRONGEN")
        photo = side_by_side(
            [cv2.imread(str(folder / f"{name}.jpg")) for name in SLOT_NAMES],
            list(labels),
        )
        data = {
            "chat_id": self.chat,
            "caption": self.caption(sighting),
            "reply_markup": self.keyboard(sighting),
            "disable_notification": "true",
        }
        if sighting.alert is not None:
            data["reply_to_message_id"] = str(sighting.alert)
            data["allow_sending_without_reply"] = "true"
        result = _call(
            self.api_url,
            "sendPhoto",
            data,
            files={"photo": (f"{sighting.id}.jpg", telegram_photo(photo), "image/jpeg")},
        )
        with self.lock:
            sighting.message = int(result["message_id"])
            self.replies[sighting.message] = sighting.id
            self._store(sighting)

    def _title(self, sighting: Sighting, slot: int) -> str:
        if not sighting.split:
            return "Sprong" if slot == sighting.mounter else "Werd besprongen"
        role = "sprong" if slot == sighting.mounter else "werd besprongen"
        return f"{SLOT_NAMES[slot]} {role}" + ("" if sighting.role_certain else " (gok)")

    def _status(self, sighting: Sighting, slot: int) -> str:
        cow, how = sighting.cows[slot], sighting.how[slot]
        if how == "auto":
            score = dict(sighting.candidates[slot]).get(cow or "", 0)
            return f"✅ {self.registry.label(cow, sighting.when)} · herkend {score:.0%}"
        if how == "boer" and sighting.bad_photo[slot]:
            return "🚫 foto klopt niet"
        if how == "boer":
            return f"✅ {self.registry.label(cow, sighting.when)}" if cow else "❔ onbekend"
        return "❓"

    def caption(self, sighting: Sighting) -> str:
        lines = ["🐄 Wie zijn het?"] + [
            f"{self._title(sighting, slot)}: {self._status(sighting, slot)}"
            for slot in (0, 1)
        ]
        if None in sighting.how:
            first, second = ("A", "B") if sighting.split else ("wie sprong", "wie werd besprongen")
            lines.append(
                f"\n{'Tik een nummer aan, of antwoord' if any(sighting.candidates) else 'Antwoord'}"
                f" op deze foto met de nummers of namen, eerst {first} dan {second}: 30 12"
            )
        else:
            lines.append("\nIets fout? Antwoord op deze foto met de goede nummers.")
        return "\n".join(lines)

    def keyboard(self, sighting: Sighting) -> str:
        at = sighting.when
        prefix = f"cow:{sighting.id}"
        names = self._names(sighting)
        rows = []
        for slot in (0, 1):
            candidates = [
                {
                    "text": f"{'✅ ' if cow == sighting.cows[slot] else ''}{names[slot]}:"
                    f" {self.registry.label(cow, at)} · {score:.0%}",
                    "callback_data": f"{prefix}:{slot}:c{index}",
                }
                for index, (cow, score) in enumerate(sighting.candidates[slot])
                if self.registry.cow(cow) is not None
            ]
            if candidates:
                rows.append(candidates)
        rows.append([{"text": "✏️ Nummers typen", "callback_data": f"{prefix}:-:n"}])
        rows.append(
            [
                {"text": f"❔ {names[slot]} onbekend", "callback_data": f"{prefix}:{slot}:u"}
                for slot in (0, 1)
            ]
        )
        if sighting.split:
            rows.append(
                [{"text": "🔄 Andersom", "callback_data": f"{prefix}:-:s"}]
                + [
                    {"text": f"🚫 Foto {SLOT_NAMES[slot]}", "callback_data": f"{prefix}:{slot}:x"}
                    for slot in (0, 1)
                ]
            )
        return json.dumps({"inline_keyboard": rows})

    def _refresh(self, sighting: Sighting, quiet: bool = False) -> None:
        """Shows the new state on the Telegram photo. From the web page (quiet)
        a failing Telegram only logs: the choice is saved either way."""
        if sighting.message is None:
            return
        if quiet:
            try:
                self._refresh(sighting)
            except Exception:
                self.logger.warning("Could not update the cow photo in Telegram", exc_info=True)
            return
        try:
            _call(
                self.api_url,
                "editMessageCaption",
                {
                    "chat_id": self.chat,
                    "message_id": str(sighting.message),
                    "caption": self.caption(sighting),
                    "reply_markup": self.keyboard(sighting),
                },
            )
        except RuntimeError as error:
            # Telegram refuses an edit that changes nothing.
            if "not modified" not in str(error):
                raise

    def set_commands(self) -> None:
        """Puts the commands in the menu of the chat, with a Dutch explanation;
        without cows in Telegram, takes away a menu an earlier version set."""
        if not self.config.telegram:
            try:
                _call(self.api_url, "deleteMyCommands", {})
            except Exception:
                self.logger.warning("Could not clear the Telegram command menu", exc_info=True)
            return
        try:
            _call(
                self.api_url,
                "setMyCommands",
                {
                    "commands": json.dumps(
                        [
                            {"command": command, "description": description}
                            for command, description in COMMANDS
                        ]
                    )
                },
            )
        except Exception:
            self.logger.warning("Could not set the Telegram command menu", exc_info=True)

    # Farmer input

    def handle_callback(self, data: str, quiet: bool = False) -> str:
        """Handles a button and returns the short text to show the farmer. The
        web page uses the same buttons (quiet, see _refresh)."""
        if data.startswith("cowswitch:"):
            return self._answer_switch(data)
        _, sighting_id, slot_text, action = data.split(":")
        with self.lock:
            sighting = self.sightings.get(sighting_id)
        if sighting is None:
            return "Deze melding is niet meer te vinden."
        if action == "s":
            with self.lock:
                sighting.mounter = 1 - sighting.mounter
                sighting.role_certain = True
                self._store(sighting)
            self._refresh(sighting, quiet)
            return "Rollen omgedraaid"
        if action == "n":
            self._ask_numbers(sighting)
            return "Typ de nummers als antwoord"
        slot = int(slot_text)
        if slot not in (0, 1):
            raise ValueError(f"Unknown slot {slot_text}")
        name = self._names(sighting)[slot]
        if action == "u":
            self._set(sighting, slot, None)
            self._refresh(sighting, quiet)
            return f"{name}: onbekend"
        if action == "x":
            self._set(sighting, slot, None, file=False)
            self._refresh(sighting, quiet)
            return f"Foto {name} gebruik ik niet"
        if action.startswith("c"):
            index = int(action[1:])
            if index >= len(sighting.candidates[slot]):
                return "Deze keuze bestaat niet meer."
            cow = sighting.candidates[slot][index][0]
            self._set(sighting, slot, cow)
            self._refresh(sighting, quiet)
            return f"{name}: {self.registry.label(cow, sighting.when)}"
        raise ValueError(f"Unknown cow action {action}")

    def _ask_numbers(self, sighting: Sighting) -> None:
        first, second = ("A", "B") if sighting.split else ("wie sprong", "wie werd besprongen")
        result = _call(
            self.api_url,
            "sendMessage",
            {
                "chat_id": self.chat,
                "text": f"Typ de nummers of namen, eerst {first} dan {second}: 30 12\n"
                "Onbekend: ?   Nieuwe koe: 44 NL123456789",
                "reply_to_message_id": str(sighting.message or ""),
                "allow_sending_without_reply": "true",
                "reply_markup": json.dumps(
                    {"force_reply": True, "input_field_placeholder": "30 12"}
                ),
            },
        )
        with self.lock:
            sighting.prompt = int(result["message_id"])
            self.replies[sighting.prompt] = sighting.id
            self._store(sighting)

    def _set(
        self, sighting: Sighting, slot: int, cow: str | None, file: bool = True
    ) -> None:
        """Stores the farmer's choice and files the masked photo in the cow's
        folder, which is what the recognition learns from. A photo that does
        not show one cow of the mount (file=False) is never filed."""
        with self.lock:
            filed = sighting.how[slot] == "boer" and not sighting.bad_photo[slot]
            previous = sighting.cows[slot]
            sighting.cows[slot] = cow
            sighting.how[slot] = "boer"
            sighting.bad_photo[slot] = not file
            self._store(sighting)
        if not sighting.split or sighting.split_wrong:
            return
        name = self._photo_name(sighting, slot)
        if filed:
            # The farmer changed their mind: take the photo from the old folder.
            (self.registry.folder(previous) / name).unlink(missing_ok=True)
        folder = self.directory / CROPS_FOLDER / sighting.id
        source = folder / f"{SLOT_NAMES[slot]}_koe.jpg"
        if not source.is_file():
            source = folder / f"{SLOT_NAMES[slot]}.jpg"
        if file and source.is_file():
            destination = self.registry.folder(cow)
            destination.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination / name)

    @staticmethod
    def _photo_name(sighting: Sighting, slot: int) -> str:
        return f"{sighting.when:%Y-%m-%dT%H-%M-%S}_{sighting.id}_{SLOT_NAMES[slot]}.jpg"

    def _filed_slots(self, sighting: Sighting) -> list[int]:
        """The slots whose photo is in a cow folder."""
        if not sighting.split or sighting.split_wrong:
            return []
        return [
            slot
            for slot in (0, 1)
            if sighting.how[slot] == "boer"
            and not sighting.bad_photo[slot]
            and sighting.cows[slot] is not None
        ]

    def set_split_wrong(self, sighting_id: str, wrong: bool) -> str:
        """From the web page: the two photos are not the two cows of the mount.
        Photos already filed leave the cow folders; with wrong=False they go
        back."""
        with self.lock:
            sighting = self.sightings.get(sighting_id)
        if sighting is None:
            raise KeyError(sighting_id)
        if not sighting.split:
            raise ValueError("Bij deze sprong zijn de koeien niet gesplitst.")
        with self.lock:
            filed = self._filed_slots(sighting)
            sighting.split_wrong = wrong
            if wrong:
                # Recognised from the wrong photos, so not to be trusted.
                sighting.candidates = [[], []]
                for slot in (0, 1):
                    if sighting.how[slot] == "auto":
                        sighting.cows[slot] = None
                        sighting.how[slot] = None
            self._store(sighting)
        folder = self.directory / CROPS_FOLDER / sighting.id
        for slot in filed if wrong else self._filed_slots(sighting):
            destination = self.registry.folder(sighting.cows[slot]) / self._photo_name(sighting, slot)
            if wrong:
                destination.unlink(missing_ok=True)
                continue
            source = folder / f"{SLOT_NAMES[slot]}_koe.jpg"
            if not source.is_file():
                source = folder / f"{SLOT_NAMES[slot]}.jpg"
            if source.is_file():
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, destination)
        if wrong:
            return "Splitsing klopt niet: de foto's gaan niet in de koemappen"
        return "Splitsing klopt toch"

    def set_mount(self, sighting_id: str, is_mount: bool) -> str:
        """From the web page, the same as Goed/Fout below the alert: the mount
        is filed for training, and a false one no longer counts."""
        with self.lock:
            sighting = self.sightings.get(sighting_id)
        if sighting is None:
            raise KeyError(sighting_id)
        label = "good" if is_mount else "bad"
        if sighting.feedback is not None:
            if self.classify is not None:
                try:
                    self.classify(sighting.feedback, label)
                except Exception:
                    # The count matters more than the training example.
                    self.logger.warning("Could not file mount %s as %s", sighting.id, label, exc_info=True)
            self.feedback(sighting.feedback, label)
        else:
            with self.lock:
                sighting.false = not is_mount
                self._store(sighting)
        return "Opgeslagen als sprong" if is_mount else "Opgeslagen als geen sprong"

    def handle_message(self, message: dict[str, Any]) -> str | None:
        """Handles typed numbers, a command or a CSV file and returns the
        reply, or None when the message is not for the cows."""
        if not self.config.telegram:
            # The web page asks about the cows; the chat only has the alerts.
            return None
        if message.get("document"):
            return self._import_document(message["document"])
        text = str(message.get("text") or "").strip()
        reply_to = (message.get("reply_to_message") or {}).get("message_id")
        with self.lock:
            sighting_id = self.replies.get(int(reply_to)) if reply_to is not None else None
            sighting = self.sightings.get(sighting_id) if sighting_id else None
        if sighting is not None and not text.startswith("/"):
            return self._answer(sighting, text)
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

    def _answer(self, sighting: Sighting, text: str) -> str:
        """Reads typed numbers for the cows of a mount: two numbers fill both,
        one number fills the cow that is still open."""
        names = self._names(sighting)
        example = "Voorbeeld: 30 12 of Anna 12   (? = onbekend, nieuwe koe: 44 NL123456789)"
        try:
            answers = parse_answers(text)
        except ValueError as error:
            return f"⚠️ {error}\n{example}"
        if not answers or len(answers) > 2:
            return f"Typ één of twee nummers of namen, eerst {names[0]} dan {names[1]}.\n{example}"
        if len(answers) == 2:
            slots = [0, 1]
        else:
            open_slots = [slot for slot in (0, 1) if sighting.how[slot] != "boer"]
            if not open_slots:
                return f"Beide koeien zijn al ingevuld. Typ twee nummers om ze te verbeteren.\n{example}"
            slots = open_slots[:1]
        chosen: dict[int, str | None] = {}
        problems = []
        for slot, answer in zip(slots, answers):
            try:
                chosen[slot] = self._resolve(sighting, answer)
            except ValueError as error:
                problems.append(str(error))
        known = [cow for cow in chosen.values() if cow is not None]
        if len(known) == 2 and known[0] == known[1]:
            return "Twee keer dezelfde koe: een koe springt niet op zichzelf."
        for slot, cow in chosen.items():
            self._set(sighting, slot, cow)
        if chosen:
            with self.lock:
                sighting.prompt = None
                self._store(sighting)
            self._refresh(sighting)
        saved = ", ".join(
            f"{names[slot]} = {self.registry.label(cow, sighting.when) if cow else 'onbekend'}"
            for slot, cow in sorted(chosen.items())
        )
        return "\n".join(([f"✅ Opgeslagen: {saved}"] if saved else []) + problems)

    def _resolve(self, sighting: Sighting, answer: Answer) -> str | None:
        """The cow the farmer means, None for unknown; a ValueError says in
        Dutch why she cannot be found."""
        if answer.unknown:
            return None
        if answer.name is not None:
            named = self.registry.with_name(answer.name)
            if len(named) == 1:
                return named[0]
            if named:
                raise ValueError(
                    f"Er zijn {len(named)} dieren die {answer.name} heten, typ het nummer."
                )
            raise ValueError(
                f"Geen koe of pink die {answer.name} heet. Typ het nummer, of zet "
                f"haar erin met /koe <nummer> <levensnummer> {answer.name}"
            )
        assert answer.number is not None
        try:
            if answer.life_number:
                return self.registry.add(
                    answer.number, answer.life_number, at=sighting.when
                ).life_number
        except NumberTaken as error:
            raise ValueError(
                f"Nummer {error.number} hoort al bij {error.holder}. Andere koe? "
                f"Stuur /wissel {error.number} <levensnummer>"
            ) from error
        cow = self.registry.cow_with_number(
            answer.number, sighting.when
        ) or self.registry.cow_with_number(answer.number)
        if cow is None:
            raise ValueError(
                f"Nummer {answer.number} ken ik nog niet. Typ nummer en "
                f"levensnummer, bijv. {answer.number} NL123456789"
            )
        return cow

    def set_cow(self, sighting_id: str, slot: int, value: str) -> str:
        """Fills in one cow of a mount from the web page: a number, a name, a
        life number, "?" for unknown or a new cow as "44 NL123456789". Raises
        ValueError with the reason in Dutch."""
        with self.lock:
            sighting = self.sightings.get(sighting_id)
        if sighting is None:
            raise KeyError(sighting_id)
        if slot not in (0, 1):
            raise ValueError(f"Unknown slot {slot}")
        cow: str | None
        try:
            known = normalize_life_number(value)
        except ValueError:
            known = None
        if known is not None and self.registry.cow(known) is not None:
            cow = known
        else:
            answers = parse_answers(value)
            if len(answers) != 1:
                raise ValueError("Typ één nummer of naam, of ? voor onbekend.")
            cow = self._resolve(sighting, answers[0])
        if cow is not None and cow == sighting.cows[1 - slot]:
            raise ValueError("Twee keer dezelfde koe: een koe springt niet op zichzelf.")
        self._set(sighting, slot, cow)
        with self.lock:
            sighting.prompt = None
            self._store(sighting)
        self._refresh(sighting, quiet=True)
        return f"{self._names(sighting)[slot]} = {self.registry.label(cow, sighting.when)}"

    def _import_document(self, document: dict[str, Any]) -> str:
        name = str(document.get("file_name") or "koeien.csv")
        if not name.lower().endswith((".csv", ".txt", *EXCEL_SUFFIXES)):
            return (
                "Stuur de koeien als Excel- of CSV-bestand, bijv. een export met "
                "Levensnummer, Werknummer, Halsbandnummer en Naam."
            )
        info = _call(self.api_url, "getFile", {"file_id": document["file_id"]})
        response = requests.get(f"{self.file_url}/{info['file_path']}", timeout=60)
        response.raise_for_status()
        folder = self.directory / IMPORT_FOLDER
        folder.mkdir(parents=True, exist_ok=True)
        safe = re.sub(r"[^A-Za-z0-9._-]", "_", Path(name).name)
        path = folder / f"{datetime.now():%Y-%m-%dT%H-%M-%S}_{safe}"
        path.write_bytes(response.content)
        result = import_cows(path, self.registry, self.config.herd_categories)
        lines = [result.summary(), *result.problems[:15]]
        if len(result.problems) > 15:
            lines.append(f"… en nog {len(result.problems) - 15} regels met een probleem")
        return "\n".join(lines)

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
            return f"✅ Nummer {number} is nu {self._animal(life_number)}"
        token = secrets.token_hex(4)
        with self.lock:
            self.switches[token] = (number, life_number, name)
        buttons = [
            [{"text": "Ja, oude koe is weg", "callback_data": f"cowswitch:{token}:y"}],
            [{"text": "Nee, alleen halsband gewisseld", "callback_data": f"cowswitch:{token}:n"}],
        ]
        _call(
            self.api_url,
            "sendMessage",
            {
                "chat_id": self.chat,
                "text": f"Nummer {number} was {self.registry.label(old)} · {old}.\n"
                f"Is die koe van het bedrijf? Dan archiveer ik haar foto's, en krijgt "
                f"{self._animal(life_number)} nummer {number}.",
                "reply_markup": json.dumps({"inline_keyboard": buttons}),
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
        before = self.registry.cow_with_number(number)
        old_label = self._animal(before) if before else ""
        old = self.registry.switch(number, life_number, answer == "y", name)
        self._send_text(
            f"✅ Nummer {number} is nu {self._animal(life_number)}."
            + (
                f" {old_label} is gearchiveerd."
                if old and answer == "y"
                else f" {old_label} heeft nu geen nummer, geef haar er een met /koe."
                if old
                else ""
            )
        )
        return "Opgeslagen"

    def _animal(self, life_number: str) -> str:
        """An animal as the farmer knows her, with her life number: "5102 (Nel) ·
        NL000000052", or only the life number for an animal not added yet."""
        if self.registry.cow(life_number) is None:
            return life_number
        return f"{self.registry.label(life_number)} · {life_number}"

    def _command_gone(self, arguments: list[str]) -> str:
        if not arguments:
            return "Gebruik: /weg 30, /weg Anna of /weg NL123456789"
        value = "".join(arguments)
        if value.lstrip("#").isdigit():
            cow = self.registry.cow_with_number(value)
        elif any(character.isdigit() for character in value):
            cow = normalize_life_number(value)
        else:
            named = self.registry.with_name(" ".join(arguments))
            if len(named) > 1:
                return f"Er zijn {len(named)} dieren die {' '.join(arguments)} heten, gebruik het nummer."
            cow = named[0] if named else None
        if cow is None or self.registry.cow(cow) is None:
            return f"Geen koe gevonden voor {value}"
        label = self.registry.label(cow)
        self.registry.archive(cow)
        return f"✅ {label} · {cow} is gearchiveerd, haar sprongen blijven bewaard."

    def _command_list(self) -> str:
        cows = self.registry.active_cows()
        if not cows:
            return (
                "Nog geen koeien. Voeg ze toe met /koe 30 NL123456789, of stuur mij "
                "de export uit het managementprogramma als Excel- of CSV-bestand."
            )

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
        if not self.config.telegram:
            self.logger.info("%s", text)
            return
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

    def overview_counts(
        self, start: datetime, end: datetime
    ) -> tuple[int, dict[str, list[int]], dict[str, str], int]:
        """The mounts in the period, and per cow [mounted, mounting] with her
        label, and how many cows were not filled in."""
        with self.lock:
            sightings = [
                sighting
                for sighting in self.sightings.values()
                if not sighting.false and start <= sighting.when < end
            ]
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
        return len(sightings), counts, labels, unknown

    def overview_text(self, start: datetime, end: datetime) -> str:
        """Lines per cow for the summary: how often she was mounted, which
        points at heat, and how often she jumped herself."""
        mounts, counts, labels, unknown = self.overview_counts(start, end)
        if not mounts:
            return ""

        def order(item: tuple[str, list[int]]) -> tuple[int, int, int, str]:
            # Most mounted first, then by collar number (7 before 12).
            number = labels[item[0]].split()[0]
            return (
                -item[1][0],
                -item[1][1],
                int(number) if number.isdigit() else 10**9,
                labels[item[0]],
            )

        lines = []
        for cow, (mounted, mounting) in sorted(counts.items(), key=order):
            parts = []
            if mounted:
                parts.append(f"{mounted}× besprongen")
            if mounting:
                parts.append(f"{mounting}× gesprongen")
            lines.append(f"{'🔥' if mounted else '•'} {labels[cow]}: {', '.join(parts)}")
        text = "\n\nPer koe (🔥 = besprongen, mogelijk tochtig):\n" + "\n".join(lines)
        if unknown:
            text += f"\n• Niet ingevuld: {unknown} {'koe' if unknown == 1 else 'koeien'}"
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


def split_message(text: str, limit: int = _MESSAGE_LIMIT) -> list[str]:
    """Splits a long reply at line ends, since Telegram allows 4096 characters."""
    parts, current = [], ""
    for line in text.split("\n"):
        while len(line) > limit:
            if current:
                parts.append(current)
                current = ""
            parts.append(line[:limit])
            line = line[limit:]
        candidate = f"{current}\n{line}" if current else line
        if len(candidate) > limit:
            parts.append(current)
            current = line
        else:
            current = candidate
    if current:
        parts.append(current)
    return parts


_cow_services: dict[tuple[str, str], CowService] = {}
_cow_services_lock = Lock()


def cow_services() -> list[CowService]:
    """The running cow services, one per Telegram chat, for the web page."""
    with _cow_services_lock:
        return list(_cow_services.values())


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
