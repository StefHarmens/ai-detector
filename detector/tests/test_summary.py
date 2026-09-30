import json
from datetime import datetime, timedelta

import numpy as np
import pytest

from aidetector.detection.detector import camera_names
from aidetector.exporters import summary as summary_module
from aidetector.exporters.summary import SummaryService
from aidetector.exporters.telegram import TelegramExporter, TelegramFeedbackListener
from aidetector.utils.config import (
    ChatConfig,
    Crop,
    Detection,
    DetectionConfig,
    ImageSet,
    SummaryConfig,
)

START = datetime(2026, 9, 23, 3, 0, 0)


def make_mount(
    source: str,
    start: datetime,
    x: int = 100,
    y: int = 100,
    seconds: int = 20,
    camera: str | None = None,
) -> tuple[Detection, list[Detection]]:
    image = np.zeros((720, 1280, 3), dtype=np.uint8)
    crop = Crop(x, y, x + 80, y + 120, label="mounting", confidence=0.9)
    detections = [
        Detection(
            start + timedelta(seconds=offset),
            ImageSet(image, [crop]),
            {"mounting": 0.9},
            source=source,
            camera=camera or source,
        )
        for offset in (0, seconds)
    ]
    return detections[-1], detections


def make_service(tmp_path, **config) -> SummaryService:
    return SummaryService("token", "chat", tmp_path, SummaryConfig(**config))


class Response:
    status_code = 200
    text = ""

    def json(self):
        return {"ok": True, "result": [{"message_id": 1}]}


def test_repeated_mount_on_same_camera_and_place_is_one_event(tmp_path):
    service = make_service(tmp_path)  # merge_seconds defaults to 120

    # Each mount lasts 20 s; the gap is measured from the end of the previous one.
    assert service.register(*make_mount("cam-a", START)) is not None
    assert service.register(*make_mount("cam-a", START + timedelta(seconds=90))) is None
    assert service.register(*make_mount("cam-a", START + timedelta(minutes=3))) is None
    # More than two minutes after the previous mount ended: a new event.
    assert (
        service.register(*make_mount("cam-a", START + timedelta(minutes=6))) is not None
    )


def test_mount_elsewhere_in_the_same_camera_is_a_new_event(tmp_path):
    service = make_service(tmp_path, merge_distance=0.25)

    assert service.register(*make_mount("cam-a", START, x=100, y=100)) is not None
    assert (
        service.register(
            *make_mount("cam-a", START + timedelta(minutes=1), x=1000, y=550)
        )
        is not None
    )


def test_same_mount_seen_by_two_cameras_is_one_event(tmp_path):
    service = make_service(tmp_path)

    assert service.register(*make_mount("cam-a", START, x=100)) is not None
    # Another camera sees the jump at the same time, at a different spot in its image.
    assert (
        service.register(*make_mount("cam-b", START + timedelta(seconds=10), x=1000))
        is None
    )
    # A mount on the other camera minutes later is not the same jump.
    assert (
        service.register(*make_mount("cam-b", START + timedelta(minutes=10), x=100))
        is not None
    )


def test_camera_groups_only_merge_cameras_that_see_the_same_area(tmp_path):
    service = make_service(
        tmp_path,
        camera_groups=[
            ["Links Voorin", "Links Achterin", "Centraal"],
            ["Rechts Voorin", "Rechts Achterin", "Centraal"],
        ],
    )
    at = START + timedelta(seconds=5)

    assert service.register(*make_mount("1", START, camera="Links Voorin")) is not None
    # A jump on the right side at the same moment is a different jump.
    assert service.register(*make_mount("2", at, camera="Rechts Voorin")) is not None
    # Cameras in a shared group see the same jump.
    assert service.register(*make_mount("3", at, camera="Links Achterin")) is None
    assert service.register(*make_mount("4", at, camera="Centraal")) is None


def test_summary_lists_events_with_cameras_and_counts(tmp_path):
    service = make_service(tmp_path)
    service.register(*make_mount("a", START, camera="Stal Rechts"))
    service.register(
        *make_mount("a", START + timedelta(minutes=2), camera="Stal Rechts")
    )
    service.register(
        *make_mount("b", START + timedelta(minutes=2), camera="Stal Links")
    )
    service.register(*make_mount("a", START + timedelta(hours=2), camera="Stal Rechts"))

    text = service.build_summary(START - timedelta(hours=8), START + timedelta(hours=4))

    # Stal Links saw the second jump at the same moment, so it is not counted twice.
    assert "3 sprongen op 2 momenten" in text
    assert "• 03:00–03:02 · Stal Rechts + Stal Links · 2x" in text
    assert text.endswith("• 05:00 · Stal Rechts")


def test_summary_without_events_says_so(tmp_path):
    service = make_service(tmp_path)

    assert service.build_summary(START, START + timedelta(hours=12)).endswith(
        "Geen sprongen gezien."
    )


