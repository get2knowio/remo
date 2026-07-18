#!/usr/bin/env bash
# docker/entrypoint.sh
#
# Container entrypoint for `remo web` (010-web-session-interface, US4;
# 011-web-adopt).
#
# Self-healing permissions model ("start as root, fix perms, drop
# privileges"). The image starts as root (no `USER remo` in the Dockerfile)
# so this script can, when it is UID 0, repair ownership on:
#
#   * the config dir ($REMO_HOME, default $HOME/.config/remo) — a deployer
#     that BIND-MOUNTS this dir gets it owned root:root, so the non-root
#     `remo` process could not create web-identity/ there (adopted mode,
#     011-web-adopt) and would fail with EACCES on first boot. Docker seeds a
#     fresh NAMED volume root-owned too, so the same fix covers that path.
#   * the SSH ControlMaster runtime dir (/run/remo-ssh) — an option-less
#     tmpfs is remounted root-owned 0755 (unwritable to `remo`) by a container
#     RESTART, which otherwise fails the startup runtime-dir check on every
#     boot after the first. Re-healing it on EVERY start makes restarts behave
#     like the first boot without the deployer pinning `rw,mode=1777`.
#
# After healing, it drops to the unprivileged `remo` user via `gosu` and
# re-execs itself; that second pass takes the non-root branch below (the gate
# + serve). `gosu` keeps us as PID 1 with correct signal semantics (SIGTERM
# reaches uvicorn for graceful shutdown) and, unlike a setuid `su`, adds no
# privilege-escalation surface — compatible with `no-new-privileges:true`.
#
# Healing is BEST-EFFORT and never hard-fails startup: a read-only config
# mount (mount-configured mode) legitimately cannot be chowned, and a deployer
# may drop CAP_CHOWN. `remo web check` (below) is the real gate on whether the
# resulting filesystem is actually usable. If the container is started with an
# explicit non-root `--user`/`user:`, this script is never UID 0, so it skips
# healing entirely and just runs the gate + serve.
#
# The `remo web check --skip-instance-checks` gate runs unguarded under
# `set -e`: a non-zero exit from the CONFIG gate aborts before `exec remo web
# serve`, so the container fails fast on genuinely bad config/mounts and never
# starts serving. `--skip-instance-checks` is deliberate: the startup gate
# validates only config/mounts/executables, NOT per-instance reachability. A
# registered instance that is merely powered off or unreachable must NOT
# prevent the whole service from starting (FR-006/US1); under `restart:
# unless-stopped` (compose.example.yml), gating on reachability would
# crash-loop the container whenever any single instance is down. Full
# per-instance diagnostics remain available via `remo web check` (no flag),
# and readiness is (correctly) config-only via GET /api/v1/ready.
#
# 011-web-adopt (SC-006): *unconfigured* is a PASSING state for this gate. A
# fresh writable state volume with no registry/key material yet makes `remo
# web check` report "awaiting adoption" and exit 0, so an adopted-mode
# container boots cleanly (no crash loop) while it waits for adoption. Only
# genuinely broken configuration still aborts here.

set -euo pipefail

# Target uid/gid to drop to. Defaults to the image's baked-in `remo` user
# (1000:1000); override PUID/PGID for a bind-mount host whose directory owner
# is some other uid/gid.
PUID="${PUID:-1000}"
PGID="${PGID:-1000}"

# Paths that must be writable by the dropped-to user. CONFIG_DIR mirrors
# core/config.py's REMO_HOME resolution; CONTROL_DIR mirrors
# WebSettings.ssh_control_dir (REMO_WEB_SSH_CONTROL_DIR, default /run/remo-ssh).
CONFIG_DIR="${REMO_HOME:-${HOME:-/home/remo}/.config/remo}"
CONTROL_DIR="${REMO_WEB_SSH_CONTROL_DIR:-/run/remo-ssh}"

if [ "$(id -u)" -eq 0 ]; then
    # Root (the image default): self-heal, then drop. Every step is
    # best-effort (`|| true`) so a read-only mount or a dropped CAP_CHOWN can
    # never abort startup — `remo web check` below is the authority.
    mkdir -p "$CONFIG_DIR" 2>/dev/null || true
    mkdir -p "$CONTROL_DIR" 2>/dev/null || true
    chown -R "$PUID:$PGID" "$CONFIG_DIR" 2>/dev/null || true
    # chmod BEFORE chown so it runs while root still owns the tmpfs (no
    # CAP_FOWNER needed). 1777 (sticky, world-writable) already lets the
    # dropped-to user create control sockets, so the chown that follows is
    # belt-and-suspenders — harmless if it can't change ownership.
    chmod 1777 "$CONTROL_DIR" 2>/dev/null || true
    chown "$PUID:$PGID" "$CONTROL_DIR" 2>/dev/null || true
    # Re-exec this same script as the unprivileged user; the second pass falls
    # through to the gate + serve below.
    exec gosu "$PUID:$PGID" "$0" "$@"
fi

# Unprivileged: either dropped from root above, or started with an explicit
# non-root `user:` (healing skipped, by design).
remo web check --skip-instance-checks
exec remo web serve
