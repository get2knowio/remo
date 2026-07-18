# Research: CLI-to-Web Adoption

**Feature**: 011-web-adopt | **Date**: 2026-07-16

All Technical Context unknowns resolved. Each decision below records what was
chosen, why, and what was rejected.

## R1. Service state layout — one volume, one new subdirectory

**Decision**: Keep the container's `REMO_HOME` at `~/.config/remo` (existing
resolution in `core/config.py`) and make it the single writable named volume
in adopted deployments. Adoption state lives under
`~/.config/remo/web-identity/`: `id_ed25519` + `id_ed25519.pub` (service
keypair), `known_hosts` (service-managed SSH host keys), `state.json`
(deployment id + adoption metadata). The registry stays at its existing path
(`~/.config/remo/known_hosts`).

**Rationale**: One volume is the smallest possible operator surface (compose:
one `volumes:` line; hola: one volume mapping). Everything the service owns
lives together, so "reset the state volume" is a complete, documented
rotation/reset procedure (spec clarification Q5). Reusing `REMO_HOME` means
`core/config.py` path resolution and `save_known_host()`'s atomic-write
machinery work unmodified — the registry write path already handles
mkdir + temp-file + rename.

**Alternatives considered**: A separate `/var/lib/remo-web` volume (second
mount, splits state from registry, complicates reset story); storing the key
in `~/.ssh` (collides with the RO bind-mount mode's mount point — a writable
`~/.ssh` volume would shadow users' existing mounts and confuse mode
detection).

## R2. Configuration-state detection

**Decision**: New `web/state.py` derives the state at startup and on demand:

- **mount-configured**: registry file exists AND `REMO_HOME` is not writable
  (`os.access(W_OK)` fails — the `:ro` bind mount), OR registry exists and a
  user SSH identity is present via today's resolution
  (`REMO_WEB_SSH_IDENTITY_FILE` / `~/.ssh/id_*`) without a service keypair.
- **unconfigured**: `REMO_HOME` writable, no registry, no service keypair —
  or service keypair present but no registry yet (generated, awaiting first
  push).
- **adopted**: `REMO_HOME` writable AND service keypair present AND registry
  present.
- **broken**: any state whose required artifacts exist but are unreadable /
  unusable (reuses the existing `health._check_*` probes, including the
  EACCES-safe wrappers added in 010).

Precedence: mount-configured wins when both a user identity and a service
keypair are somehow present (explicit mounts are the operator's stated
intent).

**Rationale**: Pure filesystem probes, no stored mode flag to drift out of
sync with reality; the RO mount is detectable exactly by its read-onlyness.
Matches FR-003/FR-005 and keeps the existing deployment mode's behavior
byte-identical (no registry mount → previously "missing"/failing; now that
same probe result maps to "unconfigured" only when the directory is
writable).

**Alternatives considered**: An explicit `REMO_WEB_MODE` env var (operator
can set it wrong; two sources of truth); a mode marker file (stale after
volume surgery).

## R3. Service keypair generation

**Decision**: Generate with `ssh-keygen -t ed25519 -N "" -C
"remo-web@<deployment-id>"` as a subprocess at service startup when the state
is unconfigured and no keypair exists; `deployment-id` is a random 8-char
token minted once and persisted in `state.json`. Never regenerate if the key
files exist (FR-002). 0600/0644 permissions enforced after creation.

**Rationale**: `ssh-keygen` is already in the image (openssh-client is a
runtime dependency for the terminal broker); subprocess generation avoids
adding a crypto library dependency. The comment embeds the marker used for
`authorized_keys` management (R7) so an operator reading a host's
`authorized_keys` can identify the entry (SC-008).

**Alternatives considered**: `cryptography`/`paramiko` in-process generation
(new dependency for one call); generating lazily on first setup-API call
(leaves the "awaiting adoption" page unable to show progress and makes
startup state ambiguous).

## R4. Setup API surface and authentication

