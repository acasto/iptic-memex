from __future__ import annotations

import threading
from typing import Callable, Optional, List


class CancellationToken:
    """Lightweight, thread-safe cancellation token for a single turn/run.

    - cancel(reason) marks the token cancelled and runs registered cleanups once.
    - is_cancelled() can be polled cooperatively by long-running code.
    - register_cleanup(fn) attaches a best-effort cleanup callback.
    """

    def __init__(self) -> None:
        self._ev = threading.Event()
        self._reason: Optional[str] = None
        self._cleanups: List[Callable[[], None]] = []
        self._lock = threading.Lock()

    def cancel(self, reason: Optional[str] = None) -> None:
        with self._lock:
            if not self._ev.is_set():
                self._reason = reason
                self._ev.set()
                # Run cleanups best-effort
                for fn in list(self._cleanups):
                    try:
                        fn()
                    except Exception:
                        pass

    def is_cancelled(self) -> bool:
        return self._ev.is_set()

    def reason(self) -> Optional[str]:
        return self._reason

    def register_cleanup(self, fn: Callable[[], None]) -> None:
        with self._lock:
            # If already cancelled, run immediately
            if self._ev.is_set():
                try:
                    fn()
                except Exception:
                    pass
                return
            self._cleanups.append(fn)

