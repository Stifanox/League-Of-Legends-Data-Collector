"""Tests for crawler.shutdown."""

from __future__ import annotations

import asyncio
import os
import signal

from crawler.shutdown import DiskGuard, ShutdownController, free_gb


def test_first_signal_requests_stop_second_forces_exit():
    exits = []

    async def scenario():
        controller = ShutdownController(force_exit=exits.append)
        controller.install()
        try:
            os.kill(os.getpid(), signal.SIGINT)
            await asyncio.wait_for(controller.event.wait(), 1)
            first = (controller.stopping, list(exits))
            os.kill(os.getpid(), signal.SIGTERM)
            for _ in range(100):
                if exits:
                    break
                await asyncio.sleep(0.01)
            return first, controller.reason
        finally:
            controller.uninstall()

    (stopping, exits_after_first), reason = asyncio.run(scenario())
    assert stopping and exits_after_first == []
    assert exits == [130]
    assert reason.startswith("SIGINT")


def test_request_stop_keeps_first_reason():
    async def scenario():
        controller = ShutdownController()
        controller.request_stop("max matches reached")
        controller.request_stop("duration reached")
        return controller.stopping, controller.reason

    assert asyncio.run(scenario()) == (True, "max matches reached")


def test_disk_guard_transitions(tmp_path, caplog):
    free = iter([10.0, 1.5, 1.0, 3.0])
    guard = DiskGuard(tmp_path, 2, free=lambda _: next(free))
    assert [guard.check() for _ in range(4)] == [True, False, False, True]
    messages = [r.getMessage() for r in caplog.records]
    assert sum("LOW DISK" in m for m in messages) == 1
    assert sum("resuming" in m for m in messages) == 1


def test_free_gb_for_missing_path(tmp_path):
    assert free_gb(tmp_path / "not" / "yet" / "created") > 0
