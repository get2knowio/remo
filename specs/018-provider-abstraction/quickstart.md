# Quickstart: Validating the Provider Abstraction

**Feature**: 018-provider-abstraction. Run from repo root. Prerequisites: `uv sync --all-extras`.

## 1. Full suite + static gates

```bash
uv run pytest                      # entire suite must pass (FR-021)
uv run mypy src/remo_cli           # Protocol conformance is statically checked
uv run ruff check src/remo_cli    # no providers-package ignores remain
```

## 2. Architecture gates (SC-003)

```bash
uv run pytest tests/unit/test_architecture.py -v
```

Expected: zero `sys.exit` in `src/remo_cli/providers/`; zero `noqa: SLF001` / private provider access in `src/remo_cli/cli/`. Cross-check by hand:

```bash
grep -rn "sys.exit" src/remo_cli/providers/        # → no matches
grep -rn "noqa: SLF001" src/remo_cli/cli/          # → no matches
```

## 3. Conformance + fifth-provider proof (SC-001, FR-022)

```bash
uv run pytest tests/unit/providers/test_provider_conformance.py -v
```

Expected: parametrized pass for incus/proxmox/aws/hetzner **and** `fake` — the FakeProvider case registers a descriptor in a fixture and asserts its full `remo fake …` group mounts with standard flags, proving the no-existing-files-touched path.

## 4. CLI uniformity & surface preservation (SC-002)

```bash
uv run pytest tests/unit/cli/test_cli_uniformity.py -v
for p in incus proxmox aws hetzner; do uv run remo $p create --help; done
```

Expected: shared options render identically everywhere; per-provider extras match `contracts/cli-surface.md`; `create --help` shows the descriptor default name; `create --yes` prints the deprecation notice and nothing else changes.

## 5. Startup laziness (SC-008, FR-024)

```bash
uv run pytest tests/unit/cli/test_startup_imports.py -v
uv run python -c "import sys, click; from remo_cli.cli.main import cli; \
  click.Context(cli).get_help(); \
  assert 'boto3' not in sys.modules and 'hcloud' not in sys.modules; print('lazy OK')"
```

## 6. No silent dispatch (SC-004)

```bash
uv run pytest tests/unit/cli/test_shell.py -k unknown_type -v
```

Expected: a registry entry with an unrecognized type makes the shell update path print an explicit error naming the type and exit 1 (today it silently returns 0); `ssh`-type entries are still explicitly skipped.

## 7. #87 merge semantics (SC-007)

```bash
uv run pytest tests/unit/core/test_reconcile.py -k observed -v
uv run pytest tests/unit/providers/test_aws_sync.py -v
```

Expected: the four acceptance cases in `contracts/sync-merge.md` pass (preserve unobserved `access_mode`, tagged value wins, new adoption defaults to `ssm`, idempotent second plan).

## 8. Behavior smoke (optional, needs real infra)

```bash
uv run remo incus list && uv run remo incus snapshot list
uv run remo aws sync --dry-run
uv run remo incus destroy --name <test-instance>      # confirm ordering: guard → snapshot cleanup → prompt → teardown → registry removal
```

Exit-code spot checks: success 0; declined confirmation 3; failed operation 1 (playbook rc quoted in the message — the documented normalization from `contracts/errors.md`).

## Done when

All of 1–7 green in CI; every SC-001…SC-008 mapped check above passes; CHANGELOG documents the `--yes` deprecation and the playbook-rc exit normalization.
