"""A small JSON API for the cow page of the web interface. The register and
the mounts live in the detector's memory, so the web interface asks the
detector instead of writing the files itself. It listens on this computer
only; the web interface shows the page on the farm network."""

import json
import logging
import re
from datetime import datetime, timedelta
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from aidetector.cows.registry import NumberTaken, normalize_life_number, normalize_number
from aidetector.cows.reid import EMBEDDINGS_FOLDER
from aidetector.cows.service import (
    CROPS_FOLDER,
    VIDEO_FILE,
    CowService,
    Sighting,
    cow_services,
)
from aidetector.review import MEDIA_TYPES, ReviewSession
from aidetector.utils.config import ApiConfig, Config
from aidetector.utils.version import REF_NAME

logger = logging.getLogger(__name__)


def version() -> str:
    """The release the detector was built from: "v0.9.0" for the tag
    "detector/v0.9.0", else the branch, e.g. "main" when run from source."""
    return REF_NAME.removeprefix("detector/").replace("/", "-")

_SIGHTING_ID = re.compile(r"^[0-9a-f]{1,32}$")
_PHOTO_NAME = re.compile(r"^[A-Za-z0-9._-]+\.jpg$")
_SIGHTING_PHOTOS = ("A", "B", "controle")
_MAX_BODY = 64 * 1024


class ApiError(Exception):
    def __init__(self, status: HTTPStatus, message: str):
        super().__init__(message)
        self.status = status


def _services() -> list[CowService]:
    services = cow_services()
    if not services:
        raise ApiError(
            HTTPStatus.SERVICE_UNAVAILABLE,
            'Koeien herkennen staat uit: zet "cows": {} bij een Telegram-chat in config.json.',
        )
    return services


def _mount_services() -> list[CowService]:
    """One service per folder of cows: chats that share a folder share its
    mounts, which would otherwise be listed and counted twice."""
    seen: set[int] = set()
    services = []
    for service in _services():
        if id(service.mounts) not in seen:
            seen.add(id(service.mounts))
            services.append(service)
    return services


def _find(sighting_id: str) -> tuple[CowService, Sighting]:
    if not _SIGHTING_ID.match(sighting_id):
        raise ApiError(HTTPStatus.NOT_FOUND, "Deze sprong bestaat niet.")
    for service in _mount_services():
        with service.lock:
            sighting = service.sightings.get(sighting_id)
        if sighting is not None:
            return service, sighting
    raise ApiError(HTTPStatus.NOT_FOUND, "Deze sprong bestaat niet (meer).")


def _life_number(value: str) -> str:
    try:
        return normalize_life_number(value)
    except ValueError as error:
        raise ApiError(HTTPStatus.BAD_REQUEST, str(error)) from error


def _latest_photo(service: CowService, life_number: str) -> str | None:
    photos = service.registry.photos(life_number)
    return photos[-1].name if photos else None


def is_open(sighting: Sighting) -> bool:
    """Still waiting for the farmer: a cow nobody filled in."""
    return not sighting.false and None in sighting.how


def sighting_view(service: CowService, sighting: Sighting) -> dict[str, Any]:
    registry = service.registry
    names = service._names(sighting)
    slots = []
    for slot in (0, 1):
        cow = sighting.cows[slot]
        scores = dict(sighting.candidates[slot])
        slots.append(
            {
                "slot": slot,
                "name": names[slot],
                "title": service._title(sighting, slot),
                "mounter": slot == sighting.mounter,
                "cow": cow,
                "label": registry.label(cow, sighting.when) if cow else None,
                "how": sighting.how[slot],
                "bad_photo": sighting.bad_photo[slot],
                "score": scores.get(cow) if cow else None,
                "candidates": [
                    {
                        "cow": candidate,
                        "label": registry.label(candidate, sighting.when),
                        "score": score,
                        "photo": _latest_photo(service, candidate),
                    }
                    for candidate, score in sighting.candidates[slot]
                    if registry.cow(candidate) is not None
                ],
            }
        )
    folder = service.directory / CROPS_FOLDER / sighting.id
    return {
        "id": sighting.id,
        "chat": service.chat,
        "date": sighting.date,
        "camera": sighting.camera,
        "split": sighting.split,
        "role_certain": sighting.role_certain,
        "false": sighting.false,
        "split_wrong": sighting.split_wrong,
        "open": is_open(sighting),
        "photos": [name for name in _SIGHTING_PHOTOS if (folder / f"{name}.jpg").is_file()],
        "video": (folder / VIDEO_FILE).is_file(),
        "slots": slots,
    }


