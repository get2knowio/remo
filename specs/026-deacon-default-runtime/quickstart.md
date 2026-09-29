# Quickstart: validating the deacon default

## A. Gates

```bash
uv run pytest -q                                   # incl. tests/ansible/test_devcontainer_runtime_resolution.py, test_deacon_role.py, test_ansible_templates.py, test_devcontainer_shim.py, test_docs_structure.py
uv run ruff check src/remo_cli && uv run mypy src/remo_cli
grep -rn '\.rc ==\|\.rc !=' ansible/ | grep -v 'default(' ; grep -rn '\.stdout' ansible/ | grep -v 'default(' | grep -v '^ansible/README'   # expect nothing
cd ansible && uv run ansible-playbook --syntax-check proxmox_configure.yml incus_configure.yml hetzner_configure.yml aws_configure.yml ssh_configure.yml -e remo_ssh_host=h -e remo_ssh_user=u -e remo_ssh_port=22
uv run remo proxmox create --help | grep -A2 devcontainer-runtime   # auto | deacon | devcontainer, default auto
```

## B. Binary verification (what R2 did)

```bash
gh release download v0.4.0 -R get2knowio/deacon -p 'deacon-v0.4.0-aarch64-apple-darwin.tar.gz' -D /tmp/deacon && tar -C /tmp/deacon -xzf /tmp/deacon/*.tar.gz
/tmp/deacon/deacon up --help | grep -E 'remove-existing-container|build-no-cache|trust-workspace-persist|workspace-folder'
/tmp/deacon/deacon exec --help | grep workspace-folder
```

## C. Manual gate (SC-005, tracked; needs hosts)

1. Fresh host, no flag: `remo <type> create …` → on the host `cat ~/.remo-devcontainer-runtime` = `deacon`; `deacon --version` = 0.4.0; `remo-host capabilities --json` lists `projects.rebuild`; `remo shell -p <project>` launches; `remo web` attaches; Rebuild from the console works.
2. `remo <type> upgrade` / `remo configure` again → no changes reported for the deacon role; marker unchanged.
3. Legacy host (reference CLI, no marker): upgrade with the default → stays on the reference CLI; marker = `devcontainer`.
4. OrbStack host, default → reference CLI + shim; forced `--devcontainer-runtime deacon` → warning task printed; build a Compose-based devcontainer with `BUILDX_BUILDER=remo-native DOCKER_BUILDKIT=1 COMPOSE_BAKE=1` exported; record the outcome in `docs/nested-overlayfs.md` and the tracked issue.
