import json
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest
from test_cows_service import BERTHA, PINK, START, Telegram, add_photos, make_service, mount, pair

from aidetector.cows import service as service_module
from aidetector.cows.api import CowApi
from aidetector.utils.config import ApiConfig


@pytest.fixture
def telegram(monkeypatch):
    fake = Telegram()
    monkeypatch.setattr(service_module.requests, "post", fake.post)
    return fake


@pytest.fixture
def api(monkeypatch):
    services: dict = {}
    monkeypatch.setattr(service_module, "_cow_services", services)
    doubts = []
    server = CowApi(ApiConfig(port=0), doubts)
    server.start()
    assert server.server is not None
    port = server.server.server_address[1]

    def call(method: str, path: str, body: dict | None = None, headers: dict | None = None):
        request = Request(
            f"http://127.0.0.1:{port}/api/{path}",
            method=method,
            data=json.dumps(body).encode() if body is not None else None,
            headers={"Content-Type": "application/json", **(headers or {})},
        )
        try:
            with urlopen(request) as response:
                data = response.read()
                if response.headers["Content-Type"] in ("image/jpeg", "video/mp4"):
                    return response.status, data
                return response.status, json.loads(data)
        except HTTPError as error:
            return error.code, json.loads(error.read())

    call.services = services
    call.doubts = doubts
    call.server = server
    yield call
    server.stop()


def register(api, service):
    api.services[("token", service.chat)] = service
    return service


def test_without_cows_the_page_says_how_to_turn_them_on(api):
    assert api("GET", "status") == (200, {"cows": False, "chats": [], "version": "main"})
    status, body = api("GET", "sprongen")
    assert status == 503
    assert '"cows": {}' in body["error"]


def test_open_mounts_with_photos_and_candidates(tmp_path, api, telegram):
    add_photos(tmp_path, BERTHA, 2)
    service = register(api, make_service(tmp_path, pair(certain=False), [[(BERTHA, 0.7)], [(PINK, 0.8)]]))
    sighting = service.identify(*mount(), alert=42, event="e1", feedback="f1")

    status, body = api("GET", "sprongen")

    assert status == 200
    assert body["total"] == 1 and body["open"] == 1
    assert body["cameras"] == ["Stal Links"]
    item = body["items"][0]
    assert item["id"] == sighting.id
    assert item["photos"] == ["A", "B", "controle"]
    assert [slot["title"] for slot in item["slots"]] == ["A werd besprongen (gok)", "B sprong (gok)"]
    assert item["slots"][0]["candidates"] == [
        {"cow": BERTHA, "label": "30 (Bertha)", "score": 0.7, "photo": "1.jpg", "photos": 2}
    ]
    # What the page needs to explain why a cow was not filled in itself.
    assert body["rules"] == {"accept_score": 0.9, "accept_margin": 0.08, "min_photos": 5}
    status, photo = api("GET", f"sprongen/{sighting.id}/A.jpg")
    assert status == 200 and photo[:2] == b"\xff\xd8"


def test_filling_in_from_the_web_files_the_photo_and_updates_telegram(tmp_path, api, telegram):
    service = register(api, make_service(tmp_path, pair(), [[], []]))
    sighting = service.identify(*mount(), alert=42, event="e1", feedback="f1")

    status, body = api("POST", f"sprongen/{sighting.id}", {"action": "koe", "slot": 0, "value": "30"})
    assert status == 200
    assert body["message"] == "A = 30 (Bertha)"
    assert body["item"]["slots"][0]["how"] == "boer"
    assert body["item"]["open"] is True

    status, body = api("POST", f"sprongen/{sighting.id}", {"action": "koe", "slot": 1, "value": PINK})
    assert status == 200
    assert body["item"]["open"] is False
    assert sighting.cows == [BERTHA, PINK]
    assert len(list((tmp_path / BERTHA).glob("*.jpg"))) == 1
    assert "✅ 30 (Bertha)" in telegram.sent("editMessageCaption")[-1]["caption"]
    assert api("GET", "sprongen")[1]["total"] == 0


