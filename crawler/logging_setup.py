"""Console + rotating file logging with API key masking (DESIGN §4, §7)."""

from __future__ import annotations

import logging
import re
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

KEY_PATTERN = re.compile(r"RGAPI-[0-9a-fA-F-]+")
KEY_MASK = "RGAPI-****"

LOG_FILE_NAME = "crawler.log"
LOG_FILE_MAX_BYTES = 10 * 1024 * 1024
LOG_FILE_BACKUPS = 5

# Third-party loggers that are noisy at INFO (httpx logs every request line).
_QUIET_LOGGERS = ("httpx", "httpcore", "asyncio")


def mask_secrets(text: str) -> str:
    """Replace anything that looks like a Riot API key with `RGAPI-****`."""
    return KEY_PATTERN.sub(KEY_MASK, text)


class SecretMaskingFilter(logging.Filter):
    """Masks API keys in the message, its args and exception/stack text of a record.

    Attach it to handlers (not loggers) so it also covers records propagated from
    child loggers.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = self._mask_value(record.msg)
        if isinstance(record.args, dict):
            record.args = {k: self._mask_value(v) for k, v in record.args.items()}
        elif isinstance(record.args, tuple):
            record.args = tuple(self._mask_value(a) for a in record.args)
        if record.exc_info and not record.exc_text:
            # Pre-render the traceback so formatters reuse the masked text.
            record.exc_text = logging.Formatter().formatException(record.exc_info)
        if record.exc_text:
            record.exc_text = mask_secrets(record.exc_text)
        if record.stack_info:
            record.stack_info = mask_secrets(record.stack_info)
        return True

    @staticmethod
    def _mask_value(value: object) -> object:
        if isinstance(value, str):
            return mask_secrets(value)
        if isinstance(value, (int, float, bool)) or value is None:
            return value
        # Arbitrary objects (exceptions, URLs, dicts...): only replace if their text leaks a key.
        text = str(value)
        masked = mask_secrets(text)
        return masked if masked != text else value


class _StderrHandler(logging.StreamHandler):
    """Writes to whatever `sys.stderr` is at emit time (e.g. after a progress bar hooks it)."""

    @property
    def stream(self):  # type: ignore[override]
        return sys.stderr

    @stream.setter
    def stream(self, value) -> None:
        pass


def setup_logging(log_dir: Path, *, console_level: int = logging.INFO) -> None:
    """Configure the root logger: compact console (INFO) + rotating file (DEBUG).

    Safe to call more than once; handlers installed by a previous call are replaced.
    """
    log_dir.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    for handler in list(root.handlers):
        if getattr(handler, "_crawler_handler", False):
            root.removeHandler(handler)
            handler.close()
    root.setLevel(logging.DEBUG)

    mask = SecretMaskingFilter()

    console = _StderrHandler()
    console.setLevel(console_level)
    console.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(message)s", "%H:%M:%S"))

    file_handler = RotatingFileHandler(
        log_dir / LOG_FILE_NAME,
        maxBytes=LOG_FILE_MAX_BYTES,
        backupCount=LOG_FILE_BACKUPS,
        encoding="utf-8",
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)-7s %(name)s [%(threadName)s] %(message)s")
    )

    for handler in (console, file_handler):
        handler.addFilter(mask)
        handler._crawler_handler = True  # type: ignore[attr-defined]
        root.addHandler(handler)

    for name in _QUIET_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)
