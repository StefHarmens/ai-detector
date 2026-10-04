import subprocess
from datetime import datetime, timedelta

import pytest
from imageio_ffmpeg import get_ffmpeg_exe

from aidetector.sources.recording import (
    Gop,
    TsSplitter,
    decode_command,
    decode_gops,
    frame_step,
    gops_between,
    record_command,
    stream_codec,
)

START = datetime(2026, 10, 3, 18, 0, 0)


def camera(tmp_path, codec: str = "libx264", seconds: float = 4, size: str = "640x360"):
    """A video like a camera's: 25 fps and a keyframe every second, as a
    bare stream, which holds its parameter sets as an RTSP stream does (and
    which, unlike MPEG-TS, the Linux build of FFmpeg can read)."""
    video = tmp_path / ("camera.hevc" if codec == "libx265" else "camera.h264")
    options = ["-preset", "ultrafast"]
    if codec == "libx265":
        options += ["-x265-params", "log-level=none"]
    subprocess.run(
        [
            get_ffmpeg_exe(), "-loglevel", "error", "-f", "lavfi",
            "-i", f"testsrc=size={size}:rate=25:duration={seconds}",
            "-c:v", codec, *options, "-g", "25", "-bf", "0", "-pix_fmt", "yuv420p", str(video),
        ],
        check=True,
    )
    return video


def recorded(video) -> bytes:
    """What the recorder reads from FFmpeg for this video."""
    return subprocess.run(record_command(str(video)), capture_output=True, check=True).stdout


def split(data: bytes, piece: int = 1 << 16, connection: int = 1) -> list[Gop]:
    # A file is read far faster than a camera sends it: all of it "now".
    splitter = TsSplitter(connection, max_clock_error=timedelta(hours=1))
    gops = []
    for offset in range(0, len(data), piece):
        gops += splitter.feed(data[offset : offset + piece], START)
    if splitter.current is not None:
        gops.append(splitter.current)
    return gops


@pytest.mark.parametrize("codec", ["libx264", "libx265"])
def test_the_stream_is_cut_at_each_keyframe(tmp_path, codec):
    gops = split(recorded(camera(tmp_path, codec)))

    assert [len(gop.frames) for gop in gops] == [25, 25, 25, 25]
    # Times follow the camera's clock: one frame per 40 ms, from START on.
    assert [gop.date for gop in gops] == [START + timedelta(seconds=n) for n in range(4)]
    assert gops[0].frames[1][1] - gops[0].frames[0][1] == timedelta(milliseconds=40)


def test_reads_that_end_halfway_a_packet_give_the_same_gops(tmp_path):
    data = recorded(camera(tmp_path, seconds=2))

    whole = split(data)
    pieces = split(data, piece=1000)

    assert [bytes(gop.data) for gop in pieces] == [bytes(gop.data) for gop in whole]
    assert [gop.frames for gop in pieces] == [gop.frames for gop in whole]


def test_frames_before_the_first_keyframe_are_left_out(tmp_path):
    data = recorded(camera(tmp_path, seconds=2))
    first = split(data)[0]
    # Joining a stream halfway: start in the middle of the first GOP.
    middle = len(first.tables) + len(first.data) // 2
    middle -= middle % 188

    splitter = TsSplitter()
    gops = splitter.feed(data[middle:], START)

    assert splitter.skipped > 0
    assert gops == [] and splitter.current is not None
    assert len(splitter.current.frames) == 25


