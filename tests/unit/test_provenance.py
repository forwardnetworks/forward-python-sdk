"""``forward_sdk.provenance``: hashing a snapshot ZIP and listing what it holds."""

from __future__ import annotations

import hashlib
import zipfile
from pathlib import Path

import pytest

from forward_sdk.errors import ForwardConfigurationError
from forward_sdk.provenance import describe_archive


def make_zip(path: Path, entries: dict[str, str]) -> Path:
    with zipfile.ZipFile(path, "w") as archive:
        for name, text in entries.items():
            archive.writestr(name, text)
    return path


def test_devices_files_size_and_hash(tmp_path: Path) -> None:
    archive = make_zip(
        tmp_path / "collection.zip",
        {
            "r1,show_version.txt": "v",
            "r1,running_config.txt": "c",
            "sub/r2,show_version.txt": "v",
            "manifest.json": "{}",
        },
    )
    described = describe_archive(archive)
    raw = archive.read_bytes()
    assert described.sha256 == hashlib.sha256(raw).hexdigest()
    assert described.size == len(raw)
    assert described.devices == {
        "r1": ("show_version.txt", "running_config.txt"),
        "r2": ("show_version.txt",),
    }
    assert described.other_files == ("manifest.json",)
    assert described.file_count == 4


def test_the_note_names_the_archive_and_its_full_hash(tmp_path: Path) -> None:
    described = describe_archive(make_zip(tmp_path / "c.zip", {"r1,a.txt": "x"}))
    note = described.note("CHG-1")
    assert note.startswith("CHG-1 | c.zip sha256:" + described.sha256)
    assert note.endswith("1 device, 1 file")
    assert described.note().startswith("c.zip sha256:")
    assert described.note("  ") == described.note()


def test_a_missing_file_or_not_a_zip_is_refused(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        describe_archive(tmp_path / "absent.zip")
    bad = tmp_path / "bad.zip"
    bad.write_text("not a zip")
    with pytest.raises(ForwardConfigurationError, match="not a ZIP"):
        describe_archive(bad)


def test_names_with_an_empty_device_or_file_part_are_other_files(tmp_path: Path) -> None:
    described = describe_archive(make_zip(tmp_path / "c.zip", {",x.txt": "1", "r1,": "2"}))
    assert described.devices == {} and described.other_files == (",x.txt", "r1,")


def test_directory_entries_are_not_files(tmp_path: Path) -> None:
    path = tmp_path / "c.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(zipfile.ZipInfo("sub/"), "")
        archive.writestr("sub/r1,a.txt", "x")
    described = describe_archive(path)
    assert described.other_files == () and described.file_count == 1


def test_counts_are_plural_except_for_one(tmp_path: Path) -> None:
    described = describe_archive(make_zip(tmp_path / "c.zip", {"r1,a.txt": "x", "r2,b.txt": "y"}))
    assert described.note().endswith("2 devices, 2 files")
