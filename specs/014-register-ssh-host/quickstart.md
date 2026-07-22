# Quickstart & Validation: Register an SSH-Reachable Host (`remo add`)

Runnable scenarios that prove the feature end-to-end. Maps to the spec's Success
Criteria (SC-001..007) and the command contracts. Use an isolated registry so you
never touch your real one:

```bash
export REMO_HOME="$(mktemp -d)/remo"      # scratch registry for this walkthrough
uv sync                                    # dev install
```

Prerequisites: a box you can already `ssh` into (any VM/container/host). No
hypervisor or cloud access required (FR-001).

---

## Scenario 1 — Add and connect (SC-001, US1)

```bash
uv run remo add mybox user@192.0.2.10
# → success; reports effective user and "remo shell mybox"
uv run remo shell mybox                    # lands in a shell on the box
```

**Expect**: one `ssh:mybox:…` line in `$REMO_HOME/known_hosts`; `remo shell`
opens a direct SSH session. Two commands, no manual registry editing.

## Scenario 2 — Custom port + identity (SC-002)

```bash
uv run remo add api dev@10.0.0.9:2222 --identity ~/.ssh/api_ed25519
grep '^ssh:api:' "$REMO_HOME/known_hosts"
# → ssh:api:10.0.0.9:dev:2222:direct:/home/you/.ssh/api_ed25519
uv run remo shell api                      # connects on :2222 using -i the identity
```

**Expect**: connection succeeds with **no** hand-editing of the registry or
`~/.ssh/config` after `add`.

## Scenario 3 — Default user reported & overridable (US1.4, FR-003)

```bash
uv run remo add plainbox 192.0.2.20        # no user@ → defaults to 'remo', reported back
uv run remo add plainbox 192.0.2.20 --user ubuntu   # override; in-place update
```

## Scenario 4 — In-place update, no duplicate (SC-003, US2)

```bash
uv run remo add api dev@10.0.0.9:2222
uv run remo add api dev@10.0.0.99:2222 --yes        # changed IP
grep -c '^ssh:api:' "$REMO_HOME/known_hosts"        # → 1 (updated, not duplicated)
```

## Scenario 5 — Remove is local-only (SC-004, US2)

```bash
uv run remo remove api --yes
grep '^ssh:api:' "$REMO_HOME/known_hosts" || echo "gone"   # → gone
uv run remo shell api                                       # → "no environment named 'api'"
```

**Expect**: entry deleted; **no** SSH/API call made to the remote host.

## Scenario 6 — Name collision with a provider host is refused (SC-005, FR-010)

```bash
# Given some provider entry, e.g. an incus 'devbox' already registered:
uv run remo add devbox user@192.0.2.30
# → refused; message names the conflicting provider entry; exit ≠ 0; no write
```

## Scenario 7 — Plain login shell when remo-host is absent (SC-006, FR-011)

```bash
# 'mybox' has no remo-host / managed tooling installed:
uv run remo shell mybox
# → NO "has no version info / Update tools?" prompt; drops straight into a login shell
```

## Scenario 8 — IPv6 literal rejected, not persisted (FR-013, D4)

```bash
uv run remo add v6box '::1'                 # → rejected: use a hostname or ~/.ssh/config alias
uv run remo add v6box 'user@[2001:db8::1]:22'   # → rejected (bracketed form out of scope)
grep -c '^ssh:v6box:' "$REMO_HOME/known_hosts"  # → 0 (nothing written)
```

## Scenario 9 — `--verify` is fail-closed (FR-014, US3)

```bash
uv run remo add deadbox user@203.0.113.254 --verify   # unreachable
echo "exit=$?"                                         # → non-zero
grep -c '^ssh:deadbox:' "$REMO_HOME/known_hosts"       # → 0 (declined; no entry)

uv run remo add mybox user@192.0.2.10 --verify         # reachable → registers after check
```

## Scenario 10 — Backward-compatible registry parsing (SC-007)

```bash
# Pre-existing provider lines (4/6/7-field) load unchanged alongside new ssh lines:
uv run remo shell           # picker lists provider hosts AND added hosts together (FR-006)
```

---

## Automated checks

```bash
uv run pytest tests/unit/providers/test_added.py \
              tests/unit/cli/test_added_cmd.py \
              tests/unit/test_host_ssh_type.py \
              tests/unit/core/test_ssh_added.py
uv run mypy src/remo_cli
uv run ruff check src/remo_cli
```

See [contracts/add-command.md](./contracts/add-command.md) and
[contracts/remove-command.md](./contracts/remove-command.md) for the full
precondition/exit-code matrices these scenarios exercise.
