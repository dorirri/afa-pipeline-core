"""Tests for read-only ingestion and hashing."""

from __future__ import annotations

import hashlib
import os
from datetime import timezone
from pathlib import Path

import pytest

from afa_pipeline.ingestion import IngestionError, discover, ingest, sha256_file


def test_hash_matches_hashlib(tmp_path: Path) -> None:
    data = os.urandom(3 * 1024 * 1024 + 17)
    target = tmp_path / "blob.bin"
    target.write_bytes(data)

    digest, size = sha256_file(target)

    assert digest == hashlib.sha256(data).hexdigest()
    assert size == len(data)


def test_hash_of_empty_file(tmp_path: Path) -> None:
    target = tmp_path / "empty.bin"
    target.write_bytes(b"")

    assert sha256_file(target) == (hashlib.sha256(b"").hexdigest(), 0)


def test_small_chunk_size_gives_same_digest(tmp_path: Path) -> None:
    target = tmp_path / "blob.bin"
    target.write_bytes(b"forensic" * 1000)

    assert sha256_file(target, chunk_size=7) == sha256_file(target)


def test_ingest_returns_evidence_item(tmp_path: Path) -> None:
    target = tmp_path / "a.bin"
    target.write_bytes(b"evidence")

    item = ingest(target)

    assert item.path == target
    assert item.sha256 == hashlib.sha256(b"evidence").hexdigest()
    assert item.size == 8
    assert item.intake_time.tzinfo == timezone.utc


def test_ingest_does_not_modify_file(tmp_path: Path) -> None:
    target = tmp_path / "a.bin"
    target.write_bytes(b"evidence")
    old_mtime = 1_600_000_000
    os.utime(target, (old_mtime, old_mtime))
    before = target.stat()

    ingest(target)

    after = target.stat()
    assert target.read_bytes() == b"evidence"
    assert after.st_mtime_ns == before.st_mtime_ns
    assert after.st_size == before.st_size


def test_to_dict_uses_posix_path(tmp_path: Path) -> None:
    sub = tmp_path / "dir"
    sub.mkdir()
    target = sub / "a.bin"
    target.write_bytes(b"x")

    data = ingest(target).to_dict()

    assert data["path"] == target.as_posix()
    assert "\\" not in data["path"]
    assert set(data) == {"path", "sha256", "size", "intake_time"}


def test_ingest_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(IngestionError, match="not a regular file"):
        ingest(tmp_path / "missing.jpg")


def test_ingest_directory_raises(tmp_path: Path) -> None:
    with pytest.raises(IngestionError):
        ingest(tmp_path)


def test_ingest_unreadable_file_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "a.bin"
    target.write_bytes(b"x")

    def boom(*_args: object, **_kwargs: object) -> None:
        raise PermissionError("denied")

    monkeypatch.setattr("afa_pipeline.ingestion.sha256_file", boom)
    with pytest.raises(IngestionError, match="cannot read"):
        ingest(target)


def test_discover_is_sorted_and_recursive(tmp_path: Path) -> None:
    (tmp_path / "b").mkdir()
    (tmp_path / "a").mkdir()
    for rel in ["b/2.jpg", "a/1.jpg", "z.jpg", "a/0.jpg"]:
        (tmp_path / rel).write_bytes(b"x")

    found = [p.relative_to(tmp_path).as_posix() for p in discover(tmp_path)]

    assert found == ["a/0.jpg", "a/1.jpg", "b/2.jpg", "z.jpg"]


def test_discover_single_file(tmp_path: Path) -> None:
    target = tmp_path / "one.jpg"
    target.write_bytes(b"x")

    assert discover(target) == [target]


def test_discover_missing_path_raises(tmp_path: Path) -> None:
    with pytest.raises(IngestionError, match="no such file"):
        discover(tmp_path / "nope")
