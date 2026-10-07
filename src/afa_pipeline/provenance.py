"""Append-only, hash-chained audit log in JSON Lines format (FR8)."""

from __future__ import annotations

import hashlib
import json
import logging
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from afa_pipeline import __version__

__all__ = [
    "GENESIS_HASH",
    "AuditLog",
    "AuditLogError",
    "AuditRecord",
    "VerificationResult",
    "canonical_json",
    "verify",
]

logger = logging.getLogger(__name__)

GENESIS_HASH = "0" * 64

TOOL = f"afa-pipeline-core/{__version__}"


class AuditLogError(Exception):
    """Raised when an existing audit log is corrupt and must not be extended."""


def canonical_json(data: Mapping[str, Any]) -> str:
    """Serialise ``data`` deterministically (sorted keys, no whitespace, UTF-8)."""
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _hash_record(fields: Mapping[str, Any]) -> str:
    body = {k: v for k, v in fields.items() if k != "record_hash"}
    return hashlib.sha256(canonical_json(body).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class AuditRecord:
    """One entry of the audit log.

    Attributes:
        seq: Zero-based position in the log.
        timestamp: UTC time at which the action was recorded.
        action: What happened, e.g. ``ingest`` or ``analyze``.
        tool: Name and version of this package.
        analyzer: Name of the analyzer involved, if any.
        analyzer_version: Version of that analyzer, if any.
        input_sha256: SHA-256 of the evidence item the action applied to, if any.
        details: Additional action-specific values.
        prev_hash: ``record_hash`` of the previous record (or :data:`GENESIS_HASH`).
        record_hash: SHA-256 over the canonical JSON of all other fields.
    """

    seq: int
    timestamp: str
    action: str
    tool: str
    analyzer: str | None
    analyzer_version: str | None
    input_sha256: str | None
    details: Mapping[str, Any]
    prev_hash: str
    record_hash: str = field(default="")

    def to_dict(self) -> dict[str, Any]:
        """Return the record as a plain dictionary."""
        return {
            "seq": self.seq,
            "timestamp": self.timestamp,
            "action": self.action,
            "tool": self.tool,
            "analyzer": self.analyzer,
            "analyzer_version": self.analyzer_version,
            "input_sha256": self.input_sha256,
            "details": dict(self.details),
            "prev_hash": self.prev_hash,
            "record_hash": self.record_hash,
        }


@dataclass(frozen=True)
class VerificationResult:
    """Outcome of :func:`verify`.

    Attributes:
        ok: ``True`` if the whole chain is intact.
        records: Number of records that were read.
        head: ``record_hash`` of the last valid record (or :data:`GENESIS_HASH`).
        errors: Human-readable description of every problem found.
    """

    ok: bool
    records: int
    head: str
    errors: tuple[str, ...] = ()


def verify(path: str | os.PathLike[str], expected_head: str | None = None) -> VerificationResult:
    """Check the hash chain of an audit log.

    Args:
        path: Audit log to check. It is opened read-only.
        expected_head: Optional ``record_hash`` the last record must have.

    Returns:
        A :class:`VerificationResult`; ``ok`` is ``False`` if anything is wrong.

    Raises:
        FileNotFoundError: If ``path`` does not exist.
    """
    errors: list[str] = []
    prev = GENESIS_HASH
    count = 0
    with open(path, encoding="utf-8", newline="") as handle:
        for lineno, raw in enumerate(handle, start=1):
            line = raw.rstrip("\r\n")
            if not raw.endswith("\n"):
                errors.append(f"line {lineno}: incomplete record (missing newline)")
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                errors.append(f"line {lineno}: invalid JSON ({exc.msg})")
                break
            if not isinstance(record, dict):
                errors.append(f"line {lineno}: record is not a JSON object")
                break
            if record.get("seq") != count:
                errors.append(f"line {lineno}: expected seq {count}, found {record.get('seq')!r}")
            if record.get("prev_hash") != prev:
                errors.append(f"line {lineno}: prev_hash does not match the previous record")
            recomputed = _hash_record(record)
            if record.get("record_hash") != recomputed:
                errors.append(f"line {lineno}: record_hash mismatch (record was modified)")
            prev = str(record.get("record_hash"))
            count += 1
    if expected_head is not None and prev != expected_head:
        errors.append(f"head mismatch: expected {expected_head}, found {prev}")
    result = VerificationResult(ok=not errors, records=count, head=prev, errors=tuple(errors))
    if not result.ok:
        logger.warning("audit log %s failed verification: %s", path, "; ".join(errors))
    return result


class AuditLog:
    """Writer for a hash-chained JSON Lines audit log.

    An existing log is verified before it is extended; a broken one raises
    :class:`AuditLogError`.
    """

    def __init__(self, path: str | os.PathLike[str]) -> None:
        """Open (or create) the audit log at ``path``."""
        self.path = Path(path)
        self._seq = 0
        self._head = GENESIS_HASH
        if self.path.exists():
            result = verify(self.path)
            if not result.ok:
                raise AuditLogError(
                    f"refusing to extend corrupt audit log {self.path}: " + "; ".join(result.errors)
                )
            self._seq = result.records
            self._head = result.head

    @property
    def head(self) -> str:
        """``record_hash`` of the most recent record."""
        return self._head

    def append(
        self,
        action: str,
        *,
        analyzer: str | None = None,
        analyzer_version: str | None = None,
        input_sha256: str | None = None,
        details: Mapping[str, Any] | None = None,
    ) -> AuditRecord:
        """Append one record and flush it to disk.

        Args:
            action: What happened, e.g. ``ingest`` or ``analyze``.
            analyzer: Analyzer name, if the action involves one.
            analyzer_version: Analyzer version, if the action involves one.
            input_sha256: SHA-256 of the evidence item, if any.
            details: Additional JSON-serialisable values.

        Returns:
            The record as written, including its ``record_hash``.
        """
        record = AuditRecord(
            seq=self._seq,
            timestamp=datetime.now(timezone.utc).isoformat(),
            action=action,
            tool=TOOL,
            analyzer=analyzer,
            analyzer_version=analyzer_version,
            input_sha256=input_sha256,
            details=dict(details or {}),
            prev_hash=self._head,
        )
        fields = record.to_dict()
        fields["record_hash"] = _hash_record(fields)
        with open(self.path, "a", encoding="utf-8", newline="\n") as handle:
            handle.write(canonical_json(fields) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        self._seq += 1
        self._head = fields["record_hash"]
        return AuditRecord(**fields)
