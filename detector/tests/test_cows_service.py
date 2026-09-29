import json
from datetime import datetime, timedelta
from itertools import count

import cv2
import numpy as np
import pytest

from aidetector.cows import service as service_module
from aidetector.cows.registry import CowRegistry
from aidetector.cows.reid import Gallery
from aidetector.cows.service import CowService, event_frames, split_message
from aidetector.cows.split import CowPair, grow
from aidetector.exporters.summary import SummaryService
from aidetector.media.video import get_image
from aidetector.utils.config import (
    CowsConfig,
    Crop,
    Detection,
    HiresFrame,
    ImageSet,
    SummaryConfig,
)

START = datetime(2026, 10, 2, 8, 0, 0)
BERTHA = "NL123456789"
PINK = "NL987654321"


class Telegram:
    """Records the Bot API calls and answers each with a new message."""

    def __init__(self):
        self.calls: list[tuple[str, dict]] = []
        self.ids = count(100)
        self.last_id = None

    def post(self, url, data=None, files=None, timeout=None):
        method = url.rsplit("/", 1)[-1]
        self.calls.append((method, dict(data or {})))
        self.last_id = next(self.ids)
        message_id = self.last_id

        class Response:
            status_code = 200
            text = ""

            def json(self):
                if method == "getFile":
                    return {"ok": True, "result": {"file_path": "documents/koeien.csv"}}
                return {"ok": True, "result": {"message_id": message_id}}

        return Response()

    def sent(self, method: str) -> list[dict]:
        return [data for name, data in self.calls if name == method]


class FakeSplitter:
    def __init__(self, pair: CowPair | None):
        self.pair = pair
        self.calls = []

    def split(self, frames, mount, start, end=None):
        self.calls.append((frames, start, end))
        return self.pair

    def mounted_region(self, image, mount):
        return grow(mount, 1.8)


class FakeGallery:
    def __init__(self, scores: list[list[tuple[str, float]]]):
        self.scores = list(scores)
        self.images = []

    def match(self, image):
        self.images.append(image)
        return np.zeros(2), self.scores.pop(0)


def cow_image(value: int) -> np.ndarray:
    return np.full((60, 40, 3), value, dtype=np.uint8)


def pair(certain: bool = True) -> CowPair:
    return CowPair(
        crops=[cow_image(10), cow_image(200)],
        masked=[cow_image(11), cow_image(201)],
        boxes=[(0.4, 0.4, 0.5, 0.6), (0.5, 0.4, 0.6, 0.6)],
        mounter=1,
        certain=certain,
        date=START,
    )


def mount() -> tuple[Detection, list[Detection]]:
    image = np.zeros((720, 1280, 3), dtype=np.uint8)
    crop = Crop(500, 300, 700, 500, "mounting", 0.9)
    # A frame from before the jump, the jump, and a frame after it.
    detections = [
        Detection(
            START + timedelta(seconds=offset),
            ImageSet(image, [crop]),
            {"mounting": 0.9} if 0 <= offset <= 5 else {},
            camera="Stal Links",
        )
        for offset in (-2, 0, 5, 7)
    ]
    return detections[2], detections


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
    service.registry.add("30", BERTHA, "Bertha", at=START - timedelta(days=100))
    service.registry.add("12", PINK, at=START - timedelta(days=100))
    return service


def add_photos(tmp_path, life_number: str, amount: int) -> None:
    folder = tmp_path / life_number
    folder.mkdir(parents=True, exist_ok=True)
    for index in range(amount):
        (folder / f"{index}.jpg").write_bytes(b"jpg")


def buttons(markup: str) -> list[list[str]]:
    return [[button["text"] for button in row] for row in json.loads(markup)["inline_keyboard"]]


def reply(service: CowService, text: str) -> str | None:
    """The farmer answers on the photo of the last mount."""
    sighting = max(service.sightings.values(), key=lambda sighting: sighting.message or 0)
    return service.handle_message({"text": text, "reply_to_message": {"message_id": sighting.message}})


