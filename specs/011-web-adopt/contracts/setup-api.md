# Contract: Setup REST API (`/api/v1/setup/*`)

**Feature**: 011-web-adopt | Consumed by `remo web adopt` / `remo web push`.
Payload field semantics: [../data-model.md](../data-model.md).

## Authentication (all routes)

`Authorization: Bearer <REMO_WEB_API_TOKEN>` — constant-time comparison.

| Condition | Response |
|-----------|----------|
| Token configured, header correct | route handles request |
| Token configured, header missing/wrong | `401 {"detail": "unauthorized"}` (no further detail; attempt logged without the credential) |
| Token NOT configured | `404` on every setup route — surface disabled, indistinguishable from absent (fail closed) |

Existing global middleware (Host allowlist, origin rules, redaction) applies
unchanged, with one scoped exception: Origin-less state-changing requests to
`/api/v1/setup/*` bypass the browser-CSRF origin allowlist (the surface is
bearer-token-only — no ambient credentials — and a cross-origin browser
request cannot attach an Authorization header; a genuine browser CSRF
attempt always carries an Origin header, which is still enforced). This is
what lets the Origin-less CLI client — including `--via` tunnels whose
`127.0.0.1:<random-port>` origin could never be allowlisted — reach the
setup API.

## GET /api/v1/setup/status

Service mode + identity presence. Cheap; safe to poll.

**200** :

```json
{
  "state": "unconfigured | adopted | mount_configured | broken",
  "deployment_id": "a1b2c3d4",
  "public_key_available": true,
  "registry_instances": 7
}
```

`deployment_id`/`public_key_available` are `null`/`false` when no service
identity exists (mount-configured mode). `registry_instances` is `0` when no
registry.

## GET /api/v1/setup/identity

**200** :

```json
{
  "deployment_id": "a1b2c3d4",
  "public_key": "ssh-ed25519 AAAA... remo-web@a1b2c3d4"
}
```

**409** `{"reason": "mount_configured"}` — a mount-configured service has no
service identity to authorize (CLI explains: this deployment is configured
via mounts and cannot be adopted, FR-017).

## PUT /api/v1/setup/registry

Body: `AdoptionPayload` (data-model). Query: `allow_empty=true` opts out of
the empty-registry guard (FR-016).

| Response | Meaning |
|----------|---------|
| `200 {"applied": true, "registry_instances": N, "host_key_instances": M}` | Mirror applied atomically (registry + service known_hosts replaced) |
| `409 {"reason": "mount_configured"}` | Read-only configuration; nothing written (FR-017) |
| `422 {"reason": "empty_registry"}` | Empty `registry` without `allow_empty` |
| `422 {"reason": "invalid_payload", "detail": "..."}` | Version/reference/parse violation; nothing written (FR-019) |

Apply order: host-keys file first, registry last (research R5). Active
terminal sessions are never touched (clarification Q3).

## POST /api/v1/setup/verify

Runs the service's existing check pass (config checks + per-instance
`capabilities` round-trips). May take up to ~5 s per unreachable instance;
the CLI sets a generous timeout.

**200** :

```json
{
  "results": [
    {"name": "registry", "passed": true, "detail": "readable at ... (7 instances)", "remediation": null},
    {"name": "instance incus/pve1/dev1", "passed": false, "detail": "unreachable", "remediation": "Check instance is running / reachable."}
  ],
  "all_passed": false
}
```

## Ready-payload extension (existing endpoint, not under /setup)

`GET /api/v1/ready` gains `"status": "unconfigured"` as a **200** variant
(research R11): healthy-and-awaiting-adoption must not fail the compose
healthcheck. `broken` states keep today's 503 semantics. The SPA switches on
this field to render the awaiting-adoption page (FR-004).
