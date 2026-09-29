import json
from datetime import datetime, timedelta
from itertools import count

import cv2
import numpy as np
import pytest

from aidetector.cows import service as service_module
from aidetector.cows.registry import CowRegistry
from aidetector.cows.reid import Gallery
from aidetector.cows.service import CowService, event_frames
from aidetector.cows.split import CowPair
from aidetector.exporters.summary import SummaryService
from aidetector.utils.config import (
    CowsConfig,
    Crop,
    Detection,
    HiresFrame,
    ImageSet,
    SummaryConfig,
)
from aidetector.media.video import get_image

START = datetime(2026, 10, 2, 8, 0, 0)
BERTHA = "NL123456789"
PINK = "NL987654321"


class Telegram:
    """Records the Bot API calls and answers each with a new message."""

    def __init__(self):
        self.calls: list[tuple[str, dict]] = []
        self.ids = count(100)

    def post(self, url, data=None, files=None, timeout=None):
        self.calls.append((url.rsplit("/", 1)[-1], dict(data or {})))
        message_id = next(self.ids)

        class Response:
            status_code = 200
            text = ""

            def json(self):
                return {"ok": True, "result": {"message_id": message_id}}

        return Response()

    def sent(self, method: str) -> list[dict]:
        return [data for name, data in self.calls if name == method]


class FakeSplitter:
    def __init__(self, pair: CowPair | None):
        self.pair = pair

    def split(self, frames, mount, start):
        return self.pair

    def mounted_region(self, image, mount):
        from aidetector.cows.split import grow

        return grow(mount, 1.8)


class FakeGallery:
    def __init__(self, scores: list[list[tuple[str, float]]]):
        self.scores = list(scores)

    def match(self, image):
        return np.zeros(2), self.scores.pop(0)


def cow_image(value: int) -> np.ndarray:
    return np.full((60, 40, 3), value, dtype=np.uint8)


def pair(certain: bool = True) -> CowPair:
    return CowPair(
        crops=[cow_image(10), cow_image(200)],
        boxes=[(0.4, 0.4, 0.5, 0.6), (0.5, 0.4, 0.6, 0.6)],
        mounter=1,
        certain=certain,
        date=START,
    )


def mount() -> tuple[Detection, list[Detection]]:
    image = np.zeros((720, 1280, 3), dtype=np.uint8)
    crop = Crop(500, 300, 700, 500, "mounting", 0.9)
    # A frame from before the jump, then the jump itself.
    detections = [
        Detection(
            START + timedelta(seconds=offset),
            ImageSet(image, [crop]),
            {"mounting": 0.9} if offset >= 0 else {},
            camera="Stal Links",
        )
        for offset in (-2, 0, 5)
    ]
    return detections[-1], detections


@pytest.fixture
def telegram(monkeypatch):
    fake = Telegram()
    monkeypatch.setattr(service_module.requests, "post", fake.post)
    return fake


def make_service(tmp_path, split, scores, **config) -> CowService:
    service = CowService(
        "token",
        "chat",
        tmp_path,
        CowsConfig(**config),
        splitter=FakeSplitter(split),
        gallery=FakeGallery(scores),
    )
    service.registry.add("30", BERTHA, "Bertha", at=START.date() - timedelta(days=100))
    service.registry.add("12", PINK, at=START.date() - timedelta(days=100))
    return service


def add_photos(tmp_path, life_number: str, amount: int) -> None:
    folder = tmp_path / life_number
    folder.mkdir(parents=True, exist_ok=True)
    for index in range(amount):
        (folder / f"{index}.jpg").write_bytes(b"jpg")


