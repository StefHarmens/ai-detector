import json
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np

from aidetector.exporters.disk import DiskExporter
from aidetector.exporters.exporter import Exporter
from aidetector.exporters.telegram import TelegramExporter, TelegramFeedbackListener
from aidetector.exporters.webhook import WebhookExporter
from aidetector.utils.config import (
    ChatConfig,
    Crop,
    Detection,
    DiskConfig,
    ExporterConfig,
    ImageSet,
    WebhookConfig,
)


def make_detections() -> list[Detection]:
    start = datetime(2026, 1, 1, 12, 0, 0)
    image = np.zeros((80, 120, 3), dtype=np.uint8)
    return [
        Detection(
            start,
            ImageSet(image, [Crop(10, 10, 40, 50, label="cow", confidence=0.7)]),
            {"cow": 0.7},
        ),
        Detection(
            start + timedelta(seconds=2),
            ImageSet(image, [Crop(12, 12, 42, 52, label="cow", confidence=0.9)]),
            {"cow": 0.9},
        ),
    ]


class RecordingExporter(Exporter[ExporterConfig]):
    def __init__(self, config: ExporterConfig):
        super().__init__(config)
        self.calls = []

    def filtered_export(self, best_detection, detections, validated):
        self.calls.append((best_detection, detections, validated))


def test_exporter_filters_by_confidence_and_rejected_state():
    detections = make_detections()
    best = detections[-1]

    exporter = RecordingExporter(ExporterConfig(confidence=0.95))
    exporter.export(best, detections, True)
    assert exporter.calls == []

    exporter = RecordingExporter(ExporterConfig(confidence=0.5, export_rejected=False))
    exporter.export(best, detections, False)
    assert exporter.calls == []

    exporter = RecordingExporter(ExporterConfig(confidence=0.5, export_rejected=True))
    exporter.export(best, detections, False)
    assert len(exporter.calls) == 1


def test_disk_exporter_writes_detection_files(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "aidetector.exporters.disk.generate_mp4", lambda *_args, **_kwargs: b"mp4"
    )
    detections = make_detections()

    exporter = DiskExporter(DiskConfig(directory=Path("events")))
    exporter.export(detections[-1], detections, True)

    event_dirs = list((tmp_path / "detections" / "events" / "approved").iterdir())
    assert len(event_dirs) == 1
    event_dir = event_dirs[0]
    assert (event_dir / "best.jpg").exists()
    assert (event_dir / "clean.jpg").exists()
    assert (event_dir / "video.mp4").read_bytes() == b"mp4"

    metadata = json.loads((event_dir / "metadata.json").read_text())
    assert metadata["validated"] is True
    assert metadata["confidence"] == 0.9
    assert metadata["detections"] == 2
    assert metadata["crop"] == {"x1": 12, "y1": 12, "x2": 42, "y2": 52}


def test_webhook_exporter_sends_no_body_for_none_data_type(monkeypatch):
    calls = []

    def fake_request(method, url, **kwargs):
        calls.append((method, url, kwargs))
        return type("Response", (), {"status_code": 200, "text": ""})()

    monkeypatch.setattr("aidetector.exporters.webhook.requests.request", fake_request)
    detections = make_detections()
    exporter = WebhookExporter(
        WebhookConfig(
            url="https://example.test/hook",
            method="GET",
            timeout=5,
            headers={"X-Test": "1"},
            data_type="none",
        )
    )

    exporter.export(detections[-1], detections, True)

    assert calls == [
        (
            "GET",
            "https://example.test/hook",
            {"headers": {"X-Test": "1"}, "timeout": 5},
        )
    ]


def test_webhook_explicit_body_overrides_generated_payload(monkeypatch):
    calls = []

    def fake_request(method, url, **kwargs):
        calls.append((method, url, kwargs))
        return type("Response", (), {"status_code": 200, "text": ""})()

    monkeypatch.setattr("aidetector.exporters.webhook.requests.request", fake_request)
    detections = make_detections()
    exporter = WebhookExporter(
        WebhookConfig(
            url="https://example.test/hook",
            body="fixed-body",
            data_type="base64",
            include_image=True,
        )
    )

    exporter.export(detections[-1], detections, True)

    request = calls[0][2]
    assert request["data"] == "fixed-body"
    assert "json" not in request
    assert "files" not in request