def test_one_photo_with_both_cows_and_their_candidates(tmp_path, telegram):
    service = make_service(tmp_path, pair(certain=False), [[(BERTHA, 0.7), (PINK, 0.6)], [(PINK, 0.8)]])

    sighting = service.identify(*mount(), alert=42, event="e1", feedback="f1")

    photos = telegram.sent("sendPhoto")
    assert len(photos) == 1
    assert photos[0]["reply_to_message_id"] == "42"
    assert photos[0]["caption"].splitlines()[:3] == [
        "🐄 Wie zijn het?",
        "A werd besprongen (gok): ❓",
        "B sprong (gok): ❓",
    ]
    assert "antwoord op deze foto met de nummers of namen, eerst A dan B: 30 12" in photos[0]["caption"]
    assert buttons(photos[0]["reply_markup"]) == [
        ["A: 30 (Bertha) · 70%", "A: 12 · 60%"],
        ["B: 12 · 80%"],
        ["✏️ Nummers typen"],
        ["❔ A onbekend", "❔ B onbekend"],
        ["🔄 Andersom", "🚫 Foto A", "🚫 Foto B"],
    ]
    # Recognition compares the masked cows, without the barn.
    assert [int(image[0, 0, 0]) for image in service.gallery.images] == [11, 201]
    folder = tmp_path / ".meldingen" / sighting.id
    assert {path.name for path in folder.iterdir()} >= {
        "A.jpg", "B.jpg", "A_koe.jpg", "B_koe.jpg", "controle.jpg", "beelden",
    }
    assert sorted(path.name for path in (folder / "beelden").iterdir()) == ["+007.0s.jpg", "-002.0s.jpg"]


def test_the_splitter_gets_frames_before_and_after_the_jump(tmp_path, telegram):
    service = make_service(tmp_path, None, [])

    service.identify(*mount(), alert=42, event="e1", feedback="f1")

    frames, start, end = service.splitter.calls[0]
    assert [date - START for date, _ in frames] == [timedelta(seconds=-2), timedelta(seconds=7)]
    assert (start, end) == (START, START + timedelta(seconds=5))


def test_a_clear_match_with_enough_photos_is_filled_in(tmp_path, telegram):
    add_photos(tmp_path, BERTHA, 5)
    service = make_service(tmp_path, pair(), [[(BERTHA, 0.95), (PINK, 0.70)], [(PINK, 0.95), (BERTHA, 0.90)]])

    sighting = service.identify(*mount(), alert=42, event="e1", feedback="f1")

    assert sighting.cows == [BERTHA, None]
    assert sighting.how == ["auto", None]
    assert "A werd besprongen: ✅ 30 (Bertha) · herkend 95%" in telegram.sent("sendPhoto")[0]["caption"]


def test_tapping_a_candidate_files_the_masked_photo(tmp_path, telegram):
    service = make_service(tmp_path, pair(), [[(BERTHA, 0.7)], [(PINK, 0.8)]])
    sighting = service.identify(*mount(), alert=42, event="e1", feedback="f1")

    assert service.handle_callback(f"cow:{sighting.id}:0:c0") == "A: 30 (Bertha)"

    filed = list((tmp_path / BERTHA).glob("*.jpg"))
    assert len(filed) == 1
    # The masked crop (value 11), not the one sent to the farmer (value 10).
    assert abs(int(cv2.imread(str(filed[0]))[0, 0, 0]) - 11) <= 1
    edit = telegram.sent("editMessageCaption")[-1]
    assert "A werd besprongen: ✅ 30 (Bertha)" in edit["caption"]
    assert buttons(edit["reply_markup"])[0] == ["✅ A: 30 (Bertha) · 70%"]

    # Changing the answer moves the photo; a wrong photo goes nowhere.
    service.handle_callback(f"cow:{sighting.id}:0:u")
    assert list((tmp_path / BERTHA).glob("*.jpg")) == []
    assert len(list((tmp_path / "onbekend").glob("*.jpg"))) == 1
    service.handle_callback(f"cow:{sighting.id}:0:x")
    assert list((tmp_path / "onbekend").glob("*.jpg")) == []
    assert "A werd besprongen: 🚫 foto klopt niet" in telegram.sent("editMessageCaption")[-1]["caption"]