def test_each_cow_gets_a_photo_with_candidates(tmp_path, telegram):
    service = make_service(tmp_path, pair(certain=False), [[(BERTHA, 0.7), (PINK, 0.6)], [(PINK, 0.8)]])

    sighting = service.identify(*mount(), alert=42, event="e1", feedback="f1")

    photos = telegram.sent("sendPhoto")
    assert len(photos) == 2
    assert all(photo["reply_to_message_id"] == "42" for photo in photos)
    assert photos[0]["caption"].startswith("🐄 Koe A werd besprongen (gok)")
    assert photos[1]["caption"].startswith("🐄 Koe B sprong (gok)")
    buttons = json.loads(photos[0]["reply_markup"])["inline_keyboard"]
    assert [button["text"] for button in buttons[0]] == ["30 (Bertha) · 70%", "12 · 60%"]
    # Too few photos to fill anything in automatically.
    assert sighting.cows == [None, None]
    assert (tmp_path / ".meldingen" / sighting.id / "A.jpg").is_file()


def test_a_clear_match_with_enough_photos_is_filled_in(tmp_path, telegram):
    add_photos(tmp_path, BERTHA, 5)
    service = make_service(tmp_path, pair(), [[(BERTHA, 0.93), (PINK, 0.70)], [(PINK, 0.95), (BERTHA, 0.90)]])

    sighting = service.identify(*mount(), alert=42, event="e1", feedback="f1")

    assert sighting.cows == [BERTHA, None]
    assert sighting.how == ["auto", None]
    assert "✅ 30 (Bertha) · herkend (93%)" in telegram.sent("sendPhoto")[0]["caption"]


def test_choosing_a_candidate_files_the_photo_for_learning(tmp_path, telegram):
    service = make_service(tmp_path, pair(), [[(BERTHA, 0.7)], [(PINK, 0.8)]])
    sighting = service.identify(*mount(), alert=42, event="e1", feedback="f1")

    answer = service.handle_callback(f"cow:{sighting.id}:0:c0")

    assert answer == "Opgeslagen als 30 (Bertha)"
    assert sighting.cows[0] == BERTHA and sighting.how[0] == "boer"
    assert len(list((tmp_path / BERTHA).glob("*.jpg"))) == 1
    edit = telegram.sent("editMessageCaption")[0]
    assert "✅ 30 (Bertha)" in edit["caption"]

    # Changing the answer moves the photo to the other cow.
    service.handle_callback(f"cow:{sighting.id}:0:u")
    assert list((tmp_path / BERTHA).glob("*.jpg")) == []
    assert len(list((tmp_path / "onbekend").glob("*.jpg"))) == 1

    # A photo that does not show one cow goes into no folder at all.
    service.handle_callback(f"cow:{sighting.id}:0:x")
    assert list((tmp_path / "onbekend").glob("*.jpg")) == []
    # Both photos are refreshed, cow A first.
    assert "🚫 Foto klopt niet" in telegram.sent("editMessageCaption")[-2]["caption"]
    service.handle_callback(f"cow:{sighting.id}:0:c0")
    assert len(list((tmp_path / BERTHA).glob("*.jpg"))) == 1


def test_typed_number_and_new_cow(tmp_path, telegram):
    service = make_service(tmp_path, pair(), [[], []])
    sighting = service.identify(*mount(), alert=42, event="e1", feedback="f1")

    service.handle_callback(f"cow:{sighting.id}:1:n")
    prompt = next(telegram.ids) - 1  # the prompt was the last message sent

    reply = service.handle_message({"text": "44", "reply_to_message": {"message_id": prompt}})
    assert reply.startswith("Nummer 44 ken ik nog niet")

    # The question survives a restart, which happens after every config change.
    service = CowService("token", "chat", tmp_path, CowsConfig(), gallery=FakeGallery([]))
    reply = service.handle_message({"text": "44 NL 5555 5555 5", "reply_to_message": {"message_id": prompt}})
    assert reply == "Opgeslagen: 44"
    sighting = service.sightings[sighting.id]
    assert sighting.cows[1] == "NL555555555"
    # The number counts from the mount the farmer answered for.
    assert service.registry.cow_with_number("44", START) == "NL555555555"


