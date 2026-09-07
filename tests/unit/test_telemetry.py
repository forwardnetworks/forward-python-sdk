"""Counter snapshots and the derived request rate."""

from __future__ import annotations

import pytest

from forward_sdk.telemetry import Counters, CounterSnapshot


def _snapshot(attempts: int, *, first: float, last: float) -> CounterSnapshot:
    return CounterSnapshot(http_attempts=attempts, first_attempt_at=first, last_attempt_at=last)


class TestAttemptsPerMinute:
    """The rate is attempts over the window they span.

    Callers compare this against Forward's published ceiling and fail builds on
    it, so an overstatement is a false alarm on a rate that was within the
    limit. The window runs first attempt to last, which is one interval fewer
    than there are attempts.
    """

    def test_counts_intervals_not_attempts(self) -> None:
        # Two attempts one second apart is one request per second, not two.
        assert _snapshot(2, first=1100.0, last=1101.0).attempts_per_minute == 60.0

    def test_steady_rate_over_a_full_minute(self) -> None:
        # 61 attempts at one-second spacing spans 60 seconds and 60 intervals.
        assert _snapshot(61, first=1000.0, last=1060.0).attempts_per_minute == 60.0

    @pytest.mark.parametrize("attempts", [0, 1])
    def test_zero_until_two_attempts_span_a_window(self, attempts: int) -> None:
        # The docstring's promise, held by the rule rather than by a zero window.
        assert _snapshot(attempts, first=1100.0, last=1100.0).attempts_per_minute == 0.0

    def test_one_attempt_with_a_nonzero_window_is_still_zero(self) -> None:
        # Cannot arise from the transport, but the rule must not divide by a
        # window a single attempt did not measure.
        assert _snapshot(1, first=1100.0, last=1160.0).attempts_per_minute == 0.0

    def test_simultaneous_attempts_do_not_divide_by_zero(self) -> None:
        assert _snapshot(50, first=1100.0, last=1100.0).attempts_per_minute == 0.0

    def test_never_overstates_a_rate_at_a_gate_sample_floor(self) -> None:
        # A consumer gate waits for 20 attempts over 10s before judging. Dividing
        # by attempts would report 120/min against a true 114/min.
        rate = _snapshot(20, first=1000.0, last=1010.0).attempts_per_minute
        assert rate == pytest.approx(114.0)


class TestElapsedSeconds:
    def test_zero_when_no_request_has_been_sent(self) -> None:
        assert CounterSnapshot().elapsed_seconds == 0.0

    def test_spans_first_to_last(self) -> None:
        assert _snapshot(3, first=1100.0, last=1130.5).elapsed_seconds == 30.5

    def test_never_negative_if_the_clock_moves_backwards(self) -> None:
        assert _snapshot(3, first=1130.0, last=1100.0).elapsed_seconds == 0.0

    def test_a_zero_timestamp_means_unset_not_the_epoch(self) -> None:
        # Both fields default to 0.0 to mean "no request yet", so a window is
        # only measured once the transport has stamped them.
        assert _snapshot(5, first=0.0, last=60.0).elapsed_seconds == 0.0


class TestCounters:
    def test_snapshot_reflects_increments(self) -> None:
        counters = Counters()
        counters.increment("http_attempts", 3)
        counters.increment("throttle_sleep_seconds", 1.5)
        snapshot = counters.snapshot()
        assert snapshot.http_attempts == 3
        assert snapshot.throttle_sleep_seconds == 1.5

    def test_unknown_counter_is_rejected(self) -> None:
        with pytest.raises(KeyError):
            Counters().increment("not_a_counter")
