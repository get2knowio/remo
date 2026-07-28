# Quickstart Validation: CLI Plane Separation

**Feature**: 021-cli-plane-separation

Runnable checks proving the feature works end-to-end. Shapes are defined in
[contracts/cli-surface.md](contracts/cli-surface.md); descriptor mechanics in
[contracts/descriptor-schema.md](contracts/descriptor-schema.md).

## Prerequisites

```bash
uv sync --all-extras          # dev install
```

No live provider needed for the unit-level checks; the last section exercises a real Incus host
if one is available.

## 1. Surface shape (no infrastructure required)

```bash
# New verbs present; update gone (expect: upgrade/resize/tag listed, no update)
uv run remo incus --help
uv run remo proxmox --help

# host subgroup only where host commands exist
uv run remo incus --help | grep -A1 '^  host'        # present
uv run remo aws --help | grep '^  host' && echo "FAIL: aws has host group"   # absent
uv run remo hetzner --help | grep '^  host' && echo "FAIL"                   # absent

# tag only on marker-supporting providers
uv run remo hetzner tag --help                       # exists
uv run remo aws tag --help; echo "exit=$? (expect 2: unknown command)"

# removed spellings are hard errors
uv run remo incus update --name x; echo "exit=$? (expect 2)"
uv run remo incus bootstrap; echo "exit=$? (expect 2)"
uv run remo incus destroy --user root --name x; echo "expect: No such option: --user"

# renamed flags present
uv run remo incus upgrade --help | grep -- '--host-user'
uv run remo proxmox upgrade --help | grep -- '--node-user'

# resize dimensions per provider (aws/hetzner must NOT show cores/memory)
uv run remo proxmox resize --help | grep -E -- '--(volume-size|cores|memory)'
uv run remo aws resize --help | grep -E -- '--(cores|memory)' && echo "FAIL"
```

## 2. Behavior invariants (unit suites)

```bash
# Full gate set
uv run pytest

# Focused suites for this feature's guarantees:
uv run pytest tests/unit/providers/test_provider_conformance.py   # SC-005: fifth provider gets upgrade/resize/tag/host from descriptor alone
uv run pytest tests/unit/cli/test_surface_preservation.py         # rewritten frozen baseline matches
uv run pytest tests/unit/providers/test_incus_marker.py \
              tests/unit/providers/test_proxmox_marker.py \
              tests/unit/providers/test_hetzner_label.py          # SC-001/SC-002: upgrade writes nothing; tag writes exactly once, no-op second run
uv run pytest tests/unit/core/test_migration_tagging_notice.py \
              tests/unit/core/test_reconcile.py                   # SC-003: notices recommend `tag`
uv run pytest tests/unit/providers/test_added_provider_guard.py   # ssh-host guard on all three new verbs
uv run pytest tests/unit/cli/test_shell.py                        # prompt names `remo <type> upgrade <name>`

# Zero stragglers in current-surface docs/code (historical archives exempt):
grep -rn 'remo \(incus\|proxmox\|aws\|hetzner\|<type>\|<provider>\|<platform>\) update' \
  README.md docs/ src/ --include='*.py' --include='*.md' \
  | grep -v feature-history.md && echo "FAIL: update stragglers" || echo "OK"

# Docs/quality gates
uv run pytest tests/unit/test_docs_structure.py
uv run ruff check src/remo_cli && uv run mypy src/remo_cli
```

## 3. End-to-end against a live Incus host (optional)

With a bootstrapped Incus host and a registered container `dev1`:

```bash
remo incus host bootstrap lab1 --host-user $USER      # today's bootstrap behavior, new home
remo incus tag dev1                                   # expect: one `incus config set` (or already-tagged notice)
remo incus tag dev1                                   # expect: "already tagged", exit 0, zero writes
remo incus resize dev1 --memory 4096                  # expect: resize playbook only, no configure play
remo incus resize dev1                                # expect: exit 1, message lists --volume-size/--cores/--memory
remo incus upgrade dev1                               # expect: configure playbook only; `incus config show dev1`
                                                      #   unchanged before/after (no marker/limit writes)
remo shell dev1                                       # on version mismatch: prompt names `remo incus upgrade dev1`
```

## Expected outcomes summary

| Check | Pass condition |
|---|---|
| Help surface | `upgrade`/`resize` everywhere; `tag` on incus/proxmox/hetzner only; `host` on incus/proxmox only; `update`/flat `bootstrap`/`--user` gone |
| SC-001 | `upgrade` performs zero provider-side writes (all four providers, seam-mocked) |
| SC-002 | `tag` = exactly one provider write, zero Ansible; second run no-op |
| SC-003 | migration notice, sync remedy, shell prompt each print a real, action-matching command |
| SC-004 | help/docs grep clean of removed surface (feature-history/CHANGELOG exempt) |
| SC-005 | conformance suite passes with FakeProvider's descriptor-only surface |
| SC-006 | `uv run pytest`, ruff, mypy, docs-structure gates all green |
