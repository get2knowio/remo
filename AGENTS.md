# Agent Instructions

This repository's canonical agent guidance lives in [`CLAUDE.md`](./CLAUDE.md).

Use `CLAUDE.md` as the source of truth for repository-specific conventions, architecture, commands, and implementation rules when working in this codebase.

The current credential-broker feature provisions a managed `_remo-vault` project alongside normal workspaces; use `remo shell -p _remo-vault` for sidecar-specific flows. Normal projects get a generated devcontainer config at launch time so the checked-in config is not mutated when the read-only manifest mount and broker socket are injected.
