# Technology choices

This document gives the reason for every technology used in `afa-pipeline-core`, with
the alternatives considered. The main criteria come from the AFA-Pipeline
non-functional requirements:

- **N1 Integrity:** evidence is never modified.
- **N2 Reproducibility:** pinned versions, deterministic output.
- **N3 Auditability:** every operation is logged.
- **N4 Offline operation:** no network calls at runtime.
- **N5 Robustness:** a corrupted file must not crash the pipeline.

Engineering criteria: small dependency footprint, maintainability, and fit with the wider
(Python, ML-based) AFA-Pipeline.

## 1. Programming language: Python ≥ 3.10

| Alternatives | Why not |
|---|---|
| Go, Rust | Single static binaries and fast hashing, but the planned CV and OCR analyzers depend on the Python ML ecosystem (PyTorch, Tesseract bindings, OpenCV). A second language would split the pipeline in two. |
| Java / Kotlin | Close to Android, but the forensic analysis runs on the examiner's workstation, not on the device, and has the same ML-ecosystem gap. |

**Reason.** Every planned analyzer is Python-based, and Python has mature libraries for
EXIF, imaging and ML. Hashing in `hashlib` is implemented in C,
so performance is limited by disk I/O, not by the language. The minimum is 3.10: it's the
oldest version still supported by current releases of Pillow, pytest and mypy, and it adds
`X | Y` type unions and structural pattern matching.

**Caveat.** Python 3.10 reaches end of life in October 2026. It is still in the CI matrix
because the assignment requires it and it costs nothing. The next minor release should
raise the minimum to 3.11.

## 2. EXIF library: Pillow

| Library | Read | Write | Maintained | Notes |
|---|---|---|---|---|
| **Pillow** | ✓ | ✓ (`Image.Exif`) | ✓ (quarterly releases) | Also decodes pixels, which is needed to detect truncated/corrupted files. Needed later by the CV analyzer anyway. |
| piexif | ✓ | ✓ | ✗ (last release 1.1.3, 2019) | Unmaintained; JPEG/WebP only. |
| exifread | ✓ | ✗ | ✓ | Read-only. It can't generate test fixtures and can't detect corrupted pixel data, so it would add a second dependency. |
| ExifTool (subprocess) | ✓ | ✓ | ✓ | The reference tool, but it's an external Perl program. It would have to be installed and version-pinned separately, and a subprocess interface is harder to test and run on Windows. |
| pyexiv2 | ✓ | ✓ | ~ | Native library binding; wheels are not available for every platform. |

**Reason.** Pillow is the only option that reads and writes EXIF *and* decodes the image.
That makes it the single runtime dependency (N2, small footprint). Only the public
`Image.getexif()` / `Exif.get_ifd()` API is used. `PIL.Image.Exif` can also write every
malformed case the tests need, including zero-denominator GPS rationals, so no separate
EXIF writer is required.

**Version bound.** `Pillow>=11.1,<13`. The floor was measured, not guessed: the suite
was run against several older Pillow releases.

- **Pillow 10.4 and 11.0** read EXIF correctly. But `Image.Exif` silently drops sub-IFDs
  (EXIF, GPS) created with `get_ifd()` when saving, so the synthetic fixtures can't be
  built and the analyzer can't be verified.
- **Pillow < 12.3** raises `ZeroDivisionError` for a zero-denominator rational, where
  12.3 returns `NaN`. That exposed a real bug, now fixed and covered by a regression test.

The CI job `test-min-deps` runs the suite with exactly Pillow 11.1.0, so the floor stays
honest. The upper bound protects against breaking changes in a future major version.

## 3. Hash function: SHA-256

| Alternatives | Why not |
|---|---|
| MD5, SHA-1 | Practical collision attacks exist. They are still recorded by some forensic tools for legacy compatibility, but they are not acceptable as the primary integrity hash. |
| SHA-3, BLAKE2/3 | Secure and (BLAKE3) faster, but SHA-256 is the most widely accepted hash in forensic reports and tools, so an examiner can check it with standard utilities (`sha256sum`, `Get-FileHash`, `shasum -a 256`). BLAKE3 would also need an extra dependency. |

