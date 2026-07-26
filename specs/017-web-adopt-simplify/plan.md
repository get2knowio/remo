# Implementation Plan: Simplify Web Adoption & Close the Lifecycle

**Branch**: `017-web-adopt-simplify` | **Date**: 2026-07-26 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/017-web-adopt-simplify/spec.md`

## Summary

Collapse the two near-identical adoption flows (`_adopt_flow` / `_push_flow` in `core/web_adopt.py`) into one `run_push` path where "adopt vs. re-sync" is auto-detected from the deployment's own state, retaining `remo web adopt` for one release as a deprecated alias. Layer on lifecycle closure and drift visibility: an offline `remo web status` command, an out-of-date nudge after every registry-mutating command, best-effort `remo-web@` authorized_keys revocation on removed instances, a `--force` flag that bypasses the fingerprint fast-path, and a deployment-reported mirror-identity marker (generation counter + last-push descriptor) that lets a second workstation detect it is about to overwrite another's mirror. Finally, fix the `web/state.py` mode-detection wart so bare-metal `remo web serve` (where a personal `~/.ssh/id_*` is always present) is adoptable, via an explicit env override plus a narrowed heuristic — without weakening the Docker read-only-mount classification.

All work is Python in the existing three-layer package; the only wire-contract change is additive fields on `GET /setup/status` and `PUT /setup/registry` (both already pairing-gated). No registry schema change; the push-cache format grows a per-deployment `mirror_generation` field under a bumped `cache_version`.

## Technical Context

**Language/Version**: Python 3.11+

**Primary Dependencies**: Click (CLI), stdlib `urllib.request` (setup-API client — `core/web_adopt.py` must stay importable without the `web` extra), FastAPI/Uvicorn (service side, `web` extra only), `subprocess` → `ssh`/`ssh-keyscan`/`ssh-keygen`. No new runtime dependencies.

**Storage**: JSON files. Registry v2 (`registry.json`) via `core/registry.py` (unchanged). Non-secret push cache `~/.config/remo/web-service.json` (`cache_version` bump 2 → 3, adds per-deployment `mirror_generation`). New service-side mirror-meta file under the writable `web-identity/` state dir (generation counter + last-push descriptor).

**Testing**: pytest (`tests/unit/**`, `tests/integration/**`); existing suites `test_web_push.py`, `test_web_adopt_*`, `test_state.py`, `test_setup_api.py`, `test_reconcile.py`, `test_web_cli_parity.py` extended; `mypy src/remo_cli`, `ruff check src/remo_cli`.

**Target Platform**: Linux/macOS workstation (CLI) + Linux container or bare-metal host (web service).

**Project Type**: Single Python package (CLI + optional web service), three-layer architecture (`cli/` → `providers/` + `web/` → `core/`).

**Performance Goals**: `remo web status` completes in < 2 s and makes zero network/SSH connections (SC-003). Push per-instance work unchanged from today.

**Constraints**: `core/web_adopt.py` MUST NOT import anything from `remo_cli.web.*` or optional deps (stdlib + `core`/`models` only). Trust model invariants preserved (service-scoped identity only, workstation-verified host keys only, single `remo-web@<deployment>` marker, pairing-gated surface, never copy personal keys). Revocation and authorization edits MUST be marker-scoped, atomic, idempotent.

**Scale/Scope**: Tens of registry instances per deployment; a workstation may cache a handful of deployments.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

The constitution is Ansible-centric; this feature touches no Ansible (roles/playbooks unchanged). The five principles map to Python obligations as follows:

- **I. Defensive Variable Access (Ansible)** — N/A (no Ansible changes). No playbook or template is modified.
- **II. Test All Conditional Paths** — PASS (planned). Every new branch is covered: first-push vs. re-sync auto-detection, force on/off, cache present/absent nudge, revocation reachable/unreachable, flap detected/not, and all four mode-detection outcomes (adopted / mount_configured via non-writable dir / mount_configured via explicit override / bare-metal-with-personal-key → adopted). Enumerated in quickstart.md.
- **III. Idempotent by Default** — PASS (planned). Re-running a push is a byte-level no-op on `authorized_keys`; revocation of an already-revoked instance is a no-op; the mirror-meta write and cache write are atomic (temp-file + `os.replace`). `remo web status` is pure-read.
- **IV. Fail Fast with Clear Messages** — PASS (planned). Existing hard-failure guards retained (mount-configured, empty-registry, payload-version skew, dormant surface); new failure surfaces (multi-deployment status without selector, flap abort, revocation-could-not-be-performed) each carry actionable remediation text. No error is swallowed silently.
- **V. Documentation Reflects Reality** — PASS (planned). `docs/web-session-interface.md` adopt+push sections are consolidated into one adoption section covering the unified push, status, nudge, revocation, `--force`, flap detection, and the mode override (FR-031); `CLAUDE.md` Recent Changes updated.

**Result**: No violations. No entries in Complexity Tracking.

**Post-design re-check (after Phase 1)**: Still PASS. The design added only additive, backward-compatible wire fields (setup-status marker), a versioned push-cache bump with graceful degradation, and a stdlib-only `core/web_drift.py` — no new dependencies, no Ansible surface, no new project. Idempotence (revocation/authorize no-ops, atomic writes), fail-fast (typed hard failures with remediation), and docs-consolidation obligations are all reflected in the contracts and quickstart.

## Project Structure

### Documentation (this feature)

```text
specs/017-web-adopt-simplify/
├── plan.md              # This file
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output
├── quickstart.md        # Phase 1 output
├── contracts/           # Phase 1 output
│   ├── setup-status-marker.md   # additive GET /setup/status + PUT /setup/registry response fields
│   ├── cli-web-push.md          # unified `remo web push` (+ deprecated `adopt` alias, --force)
│   ├── cli-web-status.md         # `remo web status` offline drift command
│   └── revocation.md             # best-effort authorized_keys revocation contract
└── checklists/
    └── requirements.md  # (from /speckit-specify)
```

### Source Code (repository root)

```text
src/remo_cli/
├── cli/
│   ├── main.py                 # (unchanged hook wiring) — nudge is called by mutating commands
│   ├── added.py                # add/remove: emit out-of-date nudge on success (FR-013)
│   ├── web.py                  # collapse adopt→deprecated alias; push gains --force; new `status` command
│   └── providers/
│       ├── incus.py            # create/destroy: emit nudge on success
│       ├── proxmox.py          #   "
│       ├── hetzner.py          #   "
│       └── aws.py              #   " (+ stop/start/reboot leave registry unchanged → no nudge)
├── core/
│   ├── web_adopt.py            # MERGE _adopt_flow/_push_flow → one run_push; add --force; revocation;
│   │                           #   flap detection; cache_version 3 (+ mirror_generation)
│   ├── web_drift.py            # NEW: offline registry-vs-cache diff + shared nudge helper (stdlib only)
│   └── reconcile.py            # run_sync: emit nudge on successful apply (FR-013, sync path)
├── web/
│   ├── state.py                # mode-detection fix: explicit override + narrowed heuristic (US6)
│   ├── config.py               # WebSettings: new REMO_WEB_MODE override + mirror-meta path
│   └── api/
│       └── setup.py            # GET /status: mirror-identity marker; PUT /registry: bump generation,
│                               #   return new marker; _apply_payload writes mirror-meta atomically
└── models/
    └── (no new model files required; dataclasses live in web_adopt.py / web_drift.py)

tests/
├── unit/core/test_web_push.py            # unified flow, force, flap, revocation
├── unit/core/test_web_drift.py           # NEW: offline diff + nudge gating
├── unit/core/test_web_adopt_*.py         # trust/payload/authorize/code — unchanged invariants
├── unit/cli/test_web_adopt_cmd.py        # adopt-alias deprecation, push --force, status command
├── unit/web/test_state.py                # four mode-detection outcomes (US6)
├── unit/web/test_setup_api.py            # status marker + PUT generation bump
└── integration/
    ├── test_web_adopt_e2e.py             # end-to-end unified push (adopt then re-sync)
    └── test_web_cli_parity.py            # nudge fires across create/destroy/sync/add/remove
```

**Structure Decision**: Single existing Python package, three-layer architecture preserved. New shared logic goes in `core/web_drift.py` (stdlib-only, importable without the `web` extra, mirroring the `core/web_adopt.py` constraint) so both the `status` command and the post-mutation nudge reuse one offline-diff implementation. Service-side additive fields live in the already-pairing-gated `web/api/setup.py`.

## Complexity Tracking

No constitution violations — section intentionally empty.
