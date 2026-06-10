import logging
import os
import resource
import threading
import time

from aidetector.utils.onnx import setup_ort

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)


logger = logging.getLogger(__name__)
_RESTART_DELAY_SECONDS = 5


def _read_env_int(name: str, default: int = 0) -> int:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        parsed = int(value)
        return parsed if parsed >= 0 else default
    except ValueError:
        return default


def _get_rss_mb() -> float:
    # Prefer current RSS if psutil is available; otherwise fall back to ru_maxrss.
    try:
        import psutil

        return psutil.Process(os.getpid()).memory_info().rss / (1024 * 1024)
    except Exception:
        usage = resource.getrusage(resource.RUSAGE_SELF)
        rss = float(usage.ru_maxrss)
        if rss <= 0:
            return 0
        # macOS reports bytes, Linux reports kilobytes.
        if rss > 1024 * 1024 * 16:
            return rss / (1024 * 1024)
        return rss / 1024


def start() -> None:
    from aidetector.utils.config import config

    logger.info(f"Starting application with config: {config}")
    setup_ort(config)
    from aidetector.detection.manager import Manager

    manager = Manager.from_config(config)
    threads = manager.start()
    max_rss_mb = _read_env_int("AIDETECTOR_MAX_RSS_MB", 0)
    stop_watchdog = threading.Event()
    memory_limit_exceeded = threading.Event()

    def rss_watchdog() -> None:
        if max_rss_mb <= 0:
            return
        logger.info("Memory watchdog enabled with max RSS: %d MB", max_rss_mb)
        while not stop_watchdog.is_set():
            rss_mb = _get_rss_mb()
            if rss_mb > max_rss_mb:
                logger.error(
                    "RSS exceeded limit: %.1f MB > %d MB. Restarting process.",
                    rss_mb,
                    max_rss_mb,
                )
                memory_limit_exceeded.set()
                manager.stop()
                return
            stop_watchdog.wait(2)

    watchdog_thread = threading.Thread(target=rss_watchdog, daemon=True)
    watchdog_thread.start()
    try:
        while True:
            if memory_limit_exceeded.is_set():
                raise RuntimeError("Memory limit exceeded")

            active = False
            for thread in threads:
                thread.join(timeout=0.5)
                if thread.is_alive():
                    active = True

            if not active:
                break
    except KeyboardInterrupt:
        logger.info("Shutdown requested")
    finally:
        stop_watchdog.set()
        watchdog_thread.join(timeout=1)
        manager.stop()


def main():
    while True:
        try:
            start()
            return
        except KeyboardInterrupt:
            logger.info("Shutdown requested")
            return
        except Exception:
            logger.exception(
                "Application crashed, restarting in %ss", _RESTART_DELAY_SECONDS
            )
            time.sleep(_RESTART_DELAY_SECONDS)


if __name__ == "__main__":
    main()
