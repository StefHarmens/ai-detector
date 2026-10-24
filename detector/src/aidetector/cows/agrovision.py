"""The AgroVision connector: the farmer fills in the login in the web
interface and the herd list (Dieranalyse, "exporteren naar Excel") is fetched
every night in the background. The list lands in the folder of the cows and is
read like any `herd_file`, so the farmer only keeps the herd in AgroVision.

The browser part runs in a separate Python with Playwright, installed on first
use next to the cows (`.agrovision/`): a browser does not belong in the
detector itself. The password is only kept in `.agrovision/connector.json`,
readable by this user alone, and is never sent to the web interface."""

import io
import json
import logging
import os
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from threading import Event, Lock, Thread
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from aidetector.cows import fertility
from aidetector.cows.importer import parse_herd, read_table

logger = logging.getLogger(__name__)

FOLDER = ".agrovision"
STATE_FILE = "connector.json"
# What the connector fetched; read as the herd list when none is configured.
HERD_FILE = "koeienlijst.xlsx"
# The last download that was refused, to look at what AgroVision gave.
KEPT_DOWNLOAD = "laatste-download.xml"
EXPORT_TEXT = "exporteren naar excel"
DEFAULT_RUN_AT = "03:30"
INSTALL_SECONDS = 20 * 60
RUN_SECONDS = 180
# A list with fewer animals than this share of the current one looks like a
# partial export: it is not taken.
MIN_SHARE = 0.8

PYTHONS = ("python3", "/opt/homebrew/bin/python3", "/usr/local/bin/python3", "/usr/bin/python3")