def list_sightings(query: dict[str, str]) -> dict[str, Any]:
    wanted = query.get("filter", "open")
    camera = query.get("camera") or None
    # The mounts of one cow, for the overview.
    cow = query.get("koe") or None
    since = (
        datetime.now() - timedelta(days=min(max(int(query["dagen"]), 1), 366))
        if query.get("dagen")
        else None
    )
    offset = max(int(query.get("offset", 0)), 0)
    limit = min(max(int(query.get("limit", 20)), 1), 100)
    found: list[tuple[CowService, Sighting]] = []
    cameras: set[str] = set()
    open_count = 0
    for service in _mount_services():
        with service.lock:
            sightings = list(service.sightings.values())
        for sighting in sightings:
            cameras.add(sighting.camera)
            if is_open(sighting):
                open_count += 1
            if camera and sighting.camera != camera:
                continue
            if cow and (cow not in sighting.cows or sighting.false):
                continue
            if since and sighting.when < since:
                continue
            if wanted == "open" and not is_open(sighting):
                continue
            if wanted == "herkend" and "auto" not in sighting.how:
                continue
            found.append((service, sighting))
    found.sort(key=lambda item: item[1].date, reverse=True)
    return {
        "items": [sighting_view(service, sighting) for service, sighting in found[offset : offset + limit]],
        "total": len(found),
        "open": open_count,
        "cameras": sorted(cameras),
    }


def change_sighting(sighting_id: str, body: dict[str, Any]) -> dict[str, Any]:
    service, sighting = _find(sighting_id)
    action = body.get("action")
    slot = body.get("slot")
    if action in ("geensprong", "welsprong"):
        message = service.set_mount(sighting.id, action == "welsprong")
    elif action in ("splitfout", "splitgoed"):
        try:
            message = service.set_split_wrong(sighting.id, action == "splitfout")
        except ValueError as error:
            raise ApiError(HTTPStatus.BAD_REQUEST, str(error)) from error
    elif action == "andersom":
        if not sighting.split:
            raise ApiError(HTTPStatus.BAD_REQUEST, "Bij deze sprong zijn de koeien niet gesplitst.")
        message = service.handle_callback(f"cow:{sighting.id}:-:s", quiet=True)
    else:
        if slot not in (0, 1):
            raise ApiError(HTTPStatus.BAD_REQUEST, "Kies koe A of B.")
        if action == "koe":
            try:
                message = service.set_cow(sighting.id, slot, str(body.get("value") or "").strip())
            except ValueError as error:
                raise ApiError(HTTPStatus.BAD_REQUEST, str(error)) from error
        elif action == "onbekend":
            message = service.handle_callback(f"cow:{sighting.id}:{slot}:u", quiet=True)
        elif action == "fotofout":
            if not sighting.split or sighting.split_wrong:
                raise ApiError(HTTPStatus.BAD_REQUEST, "Deze foto gaat toch niet in een koemap.")
            message = service.handle_callback(f"cow:{sighting.id}:{slot}:x", quiet=True)
        else:
            raise ApiError(HTTPStatus.BAD_REQUEST, f"Onbekende actie {action!r}")
    return {"message": message, "item": sighting_view(service, sighting)}


