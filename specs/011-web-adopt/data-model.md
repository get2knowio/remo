# Data Model: CLI-to-Web Adoption

**Feature**: 011-web-adopt | **Date**: 2026-07-16

Entities from the spec, mapped to their concrete representations. File
formats referenced here are normative; wire shapes live in
[contracts/setup-api.md](contracts/setup-api.md).

## ServiceIdentity

The service-scoped SSH keypair plus its stable deployment identifier.

| Field | Type | Notes |
|-------|------|-------|
| `deployment_id` | str (8-char URL-safe token) | Minted once with the keypair; embedded in the key comment and in every authorization entry |
| `private_key_path` | path | `$REMO_HOME/web-identity/id_ed25519`, 0600 |
| `public_key` | str | Single OpenSSH line, comment `remo-web@<deployment_id>` |
| `created_at` | ISO-8601 str | Persisted in `state.json` |

**Lifecycle**: created on first unconfigured boot → reused forever (FR-002)
→ replaced only by state-volume reset (documented rotation, clarification
Q5). Never transmitted; only `public_key` leaves the service.

**Validation**: key files must exist as a pair; a half-pair is `broken`
state. Generation enforces ed25519, empty passphrase.

## ConfigurationState

The service's self-knowledge of its mode — derived, never stored.

| Value | Derivation (see research R2) |
|-------|------------------------------|
| `unconfigured` | Writable `REMO_HOME`, no registry (service keypair may or may not exist yet) |
| `adopted` | Writable `REMO_HOME` + service keypair + registry |
| `mount_configured` | Registry present + (`REMO_HOME` read-only OR user identity present without service keypair) |
| `broken` | Required artifacts present but unreadable/unusable (existing health probes) |

**Transitions**: `unconfigured → adopted` (first successful registry PUT);
`adopted → adopted` (re-push, last-write-wins); `adopted → unconfigured`
(volume reset, external); `mount_configured` never transitions via the API
(PUT rejected, FR-017). Surfaced through the ready payload and
`GET /setup/status`.

## SetupApiToken

| Field | Type | Notes |
|-------|------|-------|
| value | str | From `REMO_WEB_API_TOKEN` env var at process start (WebSettings) |

**Validation**: compared with `hmac.compare_digest`; unset/empty → the setup
surface is disabled (404 on every setup route, FR-021). Never logged
(redaction covers the value and `Authorization` headers, FR-022/FR-024).
Rotation = redeploy with a new value.

## AdoptionPayload

The body of `PUT /api/v1/setup/registry` — a full mirror, not a diff.

| Field | Type | Notes |
|-------|------|-------|
| `version` | int (`1`) | Payload schema version; unknown version → 422 |
| `registry` | list[RegistryEntry] | Complete workstation registry; replaces the service registry wholesale (clarification Q1) |
| `host_keys` | dict[str, list[str]] | Key: registry entry `name`; value: verified `known_hosts` lines for that instance. Direct-access entries only; entries may be absent (skipped/unverified instances) |

**RegistryEntry** mirrors `models/host.py:KnownHost` exactly:
`type`, `name`, `host`, `user`, `instance_id`, `access_mode`, `region` —
serialized back to the colon-delimited registry format on apply.

**Validation** (all-or-nothing before any write, FR-019): non-empty
`registry` unless `allow_empty=true` query flag set (FR-016 guard is
CLI-side; the service enforces it too, defense in depth); every `host_keys`
key must reference a registry entry name; every key line must parse as a
`known_hosts` line; SSM-mode entries must not carry host keys (FR-012).

## InstanceAuthorizationEntry

The record on each instance that authorizes the service (managed remotely by
the CLI, never stored service-side).

| Aspect | Value |
|--------|-------|
| Location | `~remo/.ssh/authorized_keys` on the instance |
| Shape | One line: `ssh-ed25519 <key> remo-web@<deployment_id>` |
| Idempotence marker | The ` remo-web@` comment prefix — install filters existing marker lines then appends (research R7) |
| Revocation | Operator deletes the marker line (SC-008) |

## SavedAdoptionCredentials

Workstation-side, single default deployment (clarification Q4).

| Field | Type | Notes |
|-------|------|-------|
| `url` | str | Service base URL as adopted |
| `token` | str | The setup API token |
| `deployment_id` | str | Service identity at adopt time; push compares and aborts with re-adopt guidance on mismatch (research R10) |

**Storage**: `~/.config/remo/web-service.json`, 0600, written only with
explicit consent (FR-025). Absent/rejected → prompt or fail with guidance
(FR-027).

## VerificationReport

JSON rendering of the existing `web/check.py:CheckResult` list.

| Field | Type | Notes |
|-------|------|-------|
| `name` | str | e.g. `registry`, `ssh_identity`, `instance incus/host/dev1` |
| `passed` | bool | |
| `detail` | str | Redaction rules of `check.py` apply unchanged |
| `remediation` | str \| null | |

The CLI renders these per-instance, adding the workstation-vs-service
reachability distinction (FR-014): an instance the CLI just reached that
fails the service-side check is annotated "reachable from workstation but
not from the service".

## AdoptionRunOutcome (CLI-internal)

Per-instance result accumulated by adopt/push for the final summary
(FR-013): one of `adopted`, `skipped_unreachable`, `skipped_by_design`
(SSM), `skipped_no_trust` (non-interactive, no trusted record),
`security_flagged` (host-key mismatch, FR-010) — plus the verification
outcome once the server-side pass runs.
