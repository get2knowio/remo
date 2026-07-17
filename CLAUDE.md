# remo Development Guidelines

Auto-generated from all feature plans. Last updated: 2026-07-13

## Constitution

See `.specify/memory/constitution.md` for project principles and non-negotiable standards.

## Active Technologies
- Ansible 2.14+ / YAML + `ansible.builtin`, `community.general` (existing), Incus CLI (local) (002-incus-container-support)
- N/A (Incus storage pools already configured by 001-bootstrap-incus-host) (002-incus-container-support)
- Python 3.11+ + Click (CLI framework), InquirerPy (interactive picker), boto3 (AWS, optional), hcloud (Hetzner, optional) (003-python-cli-rewrite)
- Flat file (`~/.config/remo/known_hosts`, colon-delimited) (003-python-cli-rewrite)
- Cross-provider snapshot model (`models/snapshot.py`) + shared helpers in `core/snapshot.py` (name generator, validator, table formatter, destroy-time cleanup hook). No new runtime deps. (005-provider-snapshots)
- FastAPI/Uvicorn + WebSockets (backend, optional `web` extra), TypeScript/Vite/React + ghostty-web (frontend), Bash (`remo-host` host command templated by Ansible) (010-web-session-interface)
- Stdlib `urllib.request` CLI setup client + token-gated `/api/v1/setup/*` FastAPI surface; service state in flat files under the writable `REMO_HOME` volume (`web-identity/` keypair + service known_hosts, `~/.config/remo/web-service.json` saved credentials) (011-web-adopt)

- Ansible 2.14+ / YAML + `ansible.builtin`, `community.general` (for zypper module) (001-bootstrap-incus-host)

## Project Structure

```text
src/remo_cli/              # Python CLI package (src layout, hatchling build)
├── __init__.py            # Version from importlib.metadata
├── __main__.py            # python -m remo_cli entry point
├── cli/                   # Click command layer (parsing only, no business logic)
│   ├── main.py            # Root CLI group, command registration, passive update check
│   ├── shell.py           # remo shell
│   ├── cp.py              # remo cp
│   ├── init_cmd.py        # remo init
│   ├── web.py             # remo web {serve,check,adopt,push} — serve/check lazy-import remo_cli.web.* (NFR-008); adopt/push use core/web_adopt only
│   └── providers/         # Provider CLI groups
│       ├── incus.py       # remo incus {create,destroy,update,list,sync,bootstrap}
│       ├── hetzner.py     # remo hetzner {create,destroy,update,list,sync}
│       ├── aws.py         # remo aws {create,destroy,update,list,sync,stop,start,reboot,info}
│       └── proxmox.py     # remo proxmox {create,destroy,update,list,sync,bootstrap}
├── providers/             # Business logic (no Click imports)
│   ├── incus.py
│   ├── hetzner.py
│   ├── aws.py
│   └── proxmox.py
├── core/                  # Shared utilities (no provider knowledge)
│   ├── config.py          # REMO_HOME, paths, read-only registry accessor
│   ├── output.py          # Colored output, confirm()
│   ├── validation.py      # Name, port, region, tool validation
│   ├── known_hosts.py     # Flat-file host registry
│   ├── ssh.py             # build_ssh_base_cmd(), SSH options, terminal reset, timezone
│   ├── remo_host_client.py  # Versioned remo-host protocol client (shared by CLI + web)
│   ├── web_adopt.py       # Workstation-side adoption/push engine (stdlib HTTP, keyscan trust verify, authorized_keys mgmt, --via tunnel)
│   ├── ansible_runner.py  # Ansible playbook subprocess
│   ├── picker.py          # InquirerPy fuzzy picker
│   ├── rsync.py           # File transfer
│   ├── version.py         # Version check, passive update notification
│   └── init.py            # remo init logic
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
│   └── api/
│       ├── hosts.py            # GET /api/v1/hosts, /sessions, POST /discovery/refresh
│       ├── setup.py            # Token-gated /api/v1/setup/{status,identity,registry,verify} (011-web-adopt)
│       └── terminals.py        # POST/GET/DELETE /api/v1/terminals, WS /api/v1/terminals/{id}
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
uv sync --extra aws               # Install with AWS (boto3) only
uv sync --extra hetzner           # Install with Hetzner (hcloud) only
uv sync --extra web               # Install with web service (FastAPI/Uvicorn) only

# Verify installation
uv run remo --version
uv run remo --help

# Run tests
uv run pytest

# Type checking and linting
uv run mypy src/remo_cli
uv run ruff check src/remo_cli

# Web service (requires the `web` extra)
uv run remo web check             # Validate registry/SSH/runtime-dir/reachability
uv run remo web serve             # Run the browser terminal broker locally

# Frontend (requires Node; see frontend/package.json)
cd frontend && npm ci
npm run build                     # tsc -b && vite build -> frontend/dist
npm run lint                      # tsc --noEmit
```

## Architecture (Three-Layer)

- **cli/** → Click commands, argument parsing only. No business logic.
- **providers/** → Business logic. No Click imports. Called by cli layer.
- **core/** → Shared utilities. No provider knowledge. Used by both layers.

Provider SDKs (boto3, hcloud) are lazy-imported with clear error messages if missing.

## Code Style

- Python: Type hints, `from __future__ import annotations`, no docstrings on obvious methods
- Ansible 2.14+ / YAML: Follow standard conventions plus Constitution principles

## Recent Changes
- 011-web-adopt: Added CLI-to-web adoption — unconfigured boot with service-scoped ed25519 identity, token-gated `/api/v1/setup/*` (REMO_WEB_API_TOKEN, fail-closed), `remo web adopt`/`remo web push` (registry mirror + workstation-verified host keys + idempotent `remo-web@<id>` authorized_keys entries), AwaitingAdoption SPA page.
- 010-web-session-interface: Added remo-web Docker service (FastAPI + React/ghostty-web) brokering browser terminal sessions across all Remo-managed instances via a new remo-host SSH command; web extra + remo web {serve,check} CLI group.
- 005-provider-snapshots: Added cross-provider snapshot CLI (`remo <P> snapshot {create,list,restore,delete}`) + destroy-time cleanup hook across Incus / Proxmox / AWS / Hetzner.


<!-- MANUAL ADDITIONS START -->
<!-- MANUAL ADDITIONS END -->