def list_cows(query: dict[str, str]) -> dict[str, Any]:
    services = _services()
    registry = services[0].registry
    show_archived = query.get("archief") == "1"
    with registry.lock:
        cows = [
            cow
            for cow in registry.data.cows.values()
            if show_archived or cow.archived is None
        ]
    items = []
    for cow in cows:
        number = registry.number_of(cow.life_number)
        items.append(
            {
                "life_number": cow.life_number,
                "number": number,
                "work_number": cow.work_number,
                "name": cow.name,
                "archived": cow.archived,
                "label": registry.label(cow.life_number),
                "photos": len(registry.photos(cow.life_number)),
                "photo": _latest_photo(services[0], cow.life_number),
            }
        )
    items.sort(
        key=lambda item: (
            item["archived"] is not None,
            int(item["number"]) if item["number"] else 10**9,
            item["life_number"],
        )
    )
    herd_files = sorted(
        {str(service.config.herd_file) for service in services if service.config.herd_file}
    )
    return {"items": items, "herd_file": herd_files[0] if herd_files else None}


def cow_photos(life_number: str) -> dict[str, Any]:
    service = _services()[0]
    life_number = _life_number(life_number)
    if service.registry.cow(life_number) is None:
        raise ApiError(HTTPStatus.NOT_FOUND, "Deze koe bestaat niet.")
    return {
        "life_number": life_number,
        "label": service.registry.label(life_number),
        "photos": [photo.name for photo in reversed(service.registry.photos(life_number))],
    }


def delete_cow_photo(life_number: str, name: str) -> dict[str, Any]:
    """Takes a wrong photo out of a cow's folder, so recognition no longer
    learns from it."""
    service = _services()[0]
    path = _cow_photo(service, life_number, name)
    path.unlink()
    (path.parent / EMBEDDINGS_FOLDER / f"{path.stem}.npy").unlink(missing_ok=True)
    return {"message": "Foto verwijderd"}


def change_cows(body: dict[str, Any]) -> dict[str, Any]:
    service = _services()[0]
    registry = service.registry
    action = body.get("action")
    name = (str(body.get("name") or "").strip()) or None
    work_number = (str(body.get("work_number") or "").strip()) or None
    try:
        if action == "weg":
            life_number = _life_number(str(body.get("life_number") or ""))
            if registry.cow(life_number) is None:
                raise ApiError(HTTPStatus.NOT_FOUND, "Deze koe bestaat niet.")
            label = registry.label(life_number)
            registry.archive(life_number)
            return {"message": f"{label} is gearchiveerd, haar sprongen blijven bewaard."}
        number = normalize_number(str(body.get("number") or ""))
        life_number = _life_number(str(body.get("life_number") or ""))
        if action == "toevoegen":
            try:
                registry.add(number, life_number, name, work_number=work_number)
            except NumberTaken as error:
                raise ApiError(
                    HTTPStatus.CONFLICT,
                    f"Nummer {error.number} hoort al bij {registry.label(error.holder)} · "
                    f"{error.holder}. Kies 'Nummer wisselen' als het naar deze koe gaat.",
                ) from error
            return {"message": f"Nummer {number} is nu {registry.label(life_number)} · {life_number}"}
        if action == "wissel":
            before = registry.cow_with_number(number)
            old_label = registry.label(before) if before else None
            old_left = bool(body.get("old_left"))
            old = registry.switch(number, life_number, old_left, name, work_number=work_number)
            message = f"Nummer {number} is nu {registry.label(life_number)} · {life_number}."
            if old and old_left:
                message += f" {old_label} is gearchiveerd."
            elif old:
                message += f" {old_label} heeft nu geen nummer."
            return {"message": message}
    except ValueError as error:
        raise ApiError(HTTPStatus.BAD_REQUEST, str(error)) from error
    raise ApiError(HTTPStatus.BAD_REQUEST, f"Onbekende actie {action!r}")


