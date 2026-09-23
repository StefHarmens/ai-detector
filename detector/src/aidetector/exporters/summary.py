import json
import logging
import math
import secrets
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from pathlib import Path
from threading import Event, Lock, Thread

import requests

from aidetector.utils.config import Detection, SummaryConfig, max_confidence

RETENTION = timedelta(days=8)
MESSAGE_LIMIT = 4096


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
    start: datetime
    end: datetime
    cameras: list[str]
    detections: int


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
    height, width = detection.images.jpg.shape[:2]
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
        self.lock = Lock()
        self.stop_event = Event()
        self.started = False
        self.records = self._load_records(datetime.now())

    def register(self, best_detection: Detection, detections: list[Detection]) -> bool:
        """Stores the detection and returns True if it starts a new mounting event."""
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
        return is_new

    def _same_event(self, record: MountRecord, other: MountRecord) -> bool:
        gap = _gap_seconds(record, other)
        if record.source != other.source:
            return gap <= self.config.camera_merge_seconds
        if gap > self.config.merge_seconds:
            return False
        if record.center is None or other.center is None:
            return True
        return math.dist(record.center, other.center) <= self.config.merge_distance

    def events_between(self, start: datetime, end: datetime) -> list[MountEvent]:
        with self.lock:
            records = list(self.records)
        grouped: dict[str, list[MountRecord]] = {}
        for record in sorted(records, key=lambda record: record.start):
            grouped.setdefault(record.event, []).append(record)
        events = [
            MountEvent(
                start=group[0].start,
                end=max(record.end for record in group),
                cameras=list(dict.fromkeys(record.camera for record in group)),
                detections=len(group),
            )
            for group in grouped.values()
        ]
        return sorted(
            (event for event in events if start <= event.start < end),
            key=lambda event: event.start,
        )

    def build_summary(self, start: datetime, end: datetime) -> str:
        events = self.events_between(start, end)
        header = f"🐄 Overzicht sprongen\n{start:%d-%m %H:%M} – {end:%d-%m %H:%M}\n\n"
        if not events:
            return header + "Geen sprongen gezien."

        detections = sum(event.detections for event in events)
        text = header + (
            f"{len(events)} {'sprong' if len(events) == 1 else 'sprongen'}"
            f" ({detections} {'detectie' if detections == 1 else 'detecties'})\n"
        )
        for index, event in enumerate(events):
            period = f"{event.start:%H:%M}"
            if f"{event.end:%H:%M}" != period:
                period += f"–{event.end:%H:%M}"
            line = f"\n• {period} · {' + '.join(event.cameras)}"
            if event.detections > 1:
                line += f" · {event.detections}x"
            remaining = len(events) - index
            if len(text) + len(line) + 30 > MESSAGE_LIMIT:
                return text + f"\n… en nog {remaining} meer"
            text += line
        return text

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
        response = requests.post(
            f"{self.api_url}/sendMessage",
            data={"chat_id": self.chat, "text": self.build_summary(start, due)},
            timeout=10,
        )
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
