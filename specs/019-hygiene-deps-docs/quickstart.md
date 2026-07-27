# Quickstart: Validating 019 — Dependency, Dead-Code & Documentation Hygiene

Runnable validation for every success criterion. Run from the repository root on branch
`019-hygiene-deps-docs`.

Details live in [`contracts/docs-structure-check.md`](./contracts/docs-structure-check.md),
[`contracts/cli-surface-delta.md`](./contracts/cli-surface-delta.md), and
[`data-model.md`](./data-model.md); this file is the run guide.

## Prerequisites

```bash
uv sync --all-extras
uv run remo --version        # expect 2.2.0 or later
```

---

## 1. Full suite and lint — the baseline gate

```bash
uv run pytest --tb=short -q
uv run ruff check src/remo_cli
uv run mypy src/remo_cli
```

**Expected**: all pass. Test count should be ~1746 plus the new drift and Hetzner-HTTP cases, minus none
— no test is deleted by this feature.

> `ruff` matters more than usual here: removing `_parse_pct_json` orphans `import json` in
> `providers/proxmox.py:15`, and F401 is a hard CI failure (research R7).

**Covers**: SC-007, SC-010, FR-008, FR-026.

---

## 2. SC-002 / FR-009 — the phantom `remo init` is gone

```bash
grep -rn "remo init" --include="*.md" --include="*.sh" . \
  | grep -v "^./specs/" | grep -v node_modules
```

**Expected**: no output.

Four known sites must all be clear — `README.md` (installation, command reference, troubleshooting),
`docs/aws.md`, and `docs/install.sh`'s post-install hint.

Then confirm the replacement claim is true — collections really do install automatically:

```bash
grep -n "_ensure_collections" -A6 src/remo_cli/core/ansible_runner.py | head -20
```

**Expected**: the hash-marker auto-install is present, matching what the docs now say (FR-010).

**Covers**: SC-001, SC-002, FR-009, FR-010.

---

## 3. SC-003 / FR-011 — structure matches the tree

```bash
uv run pytest tests/unit/test_docs_structure.py -v
```

**Expected**: all pass, including the real-repository case (T-1).

Confirm the check actually bites — this is the SC-008 acceptance, and it must be done by hand once:

```bash
printf 'PLACEHOLDER = True\n' > src/remo_cli/core/_drift_probe.py
uv run pytest tests/unit/test_docs_structure.py -q     # expect FAILURE naming _drift_probe.py
rm src/remo_cli/core/_drift_probe.py
uv run pytest tests/unit/test_docs_structure.py -q     # expect PASS again
```

**Expected on the middle run**: a failure whose message names `src/remo_cli/core/_drift_probe.py`,
states both remediation directions, mentions `EXCLUDED_FROM_DOCS`, and points at
`docs/maintaining-claude-md.md` — see the required shape in the check contract, §4.

Then verify the procedure doc stands alone (FR-019a): a reader who has never opened the test module
should be able to resolve that failure using `docs/maintaining-claude-md.md` only.

**Covers**: SC-003, SC-008, FR-011, FR-017, FR-018, FR-019, FR-019a, FR-020, FR-021.

---

## 4. SC-004 / FR-012 — every documented install command works

Extract and run them rather than eyeballing:

```bash
grep -n "uv sync" CLAUDE.md AGENTS.md
```

**Expected**: only `--all-extras`, `--extra dev`, `--extra web`. No `--extra aws`, no `--extra hetzner`.

```bash
uv sync --extra web --dry-run
uv sync --extra dev --dry-run
uv sync --extra aws --dry-run       # expect FAILURE — proves the extra really is absent
```

**Covers**: SC-004, FR-012.

---

## 5. FR-013 — documented CLI surface matches the registered one

```bash
uv run remo --help | sed -n '/Commands:/,$p'
```

**Expected**: `add`, `completion`, `cp`, `remove`, `shell`, `web`, plus `aws`, `hetzner`, `incus`,
`proxmox`. Cross-check that CLAUDE.md's commands section names all of them — `add`, `remove`, and
`completion` are the three it omits today.

**Covers**: SC-004, FR-013.

---

## 6. FR-001 / FR-014 — dependency rationale is present and accurate

```bash
sed -n '/^dependencies/,/^]/p' pyproject.toml
grep -n "httpx2" -B4 pyproject.toml
```

**Expected**: `hcloud` and `boto3` each carry a comment naming their Ansible collection consumer and the
`ansible_playbook_python` assumption; `httpx2` states it is not a misspelling of `httpx`.

Prove the not-a-typo claim independently:

```bash
uv run python -c "import httpx2, starlette; print(httpx2.__name__, starlette.__version__)"
uv run python -c "import inspect, starlette.testclient as t; \
print([l for l in inspect.getsource(t).splitlines() if 'httpx' in l and 'import' in l])"
```

**Expected**: `httpx2` imports cleanly, and Starlette's testclient shows `import httpx2 as httpx` before
its `import httpx` fallback.

Confirm the footprint did not move (SC-005a):

```bash
uv run python -c "
import importlib.metadata as md
print(sorted(r for r in md.requires('remo-cli') if 'extra ==' not in r))"
```

**Expected**, unchanged by this feature — the unconditional five:

```
['ansible-core<2.20.0,>=2.18.0', 'boto3', 'click>=8.1', 'hcloud', 'inquirerpy>=0.3.4']
```