def test_web_answers_are_checked_like_telegram_answers(tmp_path, api, telegram):
    service = register(api, make_service(tmp_path, pair(), [[], []]))
    sighting = service.identify(*mount(), alert=42, event="e1", feedback="f1")
    path = f"sprongen/{sighting.id}"

    status, body = api("POST", path, {"action": "koe", "slot": 0, "value": "77"})
    assert status == 400 and "Nummer 77 ken ik nog niet" in body["error"]

    api("POST", path, {"action": "koe", "slot": 0, "value": "30"})
    status, body = api("POST", path, {"action": "koe", "slot": 1, "value": "Bertha"})
    assert status == 400 and "dezelfde koe" in body["error"]

    status, body = api("POST", path, {"action": "koe", "slot": 1, "value": "44 NL111222333"})
    assert status == 200 and body["message"] == "B = 44"
    assert service.registry.cow_with_number("44", START) == "NL111222333"


def test_unknown_bad_photo_and_swap(tmp_path, api, telegram):
    service = register(api, make_service(tmp_path, pair(certain=False), [[], []]))
    sighting = service.identify(*mount(), alert=42, event="e1", feedback="f1")
    path = f"sprongen/{sighting.id}"

    assert api("POST", path, {"action": "andersom"})[1]["item"]["slots"][0]["mounter"] is True
    assert api("POST", path, {"action": "onbekend", "slot": 0})[1]["message"] == "A: onbekend"
    status, body = api("POST", path, {"action": "fotofout", "slot": 1})
    assert status == 200
    assert body["item"]["slots"][1]["bad_photo"] is True
    assert sighting.how == ["boer", "boer"]
    assert not list(tmp_path.glob("NL*/*.jpg"))


def test_a_failing_telegram_does_not_lose_the_web_choice(tmp_path, api, telegram, monkeypatch):
    service = register(api, make_service(tmp_path, pair(), [[], []]))
    sighting = service.identify(*mount(), alert=42, event="e1", feedback="f1")

    def offline(*args, **kwargs):
        raise service_module.requests.ConnectionError("no internet")

    monkeypatch.setattr(service_module.requests, "post", offline)
    status, _ = api("POST", f"sprongen/{sighting.id}", {"action": "koe", "slot": 0, "value": "30"})

    assert status == 200
    assert sighting.cows[0] == BERTHA


def test_cows_list_photos_and_delete_a_wrong_photo(tmp_path, api, telegram):
    add_photos(tmp_path, BERTHA, 3)
    register(api, make_service(tmp_path, pair(), [[], []]))

    status, body = api("GET", "koeien")
    assert status == 200
    assert [(item["number"], item["label"], item["photos"]) for item in body["items"]] == [
        ("12", "12", 0),
        ("30", "30 (Bertha)", 3),
    ]
    assert api("GET", f"koeien/{BERTHA}/fotos")[1]["photos"] == ["2.jpg", "1.jpg", "0.jpg"]
    assert api("GET", f"koeien/{BERTHA}/fotos/..%2Fkoeien.json")[0] == 404

    assert api("DELETE", f"koeien/{BERTHA}/fotos/1.jpg")[0] == 200
    assert sorted(path.name for path in (tmp_path / BERTHA).glob("*.jpg")) == ["0.jpg", "2.jpg"]


def test_add_switch_and_archive_cows(tmp_path, api, telegram):
    service = register(api, make_service(tmp_path, pair(), [[], []]))

    status, body = api("POST", "koeien", {"action": "toevoegen", "number": "30", "life_number": "NL555666777"})
    assert status == 409 and "Nummer wisselen" in body["error"]

    status, body = api(
        "POST", "koeien",
        {
            "action": "wissel", "number": "30", "life_number": "NL555666777", "name": "Nel",
            "work_number": "5101", "old_left": True,
        },
    )
    assert status == 200
    assert body["message"] == "Nummer 30 is nu 30 (Nel) · NL555666777. 30 (Bertha) is gearchiveerd."
    assert service.registry.cow("NL555666777").work_number == "5101"
    assert service.registry.cow(BERTHA).archived is not None

    assert api("POST", "koeien", {"action": "weg", "life_number": PINK})[0] == 200
    assert [item["life_number"] for item in api("GET", "koeien")[1]["items"]] == ["NL555666777"]
    assert len(api("GET", "koeien?archief=1")[1]["items"]) == 3

    status, body = api(
        "POST", "koeien",
        {"action": "toevoegen", "number": "41", "life_number": "NL888999000", "work_number": " 7 "},
    )
    assert status == 200
    assert service.registry.cow("NL888999000").work_number == "7"


