# Phase 1 Data Model: Credential Broker (Sidecar Devcontainer Model)

**Date**: 2026-05-31

## Entities

### VaultSidecar

Represents the remo-managed `_remo-vault` devcontainer that owns the instance-local credential source of truth.

| Field | Type | Description |
|---|---|---|
| `instance_name` | `str` | Remo instance identifier (`KnownHost.name`) that owns this sidecar. |
| `container_name` | `str` | Always `_remo-vault` (reserved name). |
| `admin_socket_path` | `str` | Broker admin socket path, typically `/run/remo-broker/admin.sock`. |
| `fnox_store_path` | `str` | Persistent encrypted store path inside the sidecar, `/var/lib/remo-vault/fnox.enc`. |
| `key_source_tier` | `Literal["tpm2", "host-key", "plaintext-0600"]` | Host-side systemd-credential tier used to decrypt the fnox store. |
| `helper_commands` | `list[str]` | Managed commands exposed in the sidecar (`remo-list-creds`, `remo-test-project`, `remo-vend-status`, `remo-reload`). |
| `state` | `Literal["provisioned", "running", "degraded"]` | Operational state for user messaging and quickstart verification. |

**Validation rules**
- `container_name` is reserved and user projects may not claim `_remo-vault`.
- `admin_socket_path` must exist and be group-accessible from inside the sidecar.
- `fnox_store_path` lives on a Docker volume and survives sidecar restart.

### BrokerSecretSnapshot

Represents the complete secrets snapshot pushed from the sidecar into `remo-broker`.

| Field | Type | Description |
|---|---|---|
| `loaded_at` | `datetime | None` | Timestamp from broker v2 `status` / `push-creds` response. |
| `secret_count` | `int` | Number of top-level secret names currently loaded in broker memory. |
| `secrets` | `dict[str, SecretPayload]` | Full pushed map; replaced atomically on each successful push. |
| `protocol_version` | `int` | Must be `2` for this feature. |

**State transitions**

```text
empty --push-creds--> loaded(snapshot N) --push-creds--> loaded(snapshot N+1)
  \                                           /
   ---------------- clear-creds -------------
```

**Validation rules**
- Each `push-creds` replaces the full snapshot, not a partial merge.
- Successful push invalidates all per-project broker cache entries immediately.
- Payload size must stay within the broker's v2 admin limit (1 MiB).

### SecretPayload

Represents one manifest-addressable secret value.

| Field | Type | Description |
|---|---|---|
| `name` | `str` | Canonical secret name used in the manifest (`gh`, `openai_api_key`, `aws`, etc.). |
| `shape` | `Literal["scalar", "bundle"]` | Scalar values vend directly; bundles render by field name in file templates. |
| `scalar_value` | `str | None` | Present when `shape == "scalar"`. |
| `bundle_fields` | `dict[str, str] | None` | Present when `shape == "bundle"`; contains template-addressable keys. |
| `updated_at` | `datetime` | Last sidecar-side refresh timestamp. |

**Validation rules**
- Names must be unique within a snapshot.
- Bundle field names must be unique and ASCII-safe for placeholder substitution.
- Values are never logged or written to project-disk persistence by the feature itself.

### ProjectManifest

Canonical manifest stored at `~/projects/<project>/.remo/manifest.toml`.

| Field | Type | Description |
|---|---|---|
| `schema_version` | `int` | Versioned manifest schema; current value `1`. |
| `project` | `str` | Must match parent directory basename. |
| `secrets` | `dict[str, SecretBinding]` | Per-secret fetch/render declarations. |
| `cache` | `CacheSettings` | Optional broker cache overrides carried from broker schema. |

**Relationships**
- One `ProjectManifest` belongs to one project directory.
- One manifest references zero or more `SecretPayload.name` values.
- The broker uses the manifest for allowlisting; `remo-fetch-secrets` uses the same manifest for render mode.

**Validation rules**
- Manifest file is bind-mounted read-only into the project devcontainer.
- Updates happen outside the project container, followed by broker `reload`.
- The project name must match the directory basename to prevent identity spoofing.

### SecretBinding

Defines how one named secret is fetched into a project devcontainer.

| Field | Type | Description |
|---|---|---|
| `secret_name` | `str` | Manifest table key and broker lookup name. |
| `fetch_as` | `Literal["env", "file"]` | Injection mode. Defaults to `env`. |
| `env_var` | `str | None` | Explicit env-var name for `env` mode; defaults to uppercase secret name. |
| `file_path` | `str | None` | Target path for `file` mode. |
| `file_mode` | `str | None` | Octal string such as `"0600"` for `file` mode. |
| `template` | `str | None` | Required for `file` mode; placeholders resolve against scalar value or bundle fields. |

**Validation rules**
- `env` mode forbids file-only fields.
- `file` mode requires `file_path`, `file_mode`, and `template`.
- File-rendered bundles resolve placeholders by exact bundle field name.
- Required secrets unavailable at startup trigger bounded retry for 15 seconds, then fail the container entrypoint.

### CacheSettings

Per-project cache caps passed through to the broker.

| Field | Type | Description |
|---|---|---|
| `default_ttl_seconds` | `int` | Default 900 seconds. |
| `default_max_entries` | `int` | Default 50 entries. |

**Validation rules**
- Projects may lower cache limits but not exceed broker-wide defaults.
- Cache contents are invalidated immediately after successful `push-creds`.

## Relationships

```text
VaultSidecar (1) ----pushes----> BrokerSecretSnapshot (1 current per instance)
     |                                     |
     | stores                              | serves
     v                                     v
 SecretPayload (*) <---- referenced by ---- ProjectManifest (1 per project)
                                             |
                                             +---- contains ----> SecretBinding (*)
                                             |
                                             +---- configures ---> CacheSettings (0..1)
```

## Runtime flow model

```text
1. Provider create/update provisions broker + sidecar.
2. User authenticates or stores values inside `_remo-vault`.
3. Sidecar watcher observes fnox change and sends `push-creds`.
4. Broker atomically swaps the in-memory snapshot and clears project caches.
5. User updates `.remo/manifest.toml` outside the project devcontainer.
6. `remo-reload <project>` triggers broker manifest reload.
7. Project devcontainer startup runs `remo-fetch-secrets`.
8. Required secrets are fetched and rendered as env vars or tmpfs files.
9. If any required secret stays unavailable for 15 seconds, startup exits non-zero.
```

## Implementation notes

- No new local persistence is added to `KnownHost`; instance identity stays in the existing host registry.
- The broker remains the source of truth for project allowlist enforcement; the sidecar remains the source of truth for secret values.
- The project devcontainer only ever receives the rendered values it asked for at startup; it never gains access to the sidecar's storage volume or credential-management helpers.
