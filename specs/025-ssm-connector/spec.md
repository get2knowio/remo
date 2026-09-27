# Feature Specification: SSM Connector — Reach Remo Project Sessions Through AWS Systems Manager, With No Inbound Ports

**Feature Branch**: `025-ssm-connector`

**Created**: 2026-09-27

**Status**: Draft

**Input**: User description: "SSM connector — reach Remo project sessions through AWS Systems Manager, with no inbound ports. A connector is a Linux machine registered as an SSM hybrid-activated managed node in the operator's own AWS account, running SSM Agent plus remo. Starting an SSM session against it with Remo's session document `remo-attach` lands the caller in the same Zellij/devcontainer project session that `remo shell -p <project>` reaches, on any host the connector was explicitly told to expose. Public, product-neutral; the connector is a transport, orthogonal to compute providers." (Spec Prompts queue, Sequence 2, drafted 2026-09-25 against `get2knowio/remo@680dbf7`.)

## Context

Remo reaches instances in two ways today: direct SSH, and — for AWS EC2 only —
SSH tunnelled through AWS Systems Manager (SSM), where the AWS provider supplies
a proxy command that opens an `AWS-StartSSHSession` session. Any other host
that is not directly reachable — a Proxmox or Incus host behind home NAT, or an
isolated Hetzner box the operator would rather not expose — needs a VPN or an
overlay network before any client can reach it.

AWS Systems Manager **hybrid activation** lets any Linux machine register as a
managed node (`mi-…`) in the operator's *own* AWS account using only outbound
connections. A **connector** is such a machine running SSM Agent plus remo.
When a caller starts an SSM session against the connector using Remo's
**session document** (`remo-attach`), the connector attaches the caller to a
Remo project session on one of the hosts it can reach — the **same
Zellij/devcontainer session** that `remo shell -p <project>` reaches.

That gives Remo a transport that:

- needs no inbound ports and no VPN on the target side;
- works with any AWS account the operator controls;
- is reachable by any client that speaks the SSM session protocol: the AWS CLI
  with the Session Manager plugin today, and browser clients that speak the
  SSM data channel later (a separate feature consumes this contract).

The connector is a **transport, orthogonal to compute providers**. A Proxmox
container stays a Proxmox container; the connector is simply a way in. It is
also **not a resident Remo daemon**: SSM Agent is AWS's agent, installed only
on machines the operator explicitly enrolls as connectors. Remo's
no-daemon-on-instances stance is otherwise unchanged.

Two risks shape every requirement below:

1. **Injection.** SSM substitutes document parameters into a command string.
   Remo project names legitimately contain spaces, Unicode, quotes,
   punctuation, and leading dashes, so a raw name must never reach that
   string. The document accepts exactly one opaque, pattern-restricted
   parameter; the launcher decodes it and re-validates every field with the
   same validators the CLI and web paths use.
2. **Over-exposure.** Permission to start a session on the connector must not
   implicitly grant every host in the connector's registry. The connector
   attaches only to host/project pairs the operator explicitly exposed —
   default deny.

## Clarifications

### Session 2026-09-27

- Q: A hybrid activation has two parts — an activation *ID* and an activation
  *code*. Which is the secret, and how does each reach enrollment? → A: The
  code is the secret and is read only from a non-echoing prompt or standard
  input; the activation ID and the region are ordinary command arguments.
  Only the code is subject to the never-in-argv / never-in-logs rules.
- Q: What is the run-as user called, and can the operator change it? → A:
  The default is `remo-connector`; enrollment accepts an override. The name
  is recorded on the connector and reported by `status`, because the account
  owner must configure run-as to exactly that name.
