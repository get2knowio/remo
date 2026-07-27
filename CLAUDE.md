# remo Development Guidelines

Auto-generated from all feature plans. Last updated: 2026-07-26

## Constitution

See `.specify/memory/constitution.md` for project principles and non-negotiable standards.

## Active Technologies
- Ansible 2.14+ / YAML + `ansible.builtin`, `community.general` (existing), Incus CLI (local) (002-incus-container-support)
- N/A (Incus storage pools already configured by 001-bootstrap-incus-host) (002-incus-container-support)
- Python 3.11+ + Click (CLI framework), InquirerPy (interactive picker), boto3 (unconditional runtime dependency, used by the CLI's own lazy `import boto3` in `providers/aws.py` and by the Ansible `amazon.aws`/`community.aws` collections), hcloud (unconditional runtime dependency, consumed by the Ansible layer, not the CLI's own Python code) (003-python-cli-rewrite)
- Versioned JSON registry (`~/.config/remo/registry.json`, format v2 — named fields per type, no positional overloading; single accessor `core/registry.py` owns parse/serialize/validate/lock/migrate for CLI, providers, and the web service). Legacy `~/.config/remo/known_hosts` (colon-delimited) is read-only migration input, lazily migrated to v2 on first CLI read and renamed to `known_hosts.v1.bak`. (003-python-cli-rewrite; superseded by 015-registry-v2)
- Cross-provider snapshot model (`models/snapshot.py`) + shared helpers in `core/snapshot.py` (name generator, validator, table formatter, destroy-time cleanup hook). No new runtime deps. (005-provider-snapshots)
- FastAPI/Uvicorn + WebSockets (backend, optional `web` extra), TypeScript/Vite/React + ghostty-web (frontend), Bash (`remo-host` host command templated by Ansible) (010-web-session-interface)
- Stdlib `urllib.request` CLI setup client + token-gated `/api/v1/setup/*` FastAPI surface; service state in flat files under the writable `REMO_HOME` volume (`web-identity/` keypair + service known_hosts, `~/.config/remo/web-service.json` saved credentials, `cache_version: 2`) (011-web-adopt; payload versioning updated by 015-registry-v2)
- `core/registry.py`: stdlib `json` (format), `fcntl` (advisory locking via a `registry.lock` sidecar), `os.replace` (atomic writes). No new runtime deps. Setup API mirror payload moved to v2 (`contracts/mirror-payload-v2.md`) with a `payload_versions` capability handshake; an upgraded service still accepts v1 payloads. (015-registry-v2)
- `core/reconcile.py`: provider-agnostic sync-reconcile engine (`SyncScope`, `DiscoveredHost`, `ProbeResult`, `build_plan` (pure), `render_plan`, the consent gate, `apply_plan` via the existing `mutate_registry()`, and the `run_sync` driver). No new runtime deps — stdlib only, built on `core/registry.py`/`core/output.py` as-is. (016-sync-reconcile)
- `core/web_adopt.py` is now the single unified push engine (`run_push`; `run_adopt` is a thin deprecated alias) — first push adopts, later pushes re-sync, plus best-effort `remo-web@` revocation on removal, `--force` full re-authorization, and multi-workstation flap detection against the service's mirror-generation marker. `core/web_drift.py`: new stdlib-only offline registry-vs-push-cache diff (`diff_registry_against_cache`, `select_deployment`, `render_drift`) powering `remo web status` and the shared `out_of_date_notice()`/`emit_out_of_date_notice()` post-mutation nudge (importable without the `web` extra). Push cache bumped to `cache_version: 3` (`~/.config/remo/web-service.json`: per-deployment `{mirror_generation, instances}`, each instance retaining a non-secret connection tuple for revocation). Service side: `web/api/setup.py` writes/serves a `web-identity/mirror-meta.json` marker (generation + last-push descriptor) additively on `/setup/{status,registry}`; `web/state.py` mode detection fixed so a personal `~/.ssh/id_*` no longer forces `mount_configured` (non-writable `REMO_HOME` is the authoritative signal) with a new `REMO_WEB_MODE` override in `web/config.py`. No new runtime deps; no registry schema change. (017-web-adopt-simplify)
- No new runtime deps or registry schema change. `pyproject.toml`'s existing `hcloud`/`boto3`/`httpx2` entries gained consumer-attribution comments; `tests/unit/test_docs_structure.py` is a new stdlib-only pytest module (parses the structure diagram below, no new dependency). (019-hygiene-deps-docs)

