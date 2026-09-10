import json
import logging
import secrets
import shutil
from pathlib import Path
from threading import Event, Lock, Thread
from typing import Any

import requests

from aidetector.exporters.webhook import WebhookExporter
from aidetector.media.video import (
    compress_jpg,
    generate_mp4,
    get_crop,
    get_image,
    get_plot,
)
from aidetector.utils.config import (
    ChatConfig,
    Detection,
    WebhookConfig,
    get_timestamped_filename,
    max_confidence,
)


class TelegramFeedbackListener:
    logger = logging.getLogger("TelegramFeedbackListener")

    def __init__(self, token: str, feedback_directory: Path = Path(".")):
        self.api_url = f"https://api.telegram.org/bot{token}"
        self.allowed_chats: set[str] = set()
        self.data_root = feedback_directory.expanduser().resolve()
        self.feedback_directory = self.data_root / ".telegram-feedback"
        self.offset = 0
        self.started = False
        self.start_lock = Lock()
        self.stop_event = Event()

    def register_chat(self, chat: str) -> None:
        self.allowed_chats.add(str(chat))

    def save_detection(self, detection: Detection) -> str:
        feedback_id = secrets.token_urlsafe(12)
        image_height, image_width = detection.images.jpg.shape[:2]
        self.feedback_directory.mkdir(parents=True, exist_ok=True)
        (self.feedback_directory / f"{feedback_id}.jpg").write_bytes(
            get_image(detection.images.jpg)
        )
        (self.feedback_directory / f"{feedback_id}.json").write_text(
            json.dumps(
                {
                    "filename": f"{feedback_id}_{get_timestamped_filename(detection)}",
                    "width": image_width,
                    "height": image_height,
                    "boxes": [
                        {
                            "x1": crop.x1,
                            "y1": crop.y1,
                            "x2": crop.x2,
                            "y2": crop.y2,
                            "label": crop.label,
                        }
                        for crop in detection.images.crops
                        if crop.label
                    ],
                }
            )
        )
        return feedback_id

    def add_buttons(self, chat: str, message_id: int, feedback_id: str) -> None:
        response = requests.post(
            f"{self.api_url}/editMessageReplyMarkup",
            data={
                "chat_id": chat,
                "message_id": message_id,
                "reply_markup": self._reply_markup(feedback_id),
            },
            timeout=10,
        )
        if response.status_code >= 400:
            raise RuntimeError(response.text)

    def start(self) -> None:
        with self.start_lock:
            if self.started:
                return
            self.started = True
            Thread(target=self._poll, name="telegram-feedback", daemon=True).start()

    def _poll(self) -> None:
        while not self.stop_event.is_set():
            try:
                response = requests.get(
                    f"{self.api_url}/getUpdates",
                    params={
                        "offset": self.offset,
                        "timeout": 25,
                        "allowed_updates": json.dumps(["callback_query"]),
                    },
                    timeout=30,
                )
                response.raise_for_status()
                for update in response.json().get("result", []):
                    self.offset = max(self.offset, int(update["update_id"]) + 1)
                    callback = update.get("callback_query")
                    if callback:
                        self.process_callback(callback)
            except Exception:
                self.logger.exception("Failed to poll Telegram feedback")
                self.stop_event.wait(5)

    def process_callback(self, callback: dict[str, Any]) -> None:
        callback_id = callback.get("id")
        try:
            message = callback.get("message") or {}
            chat = str((message.get("chat") or {}).get("id", ""))
            if chat not in self.allowed_chats:
                raise ValueError("Feedback came from an unconfigured chat")

            prefix, feedback_id, label = str(callback.get("data", "")).split(":")
            if prefix != "feedback" or label not in ("good", "bad"):
                raise ValueError("Invalid feedback data")

            self._classify(feedback_id, label)
            requests.post(
                f"{self.api_url}/editMessageReplyMarkup",
                data={
                    "chat_id": chat,
                    "message_id": message["message_id"],
                    "reply_markup": self._reply_markup(feedback_id, label),
                },
                timeout=10,
            )
            self._answer_callback(callback_id, f"Saved to {label}")
        except Exception as error:
            self.logger.exception("Failed to process Telegram feedback")
            self._answer_callback(
                callback_id, f"Could not save feedback: {error}", alert=True
            )

    def _classify(self, feedback_id: str, label: str) -> None:
        if not feedback_id or any(
            character
            not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_"
            for character in feedback_id
        ):
            raise ValueError("Invalid feedback ID")

        source = self.feedback_directory / f"{feedback_id}.jpg"
        metadata = json.loads(
            (self.feedback_directory / f"{feedback_id}.json").read_text()
        )
        filename = Path(metadata["filename"]).name
        if not source.is_file() or not filename:
            raise FileNotFoundError("Feedback image not found")

        destination = self.data_root / label
        other = self.data_root / ("bad" if label == "good" else "good")
        destination.mkdir(parents=True, exist_ok=True)
        other.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination / filename)
        (destination / Path(filename).with_suffix(".json")).write_text(
            json.dumps(metadata)
        )
        (other / filename).unlink(missing_ok=True)
        (other / Path(filename).with_suffix(".json")).unlink(missing_ok=True)

    def _answer_callback(
        self, callback_id: Any, text: str, alert: bool = False
    ) -> None:
        if not callback_id:
            return
        try:
            requests.post(
                f"{self.api_url}/answerCallbackQuery",
                data={
                    "callback_query_id": callback_id,
                    "text": text,
                    "show_alert": json.dumps(alert),
                },
                timeout=10,
            )
        except Exception:
            self.logger.exception("Failed to answer Telegram callback")

    @staticmethod
    def _reply_markup(feedback_id: str, selected: str | None = None) -> str:
        good = "✅ Good" if selected == "good" else "👍 Good"
        bad = "✅ Bad" if selected == "bad" else "👎 Bad"
        return json.dumps(
            {
                "inline_keyboard": [
                    [
                        {"text": good, "callback_data": f"feedback:{feedback_id}:good"},
                        {"text": bad, "callback_data": f"feedback:{feedback_id}:bad"},
                    ]
                ]
            }
        )