**Decision**: New router `web/api/setup.py` mounted at `/api/v1/setup`:

- `GET /api/v1/setup/status` — configuration state + deployment id (+ public
  key when adopted/unconfigured).
- `GET /api/v1/setup/identity` — the service's public key + deployment id.
- `PUT /api/v1/setup/registry` — the adoption payload (registry mirror +
  host keys); applied atomically; 409 with machine-readable reason when
  mount-configured (FR-017); 422 for payload violations.
- `POST /api/v1/setup/verify` — runs the existing `check.run_checks()`
  (instances included) and returns the results as JSON.

All routes require `Authorization: Bearer <REMO_WEB_API_TOKEN>` enforced by a
shared FastAPI dependency: `hmac.compare_digest` against the configured
token; when the token is unset the dependency returns 404 for every setup
route (fail closed, surface hidden — FR-021). Failed auth → 401 with no
detail, logged without the presented credential (FR-024). The token is added
to `logging_config.py`'s redaction patterns (Authorization headers and the
token value itself).

**Rationale**: Four small endpoints map 1:1 to the CLI flow's needs and to
`check.py`'s existing logic (verify is a thin JSON wrapper — the module
docstring already anticipated CLI/service reuse). 404-when-disabled leaks
nothing about whether the feature exists. Bearer-token via dependency keeps
the existing routers untouched.

**Alternatives considered**: Hiding setup routes entirely at app-factory time
when no token is set (equivalent externally; a dependency is simpler to test
and supports hot documentation of the 404 contract); token via query param
(ends up in logs); mTLS (wildly out of proportion for home-lab).

## R5. Atomic server-side apply

**Decision**: `PUT /setup/registry` writes the registry file and the
service-managed `known_hosts` file using the same temp-file + `os.replace`
pattern as `core/known_hosts.py:_write_lines_atomically`, one file at a time,
registry last. The handler validates the full payload before writing
anything; a failed validation writes nothing (FR-019). Mirror semantics: the
payload replaces both files wholesale.

**Rationale**: Two-file "transaction" is acceptable because the host-keys
file is consumed only when connecting to hosts that the registry names —
writing host keys first means a crash between writes leaves a superset of
needed keys and the old registry, which is safe and converges on re-push
(FR-015). Established terminal sessions hold their own SSH processes and are
untouched by file replacement (spec clarification Q3).

**Alternatives considered**: SQLite for transactional state (new storage
model for two flat files; conflicts with the flat-file registry contract
shared with the CLI); single combined JSON state file (diverges from the
registry format every other code path reads).

## R6. Threading the service identity into SSH calls

**Decision**: Extend `core/ssh.py:build_ssh_opts()` with optional
`identity_file: str | None = None` and `known_hosts_file: str | None = None`
parameters that emit `-o IdentityFile=...` / `-o IdentitiesOnly=yes` and
`-o UserKnownHostsFile=...` respectively; `None` (default) leaves today's
argv byte-identical. `WebSettings` resolves both from the detected state
(adopted mode → the `web-identity/` paths; mounted mode → `None`) and the
web call sites (`discovery.py`, `terminal.py`, `check.py`) pass them through
`build_ssh_base_cmd`.

**Rationale**: Verified in `core/ssh.py`: direct-access connections currently
set no identity or known-hosts options at all — they inherit ambient
`~/.ssh` defaults, which is exactly what an adopted container must override.
A parameter default of `None` guarantees the CLI's and mounted mode's argv
are unchanged (FR-005/FR-023 regression safety).

**Alternatives considered**: Exporting a global `GIT_SSH_COMMAND`-style env
(implicit, hard to test); writing a generated `~/.ssh/config` in the state
volume (collides with the RO `~/.ssh` mount in mounted mode; another file to
keep atomic).

## R7. Idempotent authorized_keys management on instances

