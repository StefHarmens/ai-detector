"""Photos the farmer adds to a cow folder, so a cow can be recognised before
she has been seen in enough mounts.

The farmer sends a photo, or takes one from a camera; every cow on it is
found with the same model that splits a mount, and the farmer picks hers.
Her crop goes into her folder masked, like the photos from mounts."""

import logging
import re
import secrets
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from imageio_ffmpeg import get_ffmpeg_exe

from aidetector.cows.split import CowSplitter, Found, cow_crops, iou
from aidetector.media.video import get_image

logger = logging.getLogger(__name__)

# Next to the cow folders; found cows wait here until the farmer picks one.
UPLOAD_FOLDER = ".eigen-fotos"
_TOKEN = re.compile(r"^[0-9a-f]{12}$")
# The photo the cows were found on, or one cow on it as she looks.
_FOUND_PHOTO = re.compile(r"^([0-9a-f]{12})(_\d{1,3}_foto)?$")
# Waiting photos older than this are cleared away.
_KEEP_SECONDS = 3600
# Bigger photos are made this wide, as the 4K camera frames are.
_MAX_SIDE = 3840
# A phone photo can be big; a camera frame at 4K is some MB.
MAX_UPLOAD = 30 * 1024 * 1024
# Two boxes overlapping this much are one cow found twice.
_SAME_COW = 0.7
# The 4K streams of UniFi give a keyframe every 5 s; FFmpeg needs one.
_SNAPSHOT_TIMEOUT = 30


class UploadError(ValueError):
    """Something the farmer can fix, in words for them."""


def find_cows(splitter: CowSplitter, directory: Path, data: bytes) -> dict[str, Any]:
    """Finds the cows on a photo and keeps them until one is picked."""
    image = cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise UploadError("Dit is geen foto die de detector kan lezen. Gebruik een JPG of PNG.")
    image = _shrink(image)
    found = _whole_cows(splitter.find_all(image))
    folder = directory / UPLOAD_FOLDER
    folder.mkdir(parents=True, exist_ok=True)
    _clean(folder)
    token = secrets.token_hex(6)
    (folder / f"{token}.jpg").write_bytes(get_image(image, 90))
    for index, cow in enumerate(found):
        _, masked = cow_crops(image, cow)
        (folder / f"{token}_{index}.jpg").write_bytes(get_image(masked, 95))
        # To pick her by, as she goes into the folder: in a busy barn the
        # boxes overlap, and a plain crop shows her neighbours too.
        (folder / f"{token}_{index}_foto.jpg").write_bytes(get_image(_thumbnail(masked), 85))
    height, width = image.shape[:2]
    return {
        "token": token,
        "width": width,
        "height": height,
        "cows": [{"index": index, "box": list(cow.box)} for index, cow in enumerate(found)],
    }


def upload_photo(directory: Path, name: str) -> Path:
    """The photo the cows were found on, or one of them, to pick one."""
    match = _FOUND_PHOTO.match(name)
    if not match:
        raise UploadError("Deze foto bestaat niet (meer).")
    return _waiting(directory, match[1], f"{name}.jpg")


def add_photo(directory: Path, cow_folder: Path, token: str, index: int) -> Path:
    """Puts the picked cow in her folder; recognition uses it from now on."""
    source = _waiting(directory, token, f"{token}_{index}.jpg")
    cow_folder.mkdir(parents=True, exist_ok=True)
    destination = cow_folder / f"{datetime.now():%Y-%m-%dT%H-%M-%S}_eigen_{token}_{index}.jpg"
    destination.write_bytes(source.read_bytes())
    return destination


def snapshot(source: str) -> bytes:
    """A full frame from a camera stream, as JPEG."""
    command = [get_ffmpeg_exe(), "-hide_banner", "-loglevel", "error"]
    if source.lower().startswith("rtsp"):
        command += ["-rtsp_transport", "tcp"]
    command += ["-i", source, "-an"]
    # The first keyframe: joining a stream halfway, the frames before it
    # decode grey, and the farm got "no cow" on every camera. Skipping the
    # other frames in the decoder instead doubled the wait.
    command += ["-vf", "select='eq(pict_type,PICT_TYPE_I)'", "-fps_mode", "vfr"]
    command += ["-frames:v", "1", "-q:v", "2", "-f", "image2pipe", "-c:v", "mjpeg", "pipe:1"]
    try:
        result = subprocess.run(command, capture_output=True, timeout=_SNAPSHOT_TIMEOUT)
    except subprocess.TimeoutExpired as error:
        raise UploadError("De camera gaf binnen 30 seconden geen beeld.") from error
    if result.returncode != 0 or not result.stdout:
        logger.warning(
            "Snapshot failed (exit %s): %s",
            result.returncode,
            result.stderr.decode(errors="replace").strip()[-300:],
        )
        raise UploadError("De camera gaf geen beeld. Probeer het zo nog eens.")
    return result.stdout


def _whole_cows(found: list[Found]) -> list[Found]:
    """One box per cow, and per cow only her own pixels: the model sometimes
    finds a cow twice, or gives her a piece of the cow next to her."""
    kept: list[Found] = []
    for cow in found:
        if any(iou(cow.box, other.box) > _SAME_COW for other in kept):
            continue
        if cow.mask is not None:
            cow = Found(cow.box, _largest_part(cow.mask), cow.origin)
        kept.append(cow)
    return kept


def _largest_part(mask: np.ndarray) -> np.ndarray:
    count, labels, stats, _ = cv2.connectedComponentsWithStats(mask.astype(np.uint8), connectivity=8)
    if count <= 2:
        return mask
    # Label 0 is the background.
    largest = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
    return labels == largest


def _waiting(directory: Path, token: str, name: str) -> Path:
    if not _TOKEN.match(token):
        raise UploadError("Deze foto bestaat niet (meer).")
    path = directory / UPLOAD_FOLDER / name
    if not path.is_file():
        raise UploadError("Deze foto is verlopen. Kies of maak hem opnieuw.")
    return path


def _thumbnail(image: np.ndarray, side: int = 320) -> np.ndarray:
    height, width = image.shape[:2]
    if max(height, width) <= side:
        return image
    scale = side / max(height, width)
    return cv2.resize(image, (round(width * scale), round(height * scale)), interpolation=cv2.INTER_AREA)


def _shrink(image: np.ndarray) -> np.ndarray:
    height, width = image.shape[:2]
    if max(height, width) <= _MAX_SIDE:
        return image
    scale = _MAX_SIDE / max(height, width)
    return cv2.resize(image, (round(width * scale), round(height * scale)), interpolation=cv2.INTER_AREA)


def _clean(folder: Path) -> None:
    oldest = time.time() - _KEEP_SECONDS
    for path in folder.glob("*.jpg"):
        try:
            if path.stat().st_mtime < oldest:
                path.unlink()
        except OSError:
            pass
