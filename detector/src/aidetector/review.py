import argparse
import json
import shutil
import webbrowser
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

DECISIONS = {"good", "bad", "skip"}
MEDIA_TYPES = {
    "clean.jpg": "image/jpeg",
    "best.jpg": "image/jpeg",
    "video.mp4": "video/mp4",
}


@dataclass(frozen=True)
class ReviewEvent:
    name: str
    directory: Path


class ReviewSession:
    def __init__(self, source: Path, data_root: Path):
        self.source = source.resolve()
        self.data_root = data_root.resolve()
        self.state_path = self.source / ".review-decisions.json"
        self.events = {
            path.name: ReviewEvent(path.name, path)
            for path in sorted(self.source.iterdir())
            if path.is_dir()
            and (path / "clean.jpg").is_file()
            and (path / "metadata.json").is_file()
        }
        self.decisions: dict[str, str] = {}
        self.history: list[str] = []
        self._load()

    def _load(self) -> None:
        if not self.state_path.exists():
            return
        state = json.loads(self.state_path.read_text())
        self.decisions = {
            name: decision
            for name, decision in state.get("decisions", {}).items()
            if name in self.events and decision in DECISIONS
        }
        self.history = [
            name for name in state.get("history", []) if name in self.decisions
        ]

    def status(self) -> dict[str, Any]:
        current = next(
            (
                event
                for name, event in self.events.items()
                if name not in self.decisions
            ),
            None,
        )
        counts = {decision: 0 for decision in DECISIONS}
        for decision in self.decisions.values():
            counts[decision] += 1
        return {
            "total": len(self.events),
            "reviewed": len(self.decisions),
            "counts": counts,
            "current": self._event_payload(current) if current else None,
            "canUndo": bool(self.history),
        }

    def decide(self, event_name: str, decision: str) -> dict[str, Any]:
        if event_name not in self.events:
            raise ValueError("Unknown event")
        if decision not in DECISIONS:
            raise ValueError("Invalid decision")

        self._remove_outputs(event_name)
        if decision in {"good", "bad"}:
            self._copy_outputs(self.events[event_name], decision)
        self.decisions[event_name] = decision
        self.history = [name for name in self.history if name != event_name]
        self.history.append(event_name)
        self._save()
        return self.status()

    def undo(self) -> dict[str, Any]:
        if not self.history:
            return self.status()
        event_name = self.history.pop()
        self.decisions.pop(event_name, None)
        self._remove_outputs(event_name)
        self._save()
        status = self.status()
        status["current"] = self._event_payload(self.events[event_name])
        return status

    def media_path(self, event_name: str, resource: str) -> Path:
        if event_name not in self.events or resource not in MEDIA_TYPES:
            raise FileNotFoundError
        media_path = self.events[event_name].directory / resource
        if not media_path.is_file():
            raise FileNotFoundError
        return media_path

    def _copy_outputs(self, event: ReviewEvent, decision: str) -> None:
        destination = self.data_root / decision
        destination.mkdir(parents=True, exist_ok=True)
        shutil.copy2(event.directory / "clean.jpg", destination / f"{event.name}.jpg")
        shutil.copy2(
            event.directory / "metadata.json", destination / f"{event.name}.json"
        )

    def _remove_outputs(self, event_name: str) -> None:
        for decision in ("good", "bad"):
            for suffix in (".jpg", ".json"):
                (self.data_root / decision / f"{event_name}{suffix}").unlink(
                    missing_ok=True
                )

    def _save(self) -> None:
        temporary_path = self.state_path.with_suffix(".tmp")
        temporary_path.write_text(
            json.dumps({"decisions": self.decisions, "history": self.history}, indent=2)
        )
        temporary_path.replace(self.state_path)

    @staticmethod
    def _event_payload(event: ReviewEvent) -> dict[str, str]:
        return {
            "name": event.name,
            "image": f"/media/{event.name}/clean.jpg",
            "plot": f"/media/{event.name}/best.jpg",
            "video": f"/media/{event.name}/video.mp4",
        }


HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>CowCatcher feedback review</title>
<style>
:root{color-scheme:dark;--bg:#111410;--panel:#1b2019;--line:#394035;--text:#f3f4ed;--muted:#abb3a4;--good:#3c8d57;--bad:#bd4747;--skip:#5b6257}
*{box-sizing:border-box}body{margin:0;background:radial-gradient(circle at top left,#283323 0,var(--bg) 42%);color:var(--text);font-family:ui-monospace,SFMono-Regular,Menlo,monospace;min-height:100vh}main{width:min(1400px,100%);margin:auto;padding:24px}.top{display:flex;justify-content:space-between;gap:24px;align-items:end;border-bottom:1px solid var(--line);padding-bottom:16px}h1{font:700 clamp(24px,4vw,42px) Georgia,serif;margin:0;letter-spacing:0}.meta{color:var(--muted);text-align:right}.media{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin:18px 0}.media>*{display:block;width:100%;aspect-ratio:16/9;object-fit:contain;background:#000;border:1px solid var(--line)}.actions{display:grid;grid-template-columns:1fr 1fr 1fr auto;gap:10px}button{border:1px solid transparent;border-radius:6px;color:white;font:700 16px inherit;padding:15px 20px;cursor:pointer}button:disabled{opacity:.4;cursor:not-allowed}.good{background:var(--good)}.bad{background:var(--bad)}.skip{background:var(--skip)}.undo{background:transparent;border-color:var(--line)}.done{display:none;padding:15vh 0;text-align:center}.done h2{font:700 36px Georgia,serif;letter-spacing:0}@media(max-width:760px){main{padding:14px}.top{align-items:start;flex-direction:column}.meta{text-align:left}.media{grid-template-columns:1fr}.actions{grid-template-columns:1fr 1fr}.undo{grid-column:span 2}}
</style>
</head>
<body><main>
<header class="top"><div><h1>Feedback review</h1><div id="event"></div></div><div class="meta"><div id="progress"></div><div id="counts"></div></div></header>
<section id="review"><div class="media"><video id="video" controls preload="metadata"></video><img id="image" alt="Clean detection frame"></div><div class="actions"><button class="good" data-decision="good" title="Good (G)">Good</button><button class="bad" data-decision="bad" title="Bad (B)">Bad</button><button class="skip" data-decision="skip" title="Skip (S)">Skip</button><button class="undo" id="undo" title="Undo last choice">Undo</button></div></section>
<section class="done" id="done"><h2>Review complete</h2><p id="summary"></p><button class="undo" id="doneUndo">Undo last choice</button></section>
</main><script>
let state=null;let busy=false;
async function request(path,body){const response=await fetch(path,{method:body?'POST':'GET',headers:{'Content-Type':'application/json'},body:body?JSON.stringify(body):undefined});if(!response.ok)throw new Error(await response.text());return response.json()}
function render(next){state=next;const current=state.current;document.querySelector('#progress').textContent=`${state.reviewed} / ${state.total} reviewed`;document.querySelector('#counts').textContent=`Good ${state.counts.good} · Bad ${state.counts.bad} · Skip ${state.counts.skip}`;document.querySelectorAll('#undo,#doneUndo').forEach(button=>button.disabled=!state.canUndo);document.querySelector('#review').style.display=current?'block':'none';document.querySelector('#done').style.display=current?'none':'block';if(!current){document.querySelector('#summary').textContent=`${state.counts.good} good, ${state.counts.bad} bad, ${state.counts.skip} skipped`;return}document.querySelector('#event').textContent=current.name;const video=document.querySelector('#video');video.pause();video.src=current.video;video.poster=current.plot;video.load();document.querySelector('#image').src=current.image}
async function decide(decision){if(busy||!state.current)return;busy=true;try{render(await request('/api/decision',{event:state.current.name,decision}))}finally{busy=false}}
async function undo(){if(busy)return;busy=true;try{render(await request('/api/undo',{}))}finally{busy=false}}
document.querySelectorAll('[data-decision]').forEach(button=>button.onclick=()=>decide(button.dataset.decision));document.querySelectorAll('#undo,#doneUndo').forEach(button=>button.onclick=undo);document.addEventListener('keydown',event=>{if(event.target.matches('input,textarea'))return;const key=event.key.toLowerCase();if(key==='g')decide('good');if(key==='b')decide('bad');if(key==='s')decide('skip')});request('/api/state').then(render);
</script></body></html>"""


def create_handler(session: ReviewSession):
    class ReviewHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            path = urlparse(self.path).path
            if path == "/":
                self._send(HTML.encode(), "text/html; charset=utf-8")
                return
            if path == "/api/state":
                self._json(session.status())
                return
            if path.startswith("/media/"):
                try:
                    _, _, event_name, resource = path.split("/", 3)
                    media_path = session.media_path(unquote(event_name), resource)
                    self._send(media_path.read_bytes(), MEDIA_TYPES[resource])
                except (FileNotFoundError, ValueError):
                    self.send_error(HTTPStatus.NOT_FOUND)
                return
            self.send_error(HTTPStatus.NOT_FOUND)

        def do_POST(self) -> None:
            try:
                length = int(self.headers.get("Content-Length", "0"))
                body = json.loads(self.rfile.read(length) or b"{}")
                path = urlparse(self.path).path
                if path == "/api/decision":
                    self._json(session.decide(body["event"], body["decision"]))
                    return
                if path == "/api/undo":
                    self._json(session.undo())
                    return
                self.send_error(HTTPStatus.NOT_FOUND)
            except (KeyError, ValueError, json.JSONDecodeError) as error:
                self.send_error(HTTPStatus.BAD_REQUEST, str(error))

        def log_message(self, format: str, *args: Any) -> None:
            return

        def _json(self, value: Any) -> None:
            self._send(json.dumps(value).encode(), "application/json")

        def _send(self, contents: bytes, content_type: str) -> None:
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(contents)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(contents)

    return ReviewHandler


def main() -> None:
    parser = argparse.ArgumentParser(description="Review imported CowCatcher events.")
    parser.add_argument("--source", type=Path, default=Path("import"))
    parser.add_argument("--data-root", type=Path, default=Path("."))
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()

    session = ReviewSession(args.source, args.data_root)
    if not session.events:
        raise SystemExit(f"No reviewable events found in {args.source}")
    server = ThreadingHTTPServer((args.host, args.port), create_handler(session))
    browser_host = "localhost" if args.host in {"0.0.0.0", "::"} else args.host
    url = f"http://{browser_host}:{args.port}"
    print(f"Reviewing {len(session.events)} events at {url}")
    if not args.no_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