_feedback_listeners: dict[tuple[str, Path], TelegramFeedbackListener] = {}
_feedback_listeners_lock = Lock()


def get_feedback_listener(
    token: str, chat: str, feedback_directory: Path = Path(".")
) -> TelegramFeedbackListener:
    data_root = feedback_directory.expanduser().resolve()
    with _feedback_listeners_lock:
        listener = _feedback_listeners.setdefault(
            (token, data_root), TelegramFeedbackListener(token, data_root)
        )
        listener.register_chat(chat)
        return listener


class TelegramExporter(WebhookExporter):
    telegram: ChatConfig
    alert_count: int
    feedback_listener: TelegramFeedbackListener

    def __init__(self, config: ChatConfig):
        self.telegram = config
        super().__init__(
            WebhookConfig(
                url=f"https://api.telegram.org/bot{config.token}/sendMediaGroup",
                token=config.token,
                confidence=config.confidence,
                crop_padding=config.crop_padding,
                export_rejected=config.export_rejected,
                data_type="binary",
                data_max=12_000_000,
                include_video=config.include_video,
                include_image=config.include_image,
                include_plot=config.include_plot,
                include_crop=config.include_crop,
                video_width=config.video_width,
                video_crf=config.video_crf,
                timeout=getattr(config, "timeout", None) or 60,
            )
        )
        self.alert_count = 0
        self.feedback_listener = get_feedback_listener(
            config.token, config.chat, config.feedback_directory
        )

    def get_media_and_files(
        self,
        best_detection: Detection,
        detections: list[Detection],
        validated: bool | None,
    ):
        files = {}
        media = []

        if self.telegram.include_image:
            image = get_image(best_detection.images.jpg)
            if self.config.data_max is not None:
                compressed = compress_jpg(best_detection.images.jpg, self.config.data_max)
                if compressed is not None:
                    image = compressed
            files["image"] = (
                get_timestamped_filename(best_detection),
                image,
                "image/jpeg",
            )
            media.append(
                {
                    "type": "photo",
                    "media": "attach://image",
                }
            )

        if self.telegram.include_plot:
            image = get_plot(best_detection)
            photo = get_image(image)
            if self.config.data_max is not None:
                compressed = compress_jpg(image, self.config.data_max)
                if compressed is not None:
                    photo = compressed
            files["photo"] = (
                get_timestamped_filename(best_detection),
                photo,
                "image/jpeg",
            )
            media.append(
                {
                    "type": "photo",
                    "media": "attach://photo",
                }
            )

        if self.telegram.include_crop and best_detection.images.crop_region:
            c = get_crop(best_detection)
            if c is not None:
                crop = get_image(c)
                if self.config.data_max is not None:
                    compressed = compress_jpg(c, self.config.data_max)
                    if compressed is not None:
                        crop = compressed
                files["crop"] = (
                    f"{get_timestamped_filename(best_detection).replace('.jpg', '_crop.jpg')}",
                    crop,
                    "image/jpeg",
                )
                media.append(
                    {
                        "type": "photo",
                        "media": "attach://crop",
                    }
                )

        if self.telegram.include_video:
            video = generate_mp4(
                detections,
                width=self.telegram.video_width,
                crf=self.telegram.video_crf,
                data_max=self.config.data_max,
                padding=self.telegram.crop_padding,
            )
            if video:
                files["video"] = (
                    f"{get_timestamped_filename(best_detection).replace('.jpg', '.mp4')}",
                    video,
                    "video/mp4",
                )
                media.append(
                    {
                        "type": "video",
                        "media": "attach://video",
                    }
                )

        if not media:
            fallback = get_plot(best_detection) if self.telegram.include_plot else best_detection.images.jpg
            files["image"] = (
                get_timestamped_filename(best_detection),
                get_image(fallback),
                "image/jpeg",
            )
            media.append(
                {
                    "type": "photo",
                    "media": "attach://image",
                }
            )

        self.alert_count += 1
        media[0]["caption"] = (
            f"{int(max_confidence(best_detection.confidence) * 100)}%{' ✅' if validated else ' ❌' if validated is False else ''}\n{round((detections[-1].date - detections[0].date).total_seconds())} second(s)"
        )

        payload = {
            "chat_id": self.telegram.chat,
            "disable_notification": self.alert_count % self.telegram.alert_every != 0,
            "media": json.dumps(media),
        }
        return payload, files

    def get_payload(
        self,
        best_detection: Detection,
        detections: list[Detection],
        validated: bool | None,
    ):
        payload, _ = self.get_media_and_files(best_detection, detections, validated)
        return payload

    def get_file(self, detection: Detection, detections: list[Detection]):
        _, files = self.get_media_and_files(detection, detections, None)
        return files

    def filtered_export(
        self,
        best_detection: Detection,
        detections: list[Detection],
        validated: bool | None,
    ):
        try:
            payload, files = self.get_media_and_files(
                best_detection, detections, validated
            )
            if not files or not payload:
                self.logger.error("Telegram notification has no media to send")
                return

            response = requests.post(
                self.config.url,
                data=payload,
                files=files,
                headers=self.get_headers(),
                timeout=self.config.timeout or 60,
            )
            if response.status_code >= 400:
                self.logger.error(
                    "Failed to send Telegram notification to chat %s (%s): %s",
                    self.telegram.chat,
                    response.status_code,
                    response.text,
                )
                return

            messages = response.json().get("result", [])
            if messages:
                self.logger.info(
                    "Successfully sent Telegram notification to chat %s (message_id=%s)",
                    self.telegram.chat,
                    messages[0].get("message_id"),
                )
                feedback_id = self.feedback_listener.save_detection(best_detection)
                self.feedback_listener.add_buttons(
                    self.telegram.chat, messages[0]["message_id"], feedback_id
                )
                self.feedback_listener.start()
        except Exception:
            self.logger.exception("Failed to send Telegram notification")