- Ansible 2.14+ / YAML + `ansible.builtin`, `community.general` (for zypper module) (001-bootstrap-incus-host)
- Formal provider abstraction: `core/provider_registry.py` (`ProviderDescriptor`/`OptionSpec`/`CommandSpec`/`ConnectionSpec` + registry), `core/provider_protocol.py` (`Provider` Protocol), `core/errors.py` (typed taxonomy: `ProviderError`/`MissingDependencyError`/`PreconditionError`/`OperationFailedError`/`UserAbortedError`), `core/lifecycle.py` (shared `run_destroy` template), `cli/providers/factory.py` (generates all four provider CLI groups from descriptors). No new runtime deps. (018-provider-abstraction)

## Project Structure

```text
src/remo_cli/              # Python CLI package (src layout, hatchling build)
├── __init__.py            # Version from importlib.metadata
├── __main__.py            # python -m remo_cli entry point
├── cli/                   # Click command layer (parsing only, no business logic)
│   ├── main.py            # Root CLI group; mounts one group per remo_cli.core.provider_registry.all_descriptors()
│   ├── shell.py           # remo shell — registry-dispatched update_entry(); unknown type/ssh handled explicitly (no silent no-op)
│   ├── cp.py              # remo cp
│   ├── added.py           # remo add / remo remove — provider-neutral SSH host registration (feature 014)
│   ├── web.py             # remo web {serve,check,push,status,adopt} — serve/check lazy-import remo_cli.web.* (NFR-008); push/status/adopt use core/web_adopt + core/web_drift only (adopt is a deprecated alias for push)
│   └── providers/
│       └── factory.py     # build_provider_group(descriptor) generates create/destroy/update/list/info/sync/snapshot/extra_commands for every provider from its descriptor — the four hand-written per-provider CLI modules are gone
├── providers/             # Business logic (no Click imports); Provider Protocol (update_entry/teardown/probe/snapshot_*) + heterogeneous create/destroy/update/extra verbs, all raising core/errors.py taxonomy errors (never sys.exit)
│   ├── incus.py            # Incus provider implementation
│   ├── hetzner.py          # Hetzner Cloud provider implementation
│   ├── aws.py              # AWS provider implementation
│   ├── proxmox.py          # Proxmox provider implementation
│   ├── incus_descriptor.py    # metadata-only ProviderDescriptor declaration, no SDK imports
│   ├── hetzner_descriptor.py  # metadata-only ProviderDescriptor declaration, no SDK imports
│   ├── aws_descriptor.py      # metadata-only ProviderDescriptor declaration, no SDK imports
│   ├── proxmox_descriptor.py  # metadata-only ProviderDescriptor declaration, no SDK imports
│   ├── added.py            # Business logic for remo add / remo remove — provider-neutral SSH host registration (feature 014)
│   └── builtin.py         # registers the four built-in descriptors; lazily auto-imported by provider_registry on first lookup
├── core/                  # Shared utilities (no provider knowledge)
│   ├── config.py          # REMO_HOME, paths, read-only registry accessor
│   ├── errors.py          # ProviderError taxonomy (contracts/errors.md); single CLI translation boundary is factory.py's provider_command wrapper
│   ├── provider_registry.py  # ProviderDescriptor/OptionSpec/CommandSpec/ConnectionSpec + shared OptionSpec catalog + register/get_descriptor/get_provider/all_descriptors/is_provider_type/temporary_registration
│   ├── provider_protocol.py  # Provider Protocol (uniform entry-based surface: update_entry, teardown, probe, snapshot_create/restore/delete/list)
│   ├── lifecycle.py       # run_destroy(): guard → snapshot pre-cleanup → confirm → teardown → best-effort registry removal (the one destroy sequence; providers implement only teardown())
│   ├── output.py          # Colored output, confirm(), Column/render_host_table (shared list-table renderer)
│   ├── validation.py      # Name, port, region, tool validation
│   ├── registry.py        # Registry v2 accessor: parse/serialize/validate/lock/migrate (registry.json + legacy known_hosts); per-type nested-field map driven by descriptor.registry_fields (ssh pseudo-type stays local; defensive fallback + warning for unrecognized types)
│   ├── known_hosts.py     # Thin delegates onto registry.py (public API unchanged: get/save/remove/clear_known_hosts*); HOST_SCOPED short-name matching driven by descriptor.name_format
│   ├── ssh.py             # build_ssh_base_cmd(), SSH options, terminal reset, timezone; SSM ProxyCommand construction lives behind descriptor.connection.proxy_hook (AWS: providers/aws.py:ssh_proxy_hook), not hardcoded here
│   ├── reconcile.py       # SyncScope/DiscoveredHost/build_plan/run_sync; DiscoveredHost.observed (frozenset[str] | None) + observed-aware merge_entry (closes #87 — a provider-filled default never clobbers a hand-edited registry value); SyncScope validation/scoping driven by descriptor name_format + is_provider_type, no literal type tuples
│   ├── remo_host_client.py  # Versioned remo-host protocol client (shared by CLI + web)
│   ├── web_adopt.py       # Unified workstation push engine: run_push (adopt-or-resync), run_adopt alias, keyscan trust verify, authorized_keys authorize + best-effort revoke, --force, flap detection, push cache v3, --via tunnel (stdlib HTTP)
│   ├── web_drift.py       # Offline registry-vs-push-cache diff + shared out-of-date nudge (stdlib + core/models only; no web extra)
│   ├── ansible_runner.py  # Ansible playbook subprocess; build_configure_extra_vars() (timezone+tools+version, replaces 8 inline copies) and run_resize_playbook() (raises OperationFailedError on nonzero rc)
│   ├── snapshot.py        # Name generation/validation/table formatting; list_all_snapshots(type_name, lister) aggregates across a provider's registry slice (replaces 4 CLI-layer loops)
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
│   ├── state/                # discovery.ts, workspace.ts (layout persisted to localStorage)
│   └── terminal/              # RendererAdapter, GhosttyRenderer (default), XtermRenderer (fallback)
└── public/                    # Same-origin-served ghostty-web WASM asset

docker/                    # remo-web container packaging (010-web-session-interface, US4)
├── Dockerfile               # multi-stage: frontend build -> wheel build -> slim Python runtime
├── entrypoint.sh             # `remo web check` gate, then `exec remo web serve`
└── compose.example.yml       # Home-lab Compose example (RO mounts, tmpfs, hardening flags)

ansible/                   # Ansible playbooks (invoked by Python via subprocess)
├── roles/
│   ├── incus_bootstrap/
│   └── user_setup/
│       └── templates/
│           └── remo-host.sh.j2   # Versioned `remo-host` command (capabilities/sessions/attach)
├── incus_bootstrap.yml
└── requirements.yml

pyproject.toml             # Build config, dependencies (incl. `web` extra), console_scripts entry point
```

