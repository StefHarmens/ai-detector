import json
import logging
import re
from dataclasses import field
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

import cv2
import numpy as np
import requests
from aidetector.utils.errors import ConfigError, MissingConfigError
from aidetector.utils.version import REF_NAME
from numpy import ndarray
from pydantic import ConfigDict, ValidationError
from pydantic.dataclasses import dataclass

logger = logging.getLogger(__name__)


def _default_frames_min() -> int:
    try:
        import torch

        if torch.cuda.is_available():
            return 6
    except ImportError:
        pass
    return 3


Confidence = dict[str, float]
HttpMethod = Literal["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD"]


@dataclass
class Crop:
    x1: int
    y1: int
    x2: int
    y2: int
    label: str | None = None
    confidence: float | None = None


# Frames wait in memory until their event ends, which can take a minute per
# camera. As JPEG a 1280x720 barn frame is about 10 times smaller (2.8 MB → 0.3 MB).
STORED_FRAME_QUALITY = 90


class ImageSet:
    """A frame with its detected crops. The pixels are stored as JPEG and only
    decoded when they are needed."""

    def __init__(self, jpg: ndarray, crops: list[Crop] | None = None):
        self.height, self.width = jpg.shape[:2]
        success, encoded = cv2.imencode(
            ".jpg", jpg, (int(cv2.IMWRITE_JPEG_QUALITY), STORED_FRAME_QUALITY)
        )
        if not success:
            raise ValueError("Failed to encode frame")
        self.jpeg = encoded.tobytes()
        self.crops = list(crops or [])

    @property
    def jpg(self) -> ndarray:
        image = cv2.imdecode(np.frombuffer(self.jpeg, dtype=np.uint8), cv2.IMREAD_COLOR)
        if image is None:
            raise ValueError("Failed to decode frame")
        return image

    def with_crops(self, crops: list[Crop]) -> "ImageSet":
        """Returns the same frame with other crops, without encoding it again."""
        copy = object.__new__(ImageSet)
        copy.height, copy.width, copy.jpeg = self.height, self.width, self.jpeg
        copy.crops = list(crops)
        return copy

    @property
    def crop_region(self) -> Crop | None:
        if not self.crops:
            return None
        if len(self.crops) == 1:
            return self.crops[0]
        return Crop(
            min(crop.x1 for crop in self.crops),
            min(crop.y1 for crop in self.crops),
            max(crop.x2 for crop in self.crops),
            max(crop.y2 for crop in self.crops),
        )


class HiresFrame:
    """A frame from the high-resolution stream of a camera, kept as JPEG."""

    def __init__(self, date: datetime, jpeg: bytes):
        self.date = date
        self.jpeg = jpeg

    @property
    def jpg(self) -> ndarray:
        image = cv2.imdecode(np.frombuffer(self.jpeg, dtype=np.uint8), cv2.IMREAD_COLOR)
        if image is None:
            raise ValueError("Failed to decode high-resolution frame")
        return image


@dataclass(config=ConfigDict(arbitrary_types_allowed=True))
class Detection:
    date: datetime
    images: ImageSet
    confidence: Confidence
    source: str | None = None
    camera: str | None = None
    # Frames of the high-resolution stream from just before the event until its
    # end; only set on the best detection of an exported event.
    hires: list[HiresFrame] | None = None
    # Videos already made of this event, so chats with the same settings
    # share one; see event_mp4().
    videos: dict[str, Any] = field(default_factory=dict, repr=False, compare=False)


@dataclass(kw_only=True)
class YoloConfig:
    model: str
    task: Literal["detect", "segment"] = "detect"
    confidence: float | Confidence = 0
    # Boxes between review_confidence and confidence never make an alert, but
    # are kept so the event can go to exporters with review enabled.
    review_confidence: float | None = None
    tracking: bool = False
    time_max: int = 60
    timeout: int = 5
    cooldown: float | dict[str, float] = 0
    include_trailing_time: int = 1
    frames_min: int = field(default_factory=_default_frames_min)
    imgsz: int = 640


