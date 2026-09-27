import json
import sys
from threading import Event, Thread

import pytest

import aidetector
from aidetector.detection import manager as manager_module


class FakeManager:
    def __init__(self, threads, streaming=True):
        self.detectors = threads
        self.threads = threads
        self.streaming = streaming
        self.stopped = False

    def start(self):
        for thread in self.threads:
            thread.start()
        return list(self.threads)

    def is_streaming(self):
        return self.streaming

    def stop(self):
        self.stopped = True


@pytest.fixture
def run(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "config.json").write_text("{}")
    monkeypatch.setattr(aidetector, "_WATCH_SECONDS", 0.01)
    monkeypatch.setattr(aidetector, "_SETTLE_SECONDS", 0.05)
    monkeypatch.setattr("aidetector.utils.onnx.setup_ort", lambda config: None)

    def run(manager):
        monkeypatch.setattr(
            manager_module.Manager, "from_config", classmethod(lambda cls, c: manager)
        )
        return aidetector.start()

    return run


def test_changed_config_restarts(run, tmp_path):
    release = Event()
    manager = FakeManager([Thread(target=release.wait, daemon=True)])
    Thread(
        target=lambda: (release.wait(0.1), (tmp_path / "config.json").write_text("{ }"))
    ).start()

    assert run(manager) is True
    assert manager.stopped
    release.set()


def test_stopped_stream_detector_counts_as_a_crash(run):
    manager = FakeManager([Thread(target=lambda: None)])

    with pytest.raises(RuntimeError, match="stopped unexpectedly"):
        run(manager)
    assert manager.stopped


def test_finished_file_sources_exit(run):
    manager = FakeManager([Thread(target=lambda: None)], streaming=False)

    assert run(manager) is False


def test_crash_restarts_as_a_new_process(monkeypatch):
    calls = []
    monkeypatch.setattr(sys, "argv", ["aidetector"])
    monkeypatch.setattr(aidetector, "_RESTART_DELAY_SECONDS", 0)
    monkeypatch.setattr(aidetector, "start", lambda: 1 / 0)
    monkeypatch.setattr(aidetector.logging, "shutdown", lambda: None)
    monkeypatch.setattr(
        aidetector.os, "execv", lambda path, arguments: calls.append(arguments)
    )

    aidetector.main()

    assert calls and calls[0][0] == sys.executable


def test_saving_the_same_config_does_not_restart(tmp_path, monkeypatch):
    path = tmp_path / "config.json"
    path.write_bytes(b"{}")
    revision = path.read_bytes()
    # The editor has emptied the file and writes it again while we wait.
    path.write_bytes(b"")
    monkeypatch.setattr(aidetector.time, "sleep", lambda _: path.write_bytes(b"{}"))

    assert not aidetector._config_changed(path, revision)


def test_a_real_config_change_restarts(tmp_path, monkeypatch):
    path = tmp_path / "config.json"
    path.write_bytes(b"{}")
    revision = path.read_bytes()
    path.write_bytes(b'{"detectors": []}')
    monkeypatch.setattr(aidetector.time, "sleep", lambda _: None)

    assert aidetector._config_changed(path, revision)


def test_loading_a_config_with_schema_leaves_the_file_alone(tmp_path, monkeypatch):
    from aidetector.utils import config as config_module

    monkeypatch.chdir(tmp_path)
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "$schema": config_module.schema_url,
                "detectors": [{"detection": {"source": "clip.mp4"}}],
            }
        )
    )
    before = config_path.stat().st_mtime_ns

    config_module.load_config()

    assert config_path.stat().st_mtime_ns == before