def test_overview_counts_both_cows(tmp_path, api, telegram):
    service = register(api, make_service(tmp_path, pair(), [[], []]))
    sighting = service.identify(*mount(), alert=42, event="e1", feedback="f1")
    sighting.date = service_module.datetime.now().isoformat()
    service.set_cow(sighting.id, 0, "30")

    status, body = api("GET", "overzicht?dagen=7")

    assert status == 200
    assert body["mounts"] == 1 and body["unknown"] == 1
    assert body["items"] == [
        {"cow": BERTHA, "label": "30 (Bertha)", "work_number": None, "mounted": 1, "mounting": 0}
    ]


def test_no_mount_files_it_as_bad_and_stops_counting(tmp_path, api, telegram):
    service = register(api, make_service(tmp_path, pair(), [[], []]))
    sighting = service.identify(*mount(), alert=42, event="e1", feedback="f1")
    classified = []
    service.classify = lambda feedback, label: classified.append((feedback, label))
    path = f"sprongen/{sighting.id}"

    status, body = api("POST", path, {"action": "geensprong"})

    assert status == 200 and body["message"] == "Opgeslagen als geen sprong"
    assert classified == [("f1", "bad")]
    assert body["item"]["false"] is True and body["item"]["open"] is False
    assert api("GET", "sprongen")[1]["total"] == 0

    assert api("POST", path, {"action": "welsprong"})[1]["item"]["false"] is False
    assert classified[-1] == ("f1", "good")


def test_no_mount_still_counts_when_the_training_image_is_gone(tmp_path, api, telegram):
    service = register(api, make_service(tmp_path, pair(), [[], []]))
    sighting = service.identify(*mount(), alert=42, event="e1", feedback="f1")

    def missing(feedback, label):
        raise FileNotFoundError("Feedback image not found")

    service.classify = missing
    status, _ = api("POST", f"sprongen/{sighting.id}", {"action": "geensprong"})

    assert status == 200 and sighting.false is True


def test_wrong_split_takes_the_photos_out_but_keeps_the_cows(tmp_path, api, telegram):
    service = register(api, make_service(tmp_path, pair(), [[(BERTHA, 0.7)], [(PINK, 0.8)]]))
    sighting = service.identify(*mount(), alert=42, event="e1", feedback="f1")
    path = f"sprongen/{sighting.id}"
    api("POST", path, {"action": "koe", "slot": 0, "value": "30"})
    assert len(list((tmp_path / BERTHA).glob("*.jpg"))) == 1

    status, body = api("POST", path, {"action": "splitfout"})

    assert status == 200
    assert body["item"]["split_wrong"] is True
    assert [slot["candidates"] for slot in body["item"]["slots"]] == [[], []]
    assert not list((tmp_path / BERTHA).glob("*.jpg"))
    # The cows still count, but new answers file no photo.
    api("POST", path, {"action": "koe", "slot": 1, "value": "12"})
    assert sighting.cows == [BERTHA, PINK]
    assert not list(tmp_path.glob("NL*/*.jpg"))
    assert api("POST", path, {"action": "fotofout", "slot": 0})[0] == 400

    api("POST", path, {"action": "splitgoed"})
    assert len(list((tmp_path / BERTHA).glob("*.jpg"))) == 1
    assert len(list((tmp_path / PINK).glob("*.jpg"))) == 1


def test_a_jump_photo_cannot_be_a_wrong_split(tmp_path, api, telegram):
    service = register(api, make_service(tmp_path, None, []))
    sighting = service.identify(*mount(), alert=42, event="e1", feedback="f1")

    assert api("POST", f"sprongen/{sighting.id}", {"action": "splitfout"})[0] == 400


def test_a_wrong_split_drops_what_was_recognised_from_it(tmp_path, api, telegram):
    add_photos(tmp_path, BERTHA, 5)
    service = register(api, make_service(tmp_path, pair(), [[(BERTHA, 0.95), (PINK, 0.70)], [(PINK, 0.6)]]))
    sighting = service.identify(*mount(), alert=42, event="e1", feedback="f1")
    assert sighting.how == ["auto", None]

    api("POST", f"sprongen/{sighting.id}", {"action": "splitfout"})

    assert sighting.cows == [None, None] and sighting.how == [None, None]


