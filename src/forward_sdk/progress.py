"""Progress callbacks for the calls that take long enough to look hung.

A callback is a plain function, called synchronously from the SDK, so it must
be quick. It receives bytes done so far and the total, or ``None`` when the
total is unknown (a download whose server sent no length). A callback that
raises is logged and ignored: a bug in a status line must not abort a
multi-gigabyte transfer, the same rule the client's hooks follow.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

__all__ = ["ByteProgress", "ProgressCallback", "report"]

logger = logging.getLogger("forward_sdk")

#: ``callback(done_bytes, total_bytes_or_None)``.
ProgressCallback = Callable[[int, int | None], None]


def report(callback: ProgressCallback | None, done: int, total: int | None) -> None:
    """Call ``callback`` if given, never letting it raise into the transfer."""
    if callback is None:
        return
    try:
        callback(done, total)
    except Exception:
        logger.warning("forward_sdk progress callback raised; ignoring", exc_info=True)


class ByteProgress:
    """Accumulates byte counts and reports the running total.

    One instance covers one transfer, however many files or chunks it spans.
    """

    def __init__(self, callback: ProgressCallback | None, total: int | None) -> None:
        self._callback = callback
        self.total = total
        self.done = 0

    def add(self, count: int) -> None:
        self.done += count
        report(self._callback, self.done, self.total)