def test_telegram_exporter_respects_alert_every(monkeypatch):
    monkeypatch.setattr(
        "aidetector.exporters.telegram.generate_mp4", lambda *_args, **_kwargs: None
    )
    detections = make_detections()
    exporter = TelegramExporter(
        ChatConfig(
            token="token",
            chat="chat-id",
            alert_every=2,
            include_image=True,
            include_video=False,
        )
    )

    first = exporter.get_payload(detections[-1], detections, None)
    second = exporter.get_payload(detections[-1], detections, None)

    assert first["chat_id"] == "chat-id"
    assert first["disable_notification"] is True
    assert second["disable_notification"] is False
    media = json.loads(second["media"])
    assert media[0]["caption"].startswith("90%")


def test_telegram_exporter_adds_feedback_buttons(monkeypatch):
    calls = []

    class Response:
        status_code = 200
        text = ""

        def json(self):
            return {"ok": True, "result": [{"message_id": 42}]}

    def fake_post(url, **kwargs):
        calls.append((url, kwargs))
        return Response()

    monkeypatch.setattr("aidetector.exporters.telegram.requests.post", fake_post)
    detections = make_detections()
    exporter = TelegramExporter(
        ChatConfig(
            token="feedback-token",
            chat="chat-id",
            include_image=True,
            include_video=False,
        )
    )
    monkeypatch.setattr(exporter.feedback_listener, "start", lambda: None)

    exporter.export(detections[-1], detections, True)

    assert calls[0][0].endswith("/sendMediaGroup")
    assert calls[1][0].endswith("/editMessageReplyMarkup")
    assert calls[1][1]["data"]["message_id"] == 42
    markup = json.loads(calls[1][1]["data"]["reply_markup"])
    callback_data = [button["callback_data"] for button in markup["inline_keyboard"][0]]
    assert callback_data[0].endswith(":good")
    assert callback_data[1].endswith(":bad")
    assert list(Path(".telegram-feedback").glob("*.jpg"))


def test_telegram_feedback_moves_image_between_good_and_bad(monkeypatch):
    monkeypatch.setattr(
        "aidetector.exporters.telegram.requests.post",
        lambda *_args, **_kwargs: type("Response", (), {"status_code": 200})(),
    )
    detection = make_detections()[-1]
    listener = TelegramFeedbackListener("token")
    listener.register_chat("123")
    feedback_id = listener.save_detection(detection)
    filename = json.loads(
        (Path(".telegram-feedback") / f"{feedback_id}.json").read_text()
    )["filename"]

    callback = {
        "id": "callback-id",
        "data": f"feedback:{feedback_id}:good",
        "message": {"message_id": 42, "chat": {"id": 123}},
    }
    listener.process_callback(callback)

    assert (Path("good") / filename).is_file()
    good_metadata = json.loads(
        (Path("good") / Path(filename).with_suffix(".json")).read_text()
    )
    assert good_metadata["width"] == 120
    assert good_metadata["height"] == 80
    assert good_metadata["boxes"] == [
        {"x1": 12, "y1": 12, "x2": 42, "y2": 52, "label": "cow"}
    ]
    assert not (Path("bad") / filename).exists()

    callback["data"] = f"feedback:{feedback_id}:bad"
    listener.process_callback(callback)

    assert not (Path("good") / filename).exists()
    assert not (Path("good") / Path(filename).with_suffix(".json")).exists()
    assert (Path("bad") / filename).is_file()
    assert (Path("bad") / Path(filename).with_suffix(".json")).is_file()


def test_telegram_feedback_uses_configured_directory(tmp_path, monkeypatch):
    working_directory = tmp_path / "working"
    feedback_directory = tmp_path / "persistent-feedback"
    working_directory.mkdir()
    monkeypatch.chdir(working_directory)

    listener = TelegramFeedbackListener("token", feedback_directory)
    feedback_id = listener.save_detection(make_detections()[-1])
    listener._classify(feedback_id, "good")

    assert list((feedback_directory / ".telegram-feedback").glob("*.jpg"))
    assert list((feedback_directory / "good").glob("*.jpg"))
    assert not (working_directory / ".telegram-feedback").exists()
    assert not (working_directory / "good").exists()
