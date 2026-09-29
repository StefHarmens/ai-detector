"""Maakt de demo van de koeherkenning: draait de echte code op de stalbeelden
in example/good met een nagebootst Telegram en schrijft het gesprek naar
docs/demo/koeherkenning.html.

    cd detector && uv run python ../docs/demo/maak_demo.py

De koeien, levensnummers en antwoorden van de boer zijn verzonnen. De modellen
worden één keer opgehaald in ~/.cache/aidetector-demo."""
import base64
import json
import os
import tempfile
from datetime import datetime, timedelta
from itertools import count
from pathlib import Path

import cv2
import numpy as np

HERE = Path(__file__).resolve().parent
EXAMPLES = HERE.parent.parent / "example" / "good"
CACHE = Path.home() / ".cache" / "aidetector-demo"
WORK = Path(tempfile.mkdtemp(prefix="koeherkenning-demo-"))
# The detector reads config.json from the working directory on import.
os.chdir(WORK)
(WORK / "config.json").write_text('{"detectors":[{"detection":{"source":["demo"]}}]}')

from aidetector.cows import service as cow_module  # noqa: E402
from aidetector.exporters import summary as summary_module  # noqa: E402
from aidetector.exporters import telegram as telegram_module  # noqa: E402
from aidetector.exporters.summary import SummaryService  # noqa: E402
from aidetector.exporters.telegram import TelegramExporter, TelegramFeedbackListener  # noqa: E402
from aidetector.cows.reid import model_file  # noqa: E402
from aidetector.cows.service import Sighting  # noqa: E402
from aidetector.utils.config import (  # noqa: E402
    ChatConfig,
    CowsConfig,
    Crop,
    Detection,
    ImageSet,
    SummaryConfig,
)

SEGMENT_MODEL = "https://github.com/ultralytics/assets/releases/download/v8.3.0/yolo11s-seg.pt"
CHAT = "demo-chat"
ids = count(1000)
TODAY = datetime.now().date()
steps: list[dict] = []


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


def small(data: bytes, width: int = 640) -> bytes:
    """Keeps the page light: alert photos are shown at phone size."""
    image = cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_COLOR)
    if image.shape[1] > width:
        image = cv2.resize(image, (width, round(image.shape[0] * width / image.shape[1])), interpolation=cv2.INTER_AREA)
    return cv2.imencode(".jpg", image, (int(cv2.IMWRITE_JPEG_QUALITY), 80))[1].tobytes()


def buttons(markup) -> list[list[dict]]:
    if not markup:
        return []
    data = json.loads(markup)
    return [[{"text": b["text"], "data": b.get("callback_data")} for b in row] for row in data.get("inline_keyboard", [])]


class Response:
    def __init__(self, result):
        self.result = result
        self.status_code = 200
        self.text = ""

    def json(self):
        return {"ok": True, "result": self.result}


def fake_post(url, data=None, files=None, timeout=None, headers=None, **kwargs):
    method = url.rsplit("/", 1)[-1]
    data = dict(data or {})
    reply_to = data.get("reply_to_message_id")
    reply_to = int(reply_to) if reply_to not in (None, "") else None
    if method == "sendMediaGroup":
        media = json.loads(data["media"])
        message_id = next(ids)
        photo = files[media[0]["media"].removeprefix("attach://")][1]
        steps.append({"type": "message", "id": message_id, "kind": "photo", "image": b64(small(photo)),
                      "text": media[0].get("caption", ""), "buttons": [], "reply_to": reply_to})
        return Response([{"message_id": message_id}])
    if method in ("sendMessage", "sendPhoto"):
        message_id = next(ids)
        step = {"type": "message", "id": message_id, "kind": "text", "text": data.get("text") or data.get("caption", ""),
                "buttons": buttons(data.get("reply_markup")), "reply_to": reply_to}
        markup = json.loads(data["reply_markup"]) if data.get("reply_markup") else {}
        if markup.get("force_reply"):
            step["force_reply"] = markup.get("input_field_placeholder", "")
        if method == "sendPhoto":
            step["kind"] = "photo"
            step["image"] = b64(files["photo"][1])
        steps.append(step)
        return Response({"message_id": message_id})
    if method in ("editMessageCaption", "editMessageReplyMarkup"):
        edit = {"type": "edit", "id": int(data["message_id"]), "buttons": buttons(data.get("reply_markup"))}
        if "caption" in data:
            edit["text"] = data["caption"]
        steps.append(edit)
        return Response(True)
    if method == "getFile":
        return Response({"file_path": "documents/koeien.csv"})
    if method == "setMyCommands":
        return Response(True)
    if method == "answerCallbackQuery":
        if data.get("text"):
            steps.append({"type": "toast", "text": data["text"]})
        return Response(True)
    raise ValueError(method)


