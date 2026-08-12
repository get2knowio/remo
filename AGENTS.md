# remo Development Guidelines

Auto-generated from all feature plans. Last updated: 2026-07-27

## Constitution

See `.specify/memory/constitution.md` (v2.0.1) for the full text. The eight
non-negotiable principles, in short:

| # | Principle | One-line rule |
|---|-----------|---------------|
| I | Layered Architecture with One-Way Dependencies | `cli/` → `providers/` → `core/`, never backwards |
| II | Providers Are Declared, Not Special-Cased | A new provider = one module + one descriptor, zero edits elsewhere |
| III | Typed Errors, One Exit Boundary, Actionable Messages | Raise `core/errors.py`; only `provider_command` maps to an exit code |
| IV | Contracts Are Generated, Never Hand-Authored | The FastAPI app is the source of truth for every shape `frontend/` consumes |
| V | Defensive Variable Access (Ansible) | Every registered-variable access uses `\| default()` |
| VI | Test Every Path That Can Skip or Fail | Error/skip/abort paths are covered, not just the happy path |
| VII | Idempotent and Re-runnable by Default | A second run is a no-op; registry writes go through `core/registry.py` |
| VIII | Documentation Reflects Reality, and CI Proves It | Structure diagrams and docs ship in the same change as the code |

