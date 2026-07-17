#!/usr/bin/env bash
# docker/entrypoint.sh
#
# Container entrypoint for `remo web` (010-web-session-interface, US4).
# Non-root-safe: runs as whatever user the container was started as
# (see compose.example.yml `user: "1000:1000"`), no root-only operations.
#
# `set -euo pipefail` + running `remo web check --skip-instance-checks`
# unguarded means a non-zero exit from the CONFIG gate aborts this script
# before `exec remo web serve` is ever reached — i.e. the container fails
# fast and never starts serving on bad config/missing mounts.
#
# `--skip-instance-checks` is deliberate: the startup gate validates only
# config/mounts/executables, NOT per-instance reachability. A registered
# instance that is merely powered off or unreachable must NOT prevent the
# whole service from starting (FR-006/US1 — unreachable instances stay
# visible with actionable status; they don't block boot). Under
# `restart: unless-stopped` (compose.example.yml), gating on reachability
# would crash-loop the container whenever any single instance is down.
# Full per-instance diagnostics remain available via `remo web check` (no
# flag), and readiness is (correctly) config-only via GET /api/v1/ready.
#
# 011-web-adopt (SC-006): *unconfigured* is a PASSING state for this gate.
# A fresh writable state volume with no registry/key material yet makes
# `remo web check` report "awaiting adoption — run `remo web adopt`" and
# exit 0, so an adopted-mode container boots cleanly (no crash loop under
# `restart: unless-stopped`) while it waits for adoption. Only genuinely
# broken configuration (mounted artifacts present but unusable, missing
# runtime prerequisites) still aborts here.
#
# `exec` replaces this script's process (PID 1) with `remo web serve` so it
# receives signals (SIGTERM) directly, which graceful shutdown depends on.

set -euo pipefail

remo web check --skip-instance-checks
exec remo web serve