# Runs in the Playwright Python. Reads {"url", "username", "password",
# "profile", "out", "timeout", "export_text", "screenshot"} from stdin and
# prints one JSON line: {"ok": true} or {"ok": false, "reason": "..."}.
RUNNER = r'''
import json, sys
from playwright.sync_api import Error, TimeoutError, sync_playwright

job = json.load(sys.stdin)


def finish(ok, reason=None, extras=None):
    print(json.dumps({"ok": ok, "reason": reason, "extras": extras or {}}), flush=True)
    sys.exit(0 if ok else 1)


with sync_playwright() as playwright:
    # A fresh browser each run: a kept profile brought back an old session,
    # and the portal then only said that it had expired.
    browser = playwright.chromium.launch(headless=True)
    context = browser.new_context(accept_downloads=True)
    page = context.pages[0] if context.pages else context.new_page()
    page.set_default_timeout(job["timeout"] * 1000)
    try:
        try:
            page.goto(job["url"], wait_until="networkidle")
        except (TimeoutError, Error) as error:
            finish(False, "AgroVision is niet bereikbaar of reageert niet: " + str(error).splitlines()[0])
        def dismiss_expired():
            # "Uw sessie is verlopen": Ok sends the portal to the login.
            for _ in range(3):
                notice = page.get_by_text("sessie is verlopen", exact=False)
                if not notice.count():
                    return
                page.get_by_role("button", name="Ok", exact=True).first.click()
                page.wait_for_timeout(1500)

        # The portal loads for a while before it shows the login or the report.
        for _ in range(40):
            dismiss_expired()
            if page.locator("input[type=password]:visible, [data-bind*=printExcel]").count():
                break
            page.wait_for_timeout(500)
        password = page.locator("input[type=password]:visible")
        if password.count():
            fields = page.locator("input:visible:not([type=password]):not([type=hidden]):not([type=checkbox]):not([type=submit])")
            if fields.count() == 0:
                finish(False, "Op de inlogpagina staat geen veld voor de gebruikersnaam")
            fields.first.fill(job["username"])
            password.first.fill(job["password"])
            password.first.press("Enter")
            try:
                page.wait_for_load_state("networkidle")
            except TimeoutError:
                pass
            page.wait_for_timeout(2000)
            if page.locator("input[type=password]:visible").count():
                finish(False, "Inloggen mislukt: gebruikersnaam of wachtwoord klopt niet, of AgroVision vraagt een extra verificatiestap")
            if not job["url"].partition("#")[2] and page.url.rstrip("/") != job["url"].rstrip("/"):
                page.goto(job["url"], wait_until="networkidle")

        # After logging in AgroVision first shows "My applications": the report
        # is in AgroVision Cows, which may open in a new tab.
        def cows_tile():
            for tile in (
                page.get_by_alt_text("AgroVision Cows"),
                page.get_by_role("link", name="AgroVision Cows"),
                page.get_by_text("AgroVision Cows", exact=False),
            ):
                if tile.count():
                    return tile.first
            return None

        for _ in range(20):
            if page.locator("[data-bind*=printExcel]").count():
                break
            tile = cows_tile()
            if tile is not None and not page.locator("input[type=password]:visible").count():
                try:
                    with context.expect_page(timeout=10000) as opened:
                        tile.click()
                    page = opened.value
                    page.set_default_timeout(job["timeout"] * 1000)
                except TimeoutError:
                    # Same tab.
                    pass
                try:
                    page.wait_for_load_state("networkidle")
                except TimeoutError:
                    pass
                dismiss_expired()
                break
            page.wait_for_timeout(500)

        def locators(frame, text):
            # The button may be a link, an icon with a tooltip or a button.
            if "excel" in text.lower():
                # AgroVision's export is an icon without text; its tooltip only
                # exists while hovering.
                yield frame.locator("[data-bind*='printExcel'], a:has(.fa-file-excel-o), button:has(.fa-file-excel-o)")
            yield frame.get_by_text(text, exact=False)
            yield frame.get_by_role("button", name=text)
            yield frame.get_by_role("link", name=text)
            yield frame.locator("[title*='" + text + "' i], [aria-label*='" + text + "' i], [alt*='" + text + "' i]")

        def find(text, wait=10):
            # AgroVision shows its reports in frames: look in all of them.
            for _ in range(wait * 2):
                for frame in page.frames:
                    for found in locators(frame, text):
                        if found.count():
                            return found.first
                page.wait_for_timeout(500)
            return None

        def around():
            # What the page offers with "excel" or "export" in it, for the reason.
            seen = []
            for frame in page.frames:
                try:
                    texts = frame.evaluate(
                        """() => [...document.querySelectorAll('a,button,[role=button],[title],[aria-label],img[alt]')]
                            .map(e => (e.innerText || e.title || e.getAttribute('aria-label') || e.alt || '').trim())
                            .filter(t => /excel|export|xls|download/i.test(t))"""
                    )
                    seen += texts
                except Exception:
                    pass
            title = ""
            try:
                title = page.title()
            except Exception:
                pass
            return "Pagina: '" + title + "' (" + page.url.split("?")[0] + page.url.partition("#")[1] + page.url.partition("#")[2] + "). Gevonden met excel/export: " + (", ".join(sorted(set(seen))[:8]) or "niets") + "."

        from urllib.parse import unquote
        import os, re

        def wait_idle():
            try:
                page.wait_for_load_state("networkidle")
            except TimeoutError:
                pass

        def set_route(fragment):
            # The portal opens its own start report after logging in, over any
            # route in the address: set ours once it has settled, and see it hold.
            for attempt in range(5):
                page.wait_for_timeout(3000)
                if unquote(page.url.partition("#")[2]) == unquote(fragment):
                    break
                page.evaluate("fragment => { location.hash = fragment }", fragment)
                page.wait_for_timeout(2000)
            wait_idle()

        def click_steps(steps):
            for step in steps:
                item = find(step, 20)
                if item is None:
                    return "In het menu van AgroVision staat '" + step + "' niet: klopt de weg naar het rapport?"
                item.click()
                wait_idle()
            return None

        def export_report(out):
            # A report can take a while to load, and exporting before its rows
            # are there gives a file with only the column names: look again a
            # few times. Returns None, or why it did not work.
            page.wait_for_timeout(5000)
            for attempt in range(4):
                button = find(job["export_text"], 40 if attempt == 0 else 10)
                if button is None:
                    return "De knop 'exporteren naar Excel' staat niet op de pagina: klopt de weg naar het rapport? " + around()
                try:
                    with page.expect_download() as info:
                        button.click()
                except (TimeoutError, Error) as error:
                    return "Na het klikken op exporteren kwam er geen bestand: " + str(error).splitlines()[0]
                info.value.save_as(out)
                text = open(out, "rb").read().decode("utf-8", errors="replace")
                rows = re.findall(r"<Row[^>]*>(.*?)</Row>", text, re.S)
                # Not a spreadsheet in XML: the importer will judge it.
                if not rows or sum("<Data" in row for row in rows) >= 2:
                    return None
                os.remove(out)
                page.wait_for_timeout(10000)
            return "De lijst in AgroVision bleef leeg: er staan alleen kolomnamen in de export. Klopt het rapport, of staat er een filter aan?"

        fragment = job["url"].partition("#")[2]
        if fragment:
            # Cows opens on Dierregistratie: the report route only works once
            # the Dieranalyse tab is open.
            if "analysis" in fragment.lower() and not page.locator("[data-bind*=printExcel]").count():
                tab = find("Dieranalyse", 20)
                if tab is None:
                    finish(False, "De tab 'Dieranalyse' staat niet bovenin AgroVision Cows. " + around())
                tab.click()
                wait_idle()
            set_route(fragment)
        reason = click_steps(job.get("steps", []))
        if reason is None:
            reason = export_report(job["out"])
        if reason is not None:
            finish(False, reason)

        # The other reports (fertility) from the same session; one that does
        # not work does not take the herd list with it.
        results = {}
        for extra in job.get("extras", []):
            try:
                if extra.get("fragment"):
                    set_route(extra["fragment"])
                reason = click_steps(extra.get("steps", []))
                if reason is None:
                    reason = export_report(extra["out"])
            except Exception as error:
                reason = "Onverwachte fout in de browser: " + str(error).splitlines()[0]
            results[extra["key"]] = {"ok": reason is None, "reason": reason}
        finish(True, None, results)
    except SystemExit:
        raise
    except Exception as error:
        finish(False, "Onverwachte fout in de browser: " + str(error).splitlines()[0])
    finally:
        try:
            if not (job.get("out") and __import__("os").path.exists(job["out"])):
                page.screenshot(path=job["screenshot"])
        except Exception:
            pass
        context.close()
        browser.close()
'''


