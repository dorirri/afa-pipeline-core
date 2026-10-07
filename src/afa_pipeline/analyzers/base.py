"""Analyzer plugin interface (FR9)."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, ClassVar

from afa_pipeline.ingestion import EvidenceItem

__all__ = ["AnalysisResult", "AnalysisStatus", "Analyzer", "Finding"]

logger = logging.getLogger(__name__)


class AnalysisStatus(str, Enum):
    """Outcome of running one analyzer on one evidence item."""

    OK = "ok"
    SKIPPED = "skipped"
    ERROR = "error"


@dataclass(frozen=True)
class Finding:
    """A single observation an analyzer reports about an evidence item.

    Attributes:
        rule: Stable machine-readable identifier of the rule that fired.
        message: Human-readable explanation.
        confidence: How strongly the evidence supports the finding, in ``[0, 1]``.
        evidence: The concrete values that support the finding.
    """

    rule: str
    message: str
    confidence: float
    evidence: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Validate the confidence range."""
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError(f"confidence must be within [0, 1], got {self.confidence}")

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable representation."""
        return {
            "rule": self.rule,
            "message": self.message,
            "confidence": round(self.confidence, 4),
            "evidence": dict(self.evidence),
        }


@dataclass(frozen=True)
class AnalysisResult:
    """Output of one analyzer for one evidence item.

    Attributes:
        analyzer: Name of the analyzer that produced the result.
        analyzer_version: Version of that analyzer.
        evidence_sha256: SHA-256 of the analysed evidence item.
        status: Whether the analysis succeeded, was skipped or failed.
        data: Extracted values (analyzer specific).
        findings: Anomalies or observations, in a deterministic order.
        error: Reason for a ``skipped`` or ``error`` status.
    """

    analyzer: str
    analyzer_version: str
    evidence_sha256: str
    status: AnalysisStatus
    data: Mapping[str, Any] = field(default_factory=dict)
    findings: tuple[Finding, ...] = ()
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable representation."""
        return {
            "analyzer": self.analyzer,
            "analyzer_version": self.analyzer_version,
            "evidence_sha256": self.evidence_sha256,
            "status": self.status.value,
            "data": dict(self.data),
            "findings": [f.to_dict() for f in self.findings],
            "error": self.error,
        }


class Analyzer(ABC):
    """Base class for analyzer plugins.

    Subclasses set :attr:`name` and :attr:`version` and implement :meth:`analyze`.
    Callers use :meth:`run`, which converts unexpected exceptions into an
    ``error`` result.
    """

    name: ClassVar[str]
    version: ClassVar[str]

    def __init_subclass__(cls, **kwargs: Any) -> None:
        """Check that concrete subclasses declare ``name`` and ``version``."""
        super().__init_subclass__(**kwargs)
        if getattr(cls, "__abstractmethods__", None):
            return
        for attr in ("name", "version"):
            value = getattr(cls, attr, None)
            if not isinstance(value, str) or not value:
                raise TypeError(f"{cls.__name__} must define a non-empty str '{attr}'")

    @abstractmethod
    def analyze(self, item: EvidenceItem) -> AnalysisResult:
        """Analyse one evidence item.

        Args:
            item: The hashed evidence item.

        Returns:
            The analysis result.
        """

    def result(
        self,
        item: EvidenceItem,
        status: AnalysisStatus,
        *,
        data: Mapping[str, Any] | None = None,
        findings: tuple[Finding, ...] = (),
        error: str | None = None,
    ) -> AnalysisResult:
        """Build an :class:`AnalysisResult` stamped with this analyzer's identity."""
        return AnalysisResult(
            analyzer=self.name,
            analyzer_version=self.version,
            evidence_sha256=item.sha256,
            status=status,
            data=data or {},
            findings=findings,
            error=error,
        )

    def run(self, item: EvidenceItem) -> AnalysisResult:
        """Run :meth:`analyze` and contain any unexpected exception."""
        try:
            return self.analyze(item)
        except Exception as exc:
            logger.exception("analyzer %s failed on %s", self.name, item.path)
            return self.result(item, AnalysisStatus.ERROR, error=f"{type(exc).__name__}: {exc}")
