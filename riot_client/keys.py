"""API key holder and the wait-for-a-new-key loop used when Riot rejects the key (DESIGN §7)."""

from __future__ import annotations

import asyncio
import logging
import shutil
import subprocess
import sys
from typing import Callable, TextIO

log = logging.getLogger(__name__)


class KeyProvider:
    """Holds the current API key and knows how to look for a replacement.

    `reload` returns the key currently configured (e.g. by re-reading `.env`); it may
    raise or return `None` while the file is being edited. The key itself is never logged.
    """

    def __init__(
        self,
        key: str,
        reload: Callable[[], str | None],
        *,
        poll_seconds: float = 30.0,
        stdin: TextIO | None = None,
        notify: bool = True,
    ) -> None:
        self._key = key
        self._reload = reload
        self.poll_seconds = poll_seconds
        self._stdin = stdin if stdin is not None else sys.stdin
        self._notify = notify
        self._reload_requested = asyncio.Event()

    @property
    def key(self) -> str:
        return self._key

    def set_key(self, key: str) -> None:
        self._key = key

    def request_reload(self) -> None:
        """Re-read the key now instead of at the next poll (what pressing Enter does)."""
        self._reload_requested.set()

    async def wait_for_new_key(self, rejected: str) -> str:
        """Wait until the configured key differs from `rejected`, or a reload is forced.

        A forced reload (Enter) returns the configured key even if unchanged, so a key that
        was rejected by mistake can be re-validated.
        """
        listens = self._listen_for_enter()
        log.error(
            "Riot rejected the API key (401/403). Crawling is PAUSED. Put a new RIOT_API_KEY into "
            ".env; it is re-read every %.0f s%s.",
            self.poll_seconds,
            " or when you press Enter" if listens else "",
        )
        self._send_notification()
        self._reload_requested.clear()
        try:
            while True:
                forced = self._reload_requested.is_set()
                self._reload_requested.clear()
                candidate = self._try_reload()
                if candidate and (candidate != rejected or forced):
                    log.info("Found %s API key, validating", "a new" if candidate != rejected else "the same")
                    return candidate
                if forced:
                    log.warning("Reload requested but .env still has no new key")
                try:
                    await asyncio.wait_for(self._reload_requested.wait(), self.poll_seconds)
                except TimeoutError:
                    pass
        finally:
            if listens:
                self._stop_listening()

    def _try_reload(self) -> str | None:
        try:
            key = self._reload()
        except Exception as exc:  # noqa: BLE001 - a half-edited .env must not stop the wait
            log.warning("Could not re-read the key: %s", exc)
            return None
        return key.strip() if key else None

    def _listen_for_enter(self) -> bool:
        try:
            if not self._stdin.isatty():
                return False
            asyncio.get_running_loop().add_reader(self._stdin.fileno(), self._on_stdin)
        except (ValueError, OSError, NotImplementedError, AttributeError):
            return False
        return True

    def _on_stdin(self) -> None:
        line = self._stdin.readline()
        if line == "":  # EOF: stop listening so the loop does not spin
            self._stop_listening()
            return
        self.request_reload()

    def _stop_listening(self) -> None:
        try:
            asyncio.get_running_loop().remove_reader(self._stdin.fileno())
        except (ValueError, OSError, NotImplementedError, AttributeError):
            pass

    def _send_notification(self) -> None:
        if not self._notify or shutil.which("notify-send") is None:
            return
        try:
            subprocess.Popen(
                ["notify-send", "LoL crawler paused", "Riot API key rejected - update RIOT_API_KEY in .env"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except OSError:
            pass
