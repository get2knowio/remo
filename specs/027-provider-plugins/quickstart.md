# Quickstart: validating provider plugin discovery

```bash
uv sync --all-extras                      # installs the in-repo fixture plugin (dev extra)
uv run remo --help | grep -E '^  (incus|proxmox|aws|hetzner|fixture|providers)'   # five groups + providers
uv run remo providers                     # table incl. "fixture  remo-fixture-provider 0.1.0  loaded"
REMO_DISABLE_PROVIDER_PLUGINS=1 uv run remo --help | grep -c fixture   # 0
uv run remo web check --skip-instance-checks | grep provider_plugins   # PASS line (needs web extra)
uv run pytest tests/unit/core/test_provider_plugins.py tests/unit/core/test_registry_plugin_types.py tests/unit/core/test_reconcile.py tests/unit/core/test_registry_format.py tests/unit/cli/test_providers_cmd.py tests/integration/test_fixture_plugin_e2e.py -q
uv run pytest tests/unit/test_architecture.py tests/unit/test_docs_structure.py tests/unit/test_schema_drift.py tests/unit/cli/test_main.py -q
uv run ruff check src/remo_cli && uv run mypy src/remo_cli && uv run pytest --tb=short -q
```
