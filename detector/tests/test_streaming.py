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


def test_a_missing_source_is_logged_once_and_once_when_it_is_back(monkeypatch, caplog):
    import logging

    monkeypatch.setattr(
        "aidetector.sources.streaming.LoadStreams",
        lambda _source: (_ for _ in ()).throw(ConnectionError("offline")),
    )
    batcher = StreamBatcher(["cam-a", "cam-b"])
    batcher.stop()
    caplog.clear()

    with caplog.at_level(logging.INFO, logger="aidetector.sources.streaming"):
        # A night without cam-a: thousands of batches.
        for _ in range(1000):
            batcher.log_missing({"cam-b"})
        batcher.log_missing({"cam-a", "cam-b"})
        batcher.log_missing({"cam-a", "cam-b"})

    assert [record.getMessage() for record in caplog.records] == [
        "Missing frames from sources: cam-a",
        "Frames again from sources: cam-a",
    ]
