# remo Development Guidelines

Auto-generated from all feature plans. Last updated: 2026-07-27

## Constitution

See `.specify/memory/constitution.md` (v2.1.0) for the full text. The nine
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
| IX | Pre-Release Builds Never Touch PyPI | Test off-index (git ref → CI wheel); PyPI receives only final, working releases |

Principles I, IV, and VIII are machine-enforced — see [Quality Gates](#quality-gates).
Principle IX is a human gate — see [Pre-release testing](#pre-release-testing-principle-ix).

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
- No new runtime deps; no registry schema change; no new `InstanceStatus` member. `models/discovery.py`'s `DiscoverySnapshot` and `web/api/hosts.py`'s `InstanceOut` gain additive advisory fields (`stale`, `last_ok_at`, `consecutive_failures`); `web/discovery.py` gains retryable-failure retention (last-known-good snapshot kept through a monotonic-clock grace budget) + a 2x-plus-slack outer timeout; `web/config.py` gains `discovery_offline_grace_s` (`REMO_WEB_DISCOVERY_OFFLINE_GRACE_S`, default 120, 0 disables). Frontend: `railModel.ts` `isStale` beside `isError`, `SessionRail` stale chip, `WorkspacePane` in-memory sticky last-known-target map. Generated artifacts regenerated (Principle IV). (024-discovery-resilience)
- No new runtime deps; no registry schema change. New `core/attach.py` (shared attach argv, web wrapper kept), `core/connector.py` + `remo_attach_document.json` (contract v1), `providers/connector.py`, `cli/connector.py`, role `ssm_connector` (025-ssm-connector)

- No new runtime deps; no registry schema change. `core/web_sync.py` (stdlib + core only: three-way merge engine + `remo web sync` driver, push-cache v4 `entry` merge base, PUT v3 `base_generation` precondition with bounded 409 re-merge retry), `web/mirror_meta.py` (single marker writer: generation + `last_push` + additive `last_change`), `web/trust_store.py` (per-instance known_hosts slices), `web/jobs.py` (detached restart-surviving embedded-CLI jobs under `<REMO_HOME>/web-jobs`), `web/api/registry_admin.py` (REMO_WEB_REGISTRY_ADMIN dormant-404 surface). `core/ssh.py` gains the `$REMO_SSH_IDENTITY_FILE` fallback (arg → registry → env → ambient); `registry.py` gains public `canonical_entry`. Docker image bakes Galaxy collections; entrypoint seeds `collections.lock`. (023-web-registry-sync)

- Ansible 2.14+ / YAML + `ansible.builtin`, `community.general` (for zypper module) (001-bootstrap-incus-host)
- Formal provider abstraction: `core/provider_registry.py` (`ProviderDescriptor`/`OptionSpec`/`CommandSpec`/`ConnectionSpec` + registry), `core/provider_protocol.py` (`Provider` Protocol), `core/errors.py` (typed taxonomy: `ProviderError`/`MissingDependencyError`/`PreconditionError`/`OperationFailedError`/`UserAbortedError`), `core/lifecycle.py` (shared `run_destroy` template), `cli/providers/factory.py` (generates all four provider CLI groups from descriptors). No new runtime deps. (018-provider-abstraction)
- The FastAPI service is the machine-checked source of truth for every shape `frontend/` consumes. `scripts/export_openapi.py` (stdlib, new) exports `frontend/src/api/generated/openapi.json` (`create_app().openapi()`) and `terminal-frames.json` (`TypeAdapter(...).json_schema()` over the six new `web/frames.py` control-frame models); `openapi-typescript` v7 (exact-pinned frontend devDependency) generates `schema.d.ts`/`terminal-frames.d.ts` (the latter via a small synthetic-OpenAPI wrapper, `frontend/scripts/generate-frame-types.mjs`, since openapi-typescript requires a genuine OpenAPI document). `web/api/hosts.py` gained `KnownProviderType(str, Enum)` (fixed to the built-in provider set, not the live registry — FR-004a) and enum-typed `InstanceOut`/`SessionTargetOut` fields; new `ErrorEnvelope`/`HealthResponse`/`ReadinessResponse`/`MintPairingResponse`/`DetailResponse` response models declare what each route already returns (no serialized byte moves). `web/api/terminals.py`'s five ad-hoc WS control-frame dict literals are gone, replaced by `web/frames.py` models (`_handle_control` preserves its exact silent-drop behavior for malformed/unknown inbound frames). Three drift checks (`tests/unit/test_schema_drift.py` for REST + frame freshness against the Python app; `frontend/scripts/check-types-fresh.mjs` for the generated `.d.ts` files) fail the build with an actionable message — never skip — when a checked-in artifact goes stale; `frontend/src/api/client.ts` and `frontend/src/components/providerMeta.ts` now import/derive from the generated types instead of hand-declaring parallel copies (a schema-derived `Record<InstanceStatus/KnownProviderType, …>` makes a new enum member a compile error while keeping a runtime fallback for off-union values, FR-013a). No registry schema change; no new service runtime dependency. (020-openapi-type-generation)

## Project Structure

```text
src/remo_cli/              # Python CLI package (src layout, hatchling build)
├── __init__.py            # Version from importlib.metadata
├── __main__.py            # python -m remo_cli entry point
├── cli/                   # Click command layer (parsing only, no business logic)
│   ├── main.py            # Root CLI group; mounts one group per remo_cli.core.provider_registry.all_descriptors() — built-ins first, then `remo.providers` entry-point plugins in name order (027)
│   ├── plugins.py         # remo providers — lists every provider with its source (builtin / distribution version) and load status, incl. skipped plugins with reasons (027)
│   ├── shell.py           # remo shell — pre-connect version check for EVERY host type; _run_tools_upgrade() dispatches to the provider's update_entry() (delegates to upgrade()) or, for type="ssh", providers/added.configure(); prompt names the exact `remo <type> upgrade <name>` / `remo configure <name>` it will run; unknown type raises (no silent no-op); an added host with no `~/.remo-version` marker connects silently (FR-011)
│   ├── cp.py              # remo cp
│   ├── added.py           # remo add / remo remove / remo configure — provider-neutral SSH host registration (014) and configure (022)
│   ├── web.py             # remo web {serve,check,sync,push,status,adopt} — serve/check lazy-import remo_cli.web.* (NFR-008); sync/push/status/adopt use core/web_sync + core/web_adopt + core/web_drift only (adopt aliases push; push is the deprecated one-way force path — sync is the bi-directional merge)
│   ├── connector.py       # remo connector {enroll,attach,document,status,unenroll} — SSM connector (025); Click wiring only, lazy imports, no remo_cli.web import
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
│   ├── connector.py        # SSM connector business logic: launcher (attach), document, enroll/status/unenroll (025)
│   └── builtin.py         # register_builtins(): the four built-in descriptors in fixed order; called by provider_registry._ensure_discovered() before entry-point discovery (027)
├── core/                  # Shared utilities (no provider knowledge)
│   ├── config.py          # REMO_HOME, paths, read-only registry accessor
│   ├── platform.py        # POSIX-only startup gate: is_supported_platform() + the WSL2 message cli/main.py exits on (no remo_cli imports — it runs before them)
│   ├── errors.py          # ProviderError taxonomy (contracts/errors.md); single CLI translation boundary is factory.py's provider_command wrapper
│   ├── provider_registry.py  # ProviderDescriptor/OptionSpec/CommandSpec/ConnectionSpec/ArgumentSpec + shared OptionSpec catalog + register/get_descriptor/get_provider/all_descriptors/builtin_descriptors/descriptor_source/is_provider_type/temporary_registration; PROVIDER_API_VERSION; _ensure_discovered() = builtins then entry points (027); descriptor fields registry_legacy_keys/region_scoped_sync/sync_scope_description carry the last per-type facts out of core
│   ├── provider_plugins.py   # `remo.providers` entry-point discovery (stdlib importlib.metadata): one warning per broken plugin, first-wins duplicates, REMO_DISABLE_PROVIDER_PLUGINS, REMO_PROVIDER_API_VERSION handshake, PluginLoadRecord/plugin_load_records() for `remo providers` + `remo web check` (027)
│   ├── provider_protocol.py  # Provider Protocol (uniform entry-based surface: update_entry, teardown, probe, snapshot_create/restore/delete/list)
│   ├── lifecycle.py       # run_destroy(): guard → snapshot pre-cleanup → confirm → teardown → best-effort registry removal (the one destroy sequence; providers implement only teardown())
│   ├── output.py          # Colored output, confirm(), Column/render_host_table (shared list-table renderer)
│   ├── validation.py      # Name, port, region, tool validation
│   ├── registry.py        # Registry v2 accessor: parse/serialize/validate/lock/migrate (registry.json + legacy known_hosts); parse AND serialize driven by descriptor.registry_fields (+ registry_legacy_keys), access-mode rules by ConnectionSpec.mode_field_aware, is_known_type() = ssh pseudo-type or any registered provider incl. plugins (KNOWN_TYPES literal gone, 027); an entry of an uninstalled type is preserved verbatim and warned, never dropped
│   ├── known_hosts.py     # Thin delegates onto registry.py (public API unchanged: get/save/remove/clear_known_hosts*); HOST_SCOPED short-name matching and display_name_for() driven by descriptor.name_format (models/host.py's display_name is a lazy shim onto it, 027)
│   ├── ssh.py             # build_ssh_base_cmd(), SSH options, terminal reset, timezone; SSM ProxyCommand construction lives behind descriptor.connection.proxy_hook (AWS: providers/aws.py:ssh_proxy_hook), not hardcoded here
│   ├── attach.py          # build_attach_argv() — host-agnostic "ssh -tt … remo-host sessions attach" builder shared by web/terminal.py (now a thin wrapper) and the SSM connector launcher; no remo_cli.web import (025)
│   ├── connector.py       # SSM connector contract: TargetV1 codec, exposure gate (default deny), ErrorCode/format_error_line, ConnectorState, document loader (ships core/remo_attach_document.json), connector_attach_argv() (025)
│   ├── reconcile.py       # SyncScope/DiscoveredHost/build_plan/run_sync; DiscoveredHost.observed (frozenset[str] | None) + observed-aware merge_entry (closes #87 — a provider-filled default never clobbers a hand-edited registry value); SyncScope validation/scoping/describe() driven by descriptor name_format + region_scoped_sync + sync_scope_description, no provider literals at all (027)
│   ├── remo_host_client.py  # Versioned remo-host protocol client (shared by CLI + web)
│   ├── web_adopt.py       # Unified workstation push engine: run_push (adopt-or-resync), run_adopt alias, keyscan trust verify, authorized_keys authorize + best-effort revoke, verification-driven self-heal of `unchanged` instances that verify auth_failed (#122), --force, flap detection, push cache v3, --via tunnel (stdlib HTTP)
│   ├── web_drift.py       # Offline registry-vs-push-cache diff + shared out-of-date nudge (stdlib + core/models only; no web extra)
│   ├── web_sync.py        # `remo web sync` three-way merge engine + driver (base = push-cache v4 entries, PUT v3 base_generation, 409 re-merge retry; stdlib + core only) (023)
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
│   ├── mirror_meta.py         # Mirror-identity marker accessor (read_mirror_meta/record_change): generation + last_push + last_change, shared by setup + registry-admin writers (023)
│   ├── trust_store.py         # Service known_hosts helpers: line validation, atomic writes, per-instance set/remove slices (023)
│   ├── jobs.py                # CliJobRunner — detached, restart-surviving `remo` CLI subprocess jobs under <REMO_HOME>/web-jobs (023)
│   ├── check.py               # `remo web check` diagnostic
│   ├── logging_config.py      # Secret/token/proxy-command redaction in logs
│   ├── models.py               # Service-only entities: TerminalAttachment, WsToken, SshMaster
│   ├── operator_auth.py        # Pluggable operator-authentication seam gating pairing-code minting (forward-auth header today; OIDC deferred)
│   ├── pairing.py              # In-memory, single-live, TTL'd pairing-code session manager replacing the static setup API token
│   └── api/
│       ├── hosts.py            # GET /api/v1/hosts, /sessions, /hosts/{id}/stats (ungated, TTL-coalesced), POST /discovery/refresh; shared remo-host call plumbing
│       ├── gating.py           # Shared dormant-404 gate for flag-guarded admin routers (one 404 shape, one operator-auth check)
│       ├── host_admin.py       # Gated maintenance API (REMO_WEB_HOST_ADMIN, dormant-404 like /setup): project clone/delete/rebuild + job polling
│       ├── registry_admin.py   # Gated registry-admin API (REMO_WEB_REGISTRY_ADMIN, dormant-404): add/remove/configure SSH hosts via the embedded CLI + job polling (023)
│       ├── setup.py            # Pairing-gated /api/v1/setup/{status,identity,registry,verify,end} (011-web-adopt; `end` added by #158)
│       ├── terminals.py        # POST/GET/DELETE /api/v1/terminals, WS /api/v1/terminals/{id}
│       └── pairing.py          # POST /api/v1/pairing/{mint,end} — operator-auth-gated pairing-code control plane, outside the dormant setup router
└── models/
    ├── host.py             # KnownHost dataclass
    ├── snapshot.py         # Cross-provider snapshot model
    ├── capability.py       # RemoteCapability (remo-host capabilities)
    ├── session_target.py   # SessionTarget (opaque id, zellij/devcontainer state)
    ├── host_stats.py       # HostStats/DiskUsage/TempReading (remo-host host stats snapshot; tolerant from_dict)
    ├── host_job.py         # JobRef/JobState/JobStatus (detached clone/rebuild jobs)
    └── discovery.py        # DiscoverySnapshot + typed InstanceStatus

frontend/                  # remo-web browser SPA (Vite + React + TypeScript)
├── src/
│   ├── api/client.ts        # REST + WS terminal client (remo-terminal.v1 subprotocol)
│   ├── components/          # Dashboard, InstanceGroup, TargetCard, GridView, TabView, TerminalCard
│   │                        #   + masterLayout.ts (pure pane geometry: uniform grid + master/stack tiling)
│   │                        #   + HostDetailPage.tsx (full-screen host overlay: live stats strip, projects
│   │                        #   table, host-admin-gated clone/rebuild/delete + capability nudge) with
│   │                        #   HostShellPanel.tsx (TerminalConnection host_shell origin + renderer + fitLoop,
│   │                        #   NOT a TerminalCard), JobProgressPanel.tsx (2s job poll, log tail, refresh on
│   │                        #   terminal state), RebuildConfirmDialog.tsx / DeleteProjectDialog.tsx (consent ladder)
│   ├── state/                # discovery.ts, workspace.ts (layout persisted to localStorage), settings.ts (display prefs: site light/dark mode, accent, fonts, terminal theme + per-target overrides)
│   │                        #   + hostStats.ts (useHostStats: 5s poll, visibility pause/resume,
│   │                        #   stops + surfaces the nudge on a 409 unsupported_host_tools)
│   │                        #   + diagnostics.ts (read-only snapshot: pane registry + `window.__remo.diagnostics()`;
│   │                        #   redaction contract — no buffer text, no ws_token, no socket url/protocol)
│   ├── terminal/              # RendererAdapter (seam), XtermRenderer (the one engine), TerminalConnection, keymap
│   │                        #   + fitLoop.ts (the container->emulator->PTY fit, extracted so the
│   │                        #   browser geometry suite drives the shipped code and not a copy)
│   └── theme/                 # tokens.css (light-dark() palette, one pair per token), fonts.ts, terminalThemes.ts (8 terminal color schemes: a token-derived Remo Dark/Light pair the default 'auto' selection tracks, plus 6 curated third-party)
└── tests/
    ├── e2e/               # Playwright console suite — needs a live `remo web serve` (REMO_E2E_BASE_URL)
    └── geometry/          # Playwright terminal-geometry suite — no backend, gates CI
        └── harness/       # Vite fixture mounting the REAL TerminalCard + paneLayout

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
│   ├── ssm_connector/     # SSM connector (025): install/register SSM Agent, run-as user, pinned
│   │   │                  #   remo, private registry/exposure/identity, exposed-host key trust
│   │   └── tasks/
│   │       └── scan_and_trust_host_key.yml  # trust-on-first-enrollment per exposed host
│   ├── deacon/            # Pinned deacon devcontainer runtime (default for new hosts since 026)
│   └── user_setup/
│       └── templates/
│           └── remo-host.sh.j2   # Versioned `remo-host` command (capabilities/sessions/attach; projects.rebuild advertised for the reference CLI and deacon)
├── tasks/
│   └── resolve_devcontainer_runtime.yml  # `auto` → effective runtime: explicit > marker > legacy CLI > nested-overlayfs > deacon (026)
├── incus_bootstrap.yml
├── ssh_configure.yml      # Generic configure play for `remo add` hosts (022): maps remo_ssh_*
│                          #   -> ansible_port/ansible_ssh_private_key_file, binds remo_user to the
│                          #   registered account, reuses tasks/configure_dev_tools.yml
├── ssm_connector_enroll.yml    # SSM connector (025): 3 plays — role ssm_connector, then
│                                #   ansible.posix.authorized_key on every exposed host
├── ssm_connector_unenroll.yml  # SSM connector (025): stop/disable agent, remove local state
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
uv run remo providers             # every provider + source (builtin / distribution) + plugin load status (027)
REMO_DISABLE_PROVIDER_PLUGINS=1 uv run remo --help   # built-ins only (diagnostic escape hatch)

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
uv run remo web sync URL          # Bi-directional registry sync with a deployment (023)

# SSM connector — reach any host through AWS Systems Manager (025, docs/ssm-connector.md)
uv run remo connector enroll NAME --activation-id ID --region REGION --expose HOST/PROJECT
uv run remo connector attach -- TARGET    # invoked by the remo-attach session document only
uv run remo connector document            # print the shipped remo-attach session document
uv run remo connector status NAME
uv run remo connector unenroll NAME [--purge] [--yes]

# Pre-release testing, off-index (Constitution IX) — PyPI gets only final releases
uvx --from git+https://github.com/get2knowio/remo@BRANCH remo --help  # Tier 1, zero footprint
uv tool install git+https://github.com/get2knowio/remo@BRANCH         # Tier 1, daily-drive the branch
uv tool install "remo-cli[web] @ git+https://github.com/get2knowio/remo@BRANCH"  # with extras
uv tool install remo-cli --force                                      # back to the released PyPI build

# install.sh routes by version shape: a final version comes from PyPI, a
# pre-release version from its GitHub pre-release wheel (RCs never reach PyPI).
# Picks uv, or pipx when that is what is already installed; never bare pip
# (PEP 668). Refuses Git Bash/MSYS2/Cygwin before installing anything.
curl -fsSL https://raw.githubusercontent.com/get2knowio/remo/main/install.sh | bash
curl -fsSL .../install.sh | bash -s -- --prerelease            # newest GitHub pre-release
curl -fsSL .../install.sh | bash -s -- --prerelease 4.4.0rc3   # a specific one
curl -fsSL .../install.sh | bash -s -- --prerelease --dry-run  # print the command, change nothing
REMO_VERSION=4.4.0rc3 curl -fsSL .../install.sh | bash          # env twin of --version
# Flag spelling, env-var names and grep/sed release resolution are kept in
# parity with try-hola/hola's cli-install.sh; `--pre-release` stays an alias.

# Tier 2 — the real wheel, built in clean CI. Two shapes, and the difference is
# whether the artifact can later be promoted to PyPI as-is:
gh workflow run dev-build.yml                                         # dev build: X.Y.Z.devN+g<sha>,
                                                                      #   un-uploadable, validation only
gh workflow run dev-build.yml -f version=X.Y.ZrcN                     # RC: canonical, no local segment,
                                                                      #   so it stays promotable
gh workflow run dev-build.yml -f version=X.Y.ZrcN -f prerelease=true  # + GitHub pre-release (tag rc-<ver>)
gh workflow run dev-build.yml -f version=X.Y.ZrcN -f prerelease=true -f image=true  # + remo-web:<ver> image (never latest)
gh workflow run rc-image.yml -f version=X.Y.ZrcN                      # image only, for an existing rc-<ver> tag

# Install a run artifact (needs gh auth on that machine):
gh run download <run-id> -R get2knowio/remo -n remo-wheel -D ./dl
uv tool install --force "remo-cli[web] @ ./dl/remo_cli-<version>-py3-none-any.whl"

# Install from the GitHub pre-release (no gh auth needed anywhere):
uv tool install --force "remo-cli[web] @ https://github.com/get2knowio/remo/releases/\
download/rc-<version>/remo_cli-<version>-py3-none-any.whl"

# Frontend (requires Node; see frontend/package.json)
cd frontend && npm ci
npm run build                     # tsc -b && vite build -> frontend/dist
npm run lint                      # tsc --noEmit
npm run test                      # Vitest unit/component suite (jsdom, no backend)
npm run test:geometry             # Playwright terminal-geometry suite (no backend; serves its own
                                  #   fixture. jsdom has no layout engine, so this is the only place
                                  #   the terminal grid is checked against the box that clips it)
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
| Browser geometry | `cd frontend && npm run test:geometry` | Terminal grid fits its box (jsdom cannot check this) |
| Fish completion | `./tests/integration/fish_completion.sh` | Principle VI (completion runs, not just reads) |
| Packaging | wheel install smoke, Docker amd64+arm64 | Distribution integrity |
| Security | CodeQL, dependency review | Supply chain |

`ruff` and `mypy` share the one `Lint & Types` job. That job must keep
installing the `web` extra (`uv sync --all-extras`): with
`ignore_missing_imports = true`, an uninstalled FastAPI/pydantic would degrade
every `src/remo_cli/web/` module to `Any` and leave the type gate passing while
checking nothing.

### Pre-release testing (Principle IX)

PyPI receives only final, working releases. Everything before that flows
off-index, escalating only as needed: **Tier 1** git refs (`uvx --from git+…`,
`uv tool install git+…`) for the inner loop, **Tier 2** the real wheel built by
`dev-build.yml` — mandatory for any change to packaging surfaces (entry points,
extras, package data, build config), since Tier 1 exercises the sdist path and
not the wheel that ships. Non-final versions are PEP 440 pre-release/dev forms;
dev builds carry a `+g<sha>` local segment that PyPI rejects outright, so a dev
build cannot leak. An **RC is different**: `dev-build.yml` stamps an explicit
`X.Y.ZrcN` verbatim with *no* local segment, precisely so the artifact stays
promotable — promotion publishes the *identical* validated wheel, never a
rebuild. With `-f prerelease=true` that same wheel is attached to a GitHub
pre-release under an `rc-<version>` tag (never `v*`, which would trigger
`release.yml` and its PyPI/GHCR publish), giving a plain URL that installs
without `gh` auth. Add `-f image=true` (or run `rc-image.yml` for an existing
`rc-<version>` tag) to also publish `ghcr.io/get2knowio/remo-web:<version>` —
the deployable half of an RC for Compose-based installs such as a Hola catalog
channel — tagged only by its exact version, never `latest`. TestPyPI is not a dev channel. Principle IX has no CI row — it is
enforced by the `release` skill's validation gate, by that unremovable local
segment, and by review; name the tier that validated a packaging change in the
PR description.

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
- 027-provider-plugins: Made the provider mechanism pluggable and finished Principle II. **Discovery**: `core/provider_registry._ensure_discovered()` now registers the four built-ins (`providers/builtin.py::register_builtins()`, same fixed order) and then every `remo.providers` entry point in name order via new `core/provider_plugins.py` (stdlib `importlib.metadata`): an entry point resolves to a `ProviderDescriptor` or a zero-arg factory; any load failure (import error, wrong object, descriptor validation) is ONE warning naming the distribution and entry point and a skip — never an exception, never a changed exit code; a duplicate `type_name` keeps the first registration (built-in or earlier plugin) and warns naming both distributions; `REMO_DISABLE_PROVIDER_PLUGINS=1` skips discovery; `PROVIDER_API_VERSION = 1` vs the plugin's `REMO_PROVIDER_API_VERSION` module attribute warns on mismatch/absence and still registers. `builtin_descriptors()`/`descriptor_source()` keep the built-in vocabulary pinned (`KnownProviderType` and the exact-command-set test compare against built-ins; plugin types ride the `KnownEnum | str` off-union path — no artifact regenerated). **De-literalized**: `core/registry.py` parses through the reverse of `descriptor.registry_fields` plus new `registry_legacy_keys` (Proxmox's `node_user → region` migration now declared on its descriptor), gates on `is_known_type()` (ssh pseudo-type or any registered provider; `KNOWN_TYPES` is gone), keys `ssm` inference and validation off `ConnectionSpec.mode_field_aware` (message byte-identical for built-ins), and preserves an entry of an uninstalled type verbatim with a warning instead of silently carrying it; `core/reconcile.SyncScope` uses new `region_scoped_sync` + `sync_scope_description` descriptor fields (AWS/incus/proxmox strings unchanged); `KnownHost.display_name` is a lazy shim onto `core/known_hosts.display_name_for()` (HOST_SCOPED-driven), as is `providers/added.py`'s conflict check. **Visibility**: new `remo providers` (`cli/plugins.py`) and a `provider_plugins` line in `remo web check` that always PASSES (the container startup gate must never be bricked by a plugin) but lists loaded/skipped plugins with reasons and a remediation. **Fixture**: `tests/fixtures/remo_fixture_provider/` is a real distribution installed by the `dev` dependency group (`[tool.uv.sources]` path; never in the wheel's metadata) so `tests/integration/test_fixture_plugin_e2e.py` proves the whole path (group in `--help`, generated verbs, registry v2 round-trip via `box_id`/`zone`, known_hosts, web hosts API type-as-string, the disable variable in a fresh process); synthetic entry points cover every skip/duplicate/mismatch case; `tests/unit/test_architecture_provider_literals.py` gates the four provider-neutral core modules against built-in type literals. New `docs/provider-plugins.md`. No new runtime deps; no registry schema change; no optional SDK extras (#94); `ssh` untouched. Constitution IX: entry points are a packaging surface — Tier 2 wheel validation is owed before the release that ships them.
- 026-deacon-default-runtime: Made deacon the default devcontainer runtime without moving any existing host. Pin `0.2.0-rc.11` → `0.4.0` (flags remo invokes verified against the binary: `up --workspace-folder --trust-workspace-persist --remove-existing-container --build-no-cache`, `exec --workspace-folder`); `remo-host` now advertises and executes `projects.rebuild` for deacon (the two stale guards are gone; the omission mechanism survives as a `REBUILD_SUPPORTED` case list, tested with a hypothetical runtime). New request value `auto` (CLI/env/Ansible default) resolved ON THE HOST by `ansible/tasks/resolve_devcontainer_runtime.yml`: explicit > `~/.remo-devcontainer-runtime` marker > legacy host with the reference CLI > nested-overlayfs host (keeps the shimmed reference CLI — deacon is NOT verified there; forcing it prints a warning) > deacon. The effective runtime is recorded in the marker after `user_setup`, so `upgrade`/`configure` never flip a host silently; switching is explicit, stops projects first, and the other runtime's containers are neither adopted nor removed (deacon #265). Reference-CLI path unchanged; protocol version unchanged; no packaging surface.
- 025-ssm-connector: Added a transport, orthogonal to compute providers — a **connector** is a host remo already manages, enrolled (from the operator's workstation, via Ansible) as an SSM hybrid-activated managed node running SSM Agent, a dedicated non-admin run-as user, and a pinned system-wide remo. A shipped `InteractiveCommands` session document `remo-attach` (`src/remo_cli/core/remo_attach_document.json`, contract v1: `specs/025-ssm-connector/contracts/session-document.md`) takes one pattern-restricted parameter (`target` = unpadded base64url of `{"v":1,"host","project"}`) and runs `remo connector attach`, which re-validates the decoded names with the CLI's own validators (`core/validation`), refuses uid 0 / `ssm-user` (`run-as-not-in-effect`), checks a connector-local default-deny exposure file (`exposure.json`), and `exec`s the identical `ssh -tt … remo-host sessions attach` argv the web console builds for the same host/project — extracted into new `core/attach.py` so it needs no web extra (`web/terminal.py::build_attach_argv` is now a thin wrapper over it). Every pre-exec failure is exactly one `remo-connector-error: <code> <message>` line (`core/connector.py`: `TargetV1` codec, `ExposureConfig`, `ErrorCode`) — never a traceback, never a second SSH round-trip. `remo connector enroll NAME --activation-id ID --region REGION --expose HOST/PROJECT` (new `providers/connector.py`/`cli/connector.py`) reads the activation code only from a hidden prompt or stdin and hands it to a new Ansible role (`ansible/roles/ssm_connector/`, playbooks `ssm_connector_{enroll,unenroll}.yml`) through a 0600 `no_log`-guarded vars file, deleted on every exit path — the code never reaches argv, an env var, or a log line on the workstation (AWS's own `amazon-ssm-agent -register` takes it as a connector-side process argument for the seconds it runs, documented as the one residual). Enrollment materializes each exposed host as a `type: ssh` registry entry (self-target maps to `localhost`), scans and trusts its key from the connector's own network vantage point on first enrollment (a later key change is a hard error, never a silent overwrite), and authorizes the connector's own SSH identity on it. `remo connector status`/`unenroll` report/tear down local state; account-side deregistration (`aws ssm deregister-managed-instance`) stays the operator's explicit step. No `host.type` branching anywhere in the new code (Constitution II); no registry schema change; no new runtime deps. New `docs/ssm-connector.md` covers the architecture, the least-privilege caller policy, Run As (and why not `ssm-user`), the two reference deployment shapes with an honest verified/not-yet-verified split, and cost (linked to AWS's pricing page, never a hardcoded number). The live manual gate (real hybrid activation, real `aws ssm start-session`) is out of automated-test scope and tracked as an issue.
Only the newest three entries live here — `update-agent-context.sh` discards the rest on every run. The complete history is archived in [`docs/feature-history.md`](docs/feature-history.md); move displaced entries there rather than letting the generator drop them.


<!-- MANUAL ADDITIONS START -->
<!-- MANUAL ADDITIONS END -->