Principles I, IV, and VIII are machine-enforced — see [Quality Gates](#quality-gates).

## Active Technologies
- Ansible 2.14+ / YAML + `ansible.builtin`, `community.general` (existing), Incus CLI (local) (002-incus-container-support)
- N/A (Incus storage pools already configured by 001-bootstrap-incus-host) (002-incus-container-support)
- Python 3.11+ + Click (CLI framework), InquirerPy (interactive picker), boto3 (unconditional runtime dependency, used by the CLI's own lazy `import boto3` in `providers/aws.py` and by the Ansible `amazon.aws`/`community.aws` collections), hcloud (unconditional runtime dependency, consumed by the Ansible layer, not the CLI's own Python code) (003-python-cli-rewrite)
- Versioned JSON registry (`~/.config/remo/registry.json`, format v2 — named fields per type, no positional overloading; single accessor `core/registry.py` owns parse/serialize/validate/lock/migrate for CLI, providers, and the web service). Legacy `~/.config/remo/known_hosts` (colon-delimited) is read-only migration input, lazily migrated to v2 on first CLI read and renamed to `known_hosts.v1.bak`. (003-python-cli-rewrite; superseded by 015-registry-v2)
- Cross-provider snapshot model (`models/snapshot.py`) + shared helpers in `core/snapshot.py` (name generator, validator, table formatter, destroy-time cleanup hook). No new runtime deps. (005-provider-snapshots)
- FastAPI/Uvicorn + WebSockets (backend, optional `web` extra), TypeScript/Vite/React + xterm.js (frontend), Bash (`remo-host` host command templated by Ansible) (010-web-session-interface)
- Stdlib `urllib.request` CLI setup client + token-gated `/api/v1/setup/*` FastAPI surface; service state in flat files under the writable `REMO_HOME` volume (`web-identity/` keypair + service known_hosts, `~/.config/remo/web-service.json` saved credentials, `cache_version: 2`) (011-web-adopt; payload versioning updated by 015-registry-v2)
- `core/registry.py`: stdlib `json` (format), `fcntl` (advisory locking via a `registry.lock` sidecar), `os.replace` (atomic writes). No new runtime deps. Setup API mirror payload moved to v2 (`contracts/mirror-payload-v2.md`) with a `payload_versions` capability handshake; an upgraded service still accepts v1 payloads. (015-registry-v2)
- `core/reconcile.py`: provider-agnostic sync-reconcile engine (`SyncScope`, `DiscoveredHost`, `ProbeResult`, `build_plan` (pure), `render_plan`, the consent gate, `apply_plan` via the existing `mutate_registry()`, and the `run_sync` driver). No new runtime deps — stdlib only, built on `core/registry.py`/`core/output.py` as-is. (016-sync-reconcile)
- `core/web_adopt.py` is now the single unified push engine (`run_push`; `run_adopt` is a thin deprecated alias) — first push adopts, later pushes re-sync, plus best-effort `remo-web@` revocation on removal, `--force` full re-authorization, and multi-workstation flap detection against the service's mirror-generation marker. `core/web_drift.py`: new stdlib-only offline registry-vs-push-cache diff (`diff_registry_against_cache`, `select_deployment`, `render_drift`) powering `remo web status` and the shared `out_of_date_notice()`/`emit_out_of_date_notice()` post-mutation nudge (importable without the `web` extra). Push cache bumped to `cache_version: 3` (`~/.config/remo/web-service.json`: per-deployment `{mirror_generation, instances}`, each instance retaining a non-secret connection tuple for revocation). Service side: `web/api/setup.py` writes/serves a `web-identity/mirror-meta.json` marker (generation + last-push descriptor) additively on `/setup/{status,registry}`; `web/state.py` mode detection fixed so a personal `~/.ssh/id_*` no longer forces `mount_configured` (non-writable `REMO_HOME` is the authoritative signal) with a new `REMO_WEB_MODE` override in `web/config.py`. No new runtime deps; no registry schema change. (017-web-adopt-simplify)
- No new runtime deps and no registry schema change. `ansible/ssh_configure.yml` is the first playbook to plumb an SSH **port** at all (`ansible_port` appeared nowhere in the repo before) and the first to take an identity from the registry rather than hardcoding `~/.ssh/id_rsa`; both arrive as namespaced `remo_ssh_*` extra-vars so no `ansible_*` name is ever emitted at extra-var precedence. `core/known_hosts.py` gains `guard_added_ssh_host_only`; `core/web_adopt.py` gains `known_hosts_lookup_key` and a `port=` parameter on `scan_and_verify_host_key`. Host key checking is inherited from `ansible.cfg` (disabled repo-wide) — a play-level override is impossible, since `ssh_common_args` is appended after `ssh_args` and ssh honours the first occurrence. (022-configure-added-hosts)
- No new runtime deps or registry schema change. `pyproject.toml`'s existing `hcloud`/`boto3`/`httpx2` entries gained consumer-attribution comments; `tests/unit/test_docs_structure.py` is a new stdlib-only pytest module (parses the structure diagram below, no new dependency). (019-hygiene-deps-docs)
- No new runtime deps; no registry schema change (the `host_user`/`node_user` JSON keys already matched the new flag spellings). Superseded for Proxmox by the `--node-user` → `--host-user` rename: both host-scoped providers now spell the node/host login `--host-user`/`host_user` on the CLI, in the provider kwargs and in `registry.json`; `core/registry.py` still reads a legacy `proxmox.node_user` key so pre-rename registries load unchanged and migrate on the next write. `core/provider_registry.py` gains `ArgumentSpec` (positional-argument metadata) and `CommandSpec.target`; `ProviderDescriptor.update_options` is replaced by `upgrade_options`/`resize_dimensions`/`resize_options`/`tag_options`/`host_commands`. `cli/providers/factory.py` gains `_build_upgrade`/`_build_resize`/`_build_tag`/`_build_host_group` (dropping `_build_update` and flat-mounted `bootstrap`). (021-cli-plane-separation)

- Ansible 2.14+ / YAML + `ansible.builtin`, `community.general` (for zypper module) (001-bootstrap-incus-host)
- Formal provider abstraction: `core/provider_registry.py` (`ProviderDescriptor`/`OptionSpec`/`CommandSpec`/`ConnectionSpec` + registry), `core/provider_protocol.py` (`Provider` Protocol), `core/errors.py` (typed taxonomy: `ProviderError`/`MissingDependencyError`/`PreconditionError`/`OperationFailedError`/`UserAbortedError`), `core/lifecycle.py` (shared `run_destroy` template), `cli/providers/factory.py` (generates all four provider CLI groups from descriptors). No new runtime deps. (018-provider-abstraction)
- The FastAPI service is the machine-checked source of truth for every shape `frontend/` consumes. `scripts/export_openapi.py` (stdlib, new) exports `frontend/src/api/generated/openapi.json` (`create_app().openapi()`) and `terminal-frames.json` (`TypeAdapter(...).json_schema()` over the six new `web/frames.py` control-frame models); `openapi-typescript` v7 (exact-pinned frontend devDependency) generates `schema.d.ts`/`terminal-frames.d.ts` (the latter via a small synthetic-OpenAPI wrapper, `frontend/scripts/generate-frame-types.mjs`, since openapi-typescript requires a genuine OpenAPI document). `web/api/hosts.py` gained `KnownProviderType(str, Enum)` (fixed to the built-in provider set, not the live registry — FR-004a) and enum-typed `InstanceOut`/`SessionTargetOut` fields; new `ErrorEnvelope`/`HealthResponse`/`ReadinessResponse`/`MintPairingResponse`/`DetailResponse` response models declare what each route already returns (no serialized byte moves). `web/api/terminals.py`'s five ad-hoc WS control-frame dict literals are gone, replaced by `web/frames.py` models (`_handle_control` preserves its exact silent-drop behavior for malformed/unknown inbound frames). Three drift checks (`tests/unit/test_schema_drift.py` for REST + frame freshness against the Python app; `frontend/scripts/check-types-fresh.mjs` for the generated `.d.ts` files) fail the build with an actionable message — never skip — when a checked-in artifact goes stale; `frontend/src/api/client.ts` and `frontend/src/components/providerMeta.ts` now import/derive from the generated types instead of hand-declaring parallel copies (a schema-derived `Record<InstanceStatus/KnownProviderType, …>` makes a new enum member a compile error while keeping a runtime fallback for off-union values, FR-013a). No registry schema change; no new service runtime dependency. (020-openapi-type-generation)

## Project Structure

```text
src/remo_cli/              # Python CLI package (src layout, hatchling build)
├── __init__.py            # Version from importlib.metadata
├── __main__.py            # python -m remo_cli entry point
├── cli/                   # Click command layer (parsing only, no business logic)
│   ├── main.py            # Root CLI group; mounts one group per remo_cli.core.provider_registry.all_descriptors()
│   ├── shell.py           # remo shell — registry-dispatched update_entry() (delegates to upgrade()); prompt names the exact `remo <type> upgrade <name>` it will run; unknown type/ssh handled explicitly (no silent no-op)
│   ├── cp.py              # remo cp
│   ├── added.py           # remo add / remo remove / remo configure — provider-neutral SSH host registration (014) and configure (022)
│   ├── web.py             # remo web {serve,check,push,status,adopt} — serve/check lazy-import remo_cli.web.* (NFR-008); push/status/adopt use core/web_adopt + core/web_drift only (adopt is a deprecated alias for push)
│   └── providers/
│       └── factory.py     # build_provider_group(descriptor) generates create/destroy/upgrade/resize/list/info/sync/snapshot for every provider, plus tag (iff supports_managed_marker) and a host subgroup (iff host_commands non-empty) — the four hand-written per-provider CLI modules are gone
├── providers/             # Business logic (no Click imports); Provider Protocol (update_entry/teardown/probe/snapshot_*) + heterogeneous create/destroy/upgrade/resize/tag/extra verbs, all raising core/errors.py taxonomy errors (never sys.exit); update_entry delegates to upgrade
│   ├── incus.py            # Incus provider implementation
│   ├── hetzner.py          # Hetzner Cloud provider implementation
│   ├── aws.py              # AWS provider implementation
│   ├── proxmox.py          # Proxmox provider implementation
│   ├── incus_descriptor.py    # metadata-only ProviderDescriptor declaration, no SDK imports
│   ├── hetzner_descriptor.py  # metadata-only ProviderDescriptor declaration, no SDK imports
│   ├── aws_descriptor.py      # metadata-only ProviderDescriptor declaration, no SDK imports
│   ├── proxmox_descriptor.py  # metadata-only ProviderDescriptor declaration, no SDK imports
│   ├── added.py            # Business logic for remo add / remo remove / remo configure — registration (014) + generic configure play (022)
│   └── builtin.py         # registers the four built-in descriptors; lazily auto-imported by provider_registry on first lookup
├── core/                  # Shared utilities (no provider knowledge)
│   ├── config.py          # REMO_HOME, paths, read-only registry accessor
│   ├── errors.py          # ProviderError taxonomy (contracts/errors.md); single CLI translation boundary is factory.py's provider_command wrapper
│   ├── provider_registry.py  # ProviderDescriptor/OptionSpec/CommandSpec/ConnectionSpec/ArgumentSpec + shared OptionSpec catalog + register/get_descriptor/get_provider/all_descriptors/is_provider_type/temporary_registration; CommandSpec.target (ArgumentSpec) drives host-subgroup positional args
│   ├── provider_protocol.py  # Provider Protocol (uniform entry-based surface: update_entry, teardown, probe, snapshot_create/restore/delete/list)
│   ├── lifecycle.py       # run_destroy(): guard → snapshot pre-cleanup → confirm → teardown → best-effort registry removal (the one destroy sequence; providers implement only teardown())
│   ├── output.py          # Colored output, confirm(), Column/render_host_table (shared list-table renderer)
│   ├── validation.py      # Name, port, region, tool validation
│   ├── registry.py        # Registry v2 accessor: parse/serialize/validate/lock/migrate (registry.json + legacy known_hosts); per-type nested-field map driven by descriptor.registry_fields (ssh pseudo-type stays local; defensive fallback + warning for unrecognized types)
│   ├── known_hosts.py     # Thin delegates onto registry.py (public API unchanged: get/save/remove/clear_known_hosts*); HOST_SCOPED short-name matching driven by descriptor.name_format
│   ├── ssh.py             # build_ssh_base_cmd(), SSH options, terminal reset, timezone; SSM ProxyCommand construction lives behind descriptor.connection.proxy_hook (AWS: providers/aws.py:ssh_proxy_hook), not hardcoded here
│   ├── reconcile.py       # SyncScope/DiscoveredHost/build_plan/run_sync; DiscoveredHost.observed (frozenset[str] | None) + observed-aware merge_entry (closes #87 — a provider-filled default never clobbers a hand-edited registry value); SyncScope validation/scoping driven by descriptor name_format + is_provider_type, no literal type tuples
│   ├── remo_host_client.py  # Versioned remo-host protocol client (shared by CLI + web)
│   ├── web_adopt.py       # Unified workstation push engine: run_push (adopt-or-resync), run_adopt alias, keyscan trust verify, authorized_keys authorize + best-effort revoke, verification-driven self-heal of `unchanged` instances that verify auth_failed (#122), --force, flap detection, push cache v3, --via tunnel (stdlib HTTP)
│   ├── web_drift.py       # Offline registry-vs-push-cache diff + shared out-of-date nudge (stdlib + core/models only; no web extra)
│   ├── ansible_runner.py  # Ansible playbook subprocess; build_configure_extra_vars() (timezone+tools+version, replaces 8 inline copies) and run_resize_playbook() (raises OperationFailedError on nonzero rc)
│   ├── snapshot.py        # Name generation/validation/table formatting; list_all_snapshots(type_name, lister) aggregates across a provider's registry slice (replaces 4 CLI-layer loops)
│   ├── completion.py      # Shell-completion layout/detect/install/staleness ($SHELL-based;
│   │                      #   drop-in file + one idempotent rc `source` line, never a `>>` dump)
│   ├── picker.py          # InquirerPy fuzzy picker
│   ├── rsync.py           # File transfer
│   └── version.py         # Version check, passive update notification
├── web/                    # remo-web service — FastAPI; optional `web` extra, lazily imported
│   ├── app.py               # FastAPI factory: routers, Host/Origin+CSP middleware, serves built SPA
│   ├── config.py             # WebSettings (REMO_WEB_* env vars incl. api_token, see docs/web-session-interface.md)
│   ├── state.py              # ConfigurationState detection (unconfigured/adopted/mount_configured/broken) + service identity generation
│   ├── discovery.py          # Concurrent per-instance discovery via remo-host + SSH
│   ├── ssh_master.py         # Per-instance SSH ControlMaster lifecycle
│   ├── terminal.py           # PTY + `ssh -tt … remo-host sessions attach`, resize/backpressure
│   ├── terminal_registry.py  # Terminal lifecycle, global/per-client caps (32/16 default)
│   ├── frames.py              # remo-terminal.v1 control-frame Pydantic models (resize/ping/ready/exit/error/pong) + InboundFrame/OutboundFrame discriminated unions
│   ├── tokens.py              # Single-use, 30s-TTL WS terminal tokens
│   ├── health.py              # GET /api/v1/health, /api/v1/ready
│   ├── check.py               # `remo web check` diagnostic
│   ├── logging_config.py      # Secret/token/proxy-command redaction in logs
│   ├── models.py               # Service-only entities: TerminalAttachment, WsToken, SshMaster
│   ├── operator_auth.py        # Pluggable operator-authentication seam gating pairing-code minting (forward-auth header today; OIDC deferred)
│   ├── pairing.py              # In-memory, single-live, TTL'd pairing-code session manager replacing the static setup API token
│   └── api/
│       ├── hosts.py            # GET /api/v1/hosts, /sessions, POST /discovery/refresh
│       ├── setup.py            # Token-gated /api/v1/setup/{status,identity,registry,verify} (011-web-adopt)
│       ├── terminals.py        # POST/GET/DELETE /api/v1/terminals, WS /api/v1/terminals/{id}
│       └── pairing.py          # POST /api/v1/pairing/{mint,end} — operator-auth-gated pairing-code control plane, outside the dormant setup router
└── models/
    ├── host.py             # KnownHost dataclass
    ├── snapshot.py         # Cross-provider snapshot model
    ├── capability.py       # RemoteCapability (remo-host capabilities)
    ├── session_target.py   # SessionTarget (opaque id, zellij/devcontainer state)
    └── discovery.py        # DiscoverySnapshot + typed InstanceStatus

frontend/                  # remo-web browser SPA (Vite + React + TypeScript)
├── src/
│   ├── api/client.ts        # REST + WS terminal client (remo-terminal.v1 subprotocol)
│   ├── components/          # Dashboard, InstanceGroup, TargetCard, GridView, TabView, TerminalCard
│   │                        #   + masterLayout.ts (pure pane geometry: uniform grid + master/stack tiling)
│   ├── state/                # discovery.ts, workspace.ts (layout persisted to localStorage)
│   └── terminal/              # RendererAdapter (seam), XtermRenderer (the one engine), TerminalConnection, keymap

docker/                    # remo-web container packaging (010-web-session-interface, US4)
├── Dockerfile               # multi-stage: frontend build -> wheel build -> slim Python runtime
├── entrypoint.sh             # `remo web check` gate, then `exec remo web serve`
└── compose.example.yml       # Home-lab Compose example (RO mounts, tmpfs, hardening flags)

ansible/                   # Ansible playbooks (invoked by Python via subprocess)
├── roles/
│   ├── incus_bootstrap/
│   ├── nested_docker/     # Hosts whose kernel refuses NESTED overlayfs mounts (OrbStack): a
│   │   │                  #   native-snapshotter buildx builder + a devcontainer shim (#160/#171).
│   │   │                  #   Gated on group_vars' docker_nested_overlayfs; no-op elsewhere
│   │   └── templates/
│   │       └── devcontainer-shim.sh.j2  # /usr/local/bin/devcontainer: picks DOCKER_BUILDKIT /
│   │                                    #   COMPOSE_BAKE / updateUID per project, per invocation
│   └── user_setup/
│       └── templates/
│           └── remo-host.sh.j2   # Versioned `remo-host` command (capabilities/sessions/attach)
├── incus_bootstrap.yml
├── ssh_configure.yml      # Generic configure play for `remo add` hosts (022): maps remo_ssh_*
│                          #   -> ansible_port/ansible_ssh_private_key_file, binds remo_user to the
│                          #   registered account, reuses tasks/configure_dev_tools.yml
└── requirements.yml

scripts/                   # Repo-root utility scripts (not part of the installed package)
├── export_openapi.py        # Exports frontend/src/api/generated/{openapi.json,terminal-frames.json} (feature 020)
└── palette-check.sh         # Prints every ANSI colour x normal/bold/dim/bright in a live terminal — audits a theme's legibility where contrast maths can't

pyproject.toml             # Build config, dependencies (incl. `web` extra), console_scripts entry point
```

## Ansible Standards (Constitution Principle V)

### Variable Access - CRITICAL

**NEVER** access registered variable attributes directly. **ALWAYS** use `| default()` filters:

```yaml
# WRONG - will fail if task was skipped
when: my_result.rc == 0
msg: "{{ my_result.stdout }}"

# CORRECT - safe for skipped tasks
when: my_result.rc | default(1) == 0
msg: "{{ my_result.stdout | default('N/A') }}"
```

### Ansible Pre-Commit Checklist

Before committing Ansible code (the repo-wide checklist is under [Quality Gates](#quality-gates)):

1. Grep for unsafe patterns: `grep -r '\.rc ==' ansible/` and `grep -r '\.stdout' ansible/`
2. Verify all matches use `| default()`
3. Test playbook on fresh system AND system with existing state
4. Update README if behavior changed

### Safe Task Registration Pattern

```yaml
- name: Check something
  ansible.builtin.command: some_command
  register: check_result
  changed_when: false
  failed_when: false
  when: some_condition

- name: Use the result safely
  ansible.builtin.debug:
    msg: "Result: {{ check_result.stdout | default('skipped') }}"
  when: check_result.stdout is defined
```

## Commands

```bash
# Development setup
uv sync --all-extras              # Install with all optional deps + dev tools
uv sync --extra web               # Install with web service (FastAPI/Uvicorn) only

# Verify installation
uv run remo --version
uv run remo --help

# Run tests
uv run pytest

# Type checking and linting
uv run mypy src/remo_cli
uv run ruff check src/remo_cli

# Regenerate the console's generated API/frame types (feature 020) after a service
# model or control-frame change; see docs/maintaining-generated-types.md
uv run python scripts/export_openapi.py     # openapi.json + terminal-frames.json
cd frontend && npm run generate:types        # schema.d.ts + terminal-frames.d.ts
cd frontend && npm run check:types-fresh     # drift check B/C-node (no write)
uv run pytest tests/unit/test_schema_drift.py  # drift check A/C-python (no write)

# Provider-neutral SSH registration
uv run remo add NAME [user@]host[:port]   # remo add — register any SSH-reachable environment
uv run remo remove NAME                   # remo remove — deregister (local registry only)

# Shell completion
uv run remo completion bash               # remo completion {bash,zsh,fish} — print activation script

# Web service (requires the `web` extra)
uv run remo web check             # Validate registry/SSH/runtime-dir/reachability
uv run remo web serve             # Run the browser terminal broker locally

# Frontend (requires Node; see frontend/package.json)
cd frontend && npm ci
npm run build                     # tsc -b && vite build -> frontend/dist
npm run lint                      # tsc --noEmit
npm run test                      # Vitest unit/component suite (jsdom, no backend)
npm run test:e2e                  # Playwright (needs REMO_E2E_BASE_URL -> live remo web serve)
```

## Architecture

### System layers

| Layer | Path | Depends on |
|-------|------|------------|
| Browser console (React/TS SPA) | `frontend/` | The web service's **generated** types only |
| Web service (FastAPI, optional `web` extra) | `src/remo_cli/web/` | `core/`, `models/`, `providers/` (catches `ProviderError` directly) |
| CLI package (Python) | `src/remo_cli/{cli,providers,core,models}/` | Ansible via subprocess |
| Configuration (Ansible roles) | `ansible/` | — |

The service is an optional extra: `remo --help` and every non-web command work
without it installed, and web imports are lazy (NFR-008).

### Python package (three layers, one-way)

- **cli/** → Click commands, argument parsing only. No business logic.
- **providers/** → Business logic. No Click imports. No `sys.exit`. Called by cli layer.
- **core/** → Shared utilities. No provider knowledge. Used by both layers.

`tests/unit/test_architecture.py` enforces this with zero-tolerance (empty)
allowlists: no `sys.exit` in `providers/`, and no `cli/` reach-ins to a
`providers/` module's private helpers.

Provider-varying behavior lives behind a `ProviderDescriptor` field or hook —
never a `host.type` string literal in `core/`. AWS's SSM `ProxyCommand` is the
worked example: it sits in `providers/aws.py:ssh_proxy_hook`, reached via
`descriptor.connection.proxy_hook`.

Failures raise the `core/errors.py` taxonomy (`MissingDependencyError`,
`PreconditionError`, `OperationFailedError`, `UserAbortedError`).
`cli/providers/factory.py`'s `provider_command` wrapper is the *only*
exception-to-exit-code boundary: `0` success, `1` failure, `3` user-aborted.

Provider implementation modules are lazily imported by `core/provider_registry.get_provider()`; an `ImportError` during that import becomes a `MissingDependencyError` naming `descriptor.sdk_extra` (e.g. "aws", "hetzner") and the `uv sync --extra <name>` install command. In practice `boto3` and `hcloud` are both unconditional dependencies today, so this `ImportError` branch is currently unreachable for the built-in providers — the message is aspirational pending issue #94, which would introduce real optional extras; `descriptor.sdk_extra` itself is unchanged and the mechanism is exercised by third-party providers that do have an optional SDK.

## Quality Gates

These run in CI and must pass before merge. None may be skipped, `xfail`ed, or
made conditional to get a build green — fix the code or amend the gate by PR.

| Gate | Command | Enforces |
|------|---------|----------|
| Tests (3.11/3.12/3.13) | `uv run pytest` | Principles III, VI, VII |
| Architecture | `uv run pytest tests/unit/test_architecture.py` | Principle I |
| Docs structure | `uv run pytest tests/unit/test_docs_structure.py` | Principle VIII |
| Schema drift (Python) | `uv run pytest tests/unit/test_schema_drift.py` | Principle IV |
| Schema drift (Node) | `cd frontend && npm run check:types-fresh` | Principle IV |
| Lint | `uv run ruff check src/remo_cli` | Code Style |
| Types | `uv run mypy src/remo_cli` | Code Style |
| Frontend | `cd frontend && npm run lint && npm run test && npm run build` | Code Style |
| Fish completion | `./tests/integration/fish_completion.sh` | Principle VI (completion runs, not just reads) |
| Packaging | wheel install smoke, Docker amd64+arm64 | Distribution integrity |
| Security | CodeQL, dependency review | Supply chain |

`ruff` and `mypy` share the one `Lint & Types` job. That job must keep
installing the `web` extra (`uv sync --all-extras`): with
`ignore_missing_imports = true`, an uninstalled FastAPI/pydantic would degrade
every `src/remo_cli/web/` module to `Any` and leave the type gate passing while
checking nothing.

### Repo-wide pre-commit checklist

1. **Layer boundaries** — no Click in `providers/`, no provider names in `core/`, no `sys.exit` in `providers/`.
2. **Variable safety (Ansible)** — grep for `.rc ==` and `.stdout` without `| default`.
3. **Conditional coverage** — every branch touched has both sides exercised.
4. **Regeneration** — a service model or WS frame change regenerates and commits all four artifacts.
5. **Documentation sync** — `README.md`, `docs/*.md`, and the structure diagrams above match the change.
6. **Idempotency** — the mutating path runs twice; the second run is a no-op.

## Code Style

- Python: Type hints, `from __future__ import annotations`, no docstrings on obvious methods.
  Docstrings explain *why*, and cite the spec/contract they implement when one exists.
- Web service: every route declares a response model; a route returning a `Response`
  subclass must still construct that model (FastAPI skips `response_model` otherwise).
  Closed domains are enums; open wire fields are `KnownEnum | str`. Enums exported into
  the OpenAPI artifact are fixed at their declared set, never derived from a live registry.
- Frontend: service-shaped types come from `src/api/generated/`; console-owned shapes may be
  hand-written and are commented as such. Presentation maps key off a generated union via
  `Record<GeneratedUnion, …>` so a new member is a compile error — while keeping the runtime
  fallback for off-union values (deleting that fallback is a defect, not a cleanup).
- Generated artifacts (`openapi.json`, `terminal-frames.json`, `schema.d.ts`,
  `terminal-frames.d.ts`) are checked in but never hand-edited. See
  `docs/maintaining-generated-types.md`.
- Ansible 2.14+ / YAML: Follow standard conventions plus Constitution principles

## Recent Changes
- issues #160/#171 — nested overlayfs (OrbStack): Made hosts whose kernel refuses NESTED overlayfs mounts able to build devcontainers at all. An OrbStack "machine" is a container on a shared kernel (rootfs is a btrfs subvolume under `/scon/containers/<id>`), not a VM, so Docker inside it is nested and an overlay mount is refused — and Docker 29 made the containerd overlayfs snapshotter the default, so a freshly-configured host lands on the broken path with nothing to steer it. `remo configure` completed cleanly, `remo-host` responded, `remo web push` reported capabilities OK, and then every BuildKit build died on the first `RUN` with `mount source: "overlay" … operation not permitted`, surfaced as an opaque devcontainer-CLI stack trace that read like a project problem. New `ansible/roles/nested_docker/`, included from `tasks/configure_dev_tools.yml` (so all five configure playbooks inherit it) behind `group_vars/all.yml`'s `docker_nested_overlayfs`, which keys off `-orbstack-` in `ansible_kernel` — NOT `/mnt/mac`, which is absent under `orb create --isolated` and would have silently skipped the fix on exactly the configuration a user picks for a cleaner VM. It runs after `user_setup`, and every buildx task carries `become_user: remo_user`: buildx state lives in `~/.docker/buildx`, so a builder created for root leaves `project-launch` just as broken as no builder. **Two halves, because one fix is not enough.** (1) #160: a `docker-container` builder (`remo-native`) whose buildkitd.toml selects the `native` snapshotter, which copies layers instead of overlaying them — correct, and materially slower; `--bootstrap` so a broken image pull fails the configure rather than surfacing days later, and the builder is *recreated* when buildkitd.toml changes because `buildx create` reads `--config` once and an edited file otherwise never reaches an existing builder. (2) #171: the devcontainer CLI shells out to two builders whose requirements here are mutually exclusive — `docker compose build` HONOURS `DOCKER_BUILDKIT=0` and drops to the classic builder, which rejects the `additional_contexts` the CLI injects to install Features, while `updateUID`'s plain `docker build` needs exactly that setting to avoid the daemon's embedded BuildKit. No global value is correct for both, so a shim decides per invocation from the project's own `devcontainer.json`: Compose → `DOCKER_BUILDKIT=1 COMPOSE_BAKE=1` (verified: `COMPOSE_BAKE` alone still fails) plus `--update-remote-user-uid-default never` on `up`/`build` only — the CLI rejects the flag on `exec`, which `project-launch` uses; anything else → `DOCKER_BUILDKIT=0`, argv untouched; both → `BUILDX_BUILDER=remo-native`, so a user changing their default builder cannot silently break every devcontainer. Three placement decisions the tests pin, each defending a *silent* failure: the shim installs to `/usr/local/bin/devcontainer`, NOT `~/.local/bin` (that directory is added to PATH inside `~/.bashrc`, which returns early for non-interactive shells — a shim there would miss `ssh host 'devcontainer up …'`, the exact case it exists to fix, while passing every interactive test); it calls the real CLI by absolute path, because `command devcontainer` resolves through PATH, finds the shim again and recurses; and it declines to skip `updateUID` unless the host UID matches `user_setup`'s 1000 pin, since skipping at any other UID would trade a broken build for silently unwritable bind mounts. `docker buildx install` was rejected as the "clean" general fix — the docker-container driver cannot resolve the local-only image `updateUID`'s `FROM $BASE_IMAGE` names. remo NAMES a hand-added global `DOCKER_BUILDKIT` but does not delete it (the operator's line, in a file remo has no managed block in — the #121 authorized_keys lesson), and reports both costs it cannot fix via the task NAME, since `_filter_line` suppresses `debug` bodies: slower builds, and `apt` inside a running container failing with `Invalid cross-device link` (which is why `playwright install --with-deps`, and so E2E browser tests, cannot work on such a host — easy to miss, because a well-written `postCreateCommand` treats it as non-fatal and `devcontainer up` still reports success). New `tests/unit/test_devcontainer_shim.py` renders the template and *executes* it against a stub CLI for every branch. New `docs/nested-overlayfs.md`. No registry schema change; no new runtime deps; no new CLI flag or tool toggle — it is a repair for the tools already selected, not another tool.
- 022-configure-added-hosts: Made manually-added SSH hosts first-class by giving them the one thing `remo add` (014) never did — a configure path. New provider-neutral `remo configure NAME` runs a new generic `ansible/ssh_configure.yml` against a registry `type="ssh"` entry, so an added host finally gets `remo-host`/zellij/`project-launch` and stops sitting in the `remo web` rail permanently badged `no_remo_host` with zero session targets. **No refactor of the four provider plays**: the role list was already shared via `tasks/configure_dev_tools.yml`, so a fifth playbook inherits the whole toolchain — the change is purely additive. The new play is two plays: a `localhost` play that asserts (never defaults) `remo_ssh_host`/`remo_ssh_user`/`remo_ssh_port` and `add_host`s them onto `ansible_host`/`ansible_user`/`ansible_port`/`ansible_ssh_private_key_file`, then a play targeting that named group (NOT `hosts: all`, which would pick up `inventory/hosts.yml`'s `hetzner_server` and whose zero-host case exits rc 0 — a silent false success). Four decisions the tests pin, each defending a *silent* failure: the identity is `| default(omit)`, never `'~/.ssh/id_rsa'` (aws_configure.yml's anti-pattern — a path default offers the WRONG key); it is paired with an explicit `-o IdentitiesOnly=yes`, which ansible-core does not add for `private_key_file` (without it a busy agent hits MaxAuthTries and dies "Too many authentication failures" while the passed key is never tried); both `raw` pre-flight probes (python3, `sudo -n true`) set `become: false`, since `ansible.cfg` sets `become = True` globally and a sudo probe run THROUGH sudo proves nothing; and `remo_user` is bound to `remo_ssh_user` rather than `group_vars/all.yml`'s `"remo"` — everything `user_setup` installs lands in `/home/{{ remo_user }}/.local/bin` and discovery logs in as the REGISTERED user, so the default would install `remo-host` into a second account and leave the console still reporting `no_remo_host` after a "successful" configure (it also skips the UID-1000 displacement, which only fires when 1000's holder is someone else). Root is refused (UID-1000 pin), the prompt defaults to no, and remo never reboots a host it did not provision — it reports via the task NAME, since `_filter_line` suppresses `debug` bodies. `core/known_hosts.py` gains `guard_added_ssh_host_only`, the mirror of `guard_not_added_ssh_host`: a provider host is refused naming `remo <type> upgrade NAME`, because the five configure plays are not interchangeable and a mis-target would succeed while configuring wrongly. **Bug fix in the same change** (`core/web_adopt.py`): `remo web push` hardcoded port 22 in `scan_and_verify_host_key`'s `ssh-keyscan` and `_lookup_trusted_keys`' `ssh-keygen -F`, so an added host on another port was reported `skipped/unreachable` — `authorize_service_key` never ran — with a remediation blaming a host that was reachable, or, worse, a different machine answering on 22 had its keys pushed. New `known_hosts_lookup_key()` follows OpenSSH's record form (bare for 22, `[host]:port` otherwise; verified against the real binary — neither direction is forgiving), and `-p` is omitted for 22 so every provider host's argv is byte-identical. `providers/added.py`'s `_reject_unsafe_field` now also rejects Jinja delimiters (registry values become extra-vars, which Ansible templates on the CONTROL NODE), re-checked on the configure read path so pre-existing entries are covered. `web/discovery.py`'s `no_remo_host` remediation now names the real command per host type instead of "re-run configure", which for an added host pointed at nothing. No registry schema change; no new runtime deps.
- 021-cli-plane-separation: Split the three-intent `remo <provider> update` verb into three single-intent verbs — `upgrade` (in-instance configure play only, zero provider-side writes, SC-001), `resize` (resource change only — the factory rejects a dimensionless invocation with a `PreconditionError` listing that provider's dimension flags, before dispatching to the provider), and `tag` (managed-marker write only, generated iff `supports_managed_marker`; read-before-write no-op when already tagged, `OperationFailedError` — not `create`'s warn-and-continue — on write failure, SC-002) — and moved `bootstrap` off the flat instance plane into a new descriptor-driven `host` subgroup (`remo {incus,proxmox} host bootstrap`, generated iff `host_commands` is non-empty; `CommandSpec` gained a `target: ArgumentSpec | None` field so host commands take the host positionally — incus optional/defaults to localhost, proxmox required). `core/provider_registry.py`: `ProviderDescriptor.update_options` removed, replaced by `upgrade_options`/`resize_dimensions`/`resize_options`/`tag_options`/`host_commands`; new `ArgumentSpec` dataclass. `cli/providers/factory.py`: `_build_update` replaced by `_build_upgrade`/`_build_resize`/`_build_tag`/`_build_host_group`; `_instance_argument` grew a `param` kwarg so the new verbs take `NAME` positionally (matching the provider impl's `name` kwarg) while `create`/`destroy`/`info` keep `--name`; `_resolve_entry_for_destroy`'s user-hint lookup is now descriptor-driven (reads `kwargs` under the `registry_fields` JSON key ending in `_user`) instead of a `"user"` literal. Every provider module split `update()` into `upgrade()`/`resize()`/`tag()` (marker-supporting providers only) reusing existing private helpers unchanged; `update_entry()` (the `remo shell` tools-update path) now delegates to `upgrade()`. Renamed `--user` → `--host-user` (incus) / `--node-user` (proxmox) everywhere it appears (`remo add --user`, a different flag, is unchanged). Made every printed remedy truthful (SC-003): the registry-migration tagging notice and `sync`'s "Mark permanently:" line now recommend `tag` (previously wrongly recommended `sync`/`update`, which never wrote the marker); `remo shell`'s version-mismatch prompt now names the exact `remo <type> upgrade <name>` command it will run. Breaking change, no shim, no registry schema change: `remo <type> update`, flat `bootstrap`, and the old `--user` spelling are all Click "unknown command"/"no such option" errors post-change. FakeProvider's descriptor gained `upgrade_options`/`resize_dimensions`/`tag_options`/one `host_commands` entry, proving the full new surface is descriptor-only (SC-005).
Only the newest three entries live here — `update-agent-context.sh` discards the rest on every run. The complete history is archived in [`docs/feature-history.md`](docs/feature-history.md); move displaced entries there rather than letting the generator drop them.


<!-- MANUAL ADDITIONS START -->

<!-- SPECKIT START -->
For additional context about technologies to be used, project structure,
shell commands, and other important information, read the current plan
<!-- SPECKIT END -->
