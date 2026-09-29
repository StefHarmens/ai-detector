import json
import logging
import math
import secrets
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from pathlib import Path
from threading import Event, Lock, Thread

import requests

from aidetector.utils.config import Detection, SummaryConfig, max_confidence

RETENTION = timedelta(days=8)
MESSAGE_LIMIT = 4096
BUTTON_LIMIT = 100


@dataclass
class MountRecord:
    event: str
    source: str
    camera: str
    start: datetime
    end: datetime
    center: tuple[float, float] | None
    confidence: float

    def to_json(self) -> str:
        return json.dumps(
            {
                "event": self.event,
                "source": self.source,
                "camera": self.camera,
                "start": self.start.isoformat(),
                "end": self.end.isoformat(),
                "center": list(self.center) if self.center else None,
                "confidence": self.confidence,
            }
        )

    @classmethod
    def from_json(cls, line: str) -> "MountRecord":
        data = json.loads(line)
        center = data.get("center")
        return cls(
            event=data["event"],
            source=data["source"],
            camera=data["camera"],
            start=datetime.fromisoformat(data["start"]),
            end=datetime.fromisoformat(data["end"]),
            center=(center[0], center[1]) if center else None,
            confidence=data["confidence"],
        )


@dataclass
class MountEvent:
    event: str
    start: datetime
    end: datetime
    cameras: list[str]
    jumps: int
    message_id: int | None = None

    @property
    def period(self) -> str:
        period = f"{self.start:%H:%M}"
        if f"{self.end:%H:%M}" != period:
            period += f"–{self.end:%H:%M}"
        return period

    @property
    def line(self) -> str:
        line = f"{self.period} · {' + '.join(self.cameras)}"
        if self.jumps > 1:
            line += f" · {self.jumps}x"
        return line


def parse_times(times: list[str]) -> list[time]:
    if not times:
        raise ValueError('summary.times needs at least one time, e.g. "08:00"')
    try:
        return sorted(time.fromisoformat(value) for value in times)
    except ValueError as error:
        raise ValueError(f"summary.times must use HH:MM, got {times}") from error


def _gap_seconds(a: MountRecord, b: MountRecord) -> float:
    return max(0.0, (max(a.start, b.start) - min(a.end, b.end)).total_seconds())


def _center(detection: Detection) -> tuple[float, float] | None:
    region = detection.images.crop_region
    if region is None:
        return None
    height, width = detection.images.height, detection.images.width
    return ((region.x1 + region.x2) / 2 / width, (region.y1 + region.y2) / 2 / height)