class ConnectorError(Exception):
    """A failure with a reason the farmer can read."""


@dataclass
class Result:
    animals: int
    problems: int


def _without_session(url: str) -> str:
    """A pasted address holds a SessionID that is valid for one login only."""
    parts = urlsplit(url)
    query = urlencode(
        [(key, value) for key, value in parse_qsl(parts.query) if key.lower() not in ("sessionid", "session")]
    )
    return urlunsplit(parts._replace(query=query))


SPREADSHEET_NS = "urn:schemas-microsoft-com:office:spreadsheet"


def _spreadsheet_rows(data: bytes) -> list[list[Any]]:
    """Excel 2003 XML ("XML-werkblad"): AgroVision gives this with an .xml name."""
    from xml.etree import ElementTree

    root = ElementTree.fromstring(data)
    ns = {"s": SPREADSHEET_NS}
    index_key = f"{{{SPREADSHEET_NS}}}Index"
    sheet = root.find(".//s:Worksheet", ns)
    if sheet is None:
        raise ValueError("geen werkblad in het XML-bestand")
    rows: list[list[Any]] = []
    for row in sheet.iterfind(".//s:Row", ns):
        values: list[Any] = []
        for cell in row.iterfind("s:Cell", ns):
            if index_key in cell.attrib:
                values.extend([None] * (int(cell.attrib[index_key]) - 1 - len(values)))
            node = cell.find("s:Data", ns)
            # AgroVision puts part of a life number in <b>: take all text in the cell.
            text = None if node is None else "".join(node.itertext()).strip()
            kind = node.attrib.get(f"{{{SPREADSHEET_NS}}}Type") if node is not None else None
            if text and kind == "Number":
                try:
                    number = float(text)
                    text = int(number) if number.is_integer() else number
                except ValueError:
                    pass
            values.append(text or None)
        rows.append(values)
    return rows


def _html_rows(data: bytes) -> list[list[Any]]:
    """An HTML table with an Excel name, as some programs export."""
    from html.parser import HTMLParser

    class Table(HTMLParser):
        def __init__(self) -> None:
            super().__init__()
            self.rows: list[list[Any]] = []
            self.cell: list[str] | None = None

        def handle_starttag(self, tag: str, attrs: Any) -> None:
            if tag == "tr":
                self.rows.append([])
            elif tag in ("td", "th") and self.rows:
                self.cell = []

        def handle_data(self, data: str) -> None:
            if self.cell is not None:
                self.cell.append(data)

        def handle_endtag(self, tag: str) -> None:
            if tag in ("td", "th") and self.cell is not None and self.rows:
                self.rows[-1].append("".join(self.cell).strip() or None)
                self.cell = None

    table = Table()
    table.feed(data.decode("utf-8", errors="replace"))
    return table.rows