- Q: How does a self-targeting connector address itself in its own registry?
  → A: Enrollment writes the exposed host that is the connector itself as a
  registry entry whose address is the loopback host, under the same name the
  operator uses; the launcher treats it exactly like any remote host (SSH to
  loopback as the target's Remo user).
- Q: With strict host-key checking, where does the connector's known-hosts
  material come from? → A: Enrollment scans each exposed host's key from the
  connector's own vantage point and records it — trust on first enrollment,
  matching the console's adoption precedent. Re-enrollment refreshes only
  hosts whose key is not yet recorded; a changed key is an error the operator
  resolves, never silently overwritten.
- Q: Should the launcher probe an exposed host's reachability before
  replacing its process, so unreachable hosts also produce an error line? →
  A: No. The error line covers only failures the launcher can decide locally
  (decoding, validation, exposure, run-as, configuration). Once it replaces
  its process, SSH's own output is the failure signal; a probe would add a
  second connection and latency to every attach.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Attach to a project session through SSM (Priority: P1)

An operator whose Proxmox host sits behind home NAT has enrolled a small LXC on
that network as a connector. From a laptop with the AWS CLI and the Session
Manager plugin, they start an SSM session against the connector's managed-node
id with the `remo-attach` document and an encoded target naming the host and
project. Their terminal lands in the project's Zellij session — the very same
session `remo shell -p <project>` would open — with a working PTY: full-screen
TUIs render, Unicode round-trips, window resizes propagate, and closing the
client leaves the session running for the next attach.

**Why this priority**: This is the feature. Without a working attach path, the
document, the enrollment, and the exposure model have nothing to protect.

**Independent Test**: With a fake SSM invocation environment (the launcher run
under a non-root, non-`ssm-user` account with a prepared local registry and
exposure file), invoke the launcher with an encoded target and assert that the
process replaces itself with exactly the attach command the web console would
build for the same host and project. The live half — a real hybrid-activated
node and a real `aws ssm start-session` — is the manual gate (SC-008).

**Acceptance Scenarios**:

1. **Given** a connector enrolled and running, a host `h` exposed for project
   `p`, and a caller permitted to start sessions on that node, **When** the
   caller starts an SSM session with document `remo-attach` and parameter
   `target` encoding `{host: h, project: p}`, **Then** the caller is attached
   to the same Zellij session that `remo shell -p p` reaches on `h`, under a
   PTY.
2. **Given** an attached session, **When** the caller resizes their terminal,
   **Then** the remote session redraws at the new size.
3. **Given** an attached session, **When** the caller closes the client,
   **Then** the project session on `h` keeps running, and a subsequent attach
   resumes it.
4. **Given** a well-formed target naming a host that is exposed, **When** the
   launcher runs, **Then** the command it hands to SSH is byte-for-byte the
   same attach command the web console builds for that host and project
   (identity, known-hosts handling, batch mode, connection multiplexing, and
   the remote attach invocation included).

---

### User Story 2 - Only what the operator exposed is reachable (Priority: P1)

The same connector's local registry also knows about a production host that
the operator never meant to reach through SSM. A caller with permission to
start sessions on the connector asks for that host, or for an unexposed
project on an exposed host. The connector refuses with a single, stable,
machine-readable error line and never opens a session. The connector also
refuses to run at all if it finds itself running as root or as the default
`ssm-user`, because that means the account's run-as configuration is not in
effect.

**Why this priority**: The document runs on a machine that can reach every
host in its registry. Without default-deny exposure and the run-as backstop,
a session permission on the connector silently becomes a permission on
everything behind it.

**Independent Test**: Run the launcher against a registry with two hosts and
an exposure file naming only one host/project pair; assert that every
non-exposed combination, every malformed or wrong-version target, and every
raw (unencoded) name yields the documented `remo-connector-error:` line and a
non-zero exit before any SSH process is started. Run it as uid 0 and as a
user named `ssm-user` and assert the same refusal.

**Acceptance Scenarios**:

1. **Given** an exposure file that names `h`/`p` only, **When** a target
   names `h`/`q` or `other-host`/`p`, **Then** the launcher prints exactly one
   line of the form `remo-connector-error: <code> <message>` with the
   `not-exposed` code and exits non-zero without starting SSH.
2. **Given** a target whose decoded object carries an unknown `v`, or does
   not decode, or names a host or project that fails Remo's own name
   validators, **Then** the launcher prints one error line with the
   corresponding stable code and exits non-zero.
3. **Given** a caller who passes a raw project name (not the encoded form) as
   the `target` parameter, **When** SSM evaluates the document, **Then** the
   document's parameter pattern rejects it before any command runs.
4. **Given** the launcher started with effective uid 0, or as a user named
   `ssm-user`, **Then** it prints one error line with the `run-as-not-in-effect`
   code and exits non-zero.
5. **Given** an empty or missing exposure file, **Then** every target is
   refused (default deny).

---

### User Story 3 - Enroll, inspect, and unenroll a connector (Priority: P2)

From their workstation, the operator turns a host remo already manages into a
connector: they run one enrollment command, are prompted for the hybrid
activation code (which is never echoed, never placed on a command line, and
never written to a log), and the command installs or verifies SSM Agent,
registers the node, creates the dedicated non-admin run-as user, provisions a
pinned remo on the connector with its own registry, exposure file, and SSH
identity, and prints the managed-node id. Running the same command again is a
no-op. Later, `status` shows the agent state, node id, region, and exposed
targets; `unenroll` stops the agent and removes local registration material,
telling the operator plainly that account-side deregistration is their
separate step.

**Why this priority**: Enrollment is how a connector comes to exist, but a
connector prepared by hand can already exercise Stories 1 and 2; that is why
this is P2 rather than P1.

**Independent Test**: Run the enrollment playbook against a fresh host and
then against the same host again; assert both runs succeed, the second
reports no changes, every registered-variable access is defensive, and the
activation code appears in no process argument, no Ansible output, and no
log. Exercise every conditional branch (agent already installed / not
installed, node already registered / not registered, run-as user present /
absent).

**Acceptance Scenarios**:

1. **Given** a host in the operator's registry, **When** the operator runs
   the enrollment command, **Then** they are prompted for the activation code
   on an interactive, non-echoing prompt (or may pipe it on standard input),
   and the code is accepted from nowhere else — not from an argument, not from
   an environment variable.
2. **Given** the operator supplied the code, **When** enrollment runs the
   automation, **Then** the code reaches the automation only through a
   private, owner-only temporary file that is deleted afterwards, the tasks
   that handle it suppress logging, and no process listing, shell history,
   log, or automation output ever contains it.
3. **Given** a successful enrollment, **Then** SSM Agent is installed (or
   verified) and registered, the run-as user exists and is not an
   administrator, a pinned remo is installed for that user with a private
   registry, exposure file, and SSH identity readable by that user and nobody
   broader, and the managed-node id is recorded on the connector and printed
   to the operator.
4. **Given** an already-enrolled connector, **When** the operator runs
   enrollment again with the same inputs, **Then** the run reports no changes
   and the node id is unchanged.
5. **Given** an enrolled connector, **When** the operator runs `status`,
   **Then** they see the agent's state, the managed-node id, the region, and
   the exposed host/project pairs.
6. **Given** an enrolled connector, **When** the operator runs `unenroll`,
   **Then** the agent is stopped and disabled, local registration material is
   removed, and the output states that deregistering the node in the AWS
   account is a separate step the account owner performs — and that an
   expired activation does not disconnect an already-registered node.

---

### User Story 4 - Anyone can create the session document in their account (Priority: P2)

An account owner who has never seen this repository wants to use a connector
someone enrolled. They run `remo connector document`, which prints the
`remo-attach` session document exactly as shipped in the tagged remo release
they installed, and they create it in their AWS account with their own
tooling. The document's contract — its single parameter, the encoding, and
the error line the launcher emits — is versioned and documented so that other
clients can hard-code it.

**Why this priority**: The document is the contract every consumer depends on
(downstream deployments pin the release that ships it), but it is small and
static; it can be finalized alongside Story 1.

**Independent Test**: Assert that the printed document is the file shipped in
the package, that it declares exactly one parameter with the documented
pattern, that the pattern rejects a corpus of raw hostile names and accepts
every encoded target produced from that corpus, and that the document's
command invokes only the launcher with only that parameter.

**Acceptance Scenarios**:

1. **Given** an installed remo, **When** the user runs `remo connector
   document`, **Then** the printed text is the shipped document, unchanged.
2. **Given** the document, **Then** it declares exactly one parameter,
   `target`, whose allowed pattern admits only the unpadded base64url
   alphabet up to a documented maximum length, and whose command runs a PTY
   session that invokes only the connector launcher with that parameter.
3. **Given** a change to the document's contract, **Then** the change is
   published as a new contract version, and the tagged release notes name the
   document version shipped.

---

### User Story 5 - Documented, proven deployment shapes (Priority: P3)

An operator evaluating the connector reads `docs/ssm-connector.md` and finds:
the architecture; the exact contract (target encoding, the error-line codes);
a least-privilege example policy that lets a caller start sessions only with
this document and only on nodes tagged as connectors; why the run-as user is
not `ssm-user` and what the account owner must configure for run-as to take
effect; how to revoke access (terminate sessions, deregister the node); what
the two reference deployment shapes are and exactly what was verified for
each — a dedicated connector in a Proxmox LXC reaching several Proxmox and
Incus hosts, and a connector directly on an isolated Hetzner host targeting
itself — including the honest statement that SSM Agent in an unprivileged LXC
is not an AWS-documented configuration; and where the per-session cost is
documented (a link to AWS's pricing page, never a hardcoded number).

**Why this priority**: The documentation is what makes the feature usable
outside this repository, but it depends on everything above being settled.

**Independent Test**: The documentation structure gate passes; every command
and file the document names exists; a reviewer can follow the least-privilege
policy and the run-as instructions without consulting the source.

**Acceptance Scenarios**:

1. **Given** the published documentation, **When** an account owner follows
   it, **Then** they can create the document, grant a caller least-privilege
   access, configure run-as, enroll a connector, and revoke access, with no
   step that names a command or file that does not exist.
2. **Given** the self-target shape, **Then** the documentation states plainly
   that run-as isolation protects the connector host, not the target session:
   the target's Remo user has passwordless administrative rights by design.
3. **Given** the LXC shape, **Then** the documentation records what was
   tested (init-system behavior, persistence of the agent's registration
   across restarts, OS combinations) and what was not, rather than implying
   support that was not exercised.

---

### Edge Cases

- **Self-target**: the connector *is* the target (an isolated host targeting
  itself). The launcher connects to the local host as the target's Remo user
  exactly as it would to any remote host; it never attaches locally as the
  run-as user, because that user cannot enter another user's session.
- **Two-caller concurrency**: two callers attach to the same project at once.
  Both land in the same session, as two `remo shell` clients would today.
- **Exposed host that is unreachable**: the launcher's SSH attempt fails after
  the error-line stage. The failure surfaces as SSH's own output on the PTY;
  no generic shell is ever offered as a fallback.
- **Encoded target above the maximum length**: rejected by the document's
  parameter pattern before the launcher runs.
- **Target with extra JSON fields**: the launcher ignores unknown fields within
  a known `v`; it rejects any object missing a required field.
- **Host name valid in the registry but not exposed**: refused as
  `not-exposed`; the error message must not reveal whether the host exists in
  the registry (exposed-or-not is the only distinction the caller learns).
- **Registry or exposure file unreadable by the run-as user**: refused with a
  stable code that names the file to fix, never a traceback.
- **Expired activation**: enrollment with an expired activation fails at
  registration with the agent's error surfaced; an already-registered node is
  unaffected by its activation expiring.
- **Activation code piped on standard input with a trailing newline**: the
  newline is stripped; an empty code is refused before any automation runs.
- **Enrollment interrupted after the vars file was written**: the private
  temporary file is removed on every exit path, including failure and
  interruption.
- **Run-as configured by preference in one region but the node registered in
  another**: the launcher's refusal is the backstop; the documentation tells
  the account owner that preferences are per region.

## Requirements *(mandatory)*

### Functional Requirements

**Session-document contract**

- **FR-001**: The repository MUST ship a session document named `remo-attach`
  as a file included in the installed package and in every tagged release, and
  `remo connector document` MUST print that file unchanged.
- **FR-002**: The document MUST declare exactly one parameter, `target`, whose
  value is the unpadded base64url encoding of a versioned JSON object of the
  form `{"v": 1, "host": "<host>", "project": "<project>"}`. The parameter's
  allowed pattern MUST admit only the unpadded base64url alphabet
  (`A–Z a–z 0–9 - _`) with a documented maximum length chosen so that every
  host name and project name Remo's own validators accept can be encoded, and
  justified in the design.
- **FR-003**: The document MUST run its command under a PTY (an interactive
  session type), and that command MUST invoke only the connector launcher with
  only the `target` parameter. The document MUST NOT run an arbitrary command,
  and the launcher MUST NOT offer a general shell.
- **FR-004**: The contract (parameter, encoding, `v`, error-line format and
  codes) MUST be documented as a versioned contract; a change to it MUST be
  published as a new version and the release notes of the release that ships
  the document MUST name the document version.
- **FR-005**: Documentation and example policies MUST use the tag
  `remo:role=connector` to identify connector managed nodes.

**Launcher**

- **FR-006**: `remo connector attach` MUST decode the target, reject any `v`
  other than the supported versions, and validate the decoded host and
  project with the identical validators the CLI and web paths use, before any
  other action.
- **FR-007**: The launcher MUST refuse to run when its effective user id is 0
  or its user name is `ssm-user`.
- **FR-008**: The launcher MUST resolve the host in the connector's local
  registry and MUST refuse any host/project pair not present in the
  connector's exposure configuration. With no exposure configuration, every
  request MUST be refused.
- **FR-009**: On success, the launcher MUST replace its own process with the
  attach command so that the session's PTY drives SSH directly, and that
  command MUST be the same attach command the web console builds for the same
  host and project: batch mode, connection multiplexing, and identity and
  known-hosts resolution preserved.
- **FR-010**: The host-agnostic attach-command construction MUST be shared
  between the launcher and the web console rather than duplicated, MUST NOT
  depend on the optional web components, and MUST carry no provider-specific
  knowledge. The connector MUST work with remo installed without the web
  extra.
- **FR-011**: On any failure before the process is replaced, the launcher MUST
  print exactly one line of the form `remo-connector-error: <code> <message>`
  — with a stable code from a documented set and a message that states what
  failed and what fixes it — and exit non-zero. The documented set MUST cover
  at least: undecodable or malformed target, unsupported version, invalid
  host or project name, not exposed, run-as not in effect, and unreadable
  connector configuration. The launcher MUST NOT probe the target's
  reachability before replacing its process; after that point SSH's own
  output is the failure signal.
- **FR-012**: For a self-target, the launcher MUST connect to the local host
  as the target's Remo user exactly as for a remote host; enrollment writes
  the connector's own entry with the loopback address under the operator's
  name for it, so no special case exists in the launcher.
- **FR-013**: The launcher MUST NOT branch on a host's provider type; the
  transport is orthogonal to compute providers.

**Enrollment and lifecycle**

- **FR-014**: Enrollment MUST run from the operator's workstation against a
  host that remo already manages (any provider, or a host registered with
  `remo add`), through the existing automation runner; the connector's own
  installation is a result of enrollment, not a prerequisite.
- **FR-015**: The activation code MUST be read only from an interactive
  non-echoing prompt or from standard input. It MUST NOT be accepted from a
  command-line argument or an environment variable. The activation ID and
  the region are ordinary arguments; they are not secrets.
- **FR-016**: The activation code MUST reach the automation only through an
  owner-only (mode 0600) temporary variables file that is deleted on every
  exit path, and every task that handles it MUST suppress logging. On the
  operator's workstation it MUST NOT appear in any process argument list,
  shell history, log line, or automation output. (AWS's own registration
  command on the connector takes the code as an argument for the seconds it
  runs; that connector-side residual is documented, not hidden.)
- **FR-017**: Enrollment MUST be an idempotent automation role that installs
  or verifies SSM Agent; registers the node with the activation; creates the
  dedicated non-admin run-as user; installs a pinned remo for that user along
  with the connector's private registry, exposure file, and SSH identity,
  readable by the run-as user and nobody broader; and records and prints the
  managed-node id. A second run with the same inputs MUST report no changes.
- **FR-018**: The exposure configuration MUST be declared at enrollment as a
  repeatable option naming host/project pairs, MUST live in its own file on
  the connector, and re-running enrollment with a different set MUST converge
  the file to that set. The registry schema MUST NOT change for this feature.
- **FR-019**: The connector MUST use its own SSH identity, distinct from the
  operator's, with strict host-key checking against its own known-hosts
  material for direct SSH. Enrollment MUST make that identity usable:
  it MUST authorize the connector's public key for the Remo user on every
  exposed host, MUST record every exposed host's host key in the connector's
  known-hosts material by scanning it from the connector on first enrollment
  (a later key change is an error for the operator to resolve, never a silent
  overwrite), and MUST write each exposed host into the connector's private
  registry as a directly-reachable SSH host. A host removed from the
  exposure set on re-enrollment MUST be removed from the exposure file (its
  registry entry and authorization may remain; exposure alone gates attach).
- **FR-020**: `remo connector status` MUST report the agent's state, the
  managed-node id, the region, and the exposed host/project pairs.
- **FR-021**: `remo connector unenroll` MUST stop and disable the agent and
  remove local registration material, and its output MUST state that
  account-side deregistration is a separate step for the account owner and
  that an expired activation does not disconnect an already-registered node.

**Constraints and non-regression**

- **FR-022**: The feature MUST NOT change the existing EC2 SSH-over-SSM path,
  `remo shell`, or the host-tooling protocol version.
- **FR-023**: The connector path MUST respect the one-way layering rule and
  MUST NOT add any exception to the architecture test's allowlists.
- **FR-024**: All automation added MUST use defensive registered-variable
  access and accurate change reporting.
- **FR-025**: Deferred work (at minimum: running the connector as a Docker
  container if not proven, and the live manual gate) MUST be tracked as
  issues, never as inline TODOs.

**Documentation**

- **FR-026**: `docs/ssm-connector.md` MUST cover the architecture; the
  contract (target encoding, error-line codes); an example least-privilege
  caller policy scoped to this document and to connector-tagged nodes,
  including the session-document access check; run-as configuration (both
  the per-region preference and the caller-tag form, and which wins) and why
  the run-as user is not `ssm-user`; revocation (terminate sessions,
  deregister the node); the two reference deployment shapes with exactly what
  was verified; and cost, by link to AWS's pricing page.
- **FR-027**: The documentation MUST state that run-as isolation protects the
  connector host, not the target session, and that the target's Remo user has
  passwordless administrative rights by design.
- **FR-028**: The structure diagrams and command references that CI checks
  MUST be updated in the same change.

### Key Entities

- **Session document (`remo-attach`)**: the versioned AWS-side contract: one
  parameter, an allowed pattern, a PTY session, and a fixed command that
  invokes the launcher.
- **Encoded target**: the caller's request — a versioned object naming a host
  and a project — carried as one opaque, pattern-restricted string.
- **Connector**: a host remo manages that has been enrolled: SSM Agent
  registered as a managed node, a run-as user, a pinned remo, a private
  registry, an exposure file, and its own SSH identity.
- **Exposure configuration**: the connector-local allow-list of host/project
  pairs the launcher may attach to; absent or empty means deny all.
- **Run-as user**: the dedicated non-administrative account the account
  owner's run-as configuration maps SSM sessions to, and the only account the
  launcher will run under.
- **Error line**: the single machine-parseable line
  `remo-connector-error: <code> <message>` that carries a pre-attach failure
  to the caller over the PTY stream.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: For every host and project pair in a corpus of hostile names
  (spaces, quotes, Unicode, leading dashes, path traversal, control
  characters), encoding then decoding the target reproduces the original
  names exactly, and every raw name from the corpus is rejected by the
  document's parameter pattern.
- **SC-002**: For every exposed host/project pair, the launcher's attach
  command equals the web console's attach command for the same pair, in a
  test that runs without the web extra installed.
- **SC-003**: 100% of non-exposed, malformed, wrong-version, and
  invalid-name requests, and 100% of uid-0 / `ssm-user` invocations, produce
  exactly one `remo-connector-error:` line with a documented code and a
  non-zero exit, with no SSH process started.
- **SC-004**: The enrollment automation succeeds on a fresh host and reports
  zero changes on an immediate second run, with every conditional branch
  exercised under both conditions.
- **SC-005**: Across the enrollment tests, the activation code appears in
  zero process argument lists, zero log lines, and zero automation output
  lines.
- **SC-006**: The architecture, documentation-structure, lint, and type
  gates pass with no new allowlist entries.
- **SC-007**: `remo --help` and every non-connector command behave exactly as
  before; the EC2 SSH-over-SSM path and `remo shell` are unchanged.
- **SC-008** (manual gate, pre-production, tracked as an issue): with a
  connector enrolled under a hybrid activation in a test AWS account, an
  operator using the AWS CLI with the Session Manager plugin attaches to two
  targets (at least one non-AWS) and lands in the same session as
  `remo shell -p`; resize, Unicode, and full-screen TUIs work; closing the
  client leaves the session running; an unexposed host or project yields the
  documented error line; and the session runs as the run-as user. The result
  — including whether a custom interactive document honors run-as — is
  recorded in the tracked issue and in the documentation.

## Assumptions

- The operator controls an AWS account with permission to create hybrid
  activations, session documents, and Session Manager preferences; Remo does
  not provision any of that (a separate platform feature does).
- Enrollment targets a host remo already manages, from the operator's
  workstation, using the same automation runner and SSH access the existing
  configure flows use; on-connector self-enrollment is out of scope.
- The connector runs a systemd-based Linux distribution for which AWS
  publishes SSM Agent packages; the reference shapes are Debian/Ubuntu in a
  Proxmox LXC and on a Hetzner cloud host.
- The connector's pinned remo version defaults to the version of the
  enrolling CLI and can be overridden at enrollment.
- The run-as user defaults to `remo-connector` and can be overridden at
  enrollment; the account owner configures run-as (by preference or caller
  tag) to exactly the name `status` reports.
- Exposure is expressed as explicit host/project pairs; wildcard exposure
  ("every project on this host") is out of scope for this feature.
- The maximum encoded-target length is a fixed number chosen from Remo's own
  name-length limits, recorded in the contract, and enforced only by the
  document's pattern (the launcher enforces the decoded field limits).
- Session type, run-as behavior for custom interactive documents, and SSM
  Agent behavior inside an unprivileged LXC are verified against AWS's
  published schema and by the manual gate; where the manual gate has not yet
  run, the documentation says so instead of claiming support.
- Running the connector as a Docker container is deferred to a tracked issue
  unless the manual gate proves it incidentally.
- The browser-side consumer of this contract is a separate feature in the
  console repository; nothing here depends on it.