def overview(query: dict[str, str]) -> dict[str, Any]:
    days = min(max(int(query.get("dagen", 7)), 1), 366)
    end = datetime.now()
    start = end - timedelta(days=days)
    mounts = 0
    unknown = 0
    per_cow: dict[str, dict[str, Any]] = {}
    services = _mount_services()
    registry = services[0].registry
    for service in services:
        count, counts, labels, missing = service.overview_counts(start, end, every_chat=True)
        mounts += count
        unknown += missing
        for cow, (mounted, mounting) in counts.items():
            known = registry.cow(cow)
            item = per_cow.setdefault(
                cow,
                {
                    "cow": cow,
                    "label": labels[cow],
                    "work_number": known.work_number if known else None,
                    "mounted": 0,
                    "mounting": 0,
                },
            )
            item["mounted"] += mounted
            item["mounting"] += mounting
    items = sorted(
        per_cow.values(),
        key=lambda item: (-item["mounted"], -item["mounting"], item["label"]),
    )
    return {"days": days, "mounts": mounts, "unknown": unknown, "items": items}


def _cow_photo(service: CowService, life_number: str, name: str) -> Path:
    life_number = _life_number(life_number)
    if not _PHOTO_NAME.match(name) or service.registry.cow(life_number) is None:
        raise ApiError(HTTPStatus.NOT_FOUND, "Foto niet gevonden.")
    path = service.registry.folder(life_number) / name
    if not path.is_file():
        raise ApiError(HTTPStatus.NOT_FOUND, "Foto niet gevonden.")
    return path


def _sighting_photo(sighting_id: str, name: str) -> Path:
    service, sighting = _find(sighting_id)
    if name not in _SIGHTING_PHOTOS:
        raise ApiError(HTTPStatus.NOT_FOUND, "Foto niet gevonden.")
    path = service.directory / CROPS_FOLDER / sighting.id / f"{name}.jpg"
    if not path.is_file():
        raise ApiError(HTTPStatus.NOT_FOUND, "Foto niet gevonden.")
    return path


def _sighting_video(sighting_id: str) -> Path:
    service, sighting = _find(sighting_id)
    path = service.directory / CROPS_FOLDER / sighting.id / VIDEO_FILE
    if not path.is_file():
        raise ApiError(HTTPStatus.NOT_FOUND, "Geen video bij deze sprong.")
    return path


_RANGE = re.compile(r"^bytes=(\d*)-(\d*)$")


def review_folders(config: Config) -> list[Path]:
    """The folders of the disk exporters with review: true, where doubtful
    and too short mounts go. Resolved like the disk exporter does."""
    folders: list[Path] = []
    for detector in config.detectors:
        disk = detector.exporters.disk if detector.exporters else None
        for exporter in disk if isinstance(disk, list) else [disk] if disk else []:
            if exporter.review and exporter.directory:
                folder = (Path("detections") / exporter.directory).resolve()
                if folder not in folders:
                    folders.append(folder)
    return folders


_DOUBT_DATE = re.compile(r"^(\d{4}-\d{2}-\d{2})T(\d{2})-(\d{2})-(\d{2})")


def _review_session(folders: list[Path], index: int) -> ReviewSession:
    """A fresh session per request, so new doubtful mounts show up; the
    choices are kept in the folder itself, as review-feedback does. Good and
    bad go next to the folder (data/twijfel -> data/good, data/bad)."""
    if not folders:
        raise ApiError(
            HTTPStatus.NOT_FOUND,
            'Er is geen twijfel-map: zet bij "disk" een export met "review": true in config.json.',
        )
    if not 0 <= index < len(folders):
        raise ApiError(HTTPStatus.NOT_FOUND, "Deze twijfel-map bestaat niet.")
    folder = folders[index]
    folder.mkdir(parents=True, exist_ok=True)
    return ReviewSession(folder, folder.parent)