@dataclass(kw_only=True)
class HiresConfig:
    # One stream per detection source, in the same order; null for a camera
    # without a high-resolution stream. Leave it out when detection.source is
    # already the 4K stream: then its own frames are kept, before they are
    # made smaller for detection, and nothing is decoded twice.
    source: str | list[str | None] | None = None
    # Frames kept per second. The event's video is made from them, so 10 keeps
    # it fluent; each 4K frame is about 0.6 MB of memory.
    fps: float = 10
    # How long frames are kept outside a mount; must cover before_seconds.
    # During a mount its frames are kept until it is handled. Each second is
    # fps 4K frames per camera in memory.
    seconds: int = 12
    before_seconds: int = 10
    quality: int = 85
    hwaccel: str | None = "auto"
    # Decode only the keyframes of a separate stream: far less memory and CPU,
    # at most one frame per keyframe interval of the camera. A camera whose
    # keyframes are more than 2 s apart (UniFi: 5 s) is decoded in full.
    keyframes_only: bool = True
    # Kept frames are made this wide at most: 4K, so the numbers on the cows
    # can be read on the web page. Telegram photos are made 2560 px anyway;
    # 2560 here uses about half the memory.
    max_width: int | None = 3840


@dataclass(kw_only=True)
class DetectionConfig:
    source: str | list[str]
    name: str | list[str] | None = None
    interval: float = 0
    frame_retention: int = 15
    frames_width: int = 1280
    hires: HiresConfig | None = None


@dataclass(kw_only=True)
class VLMConfig:
    prompt: str
    model: str | list[str]
    key: str | None = field(default=None, repr=False)
    url: str | None = None
    strategy: Literal["IMAGE", "VIDEO"] = "VIDEO"
    crop_padding: float = 0.1


@dataclass(kw_only=True)
class ExporterConfig:
    confidence: float | Confidence | None = None
    crop_padding: float = 0.1
    export_rejected: bool = False
    # Only receive events that did not become an alert, for manual review.
    review: bool = False


@dataclass(kw_only=True)
class HttpConfig:
    url: str
    method: HttpMethod = "GET"
    timeout: int | None = None
    headers: dict[str, str] | None = field(default=None, repr=False)
    body: str | None = None


@dataclass(kw_only=True)
class SummaryConfig:
    # An empty list sends no overview, but still sends one alert per mount.
    times: list[str] = field(default_factory=lambda: ["08:00", "16:00"])
    merge_seconds: int = 120
    merge_distance: float = 0.25
    camera_merge_seconds: int = 10
    camera_groups: list[list[str]] | None = None
    send_events: bool = True


@dataclass(kw_only=True)
class CowsConfig:
    # Defaults to <feedback_directory>/koeien.
    directory: Path | None = None
    # The herd list exported from the herd program (Excel or CSV). It is read
    # at the start and again whenever the file changes, and is leading.
    herd_file: Path | None = None
    # The animal categories (Lely: Diercat) that take part; the rest, such as
    # calves and male animals, are left out. null uses no list: then only
    # calves and bulls are recognised and left out.
    herd_categories: list[str] | None = field(
        default_factory=lambda: ["Koeien", "Vrouwelijk jongvee"]
    )
    # Generic model that finds single cows; COCO has the class "cow". A
    # segmentation model also gives her pixels, so the barn can be masked out.
    segment_model: str = "yolo11s-seg.pt"
    segment_confidence: float = 0.25
    reid_model: str = "https://huggingface.co/onnx-community/dinov2-small/resolve/main/onnx/model.onnx"
    # A cow is filled in without asking when her score is at least accept_score,
    # beats the next cow by accept_margin and her folder has min_photos photos.
    # Barn and cubicles fill much of a crop: on the barn examples two different
    # cows still scored 0.80.
    accept_score: float = 0.90
    accept_margin: float = 0.08
    min_photos: int = 5
    candidates: int = 3
    # Ask about the cows in the Telegram chat as well (photo with buttons,
    # typed numbers, the command menu). "nummers": only the photo, to answer
    # with the numbers. Off: only the web page asks.
    telegram: bool | Literal["nummers"] = False
    # Days a mount's video is kept for the web page; the photos stay.
    video_days: int = 90


