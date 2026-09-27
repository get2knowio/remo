# SSM Connector

Reach a Remo project session on any host you manage through AWS Systems
Manager (SSM), with **no inbound ports** on the target and no VPN. A
**connector** is a Linux machine remo already manages, enrolled as an SSM
**hybrid-activated managed node** in your own AWS account. Starting an SSM
session against it with Remo's session document (`remo-attach`) lands the
caller in the same Zellij/devcontainer project session that `remo shell -p
<project>` reaches, on any host/project pair the connector was explicitly
told to expose.

The connector is a **transport**, orthogonal to compute providers: a Proxmox
container stays a Proxmox container, and the connector is simply a way in.
It is not a resident Remo daemon — SSM Agent is AWS's own agent, installed
only on machines you explicitly enroll as connectors.

## Architecture

```
AWS CLI (Session Manager plugin)
        │  aws ssm start-session --document-name remo-attach --parameters target=<encoded>
        ▼
 AWS Systems Manager  ──(outbound-only)──  SSM Agent on the connector
                                                   │
                                          remo connector attach -- <encoded>
                                                   │  decode + validate + exposure check
                                                   ▼
                                          exec ssh -tt <exposed host>
                                          "remo-host sessions attach --project P"
                                                   │
                                                   ▼
                                    the SAME Zellij/devcontainer session
                                    `remo shell -p P` reaches on that host
```

The connector never opens an inbound port: it dials out to AWS over HTTPS
(SSM Agent) and dials out to each exposed host over SSH (outbound from the
connector's own network position — which is exactly what lets it reach a
Proxmox host behind home NAT that a workstation cannot).

## The v1 contract

Full contract: [`specs/025-ssm-connector/contracts/session-document.md`](../specs/025-ssm-connector/contracts/session-document.md).

- The document is `InteractiveCommands`, PTY-backed, with exactly one
  parameter, `target`: the unpadded base64url encoding of
  `{"v":1,"host":"<host>","project":"<project>"}`.
- The `allowedPattern` (`^[A-Za-z0-9_-]{1,1024}$`) admits only the base64url
  alphabet, so a raw (unencoded) name — which may legitimately contain
  spaces, Unicode, quotes, or a leading dash — can never reach the launcher
  as a literal string. SSM itself rejects anything else before any command
  runs.
- The document's command is fixed: `exec /opt/remo-connector/bin/remo
  connector attach -- {{ target }}`. It invokes only the launcher, with only
  that one parameter; there is no general shell.
- On any pre-attach failure the launcher prints exactly one line —
  `remo-connector-error: <code> <message>` — and exits non-zero. The
  documented codes: `bad-target`, `unsupported-version`, `invalid-name`,
  `not-exposed`, `run-as-not-in-effect`, `config-unreadable`, `ssh-missing`.
  Once the launcher `exec`s into `ssh`, SSH's own output is the failure
  signal — the launcher never probes reachability first, so a normal attach
  costs no extra round-trip.

## Creating the document

Anyone with an installed `remo` can print and create the exact shipped
document, unchanged:

```bash
remo connector document > remo-attach.json
aws ssm create-document --name remo-attach --document-type Session --content file://remo-attach.json
```

A change to the contract ships as a new `v`/document version, and the
release notes of the tagged release that ships it name the version.

## Hybrid activation

Create an activation scoped with the `remo:role=connector` tag so a
least-privilege policy can target connector nodes specifically:

```bash
aws ssm create-activation \
  --region eu-central-1 \
  --iam-role SSMServiceRole \
  --registration-limit 1 \
  --tags Key=remo:role,Value=connector
```

Note the **activation ID** (not secret) and the **activation code** (secret,
consumed once at enrollment — see below).

## Least-privilege caller policy

Scope a caller to start sessions only through `remo-attach`, only against
connector-tagged nodes:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": "ssm:StartSession",
      "Resource": "arn:aws:ssm:REGION:ACCOUNT:managed-instance/*",
      "Condition": {
        "StringEquals": { "ssm:resourceTag/remo:role": "connector" },
        "BoolIfExists": { "ssm:SessionDocumentAccessCheck": "true" }
      }
    },
    {
      "Effect": "Allow",
      "Action": "ssm:StartSession",
      "Resource": "arn:aws:ssm:REGION:ACCOUNT:document/remo-attach"
    },
    {
      "Effect": "Allow",
      "Action": ["ssm:TerminateSession", "ssm:ResumeSession"],
      "Resource": "arn:aws:ssm:*:*:session/${aws:userid}-*"
    },
    {
      "Effect": "Allow",
      "Action": "ssmmessages:OpenDataChannel",
      "Resource": "arn:aws:ssm:*:*:session/${aws:userid}-*"
    }
  ]
}
```

`ssm:SessionDocumentAccessCheck` makes the `StartSession` grant honor the
document-level check too, so a caller who can start sessions on connector
nodes still can't use a *different* document against them.

## Run As: why not `ssm-user`

Configure **Session Manager Run As** — either the per-region preference
(an OS user name) or, per-principal, the `SSMSessionRunAs` IAM tag, which is
checked first. Set it to exactly the name `remo connector status` reports
(default: `remo-connector`). With Run As enabled, a session never falls back
to `ssm-user`; **the launcher itself refuses to run as `ssm-user` or as
uid 0** (`run-as-not-in-effect`), so a misconfigured account fails closed
instead of silently granting broader access than intended. AWS's own
documentation does not state whether the preference's Run As user applies to
a *custom* `InteractiveCommands` document the way it does to the built-in
ones — this is exactly the situational risk the manual gate (below) records.

