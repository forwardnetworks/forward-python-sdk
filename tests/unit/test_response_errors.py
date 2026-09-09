"""A body the SDK cannot parse must still be a Forward error."""

from __future__ import annotations

import httpx
import pytest
from pydantic import ValidationError

from forward_sdk._http import parse_error_body
from forward_sdk.errors import ForwardError, ForwardResponseError
from forward_sdk.models import Network


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