def test_answering_on_the_photo_with_two_numbers(tmp_path, telegram):
    service = make_service(tmp_path, pair(), [[], []])
    sighting = service.identify(*mount(), alert=42, event="e1", feedback="f1")

    assert reply(service, "30 12") == "✅ Opgeslagen: A = 30 (Bertha), B = 12"
    assert sighting.cows == [BERTHA, PINK]
    assert "Iets fout? Antwoord op deze foto met de goede nummers." in telegram.sent("editMessageCaption")[-1]["caption"]

    # Correcting later works the same way.
    assert reply(service, "? 30") == "✅ Opgeslagen: A = onbekend, B = 30 (Bertha)"


def test_one_number_fills_the_open_cow_and_new_cows_need_a_life_number(tmp_path, telegram):
    service = make_service(tmp_path, pair(), [[], []])
    sighting = service.identify(*mount(), alert=42, event="e1", feedback="f1")

    assert reply(service, "30") == "✅ Opgeslagen: A = 30 (Bertha)"
    assert reply(service, "44").startswith("Nummer 44 ken ik nog niet")
    assert reply(service, "44 NL 5555 5555 5") == "✅ Opgeslagen: B = 44"
    assert sighting.cows == [BERTHA, "NL555555555"]
    # The number counts from the mount the farmer answered for.
    assert service.registry.cow_with_number("44", START) == "NL555555555"
    assert reply(service, "12").startswith("Beide koeien zijn al ingevuld")
    assert reply(service, "30 30") == "Twee keer dezelfde koe: een koe springt niet op zichzelf."
    assert reply(service, "3x").startswith("⚠️ '3x' is geen nummer of naam")


def test_heifers_without_a_collar_by_work_number_and_name(tmp_path, telegram):
    service = make_service(tmp_path, pair(), [[], []])
    # A heifer is added with her work number; she has no collar yet.
    assert service.handle_message({"text": "/koe 1234 NL100000001 Anna"}).startswith("✅ Nummer 1234 is nu 1234 (Anna)")
    service.handle_message({"text": "/koe 1235 NL100000002 Anna"})
    service.handle_message({"text": "/koe 1236 NL100000003 Nel"})
    sighting = service.identify(*mount(), alert=42, event="e1", feedback="f1")

    assert reply(service, "nel anna").splitlines() == [
        "✅ Opgeslagen: A = 1236 (Nel)",
        "Er zijn 2 dieren die anna heten, typ het nummer.",
    ]
    assert reply(service, "Tessa").startswith("Geen koe of pink die Tessa heet")
    assert reply(service, "1234") == "✅ Opgeslagen: B = 1234 (Anna)"
    assert sighting.cows == ["NL100000003", "NL100000001"]

    # After calving she gets collar 31: the same animal, with a new number.
    service.handle_message({"text": "/koe 31 NL100000001"})
    assert service.registry.label("NL100000001") == "31 (Anna)"
    assert service.handle_message({"text": "/weg Nel"}).startswith("✅ 1236 (Nel) · NL100000003 is gearchiveerd")


def test_the_heifer_camera_shares_the_cows_but_counts_its_own_mounts(tmp_path, telegram):
    cows = make_service(tmp_path, pair(), [[], []])
    heifers = CowService(
        "token", "heifer-chat", tmp_path, CowsConfig(), splitter=FakeSplitter(pair()), gallery=FakeGallery([[], []])
    )
    heifers.handle_message({"text": "/koe 1234 NL100000001 Anna"})

    # The cows' chat knows the heifer too.
    assert cows.registry.label("NL100000001") == "1234 (Anna)"
    heifers.identify(*mount(), alert=42, event="e1", feedback="f1")
    reply(heifers, "Anna ?")

    window = (START - timedelta(hours=1), START + timedelta(hours=1))
    assert "1234 (Anna)" in heifers.overview_text(*window)
    assert cows.overview_text(*window) == ""


