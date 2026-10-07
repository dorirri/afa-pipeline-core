"""Tests for the analyzer interface and registry."""

from __future__ import annotations

from typing import Any

import pytest

from afa_pipeline.analyzers import (
    AnalysisResult,
    AnalysisStatus,
    Analyzer,
    AnalyzerRegistry,
    DuplicateAnalyzerError,
    Finding,
    UnknownAnalyzerError,
)
from afa_pipeline.ingestion import EvidenceItem


class EchoAnalyzer(Analyzer):
    name = "echo"
    version = "1.0.0"

    def analyze(self, item: EvidenceItem) -> AnalysisResult:
        finding = Finding("echo.size", "size reported", 1.0, {"size": item.size})
        return self.result(item, AnalysisStatus.OK, data={"size": item.size}, findings=(finding,))


class OtherEcho(Analyzer):
    name = "echo"
    version = "2.0.0"

    def analyze(self, item: EvidenceItem) -> AnalysisResult:
        return self.result(item, AnalysisStatus.OK)


class AlphaAnalyzer(Analyzer):
    name = "alpha"
    version = "0.1.0"

    def analyze(self, item: EvidenceItem) -> AnalysisResult:
        return self.result(item, AnalysisStatus.SKIPPED, error="not applicable")


class CrashingAnalyzer(Analyzer):
    name = "crash"
    version = "0.0.1"

    def analyze(self, item: EvidenceItem) -> AnalysisResult:
        raise RuntimeError("boom")


def test_register_and_get() -> None:
    reg = AnalyzerRegistry()
    assert reg.register(EchoAnalyzer) is EchoAnalyzer
    assert reg.get("echo") is EchoAnalyzer
    assert "echo" in reg
    assert len(reg) == 1


def test_register_works_as_decorator() -> None:
    reg = AnalyzerRegistry()

    @reg.register
    class Decorated(Analyzer):
        name = "decorated"
        version = "1"

        def analyze(self, item: EvidenceItem) -> AnalysisResult:
            return self.result(item, AnalysisStatus.OK)

    assert reg.names() == ["decorated"]


def test_duplicate_name_is_rejected() -> None:
    reg = AnalyzerRegistry()
    reg.register(EchoAnalyzer)
    with pytest.raises(DuplicateAnalyzerError, match="echo"):
        reg.register(OtherEcho)


def test_registering_same_class_twice_is_idempotent() -> None:
    reg = AnalyzerRegistry()
    reg.register(EchoAnalyzer)
    reg.register(EchoAnalyzer)
    assert len(reg) == 1


def test_unknown_name_raises() -> None:
    with pytest.raises(UnknownAnalyzerError):
        AnalyzerRegistry().get("missing")


def test_non_analyzer_is_rejected() -> None:
    with pytest.raises(TypeError):
        AnalyzerRegistry().register(object)  # type: ignore[arg-type]


def test_abstract_analyzer_is_rejected() -> None:
    with pytest.raises(TypeError, match="abstract"):
        AnalyzerRegistry().register(Analyzer)  # type: ignore[type-abstract]


def test_concrete_analyzer_needs_name_and_version() -> None:
    with pytest.raises(TypeError, match="version"):

        class NoVersion(Analyzer):
            name = "no-version"

            def analyze(self, item: EvidenceItem) -> AnalysisResult:
                raise NotImplementedError


def test_names_and_create_are_sorted() -> None:
    reg = AnalyzerRegistry()
    reg.register(EchoAnalyzer)
    reg.register(AlphaAnalyzer)
    assert reg.names() == ["alpha", "echo"]
    assert list(reg) == ["alpha", "echo"]
    assert [a.name for a in reg.create()] == ["alpha", "echo"]
    assert [a.name for a in reg.create(["echo"])] == ["echo"]


def test_result_is_stamped_with_identity(evidence_item: EvidenceItem) -> None:
    result = EchoAnalyzer().run(evidence_item)

    assert result.to_dict() == {
        "analyzer": "echo",
        "analyzer_version": "1.0.0",
        "evidence_sha256": evidence_item.sha256,
        "status": "ok",
        "data": {"size": evidence_item.size},
        "findings": [
            {
                "rule": "echo.size",
                "message": "size reported",
                "confidence": 1.0,
                "evidence": {"size": evidence_item.size},
            }
        ],
        "error": None,
    }


def test_run_contains_exceptions(evidence_item: EvidenceItem) -> None:
    result = CrashingAnalyzer().run(evidence_item)

    assert result.status is AnalysisStatus.ERROR
    assert result.error == "RuntimeError: boom"


@pytest.mark.parametrize("confidence", [-0.1, 1.5])
def test_finding_confidence_is_validated(confidence: float) -> None:
    with pytest.raises(ValueError, match="confidence"):
        Finding("r", "m", confidence)


class _FakeEntryPoint:
    def __init__(self, name: str, target: Any) -> None:
        self.name = name
        self._target = target

    def load(self) -> Any:
        return self._target


def test_load_entry_points(monkeypatch: pytest.MonkeyPatch) -> None:
    eps = [_FakeEntryPoint("echo", EchoAnalyzer), _FakeEntryPoint("alpha", AlphaAnalyzer)]
    monkeypatch.setattr(
        "afa_pipeline.analyzers.registry.entry_points", lambda group: eps if group else []
    )
    reg = AnalyzerRegistry()

    assert reg.load_entry_points() == ["alpha", "echo"]
    assert reg.names() == ["alpha", "echo"]
