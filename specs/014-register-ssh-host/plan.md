# Implementation Plan: Register an SSH-Reachable Host (`remo add`)

**Branch**: `014-register-ssh-host` | **Date**: 2026-07-22 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/014-register-ssh-host/spec.md`

## Summary

Add a provider-neutral `remo add <name> <target>` that registers a single
SSH-reachable environment into the existing colon-delimited known-hosts registry,
and a matching `remo remove <name>` that deregisters it (local-only, no remote
action). Added hosts are stored as a **new registry `type` = `ssh`** with
`access_mode = direct`, so they flow through the *existing* `direct`-mode SSH
connection path (`remo shell`, `remo cp`) and the interactive picker with no
special-casing by the user.

Technical approach, grounded in the current code:

- **Storage**: reuse the existing `KnownHost` positional serialization — no
  format extension. For `type=ssh`, the otherwise-provider-specific slots carry
  the SSH coordinates: `instance_id` = port, `access_mode` = `direct`,
  `region` = identity path. This keeps the current 4/6/7-field forms intact and
  backward-compatible (SC-007). Two type-gated helper properties (`ssh_port`,
  `ssh_identity`) localize the field-overloading in the model.
- **Connection**: extend `build_ssh_opts()` (the single shared SSH-argv builder
  used by both CLI and web) to emit `-o Port=<port>` and fold in the stored
  identity **only for `type=ssh`**, so existing incus/proxmox/aws/hetzner argv is
  byte-identical. The `identity_file` param already exists there (web-adopt R6);
  an explicit param still wins over the stored identity.
- **Unmanaged degradation (FR-011)**: `remo shell` skips the pre-connect
  `remo-host`/tools version check for `type=ssh` — a missing marker on an added
  host is "unknown/unmanaged," not a prompt to run a nonexistent provider update.
- **Safety**: `add` checks the *whole* registry for a name collision (the
  registry dedupes only within `(type, name)`), refusing to shadow a
  provider-managed entry (FR-010) and treating a same-named existing `ssh` entry
  as an in-place update (FR-007). `remove` refuses non-`ssh` names (FR-009).
  Target parsing rejects un-bracketed IPv6 literals and colon-bearing identity
  paths before any write (FR-013), so a malformed target never corrupts the
  colon-delimited registry.

## Technical Context

**Language/Version**: Python 3.11+ (`from __future__ import annotations`, type hints)

**Primary Dependencies**: Click (CLI). No new runtime dependencies. SSH is the
system `ssh` binary via `subprocess` (already the connection substrate).

**Storage**: Existing flat-file registry `~/.config/remo/known_hosts`
(colon-delimited `KnownHost` lines). No schema/format change — a new `type`
value only.

**Testing**: pytest (`uv run pytest`), `mocker.patch` of SSH/subprocess and
registry helpers, Click `CliRunner` for the command layer. mypy + ruff.

**Target Platform**: Developer workstation CLI (Linux/macOS).

**Project Type**: Single-project Python CLI (three-layer: `cli/` → `providers/`
→ `core/`).

**Performance Goals**: N/A — one registry read/write per invocation; the only
network round-trip is the *opt-in* `--verify` SSH check (FR-014).

**Constraints**: Backward-compatible registry parsing (SC-007); no hypervisor/
cloud access required (FR-001); `--verify` fail-closed and network-free when not
requested (FR-014).

**Scale/Scope**: Two new CLI commands (`add`, `remove`); one new provider module;
small extensions to the model, the SSH builder, and the shell version-check gate.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

The constitution's principles I and the Ansible-Specific Standards target Ansible
code; this feature adds **no Ansible** (pure Python CLI + registry), so those are
N/A. The generally-applicable principles:

- **II. Test All Conditional Paths** — PASS (planned). The feature is branch-heavy
  (verify pass/fail; collision refuse vs in-place update; IPv6 reject; port
  present/default; identity present/absent; added-host version-check skip;
  remove refuses provider host). Tasks enumerate a test per branch, both truthy
  and falsy, matching the 013 test approach.
- **III. Idempotent by Default** — PASS. Re-running `add` with the same name
  updates in place (no duplicate line, FR-007/SC-003); `remove` is a no-op when
  the entry is already absent (`remove_known_host` already tolerates this).
- **IV. Fail Fast with Clear Messages** — PASS. Name collisions, malformed/IPv6
  targets, colon-bearing identities, `--verify` failures, and provider ops on an
  added host all exit non-zero with an actionable message (FR-009/010/012/013/014).
- **V. Documentation Reflects Reality** — PASS (planned). README gains an `add`/
  `remove` section; the quickstart doubles as the tested validation guide.

No violations → Complexity Tracking is empty.

## Project Structure

### Documentation (this feature)

```text
specs/014-register-ssh-host/
├── plan.md              # This file
├── research.md          # Phase 0 output — decisions & rationale
├── data-model.md        # Phase 1 output — KnownHost `ssh` type & encoding
├── quickstart.md        # Phase 1 output — tested validation scenarios
├── contracts/           # Phase 1 output — CLI command contracts
│   ├── add-command.md
│   └── remove-command.md
└── tasks.md             # Phase 2 output (/speckit-tasks — NOT created here)
```

### Source Code (repository root)

```text
src/remo_cli/
├── cli/
│   ├── main.py              # + register `add` and `remove` top-level commands
│   └── added.py             # NEW — Click layer for `remo add` / `remo remove`
├── providers/
│   └── added.py             # NEW — add/remove/verify business logic + target parse
│                            #        (no Click imports; three-layer compliant)
├── core/
│   ├── config.py            # + ADDED_HOST_TYPE="ssh", DEFAULT_ADDED_HOST_USER,
│   │                        #   DEFAULT_SSH_PORT (fixed constants, single site)
│   ├── ssh.py               # build_ssh_opts(): type-gated `-o Port=` + stored
│   │                        #   identity; shell version-check gate lives in shell.py
│   └── validation.py        # (reuse validate_name / validate_port; add target/
│                            #   identity checks as needed)
├── cli/
│   └── shell.py             # skip pre-connect tools version check for type=ssh
└── models/
    └── host.py              # + ssh_port / ssh_identity type-gated properties

tests/unit/
├── providers/test_added.py          # NEW — target parsing, add/remove, verify, collisions
├── cli/test_added_cmd.py            # NEW — CliRunner: add/remove flags, exit codes
├── test_host_ssh_type.py            # NEW — serialization round-trip, ssh_port/ssh_identity
└── core/test_ssh_added.py           # NEW — build_ssh_opts port/identity for ssh; others unchanged
```

**Structure Decision**: Single-project Python CLI, existing three-layer
architecture (`cli/` parsing only → `providers/` business logic → `core/`
provider-agnostic utilities). The added-host logic is a new `providers/added.py`
(not a hypervisor/cloud provider, but occupies the same layer and pattern);
`core/` changes are minimal, backward-compatible extensions to shared utilities.

## Complexity Tracking

No constitution violations — no entries.