def test_switch_asks_whether_the_old_cow_left(tmp_path, telegram):
    service = make_service(tmp_path, pair(), [])
    new = "NL111111111"

    assert service.handle_message({"text": "/wissel 30 NL111111111"}) is None
    question = telegram.sent("sendMessage")[-1]
    assert "Nummer 30 was 30 (Bertha)" in question["text"]
    yes = json.loads(question["reply_markup"])["inline_keyboard"][0][0]["callback_data"]

    assert service.handle_callback(yes) == "Opgeslagen"
    assert service.registry.cow_with_number("30") == new
    assert service.registry.cow(BERTHA).archived is not None


def test_commands(tmp_path, telegram):
    service = make_service(tmp_path, pair(), [])

    assert service.handle_message({"text": "/koe 30 NL111111111"}).startswith("Nummer 30 hoort al bij")
    assert service.handle_message({"text": "/koe 7 NL 2222 2222 2 Klaartje"}).startswith("✅ Nummer 7 is nu 7 (Klaartje)")
    listing = service.handle_message({"text": "/koeien@CowCatcherBot"})
    assert listing.splitlines()[2:] == [
        "• 7 (Klaartje) · NL222222222 · 0 foto's",
        "• 12 · NL987654321 · 0 foto's",
        "• 30 (Bertha) · NL123456789 · 0 foto's",
    ]
    assert service.handle_message({"text": "/weg 12"}).startswith("✅ 12 · NL987654321 is gearchiveerd")
    assert service.handle_message({"text": "/koe 30 abc"}) == "⚠️ '' is geen levensnummer, verwacht bijvoorbeeld NL123456789"
    assert service.handle_message({"text": "hallo"}) is None


def test_overview_counts_both_cows_and_skips_wrong_alerts(tmp_path, telegram):
    service = make_service(
        tmp_path,
        pair(),
        [[(BERTHA, 0.7)], [(PINK, 0.8)], [(BERTHA, 0.7)], [(PINK, 0.8)], [], []],
    )
    first = service.identify(*mount(), alert=1, event="e1", feedback="f1")
    second = service.identify(*mount(), alert=2, event="e2", feedback="f2")
    wrong = service.identify(*mount(), alert=3, event="e3", feedback="f3")
    for sighting in (first, second):
        service.handle_callback(f"cow:{sighting.id}:0:c0")
        service.handle_callback(f"cow:{sighting.id}:1:c0")
    service.handle_callback(f"cow:{wrong.id}:0:u")
    service.feedback("f3", "bad")

    text = service.overview_text(START - timedelta(hours=1), START + timedelta(hours=1))

    assert text.splitlines()[3:] == [
        "🔥 30 (Bertha): 2× besprongen",
        "• 12: 2× gesprongen",
    ]
    # A restart keeps the answers.
    restarted = CowService("token", "chat", tmp_path, CowsConfig())
    assert restarted.overview_text(START - timedelta(hours=1), START + timedelta(hours=1)) == text


def test_without_two_cows_both_photos_show_the_mount(tmp_path, telegram):
    service = make_service(tmp_path, None, [])

    sighting = service.identify(*mount(), alert=42, event="e1", feedback="f1")

    photos = telegram.sent("sendPhoto")
    assert photos[0]["caption"].startswith("🐄 Welke koe sprong?")
    assert photos[1]["caption"].startswith("🐄 Welke koe werd besprongen?")
    assert not sighting.split
    # The mounted cow gets a wider photo than the mounter.
    folder = tmp_path / ".meldingen" / sighting.id
    mounter, mounted = (cv2.imread(str(folder / f"{slot}.jpg")) for slot in "AB")
    assert mounted.shape[0] > mounter.shape[0] and mounted.shape[1] > mounter.shape[1]
    # The photo shows both cows, so it is never filed in a cow folder.
    service.handle_callback(f"cow:{sighting.id}:1:u")
    assert not (tmp_path / "onbekend").exists()


def test_summary_includes_the_overview_per_cow(tmp_path, telegram):
    summary = SummaryService("token", "chat", tmp_path / "summary", SummaryConfig())
    best, detections = mount()
    summary.register(best, detections)
    summary.overview = lambda start, end: "\n\nPer koe (🔥 = besprongen, mogelijk tochtig):\n🔥 30: 1× besprongen"

    text = summary.build_summary(START - timedelta(hours=1), START + timedelta(hours=1))

    assert text.endswith("🔥 30: 1× besprongen")
    assert "\n\n\n" not in text