**Run-as isolation protects the connector host, not the target session.**
The target host's Remo user (the account `remo shell -p` would land you in)
has passwordless administrative rights by design, same as every other Remo
target. Run As only constrains what the *connector* process itself can do.

## Enrollment

Turn a host remo already manages into a connector from your workstation:

```bash
remo connector enroll lab \
  --activation-id <id> --region eu-central-1 \
  --expose lab/remo --expose lab/blog
```

You are prompted for the activation code on a hidden, non-echoing prompt
(pipe it on stdin instead for scripting: `echo "$CODE" | remo connector
enroll ...`). The code is never a flag and never an environment variable; it
travels to Ansible only through a private, owner-only (`0600`) temporary
vars file, deleted on every exit path, and every task that touches it sets
`no_log: true`. On the operator's workstation the code never appears in a
process argument, shell history, log line, or automation output. **One
documented residual**: AWS's own `amazon-ssm-agent -register` command takes
the code as a process argument for the seconds it runs — visible in `ps` —
but only *on the connector itself*, a machine you control; neither AWS tool
reads the code from stdin.

Enrollment installs/verifies SSM Agent, registers the node, creates the
dedicated non-admin run-as user, installs a pinned `remo` for that user with
its own private registry, exposure file, and SSH identity, and authorizes
that identity on every exposed host (scanning and trusting each host's key
from the *connector's own* network vantage point on first enrollment — a
later key change is a hard error you resolve, never a silent overwrite).
Re-running enrollment with the same inputs is a no-op; re-running with a
different `--expose` set converges the exposure file to that set.

**Self-target port (known limitation)**: when the connector exposes itself,
its registry entry is `localhost` with the port *you* registered for the
connector. If you reach the connector through a NAT or port-forward (say
2222 forwarded to 22), `localhost:2222` will not answer on the connector
itself; register the host with its real local port, or wait for the
`--self-port` follow-up tracked in the manual-gate issue (#TBD).

`--remo-version` pins a specific released `remo-cli`; `--remo-source` installs
from a PEP 508 / git spec instead (Constitution IX Tier 1 testing) and is
mutually exclusive with `--remo-version`.

### Status and revocation

```bash
remo connector status lab      # agent state, managed-node id, region, exposed pairs
remo connector unenroll lab    # stop + disable the agent, remove local state
```

`unenroll` never touches your AWS account — it prints the exact
`aws ssm deregister-managed-instance` command to run afterward, and notes
that an already-registered node stays registered even after its activation
expires, so deregistration is a deliberate, separate step.

To immediately cut off an active session rather than wait for it to end:

```bash
aws ssm terminate-session --session-id <id>
```

## Reference deployment shapes

| Shape | Verified | Not yet verified |
|-------|----------|-------------------|
| Dedicated connector in a Proxmox LXC, reaching several Proxmox/Incus hosts on the same LAN | The role's structure (`.deb` install path, `no_log` on every activation-code task, `default()` on registered variables, the host-key trust logic) is statically tested and the playbooks pass `--syntax-check` | Any live run of the enrollment automation (including a second-run idempotency check), live SSM registration, attach through a real session, agent persistence in an **unprivileged** LXC across container restarts (`/etc/machine-id`/`/var/lib/amazon/ssm/registration` persistence is a known AWS troubleshooting failure mode and SSM Agent inside an unprivileged LXC is **not an AWS-documented configuration**) — tracked in issue #TBD |
| Connector directly on an isolated Hetzner host, targeting itself (self-target) | The self-target `host: "localhost"` registry mapping and the launcher's ordinary (non-special-cased) loopback SSH path are unit-tested | Live enrollment and attach, and whether a custom `InteractiveCommands` document honors Run As on a real hybrid node — tracked in issue #TBD |

Both shapes' live proof, plus resize/Unicode/full-screen-TUI checks and the
run-as identity proof, are the manual gate (SC-008) — tracked in issue #TBD
and recorded here once it runs.

## Cost

Session Manager sessions on hybrid-activated (non-EC2) managed nodes are
billed per AWS's published pricing — see
<https://aws.amazon.com/systems-manager/pricing/> for current rates; this
document does not hardcode a number.

## Troubleshooting by error code

| Code | Cause | Fix |
|------|-------|-----|
| `bad-target` | The `target` value didn't decode to the v1 JSON shape | Re-encode it: `base64.urlsafe_b64encode(json.dumps({"v":1,"host":H,"project":P}, separators=(",",":")).encode()).rstrip(b"=")` |
| `unsupported-version` | The client sent a newer `v` than this connector's `remo` speaks | Update `remo` on the connector, or use a client that speaks the connector's document version |
| `invalid-name` | The decoded host or project fails Remo's own validators, or the project exceeds the 255-byte contract cap | Fix the name |
| `not-exposed` | The host/project pair (or the whole exposure set) isn't exposed on this connector | `remo connector enroll <connector> ... --expose HOST/PROJECT` |
| `run-as-not-in-effect` | The session ran as uid 0 or `ssm-user` — Run As isn't taking effect | Configure Session Manager Run As (preference or `SSMSessionRunAs` tag) to the connector's run-as user, exactly as `remo connector status` reports it |
| `config-unreadable` | A connector state file is missing, unreadable, or malformed (path named in the message) | Re-run `remo connector enroll` |
| `ssh-missing` | No `ssh` executable on the connector's `PATH` | `apt install openssh-client` on the connector |

`InteractiveCommands` session documents are documented by AWS as supported
only from the AWS CLI's Session Manager plugin (not a browser data-channel
client) — noted here because a future browser-direct transport depends on
that changing or being worked around; tracked in issue #TBD.

## Docker (deferred)

Running the connector itself as a Docker container is not proven by this
feature; it is explicitly deferred — tracked in issue #TBD.