def test_the_alert_video_plays_in_parts(tmp_path, api, telegram):
    service = register(api, make_service(tmp_path, pair(), [[], []]))
    sighting = service.identify(*mount(), alert=42, event="e1", feedback="f1", video=b"0123456789")
    path = f"sprongen/{sighting.id}/video.mp4"

    assert api("GET", "sprongen")[1]["items"][0]["video"] is True
    assert api("GET", path) == (200, b"0123456789")
    assert api("GET", path, headers={"Range": "bytes=2-5"}) == (206, b"2345")
    assert api("GET", path, headers={"Range": "bytes=7-"}) == (206, b"789")
    assert api("GET", path, headers={"Range": "bytes=-3"}) == (206, b"789")


def test_a_mount_without_video_says_so(tmp_path, api, telegram):
    service = register(api, make_service(tmp_path, pair(), [[], []]))
    sighting = service.identify(*mount(), alert=42, event="e1", feedback="f1")

    assert api("GET", "sprongen")[1]["items"][0]["video"] is False
    assert api("GET", f"sprongen/{sighting.id}/video.mp4")[0] == 404


def test_the_mounts_of_one_cow(tmp_path, api, telegram):
    service = register(api, make_service(tmp_path, pair(), [[], [], [], []]))
    first = service.identify(*mount(), alert=42, event="e1", feedback="f1")
    second = service.identify(*mount(), alert=43, event="e2", feedback="f2")
    service.set_cow(first.id, 0, "30")
    service.set_cow(second.id, 1, "30")
    service.set_cow(second.id, 0, "12")
    service.set_mount(second.id, False)

    body = api("GET", f"sprongen?filter=alles&koe={BERTHA}")[1]

    assert [item["id"] for item in body["items"]] == [first.id]


def make_doubt(folder, name: str, confidence: float = 0.74) -> None:
    event = folder / name
    event.mkdir(parents=True)
    (event / "clean.jpg").write_bytes(b"\xff\xd8clean")
    (event / "best.jpg").write_bytes(b"\xff\xd8best")
    (event / "video.mp4").write_bytes(b"mp4")
    (event / "metadata.json").write_text(
        json.dumps({"confidence": confidence, "duration": 4.2, "camera": name[20:]})
    )


def test_doubtful_mounts_are_judged_like_review_feedback(tmp_path, api):
    folder = tmp_path / "data" / "twijfel"
    make_doubt(folder, "2026-09-30T09-07-00 Stal Rechts Voorin")
    make_doubt(folder, "2026-09-30T10-12-30 Camera kleine stal", 0.71)
    api.doubts.append(folder)

    status, body = api("GET", "twijfel")
    assert status == 200
    assert [item["name"] for item in body["items"]] == [
        "2026-09-30T10-12-30 Camera kleine stal",
        "2026-09-30T09-07-00 Stal Rechts Voorin",
    ]
    assert body["items"][0]["date"] == "2026-09-30T10:12:30"
    assert body["items"][0]["camera"] == "Camera kleine stal"
    assert body["counts"] == {"open": 2, "good": 0, "bad": 0, "skip": 0}

    name = "2026-09-30T09-07-00 Stal Rechts Voorin"
    assert api("GET", f"twijfel/0/{name.replace(' ', '%20')}/best.jpg") == (200, b"\xff\xd8best")
    status, body = api("POST", f"twijfel/0/{name.replace(' ', '%20')}", {"decision": "good"})
    assert status == 200 and body["decision"] == "good"
    assert (tmp_path / "data" / "good" / f"{name}.jpg").read_bytes() == b"\xff\xd8clean"
    assert api("GET", "twijfel")[1]["total"] == 1

    api("POST", f"twijfel/0/{name.replace(' ', '%20')}", {"decision": "bad"})
    assert not (tmp_path / "data" / "good" / f"{name}.jpg").exists()
    assert (tmp_path / "data" / "bad" / f"{name}.json").is_file()

    api("POST", f"twijfel/0/{name.replace(' ', '%20')}", {"decision": None})
    assert not (tmp_path / "data" / "bad" / f"{name}.jpg").exists()
    assert api("GET", "twijfel?filter=alles")[1]["counts"]["open"] == 2


def test_without_a_doubt_folder_the_page_says_how_to_add_one(api):
    status, body = api("GET", "twijfel")
    assert status == 404
    assert '"review": true' in body["error"]


def test_doubt_files_stay_inside_the_folder(tmp_path, api):
    folder = tmp_path / "data" / "twijfel"
    make_doubt(folder, "2026-09-30T09-07-00 Stal Rechts Voorin")
    (tmp_path / "data" / "koeienlijst.xlsx").write_bytes(b"secret")
    api.doubts.append(folder)

    assert api("GET", "twijfel/0/..%2Fkoeienlijst.xlsx/clean.jpg")[0] == 404
    assert api("GET", "twijfel/0/2026-09-30T09-07-00%20Stal%20Rechts%20Voorin/metadata.json")[0] == 404