def list_doubts(folders: list[Path], query: dict[str, str]) -> dict[str, Any]:
    wanted = query.get("filter", "open")
    offset = max(int(query.get("offset", 0)), 0)
    limit = min(max(int(query.get("limit", 20)), 1), 100)
    items = []
    counts = {"open": 0, "good": 0, "bad": 0, "skip": 0}
    for index in range(len(folders)):
        session = _review_session(folders, index)
        for name, event in session.events.items():
            decision = session.decisions.get(name)
            counts[decision or "open"] += 1
            if wanted == "open" and decision is not None:
                continue
            try:
                metadata = json.loads((event.directory / "metadata.json").read_text())
            except (OSError, ValueError):
                metadata = {}
            match = _DOUBT_DATE.match(name)
            items.append(
                {
                    "folder": index,
                    "name": name,
                    "date": f"{match[1]}T{match[2]}:{match[3]}:{match[4]}" if match else None,
                    "camera": metadata.get("camera") or name[20:] or None,
                    "confidence": metadata.get("confidence"),
                    "duration": metadata.get("duration"),
                    "detections": metadata.get("detections"),
                    "decision": decision,
                    "video": (event.directory / "video.mp4").is_file(),
                }
            )
    items.sort(key=lambda item: item["name"], reverse=True)
    if not folders:
        _review_session(folders, 0)
    return {"items": items[offset : offset + limit], "total": len(items), "counts": counts}


def decide_doubt(folders: list[Path], index: int, name: str, body: dict[str, Any]) -> dict[str, Any]:
    session = _review_session(folders, index)
    if name not in session.events:
        raise ApiError(HTTPStatus.NOT_FOUND, "Dit twijfelgeval bestaat niet (meer).")
    decision = body.get("decision")
    if decision is None:
        session.clear(name)
        return {"message": "Keuze gewist", "decision": None}
    if decision not in ("good", "bad", "skip"):
        raise ApiError(HTTPStatus.BAD_REQUEST, "Kies good, bad of skip.")
    session.decide(name, decision)
    return {
        "message": {"good": "Goed: naar data/good", "bad": "Fout: naar data/bad", "skip": "Overgeslagen"}[decision],
        "decision": decision,
    }


def _doubt_file(folders: list[Path], index: int, name: str, resource: str) -> Path:
    session = _review_session(folders, index)
    if resource not in MEDIA_TYPES:
        raise ApiError(HTTPStatus.NOT_FOUND, "Niet gevonden.")
    try:
        return session.media_path(name, resource)
    except FileNotFoundError as error:
        raise ApiError(HTTPStatus.NOT_FOUND, "Niet gevonden.") from error