def test_events_are_reloaded_after_restart(tmp_path):
    first = make_service(tmp_path)
    first.register(*make_mount("cam-a", datetime.now() - timedelta(minutes=3)))

    second = make_service(tmp_path)

    assert (
        second.register(*make_mount("cam-a", datetime.now() - timedelta(minutes=2)))
        is None
    )


def test_summary_is_sent_once_per_scheduled_time(tmp_path, monkeypatch):
    sent = []
    monkeypatch.setattr(
        summary_module.requests,
        "post",
        lambda url, **kwargs: sent.append(kwargs["data"]) or Response(),
    )
    service = make_service(tmp_path)  # defaults to 08:00 and 16:00
    service.register(*make_mount("cam-a", datetime(2026, 9, 23, 3, 0)))

    service.send_due(datetime(2026, 9, 23, 6, 0))
    assert sent == []  # first start only records the schedule

    service.send_due(datetime(2026, 9, 23, 8, 0, 30))
    service.send_due(datetime(2026, 9, 23, 8, 1))
    assert len(sent) == 1
    assert "22-09 16:00 – 23-09 08:00" in sent[0]["text"]
    assert "1 sprong op 1 moment" in sent[0]["text"]

    service.send_due(datetime(2026, 9, 23, 16, 0))
    assert len(sent) == 2
    assert "Geen sprongen gezien." in sent[1]["text"]


def test_invalid_summary_time_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="HH:MM"):
        make_service(tmp_path, times=["7 uur"])