**Reason.** SHA-256 is a NIST-standardised (FIPS 180-4), collision-resistant hash. It's
built into Python and is the common choice for evidence integrity. Hashing reads 1 MiB
at a time, so memory use stays flat for large files, and needs no external `sha256sum`
command (not available on Windows).

## 4. Audit log: append-only JSON Lines with a SHA-256 hash chain

| Alternatives | Why not |
|---|---|
| Plain `logging` file | Not structured and not tamper-evident. |
| SQLite table | Queryable, but rows can be updated in place. Tamper evidence would still need a hash chain, and the file is binary and hard to diff or read. |
| Blockchain anchoring (e.g. Ethereum via web3) | Needs network access (violates N4), a funded wallet and a private key that must be stored somewhere. It can't run in CI. |
| Signed log (Ed25519 / HMAC) | Stronger, but needs key management, which is out of scope for the initial modules. |
| Transparency log (Sigstore Rekor, Merkle trees) | External service or substantial complexity. |

**Reason.** JSON Lines can only be appended to, any tool can read it, and it diffs line by
line. Each record includes the hash of the previous one, so modifying, inserting,
deleting or reordering a record is detectable without any key (N3). Hashes are computed
over **canonical JSON** (sorted keys, no whitespace, UTF-8) and lines are always written
with `\n`, so verification gives the same result on every OS.

**Known limitation.** A hash chain can't detect records removed from the *end*, or a full
rewrite by someone who recomputes every hash. The CLI therefore prints the chain *head*,
which the examiner keeps outside the log, and `verify(expected_head=…)` checks it. Signing
or externally anchoring the head (for example on a blockchain, as an optional
out-of-band step) is on the roadmap.

## 5. Plugin mechanism: abstract base class + explicit registry + entry points

| Alternatives | Why not |
|---|---|
| `typing.Protocol` | Structural typing is flexible, but an ABC lets the base class provide shared behaviour (`run()` with fault isolation, `result()` stamping name and version) and check that `name`/`version` are declared when the class is defined. |
| pluggy, stevedore | Full plugin frameworks; more than one analyzer type needs. |
| Import-path scanning | Implicit and fragile; the order would depend on the file system. |

**Reason.** `Analyzer` (ABC) fixes the contract `name`, `version`,
`analyze(item) -> AnalysisResult`. `AnalyzerRegistry` rejects duplicate names and returns
analyzers in sorted order (N2). `Analyzer.run()` turns any exception into an `error`
result (N5). Third-party analyzers can be installed as packages and found through the
standard `importlib.metadata` entry points with no extra dependency. Entry points are read
from locally installed packages only (N4).

## 6. CLI: `argparse`

**Alternatives:** Click and Typer offer nicer syntax and help output, but each adds
runtime dependencies. The CLI has three subcommands, which `argparse` (standard library)
handles well, so the package keeps Pillow as its only dependency.

## 7. Packaging: `pyproject.toml` (PEP 621) + setuptools, `src/` layout

| Alternatives | Why not |
|---|---|
| Poetry, PDM | Good dependency managers, but the project then needs another tool, and Poetry's metadata was non-standard until v2. |
| Hatch / hatchling, Flit | Good, modern build back-ends; setuptools was chosen because it is the most widely known. |
| `setup.py` | Executable configuration, now discouraged by PyPA. |

**Reason.** PEP 621 metadata is tool-independent. The `src/` layout makes tests run
against the installed package rather than the working directory, which catches
packaging mistakes. The `afa` console script is declared with `[project.scripts]`.

**Dependency pinning.**

- The runtime dependency is **bounded** (`>=10.4,<13`). Exact pins in a library would
  stop users combining it with other packages.
- Development tools are **pinned exactly** in the `dev` extra, so CI and every developer
  run the same ruff/mypy/pytest versions.