@dataclass(kw_only=True)
class ChatConfig(ExporterConfig):
    token: str = field(repr=False)
    chat: str
    feedback_directory: Path = Path(".")
    alert_every: int = 1
    include_image: bool = False
    include_plot: bool = False
    include_crop: bool = False
    include_video: bool = True
    video_width: int | None = 1280
    video_crf: int = 28
    summary: SummaryConfig | None = None
    cows: CowsConfig | None = None


@dataclass(kw_only=True)
class WebhookConfig(ExporterConfig, HttpConfig):
    method: HttpMethod = "POST"
    token: str | None = field(default=None, repr=False)
    data_type: Literal["binary", "base64", "none"] = "binary"
    data_max: int | None = None
    include_image: bool = False
    include_plot: bool = False
    include_crop: bool = True
    include_video: bool = False
    video_width: int | None = 1280
    video_crf: int = 28


@dataclass(kw_only=True)
class DiskConfig(ExporterConfig):
    directory: Path | None = None
    strategy: Literal["ALL", "BEST"] = "BEST"
    export_rejected: bool = True


@dataclass
class ExportersConfig:
    disk: DiskConfig | list[DiskConfig] | None = None
    telegram: ChatConfig | list[ChatConfig] | None = None
    webhook: WebhookConfig | list[WebhookConfig] | None = None


@dataclass(kw_only=True)
class HealthcheckConfig(HttpConfig):
    interval: int = 60
    timeout: int = 5


@dataclass(kw_only=True)
class ApiConfig:
    """The cow page of the web interface talks to the detector here. Keep the
    host on 127.0.0.1: the web interface runs on the same computer and shows
    the page on the network itself."""

    host: str = "127.0.0.1"
    port: int = 8765


@dataclass
class DetectorConfig:
    detection: DetectionConfig
    yolo: YoloConfig | None = None
    vlm: VLMConfig | list[VLMConfig] | None = None
    exporters: ExportersConfig | None = None


@dataclass(kw_only=True)
class OnnxConfig:
    provider: str | None = None
    winml: bool = True
    opset: int = 20


@dataclass
class Config:
    detectors: list[DetectorConfig]
    onnx: OnnxConfig = field(default_factory=OnnxConfig)
    health: HealthcheckConfig | None = None
    # null turns the API for the web interface off.
    api: ApiConfig | None = field(default_factory=ApiConfig)


template_url = f"https://raw.githubusercontent.com/StefHarmens/ai-detector/{REF_NAME}/config/config.template.json"
schema_url = f"https://raw.githubusercontent.com/StefHarmens/ai-detector/{REF_NAME}/config/config.schema.json"


def get_template() -> Any | None:
    try:
        template = requests.get(template_url).json()
        template["$schema"] = schema_url
        return template
    except requests.exceptions.RequestException as e:
        logger.error(f"Failed to fetch template from {template_url}: {e}")
        return None


def get_timestamped_filename(detection: Detection) -> str:
    rounded_confidence = round(max_confidence(detection.confidence), 3)
    timestamp = get_date_path(detection, "milliseconds")
    return f"{timestamp}_{rounded_confidence}.jpg"


def get_date_path(detection: Detection, timespec: Literal["seconds", "milliseconds"]) -> str:
    return detection.date.isoformat(timespec=timespec).replace(":", "-")


def min_confidence(confidence: float | Confidence | None) -> float:
    if confidence is None or not confidence:
        return 0
    if isinstance(confidence, dict):
        return min(float(value) for value in confidence.values())
    return float(confidence)


def max_confidence(confidence: float | Confidence | None) -> float:
    if confidence is None or not confidence:
        return 0
    if isinstance(confidence, dict):
        return max(float(value) for value in confidence.values())
    return float(confidence)


def confidence_matches(
    value: dict[str, float],
    threshold: float | dict[str, float],
) -> bool:
    if isinstance(threshold, dict):
        return any(
            class_id in value and float(value[class_id]) >= float(class_threshold)
            for class_id, class_threshold in threshold.items()
        )
    else:
        return max_confidence(value) >= threshold


