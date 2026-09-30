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
    server = CowApi(ApiConfig(port=0))
    server.start()
    assert server.server is not None
    port = server.server.server_address[1]

    def call(method: str, path: str, body: dict | None = None):
        request = Request(
            f"http://127.0.0.1:{port}/api/{path}",
            method=method,
            data=json.dumps(body).encode() if body is not None else None,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urlopen(request) as response:
                data = response.read()
                if response.headers["Content-Type"] == "image/jpeg":
                    return response.status, data
                return response.status, json.loads(data)
        except HTTPError as error:
            return error.code, json.loads(error.read())

    call.services = services
    yield call
    server.stop()


def register(api, service):
    api.services[("token", service.chat)] = service
    return service


def test_without_cows_the_page_says_how_to_turn_them_on(api):
    assert api("GET", "status") == (200, {"cows": False, "chats": []})
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
        {"cow": BERTHA, "label": "30 (Bertha)", "score": 0.7, "photo": "1.jpg"}
    ]
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
        {"action": "wissel", "number": "30", "life_number": "NL555666777", "name": "Nel", "old_left": True},
    )
    assert status == 200
    assert body["message"] == "Nummer 30 is nu 30 (Nel) · NL555666777. 30 (Bertha) is gearchiveerd."
    assert service.registry.cow(BERTHA).archived is not None

    assert api("POST", "koeien", {"action": "weg", "life_number": PINK})[0] == 200
    assert [item["life_number"] for item in api("GET", "koeien")[1]["items"]] == ["NL555666777"]
    assert len(api("GET", "koeien?archief=1")[1]["items"]) == 3


def test_overview_counts_both_cows(tmp_path, api, telegram):
    service = register(api, make_service(tmp_path, pair(), [[], []]))
    sighting = service.identify(*mount(), alert=42, event="e1", feedback="f1")
    sighting.date = service_module.datetime.now().isoformat()
    service.set_cow(sighting.id, 0, "30")

    status, body = api("GET", "overzicht?dagen=7")

    assert status == 200
    assert body["mounts"] == 1 and body["unknown"] == 1
    assert body["items"] == [{"cow": BERTHA, "label": "30 (Bertha)", "mounted": 1, "mounting": 0}]


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
