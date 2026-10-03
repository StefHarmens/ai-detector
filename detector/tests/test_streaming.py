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

    assert "Could not open stream test-stream: Failed to open test stream" in caplog.text
    assert "Traceback" not in caplog.text
    assert "Failed to close stream loader" not in caplog.text
    assert "UnboundLocalError" not in caplog.text


def test_missing_frames_warning_hides_the_stream_key(monkeypatch, caplog):
    monkeypatch.setattr(
        "aidetector.sources.streaming.LoadStreams",
        lambda _source: (_ for _ in ()).throw(ConnectionError("offline")),
    )
    source = "rtsps://192.168.1.77:7441/SecretKey123?enableSrtp"
    batcher = StreamBatcher([source])
    batcher.stop()

    batcher.log_missing(set())
    batcher.log_missing(set())

    assert "Missing frames from sources: rtsps://192.168.1.77:7441/<key>" in caplog.text
    assert "SecretKey123" not in caplog.text