def _download_rows(data: bytes) -> list[list[Any]]:
    """The rows of a download that is not a real .xlsx: Excel 2003 XML or an
    HTML table, whatever AgroVision called it."""
    head = data[:2000].lstrip(b"\xef\xbb\xbf \r\n\t")
    if SPREADSHEET_NS.encode() in data[:4000]:
        return _spreadsheet_rows(data)
    if b"<table" in data[:200000].lower():
        return _html_rows(data)
    shown = head[:60].decode("utf-8", errors="replace")
    raise ValueError(f"onbekend bestandstype, begint met {shown!r}")


def read_rows(source: Path) -> list[list[Any]]:
    """The first sheet of a download, as rows."""
    data = source.read_bytes()
    if data[:2] == b"PK":
        from openpyxl import load_workbook

        # From memory: openpyxl judges a file by its name, and this one says .xml.
        workbook = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        try:
            return [list(row) for row in workbook.active.iter_rows(values_only=True)]
        finally:
            workbook.close()
    return _download_rows(data)


def to_xlsx(source: Path, target: Path) -> None:
    """Makes the download a real .xlsx, whatever AgroVision called it."""
    from openpyxl import Workbook

    data = source.read_bytes()
    if data[:2] == b"PK":
        shutil.copyfile(source, target)
        return
    try:
        rows = _download_rows(data)
    except Exception as error:
        raise ConnectorError(f"Wat AgroVision gaf is geen koeienlijst ({error})") from error
    workbook = Workbook()
    for row in rows:
        workbook.active.append(row)
    workbook.save(target)


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