def test_telegram_sends_only_the_first_detection_of_an_event(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(
        "aidetector.exporters.telegram.requests.post",
        lambda url, **kwargs: calls.append(url) or Response(),
    )
    monkeypatch.setattr(SummaryService, "start", lambda self: None)
    monkeypatch.setattr(TelegramFeedbackListener, "start", lambda self: None)
    monkeypatch.setattr(summary_module, "_summary_services", {})
    exporter = TelegramExporter(
        ChatConfig(
            token="summary-token",
            chat="chat-id",
            feedback_directory=tmp_path,
            include_image=True,
            include_video=False,
            summary=SummaryConfig(),
        )
    )

    exporter.export(*make_mount("cam-a", START), True)
    exporter.export(*make_mount("cam-a", START + timedelta(minutes=2)), True)

    assert [url.rsplit("/", 1)[-1] for url in calls] == [
        "sendMediaGroup",
        "sendMessage",
    ]
    records = (tmp_path / ".telegram-summary" / "chat-id" / "events.jsonl").read_text()
    assert len(records.splitlines()) == 2
    assert len({json.loads(line)["event"] for line in records.splitlines()}) == 1


def test_telegram_still_alerts_when_the_summary_log_fails(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(
        "aidetector.exporters.telegram.requests.post",
        lambda url, **kwargs: calls.append(url) or Response(),
    )
    monkeypatch.setattr(SummaryService, "start", lambda self: None)
    monkeypatch.setattr(TelegramFeedbackListener, "start", lambda self: None)
    monkeypatch.setattr(summary_module, "_summary_services", {})
    exporter = TelegramExporter(
        ChatConfig(
            token="summary-token",
            chat="chat-id",
            feedback_directory=tmp_path,
            include_image=True,
            include_video=False,
            summary=SummaryConfig(),
        )
    )

    def fail(*_args):
        raise OSError("disk full")

    monkeypatch.setattr(exporter.summary, "register", fail)

    exporter.export(*make_mount("cam-a", START), True)

    assert calls[0].endswith("/sendMediaGroup")


def test_telegram_summary_only_mode_sends_no_event_messages(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(
        "aidetector.exporters.telegram.requests.post",
        lambda url, **kwargs: calls.append(url) or Response(),
    )
    monkeypatch.setattr(SummaryService, "start", lambda self: None)
    monkeypatch.setattr(TelegramFeedbackListener, "start", lambda self: None)
    monkeypatch.setattr(summary_module, "_summary_services", {})
    exporter = TelegramExporter(
        ChatConfig(
            token="summary-token",
            chat="chat-id",
            feedback_directory=tmp_path,
            summary=SummaryConfig(send_events=False),
        )
    )

    exporter.export(*make_mount("cam-a", START), True)

    assert calls == []


def test_summary_shows_alerted_events_as_buttons(tmp_path, monkeypatch):
    sent = []
    monkeypatch.setattr(
        summary_module.requests,
        "post",
        lambda url, **kwargs: sent.append(kwargs["data"]) or Response(),
    )
    service = make_service(tmp_path)
    first = service.register(
        *make_mount("a", datetime(2026, 9, 23, 3, 0), camera="Stal Rechts")
    )
    service.register(
        *make_mount("a", datetime(2026, 9, 23, 3, 2), camera="Stal Rechts")
    )
    service.register(*make_mount("a", datetime(2026, 9, 23, 5, 0), camera="Stal Links"))
    service.set_message(first, 42)  # the second event's alert was not sent

    service.send_due(datetime(2026, 9, 23, 6, 0))
    service.send_due(datetime(2026, 9, 23, 8, 0))

    assert sent[0]["text"].endswith(
        "3 sprongen op 2 momenten\n"
        "\n• 05:00 · Stal Links"
        "\n\nTik op een moment om de melding te zien."
    )
    keyboard = json.loads(sent[0]["reply_markup"])["inline_keyboard"]
    assert keyboard == [
        [
            {
                "text": "▶️ 03:00–03:02 · Stal Rechts · 2x",
                "callback_data": f"summary:{first}",
            }
        ]
    ]


def test_summary_without_alerts_has_no_buttons(tmp_path, monkeypatch):
    sent = []
    monkeypatch.setattr(
        summary_module.requests,
        "post",
        lambda url, **kwargs: sent.append(kwargs["data"]) or Response(),
    )
    service = make_service(tmp_path)
    service.register(*make_mount("cam-a", datetime(2026, 9, 23, 3, 0)))

    service.send_due(datetime(2026, 9, 23, 6, 0))
    service.send_due(datetime(2026, 9, 23, 8, 0))

    assert "reply_markup" not in sent[0]
    assert sent[0]["text"].endswith("• 03:00 · cam-a")


def test_alert_messages_are_reloaded_after_restart(tmp_path):
    first = make_service(tmp_path)
    event = first.register(*make_mount("cam-a", datetime.now() - timedelta(minutes=3)))
    first.set_message(event, 42)

    second = make_service(tmp_path)

    assert second.events()[0].message_id == 42


def test_summary_button_replies_to_the_alert(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(
        "aidetector.exporters.telegram.requests.post",
        lambda url, **kwargs: calls.append((url, kwargs["data"])) or Response(),
    )
    service = make_service(tmp_path)
    event = service.register(*make_mount("cam-a", START, camera="Stal Rechts"))
    service.set_message(event, 42)
    listener = TelegramFeedbackListener("token", tmp_path)
    listener.register_chat("chat")
    listener.register_summary("chat", service)

    listener.process_callback(
        {
            "id": "callback-id",
            "data": f"summary:{event}",
            "message": {"message_id": 50, "chat": {"id": "chat"}},
        }
    )

    (reply_url, reply), (answer_url, answer) = calls
    assert reply_url.endswith("/sendMessage")
    assert reply["reply_to_message_id"] == 42
    assert reply["text"] == "⬆️ Melding van 23-09 03:00 · Stal Rechts"
    assert answer_url.endswith("/answerCallbackQuery")
    assert "text" not in answer


def test_summary_button_for_a_removed_event_says_so(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(
        "aidetector.exporters.telegram.requests.post",
        lambda url, **kwargs: calls.append((url, kwargs["data"])) or Response(),
    )
    listener = TelegramFeedbackListener("token", tmp_path)
    listener.register_chat("chat")
    listener.register_summary("chat", make_service(tmp_path))

    listener.process_callback(
        {
            "id": "callback-id",
            "data": "summary:deadbeef",
            "message": {"message_id": 50, "chat": {"id": "chat"}},
        }
    )

    [(url, answer)] = calls
    assert url.endswith("/answerCallbackQuery")
    assert answer["text"] == "Deze melding is niet meer te vinden."


def test_telegram_remembers_the_alert_of_an_event(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "aidetector.exporters.telegram.requests.post",
        lambda url, **kwargs: Response(),
    )
    monkeypatch.setattr(SummaryService, "start", lambda self: None)
    monkeypatch.setattr(TelegramFeedbackListener, "start", lambda self: None)
    monkeypatch.setattr(summary_module, "_summary_services", {})
    exporter = TelegramExporter(
        ChatConfig(
            token="summary-token",
            chat="chat-id",
            feedback_directory=tmp_path,
            include_image=True,
            include_video=False,
            summary=SummaryConfig(),
        )
    )

    exporter.export(*make_mount("cam-a", START), True)

    assert [event.message_id for event in exporter.summary.events()] == [1]


def test_camera_names_hide_stream_urls():
    names = camera_names(
        DetectionConfig(
            source=[
                "rtsps://10.0.0.1:7441/secret1",
                "rtsps://10.0.0.1:7441/secret2",
                "clip.mp4",
            ],
            name=["Stal Rechts Achterin"],
        )
    )

    assert list(names.values()) == ["Stal Rechts Achterin", "Camera 2", "clip.mp4"]


def test_no_summary_times_still_groups_but_sends_no_overview(tmp_path, monkeypatch):
    posts = []
    monkeypatch.setattr(summary_module.requests, "post", lambda *args, **kwargs: posts.append(args) or Response())
    service = make_service(tmp_path, times=[])

    assert service.register(*make_mount("cam-a", START)) is not None
    assert service.register(*make_mount("cam-a", START + timedelta(seconds=90))) is None
    service.start()

    assert not service.stop_event.is_set()
    assert posts == []