def test_typing_button_asks_and_the_answer_survives_a_restart(tmp_path, telegram):
    service = make_service(tmp_path, pair(), [[], []])
    sighting = service.identify(*mount(), alert=42, event="e1", feedback="f1")

    assert service.handle_callback(f"cow:{sighting.id}:-:n") == "Typ de nummers als antwoord"
    assert telegram.sent("sendMessage")[-1]["text"].startswith("Typ de nummers of namen, eerst A dan B: 30 12")
    prompt = telegram.last_id

    # A restart happens after every change to config.json.
    restarted = CowService("token", "chat", tmp_path, CowsConfig(), gallery=FakeGallery([]))
    answer = restarted.handle_message({"text": "30 12", "reply_to_message": {"message_id": prompt}})
    assert answer == "✅ Opgeslagen: A = 30 (Bertha), B = 12"


def test_swapping_roles(tmp_path, telegram):
    service = make_service(tmp_path, pair(certain=False), [[], []])
    sighting = service.identify(*mount(), alert=42, event="e1", feedback="f1")

    assert service.handle_callback(f"cow:{sighting.id}:-:s") == "Rollen omgedraaid"
    assert telegram.sent("editMessageCaption")[-1]["caption"].splitlines()[1:3] == [
        "A sprong: ❓",
        "B werd besprongen: ❓",
    ]


def test_without_two_cows_the_photo_shows_the_jump_per_role(tmp_path, telegram):
    service = make_service(tmp_path, None, [])

    sighting = service.identify(*mount(), alert=42, event="e1", feedback="f1")

    photo = telegram.sent("sendPhoto")[0]
    assert photo["caption"].splitlines()[:3] == ["🐄 Wie zijn het?", "Sprong: ❓", "Werd besprongen: ❓"]
    assert "eerst wie sprong dan wie werd besprongen: 30 12" in photo["caption"]
    assert "nummers of namen" in photo["caption"]
    assert buttons(photo["reply_markup"]) == [
        ["✏️ Nummers typen"],
        ["❔ Sprong onbekend", "❔ Besprongen onbekend"],
    ]
    # The mounted cow gets a wider photo than the mounter.
    folder = tmp_path / ".meldingen" / sighting.id
    mounter, mounted = (cv2.imread(str(folder / f"{slot}.jpg")) for slot in "AB")
    assert mounted.shape[0] > mounter.shape[0] and mounted.shape[1] > mounter.shape[1]
    # Both cows are on these photos, so they never go into a cow folder.
    assert reply(service, "? 30") == "✅ Opgeslagen: Sprong = onbekend, Besprongen = 30 (Bertha)"
    assert sighting.cows == [None, BERTHA]
    assert not (tmp_path / BERTHA).exists() and not (tmp_path / "onbekend").exists()


def test_switch_asks_whether_the_old_cow_left(tmp_path, telegram):
    service = make_service(tmp_path, pair(), [])
    new = "NL111111111"

    assert service.handle_message({"text": "/wissel 30 NL111111111"}) is None
    question = telegram.sent("sendMessage")[-1]
    assert "Nummer 30 was 30 (Bertha)" in question["text"]
    yes = json.loads(question["reply_markup"])["inline_keyboard"][0][0]["callback_data"]

    assert service.handle_callback(yes) == "Opgeslagen"
    assert telegram.sent("sendMessage")[-1]["text"] == (
        "✅ Nummer 30 is nu 30 · NL111111111. 30 (Bertha) · NL123456789 is gearchiveerd."
    )
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
    assert service.handle_message({"text": "/help"}).startswith("🐄 Koeien herkennen")
    assert service.handle_message({"text": "hallo"}) is None


