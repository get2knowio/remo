# Feature Specification: CLI-to-Web Adoption

**Feature Branch**: `011-web-adopt`

**Created**: 2026-07-16

**Status**: Draft

**Input**: User description: "Add a 'CLI-to-web adoption' flow that lets a user with a working, already-configured remo CLI hand off their configuration to a freshly deployed remo-web container — without ever copying their personal SSH private key."

## Overview

Today the remo-web service can only run against read-only copies of the user's
workstation configuration (registry and personal SSH private key), which
effectively requires the container to run on the same machine as the CLI. The
real deployment target is a container on a separate home-lab host — plain
Docker Compose, or deployed as a hola app behind a TLS-terminating reverse
proxy. This feature lets a user whose workstation CLI already manages a set of
bootstrapped, reachable remo hosts hand that configuration off to a freshly
deployed remo-web service with a single command — and the user's personal SSH
private key never leaves the workstation. Instead, the service is born with
its own service-scoped identity, and the workstation CLI (which already has
working access to every host) authorizes that identity everywhere it needs to
go.

Host bootstrapping from the web, browser-based setup wizards, and provider
credential management are explicitly out of scope (see Out of Scope).

## Clarifications

### Session 2026-07-16

- Q: When the workstation pushes its registry, does the service's registry
  become an exact mirror (removals propagate) or an additive merge? → A:
  Exact mirror — the workstation is the source of truth; entries absent
  locally are removed from the service's registry. The push does NOT
  automatically de-authorize the service identity on removed instances
  (revocation remains the manual per-instance action of SC-008).
- Q: What happens when the workstation has no trusted host-key record for a
  registered instance? → A: The CLI prompts the user to confirm the scanned
  fingerprint interactively (the human decides; the service still never makes
  a trust decision). In non-interactive contexts the instance is skipped and
  reported.
- Q: Do adoption/push operations affect terminal sessions already open in the
  browser? → A: No — active sessions continue; pushed configuration applies
  to discovery and newly created sessions only.
- Q: Do saved adoption credentials support multiple remo-web deployments? →
  A: v1 stores a single default deployment; multiple named deployments are
  out of scope.
- Q: Is there an operator-initiated service key rotation in v1? → A: No
  dedicated rotation command; rotation is performed by resetting the service
  state (producing a new identity) and re-running adoption, which replaces
  the authorization entries. Documented procedure, not a command.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - First-time adoption from the workstation CLI (Priority: P1)

A user has a working remo CLI on their workstation that manages N direct-access
instances, and has just deployed a fresh remo-web container on another machine.
They run a single `remo web adopt` command on the workstation, providing the
service URL and API token (interactively or via `REMO_API_URL` /
`REMO_API_TOKEN` environment variables). The command retrieves the service's
public identity, pushes the workstation's registry and the verified SSH host
keys for each direct-access instance, authorizes the service's identity on
every one of those instances using the user's existing working SSH access, and
finishes by triggering a server-side verification pass whose per-instance
PASS/FAIL report is rendered in the terminal. The user opens the browser and
sees all their instances with working terminals.

**Why this priority**: This is the headline capability — the single-command
handoff is the entire reason the feature exists. Without it, deploying
remo-web on a separate machine requires manually copying files (including a
private key) into the container.

**Independent Test**: Starting from a fresh service deployment (User Story 2
state) and a workstation CLI managing at least two reachable instances, run
the adopt command and verify the web dashboard shows all instances with
working terminals, and that no private key material was transferred from the
workstation.

**Acceptance Scenarios**:

1. **Given** a fresh unconfigured remo-web service and a workstation CLI with
   N reachable direct-access instances registered, **When** the user runs the
   adopt command with a valid URL and token, **Then** the service ends up with
   the full registry, verified host keys for each direct-access instance, and
   its own identity authorized on each instance, and the terminal shows a
   per-instance PASS/FAIL verification report.
