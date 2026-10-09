# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Fixed

- Docker image is now published for `linux/amd64` and `linux/arm64`; `v0.1.0` was
  amd64-only and could not be pulled on Apple Silicon without `--platform`.

## [0.1.0] - 2026-10-08

First public release of the AFA-Pipeline core modules.

### Added

- Read-only evidence ingestion with chunked SHA-256 hashing and deterministic
  file discovery (`EvidenceItem`) (#1).
- `Analyzer` plugin interface, `Finding`/`AnalysisResult` data model and an
  `AnalyzerRegistry` with duplicate-name detection and entry-point loading (#2).
- `MetadataAnalyzer` (`exif` 1.0.0): extraction of make, model, timestamps and GPS, with
  anomaly rules `exif.missing`, `exif.timestamp_inconsistency`,
  `exif.timestamp_malformed`, `exif.editing_software` and `exif.gps_malformed` (#3).
- Append-only, hash-chained JSON Lines audit log with `verify()` and optional
  expected-head check (#4).
- `afa` CLI with `analyze`, `verify-log` and `analyzers` subcommands; the report hash
  is recorded in the audit log (#5).
- CI workflow: ruff, mypy, and pytest with an 85 % coverage gate on Python 3.10–3.12
  (Ubuntu) and 3.12 (Windows), plus an oldest-supported-Pillow job (#6, #9).
- Release workflow: sdist and wheel, GitHub Release, and Docker image on GHCR (#7).
- Documentation: README, technology choices, contribution guide, issue and PR
  templates, citation file (#8).

### Fixed

- Audit log now creates its parent directory (#5).
- Zero-denominator GPS rationals on Pillow < 12.3 were reported as an analyzer
  error instead of an `exif.gps_malformed` finding (#9).

[Unreleased]: https://github.com/dorirri/afa-pipeline-core/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/dorirri/afa-pipeline-core/releases/tag/v0.1.0