for module in (telegram_module, cow_module, summary_module):
    module.requests.post = fake_post
TelegramFeedbackListener.start = lambda self: None
SummaryService.start = lambda self: None


def note(text: str) -> None:
    steps.append({"type": "note", "text": text})


def farmer_text(text: str, reply_to: int | None = None) -> None:
    message_id = next(ids)
    steps.append({"type": "farmer", "id": message_id, "text": text, "reply_to": reply_to})
    message = {"chat": {"id": CHAT}, "message_id": message_id, "text": text}
    if reply_to:
        message["reply_to_message"] = {"message_id": reply_to}
    listener.process_message(message)


CSV = (
    "Levensnummer;Werknummer;Halsbandnummer;Naam\n"
    "NL000000030;4030;30;Bertha\n"
    "NL000000012;4012;12;\n"
    "NL000000007;4007;7;Klaartje\n"
    "NL000000051;5101;;Anna\n"
    "NL000000052;5102;;Nel\n"
)


class Download:
    content = CSV.encode()

    def raise_for_status(self):
        pass


real_get = cow_module.requests.get


def fake_get(url, *args, **kwargs):
    """Answers the download of the CSV from Telegram; models download for real."""
    if "/file/bot" in url:
        return Download()
    return real_get(url, *args, **kwargs)


cow_module.requests.get = fake_get


def farmer_document(name: str) -> None:
    message_id = next(ids)
    steps.append({"type": "farmer", "id": message_id, "text": f"📎 {name}", "document": CSV, "reply_to": None})
    listener.process_message({"chat": {"id": CHAT}, "message_id": message_id, "document": {"file_id": "demo", "file_name": name}})


def message(message_id: int) -> dict:
    return next(s for s in steps if s["type"] == "message" and s["id"] == message_id)


def current_buttons(message_id: int) -> list[list[dict]]:
    found = []
    for step in steps:
        if step.get("id") == message_id and step["type"] in ("message", "edit"):
            found = step["buttons"]
    return found


def tap(message_id: int, starts_with: str) -> None:
    button = next(b for row in current_buttons(message_id) for b in row if b["text"].lstrip("✅ ").startswith(starts_with))
    steps.append({"type": "tap", "id": message_id, "text": button["text"]})
    listener.process_callback({"id": "cb", "data": button["data"], "message": {"chat": {"id": CHAT}, "message_id": message_id}})


def last_bot_messages(amount: int) -> list[int]:
    return [s["id"] for s in steps if s["type"] == "message"][-amount:]


exporter = TelegramExporter(
    ChatConfig(
        token="demo",
        chat=CHAT,
        include_plot=True,
        include_video=False,
        feedback_directory=WORK / "data",
        summary=SummaryConfig(times=["06:00", "18:00"]),
        cows=CowsConfig(
            reid_model=str(model_file(CowsConfig().reid_model, CACHE)),
            segment_model=str(model_file(SEGMENT_MODEL, CACHE)),
        ),
    )
)
listener = exporter.feedback_listener


def alert(name: str, seconds: int) -> int:
    meta = json.loads((EXAMPLES / f"{name}.json").read_text())
    box = meta["crop"]
    image = cv2.imread(str(EXAMPLES / f"{name}.jpg"))
    # Moved to today, as if the jump happened this morning.
    taken = datetime.strptime(name, "%Y-%m-%dT%H-%M-%S")
    start = datetime.combine(TODAY, taken.time())
    crop = Crop(box["x1"], box["y1"], box["x2"], box["y2"], "mounting", meta["confidence"])
    camera = "Stal Links PTZ Voorin"
    # There are no frames from before these jumps, so the jump frame stands in for them.
    detections = [
        Detection(start - timedelta(seconds=2), ImageSet(image, [crop]), {}, camera=camera, source="cam"),
        Detection(start, ImageSet(image, [crop]), {"mounting": meta["confidence"]}, camera=camera, source="cam"),
        Detection(start + timedelta(seconds=seconds), ImageSet(image, [crop]), {"mounting": meta["confidence"]}, camera=camera, source="cam"),
    ]
    exporter.export(detections[-1], detections, True)
    exporter.cows.executor.submit(lambda: None).result()
    return last_bot_messages(1)[0]


