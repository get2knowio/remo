# Specification Quality Checklist: Dependency, Dead-Code & Documentation Hygiene

**Purpose**: Validate specification completeness and quality before proceeding to planning
**Created**: 2026-07-26
**Feature**: [spec.md](../spec.md)

## Content Quality

- [x] No implementation details (languages, frameworks, APIs)
- [x] Focused on user value and business needs
- [x] Written for non-technical stakeholders
- [x] All mandatory sections completed

## Requirement Completeness

- [x] No [NEEDS CLARIFICATION] markers remain
- [x] Requirements are testable and unambiguous
- [x] Success criteria are measurable
- [x] Success criteria are technology-agnostic (no implementation details)
- [x] All acceptance scenarios are defined
- [x] Edge cases are identified
- [x] Scope is clearly bounded
- [x] Dependencies and assumptions identified

## Feature Readiness

- [x] All functional requirements have clear acceptance criteria
- [x] User scenarios cover primary flows
- [x] Feature meets measurable outcomes defined in Success Criteria
- [x] No implementation details leak into specification

## Validation Notes

**Iteration 1 findings and fixes:**

1. *Implementation detail leakage* — the first draft's requirements named specific files
   (`pyproject.toml`, `providers/proxmox.py`) throughout. Because this feature's subject matter **is**
   the repository's own artifacts, file names are the user-visible objects, not implementation choices.
   Retained deliberately, mirroring the precedent set in `specs/005-provider-snapshots/checklists/requirements.md`.
   Requirements are still phrased as outcomes ("every dependency is traceable to a named consumer"),
   not as edit instructions.

2. *Unverifiable requirement* — FR-017 originally read "surfaced as part of the project's normal change
   process", which no test can falsify. Tightened to an executable check that runs in CI and fails the
   build, making SC-008 verifiable by deliberately adding an undocumented module.

3. *Underspecified decisions* — three items the source description left open (`boto3` classification,
   drift-check mechanism, `--yes` removal timing) were resolved in the spec with recorded rationale
   rather than left as markers. Each is confirmed with the requester; see the Decision Log below.

## Decision Log

| Decision | Resolution | Basis |
|---|---|---|
| `hcloud` fate | Retained as a hard runtime dependency, annotated with its Ansible-collection consumer | Verified `hetzner.hcloud` modules import it under `ansible_playbook_python`; removal breaks Hetzner create/destroy/resize |
| `boto3` status | Stays a hard runtime dependency (FR-004/FR-004a); slimming split to [#94](https://github.com/get2knowio/remo/issues/94) | Measured 27.1 MB / 42% of a 65 MB install (`botocore` alone is 38%), but 41 MB of collections install unconditionally regardless, and the AWS + Hetzner teardown/resize playbooks have no SDK preflight — a pyproject-only change would create a new failure mode |
| Scope of dependency work | Annotation only; no required/optional reclassification | Requester's call: keep 019 to documentation and annotation truth |
| `httpx2` | Retained, rationale recorded (FR-007) | Real pydantic package; Starlette 1.3.1 `testclient` resolves it before `httpx`; suite passes with it and no `httpx` |
| Drift prevention | Executable CI check that fails the build, plus a short written update procedure (FR-017, FR-019a) | A ritual alone is unverifiable and is precisely what failed across features 010–018; the doc exists so the check's failure is actionable for a first-time contributor |
| `create --yes` | Removed outright now (FR-025) | 018 is unreleased; keeping the notice would ship and withdraw a deprecation in the same release for a flag that never did anything |

## Notes

- Items marked incomplete require spec updates before `/speckit-clarify` or `/speckit-plan`
