# Feature Specification: Register an SSH-Reachable Host (`remo add`)

**Feature Branch**: `014-register-ssh-host`
**Created**: 2026-07-22
**Status**: Draft
**Input**: User description: "Let a user register a single environment they already have SSH access to, without needing hypervisor/API access. Decouple *connecting to* an environment from *owning the infrastructure that hosts it*. A provider-neutral `remo add`."

## Problem & Motivation

Today every path into the registry is provider-specific and requires privileged
access to the *infrastructure*, not just the environment:

- **Incus / Proxmox** `sync` needs SSH access to the **host/node** (often root)
  to run `incus list` / `pct list`.
- **AWS** `sync` needs cloud credentials; **Hetzner** `sync` needs an API token.

But the thing a user actually connects to is just an SSH endpoint: `remo shell`
and `remo cp` ultimately resolve a registry entry to `user@host` and run SSH
(the connection path already dispatches generically on `access_mode`, using
`direct` SSH for everything that is not AWS SSM). There is currently no way to
say "I have SSH access to this box — register it so I can `remo shell` into it,"
without going through a hypervisor or cloud API you may not control.

Concretely: a developer has SSH access to a Proxmox LXC container but does not
have (or does not want to track down) root SSH to the Proxmox node or an API
key. They cannot register that one container. They must obtain host-level access
purely to import a single environment they can already reach.

This feature adds a provider-neutral `remo add` that registers one
SSH-reachable environment directly into the registry, requiring only SSH
reachability. It establishes a clean division of the mental model:

- **`sync`** = *bulk discovery*, provider-specific, needs provider/host access.
- **`add`** = *single manual registration*, provider-agnostic, needs only SSH
  reachability.

(The error message in `resolve_remo_host_by_name` already tells users to
"Use 'remo add' to register an environment" — this feature makes that real.)

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Register a host I can already SSH to (Priority: P1)