@pytest.mark.parametrize(
    ("ref_name", "expected"),
    [("detector/v0.9.0", "v0.9.0"), ("main", "main"), ("feat/versions", "feat-versions")],
)
def test_version_is_the_release_tag_without_prefix(monkeypatch, ref_name, expected):
    from aidetector.cows import api as api_module

    monkeypatch.setattr(api_module, "REF_NAME", ref_name)

    assert api_module.version() == expected


def upload(api, path: str, data: bytes):
    """Sends a photo as it is, as the web page does."""
    port = api.server.server.server_address[1]
    request = Request(f"http://127.0.0.1:{port}/api/{path}", method="POST", data=data, headers={"Content-Type": "image/jpeg"})
    try:
        with urlopen(request) as response:
            return response.status, json.loads(response.read())
    except HTTPError as error:
        return error.code, json.loads(error.read())


def barn_photo() -> bytes:
    """Two cows of different size on a dark barn floor."""
    import numpy as np

    from aidetector.media.video import get_image

    image = np.full((400, 600, 3), 30, dtype=np.uint8)
    image[100:200, 50:200] = 220  # the big cow
    image[250:300, 400:460] = 160  # a small one further away
    return get_image(image, 95)


def two_cows(image, imgsz=640):
    """A cow model that finds the two cows of barn_photo, with their pixels."""
    import numpy as np

    height, width = image.shape[:2]
    found = []
    for x1, y1, x2, y2 in ((400, 250, 460, 300), (50, 100, 200, 200)):
        mask = np.zeros((height, width), dtype=bool)
        mask[y1:y2, x1:x2] = True
        found.append(((x1 / width, y1 / height, x2 / width, y2 / height), mask))
    two_cows.sizes.append(imgsz)
    return found


two_cows.sizes = []