# 1. Cows and heifers in one go, from the herd program.
steps.append({"type": "time", "text": "06:50"})
note("De boer stuurt de export uit het managementprogramma naar de bot. Koeien hebben een halsbandnummer, pinken nog niet: voor hen neemt de bot het werknummer. De commando's staan ook in het menu onder de /-knop.")
farmer_document("koeien.csv")

# 2. First alert: the farmer answers on the photo with both numbers.
steps.append({"type": "time", "text": "07:26"})
note("Eerste melding. Na de melding en Goed/Fout komt één foto met beide koeien. De bot vindt hier geen twee losse koeien (het paar staat als één koe in beeld), dus hij toont de sprong per rol. De boer antwoordt op de foto met twee nummers: eerst wie sprong.")
photo = alert("2026-09-09T07-26-27", 9)
farmer_text("30 12", reply_to=photo)

# 3. Second alert: the typing button, and one cow unknown.
steps.append({"type": "time", "text": "12:32"})
note("Tweede melding. Nu via de knop ✏️ Nummers typen, en met een naam in plaats van een nummer. De koe die sprong kent de boer niet: een vraagteken.")
photo = alert("2026-09-09T12-32-37", 11)
tap(photo, "✏️")
farmer_text("? Bertha", reply_to=last_bot_messages(1)[0])

# 4. Third alert: a new cow in the same answer.
steps.append({"type": "time", "text": "13:06"})
note("Derde melding. De koe die sprong staat nog niet in de bot: de boer typt haar nummer met levensnummer, en daarna de andere koe.")
photo = alert("2026-09-09T13-06-50", 7)
farmer_text("44 NL000000044 12", reply_to=photo)

# 5. A collar number goes to a heifer.
steps.append({"type": "time", "text": "17:10"})
note("Koe 12 is verkocht; haar halsband met nummer 12 gaat naar een pink die net gekalfd heeft.")
farmer_text("/wissel 12 NL000000052")
tap(last_bot_messages(1)[0], "Ja")
note("Pink Anna (werknummer 5101) heeft gekalfd en krijgt halsband 31. Ze blijft hetzelfde dier, met haar sprongen en foto's.")
farmer_text("/koe 31 NL000000051")
farmer_text("/koeien")

# An example of the buttons once there are split photos, built by the real
# caption and keyboard code but without a real crop.
cows = exporter.cows
example = Sighting(
    id="voorbeeld", date=datetime.combine(TODAY, datetime.min.time()).replace(hour=14).isoformat(), camera="Stal Links PTZ Voorin",
    split=True, mounter=1, role_certain=False,
    candidates=[[("NL000000030", 0.91), ("NL000000044", 0.78), ("NL000000007", 0.74)], [("NL000000044", 0.88), ("NL000000030", 0.80)]],
)
example.cows[0], example.how[0] = "NL000000030", "auto"
steps.append({"type": "example", "items": [
    {"text": cows.caption(example), "buttons": buttons(cows.keyboard(example))}
]})

# 6. The 18:00 summary with the overview per cow.
steps.append({"type": "time", "text": "18:00"})
summary = exporter.summary
state = summary.state_path
state.parent.mkdir(parents=True, exist_ok=True)
state.write_text(json.dumps({"last_sent": datetime.combine(TODAY, datetime.min.time()).replace(hour=6).isoformat()}))
summary.send_due(datetime.combine(TODAY, datetime.min.time()).replace(hour=18, second=5))

data = json.dumps(steps, ensure_ascii=False).replace("</", "<\\/")
page = (HERE / "template.html").read_text().replace("__DATA__", data)
(HERE / "koeherkenning.html").write_text(page)
print(f"{len(steps)} stappen, geschreven naar {HERE / 'koeherkenning.html'}")
