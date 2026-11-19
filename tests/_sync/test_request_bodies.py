# Generated from tests/_async/test_request_bodies.py by scripts/unasync.py -- do not edit.
# Edit the async source and re-run: uv run python scripts/unasync.py

"""Bodies sent in the media type each operation declares, not always as JSON.

Forward reads ``POST /users/current/tokens`` and ``/users/current/password`` as
form fields and the data-file and approved-CLI uploads as multipart parts; a
JSON body to any of them is read as no fields at all ("'password' is required").
"""

from __future__ import annotations

import contextlib
from urllib.parse import parse_qs

import httpx
import pytest

from forward_sdk._sync.client import ForwardClient
from forward_sdk.errors import ForwardResponseError
from tests.conftest import Recorder, json_response

pytestmark = pytest.mark.anyio


def make_client(recorder: Recorder) -> ForwardClient:
    return ForwardClient(
        "https://forward.test",
        username="me@example.com",
        password="secret",
        rate_limit_rpm=None,
        network_id="101",
        transport=recorder.transport,
    )


def form_of(request: httpx.Request) -> dict[str, list[str]]:
    assert request.headers["Content-Type"] == "application/x-www-form-urlencoded"
    return parse_qs(request.content.decode())


def test_minting_an_api_token_sends_the_password_as_a_form_field() -> None:
    recorder = Recorder().add(
        "POST",
        "/api/users/current/tokens",
        json_response(
            {
                "token": {
                    "accessKey": "AK",
                    "name": "lab key",
                    "createdAt": "2026-01-01T00:00:00Z",
                },
                "secret": "S",
            }
        ),
    )
    with make_client(recorder) as client:
        minted = client.user_accounts.create_current_user_api_token(
            name="lab key", body={"password": "pa&ss=word"}
        )

    request = recorder.requests[-1]
    assert form_of(request) == {"password": ["pa&ss=word"]}
    assert request.url.params["name"] == "lab key"
    assert minted.secret == "S" and minted.token.access_key == "AK"


def test_changing_the_password_sends_form_fields() -> None:
    recorder = Recorder().add("POST", "/api/users/current/password", httpx.Response(204))
    with make_client(recorder) as client:
        client.user_accounts.change_current_user_password(
            body={"oldPassword": "a", "newPassword": "b"}
        )

    assert form_of(recorder.requests[-1]) == {"oldPassword": ["a"], "newPassword": ["b"]}


def test_a_multipart_upload_sends_its_parts() -> None:
    recorder = Recorder().add(
        "POST", "/api/approved-cli-commands", json_response({"commands": ["show version"]})
    )
    with make_client(recorder) as client:
        client.endpoint_profiles.update_approved_cli_commands(
            body={"file": ("approved.signed", b"signed-bytes", "application/octet-stream")}
        )

    request = recorder.requests[-1]
    assert request.headers["Content-Type"].startswith("multipart/form-data; boundary=")
    body = request.read()
    assert b'name="file"; filename="approved.signed"' in body and b"signed-bytes" in body


@pytest.mark.parametrize("body", ["password=x", ["password"]])
def test_a_form_body_that_is_not_a_mapping_is_refused_before_sending(body: object) -> None:
    recorder = Recorder()
    with make_client(recorder) as client:
        with pytest.raises(TypeError, match="mapping"):
            client.user_accounts.create_current_user_api_token(name="x", body=body)
    assert recorder.requests == []


def test_an_sso_user_mints_a_token_with_its_name_only() -> None:
    recorder = Recorder().add(
        "POST",
        "/api/users/current/tokens",
        json_response(
            {
                "token": {"accessKey": "AK", "name": "x", "createdAt": "2026-01-01T00:00:00Z"},
                "secret": "S",
            }
        ),
    )
    with make_client(recorder) as client:
        client.user_accounts.create_current_user_api_token(name="x", body=None)

    request = recorder.requests[-1]
    assert request.content == b"" and "Content-Type" not in request.headers
    assert request.url.params["name"] == "x"


def test_form_values_are_written_as_forward_reads_them() -> None:
    recorder = Recorder().add("POST", "/api/users/current/password", httpx.Response(204))
    with make_client(recorder) as client:
        client.user_accounts.change_current_user_password(
            body={"newPassword": "b", "logoutOthers": True, "unused": None, "tag": ["a", "b"]}
        )

    assert form_of(recorder.requests[-1]) == {
        "newPassword": ["b"],
        "logoutOthers": ["true"],
        "tag": ["a", "b"],
    }


def test_a_multipart_text_field_is_a_plain_field_and_a_list_repeats() -> None:
    recorder = Recorder().add("POST", "/api/data-files", json_response({}))
    with make_client(recorder) as client:
        # Only the request matters here; the empty reply is not a valid inference.
        with contextlib.suppress(ForwardResponseError):
            client.data_files.infer_data_file_schema(
                body={
                    "file": ("hosts.csv", b"a,b\n", "text/csv"),
                    "fileType": "CSV",
                    "headerRow": ["a", "b"],
                }
            )

    body = recorder.requests[-1].read()
    assert b'name="fileType"\r\n\r\nCSV\r\n' in body
    assert body.count(b'name="headerRow"\r\n\r\n') == 2
    assert b'name="file"; filename="hosts.csv"' in body


def test_a_mapping_part_is_sent_as_json() -> None:
    recorder = Recorder().add("POST", "/api/data-files", json_response({}))
    with make_client(recorder) as client:
        with contextlib.suppress(ForwardResponseError):
            client.data_files.add_data_file(
                body={"request": {"name": "hosts"}, "file": ("hosts.csv", memoryview(b"a,b\n"))}
            )

    body = recorder.requests[-1].read()
    assert b'name="request"\r\nContent-Type: application/json\r\n\r\n{"name": "hosts"}' in body
    assert b'filename="hosts.csv"' in body and b"a,b\n" in body


def test_a_multipart_body_without_a_file_part_is_refused_before_sending() -> None:
    recorder = Recorder()
    with make_client(recorder) as client:
        with pytest.raises(TypeError, match="file part"):
            client.data_files.infer_data_file_schema(body={"file": "a,b\n", "fileType": "CSV"})
    assert recorder.requests == []
