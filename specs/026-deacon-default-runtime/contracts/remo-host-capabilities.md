# Contract: `remo-host` capabilities and `projects rebuild` per runtime

`remo-host.sh.j2` computes once, near `DEVCONTAINER_BIN`:

```bash
# Runtimes whose `up` accepts --remove-existing-container (verified: the
# reference CLI, and deacon >= 0.4.0). A runtime not in this list is
# advertised honestly as lacking projects.rebuild and refused at call time.
case "$DEVCONTAINER_BIN" in
    devcontainer|deacon) REBUILD_SUPPORTED=true ;;
    *)                   REBUILD_SUPPORTED=false ;;
esac
```

- `capabilities --json`: `operations` includes `"projects.rebuild"` iff `$REBUILD_SUPPORTED == true`.
- `projects rebuild`: if not supported → stderr `remo-host: unsupported subcommand: projects rebuild (requires --remove-existing-container, this host uses $DEVCONTAINER_BIN)`, exit 4; otherwise unchanged (`--json` required → exit 2; `--project` required → exit 2; name validation → exit 3; then the detached job execs `$DEVCONTAINER_BIN up --workspace-folder DIR --remove-existing-container [--build-no-cache]`).
- `PROTOCOL_VERSION` stays 1.

Tests (`tests/unit/test_ansible_templates.py`): render with `deacon` → `projects.rebuild` present and `projects rebuild --project beta` (no `--json`) exits 2 (past the runtime check); render with `nope` → absent and exit 4 with empty stdout.
