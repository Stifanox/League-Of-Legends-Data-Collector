"""An asyncio event loop with a virtual clock, so tests with long sleeps run instantly.

When nothing is ready to run, the loop jumps its clock straight to the next scheduled
timer instead of blocking. `asyncio.sleep`, `wait_for` and `loop.time()` all follow it.
"""

from __future__ import annotations

import asyncio
import selectors
from typing import Any, Coroutine, TypeVar

T = TypeVar("T")

START_TIME = 1000.0


class _VirtualSelector(selectors.BaseSelector):
    def __init__(self) -> None:
        self._real = selectors.DefaultSelector()
        self.loop: VirtualTimeLoop | None = None

    def register(self, fileobj, events, data=None):
        return self._real.register(fileobj, events, data)

    def unregister(self, fileobj):
        return self._real.unregister(fileobj)

    def modify(self, fileobj, events, data=None):
        return self._real.modify(fileobj, events, data)

    def get_map(self):
        return self._real.get_map()

    def close(self) -> None:
        self._real.close()

    def select(self, timeout=None):
        ready = self._real.select(0)
        if ready:
            return ready
        if timeout is None:
            raise RuntimeError("virtual loop deadlock: nothing scheduled and nothing ready")
        self.loop.advance(max(timeout, 0.0))
        return []


class VirtualTimeLoop(asyncio.SelectorEventLoop):
    def __init__(self) -> None:
        self._virtual_now = START_TIME
        selector = _VirtualSelector()
        super().__init__(selector)
        selector.loop = self

    def time(self) -> float:
        return self._virtual_now

    def advance(self, seconds: float) -> None:
        self._virtual_now += seconds


def run(coro: Coroutine[Any, Any, T]) -> T:
    """Run `coro` to completion on a fresh virtual-time loop."""
    loop = VirtualTimeLoop()
    try:
        return loop.run_until_complete(coro)
    finally:
        pending = [t for t in asyncio.all_tasks(loop) if not t.done()]
        for task in pending:
            task.cancel()
        if pending:
            loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
        loop.close()


def now() -> float:
    """Elapsed virtual seconds since the loop started."""
    return asyncio.get_running_loop().time() - START_TIME
