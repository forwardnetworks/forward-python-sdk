"""A body the SDK cannot parse must still be a Forward error."""

from __future__ import annotations

import httpx
import pytest
from pydantic import ValidationError

from forward_sdk._http import parse_error_body, raise_for_response
from forward_sdk.errors import (
    ForwardAPIError,
    ForwardAuthError,
    ForwardError,
    ForwardNotFoundError,
    ForwardResponseError,
)
from forward_sdk.models import Network
from forward_sdk.telemetry import Counters, CounterSnapshot


class TestParseFailuresStayInTheTree:
    """`except ForwardError` has to cover parsing, or it covers almost nothing.

    Roughly 546 fields across the generated models are required. Any Forward
    that omits one, an older release or a proxy that rewrites a body, would
    otherwise raise pydantic's ValidationError, which is not a ForwardError and
    travels straight through every consumer's error handling at once. A sync
    dies with a validation traceback in a job log rather than a failure it could
    classify and report.
    """

    def test_a_missing_required_field_raises_a_forward_error(self) -> None:
        with pytest.raises(ForwardError) as caught:
            Network.model_validate({"id": "n1", "name": "Prod"})

        assert isinstance(caught.value, ForwardResponseError)

    def test_it_is_not_a_pydantic_error(self) -> None:
        """Catching ValidationError alone must no longer be how you handle this."""
        with pytest.raises(ForwardResponseError):
            Network.model_validate({"id": "n1"})

    def test_the_body_and_the_detail_both_survive(self) -> None:
        """The raw payload is what tells you whether the model or Forward is wrong."""
        body = {"id": "n1", "name": "Prod"}
        with pytest.raises(ForwardResponseError) as caught:
            Network.model_validate(body)

        error = caught.value
        assert error.payload == body
        assert error.model_name == "Network"
        assert isinstance(error.__cause__, ValidationError)

    def test_the_message_names_the_model_and_the_fields(self) -> None:
        """An operator reading a job log should not need the traceback."""
        with pytest.raises(ForwardResponseError) as caught:
            Network.model_validate({"id": "n1"})

        message = str(caught.value)
        assert "Network" in message
        assert "orgId" in message

    def test_a_valid_body_is_untouched(self) -> None:
        network = Network.model_validate({"id": "n1", "name": "Prod", "orgId": "7"})
        assert network.id == "n1"

    def test_tolerance_in_the_other_direction_is_unchanged(self) -> None:
        """Unknown fields are still kept; this strictness is only about missing ones."""
        network = Network.model_validate(
            {"id": "n1", "name": "Prod", "orgId": "7", "somethingNew": 1}
        )
        assert network.model_dump(by_alias=True)["somethingNew"] == 1


class TestAnUnreadableErrorBody:
    def test_does_not_mask_the_failure_it_describes(self) -> None:
        """The riskiest place for this change: parsing a body while handling an error.

        `parse_error_body` deliberately gives up rather than raise, because the
        HTTP failure matters more than the shape of its explanation. It now has
        to give up on a ForwardResponseError too, since the models raise that
        instead of a ValidationError.
        """
        response = httpx.Response(
            403,
            json={"message": ["not", "a", "string"], "httpMethod": 7},
            request=httpx.Request("GET", "https://forward.test/api/networks"),
        )
        assert parse_error_body(response) is None


class TestCounterSnapshotIsLoggable:
    """A snapshot's destination is a log line or a metrics sink.

    It carried `as_dict()` from the start, but nothing signalled that, so a
    consumer trying `dict(snapshot)` hit a TypeError and concluded there was no
    way to serialize it. Reading as a mapping makes the obvious attempt work.
    """

    def test_converts_to_a_dict(self) -> None:
        counters = Counters()
        counters.increment("http_attempts", 3)
        snapshot = counters.snapshot()

        assert dict(snapshot)["http_attempts"] == 3
        assert "http_attempts" in snapshot
        assert len(snapshot) == len(snapshot.as_dict())

    def test_unpacks_as_keyword_arguments(self) -> None:
        def sink(**fields: float) -> float:
            return fields["http_attempts"]

        assert sink(**CounterSnapshot(http_attempts=7)) == 7

    def test_an_unknown_counter_raises_keyerror(self) -> None:
        with pytest.raises(KeyError):
            CounterSnapshot()["not_a_counter"]


class TestWhatKindOfRefusalA404Or401Is:
    """A 404 can be a route Forward does not serve, a 401 a locked account.

    Both look like the ordinary kind of the same status, and acting on the
    ordinary reading is wrong: a "not found" DELETE did not delete anything,
    and a locked account is not fixed by a new password.
    """

    @staticmethod
    def raised(status: int, message: str) -> ForwardAPIError:
        response = httpx.Response(
            status,
            json={"message": message, "status": str(status)},
            request=httpx.Request("GET", "https://forward.test/api/x"),
        )
        with pytest.raises(ForwardAPIError) as caught:
            raise_for_response(response, operation=None)
        return caught.value

    def test_a_route_forward_does_not_map(self) -> None:
        error = self.raised(404, "No endpoint GET /api/backups.")
        assert isinstance(error, ForwardNotFoundError)
        assert error.endpoint_not_served is True

    def test_a_missing_resource_is_not_a_missing_route(self) -> None:
        assert self.raised(404, "Network 7 not found").endpoint_not_served is False

    @pytest.mark.parametrize("message", ["User account is locked: alice", "Account is locked"])
    def test_a_locked_account(self, message: str) -> None:
        error = self.raised(401, message)
        assert isinstance(error, ForwardAuthError)
        assert error.account_locked is True

    def test_a_wrong_credential_is_not_a_locked_account(self) -> None:
        assert self.raised(401, "Bad credentials").account_locked is False

    def test_the_status_matters_as_much_as_the_words(self) -> None:
        assert self.raised(403, "Account is locked").account_locked is False
