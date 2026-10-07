# Contributing

Thank you for helping improve afa-pipeline-core.

## Ground rules for a forensic code base

1. **Never commit real evidence**: no real photos, disk images, case outputs, personal
   data, credentials or model weights. `.gitignore` and the pre-commit hooks
   (`check-added-large-files`, `detect-private-key`) help, but you are responsible.
   Tests must generate their data synthetically (see `tests/conftest.py`).
2. **Evidence is read-only.** Open evidence files with `"rb"` only. Never copy, move,
   rename or rewrite them.
3. **No network access at runtime.** Analyzers must not download models or call
   services. Ship or mount pinned, checksummed files instead.
4. **Fail soft.** A corrupted file must produce a `skipped` or `error` result, never an
   unhandled exception.
5. **Deterministic output.** Sorted keys, stable ordering. Bump an analyzer's `version`
   whenever its results can change.

## Development setup

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
pre-commit install
```

Run the checks that CI runs:

```bash
ruff check . && ruff format --check .
mypy
pytest --cov
```

## Workflow

- Open or pick an issue first. Every change is tracked by an issue.
- Create a branch from `main` named after the change type:
  `feature/<topic>`, `fix/<topic>`, `ci/<topic>`, `docs/<topic>`.
- Use small [Conventional Commits](https://www.conventionalcommits.org/):
  `feat:`, `fix:`, `test:`, `ci:`, `build:`, `docs:`, `chore:`; add a scope where useful,
  e.g. `feat(exif): …`.
- Open a pull request against `main` with `Closes #N` in the description, fill in the
  checklist, and wait for CI to pass before merging. Use merge commits, and never
  force-push to `main`.
- Add user-visible changes to `CHANGELOG.md` under **Unreleased**.

## Adding an analyzer

1. Subclass `afa_pipeline.analyzers.Analyzer` and set `name` and `version`.
2. Implement `analyze(item) -> AnalysisResult` and build results with `self.result(...)`.
3. Register it with `@register`, or expose it through the `afa_pipeline.analyzers`
   entry-point group from your own package.
4. Report anomalies as `Finding(rule, message, confidence, evidence)` with a stable
   `rule` id such as `<analyzer>.<rule>`.
5. Add positive *and* negative tests for every rule.

## Releasing (maintainers)

1. Move the *Unreleased* entries in `CHANGELOG.md` into a new version section and set
   `version` in `pyproject.toml` and `src/afa_pipeline/__init__.py`.
2. Merge to `main`, then tag: `git tag -a vX.Y.Z -m "vX.Y.Z" && git push origin vX.Y.Z`.
3. The release workflow builds the packages, creates the GitHub Release and pushes the
   Docker image to GHCR.
