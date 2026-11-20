"""``forward_sdk.snapshot_package``: sources.json and log redaction.

The fixture is the Go SDK's synthetic ``testdata/snapshot_package/sources.json``;
expectations come from what each field is documented to mean.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from forward_sdk.errors import ForwardConfigurationError
from forward_sdk.snapshot_package import parse_sources, read_sources, redact_log

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "snapshot_package" / "sources.json"


def test_the_index_names_each_source_and_keeps_what_it_does_not_model() -> None:
    sources = parse_sources(FIXTURE.read_text(encoding="utf-8"))

    assert [s.name for s in sources.sources] == ["k8s1", "sw1"]
    k8s = sources.source("K8S1")  # case-insensitive
    assert k8s is not None
    assert k8s.created_by == "k8s_cni_control_plane_api"
    assert k8s.collection_port == 6443 and k8s.error == "NONE"
    assert k8s.files == ("k8s1,acl_conf.txt", "k8s1,k8s_cni_nodes.json", "k8s1,metadata.txt")
    assert k8s.raw["futureField"] == {"a": 1}
    assert sources.source("nope") is None


def test_devices_without_snapshots_are_merged_sorted_and_unique() -> None:
    sources = parse_sources(FIXTURE.read_text(encoding="utf-8"))

    # k8s1_w1 is listed by both sources and appears once.
    assert sources.devices_without_snapshots() == ["k8s1_cp1", "k8s1_default_web-a", "k8s1_w1"]


def test_a_package_is_read_from_its_top_level_sources_json(tmp_path: Path) -> None:
    package = tmp_path / "package.zip"
    with zipfile.ZipFile(package, "w") as zipped:
        zipped.writestr("sources.json", FIXTURE.read_bytes())
        zipped.writestr("k8s1,metadata.txt", "x")

    assert read_sources(package).source("sw1") is not None


def test_a_package_without_sources_json_or_not_a_zip_is_refused(tmp_path: Path) -> None:
    nested = tmp_path / "nested.zip"
    with zipfile.ZipFile(nested, "w") as zipped:
        zipped.writestr("inner/sources.json", FIXTURE.read_bytes())
    with pytest.raises(ForwardConfigurationError, match=r"no sources\.json"):
        read_sources(nested)

    text = tmp_path / "plain.zip"
    text.write_text("not a zip")
    with pytest.raises(ForwardConfigurationError, match="not a ZIP"):
        read_sources(text)
    with pytest.raises(FileNotFoundError):
        read_sources(tmp_path / "missing.zip")


def test_bad_json_is_a_configuration_error() -> None:
    with pytest.raises(ForwardConfigurationError):
        parse_sources("{not json")
    with pytest.raises(ForwardConfigurationError):
        parse_sources('{"sources": ["a string"]}')


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("Authorization: Bearer eyJhbGciOiJSUzI1NiJ9.payload\n", "Authorization: <redacted>\n"),
        ("proxy-authorization=abc def\r\n", "proxy-authorization=<redacted>\r\n"),
        ('"Authorization": "Basic dXNlcjpwYXNzd29yZA=="', '"Authorization": "<redacted>'),
        (
            "curl -H token bearer abcdefgh12345 https://192.0.2.10",
            "curl -H token bearer <redacted> https://192.0.2.10",
        ),
        # Too short to be a token, and not a header: left alone.
        ("Basic auth failed for user", "Basic auth failed for user"),
        ("collected 42 interfaces\n", "collected 42 interfaces\n"),
    ],
)
def test_logs_have_credentials_masked_and_line_endings_kept(line: str, expected: str) -> None:
    assert redact_log(line) == expected


def test_a_lone_carriage_return_does_not_end_the_line() -> None:
    # Go splits on "\n" only, so the whole header line is masked, not just up to "\r".
    assert (
        redact_log("Authorization: Bearer abcdefgh\rsecret-part\n") == "Authorization: <redacted>\n"
    )


def test_other_unicode_line_breaks_do_not_split_or_escape_the_mask() -> None:
    assert redact_log("Authorization: x\x0bsecret\n") == "Authorization: <redacted>\n"
    assert redact_log("Authorization: x\u2028secret") == "Authorization: <redacted>"


def test_redaction_is_per_line() -> None:
    log = "GET /api/v1/nodes\nAuthorization: Bearer secrettoken123\n200 OK\n"
    assert redact_log(log) == "GET /api/v1/nodes\nAuthorization: <redacted>\n200 OK\n"