def matching_confidences(
    value: dict[str, float],
    threshold: float | dict[str, float],
) -> list[str]:
    return [
        class_name
        for class_name, confidence in value.items()
        if confidence_matches({class_name: confidence}, threshold)
    ]


def format_validation_errors(error: ValidationError) -> str:
    messages = []
    for err in error.errors():
        location = " -> ".join(str(loc) for loc in err["loc"])
        msg = err["msg"]
        messages.append(f"  • {location}: {msg}")
    return "\n".join(messages)


_SECRETS = (
    (re.compile(r'(rtsps?://[^/"]+/)[^"]*'), r"\1<key>"),
    (re.compile(r'("(?:token|chat|key)"\s*:\s*")[^"]*'), r"\1<hidden>"),
)


def _hide_secrets(line: str) -> str:
    for pattern, replacement in _SECRETS:
        line = pattern.sub(replacement, line)
    return line


def json_error_hint(text: str, error: json.JSONDecodeError) -> str:
    """Shows the line of a JSON error with a pointer, and the usual cause: a
    missing comma after the block above. Stream keys and tokens are hidden,
    since such logs get pasted into chats."""
    lines = text.splitlines()
    if not 0 < error.lineno <= len(lines):
        return ""
    line = lines[error.lineno - 1]
    before = line[: error.colno - 1]
    pointer = "".join("\t" if character == "\t" else " " for character in before) + "^"
    hint = [f"Line {error.lineno}: {_hide_secrets(line)}", f"Line {error.lineno}: {pointer}"]
    if error.msg.startswith("Expecting ',' delimiter") and not before.strip():
        previous = next(
            (number for number in range(error.lineno - 1, 0, -1) if lines[number - 1].strip()),
            None,
        )
        if previous is not None:
            hint.append(
                f"A comma is probably missing at the end of line {previous}: "
                f"{_hide_secrets(lines[previous - 1].strip())}"
            )
    return "\n".join(hint)


def _folder_hint() -> str:
    """Started from the wrong folder (e.g. Downloads) the program does not
    find config.json, since it looks next to itself."""
    return (
        f"The detector reads config.json next to the program, in {Path.cwd()}. "
        "Is the program in the same folder as your config.json? Move it there and start it again."
    )


def load_config() -> Config:
    config_path = Path("config.json")
    if not config_path.exists():
        template = get_template()
        if template:
            with open(config_path, "w") as f:
                json.dump(template, f, indent=4)
            logger.warning(
                f"Created an empty {config_path.resolve()} from the template. {_folder_hint()}"
            )
            raise MissingConfigError(f"Configure before running: {config_path.resolve()}")
        else:
            logger.error(f"Configuration file not found: {config_path.resolve()}")
            logger.error(_folder_hint())
            logger.error("Create a config.json file. See: https://github.com/StefHarmens/ai-detector")
            raise MissingConfigError(f"Configuration file not found: {config_path.resolve()}")

    text = config_path.read_text()
    try:
        config_json = json.loads(text)
    except json.JSONDecodeError as e:
        message = f"Invalid JSON in {config_path.resolve()}: {e}\n{json_error_hint(text, e)}".rstrip()
        logger.error(message)
        raise ConfigError(message)

    if config_json is None:
        logger.error(f"Config file is empty: {config_path}")
        raise ConfigError(f"Config file is empty: {config_path}")

    try:
        # Only write when needed: the detector restarts when config.json changes.
        if config_json.get("$schema") != schema_url:
            config_json["$schema"] = schema_url
            with open(config_path, "w") as f:
                json.dump(config_json, f, indent=4)
    except Exception as e:
        logger.warning(f"Failed to update schema in {config_path}: {e}")

    try:
        return Config(**config_json)
    except ValidationError as e:
        message = f"Configuration validation failed for {config_path.resolve()}:\n{format_validation_errors(e)}"
        if "detectors" not in config_json:
            # Most likely the empty config.json made on a first start elsewhere.
            message += f"\n{_folder_hint()}"
        logger.error(message)
        raise ConfigError(message)


config = load_config()
