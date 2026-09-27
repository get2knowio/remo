# Deferred issues: 025-ssm-connector

Drafted per research.md R15 / tasks.md T041. **Not filed** in this
implementation run (the branch has not been pushed). File each with `gh
issue create` when the branch is pushed/PR is opened, then replace the
`issue #TBD` placeholders in `docs/ssm-connector.md` and in this file with
the real issue numbers.

---

## 1. Manual gate for spec 025: live SSM connector attach, run-as proof, LXC + Hetzner self-target shapes (SC-008)

**Body:**

The SSM connector feature (specs/025-ssm-connector) ships with every
non-AWS-dependent path unit-tested (codec, exposure gate, error line,
launcher argv parity, enrollment pre-flight/secret-safety, Ansible role
structure) but the live path — a real hybrid activation, a real
`aws ssm start-session`, and the two reference deployment shapes — is out of
automated-test scope by design (it needs a test AWS account and real
network-reachable hosts).

**What to verify** (spec.md SC-008, quickstart.md §D–F):

- Enroll a connector under a real hybrid activation in a test AWS account.
- Attach to at least two exposed targets, at least one non-AWS (e.g. a
  Proxmox/Incus host reached only through the connector).
- Confirm: same Zellij session as `remo shell -p`; resize propagates;
  Unicode round-trips; a full-screen TUI renders correctly; closing the
  client leaves the session running; a subsequent attach resumes it.
- Confirm an unexposed host or project yields exactly the documented
  `remo-connector-error: not-exposed …` line.
- Confirm the session actually runs as the configured run-as user (not
  `ssm-user`), and specifically **whether a custom `InteractiveCommands`
  document honors Session Manager Run As** the same way built-in documents
  do — AWS's docs do not state this explicitly (research R10).
- Exercise the LXC shape: a dedicated connector in an unprivileged Proxmox
  LXC reaching several Proxmox/Incus hosts. Restart the LXC and confirm the
  agent's registration and `/etc/machine-id` survive (SSM Agent inside an
  unprivileged LXC is not an AWS-documented configuration — this is the
  known failure mode to check for).
- Exercise the Hetzner shape: a connector directly on an isolated Hetzner
  host, targeting itself (self-target, `host: "localhost"`).

Record the results — including whether run-as is honored for a custom
document — in this issue AND in `docs/ssm-connector.md`'s
"Reference deployment shapes" table, replacing "Not yet verified" with what
was actually proven.

**Labels:** `manual-gate`, `025-ssm-connector`

---

- Self-target port: enrollment writes the connector's own registry entry as `localhost` with the
  **operator-side** port. If the operator reaches the connector through a NAT/port-forward
  (e.g. 2222 → 22), `localhost:2222` will not answer on the connector. Verify the self-target
  shape with a non-22 forwarded port and, if it fails, add a `--self-port` override or probe the
  local sshd port at enrollment.

## 2. Connector as a Docker container: prove or document unsupported

**Body:**

specs/025-ssm-connector's enrollment role assumes a systemd-based Debian/
Ubuntu host (bare metal, VM, or LXC) and installs SSM Agent as a systemd
unit. Running the connector itself as a Docker container was explicitly out
of scope for this feature (spec.md Assumptions: "Running the connector as a
Docker container is deferred to a tracked issue unless the manual gate
proves it incidentally").

**What to do:** either prove a container shape works (SSM Agent typically
wants systemd or an init process; a container would need one, plus a stable
machine identity across restarts — the same persistence concern as the
unprivileged-LXC shape) and document the exact image/entrypoint shape that
works, or document plainly in `docs/ssm-connector.md` that it is
unsupported and why.

**Labels:** `enhancement`, `025-ssm-connector`

---

## 3. `InteractiveCommands` documents are documented as AWS-CLI-only — verify the browser data channel before Sequence 4

**Body:**

AWS's own documentation (session-manager-restrict-command-access.html,
fetched 2026-09-27, research R1) states: "Documents with the `sessionType`
of `InteractiveCommands` are only supported for sessions started from the
AWS Command Line Interface (AWS CLI)." specs/025-ssm-connector's client is
the AWS CLI with the Session Manager plugin, so this does not affect this
feature. It IS a risk for a planned follow-on (Spec Prompts queue Sequence
4: a browser-direct client speaking the SSM session/data-channel protocol
without going through the AWS CLI).

**What to do:** before planning that follow-on feature, run a small
experiment against a real `remo-attach` document from a browser-side SSM
data-channel client (or the equivalent AWS SDK-for-JS session stream) and
confirm whether an `InteractiveCommands` document actually works outside the
CLI in practice, despite the documented constraint. Record the result here;
it gates whether Sequence 4 needs a different `sessionType` or document
shape.

**Labels:** `research`, `025-ssm-connector`

---

## 4. `remo connector expose`/`unexpose` without re-running enrollment

**Body:**

Today, changing which host/project pairs a connector exposes requires
re-running `remo connector enroll NAME ... --expose ...` with the full
`--expose` set (it converges the exposure file to exactly that set — FR-018).
This re-runs the whole idempotent enrollment role, which is correct but
heavier than necessary for a pure exposure-set change (no agent
reinstall/re-registration/identity regeneration is needed).

**What to do:** add `remo connector expose NAME HOST/PROJECT` and
`remo connector unexpose NAME HOST/PROJECT` convenience commands that read
the connector's current `exposure.json` over SSH, apply the single
add/remove, and write it back directly — without invoking Ansible at all.
Keep `enroll --expose` as the full-convergence path (useful for drift
recovery); these become a lighter-weight day-2 operation.

**Labels:** `enhancement`, `025-ssm-connector`