def test_a_jump_in_the_cameras_clock_starts_again_from_ours(tmp_path):
    data = recorded(camera(tmp_path, seconds=2))
    splitter = TsSplitter()
    splitter.feed(data[: len(data) // 2], START)
    later = START + timedelta(minutes=5)

    splitter.feed(data[len(data) // 2 :], later)

    dates = [date for _, date in splitter.current.frames]
    assert dates[-1] - later < timedelta(seconds=1)


def test_the_gops_of_an_event_start_at_the_keyframe_before_it(tmp_path):
    gops = split(recorded(camera(tmp_path)))

    chosen = gops_between(gops, START + timedelta(seconds=1.5), START + timedelta(seconds=2.2))

    assert [gop.date for gop in chosen] == [START + timedelta(seconds=1), START + timedelta(seconds=2)]
    assert gops_between(gops, START - timedelta(seconds=10), START) == gops[:1]


def test_only_the_frames_of_the_event_are_decoded(tmp_path):
    gops = split(recorded(camera(tmp_path, size="1280x720")))
    start, end = START + timedelta(seconds=1.5), START + timedelta(seconds=2.5)

    frames = decode_gops(gops, start, end, fps=10, max_width=640, quality=85, hwaccel=None)

    # Every second frame of the 25 fps camera, not every third (8.3 per second).
    assert 11 <= len(frames) <= 14
    assert all(start <= frame.date <= end for frame in frames)
    # The first frame of the event, not the keyframe before it.
    assert frames[0].date - start < timedelta(milliseconds=40)
    assert frames[0].jpg.shape == (360, 640, 3)


def test_the_full_frame_rate_of_the_camera_can_be_kept(tmp_path):
    gops = split(recorded(camera(tmp_path)))

    frames = decode_gops(
        gops, START, START + timedelta(seconds=1.96), fps=25, max_width=None, quality=85, hwaccel=None
    )

    assert len(frames) == 50
    assert [frame.date for frame in frames] == [
        START + timedelta(milliseconds=40 * n) for n in range(50)
    ]


def test_each_connection_is_decoded_on_its_own(tmp_path):
    data = recorded(camera(tmp_path, seconds=2))
    first = split(data, connection=1)
    second = [
        Gop(2, gop.tables, gop.data, [(pts, date + timedelta(seconds=30)) for pts, date in gop.frames])
        for gop in split(data, connection=2)
    ]

    frames = decode_gops(
        first + second, START, START + timedelta(seconds=40), fps=5, max_width=None, quality=85, hwaccel=None
    )

    dates = [frame.date for frame in frames]
    assert any(date < START + timedelta(seconds=2) for date in dates)
    assert any(date >= START + timedelta(seconds=30) for date in dates)
    assert dates == sorted(dates)


def test_an_event_without_frames_is_not_decoded(tmp_path):
    gops = split(recorded(camera(tmp_path, seconds=1)))

    assert decode_gops(gops, START + timedelta(minutes=1), START + timedelta(minutes=2), 10, None, 85, None) == []


def test_recording_copies_the_stream_without_decoding_it():
    command = record_command("rtsps://192.168.1.77:7441/key")

    assert command[command.index("-c:v") + 1] == "copy"
    assert command[command.index("-rtsp_transport") + 1] == "tcp"
    assert "-vf" not in command and "-hwaccel" not in command


def test_decoding_uses_the_hardware_and_one_fixed_format():
    command = decode_command("hevc", 3840, 85, "auto", 30, 300, 2)

    assert command[command.index("-hwaccel") + 1] == "auto"
    # The bare stream, not MPEG-TS, which the Linux build cannot read.
    assert command[command.index("-i") - 1] == "hevc"
    filters = command[command.index("-vf") + 1]
    assert "between(n\\,30\\,300)*not(mod(n-30\\,2))" in filters
    assert filters.endswith(":out_range=full,format=yuv420p")


@pytest.mark.parametrize(("codec", "name"), [("libx264", "h264"), ("libx265", "hevc")])
def test_the_codec_is_read_from_the_stream(tmp_path, codec, name):
    gops = split(recorded(camera(tmp_path, codec, seconds=1)))

    assert stream_codec(gops[0].tables) == name


def test_about_fps_frames_per_second_are_taken():
    def camera_dates(fps: float) -> list[datetime]:
        return [START + timedelta(seconds=n / fps) for n in range(50)]

    # A 25 fps camera: every second frame for 10, all for 25.
    assert frame_step(camera_dates(25), 10) == 2
    assert frame_step(camera_dates(25), 25) == 1
    assert frame_step(camera_dates(25), 4) == 5
    assert frame_step(camera_dates(30), 10) == 3
    assert frame_step(camera_dates(30), 25) == 1
    assert frame_step(camera_dates(25)[:1], 10) == 1
