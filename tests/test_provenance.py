"""Tests for the hash-chained audit log."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from afa_pipeline import __version__
from afa_pipeline.provenance import GENESIS_HASH, AuditLog, AuditLogError, canonical_json, verify

HASH_A = "a" * 64


def write_log(path: Path, count: int = 3) -> AuditLog:
    log = AuditLog(path)
    for i in range(count):
        log.append(
            "analyze",
            analyzer="exif",
            analyzer_version="1.0.0",
            input_sha256=HASH_A,
            details={"n": i},
        )
    return log


def read_lines(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8").splitlines(keepends=True)


def test_append_then_verify(tmp_path: Path) -> None:
    path = tmp_path / "audit.jsonl"
    log = write_log(path)

    result = verify(path)

    assert result.ok, result.errors
    assert result.records == 3
    assert result.head == log.head


def test_record_fields(tmp_path: Path) -> None:
    path = tmp_path / "audit.jsonl"
    log = AuditLog(path)

    first = log.append("ingest", input_sha256=HASH_A, details={"size": 10})
    second = log.append("analyze", analyzer="exif", analyzer_version="1.0.0", input_sha256=HASH_A)

    assert first.seq == 0
    assert first.prev_hash == GENESIS_HASH
    assert second.prev_hash == first.record_hash
    assert second.tool == f"afa-pipeline-core/{__version__}"
    stored = json.loads(read_lines(path)[1])
    assert stored == second.to_dict()
    assert set(stored) >= {"timestamp", "action", "analyzer", "analyzer_version", "input_sha256"}


def test_lines_are_canonical_json_with_lf(tmp_path: Path) -> None:
    path = tmp_path / "audit.jsonl"
    write_log(path, 2)

    raw = path.read_bytes()
    assert b"\r\n" not in raw
    for line in raw.decode("utf-8").splitlines():
        assert canonical_json(json.loads(line)) == line


def test_empty_log_verifies(tmp_path: Path) -> None:
    path = tmp_path / "audit.jsonl"
    path.write_text("", encoding="utf-8")

    result = verify(path)

    assert result.ok
    assert result.records == 0
    assert result.head == GENESIS_HASH


def test_modified_field_is_detected(tmp_path: Path) -> None:
    path = tmp_path / "audit.jsonl"
    write_log(path)
    lines = read_lines(path)
    lines[1] = lines[1].replace('"analyze"', '"ingest"')
    path.write_text("".join(lines), encoding="utf-8")

    result = verify(path)

    assert not result.ok
    assert any("line 2: record_hash mismatch" in e for e in result.errors)


def test_rehashed_modification_breaks_next_link(tmp_path: Path) -> None:
    from afa_pipeline.provenance import _hash_record

    path = tmp_path / "audit.jsonl"
    write_log(path)
    lines = read_lines(path)
    record = json.loads(lines[1])
    record["details"] = {"n": 999}
    record["record_hash"] = _hash_record(record)
    lines[1] = canonical_json(record) + "\n"
    path.write_text("".join(lines), encoding="utf-8")

    result = verify(path)

    assert not result.ok
    assert result.errors == ("line 3: prev_hash does not match the previous record",)


def test_deleted_record_is_detected(tmp_path: Path) -> None:
    path = tmp_path / "audit.jsonl"
    write_log(path)
    lines = read_lines(path)
    del lines[1]
    path.write_text("".join(lines), encoding="utf-8")

    result = verify(path)

    assert not result.ok
    assert any("expected seq 1" in e for e in result.errors)


def test_reordered_records_are_detected(tmp_path: Path) -> None:
    path = tmp_path / "audit.jsonl"
    write_log(path)
    lines = read_lines(path)
    lines[1], lines[2] = lines[2], lines[1]
    path.write_text("".join(lines), encoding="utf-8")

    assert not verify(path).ok


def test_truncation_at_end_needs_expected_head(tmp_path: Path) -> None:
    path = tmp_path / "audit.jsonl"
    head = write_log(path).head
    path.write_text("".join(read_lines(path)[:-1]), encoding="utf-8")

    assert verify(path).ok
    result = verify(path, expected_head=head)
    assert not result.ok
    assert result.errors[0].startswith("head mismatch")


def test_invalid_json_line(tmp_path: Path) -> None:
    path = tmp_path / "audit.jsonl"
    write_log(path, 1)
    with open(path, "a", encoding="utf-8") as handle:
        handle.write("{not json}\n")

    result = verify(path)

    assert not result.ok
    assert result.errors == (
        "line 2: invalid JSON (Expecting property name enclosed in double quotes)",
    )


def test_non_object_line(tmp_path: Path) -> None:
    path = tmp_path / "audit.jsonl"
    path.write_text("[1, 2]\n", encoding="utf-8")

    assert verify(path).errors == ("line 1: record is not a JSON object",)


def test_partial_last_line(tmp_path: Path) -> None:
    path = tmp_path / "audit.jsonl"
    write_log(path, 2)
    data = path.read_text(encoding="utf-8")
    path.write_text(data.rstrip("\n"), encoding="utf-8")

    result = verify(path)

    assert not result.ok
    assert "missing newline" in result.errors[0]


def test_reopening_continues_the_chain(tmp_path: Path) -> None:
    path = tmp_path / "audit.jsonl"
    first = write_log(path, 2)

    second = AuditLog(path)
    record = second.append("analyze")

    assert record.seq == 2
    assert record.prev_hash == first.head
    assert verify(path).ok
    assert verify(path).records == 3


def test_refuses_to_extend_tampered_log(tmp_path: Path) -> None:
    path = tmp_path / "audit.jsonl"
    write_log(path)
    path.write_text(path.read_text(encoding="utf-8").replace('"n":1', '"n":7'), encoding="utf-8")

    with pytest.raises(AuditLogError, match="refusing to extend"):
        AuditLog(path)


def test_verify_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        verify(tmp_path / "missing.jsonl")
