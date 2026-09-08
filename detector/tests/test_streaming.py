from threading import Event

from aidetector.sources.streaming import StreamBatcher


def test_failed_stream_initialization_does_not_run_loader_cleanup(monkeypatch, caplog):
    attempted = Event()

    def fail_to_open(_source):
        attempted.set()
        raise ConnectionError("Failed to open test stream")

    monkeypatch.setattr("aidetector.sources.streaming.LoadStreams", fail_to_open)

    batcher = StreamBatcher(["test-stream"])
    assert attempted.wait(timeout=1)
    batcher.stop()

    assert "Stream loader crashed for test-stream" in caplog.text
    assert "Failed to close stream loader" not in caplog.text
    assert "UnboundLocalError" not in caplog.text
