"""Graceful shutdown (SIGINT/SIGTERM) and the low-disk guard."""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import signal
from pathlib import Path
from typing import Callable

log = logging.getLogger(__name__)

_GIB = 1024**3


class ShutdownController:
    """First SIGINT/SIGTERM asks everything to stop after in-flight work; the second forces exit."""

    def __init__(self, *, force_exit: Callable[[int], None] = os._exit) -> None:
        self.event = asyncio.Event()
        self.reason: str | None = None
        self._signals = 0
        self._force_exit = force_exit
        self._installed: list[signal.Signals] = []

    @property
    def stopping(self) -> bool:
        return self.event.is_set()

    def request_stop(self, reason: str) -> None:
        """Ask for a clean stop (signal, --max-matches, --duration, write error...)."""
        if not self.event.is_set():
            self.reason = reason
            log.warning("Stopping: %s - finishing in-flight work", reason)
            self.event.set()

    def install(self) -> None:
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, self._on_signal, sig)
            self._installed.append(sig)

    def uninstall(self) -> None:
        loop = asyncio.get_running_loop()
        for sig in self._installed:
            loop.remove_signal_handler(sig)
        self._installed.clear()

    def _on_signal(self, sig: signal.Signals) -> None:
        self._signals += 1
        if self._signals == 1:
            self.request_stop(f"{sig.name} received (press Ctrl-C again to force)")
            return
        # Raw files are flushed per record and SQLite is in WAL mode, so a hard exit is safe.
        log.error("Second %s - exiting immediately", sig.name)
        self._force_exit(130)


def free_gb(path: Path) -> float:
    """Free space in GiB on the filesystem holding `path` (or its nearest existing parent)."""
    path = path.resolve()
    while not path.exists() and path != path.parent:
        path = path.parent
    return shutil.disk_usage(path).free / _GIB


class DiskGuard:
    """Tracks whether free space is below `min_gb`; logs only on state changes."""

    def __init__(self, path: Path, min_gb: float, *, free: Callable[[Path], float] = free_gb) -> None:
        self.path = path
        self.min_gb = min_gb
        self._free = free
        self.low = False
        self.last_free_gb: float | None = None

    def check(self) -> bool:
        """Re-measure; returns True when there is enough space."""
        self.last_free_gb = self._free(self.path)
        low = self.last_free_gb < self.min_gb
        if low and not self.low:
            log.error(
                "LOW DISK: %.1f GiB free in %s (minimum %.1f) - crawling paused", self.last_free_gb, self.path, self.min_gb
            )
        elif not low and self.low:
            log.warning("Disk space back to %.1f GiB - resuming", self.last_free_gb)
        self.low = low
        return not low