A developer has SSH access to a remote environment (a Proxmox LXC container, a
VM, a bare-metal box, a container on someone else's Incus host) but no
hypervisor or cloud API access to it. They run a single `remo add` command
giving it a friendly name and an SSH target, and from then on `remo shell
<name>` and `remo cp` work exactly as they do for any other registered
environment.

**Why this priority**: This is the entire point of the feature — decoupling
"connect to an environment" from "own the infrastructure." Without it, a user
who can reach a box over SSH still cannot use `remo` with it.

**Independent Test**: On a machine you have SSH access to (but no hypervisor
access), run `remo add <name> <target>`, then `remo shell <name>`, and confirm
you land in a shell on that machine.

**Acceptance Scenarios**:

1. **Given** an SSH-reachable host and no prior registry entry for it, **When**
   the user runs `remo add <name> <target>` where target is `[user@]host[:port]`,
   **Then** a registry entry is created and a success message confirms it,
   naming how to connect (`remo shell <name>`).
2. **Given** a registered added host, **When** the user runs `remo shell <name>`,
   **Then** `remo` opens an SSH session to the recorded `user@host` (on the
   recorded port), using the same connection machinery as other `direct` hosts.
3. **Given** a registered added host, **When** the user runs `remo cp` against
   it, **Then** file transfer works over the same SSH path.
4. **Given** a target with no explicit user, **When** the user runs `remo add`,
   **Then** a documented default SSH user is used (and reported back), and the
   user can override it.
5. **Given** the added host appears in the interactive picker, **When** the user
   runs `remo shell` with no name, **Then** the added host is selectable
   alongside provider-managed hosts.

---

### User Story 2 - Update or remove a manually-added host (Priority: P2)

The developer's added host changes address (new IP, new port), or they no longer
need it. They want to update the entry in place or remove it — without editing
the registry file by hand and without any hypervisor call (there is no
infrastructure for `remo` to tear down; the environment's lifecycle is not
`remo`'s to manage).

**Why this priority**: An entry you can create but never correct or clean up
rots. Necessary for the feature to be usable over time, but secondary to being
able to add at all.

**Independent Test**: Add a host, re-run `add` with a changed target, confirm
the entry reflects the new target; then remove it and confirm it is gone from
the registry and the picker.

**Acceptance Scenarios**:

1. **Given** an existing added host, **When** the user runs `remo add` again with
   the same name and a different target, **Then** the entry is updated in place
   (after confirming, unless a bypass flag is given), and no duplicate is
   created.
2. **Given** an existing added host, **When** the user runs the remove command
   for it, **Then** the registry entry is deleted and the host no longer appears
   in `remo shell` or the picker. No connection to the remote environment is
   made and nothing on the remote side is changed.
3. **Given** the remove command targets an added host, **When** it runs, **Then**
   it only deregisters; it never attempts to destroy, stop, or otherwise mutate
   the remote environment (unlike provider `destroy`).

---

### User Story 3 - Verify reachability at add time (Priority: P3)

When adding a host, the developer wants immediate feedback that `remo` can
actually reach it over SSH, rather than discovering a typo later when
`remo shell` fails.

**Why this priority**: A convenience that catches errors early. The feature is
fully usable without it (the first `remo shell` would reveal a bad target), so
it is lowest priority.

**Independent Test**: Run `remo add` against an unreachable target with the
verify option and confirm the command reports the failure clearly and does not
silently register a broken entry (or registers it with a visible warning).

**Acceptance Scenarios**:

1. **Given** a reachable SSH target, **When** the user runs `remo add` with the
   verify option, **Then** `remo` performs a lightweight SSH connectivity check
   and reports success before registering.
2. **Given** an unreachable or auth-failing target, **When** the user runs
   `remo add` with the verify option, **Then** the command surfaces the SSH
   error and either declines to register or registers with an explicit warning
   (behavior chosen consistently and documented), exiting non-zero on failure.
3. **Given** the verify option is not used, **When** the user runs `remo add`,
   **Then** no connectivity check is performed and the entry is registered
   immediately (today's low-friction default for other registry writes).

---

### Edge Cases

- **Name collides with an existing registry entry**: If `<name>` already matches
  a host of *any* type (a provider-managed instance or another added host),
  `remo add` MUST NOT silently shadow or duplicate it. It MUST either refuse with
  a clear message naming the conflicting entry, or (for an existing *added* host
  of the same name) treat it as an update per US2 — provider-managed entries are
  never overwritten by `add`.
- **Target with an explicit port**: `host:port` MUST be parsed so the port is
  used for the SSH connection. Because the registry line is colon-delimited, the
  port MUST be stored without corrupting the line format (storage representation
  is a plan/data-model concern; the requirement is that a non-default port
  round-trips correctly through save → load → connect).
- **IPv6 literal targets**: A raw IPv6 address contains colons and collides with
  both the `host:port` syntax and the colon-delimited registry format. The
  command MUST either support a documented bracketed form (`[::1]:22`) or clearly
  reject IPv6 literals with guidance to use a hostname/alias — it MUST NOT store
  a malformed entry that breaks registry parsing.
- **Custom identity file / SSH key**: Users whose target needs a specific private
  key MUST have a way to record that (an identity option), or a documented
  reliance on their `~/.ssh/config`. A target that only works with a non-default
  key MUST be connectable after `add` without hand-editing.
- **Host key trust on first connect**: `remo add` does not pre-seed the local
  SSH `known_hosts`. The first `remo shell` to an added host follows normal SSH
  host-key behavior (prompt/accept), consistent with existing `direct`-mode
  connections. This is called out so users are not surprised by a first-connect
  host-key prompt.
- **remo-host tooling absent on the target**: A manually added host may not have
  the `remo-host` command or the dev-tools stack installed. `remo shell` against
  such a host MUST degrade gracefully to a plain login shell rather than erroring
  out because the server-side session picker/capabilities probe is unavailable.
  Version checks that assume `remo-host` MUST be treated as "unknown/unmanaged,"
  not as a hard failure.
- **Provider lifecycle commands against an added host**: An added host has no
  `remo` provider backing it. Commands like `snapshot`, resize, or a provider
  `destroy` are not applicable. Attempting a provider-specific operation on an
  added host MUST fail with a clear message that this is a manually-registered
  SSH host with no managed infrastructure, not with an opaque error.

## Requirements *(mandatory)*

### Functional Requirements

**Add**

- **FR-001**: `remo` MUST provide a provider-neutral command to register a
  single SSH-reachable environment into the registry, requiring only SSH
  reachability — no hypervisor host access, cloud credentials, or API token.
- **FR-002**: The command MUST accept a user-facing name and an SSH target
  expressed as `[user@]host[:port]`, and MUST allow the user, host, and port to
  be supplied (or overridden) explicitly.
- **FR-003**: When no user is given, the command MUST apply a documented default
  SSH user and report the effective user back to the caller. When no port is
  given, the standard SSH port MUST be used.
- **FR-004**: The command MUST allow the user to record an explicit SSH identity
  (private key) for the host, or MUST document that connection relies on the
  user's `~/.ssh/config`, such that an added host requiring a non-default key is
  connectable without manual registry or config edits after `add`.
- **FR-005**: A registered added host MUST be connectable via `remo shell` and
  usable with `remo cp` through the existing `direct`-mode SSH connection path,
  with no special-casing required from the user.
- **FR-006**: A registered added host MUST be discoverable by name and MUST
  appear in the interactive `remo shell` picker alongside provider-managed hosts.

**Update & remove**

- **FR-007**: Re-running the add command with an existing added host's name and a
  changed target MUST update that entry in place (guarded by a confirmation
  prompt unless a bypass flag is supplied) rather than creating a duplicate.
- **FR-008**: `remo` MUST provide a way to remove/deregister an added host that
  deletes only the local registry entry and performs no action against the
  remote environment (no destroy, stop, or mutation).
- **FR-009**: The remove path MUST refuse (or clearly distinguish itself) when
  asked to act on a provider-managed host, so that a user cannot mistake
  "deregister my manually-added SSH host" for "destroy my provider instance."

**Safety & interaction with existing behavior**

- **FR-010**: `remo add` MUST NOT overwrite or shadow a provider-managed registry
  entry (incus/proxmox/aws/hetzner) that shares the requested name. On such a
  collision it MUST refuse with a message naming the existing entry.
- **FR-011**: `remo shell` against an added host that lacks `remo-host` / the
  managed tooling MUST degrade to a plain login shell; the tools-version check
  MUST treat a missing `remo-host` as "unknown/unmanaged" rather than a fatal
  error.
- **FR-012**: Provider-specific lifecycle operations (`snapshot`, resize,
  provider `destroy`) invoked against an added host MUST fail with a clear
  message explaining it is a manually-registered SSH host with no managed
  infrastructure.
- **FR-013**: The command MUST validate the supplied name against the same rules
  used for other registry names, and MUST validate/parse the target so that a
  malformed target (including an un-bracketed IPv6 literal, per Edge Cases) is
  rejected with a clear message rather than persisted as a broken registry line.

**Optional verification**

- **FR-014**: The command SHOULD offer an opt-in reachability check that performs
  a lightweight SSH connection test at add time; on failure it MUST surface the
  SSH error and exit non-zero (declining to register, or registering only with an
  explicit warning — chosen consistently and documented). When the check is not
  requested, registration MUST proceed without any network round-trip.

### Out of Scope

- **Bulk discovery of SSH hosts** (e.g. scanning a subnet, importing an
  `~/.ssh/config`). `add` registers one host at a time; bulk discovery remains
  the province of provider `sync`.
- **Lifecycle management of added hosts**: `remo` never creates, destroys,
  resizes, or snapshots a manually added host — it does not own that
  infrastructure. Only registration/deregistration and connection are in scope.
- **Provisioning dev tools onto an added host** (running the Ansible dev-tools
  configure flow). An added host is registered as-is; installing the managed
  tooling onto it is a possible future enhancement, not part of this feature.
- **Web service (`remo web`) discovery of added hosts.** Whether the browser
  session interface surfaces added hosts (which may lack `remo-host`
  capabilities) is deferred; this feature targets the CLI connection path.
- **Changing how provider `sync` works.** The companion feature
  `013-managed-instance-tags` addresses filtered sync; `add` is a distinct,
  complementary path and does not alter `sync`.

### Key Entities

- **Added host (manually-registered SSH host)**: A registry entry representing an
  environment reachable purely over SSH, with no `remo`-managed infrastructure
  behind it. Attributes: user-facing name, SSH host/address, SSH user, SSH port,
  optional identity reference. Modeled as a `KnownHost` with a new provider
  *type* denoting a manually-added SSH host and `access_mode = direct`. It is
  connectable like any `direct` host but is excluded from provider lifecycle
  commands.
- **Registry** (existing): The colon-delimited known-hosts store. This feature
  introduces a new host *type* into it and MUST preserve backward-compatible
  parsing of existing entries. Any new field needed (e.g. port, identity) MUST
  fit the existing serialization without breaking the current 4/6/7-field forms.

### Assumptions

- The connection layer already dispatches on `access_mode` and treats everything
  that is not AWS SSM as a `direct` `user@host` SSH connection, so an added host
  with `access_mode = direct` is connectable with minimal new connection logic.
- SSH authentication itself (keys, agent, `~/.ssh/config`) is the user's existing
  responsibility, exactly as it is for `direct`-mode provider hosts today. `remo
  add` records *where and as whom* to connect, not *how to authenticate* beyond
  an optional identity reference.
- The default SSH user for an added host, when unspecified, follows the same
  convention `remo` uses elsewhere for managed environments (the `remo` user is
  the natural candidate, but the exact default is a plan decision; whatever it
  is, it is reported back at add time so the user can override).
- Storing a non-default port and an optional identity reference can be
  accommodated by the registry format (via new trailing fields or an escaped
  representation); the precise encoding is a data-model decision, constrained by
  FR-013 and the requirement to keep existing entries parseable.
- A new registry *type* (rather than reusing `incus`/`proxmox`/etc.) is the right
  model, so that provider command groups continue to operate only on the
  instances they manage and the added host is visibly not one of them.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A user with only SSH access to a host (no hypervisor/API access)
  can register it and open a working shell using exactly two commands
  (`remo add …` then `remo shell <name>`), with no manual registry editing — on
  100% of attempts against a reachable target.
- **SC-002**: An added host with a non-default SSH port and/or a custom identity
  connects successfully via `remo shell` without any hand-editing of the registry
  or of `remo` configuration after `add` — on 100% of attempts.
- **SC-003**: Re-running `add` with an existing added-host name updates the entry
  in place (no duplicate registry lines) — verified by inspecting the registry
  after the second add.
- **SC-004**: Removing an added host deletes its registry entry and makes no
  network connection to the remote environment — verified by confirming the
  entry is gone and that no SSH/API call to the target occurred.
- **SC-005**: Attempting `remo add <name>` where `<name>` already names a
  provider-managed instance never overwrites that instance's entry — on 100% of
  attempts it is refused with a message naming the conflict.
- **SC-006**: `remo shell` into an added host that lacks `remo-host` lands the
  user in a plain login shell instead of erroring — on 100% of attempts.
- **SC-007**: Existing registry files written before this feature continue to
  load without error after the new host type and any new fields are introduced —
  100% backward-compatible parsing.
