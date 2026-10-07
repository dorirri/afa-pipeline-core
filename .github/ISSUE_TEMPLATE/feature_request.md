---
name: Feature request
about: Propose a new analyzer, anomaly rule or improvement
title: "[Feature] "
labels: feature
---

## Problem / forensic question

What should an investigator be able to find out that they cannot today?

## Proposed solution

For a new analyzer: its `name`, what it extracts, the findings (rule ids, confidence
and supporting evidence) and any dependencies or model files it needs.

## Non-functional requirements check

- [ ] Evidence is opened read-only
- [ ] No network access at runtime (models/data loaded from a local, pinned path)
- [ ] Corrupted input yields a `skipped`/`error` result instead of an exception
- [ ] Output is deterministic and records the analyzer version

## Alternatives considered
