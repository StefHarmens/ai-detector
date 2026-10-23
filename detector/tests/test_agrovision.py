import os
import stat

import pytest
from openpyxl import Workbook

from aidetector.cows.agrovision import ConnectorError, get_connector

HEADER = ["Diernr", "Resp 1", "Levensnummer", "Gesl", "Geb dat", "Naam", "Werknummer", "Diercat", "Levnr moeder", "Aankdat"]


def herd(path, count):
    workbook = Workbook()
    workbook.active.append(HEADER)
    for number in range(1, count + 1):
        workbook.active.append([number, None, f"NL{number:09d}", "V", None, f"Koe{number}", number, "Koeien", None, None])
    workbook.save(path)


LOGIN = {"url": "https://agrovision.example/rapport", "username": "boer", "password": "geheim"}


@pytest.fixture
def connector(tmp_path):
    connector = get_connector(tmp_path)
    connector.fetch = lambda state, out: herd(out, 10)
    return connector


def test_save_keeps_the_password_private(connector):
    connector.save(LOGIN)
    view = connector.view()
    assert "password" not in view and view["has_password"] and view["configured"]
    assert stat.S_IMODE(os.stat(connector.folder / "connector.json").st_mode) == 0o600
    # Empty password on a later save keeps the old one.
    connector.save({**LOGIN, "password": "", "run_at": "04:00"})
    assert connector._read()["password"] == "geheim"
    assert connector.view()["run_at"] == "04:00"


@pytest.mark.parametrize("change", [{"url": "agrovision"}, {"username": ""}, {"run_at": "25:00"}])
def test_save_rejects_incomplete_input(connector, change):
    with pytest.raises(ValueError):
        connector.save({**LOGIN, **change})


def test_success_writes_the_herd_list_and_status(connector):
    connector.save(LOGIN)
    connector._run()
    view = connector.view()
    assert view["status"] == "ok" and view["animals"] == 10 and view["succeeded"]
    assert connector.herd_file.is_file()


def test_failure_keeps_the_old_list_and_gives_the_reason(connector):
    connector.save(LOGIN)
    connector._run()
    told = []
    connector.notify = told.append

    def refuse(state, out):
        raise ConnectorError("Inloggen mislukt")

    connector.fetch = refuse
    before = connector.herd_file.read_bytes()
    connector._run()
    connector._run()
    view = connector.view()
    assert view["status"] == "failed" and view["reason"] == "Inloggen mislukt"
    assert view["animals"] == 10
    assert connector.herd_file.read_bytes() == before
    # Told once per streak, and again when it works.
    assert len(told) == 1
    connector.fetch = lambda state, out: herd(out, 10)
    connector._run()
    assert len(told) == 2 and connector.view()["status"] == "ok"


def test_partial_list_and_non_list_are_refused(connector):
    connector.save(LOGIN)
    connector._run()
    connector.fetch = lambda state, out: herd(out, 5)
    connector._run()
    assert "onvolledig" in connector.view()["reason"]
    connector.fetch = lambda state, out: out.write_text("<html>Inloggen</html>")
    connector._run()
    assert connector.view()["status"] == "failed"
    assert len(__import__("aidetector.cows.importer", fromlist=["x"]).parse_herd(
        __import__("aidetector.cows.importer", fromlist=["x"]).read_table(connector.herd_file)).animals) == 10


def test_delete_forgets_everything(connector):
    connector.save(LOGIN)
    connector.delete()
    assert not connector.view()["configured"]