def test_event_frames_prefer_4k_and_stop_at_the_first_confident_frame():
    best, detections = mount()
    best.hires = [
        HiresFrame(START + timedelta(seconds=offset), get_image(cow_image(offset + 10), 90))
        for offset in range(-8, 6)
    ]

    frames, start = event_frames(best, detections)

    assert start == START
    # The last six frames before the mount started.
    assert [date for date, _ in frames] == [
        START + timedelta(seconds=offset) for offset in range(-6, 0)
    ]
    assert frames[0][1].shape == (60, 40, 3)


def test_gallery_ranks_cows_and_skips_archived_ones(tmp_path):
    registry = CowRegistry(tmp_path)
    registry.add("30", BERTHA)
    registry.add("12", PINK)
    for life_number, value in ((BERTHA, 20), (PINK, 220)):
        (tmp_path / life_number).mkdir()
        cv2.imwrite(str(tmp_path / life_number / "a.jpg"), cow_image(value))

    def embed(image):
        vector = np.array([image.mean(), 255 - image.mean()], dtype=np.float32)
        return vector / np.linalg.norm(vector)

    gallery = Gallery(registry, embed)
    _, scores = gallery.match(cow_image(30))
    assert [cow for cow, _ in scores] == [BERTHA, PINK]
    assert (tmp_path / BERTHA / ".embeddings" / "a.npy").is_file()

    registry.archive(BERTHA)
    _, scores = gallery.match(cow_image(30))
    assert [cow for cow, _ in scores] == [PINK]


def test_telegram_alert_starts_recognition_and_routes_farmer_input(tmp_path, monkeypatch):
    from aidetector.exporters.telegram import TelegramExporter, TelegramFeedbackListener
    from aidetector.utils.config import ChatConfig

    class AlertResponse:
        status_code = 200
        text = ""

        def json(self):
            return {"ok": True, "result": [{"message_id": 42}]}

    monkeypatch.setattr(TelegramFeedbackListener, "start", lambda self: None)
    posts = []
    monkeypatch.setattr(
        "aidetector.exporters.telegram.requests.post",
        lambda url, **kwargs: posts.append((url, kwargs)) or AlertResponse(),
    )
    exporter = TelegramExporter(
        ChatConfig(
            token="cow-token",
            chat="cow-chat",
            include_image=True,
            include_video=False,
            feedback_directory=tmp_path,
            cows=CowsConfig(),
        )
    )
    submitted = []
    monkeypatch.setattr(exporter.cows, "submit", lambda *args: submitted.append(args))
    best, detections = mount()

    exporter.export(best, detections, True)
    assert submitted and submitted[0][2] == 42
    assert exporter.cows.directory == tmp_path.resolve() / "koeien"

    # A mount the VLM rejected is not identified.
    submitted.clear()
    exporter.telegram.export_rejected = True
    exporter.config.export_rejected = True
    exporter.export(best, detections, False)
    assert submitted == []

    listener = exporter.feedback_listener
    handled = []
    monkeypatch.setattr(exporter.cows, "handle_message", lambda message: handled.append(message) or "ok")
    listener.process_message({"chat": {"id": "cow-chat"}, "message_id": 5, "text": "/koeien"})
    listener.process_message({"chat": {"id": "stranger"}, "message_id": 6, "text": "/koeien"})
    assert [message["message_id"] for message in handled] == [5]
    assert posts[-1][1]["data"]["text"] == "ok"

    monkeypatch.setattr(exporter.cows, "handle_callback", lambda data: f"got {data}")
    listener.process_callback(
        {"id": "cb", "data": "cow:abcd:0:u", "message": {"chat": {"id": "cow-chat"}, "message_id": 7}}
    )
    assert posts[-1][1]["data"]["text"] == "got cow:abcd:0:u"
