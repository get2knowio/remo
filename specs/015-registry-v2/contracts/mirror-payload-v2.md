# Contract: Adoption Mirror Payload v2 & Version Negotiation

Governs the workstation→service registry mirror (push) after Registry v2. Existing endpoint semantics (pairing-code gating, dormant-404, validate-all-before-write, atomic ordered apply) are unchanged; this contract changes the payload schema and adds version negotiation.

## 1. `GET /api/v1/setup/status` — capability advertisement

Response gains one field:

```json
{
  "state": "unconfigured | adopted | mount_configured | broken",
  "deployment_id": "…",
  "public_key_available": true,
  "registry_instances": 5,
  "payload_versions": [1, 2]
}
```

- `payload_versions`: payload versions this service accepts on `PUT /setup/registry`.
- **Workstation rule (FR-021)**: read `payload_versions` before pushing; absence of the field means `[1]` (pre-v2 service). If the workstation's payload version (2) is not listed, ABORT before any instance processing or PUT, with: *"this remo-web deployment only accepts registry payload v1 — upgrade the remo-web container image, then re-run the push."* No mutation of any kind (instances are not keyscanned/authorized either — fail truly fast).

## 2. `PUT /api/v1/setup/registry` — payload schemas

### v2 payload (new canonical; what an upgraded CLI sends)

```json
{
  "version": 2,
  "registry": [ { …hostEntry per registry-file-v2.md… } ],
  "host_keys": { "<name>": ["<known_hosts line>", …] }
}
```

- `registry` entries use the exact `hostEntry` schema from [registry-file-v2.md](registry-file-v2.md) — no overloaded fields on the wire (FR-020).
- `host_keys` semantics unchanged: keys must reference registry entries; entries with `access: "ssm"` MUST NOT have host keys (both ends enforce, as today).
- Validation is all-or-nothing before any write, as today. The colon/newline field checks that existed to protect the legacy line format are replaced by the v2 validation rules (V2–V6); the newline/control-character ban stays.

### v1 payload (accepted for backward compatibility, FR-022)

```json
{ "version": 1, "registry": [{ "type": "...", "name": "...", "host": "...", "user": "...", "instance_id": "", "access_mode": "", "region": "" }], "host_keys": { … } }
```

- An upgraded service MUST continue accepting v1 payloads from a not-yet-upgraded workstation, mapping entries through the same legacy→v2 mapper used by file migration (data-model §4), then storing v2.
- SSM classification of v1 entries follows the legacy implicit rule, exactly as the old service computed it.

### Unknown versions

- `version` not in `payload_versions` → `400` with `{"error": {"code": "unsupported_payload_version", "supported": [1, 2], …}}`. No partial application; the service's existing mirror stays intact and served (FR-021).

## 3. Apply sequence (service side)

Ordered, each step atomic (`os.replace`), crash-convergent (a crash mid-sequence leaves a readable superset; re-push converges):

1. Write service trust file `web-identity/known_hosts` (unchanged from today).
2. Write `registry.json` (v2) via `core.registry.replace_registry`.
3. Remove any legacy `known_hosts` mirror file left from a pre-upgrade push (service-owned replaceable state — not user data; removal keeps the service out of the both-files-present state permanently).

The service stores v2 regardless of received payload version. Empty registry still requires `?allow_empty=true`.

## 4. Compatibility matrix (FR-021/FR-022, SC-006)

| Workstation | Service | Outcome |
|-------------|---------|---------|
| v2 (new) | new (`payload_versions: [1,2]`) | v2 push, normal flow |
| v2 (new) | old (no `payload_versions`) | abort before any mutation, remediation names the service as the side to upgrade |
| v1 (old) | new | v1 payload accepted, mapped, stored as v2; old CLI needs no changes |
| v1 (old) | old | unchanged legacy behavior (out of this feature's scope) |
| any | any, mount_configured | 409 `mount_configured`, unchanged from today |

## 5. Push delta cache (workstation side)

`~/.config/remo/web-service.json`:

- Adds `"cache_version": 2`; loaders treat any other/missing value as an empty cache (one-time full re-verification push after upgrade — FR-026, clarification #4).
- `fingerprint` = SHA-256 over the canonical sorted-key JSON of the entry's v2 `hostEntry` object (replaces the legacy 7-field digest).
- Unchanged: keyed by `deployment_id`, non-secret, 0600, atomic writes, cached `host_keys` re-sent on every push because the PUT is wholesale.