The `extra ==` filter matters: `md.requires()` returns extras-marked entries in the same list.

**Covers**: SC-005, SC-005a, FR-001, FR-002, FR-004, FR-004a, FR-007, FR-014.

---

## 7. FR-005 / FR-006 — unreachable guards are annotated, not silently kept

```bash
grep -n "94" src/remo_cli/providers/aws.py src/remo_cli/core/provider_registry.py
```

**Expected**: both missing-SDK paths in `aws.py` (the raising `_require_boto3` and the silent-return
path around line 88) and the `sdk_extra` message in `provider_registry.py` carry a comment naming issue
#94 as what makes them load-bearing again.

**Covers**: FR-005, FR-006.

---

## 8. FR-025 — `create --yes` is gone, everywhere else is untouched

```bash
for p in incus hetzner aws proxmox; do
  uv run remo $p create --yes 2>&1 | tail -1        # expect: No such option: --yes
  uv run remo $p create --help | grep -c -- "--yes" # expect: 0
done

for p in incus hetzner aws proxmox; do
  uv run remo $p destroy --help | grep -c -- "--yes"          # expect: 1
  uv run remo $p sync --help | grep -c -- "--yes"             # expect: 1
  uv run remo $p snapshot restore --help | grep -c -- "--yes" # expect: 1
done
uv run remo remove --help | grep -c -- "--yes"                # expect: 1

grep -rn "deprecated_options\|DeprecatedOption\|CREATE_YES" src/    # expect: no output
uv run pytest tests/unit/cli/test_surface_preservation.py -q        # expect: pass
```

**Covers**: SC-010, FR-025, and contract `cli-surface-delta.md` V-1 … V-7.

---

## 9. FR-022 / FR-023 — dead code gone, Hetzner HTTP consolidated

```bash
grep -rn "_parse_pct_json" src/ tests/                          # expect: no output
grep -c "^import json" src/remo_cli/providers/proxmox.py        # expect: 0

grep -n "urllib.request.Request" src/remo_cli/providers/hetzner.py
```

**Expected**: exactly **one** `urllib.request.Request` in `hetzner.py` — inside `_hetzner_api`. Today
there are four.

```bash
uv run pytest tests/unit/providers/test_hetzner_http.py -v
uv run pytest tests/unit/providers/ tests/integration/test_sync_reconcile.py -q
```

**Expected**: the new per-call-site tests pass, and every pre-existing Hetzner test passes **unmodified**
— that is the FR-024 no-behavior-change evidence. Per research R3 the sites differ in raise-vs-swallow,
timeout (15s vs 30s), and message text; the new tests pin each.

**Covers**: SC-009, FR-022, FR-023, FR-024.

---

## 10. SC-011 / FR-015 — `AGENTS.md` describes *this* repository

```bash
grep -n "src/remo/\|known_hosts\|notifier\|telegram\|structlog\|tomli" AGENTS.md
```

**Expected**: no output. Today this returns the wrong package path, the superseded flat-file registry,
and three features (007/008/009) that have no `specs/` directory here.

```bash
diff <(sed -n '/## Project Structure/,/^## /p' CLAUDE.md) \
     <(sed -n '/## Project Structure/,/^## /p' AGENTS.md)
```

**Expected**: no differences — both orientation documents describe the same tree, and both are covered by
the drift check in step 3.

**Covers**: SC-011, FR-015.

---

## 11. FR-016 — corrections survive the generator

`.specify/scripts/bash/update-agent-context.sh` is live: it appends to `## Active Technologies` and
prepends to `## Recent Changes` in **all existing agent files**, `AGENTS.md` included (research R5).

```bash
cp CLAUDE.md /tmp/claude-before.md && cp AGENTS.md /tmp/agents-before.md
.specify/scripts/bash/update-agent-context.sh claude
diff /tmp/claude-before.md CLAUDE.md
```

**Expected**: the only differences are an added Active Technologies line and a Recent Changes entry, both
derived from this feature's `plan.md`. Specifically the appended dependency line must read as *no
change / annotation only* — **not** reintroduce "boto3 (AWS, optional), hcloud (Hetzner, optional)". The
project-structure and commands sections must be untouched, confirming they are hand-maintained and that
step 3's check is the right enforcement point.

**Covers**: FR-016.

---

## 12. SC-006 — Hetzner lifecycle on a clean install

Requires live credentials; the repository's provider smoke workflows are the intended vehicle.

```bash
uv venv /tmp/v-clean && VIRTUAL_ENV=/tmp/v-clean uv pip install .
VIRTUAL_ENV=/tmp/v-clean uv run python -c "import hcloud, boto3; print('SDKs present')"
```

**Expected**: both import — the property `hetzner destroy`/`resize` depend on, since those playbooks have
no SDK preflight (deferred to #94, see plan Complexity Tracking). Then exercise `create`, `destroy`, and
`resize` via the smoke workflow, confirming order-independence.

**Covers**: SC-006, FR-002, FR-003.

---

## Coverage map

| Criterion | Step |
|---|---|
| SC-001, SC-002 | 2 |
| SC-003, SC-008 | 3 |
| SC-004 | 4, 5 |
| SC-005, SC-005a | 6 |
| SC-006 | 12 |
| SC-007 | 1 |
| SC-009 | 9 |
| SC-010 | 1, 8 |
| SC-011 | 10 |