def test_a_photo_the_farmer_adds_goes_masked_into_the_cow_folder(tmp_path, api, telegram):
    from aidetector.cows.split import MASK_FILL, CowSplitter

    service = register(api, make_service(tmp_path, pair(), []))
    service.splitter = CowSplitter(two_cows)

    status, found = upload(api, "koeien/zoek", barn_photo())

    assert status == 200
    # Biggest first, looked at large enough for cows far away in a barn view.
    assert [cow["index"] for cow in found["cows"]] == [0, 1]
    assert found["cows"][0]["box"] == pytest.approx([50 / 600, 100 / 400, 200 / 600, 200 / 400])
    assert two_cows.sizes[-1] == 1280
    status, photo = api("GET", f"koeien/zoek/{found['token']}.jpg")
    assert status == 200 and photo[:2] == b"\xff\xd8"
    # Each cow on her own as well, to pick her when the boxes overlap.
    status, photo = api("GET", f"koeien/zoek/{found['token']}_1_foto.jpg")
    assert status == 200 and photo[:2] == b"\xff\xd8"
    assert api("GET", f"koeien/zoek/{found['token']}_1.jpg")[0] == 400

    status, added = api("POST", f"koeien/{BERTHA}/fotos", {"token": found["token"], "index": 0})

    assert status == 200
    assert added["photos"] == 1 and added["message"] == "Foto toegevoegd aan 30 (Bertha)"
    import cv2

    saved = cv2.imread(str(tmp_path / BERTHA / added["photo"]))
    # The cow herself, with the barn around her grey as in the mount photos.
    assert saved[saved.shape[0] // 2, saved.shape[1] // 2].mean() > 200
    assert abs(int(saved[1, 1].mean()) - MASK_FILL) < 10
    status, body = api("GET", f"koeien/{BERTHA}/fotos")
    assert body["photos"] == [added["photo"]]


def test_a_photo_that_is_no_photo_or_has_expired_says_so(tmp_path, api, telegram):
    from aidetector.cows.split import CowSplitter

    service = register(api, make_service(tmp_path, pair(), []))
    service.splitter = CowSplitter(two_cows)

    assert upload(api, "koeien/zoek", b"no image")[0] == 400
    status, body = api("POST", f"koeien/{BERTHA}/fotos", {"token": "0123456789ab", "index": 0})
    assert status == 400 and "verlopen" in body["error"]
    status, body = api("POST", f"koeien/{BERTHA}/fotos", {"token": "../../etc", "index": 0})
    assert status == 400
    status, found = upload(api, "koeien/zoek", barn_photo())
    status, body = api("POST", "koeien/NL000000000/fotos", {"token": found["token"], "index": 0})
    assert status == 404


def test_a_photo_can_be_taken_from_a_camera(tmp_path, api, telegram):
    import subprocess

    from imageio_ffmpeg import get_ffmpeg_exe

    from aidetector.cows.split import CowSplitter

    service = register(api, make_service(tmp_path, pair(), []))
    service.splitter = CowSplitter(two_cows)
    video = tmp_path / "stal.mp4"
    subprocess.run(
        [get_ffmpeg_exe(), "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=size=640x360:rate=5:duration=1", str(video)],
        check=True,
    )
    api.server.server.cameras = [("Stal Links", str(video))]

    assert api("GET", "cameras") == (200, [{"index": 0, "name": "Stal Links"}])
    status, found = api("POST", "koeien/zoek?camera=0")
    assert status == 200 and (found["width"], found["height"]) == (640, 360)
    assert len(found["cows"]) == 2
    assert api("POST", "koeien/zoek?camera=3")[0] == 404


def test_cameras_use_their_sharpest_stream():
    from aidetector.cows.api import camera_sources
    from aidetector.utils.config import Config

    config = Config(
        detectors=[
            {
                "detection": {
                    "source": ["rtsps://nvr/a-medium", "rtsps://nvr/b-medium"],
                    "name": ["Stal Links", "Stal Rechts"],
                    "hires": {"source": ["rtsps://nvr/a-high", None]},
                }
            }
        ]
    )

    assert camera_sources(config) == [
        ("Stal Links", "rtsps://nvr/a-high"),
        ("Stal Rechts", "rtsps://nvr/b-medium"),
    ]


def test_a_cow_found_twice_is_shown_once_and_only_with_her_own_pixels(tmp_path):
    import numpy as np

    from aidetector.cows.split import Found
    from aidetector.cows.upload import _whole_cows

    mask = np.zeros((100, 100), dtype=bool)
    mask[40:90, 40:90] = True
    # A piece of the neighbour's coat, apart from her.
    mask[5:10, 5:10] = True
    cow = Found((0.4, 0.4, 0.9, 0.9), mask)
    twice = Found((0.41, 0.41, 0.9, 0.9), mask)
    other = Found((0.0, 0.0, 0.3, 0.3), None)

    kept = _whole_cows([cow, twice, other])

    assert [found.box for found in kept] == [cow.box, other.box]
    assert kept[0].mask is not None
    assert not kept[0].mask[5:10, 5:10].any() and kept[0].mask[40:90, 40:90].all()


def test_a_camera_photo_is_a_whole_frame_when_joining_a_stream_halfway(tmp_path):
    import subprocess

    import cv2
    import numpy as np
    from imageio_ffmpeg import get_ffmpeg_exe

    from aidetector.cows.upload import snapshot

    # An HEVC stream, as the UniFi 4K streams are, with a keyframe every 2 s.
    stream = tmp_path / "stream.hevc"
    subprocess.run(
        [
            get_ffmpeg_exe(), "-loglevel", "error", "-f", "lavfi",
            "-i", "testsrc2=size=640x360:rate=25:duration=4", "-c:v", "libx265",
            "-x265-params", "keyint=50:min-keyint=50:bframes=0:log-level=none",
            "-pix_fmt", "yuv420p", str(stream),
        ],
        check=True,
    )
    data = stream.read_bytes()
    # Over RTSP the decoder has the parameter sets from the start (before the
    # first keyframe here) and joins at whatever frame comes: the frames
    # before the next keyframe have nothing to build on and decoded grey.
    keyframe = next(
        index for index in range(len(data) - 4)
        if data[index : index + 3] == b"\x00\x00\x01" and 16 <= (data[index + 3] >> 1) & 0x3F <= 21
    )
    halfway = data.index(b"\x00\x00\x01", len(data) // 4)
    joined = tmp_path / "joined.hevc"
    joined.write_bytes(data[:keyframe] + data[halfway:])

    image = cv2.imdecode(np.frombuffer(snapshot(str(joined)), dtype=np.uint8), cv2.IMREAD_COLOR)

    # testsrc2 is colourful all over; a grey frame is nearly flat.
    assert image.std() > 40