2. **Given** an adoption in progress, **When** one registered instance is
   powered off or unreachable, **Then** that instance is reported as skipped
   with a clear reason, the remaining instances are processed normally, and
   the command exits successfully with the partial result clearly summarized.
3. **Given** a registry containing SSM-routed (non-direct-access) instances,
   **When** the user runs adopt, **Then** those instances are reported as
   "skipped by design" (not failed), with a pointer to the documented
   credential-mount path for SSM targets.
4. **Given** a previously adopted service, **When** the user re-runs the adopt
   command, **Then** the end state is identical (no duplicate authorization
   entries on any instance, no duplicate registry or host-key entries) and the
   command succeeds.
5. **Given** an instance whose scanned host key does not match the
   workstation's own trusted record for that host, **When** adopt processes
   that instance, **Then** no host key is pushed for it, the mismatch is
   prominently reported as a potential security issue, and the rest of the
   adoption continues.
6. **Given** a service URL that is not directly reachable from the
   workstation, **When** the user runs adopt with the tunnel fallback option
   (e.g. `remo web adopt --via <host>`), **Then** the adoption completes over
   an SSH tunnel using the user's existing SSH access to the deployment host.

---

### User Story 2 - Fresh service boots into a clear "awaiting adoption" state (Priority: P2)

An operator deploys the remo-web container on a home-lab host with a writable
state volume and an API token, but no registry and no SSH key. Instead of
failing or crash-looping, the service starts successfully in an
"unconfigured" state: it generates its own service-scoped SSH keypair in the
state volume, and both the readiness endpoint and the browser UI clearly
communicate that the service is healthy but awaiting adoption — distinguishing
"unconfigured" (expected, actionable) from "broken" (mounts present but
unreadable, missing runtime prerequisites, etc.).

**Why this priority**: It is the foundation the adopt command lands on, and it
has standalone value: today a configless container is indistinguishable from a
broken one. Operators deploying via compose or hola get an immediate, honest
signal about what state the service is in and what to do next.

**Independent Test**: Deploy the container with a writable state volume and no
registry/key mounts; verify it starts, stays up, reports "awaiting adoption"
via the readiness endpoint and the browser page, and has generated a service
keypair in the state volume. Separately verify an existing read-only
bind-mount deployment still behaves exactly as today.

**Acceptance Scenarios**:

1. **Given** a container started with a writable state volume and no registry
   or key material, **When** it boots, **Then** it reaches a running state
   without crashing, generates a service-scoped keypair in the state volume,
   and reports an "unconfigured / awaiting adoption" status via the readiness
   endpoint.
2. **Given** an unconfigured service, **When** a user opens the web UI,
   **Then** they see an "awaiting adoption" page explaining that the service
   is healthy and how to adopt it from a workstation CLI (no host list, no
   terminals).
3. **Given** a deployment using today's read-only bind mounts of an existing
   registry and key, **When** the container boots, **Then** behavior is
   unchanged from the current release: the service is immediately
   "configured" and the dashboard works as today.
4. **Given** an unconfigured service that already generated its keypair,
   **When** the container restarts, **Then** the same keypair is reused (no
   regeneration), so identities authorized on instances remain valid.
5. **Given** a service whose configuration is present but unusable (e.g.
   mounted registry unreadable), **When** the readiness endpoint is queried,
   **Then** the reported state is distinguishable from "unconfigured" and
   includes actionable detail, as today.

---

### User Story 3 - Token-gated setup surface (Priority: P3)

An operator supplies a persistent admin API token at deploy time (prompted for
or generated during hola app installation, or set in the compose file). Every
adoption/setup operation the service exposes requires this token. When no
token is configured, the setup surface is disabled entirely — the service
fails closed, never open.

**Why this priority**: The setup surface can rewrite the service's registry
and trusted host keys, so it must never be reachable without authentication.
It ships in the same release as User Story 1 (the adopt command cannot be
exposed without it), but is independently testable and reviewable.

