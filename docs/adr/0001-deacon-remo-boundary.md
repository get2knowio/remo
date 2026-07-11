# ADR 0001 — The Deacon / Remo capability boundary

- **Status:** Accepted
- **Date:** 2026-07-11
- **Deciders:** Paul O'Fallon
- **Applies to:** `get2knowio/deacon`, `get2knowio/remo`
- **Scope:** Architecture only — no pricing/monetization. Lives on the
  `feature/notifier` branch (with the notifier work it governs) until that work
  reintegrates to `main`.

## Context

Both projects are ours, and they **stack**:

- **Deacon** — a local DevContainer CLI (Rust). Runs where the container runs;
  owns container creation/lifecycle. "The DevContainer CLI, minus the parts you
  don't use." Its "agent-mode" roadmap adds container-edge security (secret
  injection/scoping, egress proxy, decoy credentials, CoW workspace, Landlock,
  command policy).
- **Remo** — remote dev-environment management. Provisions hosts (incus/hetzner/
  aws), gives SSH access (`remo shell`), and runs a host-side notifier that
  delivers agent approvals out-of-band (Telegram) to an absent human.
- **Remo runs Deacon** to launch containers on its remote hosts (tracked as the
  deacon-migration work, remo#41).

Overlapping ambitions in agent security (secrets, egress, approvals) created
recurring "which repo does this belong in?" ambiguity, and some Remo work had
begun creeping into Deacon's domain (see the creep audit below).

## Decision

### Principle: **Deacon exposes, Remo externalizes.**

A capability lives in **Deacon** if it exists at the **container edge** — valuable
even if you never leave your laptop. It lives in **Remo** only as the act of
**projecting that capability across the remote boundary to a human who isn't
there.** Remo never reimplements the capability; it transports it, and picks the
transport by the payload:

| Deacon surfaces… | Remo externalizes it via… |
|---|---|
| an **approval decision** (async human attention) | out-of-band push — the notifier (Telegram/…) |
| a **forwarded port** / interactive I/O (byte stream) | SSH — tunneled to whoever's on `remo shell` |
| an **audit stream** (events) | aggregation + remote surfacing |
| an **exec / shell** | `remo shell` over SSH |

`remo shell` and the notifier are the *same kind of thing* — two transports on
Remo's externalization plane, differing only because their payloads differ.

### The 3-axis test (apply all three; they rarely disagree)

1. **Local-valuable?** Would I want this if the agent ran in a container on my own
   laptop, right in front of me? **Yes → Deacon.**
2. **Human present or reached?** Reached (push/async/phone) → **Remo**. Present
   (terminal prompt) → **Deacon**.
3. **One or many?** One container in front of me → **Deacon**. Many on a host I
   operate but don't sit at → **Remo**.

### The seam

The dividing line runs through the **approval/audit event**: whoever **decides and
enforces** is Deacon (or agentsh); whoever **delivers the decision to an absent
human and remembers it across a fleet** is Remo.

## Consequences

### Capability placement

**Deacon owns (container-edge enforcement substrate):**
- Exec-time secret injection + provider-agnostic resolution.
- Per-command secret/env scoping.
- Egress proxy with domain/port allowlist + audit.
- Decoy credential substitution at the egress edge.
- Copy-on-write workspace with gated write-back.
- Landlock self-restriction exec wrapper.
- Command policy engine (allow/deny/approve) at the exec choke point.
- **Audit event emission.**

**Remo owns (externalization + minimal fleet state):**
- The **notifier** — out-of-band approval relay.
- **Grants as memory** — remembering a human's decision so it isn't re-asked.
- Multi-source registry + per-source delivery (many containers on one host).
- The socket-watching **controller** — *as a stopgap* for a Deacon enrollment
  hook that doesn't exist yet (see open questions).
- **Cross-project host network topology** (isolate projects; keep the notifier
  reachable) — not per-container network *creation*.
- **Audit aggregation + remote surfacing.**
- **Port-forward relay over SSH** for `remo shell`.

### Corollaries

- **Remo consumes a *generic* approval event** — pluggable source. It should relay
  an approval identically whether it originated from agentsh or from Deacon's
  command policy. Remo must not care which enforcement engine raised it.
- **agentsh's role narrows** to the syscall-level fidelity Deacon deliberately
  won't build (e.g. FUSE per-op file approval, per-descendant-process scoping).
  For everything else, Deacon-on-the-host can be the enforcement engine Remo
  operates.
- **remo#41 (deacon migration) is the keystone** — once Remo runs Deacon on its
  hosts, everything Deacon exposes (approvals, forwards, audit, secret hygiene)
  automatically becomes something Remo can externalize.
- **Remo never reimplements enforcement.** When Remo "remembers" a decision (a
  grant), the durable form of that decision should ideally be *pushed down* to
  Deacon/agentsh policy (e.g. an allowlist entry), not enforced inside Remo.

### The "one honest nuance"

Remo is not a pure dumb pipe. Beyond transport it owns exactly two kinds of state,
and only because they exist at "many containers on a host you don't sit at":
**memory** (grants) and **fleet host-ops** (discovery, isolation, reachability).
Everything else is relay.

## The creep audit (what this ADR corrects)

Work that had drifted toward Deacon's domain, and its correction:

- **remo#44 egress "exfiltration defense"** — the wildcard grant is *memory*
  (fine); the "exfil defense" framing implied Remo builds an egress
  allowlist/enforcer. **Correction:** Remo relays the approval + remembers the
  grant; enforcement (proxy/allowlist/decoy) is Deacon.
- **remo#42 / remo#46 per-project network *creation*** — Deacon creates the
  container and (via its egress feature) its network. **Correction:** Remo only
  *attaches its own notifier* to reach it; it does not create/assign the network.
- **remo#43 agentsh-as-a-devcontainer-Feature** — injecting enforcement *into* a
  container is feature-injection = Deacon's job, or obviated by Deacon agent-mode.
  **Correction:** re-home to Deacon or drop.
- **The controller's discovery half** — snooping Docker to discover containers
  reaches into lifecycle Deacon already owns. **Correction:** treat as temporary
  scaffolding; replace with a Deacon-exposed enrollment/approval hook when it
  exists.

Code that was correctly placed and stays: the notifier core, grants-as-memory,
`netwire` (attaches Remo's own notifier), the multi-source registry.

## Open questions

- **agentsh's long-term role** given Deacon's agent-mode reproduces much of it at
  the edge without a daemon.
- **The pluggable approval-event contract** — the exact shape Remo consumes so the
  source (agentsh vs Deacon command-policy) is transparent.
- **A Deacon enrollment hook** — the interface that would retire the controller's
  socket-watching discovery.

## Related

- Deacon agent-mode issues: exec-time secret injection, per-command scoping, egress
  proxy + allowlist, decoy credentials, CoW workspace, Landlock exec wrapper,
  command policy with approvals.
- Remo: #41 (deacon migration, keystone), #42/#43/#44/#46 (see corrections above),
  #45/#47 (notifier work, on `feature/notifier`).
