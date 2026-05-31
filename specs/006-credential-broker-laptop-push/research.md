# Phase 0 Research: Credential Broker (Sidecar Devcontainer Model)

**Date**: 2026-05-31  
**Status**: Complete — no `NEEDS CLARIFICATION` items remain from the technical context.

## Scope

This feature is mostly orchestration work in the `remo` repository, but it depends on the sibling `remo-broker` repository for the daemon-side wire contract. Research focused on three things:

1. Where this repo already provisions tools and launches devcontainers.
2. How to add sidecar-specific assets without fighting the current packaging model.
3. Which broker-side contracts are already defined in `/workspaces/remo-broker` and must be treated as source-of-truth dependencies.

## Decisions

### Decision: Provision the sidecar and broker through the existing provider configure flows

**Rationale:** All four providers already converge on shared Ansible-driven configuration flows (`ansible/*_site.yml`, `ansible/*_configure.yml`, and `ansible/tasks/configure_dev_tools.yml`). Adding sidecar/broker setup there preserves the existing `remo {provider} create` and `remo {provider} update` commands, keeps the feature idempotent, and matches the repository's architecture of thin Click entrypoints delegating to Ansible-backed provisioning.

**Alternatives considered:**  
- Ad-hoc post-create SSH bootstrap scripts: rejected because they bypass the repo's idempotent automation path.  
- New laptop CLI commands for sidecar bootstrap: rejected because spec NFR-006 explicitly keeps the laptop CLI unchanged.  
- Provider-specific bespoke setup flows: rejected because the broker/sidecar behavior is intentionally shared across providers.

### Decision: Ship sidecar, broker, and secrets-feature assets from `ansible/`, not Python package data

**Rationale:** `pyproject.toml` already force-includes the repository's `ansible/` tree inside the wheel. That makes Ansible roles/templates/files the lowest-friction place to stage sidecar compose/devcontainer definitions, helper scripts, systemd units, and project-side secrets feature files without adding a second asset-packaging mechanism.

**Alternatives considered:**  
- Add new package-data rules under `src/remo_cli/`: workable, but unnecessary packaging complexity for assets that are ultimately installed by Ansible.  
- Download assets from GitHub at runtime: rejected for reliability and offline reasons.  
- Generate all assets inline from Python strings: rejected because the repo already uses Ansible templates for remote scripts and shell helpers.

### Decision: Model `_remo-vault` as a reserved picker/launcher target, not a new top-level CLI primitive

**Rationale:** The local CLI already routes interactive project access through `remo shell`, remote `project-menu`, and `project-launch`. The sidecar can slot into that model as a reserved entry visible in the picker and addressable via `remo shell -p _remo-vault`, which satisfies the spec while preserving current user muscle memory and avoiding a new laptop-side command family.

**Alternatives considered:**  
- Add `remo vault shell` or similar: rejected because it expands the laptop CLI surface without functional necessity.  
- Treat the sidecar as a normal project repo under `~/projects`: rejected because `_remo-vault` is a managed service container, not user-owned project workspace content.  
- Hide the sidecar entirely from the picker: rejected because the spec explicitly calls for a visible reserved entry.

### Decision: Treat `.remo/manifest.toml` as the canonical project contract and align directly with broker v2 schema work

**Rationale:** The spec requires one manifest that controls both broker allowlisting and project-side fetch/render behavior. The sibling broker spec already acknowledges that the schema will grow to carry Remo-side directives like `fetch_as`, so the clean design is a single canonical manifest rather than a user-authored file plus a hidden translated broker-only file.

**Alternatives considered:**  
- Synthesize `.remo/broker.toml` from `.remo/manifest.toml`: rejected because dual manifests introduce drift and opaque failure modes.  
- Keep the old `.devcontainer/remo-broker.toml` path as canonical: rejected because the spec intentionally centralizes sensitive configuration under `.remo/`.  
- Make the broker parse a different file than the project-side helper: rejected because the allowlist and fetch contract should share one source of truth.

### Decision: Implement project startup as fail-closed with bounded retry in the secrets feature

**Rationale:** The clarification session established four critical runtime rules: missing required secrets retry for 15 seconds then fail startup, file-rendered secrets may be structured bundles, `push-creds` atomically replaces the broker snapshot, and successful pushes invalidate per-project caches immediately. The project-side feature should therefore be a thin client over broker truth, not a best-effort cache or fallback layer.

**Alternatives considered:**  
- Warn and continue when required secrets are missing: rejected because it leaks runtime misconfiguration into user tools and weakens the security model.  
- Merge pushes into existing broker state: rejected because stale secrets would survive after revocation.  
- Let old per-project cache entries expire naturally: rejected because revoked secrets would remain fetchable after a successful push.

### Decision: Keep contract artifacts in markdown, because this feature exposes CLI, TOML, and NDJSON surfaces rather than HTTP endpoints

**Rationale:** The repo's prior plan artifacts already use markdown contracts for non-HTTP command surfaces. This feature's user-visible contracts are the local CLI flow, the project manifest schema, and the broker admin/project socket NDJSON payloads, so markdown documents with explicit request/response examples are a better fit than forcing an artificial OpenAPI shape.

**Alternatives considered:**  
- OpenAPI: rejected because there is no HTTP API in this repository for the feature.  
- GraphQL schema: rejected for the same reason.  
- No contract docs: rejected because the cross-repo dependency and startup flow are the highest-risk parts of the implementation.

## Repo evidence

### Remo repo

- Provider create/update entrypoints already exist in `src/remo_cli/providers/{aws,hetzner,incus,proxmox}.py`.
- Shared remote-tool provisioning happens through `ansible/tasks/configure_dev_tools.yml`.
- Remote picker/launch behavior is owned by `ansible/roles/user_setup/templates/project-menu.sh.j2` and `project-launch.sh.j2`.
- Devcontainer startup is already centralized in `ansible/roles/user_setup/tasks/main.yml` and the installed `@devcontainers/cli` role.

### Sibling `remo-broker` repo

- `specs/002-laptop-push-secrets/spec.md` defines the v2 daemon contract: `push-creds`, `clear-creds`, `secret_count`, `secrets_loaded_at`, a 1 MiB push payload limit, and `AuditEvent::SecretsPushed`.
- `docs/wire-protocol.md` and `schema/remo-broker.v1.json` show the current v1 baseline this feature must supersede.
- `src/registry.rs` and `src/manifest.rs` confirm the broker already has strong manifest/reload primitives; this repo should integrate with those rather than inventing a parallel allowlist system.

## Constitution alignment

| Principle | Research conclusion |
|---|---|
| Defensive Variable Access | New provisioning work belongs in Ansible roles, so safe registered-variable access is a first-class implementation constraint. |
| Test All Conditional Paths | The highest-risk branches are startup failure handling, manifest reload, env vs. file rendering, and picker/launcher behavior. |
| Idempotent by Default | Existing provider configure flows are the right insertion point because they are already re-runnable. |
| Fail Fast with Clear Messages | Missing required secrets, invalid manifests, and missing sidecar/broker prerequisites should halt with actionable errors rather than best-effort fallbacks. |
| Documentation Reflects Reality | Because the laptop CLI stays stable while remote behavior changes substantially, quickstart and provider docs are mandatory deliverables. |

No unresolved research blockers remain for Phase 1 design.