## Ansible Standards (from Constitution)

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

### Pre-Commit Checklist

Before committing Ansible code:

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

## Architecture (Three-Layer)

- **cli/** → Click commands, argument parsing only. No business logic.
- **providers/** → Business logic. No Click imports. Called by cli layer.
- **core/** → Shared utilities. No provider knowledge. Used by both layers.

Provider implementation modules are lazily imported by `core/provider_registry.get_provider()`; an `ImportError` during that import becomes a `MissingDependencyError` naming `descriptor.sdk_extra` (e.g. "aws", "hetzner") and the `uv sync --extra <name>` install command. In practice `boto3` and `hcloud` are both unconditional dependencies today, so this `ImportError` branch is currently unreachable for the built-in providers — the message is aspirational pending issue #94, which would introduce real optional extras; `descriptor.sdk_extra` itself is unchanged and the mechanism is exercised by third-party providers that do have an optional SDK.

## Code Style

- Python: Type hints, `from __future__ import annotations`, no docstrings on obvious methods
- Ansible 2.14+ / YAML: Follow standard conventions plus Constitution principles

## Recent Changes
- 019-hygiene-deps-docs: Repository hygiene pass across dependencies, dead code, and documentation — no behavior change except one deliberate CLI break. Annotated every dependency whose necessity isn't visible from `src/remo_cli/` imports alone (`hcloud`/`boto3` in `pyproject.toml` now name their real consumers — the Ansible `hetzner.hcloud`/`amazon.aws`/`community.aws` collections — and stay unconditional pending issue #94; the `httpx2` dev-extra comment clarifies it's a real pydantic package, not a typo) and annotated two currently-unreachable missing-boto3 code paths in `providers/aws.py` rather than deleting them. Removed the phantom `remo init` command from every doc surface (README, docs/aws.md, install.sh, CONTRIBUTING.md) — Ansible collections install automatically on first provider command via `core/ansible_runner.py::_ensure_collections`. Corrected this file's structure diagram, Commands, and Active Technologies sections to match the real tree (removed two phantom entries, documented 13 previously-undocumented modules, dropped two nonexistent `--extra` install commands) and rewrote `AGENTS.md`, which had described an unrelated project. Added `tests/unit/test_docs_structure.py`, a new CI-gating pytest module (stdlib-only, no new dependency) that parses both files' structure diagrams and fails the build on drift, naming the offending paths; `docs/maintaining-claude-md.md` documents the fix procedure. Deleted the dead `providers/proxmox.py::_parse_pct_json` (zero call sites) and its orphaned `import json`. Consolidated all four of `providers/hetzner.py`'s hand-rolled `urllib.request.Request` call sites onto the module's existing `_hetzner_api()` helper, preserving each site's distinct raise-vs-swallow/timeout/message-text contract exactly (`tests/unit/providers/test_hetzner_http.py` characterizes and pins the behavior). **Breaking change**: removed the `--yes`/`-y` flag from `remo <provider> create` on all four providers — it never had any effect (creation has no confirmation prompt); `--yes` is unchanged on `destroy`, `sync`, `snapshot restore`, `snapshot delete`, and `remo remove`.
- 018-provider-abstraction: Replaced the convention-by-copy provider layer with a formal abstraction. A `Provider` Protocol (`core/provider_protocol.py`: `update_entry`/`teardown`/`probe`/`snapshot_create`/`snapshot_restore`/`snapshot_delete`/`snapshot_list`, entry-based) plus a generic `ProviderDescriptor` + registry (`core/provider_registry.py`: `OptionSpec`/`CommandSpec`/`ConnectionSpec`, a canonical shared `OptionSpec` catalog, `register`/`get_descriptor`/`get_provider`/`all_descriptors`/`is_provider_type`/`temporary_registration`) replace scattered `host.type` string-matching. A CLI factory (`cli/providers/factory.py`) generates all four provider command groups from their descriptors (`providers/{incus,hetzner,aws,proxmox}_descriptor.py`, registered in `providers/builtin.py`) — the four hand-written `cli/providers/*.py` modules (~1,375 lines) and `core/completion.py` are deleted; a `FakeProvider` conformance test proves a fifth provider needs only one implementation module + one descriptor registration, touching no existing files (SC-001). A typed error taxonomy (`core/errors.py`: `ProviderError`/`MissingDependencyError`/`PreconditionError`/`OperationFailedError`/`UserAbortedError`) replaces all 15 business-layer `sys.exit` calls and every bare `RuntimeError`; the CLI factory's `provider_command` wrapper is the single exit-code translation boundary (0/1/3; a nonzero ansible-playbook rc now normalizes to exit 1 instead of propagating the raw rc — documented behavior change). Five duplicated skeletons collapsed into shared templates: `core/lifecycle.run_destroy` (guard → snapshot pre-cleanup → confirm → provider `teardown()` → best-effort registry removal — providers implement only `teardown`), `core/snapshot.list_all_snapshots` (all-instances aggregation), `core/ansible_runner.build_configure_extra_vars`/`run_resize_playbook`, and `core/output.render_host_table`. Provider-specific knowledge migrated out of `core/`: the AWS SSM `ProxyCommand` moves behind `descriptor.connection.proxy_hook` (`providers/aws.py:ssh_proxy_hook`) instead of being hardcoded in `core/ssh.py`; `core/reconcile.SyncScope` validation/scoping and `core/known_hosts.py`'s HOST_SCOPED short-name matching are now driven by `descriptor.name_format`/`is_provider_type` instead of literal type tuples; `core/registry.py`'s per-type nested-field serialization is driven by `descriptor.registry_fields` (the `ssh` pseudo-type stays local; unrecognized types get a defensive serialize-and-warn fallback instead of crashing). Closes issue #87: `DiscoveredHost` gains `observed: frozenset[str] | None` (`None` = legacy "every non-empty field observed" semantics) and `merge_entry` only lets a discovered value win when the provider actually observed that field — a provider-filled default (e.g. AWS's `access_mode="ssm"` when the `remo_access_mode` tag is absent) can no longer silently overwrite a hand-edited registry value. Uniformity locked by construction and CI: identical shared flags are the same `OptionSpec` object everywhere (SC-002); `create --yes` is accepted with a one-release deprecation notice on all four providers (has no effect, matching today's behavior — removed next release); `remo shell`'s unknown-provider-type update path now raises an explicit error instead of silently returning 0 (SC-004); zero-tolerance architecture gates (`tests/unit/test_architecture.py`) enforce no `sys.exit` in `providers/` and no private cross-module reach-ins from `cli/`. No registry schema change; no new runtime deps; `remo --help`/shell completion still import zero optional provider SDKs (SC-008).
- 017-web-adopt-simplify: Collapsed the two near-identical adoption flows into ONE `core/web_adopt.run_push` path — first push adopts, later pushes re-sync (auto-detected from the delta cache); `_adopt_flow` deleted, `remo web adopt` kept one release as a deprecated alias that prints a notice and delegates to `run_push`. Added lifecycle closure and drift visibility: a zero-network `remo web status` offline drift command + a new stdlib-only `core/web_drift.py`; an out-of-date nudge after every registry-mutating command (`run_sync` apply path + provider create/destroy + add/remove, never on dry-run/no-op or `aws stop|start|reboot`); best-effort `remo-web@` authorized_keys revocation of removed instances over the operator's ambient SSH (reported `revoked`/`could_not_revoke`, never fatal); a `--force` flag that bypasses the fingerprint fast-path and re-scans/re-authorizes every direct-access instance; and a service-reported mirror-identity marker (`web-identity/mirror-meta.json`: generation counter + last-push descriptor, additive on `GET/PUT /setup/*`) that warns a second workstation before it overwrites another's mirror. Fixed `web/state.py` mode detection so bare-metal `remo web serve` (always has a personal `~/.ssh/id_*`) classifies `adopted` — a readable user key no longer forces `mount_configured` (non-writable `REMO_HOME` is now the authoritative signal), plus an explicit `REMO_WEB_MODE` override. Push cache bumped `cache_version: 2 → 3` (per-deployment `{mirror_generation, instances}` with a non-secret per-instance connection tuple; older caches degrade to empty). No new runtime deps; no registry schema change; the only wire change is additive setup-status/registry fields.

Only the newest three entries live here — `update-agent-context.sh` discards the rest on every run. The complete history is archived in [`docs/feature-history.md`](docs/feature-history.md); move displaced entries there rather than letting the generator drop them.


<!-- MANUAL ADDITIONS START -->
<!-- MANUAL ADDITIONS END -->
