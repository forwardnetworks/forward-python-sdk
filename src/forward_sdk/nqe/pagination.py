"""Safety rules for paging through NQE results.

Paging is offset-based, and several things can go wrong that a naive loop turns
into an infinite one or an out-of-memory kill: a server that keeps returning the
same page, one that stops early, or a query whose result set is far larger than
the caller expected. This module is the pure decision logic for those cases, so
the rules are testable without a server and shared by every paging path.

The specific failure modes here were learned in production by the Forward NetBox
plugin; they are not hypothetical.
"""

from __future__ import annotations

import json
import time
from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum
from typing import Any

from forward_sdk.errors import ForwardPaginationError, ForwardTimeoutError

__all__ = ["Decision", "PageGuards", "PageTracker"]

#: Forward's maximum rows per page. Requesting more is rejected.
MAX_PAGE_SIZE = 10_000
DEFAULT_PAGE_SIZE = 1_000


class Decision(Enum):
    """What to do after a page arrives."""

    CONTINUE = "continue"
    DONE = "done"


@dataclass(frozen=True, slots=True)
class PageGuards:
    """Limits applied while paging.

    Attributes:
        max_pages: Stop after this many requests.
        max_rows: Stop after this many rows. Guards memory: the page ceiling
            alone would permit tens of millions of rows.
        repeat_limit: How many identical full pages to tolerate before
            concluding that paging is not advancing.
        deadline: A ``time.monotonic()`` value after which to stop.
    """

    max_pages: int = 5_000
    max_rows: int = 2_000_000
    repeat_limit: int = 25
    deadline: float | None = None

    def __post_init__(self) -> None:
        if self.max_pages < 1 or self.max_rows < 1:
            raise ValueError("max_pages and max_rows must be positive")


class PageTracker:
    """Tracks paging progress and decides when to stop.

    Feed each page to :meth:`observe`; it returns whether to continue and raises
    when continuing would be unsafe.
    """

    __slots__ = (
        "_guards",
        "_last_signature",
        "_page_size",
        "_pages",
        "_repeats",
        "_rows",
        "_total",
    )

    def __init__(
        self, guards: PageGuards | None = None, *, page_size: int = DEFAULT_PAGE_SIZE
    ) -> None:
        if page_size < 1 or page_size > MAX_PAGE_SIZE:
            raise ValueError(f"page_size must be between 1 and {MAX_PAGE_SIZE}")
        self._guards = guards or PageGuards()
        self._page_size = page_size
        self._rows = 0
        self._pages = 0
        self._total: int | None = None
        self._last_signature: tuple[int, str, str] | None = None
        self._repeats = 0

    @property
    def rows(self) -> int:
        return self._rows

    @property
    def pages(self) -> int:
        return self._pages

    @property
    def offset(self) -> int:
        """The offset to request next."""
        return self._rows

    @property
    def page_size(self) -> int:
        return self._page_size

    @property
    def total(self) -> int | None:
        """Total rows the server reported, if it reported one."""
        return self._total

    def check_deadline(self) -> None:
        if self._guards.deadline is not None and time.monotonic() > self._guards.deadline:
            raise ForwardTimeoutError(
                f"deadline exceeded after {self._rows} rows across {self._pages} pages"
            )

    def observe(self, page: Sequence[Any], total: int | None = None) -> Decision:
        """Record a page and decide whether to request another.

        Args:
            page: The rows in this page.
            total: The server's reported total, when it sends one.

        Raises:
            ForwardPaginationError: If paging cannot safely continue.
            ForwardTimeoutError: If the deadline has passed.
        """
        self.check_deadline()
        self._pages += 1
        if total is not None:
            self._total = total

        if not page:
            # An empty page before the promised total means the server stopped
            # short. Returning silently would hand back a truncated result set
            # that looks complete.
            if self._total is not None and self._rows < self._total:
                raise ForwardPaginationError(
                    f"paging ended early: got {self._rows} of {self._total} rows",
                    rows=self._rows,
                    pages=self._pages,
                )
            return Decision.DONE

        self._track_repeats(page)
        self._rows += len(page)

        if self._total is not None and self._rows >= self._total:
            return Decision.DONE
        if self._total is None and len(page) < self._page_size:
            # Without a total, a short page is the only end-of-results signal.
            return Decision.DONE
        if self._rows >= self._guards.max_rows:
            raise ForwardPaginationError(
                f"row limit reached: {self._rows} rows (max_rows={self._guards.max_rows}). "
                "Narrow the query or raise the limit.",
                rows=self._rows,
                pages=self._pages,
            )
        if self._pages >= self._guards.max_pages:
            raise ForwardPaginationError(
                f"page limit reached: {self._pages} pages (max_pages={self._guards.max_pages}). "
                "Use a larger page size or narrow the query.",
                rows=self._rows,
                pages=self._pages,
            )
        return Decision.CONTINUE

    def _track_repeats(self, page: Sequence[Any]) -> None:
        """Detect a server that returns the same full page indefinitely.

        Only full pages are compared: a repeated short page is normal at the end
        of a result set, whereas a full page that never advances means the offset
        is being ignored, and looping would never terminate.

        A page whose first and last rows are identical is skipped, because it
        carries no evidence either way. ``select {n: 1}`` gives every row the
        same value, so every page looks the same whether the offset advanced or
        not, and counting those produced a false stall: the guard fired at
        ``repeat_limit * page_size`` rows on a query that was paging perfectly
        well, and reported it as the server not advancing. An integration read
        that as a server-side row cap, which it is not.

        The cost is that a genuinely stalled server returning uniform rows is not
        caught here. ``max_rows``, ``max_pages`` and the reported total still
        bound it, so it terminates; it is simply not diagnosed.
        """
        if len(page) < self._page_size:
            self._last_signature = None
            self._repeats = 0
            return

        first, last = _fingerprint(page[0]), _fingerprint(page[-1])
        if first == last:
            self._last_signature = None
            self._repeats = 0
            return

        signature = (len(page), first, last)
        if signature == self._last_signature:
            self._repeats += 1
            if self._repeats >= self._guards.repeat_limit:
                raise ForwardPaginationError(
                    f"paging is not advancing: {self._repeats} identical pages of "
                    f"{self._page_size} rows at offset {self._rows}. Forward appears to "
                    "be ignoring the offset. If the query selects a constant, so that "
                    "rows are genuinely identical, raise repeat_limit instead.",
                    rows=self._rows,
                    pages=self._pages,
                )
        else:
            self._last_signature = signature
            self._repeats = 0


def _fingerprint(row: Any) -> str:
    try:
        return json.dumps(row, sort_keys=True, default=str)
    except (TypeError, ValueError):
        return repr(row)
