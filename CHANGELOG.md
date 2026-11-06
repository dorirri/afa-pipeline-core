# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Jenkins pipeline (`Jenkinsfile`) with a local Jenkins + Minikube stand in `jenkins/`:
  lint, type check, unit tests, Docker image, smoke test and deployment as a Kubernetes
  Job (`k8s/job.yaml`).
- `scripts/image_generator.py`: synthetic Android photo generator ported from the
  original prototype (fake device, time and GPS EXIF, `--seed` for reproducible
  sets); `piexif` added to the `dev` and new `demo` extras.
- `integrity` analyzer: Error Level Analysis and JPEG ghost ported from the original
  prototype (`analyzers/legacy/image_integrity.py`, algorithms unchanged) behind a typed
  adapter; rules `integrity.ela` and `integrity.jpeg_ghost`. New `integrity` extra
  (numpy, scipy, matplotlib), installed in the Docker image.
- `ocr` analyzer: Tesseract OCR ported from the original prototype
  (`analyzers/legacy/content_ocr.py`, filtering unchanged) with languages from
  `AFA_OCR_LANG` (default `kaz+rus+eng`). New `ocr` extra (pytesseract,
  opencv-python-headless); the Docker image ships Tesseract with kaz, rus and eng.
- `afa geo-timeline`: case-level travel segments, location clusters and anomalies
  (`geo.high_speed`, `geo.time_gap`, `geo.night_activity`) ported from the original
  prototype (`analyzers/legacy/geo_timeline_analyzer.py`, algorithms and HTML/map
  outputs unchanged), run on an `afa analyze` report; input report and output hashes
  are recorded in the audit log. New `geo` extra (pandas, folium, geopy, scikit-learn).
- `afa verdict`: explainable per-file and case verdict (`ALERT` / `CAUTION` / `PASSED`,
  the prototype's integrity grading) combining all findings with a noisy-OR, listing
  supporting indicators and analyzer limitations, and geo anomalies for the case.
  The report must be intact and recorded in the audit log; the verdict is logged.
- `cv` analyzer: objects of forensic interest, faces and scene, ported from the original
  prototype's `ContentAnalyzer` (`analyzers/legacy/content_analyzer.py`, categories and
  result format unchanged). Models load from local files only: YOLO weights from
  `AFA_YOLO_WEIGHTS` (SHA-256 recorded in every result), optional InceptionV3 from
  `AFA_INCEPTION_WEIGHTS`. Ultralytics runs offline, without telemetry, and its
  process-wide `PIL.Image.open` patch is undone. Only face counts and boxes are reported,
  not face encodings. New opt-in `cv` extra; Docker: `--build-arg EXTRAS=integrity,ocr,geo,cv`.

### Fixed

- Synthetic generator: labels were drawn with a 10 px fallback font outside Windows and
  overlapped; they now use 100/60 px dark text on light labels, readable by OCR.

### Changed

- CI runs on pushes to every branch and builds and smoke-tests the Docker image on each
  commit, not only on release tags.

## [0.1.1] - 2026-10-09

### Fixed

- Docker image is now published for `linux/amd64` and `linux/arm64`; `v0.1.0` was
  amd64-only and could not be pulled on Apple Silicon without `--platform`.
- Release workflow publishes the Docker image only after the tag/version check and
  package build succeed.

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

[Unreleased]: https://github.com/dorirri/afa-pipeline-core/compare/v0.1.1...HEAD
[0.1.1]: https://github.com/dorirri/afa-pipeline-core/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/dorirri/afa-pipeline-core/releases/tag/v0.1.0