def test_command_menu(tmp_path, telegram):
    service = make_service(tmp_path, pair(), [])

    service.set_commands()

    commands = json.loads(telegram.sent("setMyCommands")[0]["commands"])
    assert [command["command"] for command in commands] == ["koeien", "overzicht", "koe", "wissel", "weg", "help"]


def test_a_csv_sent_to_the_bot_adds_the_cows(tmp_path, telegram, monkeypatch):
    service = make_service(tmp_path, pair(), [])

    class Download:
        content = "Werknummer;Levensnummer;Naam\n7;NL222222222;Klaartje\n8;fout;\n".encode()

        def raise_for_status(self):
            pass

    monkeypatch.setattr(service_module.requests, "get", lambda url, timeout: Download())

    answer = service.handle_message({"document": {"file_id": "f", "file_name": "export koeien.csv"}})

    assert answer.splitlines() == [
        "✅ 1 dier ingelezen op werknummer.",
        "Regel 3: 'fout' is geen levensnummer, verwacht bijvoorbeeld NL123456789",
    ]
    assert service.registry.label("NL222222222") == "7 (Klaartje)"
    assert service.handle_message({"document": {"file_id": "f", "file_name": "foto.jpg"}}).startswith("Stuur de koeien als Excel- of CSV-bestand")


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


def test_summary_includes_the_overview_per_cow(tmp_path, telegram):
    summary = SummaryService("token", "chat", tmp_path / "summary", SummaryConfig())
    best, detections = mount()
    summary.register(best, detections)
    summary.overview = lambda start, end: "\n\nPer koe (🔥 = besprongen, mogelijk tochtig):\n🔥 30: 1× besprongen"

    text = summary.build_summary(START - timedelta(hours=1), START + timedelta(hours=1))

    assert text.endswith("🔥 30: 1× besprongen")
    assert "\n\n\n" not in text


def test_event_frames_prefer_4k_and_skip_the_jump_itself():
    best, detections = mount()
    best.hires = [
        HiresFrame(START + timedelta(seconds=offset), get_image(cow_image(offset + 10), 90))
        for offset in range(-8, 14)
    ]

    frames = event_frames(best, detections)

    assert (frames.start, frames.end) == (START, START + timedelta(seconds=5))
    # Six frames on each side of the jump, none during it.
    assert [date - START for date, _ in frames.frames] == [
        timedelta(seconds=offset) for offset in [*range(-6, 0), *range(6, 12)]
    ]
    assert frames.frames[0][1].shape == (60, 40, 3)
    assert frames.jpegs[0][1] == best.hires[2].jpeg


def test_long_replies_are_split_at_line_ends():
    text = "\n".join(f"• koe {number}" for number in range(1000))

    parts = split_message(text, limit=100)

    assert all(len(part) <= 100 for part in parts)
    assert "\n".join(parts) == text


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
    monkeypatch.setattr(CowService, "set_commands", lambda self: None)
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
    monkeypatch.setattr(
        exporter.cows, "handle_message", lambda message: handled.append(message) or "ok\n" * 3000
    )
    before = len(posts)
    listener.process_message({"chat": {"id": "cow-chat"}, "message_id": 5, "text": "/koeien"})
    listener.process_message({"chat": {"id": "stranger"}, "message_id": 6, "text": "/koeien"})
    assert [message["message_id"] for message in handled] == [5]
    # A long reply goes out in parts.
    assert len(posts) - before >= 3

    monkeypatch.setattr(exporter.cows, "handle_callback", lambda data: f"got {data}")
    listener.process_callback(
        {"id": "cb", "data": "cow:abcd:0:u", "message": {"chat": {"id": "cow-chat"}, "message_id": 7}}
    )
    assert posts[-1][1]["data"]["text"] == "got cow:abcd:0:u"
