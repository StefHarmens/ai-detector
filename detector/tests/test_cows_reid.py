import threading
import time

from aidetector.cows import reid
from aidetector.cows.reid import model_file


class SlowDownload:
    """A download that takes a while, so two chats ask at the same time."""

    def __init__(self, calls: list[str]):
        self.calls = calls

    def __call__(self, url, **_):
        self.calls.append(url)
        return self

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def raise_for_status(self):
        pass

    def iter_content(self, _):
        time.sleep(0.1)
        yield b"onnx"


def test_two_chats_download_the_model_once(tmp_path, monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(reid.requests, "get", SlowDownload(calls))
    paths, errors = [], []

    def ask():
        try:
            paths.append(model_file("https://example.com/model.onnx", tmp_path))
        except Exception as error:
            errors.append(error)

    threads = [threading.Thread(target=ask) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    assert calls == ["https://example.com/model.onnx"]
    assert paths == [tmp_path / ".model" / "model.onnx"] * 2
    assert paths[0].read_bytes() == b"onnx"
    assert [path.name for path in (tmp_path / ".model").iterdir()] == ["model.onnx"]