class Handler(BaseHTTPRequestHandler):
    server_version = "CowCatcher"

    def log_message(self, format: str, *args: Any) -> None:
        logger.debug("%s - %s", self.address_string(), format % args)

    def do_GET(self) -> None:
        self._handle("GET")

    def do_POST(self) -> None:
        self._handle("POST")

    def do_DELETE(self) -> None:
        self._handle("DELETE")

    def _handle(self, method: str) -> None:
        url = urlparse(self.path)
        parts = [unquote(part) for part in url.path.strip("/").split("/") if part]
        query = {key: values[-1] for key, values in parse_qs(url.query).items()}
        try:
            if parts[:1] != ["api"]:
                raise ApiError(HTTPStatus.NOT_FOUND, "Niet gevonden.")
            result = self._route(method, parts[1:], query)
            if isinstance(result, Path):
                self._send_file(result)
            else:
                self._send_json(HTTPStatus.OK, result)
        except ApiError as error:
            self._send_json(error.status, {"error": str(error)})
        except (KeyError, ValueError) as error:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(error)})
        except Exception:
            logger.exception("Cow API request failed: %s %s", method, url.path)
            self._send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "Er ging iets mis in de detector, zie het log."})

    def _route(self, method: str, parts: list[str], query: dict[str, str]) -> Any:
        match method, parts:
            case "GET", ["status"]:
                services = cow_services()
                return {
                    "cows": bool(services),
                    "chats": [service.chat for service in services],
                    "version": version(),
                }
            case "GET", ["sprongen"]:
                return list_sightings(query)
            case "GET", ["sprongen", sighting_id]:
                service, sighting = _find(sighting_id)
                return sighting_view(service, sighting)
            case "POST", ["sprongen", sighting_id]:
                return change_sighting(sighting_id, self._body())
            case "GET", ["sprongen", sighting_id, name] if name.endswith(".jpg"):
                return _sighting_photo(sighting_id, name.removesuffix(".jpg"))
            case "GET", ["sprongen", sighting_id, "video.mp4"]:
                return _sighting_video(sighting_id)
            case "GET", ["koeien"]:
                return list_cows(query)
            case "POST", ["koeien"]:
                return change_cows(self._body())
            case "GET", ["koeien", life_number, "fotos"]:
                return cow_photos(life_number)
            case "GET", ["koeien", life_number, "fotos", name]:
                return _cow_photo(_services()[0], life_number, name)
            case "DELETE", ["koeien", life_number, "fotos", name]:
                return delete_cow_photo(life_number, name)
            case "GET", ["overzicht"]:
                return overview(query)
            case "GET", ["twijfel"]:
                return list_doubts(self._folders, query)
            case "GET", ["twijfel", index, name, resource]:
                return _doubt_file(self._folders, int(index), name, resource)
            case "POST", ["twijfel", index, name]:
                return decide_doubt(self._folders, int(index), name, self._body())
        raise ApiError(HTTPStatus.NOT_FOUND, "Niet gevonden.")

    @property
    def _folders(self) -> list[Path]:
        return getattr(self.server, "review_folders", [])

    def _body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        if length > _MAX_BODY:
            raise ApiError(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "Te groot.")
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except ValueError as error:
            raise ApiError(HTTPStatus.BAD_REQUEST, "Geen geldige JSON.") from error
        if not isinstance(body, dict):
            raise ApiError(HTTPStatus.BAD_REQUEST, "Geen geldige JSON.")
        return body

    def _send_json(self, status: HTTPStatus, data: Any) -> None:
        payload = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(payload)

    def _send_file(self, path: Path) -> None:
        """Sends a photo or video. Browsers ask for a video in parts (Range),
        and Safari plays none without it."""
        payload = path.read_bytes()
        content_type = "video/mp4" if path.suffix == ".mp4" else "image/jpeg"
        wanted = _RANGE.match(self.headers.get("Range") or "")
        status = HTTPStatus.OK
        start, end = 0, len(payload) - 1
        if wanted and payload and (wanted[1] or wanted[2]):
            if wanted[1]:
                start = int(wanted[1])
                end = min(int(wanted[2]), end) if wanted[2] else end
            else:
                start = max(0, len(payload) - int(wanted[2]))
            if start > end:
                self.send_response(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
                self.send_header("Content-Range", f"bytes */{len(payload)}")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            status = HTTPStatus.PARTIAL_CONTENT
        body = payload[start : end + 1]
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Accept-Ranges", "bytes")
        if status == HTTPStatus.PARTIAL_CONTENT:
            self.send_header("Content-Range", f"bytes {start}-{end}/{len(payload)}")
        self.send_header("Cache-Control", "private, max-age=60")
        self.end_headers()
        self.wfile.write(body)


class CowApi:
    def __init__(self, config: ApiConfig, review: list[Path] | None = None):
        self.config = config
        self.review = review if review is not None else []
        self.server: ThreadingHTTPServer | None = None

    def start(self) -> None:
        try:
            self.server = ThreadingHTTPServer((self.config.host, self.config.port), Handler)
        except OSError as error:
            logger.warning(
                "Cow API could not listen on %s:%s (%s); the cow page of the web "
                "interface will not work",
                self.config.host,
                self.config.port,
                error,
            )
            return
        self.server.daemon_threads = True
        self.server.review_folders = self.review  # type: ignore[attr-defined]
        Thread(target=self.server.serve_forever, name="cow-api", daemon=True).start()
        logger.info("Cow API for the web interface on http://%s:%s", self.config.host, self.config.port)

    def stop(self) -> None:
        if self.server is not None:
            self.server.shutdown()
            self.server.server_close()
            self.server = None