**Independent Test**: Deploy the service with a token and verify setup
requests succeed only with the exact token; redeploy without a token and
verify every setup request is rejected while the rest of the service
(dashboard, terminals, health) is unaffected.

**Acceptance Scenarios**:

1. **Given** a service deployed with an API token, **When** a setup request
   arrives with the correct token, **Then** it is accepted.
2. **Given** a service deployed with an API token, **When** a setup request
   arrives with a missing or incorrect token, **Then** it is rejected with no
   information disclosed beyond the authentication failure, and the attempt is
   observable in service logs without revealing the presented credential.
3. **Given** a service deployed with no API token configured, **When** any
   setup request arrives, **Then** it is rejected — the setup surface behaves
   as if it does not exist.
4. **Given** any service logs produced during adoption, **When** they are
   inspected, **Then** neither the API token nor any credential material
   appears in them (consistent with the existing redaction guarantees).

---

### User Story 4 - Ongoing push after local changes (Priority: P4)

After adopting once, the user registers a new instance locally (e.g. via a
provider sync command). They run a lightweight zero-argument push command that
updates the service's registry, pushes host keys for any new direct-access
instances, and authorizes the service's existing identity on those new
instances — using the service URL and token saved (with the user's consent,
in a user-readable-only file under the CLI's configuration directory) during
the initial adoption.

**Why this priority**: This is the recurring everyday value — it turns the
adoption machinery into the standing bridge between the CLI (where instances
are created and synced) and the web dashboard. It layers cleanly on top of
User Story 1.

**Independent Test**: After a successful adoption, register one additional
instance locally, run the push command with no arguments, and verify the new
instance appears in the web dashboard with a working terminal while existing
instances are untouched.

**Acceptance Scenarios**:

1. **Given** a completed adoption where the user consented to saving
   credentials, **When** the user runs the push command with no arguments,
   **Then** the saved URL and token are used without prompting.
2. **Given** a new locally registered direct-access instance, **When** the
   user pushes, **Then** the service's registry gains the instance, its host
   key is pushed after verification against the workstation's trusted record,
   and the service's existing identity is authorized on it — without touching
   or re-authorizing unchanged instances.
3. **Given** saved credentials whose token has since been rotated on the
   service, **When** the user pushes, **Then** the command fails with a clear
   authentication error explaining how to re-run adoption with the new token.
4. **Given** a user who declined to save credentials during adoption, **When**
   they run the push command, **Then** they are prompted for URL and token
   exactly as in first-time adoption.

---

### Edge Cases

- **Adopting a mount-configured service**: if the service is running in the
  read-only bind-mount mode, adoption pushes cannot apply (the configuration
  is not writable). The service must reject the attempt with a message
  explaining that this deployment is configured via mounts, and the CLI must
  render that clearly.
- **State volume lost / service redeployed**: the service generates a new
  identity; re-running adopt authorizes the new identity. Authorization
  entries the flow installs on instances must be written in a recognizable,
  replaceable form so that re-adoption replaces a stale entry rather than
  accumulating orphans. This state-reset-plus-re-adoption sequence is also
  the documented key-rotation procedure (no dedicated rotation command in
  v1).
- **Instance removed from the workstation registry**: a subsequent push
  removes it from the service's registry (mirror semantics), so it stops
  appearing in discovery and no new sessions can target it; the service's
  key remains authorized on the instance until manually removed (SC-008).
- **Empty workstation registry**: adopt must refuse to push an empty registry
  by default (guarding against wiping a previously adopted service from the
  wrong machine) with an explicit override for the intentional case.
- **Workstation registry contains instances the container cannot reach**
  (e.g. reachable only via workstation-specific SSH client config such as
  ProxyJump): the verification report must surface "reachable from
  workstation, unreachable from service" as a distinct, explained outcome.
- **Interrupted adoption** (network drop, Ctrl-C mid-run): a subsequent
  re-run must converge to the correct end state; partially applied changes
  must not require manual cleanup.
- **Concurrent adoptions from two workstations**: last completed push wins;
  the flow does not need merge semantics, but must not corrupt the service's
  stored configuration (each push applies atomically).
- **Host key scan timeout** for one instance: treated like an unreachable
  instance — reported and skipped, never fatal to the run.
- **Very large registries**: adoption processes instances independently, so
  one slow instance delays only itself; the overall run must apply a bounded
  per-instance time budget.

## Requirements *(mandatory)*

### Functional Requirements

#### Service configuration states (User Story 2)

- **FR-001**: The service MUST support starting with a writable state
  directory containing no registry and no key material, reaching a running
  "unconfigured" state rather than failing.
- **FR-002**: On first start in the unconfigured state, the service MUST
  generate a service-scoped SSH keypair in its writable state directory, and
  MUST reuse (never silently regenerate) that keypair on subsequent starts.
- **FR-003**: The service's readiness reporting MUST distinguish at minimum:
  "unconfigured (awaiting adoption)", "configured and ready", and "broken
  (configuration present but unusable)" — each with actionable detail.
- **FR-004**: The web UI MUST present an "awaiting adoption" page when the
  service is unconfigured, explaining how to complete adoption from a
  workstation CLI; no instance data or terminal access is available in this
  state.
- **FR-005**: The existing read-only bind-mount deployment mode MUST continue
  to work unchanged; when usable mounted configuration is present, the service
  behaves exactly as it does today and its setup surface reports that the
  deployment is mount-configured (see FR-017).

#### Adoption flow (User Story 1)

- **FR-006**: The CLI MUST provide an adoption command (`remo web adopt`) that
  accepts the service URL and API token interactively, via command options, or
  via the `REMO_API_URL` / `REMO_API_TOKEN` environment variables.
- **FR-007**: The adoption flow MUST retrieve the service's public key and
  MUST NOT transfer any private key material in either direction at any point.
- **FR-008**: The adoption flow MUST push the workstation's registry contents
  to the service as an exact replacement (mirror): after a successful push,
  the service's registry matches the workstation's, including removals.
  Removal from the registry MUST NOT automatically de-authorize the service
  identity on the removed instance (revocation remains the manual
  per-instance action of SC-008, covered by documentation per FR-028).
- **FR-009**: For each registered direct-access instance, the adoption flow
  MUST obtain the instance's current SSH host key and verify it against the
  workstation's own trusted host-key records before pushing it to the
  service; the service itself MUST never make a trust-on-first-use decision.
  When the workstation has no trusted record for an instance, the CLI MUST
  prompt the user to confirm the scanned fingerprint interactively before
  pushing it; in non-interactive contexts such instances MUST be skipped and
  reported.
- **FR-010**: If an instance's scanned host key does not match the
  workstation's trusted record, the adoption flow MUST NOT push a host key for
  that instance and MUST prominently flag the mismatch as a potential security
  issue while continuing with remaining instances.
- **FR-011**: For each registered direct-access instance, the adoption flow
  MUST authorize the service's public key on the instance using the user's
  existing SSH access, in an idempotent, recognizable, replaceable form (no
  duplicates on re-run; stale entries from a previous service identity are
  replaced).
- **FR-012**: SSM-routed instances MUST be excluded from host-key push and key
  authorization, and reported as "skipped by design" with a pointer to the
  documented credential-mount path for such targets.
- **FR-013**: Unreachable or timed-out instances MUST be reported and skipped
  without failing the overall adoption; the final summary MUST clearly list
  per-instance outcomes (adopted / skipped-unreachable / skipped-by-design /
  security-flagged).
- **FR-014**: The adoption flow MUST conclude with a server-side verification
  pass (reusing the service's existing per-instance check capability) and
  render the per-instance PASS/FAIL report in the terminal, including the
  distinct outcome "reachable from workstation but not from the service".
- **FR-015**: The adoption command MUST be idempotent: re-running it against
  the same workstation state and service MUST converge to the same end state
  with no duplicate entries anywhere.
- **FR-016**: The adoption command MUST refuse to push an empty registry
  unless the user explicitly overrides, to guard against accidentally wiping
  an adopted service from the wrong workstation.
- **FR-017**: When the target service is mount-configured (read-only
  configuration), the service MUST reject adoption pushes with a
  machine-readable reason and the CLI MUST explain that this deployment is
  configured via mounts and cannot be adopted.
- **FR-018**: The adoption command MUST offer a fallback for services not
  directly reachable from the workstation, tunneling the adoption over the
  user's existing SSH access to the deployment host (e.g.
  `remo web adopt --via <host>`).
- **FR-019**: Each push MUST apply atomically on the service: a failed or
  interrupted transfer MUST NOT leave the service with partially updated
  configuration. Applying a push MUST NOT disrupt terminal sessions already
  established in the browser; pushed configuration takes effect for
  discovery and newly created sessions only.

#### Setup surface security (User Story 3)

- **FR-020**: All adoption/setup operations exposed by the service MUST
  require a persistent admin API token supplied to the service at deploy time
  via environment variable (`REMO_WEB_API_TOKEN`, following the service's
  existing settings naming convention).
- **FR-021**: When no API token is configured, the setup surface MUST be
  disabled entirely (fail closed); all other service functionality is
  unaffected.
- **FR-022**: Token verification MUST use a constant-time comparison, and the
  token (and any credential material handled during adoption) MUST be covered
  by the service's existing log-redaction guarantees.
- **FR-023**: The existing security posture MUST be preserved unchanged:
  host/origin allowlisting, no wildcard trust, and single-use terminal
  connection tokens continue to apply exactly as today.
- **FR-024**: Failed authentication attempts against the setup surface MUST be
  observable in service logs without revealing the presented credential.

#### Ongoing push (User Story 4)

- **FR-025**: After a successful adoption, the CLI MUST offer to save the
  service URL and token to a user-readable-only file under the CLI's existing
  configuration directory; saving requires explicit user consent. v1 stores a
  single default deployment (one saved entry); multiple named deployments are
  out of scope.
- **FR-026**: The CLI MUST provide a lightweight push command that, using
  saved credentials, updates the service's registry, pushes verified host keys
  for newly added direct-access instances, and authorizes the service's
  existing identity on those new instances — without re-processing unchanged
  instances.
- **FR-027**: When saved credentials are missing or rejected by the service,
  the push command MUST fail with a clear message explaining how to
  re-authenticate or re-run adoption.

#### Documentation

- **FR-028**: Deployment documentation MUST be updated to cover: the writable
  state volume, deploy-time token configuration (compose and hola), the
  adoption workflow, the SSM/AWS credential-mount path remaining as-is,
  service key rotation via state reset + re-adoption, and manual
  de-authorization of the service identity on instances removed from the
  registry.
- **FR-029**: Documentation MUST state that when the service is deployed
  behind a reverse proxy without SSO, the browser surface still requires
  proxy-level protection, and that a future forward-auth deployment will need
  an authentication bypass scoped to the setup surface — acceptable because
  the service itself enforces the token there. (Documentation only; no
  proxy/SSO implementation in this feature.)

### Key Entities

- **Service identity**: the service-scoped SSH keypair generated by an
  unconfigured service in its writable state; its public half is what gets
  authorized on instances. Survives restarts; replaced only when the state
  volume is lost or the operator forces regeneration.
- **Configuration state**: the service's self-knowledge of which mode it is
  in — unconfigured (awaiting adoption), configured via adoption (writable
  state), configured via mounts (read-only), or broken — surfaced through
  readiness reporting and the UI.
- **Setup API token**: the persistent deploy-time secret gating all setup
  operations; supplied by the operator (compose or hola app settings),
  rotated by redeploying with a new value.
- **Adoption payload**: the registry contents plus per-instance verified host
  keys pushed from workstation to service; contains no private keys and no
  provider credentials.
- **Instance authorization entry**: the recognizable, replaceable record on
  each instance that authorizes the service identity; written idempotently by
  adopt/push.
- **Saved adoption credentials**: the service URL and token optionally stored
  on the workstation (user-readable-only) for zero-argument pushes.
- **Verification report**: per-instance PASS/FAIL outcomes from the service's
  post-adoption self-check, rendered in the CLI.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Starting from a fresh container (compose or hola) and a
  workstation CLI managing N reachable direct-access instances, a single
  adoption command results in the web dashboard showing all N instances with
  working browser terminals — with zero manual file copying into the
  container and the user's personal private key never leaving the
  workstation.
- **SC-002**: For a registry of 10 reachable instances, the adoption command
  completes (including verification) in under 2 minutes, excluding time spent
  in interactive prompts.
- **SC-003**: Re-running the adoption command immediately after a successful
  run completes successfully and produces zero changes to instances or the
  service (verifiable by comparing authorization entries, registry, and
  host-key material before and after).
- **SC-004**: With no API token configured, 100% of setup requests are
  rejected; with a token configured, 100% of setup requests presenting a
  wrong or missing token are rejected.
- **SC-005**: Existing read-only bind-mount deployments upgrade to this
  release with zero behavior or configuration changes (all existing checks
  and workflows pass unchanged).
- **SC-006**: A fresh unconfigured container reaches its "awaiting adoption"
  state within 30 seconds of start and remains stable (no restarts) for at
  least 24 hours without configuration.
- **SC-007**: After registering one new instance locally, a single
  zero-argument push makes it available in the web dashboard with a working
  terminal in under 60 seconds.
- **SC-008**: An operator can revoke the service's access to any single
  instance by removing one clearly identifiable authorization entry on that
  instance, without affecting the workstation's own access.

## Assumptions

- All remo hosts/instances are already bootstrapped with current remo host
  tooling and reachable from the workstation CLI; host bootstrapping from the
  web is out of scope.
- Direct-access instances reachable from the workstation are, in general,
  also reachable from the network the service container runs on; asymmetric
  reachability is surfaced by verification (FR-014) rather than solved by
  this feature.
- The service keypair is passphrase-less in v1 (no key agent inside the
  container); protection comes from container/volume isolation and the
  service's existing hardening posture.
- The deployment transport for adoption is either TLS (reverse-proxy
  deployment, e.g. hola behind Traefik) or a trusted home LAN / SSH tunnel
  (plain compose); the feature does not implement its own transport
  encryption.
- A single-administrator model: one workstation is the source of truth at any
  given time; concurrent pushes resolve as last-write-wins with atomic
  application (no merge semantics).
- Provider credentials (cloud API tokens, cloud CLI credentials) are never
  pushed to or stored by the service; SSM-routed targets continue to use the
  existing documented credential-mount path.
- The operator deploying the container can supply environment variables and a
  writable volume (true for both compose and hola app deployments).
- The hola app manifest work itself (prompting for/generating the token at
  install time) happens in the hola apps repository; this feature only needs
  to expose the deploy-time settings that manifest will set.

## Out of Scope

- Browser-based setup wizard (the browser's only role in this feature is the
  "awaiting adoption" page and the normal dashboard afterward).
- Bootstrapping or configuring new remo hosts from the web.
- Pushing or storing provider credentials (cloud API tokens/credentials) on
  the service; key authorization and host-key push for SSM-routed instances.
- Passphrase-protected service keys / key agent support inside the container.
- Reverse-proxy SSO / forward-auth integration (documented as a deployment
  consideration only, per FR-029).
- Multi-administrator merge semantics for concurrent adoption.
- Multiple named remo-web deployments per workstation (v1 saves a single
  default deployment's credentials).
- A dedicated service key rotation command (rotation is the documented
  state-reset + re-adoption procedure).
- Automatic de-authorization of the service identity on instances removed
  from the registry (manual, per SC-008).
