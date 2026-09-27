import logging
import multiprocessing
import os
import pathlib
import sys
import time
from pathlib import Path

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)


logger = logging.getLogger(__name__)
_RESTART_DELAY_SECONDS = 5
_WATCH_SECONDS = 2


def _set_working_directory() -> None:
    if getattr(sys, "frozen", False):
        os.chdir(Path(sys.executable).resolve().parent)


def _patch_windows_path_checkpoints() -> None:
    if os.name != "nt":
        pathlib.WindowsPath = pathlib.PosixPath


def _run_command() -> bool:
    if len(sys.argv) < 2 or sys.argv[1] not in ("train-feedback", "review-feedback"):
        return False

    command = sys.argv[1]
    _set_working_directory()
    _patch_windows_path_checkpoints()
    sys.argv = [sys.argv[0], *sys.argv[2:]]
    if command == "review-feedback":
        from aidetector.review import main as review_feedback

        review_feedback()
    else:
        from aidetector.training import main as train_feedback

        train_feedback()
    return True


def _config_revision(path: Path) -> bytes | None:
    # The content, not the modification time, so saving without changes does
    # not restart.
    try:
        return path.read_bytes()
    except FileNotFoundError:
        return None


def start() -> bool:
    """Runs the detectors until config.json changes (returns True), all file
    sources are done (returns False) or a detector thread stops (raises)."""
    _set_working_directory()
    _patch_windows_path_checkpoints()

    config_path = Path("config.json").resolve()
    from aidetector.utils.config import config
    from aidetector.utils.onnx import setup_ort

    # Read after loading, which may add "$schema" to the file.
    revision = _config_revision(config_path)

    logger.info(f"Starting application with config: {config}")
    setup_ort(config)
    from aidetector.detection.manager import Manager

    manager = Manager.from_config(config)
    # The healthcheck thread comes last and never stops by itself.
    threads = manager.start()[: len(manager.detectors)]
    try:
        while True:
            time.sleep(_WATCH_SECONDS)
            if _config_revision(config_path) != revision:
                logger.info("config.json changed, restarting the detector")
                return True
            stopped = [thread for thread in threads if not thread.is_alive()]
            if stopped and not manager.is_streaming():
                if len(stopped) == len(threads):
                    logger.info("All sources are done")
                    return False
                continue
            if stopped:
                # Stream loaders reconnect by themselves, so a stopped detector
                # thread means it crashed.
                raise RuntimeError(
                    f"Detector stopped unexpectedly: {[t.name for t in stopped]}"
                )
    finally:
        manager.stop()


def _restart() -> None:
    """Starts the program again as a new process, so the new config, models and
    Telegram services start from a clean state."""
    if getattr(sys, "frozen", False):
        # A PyInstaller onefile build keeps its environment, so the new process
        # reuses the unpacked files instead of extracting them again.
        arguments = [sys.executable, *sys.argv[1:]]
    else:
        arguments = [sys.executable, *sys.orig_argv[1:]]
    logger.info("Restarting: %s", " ".join(arguments))
    logging.shutdown()
    os.execv(sys.executable, arguments)


def main():
    multiprocessing.freeze_support()
    if _run_command():
        return
    try:
        if not start():
            return
    except KeyboardInterrupt:
        logger.info("Shutdown requested")
        return
    except Exception:
        logger.exception(
            "Application crashed, restarting in %ss", _RESTART_DELAY_SECONDS
        )
        try:
            time.sleep(_RESTART_DELAY_SECONDS)
        except KeyboardInterrupt:
            logger.info("Shutdown requested")
            return
    _restart()


if __name__ == "__main__":
    main()