**Decision**: The authorization entry is the service's single-line public key
whose comment field is `remo-web@<deployment-id>`. The CLI installs it by
running one POSIX-sh command over the user's existing SSH access that (a)
filters out every line containing ` remo-web@` from
`~/.ssh/authorized_keys`, (b) appends the current key, (c) writes via
temp-file + `mv` with permissions preserved (0600, `~/.ssh` 0700 already
guaranteed by remo bootstrap). Re-running is a no-op diff; a stale entry from
a previous deployment id is replaced (lost-volume edge case); revocation =
delete the one `remo-web@` line (SC-008).

**Rationale**: Filtering on the ` remo-web@` marker (not the full key) is
what makes rotation replace rather than accumulate. Standard
`authorized_keys` comment field needs no sshd config changes and is
human-auditable on the instance.

**Alternatives considered**: `ssh-copy-id` (append-only — violates
idempotence and rotation); a separate `authorized_keys.d` include (needs
sshd_config changes on every instance — out of scope by the "hosts already
deployed" premise); marker as a `# comment line` above the key (two-line
management is fragile under concurrent editors).

## R8. Host-key scan and workstation-side verification

**Decision**: For each direct-access instance the CLI runs `ssh-keyscan -T 5
-t ed25519,ecdsa,rsa <host>` and verifies the result against the
workstation's trusted store with `ssh-keygen -F <host> -f
~/.ssh/known_hosts` (which transparently handles `HashKnownHosts` hashed
entries), comparing full key lines. Match → push the scanned lines. Mismatch
→ push nothing for that instance, flag prominently (FR-010). No trusted
record → interactive fingerprint confirmation (`ssh-keygen -lf` rendering,
SHA256), TTY only; non-interactive runs skip and report (spec clarification
Q2).

**Rationale**: `ssh-keygen -F` is the only robust answer to hashed
known_hosts files — string comparison against the raw file breaks for any
workstation with `HashKnownHosts yes`. Scanning fresh (rather than exporting
stored lines) guarantees the service receives keys for the addresses it will
actually dial.

**Alternatives considered**: Parsing/exporting `~/.ssh/known_hosts` directly
(fails on hashed entries, exports stale keys); letting the service
`ssh-keyscan` for itself (recreates TOFU inside the service — explicitly
forbidden by FR-009).

## R9. CLI HTTP client and `--via` tunnel

**Decision**: The CLI talks to the setup API with stdlib
`urllib.request` (JSON bodies, `Authorization: Bearer` header, explicit
timeouts) — the same approach `providers/hetzner.py` already uses; no new
dependency, and `cli/web.py` keeps its no-web-extra import guarantee.
`remo web adopt --via <host>` binds a free local port (bind-to-port-0 probe),
starts `ssh -N -L <port>:127.0.0.1:<service-port> <host>` with
`ExitOnForwardFailure=yes`, waits for the forward, then runs the identical
flow against `http://127.0.0.1:<port>`. Documentation + the CLI's error
message state that tunneled adoption requires `127.0.0.1` in
`REMO_WEB_ALLOWED_HOSTS` (the compose example's default already includes it).

**Rationale**: urllib keeps the workstation CLI dependency-free (FR-006 needs
four small HTTP calls). The tunnel reuses the user's existing SSH trust — the
same trust adopt is built on — and needs zero service-side support.

**Alternatives considered**: httpx/requests (new runtime dependency for the
base CLI); implementing the tunnel with `-W`/ProxyCommand tricks (fragile);
teaching the service a second bind for "internal" traffic (more surface,
solves nothing the tunnel doesn't).

## R10. Saved adoption credentials

**Decision**: `~/.config/remo/web-service.json`, mode 0600, single default
deployment (spec clarification Q4): `{"url": ..., "token": ...,
"deployment_id": ...}`. Written only after explicit confirmation at the end
of a successful adopt (FR-025). `remo web push` reads it; missing file →
prompt exactly like first-time adopt (FR-027 / US4 scenario 4).

**Rationale**: Same directory the CLI already owns (`REMO_HOME`), same
pattern as `gh`/`hcloud` hosts files. Storing `deployment_id` lets push
detect a service identity change (state volume reset) and recommend full
re-adopt instead of a half-applied push.

**Alternatives considered**: OS keyring (new dependency, headless-unfriendly);
appending to the registry file (wrong lifecycle, wrong sensitivity).

## R11. Entrypoint gate and health/readiness in the unconfigured state

**Decision**: `GET /api/v1/ready` returns 200 with
`{"status": "unconfigured", ...}` when the state is unconfigured (the
container is healthy and doing its job: awaiting adoption); 200
`"ok"` when configured; 503 for broken, as today. `remo web check` (and thus
`docker/entrypoint.sh`'s startup gate, which runs it with instance checks
disabled) treats unconfigured as PASS with an "awaiting adoption — run
`remo web adopt`" detail line. The compose healthcheck (`curl -fsS
/api/v1/ready`) therefore passes in the unconfigured state, satisfying
SC-006's no-restart requirement.

**Rationale**: An unconfigured service must be healthy per FR-001/FR-003 —
503 would crash-loop `restart: unless-stopped` deployments and make hola show
a failing app. The status body (not the HTTP code) carries the state
distinction, which the SPA reads to render the awaiting-adoption page.

**Alternatives considered**: A separate `/api/v1/state` endpoint (the ready
payload already exists and the SPA already calls it); 503-with-state-body
(fails the compose healthcheck and hola's lifecycle view).

## R12. Frontend awaiting-adoption page

**Decision**: The SPA reads the extended ready/status payload on load; when
`unconfigured`, it renders a single `AwaitingAdoption` component: a short
explanation, the copy-pastable `remo web adopt <url>` command with the
current origin pre-filled, and a "waiting..." poll that flips to the
dashboard automatically once the state becomes configured. No instance data,
no terminals, no public key display (identity retrieval stays behind the
token per FR-004/R4).

**Rationale**: Matches FR-004's minimal role for the browser; the
auto-flip-on-poll gives the operator a satisfying zero-refresh finish to the
adopt flow (the SPA already polls discovery state on an interval — same
mechanism).

**Alternatives considered**: Showing the public key / fingerprint on the page
(pre-auth information disclosure with no user need — the CLI fetches it with
the token); a static "not configured" error (hostile, indistinguishable from
broken).

## R13. Testing strategy

**Decision**:
- **Unit (service)**: state detection matrix (all four states × probe
  failures), token dependency (set/unset/wrong/constant-time — reuse the
  skipif-root pattern from 010's unreadable-registry tests where perms are
  involved), atomic apply (interrupt between files), ready-payload states,
  redaction of Authorization headers.
- **Unit (workstation)**: payload building from a fixture registry
  (mirror semantics, SSM exclusion), keyscan-verify decision table
  (match/mismatch/absent × interactive/non-interactive) with mocked
  subprocess, authorized_keys command construction, saved-credentials
  read/write/permissions, empty-registry guard.
- **Integration**: full adopt against `remo web serve` bound to
  127.0.0.1 with a temp REMO_HOME (no Docker), asserting end state of
  registry + web-identity files and the verify report; unreachable-instance
  entries use the RFC 2606 `.invalid` pattern established in 010.
- **Image** (`REMO_RUN_IMAGE_TESTS=1`): container boots with an empty named
  volume + token env → `/api/v1/ready` reports unconfigured within 30 s,
  keypair exists in the volume, restart reuses the keypair; RO bind-mount
  mode regression (existing tests must stay green unchanged).

**Rationale**: Mirrors the layered test structure 010 established; every
conditional path called out by Constitution Principle II has a named test
above.

**Alternatives considered**: End-to-end with real instances in CI (no remo
hosts exist in CI; the smoke-test workflow already covers real-instance SSH
separately).