class SummaryService:
    """Groups repeated detections of one mount into a single event and sends a
    periodic overview of those events to a Telegram chat."""

    logger = logging.getLogger("SummaryService")

    def __init__(self, token: str, chat: str, directory: Path, config: SummaryConfig):
        self.api_url = f"https://api.telegram.org/bot{token}"
        self.chat = chat
        self.config = config
        self.times = parse_times(config.times)
        self.directory = directory
        self.records_path = directory / "events.jsonl"
        self.state_path = directory / "state.json"
        self.messages_path = directory / "messages.jsonl"
        self.lock = Lock()
        self.stop_event = Event()
        self.started = False
        self.records = self._load_records(datetime.now())
        self.messages = self._load_messages()
        # Extra text per period, e.g. the mounts per cow.
        self.overview: Callable[[datetime, datetime], str] | None = None

    def register(
        self, best_detection: Detection, detections: list[Detection]
    ) -> str | None:
        """Stores the detection and returns the event ID if it starts a new
        mounting event, or None if it belongs to an earlier one."""
        source = best_detection.source or ""
        record = MountRecord(
            event="",
            source=source,
            camera=best_detection.camera or source,
            start=detections[0].date if detections else best_detection.date,
            end=detections[-1].date if detections else best_detection.date,
            center=_center(best_detection),
            confidence=max_confidence(best_detection.confidence),
        )
        with self.lock:
            matches = [
                other for other in self.records if self._same_event(record, other)
            ]
            is_new = not matches
            record.event = (
                secrets.token_hex(4)
                if is_new
                else max(matches, key=lambda other: other.end).event
            )
            self.records.append(record)
            self.directory.mkdir(parents=True, exist_ok=True)
            with self.records_path.open("a") as file:
                file.write(record.to_json() + "\n")
        self.logger.info(
            "Registered %s mounting event %s on %s",
            "new" if is_new else "repeated",
            record.event,
            record.camera,
        )
        return record.event if is_new else None

    def set_message(self, event: str, message_id: int) -> None:
        """Remembers the alert message of an event, so the summary can link to it."""
        with self.lock:
            self.messages[event] = message_id
            self.directory.mkdir(parents=True, exist_ok=True)
            with self.messages_path.open("a") as file:
                file.write(
                    json.dumps({"event": event, "message_id": message_id}) + "\n"
                )

    def _same_event(self, record: MountRecord, other: MountRecord) -> bool:
        gap = _gap_seconds(record, other)
        if record.source != other.source:
            return gap <= self.config.camera_merge_seconds and self._cameras_overlap(
                record.camera, other.camera
            )
        if gap > self.config.merge_seconds:
            return False
        if record.center is None or other.center is None:
            return True
        return math.dist(record.center, other.center) <= self.config.merge_distance

    def _cameras_overlap(self, camera: str, other: str) -> bool:
        if self.config.camera_groups is None:
            return True
        return any(
            camera in group and other in group for group in self.config.camera_groups
        )

    def _count_jumps(self, records: list[MountRecord]) -> int:
        """Counts jumps, not detections: another camera seeing the same jump at
        the same moment does not add one."""
        counted: list[MountRecord] = []
        for record in records:
            if not any(
                other.source != record.source and self._same_event(record, other)
                for other in counted
            ):
                counted.append(record)
        return len(counted)

    def events(self) -> list[MountEvent]:
        with self.lock:
            records = list(self.records)
            messages = dict(self.messages)
        grouped: dict[str, list[MountRecord]] = {}
        for record in sorted(records, key=lambda record: record.start):
            grouped.setdefault(record.event, []).append(record)
        events = [
            MountEvent(
                event=event,
                start=group[0].start,
                end=max(record.end for record in group),
                cameras=list(dict.fromkeys(record.camera for record in group)),
                jumps=self._count_jumps(group),
                message_id=messages.get(event),
            )
            for event, group in grouped.items()
        ]
        return sorted(events, key=lambda event: event.start)

    def events_between(self, start: datetime, end: datetime) -> list[MountEvent]:
        return [event for event in self.events() if start <= event.start < end]

    def build_summary(self, start: datetime, end: datetime) -> str:
        return self._summary(start, end)[0]

    def _summary(self, start: datetime, end: datetime) -> tuple[str, list[MountEvent]]:
        """Returns the summary text and the events shown as buttons below it.

        An event with an alert becomes a button with its line as label; pressing
        it replies to that alert, so the farmer can jump to it. Private chats have
        no message links, and buttons cannot be placed inside the text. Events
        without an alert stay lines in the text."""
        events = self.events_between(start, end)
        header = f"🐄 Overzicht sprongen\n{start:%d-%m %H:%M} – {end:%d-%m %H:%M}\n\n"
        if not events:
            return header + "Geen sprongen gezien.", []

        jumps = sum(event.jumps for event in events)
        text = header + (
            f"{jumps} {'sprong' if jumps == 1 else 'sprongen'} op {len(events)}"
            f" {'moment' if len(events) == 1 else 'momenten'}\n"
        )
        buttons = [event for event in events if event.message_id is not None][
            :BUTTON_LIMIT
        ]
        lines = [event for event in events if event not in buttons]
        overview = self._overview(start, end)
        for index, event in enumerate(lines):
            line = f"\n• {event.line}"
            if len(text) + len(line) + len(overview) + 30 > MESSAGE_LIMIT:
                text += f"\n… en nog {len(lines) - index} meer"
                break
            text += line
        if overview and len(text) + len(overview) + 50 <= MESSAGE_LIMIT:
            text = text.rstrip("\n") + overview
        if buttons:
            text += "\n\nTik op een moment om de melding te zien."
        return text.rstrip("\n"), buttons

    def _overview(self, start: datetime, end: datetime) -> str:
        if self.overview is None:
            return ""
        try:
            return self.overview(start, end)
        except Exception:
            # The summary itself must still go out.
            self.logger.exception("Failed to add the overview per cow")
            return ""

    @staticmethod
    def reply_markup(events: list[MountEvent]) -> str | None:
        if not events:
            return None
        return json.dumps(
            {
                "inline_keyboard": [
                    [
                        {
                            "text": f"▶️ {event.line}",
                            "callback_data": f"summary:{event.event}",
                        }
                    ]
                    for event in events
                ]
            }
        )

    def show_event(self, event_id: str) -> bool:
        """Replies to the alert of an event. Returns False if the alert is gone."""
        event = next(
            (event for event in self.events() if event.event == event_id), None
        )
        if event is None or event.message_id is None:
            return False
        response = requests.post(
            f"{self.api_url}/sendMessage",
            data={
                "chat_id": self.chat,
                "reply_to_message_id": event.message_id,
                "allow_sending_without_reply": False,
                "text": f"⬆️ Melding van {event.start:%d-%m} {event.period}"
                f" · {' + '.join(event.cameras)}",
            },
            timeout=10,
        )
        if response.status_code >= 400:
            self.logger.warning(
                "Failed to reply to alert %s of event %s: %s",
                event.message_id,
                event_id,
                response.text,
            )
            return False
        return True

    def latest_due(self, now: datetime) -> datetime:
        return max(
            datetime.combine(now.date() - timedelta(days=days), moment)
            for days in (0, 1)
            for moment in self.times
            if datetime.combine(now.date() - timedelta(days=days), moment) <= now
        )

    def send_due(self, now: datetime) -> None:
        due = self.latest_due(now)
        last_sent = self._read_last_sent()
        if last_sent is None:
            # First start: begin counting from now instead of sending a summary
            # for a period in which nothing was recorded.
            self._write_last_sent(due)
            return
        if due <= last_sent:
            return

        start = self.latest_due(due - timedelta(microseconds=1))
        text, events = self._summary(start, due)
        data = {"chat_id": self.chat, "text": text}
        markup = self.reply_markup(events)
        if markup:
            data["reply_markup"] = markup
        response = requests.post(f"{self.api_url}/sendMessage", data=data, timeout=10)
        if response.status_code >= 400:
            # Retrying would not help, so skip this summary instead of repeating it.
            self.logger.error(
                "Failed to send summary to chat %s (%s): %s",
                self.chat,
                response.status_code,
                response.text,
            )
        else:
            self.logger.info(
                "Sent summary for %s – %s to chat %s", start, due, self.chat
            )
        self._write_last_sent(due)

    def start(self) -> None:
        with self.lock:
            if self.started:
                return
            self.started = True
        Thread(target=self._run, name="telegram-summary", daemon=True).start()

    def stop(self) -> None:
        self.stop_event.set()

    def _run(self) -> None:
        while not self.stop_event.is_set():
            try:
                self.send_due(datetime.now())
            except Exception:
                self.logger.exception("Failed to send summary")
            self.stop_event.wait(30)

    def _load_records(self, now: datetime) -> list[MountRecord]:
        if not self.records_path.is_file():
            return []
        records = []
        for line in self.records_path.read_text().splitlines():
            try:
                record = MountRecord.from_json(line)
            except (ValueError, KeyError, TypeError):
                self.logger.warning("Skipping invalid summary record: %s", line)
                continue
            if record.end >= now - RETENTION:
                records.append(record)
        self.records_path.write_text(
            "".join(record.to_json() + "\n" for record in records)
        )
        return records

    def _load_messages(self) -> dict[str, int]:
        if not self.messages_path.is_file():
            return {}
        events = {record.event for record in self.records}
        messages = {}
        for line in self.messages_path.read_text().splitlines():
            try:
                data = json.loads(line)
                event, message_id = data["event"], int(data["message_id"])
            except (ValueError, KeyError, TypeError):
                self.logger.warning("Skipping invalid summary message: %s", line)
                continue
            if event in events:
                messages[event] = message_id
        self.messages_path.write_text(
            "".join(
                json.dumps({"event": event, "message_id": message_id}) + "\n"
                for event, message_id in messages.items()
            )
        )
        return messages

    def _read_last_sent(self) -> datetime | None:
        try:
            return datetime.fromisoformat(
                json.loads(self.state_path.read_text())["last_sent"]
            )
        except (FileNotFoundError, KeyError, ValueError):
            return None

    def _write_last_sent(self, value: datetime) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(json.dumps({"last_sent": value.isoformat()}))


_summary_services: dict[tuple[str, str], SummaryService] = {}
_summary_services_lock = Lock()


def get_summary_service(
    token: str, chat: str, feedback_directory: Path, config: SummaryConfig
) -> SummaryService:
    """Returns one shared service per chat, so detections from every camera and
    detector that report to the same chat are grouped together."""
    safe_chat = "".join(c if c.isalnum() or c in "-_" else "_" for c in str(chat))
    directory = (
        feedback_directory.expanduser().resolve() / ".telegram-summary" / safe_chat
    )
    key = (token, str(chat))
    with _summary_services_lock:
        if key not in _summary_services:
            _summary_services[key] = SummaryService(token, str(chat), directory, config)
        return _summary_services[key]