class AgrovisionConnector:
    def __init__(self, directory: Path):
        self.directory = directory
        self.folder = directory / FOLDER
        self.lock = Lock()
        self.stop_event = Event()
        self.wake = Event()
        self.running = False
        self.thread: Thread | None = None
        self.notify: Callable[[str], None] | None = None
        # Why a fertility report could not be fetched, from the last browser run.
        self.extra_reasons: dict[str, str | None] = {}
        # Replaced in tests.
        self.fetch: Callable[[dict[str, Any], Path], None] = self._fetch_in_browser

    # State

    @property
    def screenshot(self) -> Path:
        """What the browser saw when the last run failed."""
        return self.folder / "laatste-fout.png"

    @property
    def herd_file(self) -> Path:
        return self.directory / HERD_FILE

    def _read(self) -> dict[str, Any]:
        try:
            return json.loads((self.folder / STATE_FILE).read_text())
        except (FileNotFoundError, ValueError):
            return {}

    def _write(self, state: dict[str, Any]) -> None:
        self.folder.mkdir(parents=True, exist_ok=True)
        path = self.folder / STATE_FILE
        temp = path.with_suffix(".tmp")
        # Only this user may read the password.
        descriptor = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, "w") as file:
            json.dump(state, file)
        os.replace(temp, path)

    @property
    def configured(self) -> bool:
        state = self._read()
        return bool(state.get("url") and state.get("username") and state.get("password"))

    def view(self) -> dict[str, Any]:
        """The state for the web interface, without the password."""
        state = self._read()
        last = state.get("last") or {}
        return {
            "configured": self.configured,
            "url": state.get("url", ""),
            "username": state.get("username", ""),
            "has_password": bool(state.get("password")),
            "run_at": state.get("run_at", DEFAULT_RUN_AT),
            "path": " > ".join(state.get("steps", [])),
            "status": "running" if self.running else last.get("status", "never"),
            "step": state.get("step"),
            "screenshot": self.screenshot.is_file() and last.get("status") == "failed",
            "reason": last.get("reason"),
            "attempted": last.get("at"),
            "succeeded": state.get("succeeded"),
            "animals": state.get("animals"),
            "reports": {
                group: {
                    "value": (state.get("reports") or {}).get(group, ""),
                    "label": fertility.LABELS[group],
                    **((state.get("fertility") or {}).get(group) or {"status": "never"}),
                }
                for group in fertility.GROUPS
            },
        }

    def save(self, body: dict[str, Any]) -> None:
        url = str(body.get("url", "")).strip()
        username = str(body.get("username", "")).strip()
        password = str(body.get("password") or "")
        run_at = str(body.get("run_at") or DEFAULT_RUN_AT).strip()
        # "Dieranalyse > Koeienlijst": what to click before the export button.
        steps = [part.strip() for part in str(body.get("path") or "").split(">") if part.strip()]
        reports = {
            group: _without_session(str(body.get(f"report_{group}") or "").strip())
            for group in fertility.GROUPS
        }
        for group, value in reports.items():
            if value.lower().startswith(("http://", "https://")) and not value.partition("#")[2]:
                raise ValueError(
                    f"Het adres van {fertility.LABELS[group]} moet een route na de # hebben, of vul de weg in met >."
                )
        state = self._read()
        # Left empty, the password stays as it was.
        password = password or state.get("password", "")
        url = _without_session(url)
        if not url.lower().startswith(("https://", "http://")):
            raise ValueError("Vul het adres van het rapport in, beginnend met https://")
        if not username or not password:
            raise ValueError("Vul gebruikersnaam en wachtwoord in.")
        try:
            hour, minute = (int(part) for part in run_at.split(":"))
            if not (0 <= hour < 24 and 0 <= minute < 60):
                raise ValueError
        except ValueError:
            raise ValueError("Geef de tijd als uren:minuten, bijvoorbeeld 03:30.") from None
        state.update(
            url=url, username=username, password=password, run_at=run_at, steps=steps, reports=reports
        )
        self._write(state)

    def delete(self) -> None:
        shutil.rmtree(self.folder, ignore_errors=True)

    # Running

    def start(self, notify: Callable[[str], None] | None = None) -> None:
        """Starts the nightly schedule; the first chat that asks sets how the
        farmer is told about a failure."""
        with self.lock:
            if notify is not None and self.notify is None:
                self.notify = notify
            if self.thread is not None:
                return
            self.thread = Thread(target=self._schedule, name="agrovision", daemon=True)
            self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        self.wake.set()

    def sync_now(self) -> bool:
        """Starts a run in the background; False when one is already going."""
        if not self.configured:
            raise ValueError("Vul eerst de inloggegevens in.")
        with self.lock:
            if self.running:
                return False
            self.running = True
        Thread(target=self._run, name="agrovision-run", daemon=True).start()
        return True

    def _schedule(self) -> None:
        last_day = ""
        while not self.stop_event.is_set():
            self.wake.wait(30)
            self.wake.clear()
            state = self._read()
            now = datetime.now()
            today = now.strftime("%Y-%m-%d")
            if not self.configured or self.running or last_day == today:
                continue
            if now.strftime("%H:%M") >= state.get("run_at", DEFAULT_RUN_AT):
                # Also after the Mac was off at run time, as soon as it is on.
                last_attempt = (state.get("last") or {}).get("at", "")
                if last_attempt[:10] == today:
                    last_day = today
                    continue
                last_day = today
                try:
                    self.sync_now()
                except ValueError:
                    pass

    def _set(self, **changes: Any) -> None:
        with self.lock:
            state = self._read()
            state.update(changes)
            if state:
                self._write(state)

    def _run(self) -> None:
        before = (self._read().get("last") or {}).get("status")
        try:
            result = self.run_once()
        except ConnectorError as error:
            reason = str(error)
            logger.warning("AgroVision sync failed: %s", reason)
            self._set(last={"status": "failed", "at": _now(), "reason": reason}, step=None)
            # Once per streak of failures, not every night.
            if before != "failed" and self.notify is not None:
                self.notify(f"⚠️ De koeienlijst uit AgroVision is niet ververst: {reason}")
        except Exception as error:
            logger.exception("AgroVision sync crashed")
            reason = f"Onverwachte fout: {error}"
            self._set(last={"status": "failed", "at": _now(), "reason": reason}, step=None)
            if before != "failed" and self.notify is not None:
                self.notify(f"⚠️ De koeienlijst uit AgroVision is niet ververst: {reason}")
        else:
            logger.info("AgroVision sync: %s animals", result.animals)
            self._set(
                last={"status": "ok", "at": _now(), "reason": None},
                succeeded=_now(),
                animals=result.animals,
                step=None,
            )
            if before == "failed" and self.notify is not None:
                self.notify("✅ De koeienlijst uit AgroVision wordt weer ververst.")
        finally:
            with self.lock:
                self.running = False

    def run_once(self) -> Result:
        state = self._read()
        if not (state.get("url") and state.get("username") and state.get("password")):
            raise ConnectorError("De inloggegevens zijn niet ingevuld.")
        self.folder.mkdir(parents=True, exist_ok=True)
        out = self.folder / "download.xlsx"
        out.unlink(missing_ok=True)
        self.screenshot.unlink(missing_ok=True)
        # The list always goes to the same file, replacing the last one; the
        # download itself is removed whatever happens.
        try:
            self.fetch(state, out)
            if not out.is_file():
                raise ConnectorError("Er is geen bestand gedownload.")
            converted = out.with_name("lijst.xlsx")
            to_xlsx(out, converted)
            result = self._check(converted)
            staged = self.herd_file.with_name(self.herd_file.name + ".new")
            self.herd_file.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(converted, staged)
            os.replace(staged, self.herd_file)
        except ConnectorError:
            # Kept for the farmer to look at: one file, replaced by the next run.
            if out.is_file():
                os.replace(out, self.folder / KEPT_DOWNLOAD)
            # Without a good herd list the fertility reports are not read.
            for extra in self._extras(state):
                Path(extra["out"]).unlink(missing_ok=True)
            raise
        finally:
            out.unlink(missing_ok=True)
            out.with_name("lijst.xlsx").unlink(missing_ok=True)
        (self.folder / KEPT_DOWNLOAD).unlink(missing_ok=True)
        self._fertility(state)
        return result

    def _fertility(self, state: dict[str, Any]) -> None:
        """Reads the fertility reports fetched with the herd list. A report
        that fails is reported on its own and never fails the herd list."""
        for extra in self._extras(state):
            group, file = extra["key"], Path(extra["out"])
            try:
                try:
                    if not file.is_file():
                        raise ConnectorError(
                            self.extra_reasons.get(group) or "Er is geen bestand gedownload."
                        )
                    try:
                        animals = fertility.parse(read_rows(file), group)
                    except ValueError as error:
                        raise ConnectorError(f"{fertility.LABELS[group]}: {error}") from error
                    except Exception as error:
                        raise ConnectorError(
                            f"{fertility.LABELS[group]} is geen leesbaar rapport ({error})"
                        ) from error
                    if not animals:
                        raise ConnectorError(f"{fertility.LABELS[group]} bevat geen dieren.")
                    fertility.save(self.directory, group, animals, _now())
                except ConnectorError:
                    if file.is_file():
                        os.replace(file, self.folder / f"laatste-download-{group}.xml")
                    raise
            except ConnectorError as error:
                logger.warning("AgroVision %s failed: %s", fertility.LABELS[group], error)
                outcome = {"status": "failed", "at": _now(), "reason": str(error)}
            else:
                (self.folder / f"laatste-download-{group}.xml").unlink(missing_ok=True)
                outcome = {"status": "ok", "at": _now(), "reason": None, "animals": len(animals)}
            finally:
                file.unlink(missing_ok=True)
            with self.lock:
                latest = self._read()
                latest.setdefault("fertility", {})[group] = outcome
                self._write(latest)

    def _check(self, path: Path) -> Result:
        """The download must be a herd list, and a whole one."""
        try:
            parsed = parse_herd(read_table(path))
        except Exception as error:
            raise ConnectorError(f"Wat AgroVision gaf is geen koeienlijst ({error})") from error
        if not parsed.animals:
            table = read_table(path)
            filled = [row for row in table if any(cell.strip() for cell in row)]
            names = ", ".join(cell for cell in (filled[0] if filled else []) if cell and not any(c.isdigit() for c in cell))
            raise ConnectorError(
                "Wat AgroVision gaf is geen koeienlijst: geen dieren met Diernr of Levensnummer gevonden "
                f"({len(filled)} gevulde rijen; kolommen: {names[:150] or 'niet herkend'})."
            )
        if self.herd_file.is_file():
            try:
                before = len(parse_herd(read_table(self.herd_file)).animals)
            except Exception:
                before = 0
            if before and len(parsed.animals) < before * MIN_SHARE:
                raise ConnectorError(
                    f"De nieuwe lijst heeft {len(parsed.animals)} dieren tegen {before} nu en lijkt onvolledig; de oude lijst blijft staan."
                )
        return Result(animals=len(parsed.animals), problems=len(parsed.problems))

    # The browser

    def _python(self) -> Path:
        return self.folder / "runtime" / "bin" / "python"

    def _install_runtime(self) -> None:
        if self._python().exists() and (self.folder / "runtime" / ".ready").exists():
            return
        self._set(step="Browser voor AgroVision installeren (eenmalig, enkele minuten)")
        base = next((found for name in PYTHONS if (found := shutil.which(name))), None)
        if base is None:
            raise ConnectorError(
                "Python 3 is niet gevonden op deze computer; het is nodig voor de browser van de koppeling."
            )
        runtime = self.folder / "runtime"
        shutil.rmtree(runtime, ignore_errors=True)
        for command in (
            [base, "-m", "venv", str(runtime)],
            [str(self._python()), "-m", "pip", "install", "--quiet", "playwright"],
            [str(self._python()), "-m", "playwright", "install", "chromium"],
        ):
            try:
                done = subprocess.run(command, capture_output=True, text=True, timeout=INSTALL_SECONDS)
            except subprocess.TimeoutExpired:
                raise ConnectorError("Het installeren van de browser duurde te lang.") from None
            if done.returncode != 0:
                tail = (done.stderr or done.stdout).strip().splitlines()[-1:] or ["onbekende fout"]
                raise ConnectorError(f"Het installeren van de browser mislukte: {tail[0]}")
        (runtime / ".ready").write_text("")

    def _extras(self, state: dict[str, Any]) -> list[dict[str, Any]]:
        """The fertility reports to fetch after the herd list: one with a
        #route in its address, or a menu path like "Mijn rapporten > Naam"."""
        extras = []
        for group in fertility.GROUPS:
            value = ((state.get("reports") or {}).get(group) or "").strip()
            if not value:
                continue
            fragment = value.partition("#")[2] if "#" in value else ""
            steps = [] if fragment or value.lower().startswith("http") else [
                part.strip() for part in value.split(">") if part.strip()
            ]
            extras.append(
                {"key": group, "fragment": fragment, "steps": steps, "out": str(self._extra_file(group))}
            )
        return extras

    def _extra_file(self, group: str) -> Path:
        return self.folder / f"download-{group}.xml"

    def _fetch_in_browser(self, state: dict[str, Any], out: Path) -> None:
        self._install_runtime()
        self._set(step="Lijst ophalen uit AgroVision")
        job = {
            "url": state["url"],
            "username": state["username"],
            "password": state["password"],
            "profile": str(self.folder / "profile"),
            "out": str(out),
            "timeout": 60,
            "export_text": EXPORT_TEXT,
            "steps": state.get("steps", []),
            "extras": self._extras(state),
            "screenshot": str(self.screenshot),
        }
        self.extra_reasons = {}
        try:
            # The login goes in over stdin: not in the process list.
            done = subprocess.run(
                [str(self._python()), "-c", RUNNER],
                input=json.dumps(job),
                capture_output=True,
                text=True,
                timeout=RUN_SECONDS,
            )
        except subprocess.TimeoutExpired:
            raise ConnectorError("AgroVision reageerde niet binnen drie minuten.") from None
        lines = [line for line in done.stdout.splitlines() if line.startswith("{")]
        if lines:
            outcome = json.loads(lines[-1])
            if not outcome["ok"]:
                raise ConnectorError(outcome["reason"])
            self.extra_reasons = {
                key: result.get("reason") for key, result in (outcome.get("extras") or {}).items()
            }
        elif done.returncode != 0:
            tail = done.stderr.strip().splitlines()[-1:] or ["onbekende fout"]
            raise ConnectorError(f"De browser stopte onverwacht: {tail[0]}")


_connectors: dict[Path, AgrovisionConnector] = {}
_connectors_lock = Lock()


def get_connector(directory: Path) -> AgrovisionConnector:
    """One per folder of cows, shared by the chats that use it."""
    key = directory.resolve()
    with _connectors_lock:
        if key not in _connectors:
            _connectors[key] = AgrovisionConnector(directory)
        return _connectors[key]