- **Exact reproducibility for case work** comes from the versioned Docker image. Every
  result also records the tool and analyzer versions.

## 8. Testing: pytest + pytest-cov, synthetic fixtures

**Alternatives:** `unittest` is in the standard library but more verbose. It lacks
fixtures like `tmp_path` and has weaker parametrisation. Hypothesis (property-based
testing) is a candidate for the parsers later.

**Reason.** pytest fixtures (`tmp_path`, `monkeypatch`, `capsys`) and `parametrize` keep
the per-rule positive/negative cases short. Images are generated by a fixture factory at
test time, so **no real photo is ever committed**. The coverage gate (85 %, branch
coverage) is enforced in CI.

## 9. Lint and format: ruff

**Alternatives:** flake8 + isort + black + pylint + bandit. That's five tools with
separate configurations, and much slower.

**Reason.** A single, very fast tool covers pycodestyle, pyflakes, isort, bugbear,
pyupgrade, pydocstyle (Google convention, enforcing docstrings on the public API) and
bandit-style security checks (`S`). `ruff format` matches black's output.

## 10. Static typing: mypy (strict)

**Alternative:** pyright is faster and has a good IDE story. mypy was chosen because it's
the reference type checker, runs as a plain pip package in CI and pre-commit without Node.js,
and its `strict` mode is well documented. Pillow ships inline type hints, so no stub
packages are needed.

## 11. Git hooks: pre-commit

Runs the same ruff and mypy checks as CI before each commit. It also has guards that
matter for a forensic project: `check-added-large-files` (500 KB) and
`detect-private-key`. `.gitignore` additionally excludes all image formats, `*.jsonl`
outputs and model weights.

## 12. CI/CD: GitHub Actions, GitHub Releases, GHCR

| Alternatives | Why not |
|---|---|
| GitLab CI, Jenkins, CircleCI | The repository is on GitHub, which the assignment requires; Actions needs no extra service or runner. |
| PyPI for distribution | Possible later. For now, GitHub Releases host the wheel and sdist next to the source, with no extra account or secret. |
| Docker Hub | Requires a separate account and secret. GHCR authenticates with the built-in `GITHUB_TOKEN` and links the image to the repository. |

**CI design.**

- Jobs: `lint`, `typecheck` and `test` run in parallel.
- Test matrix: Ubuntu × 3.10/3.11/3.12, plus Windows × 3.12 to catch path and
  line-ending issues.
- A `test-min-deps` job runs the suite against the oldest supported Pillow (11.1.0).
- pip cache keyed on `pyproject.toml`, `concurrency` cancels superseded runs, and
  per-job timeouts keep the run well under 5 minutes.
- Coverage reports are uploaded as artifacts.

**Release design.** The release workflow calls the CI workflow (`workflow_call`), so a
release can't skip the tests. It then:

1. checks that the tag matches the package version;
2. builds with `python -m build` and smoke-tests the wheel;
3. creates the release with the GitHub CLI preinstalled on the runner, so no third-party
   release action is involved;
4. builds and smoke-tests the image with `--network none` before pushing it.

## 13. Container: `python:3.12-slim`, multi-stage, non-root

| Alternatives | Why not |
|---|---|
| `python:3.12-alpine` | musl libc. Pillow wheels exist but are less common, and other wheels may compile from source, which is slower and harder to reproduce. |
| Distroless | Smaller attack surface, but no shell for debugging, and the Python version is tied to Debian's. |
| Full `python:3.12` | ~1 GB, includes compilers that aren't needed at runtime. |

**Reason.** The build stage creates the wheel. The runtime stage contains only the wheel
and Pillow (≈170 MB), runs as an unprivileged user, and works with `--network none`
(N4). Evidence is meant to be mounted read-only (`:ro`).

## 14. License: MIT

**Alternatives:** Apache-2.0 (explicit patent grant, longer), GPL-3.0 (copyleft, which would
stop the code being used in closed forensic suites). MIT is short, permissive and common
for academic projects.
