# Quickstart: Python CLI Rewrite

**Feature Branch**: `003-python-cli-rewrite`
**Date**: 2026-02-28

## Prerequisites

- Python 3.11+
- git
- pip or uv

## Development Setup

```bash
# Clone and checkout feature branch
git clone <repo-url> && cd remote-coding
git checkout 003-python-cli-rewrite

# Install in editable mode with all optional dependencies and dev tools
pip install -e ".[all,dev]"

# Or with uv (faster)
uv pip install -e ".[all,dev]"

# Verify installation
remo --version
```

## Running Tests

```bash
# All tests
pytest

# Unit tests only
pytest tests/unit/

# Specific module
pytest tests/unit/core/test_known_hosts.py

# With coverage
pytest --cov=remo --cov-report=term-missing
```

## Project Layout

```
src/remo/cli/          → Click command definitions (parsing only)
src/remo/providers/    → Business logic per provider (no CLI dependency)
src/remo/core/         → Shared utilities (SSH, config, output, ansible runner)
src/remo/models/       → Data classes (KnownHost, etc.)
tests/                 → Mirrors src/ structure
ansible/               → Unchanged Ansible playbooks (not part of Python package)
```

## Key Development Patterns

### Adding a new CLI command

1. Create the Click command in `src/remo/cli/` (or `cli/providers/` for provider subcommands)
2. Implement business logic in `src/remo/providers/` or `src/remo/core/`
3. Wire the command into the group in `cli/main.py`
4. Add tests in `tests/unit/cli/` and `tests/unit/providers/`

### Testing subprocess calls

All subprocess invocations (SSH, rsync, ansible-playbook, incus CLI) go through standard `subprocess.run`/`subprocess.Popen`. Tests mock these at the call site:

```python
def test_ssh_connect(mocker):
    mock_run = mocker.patch("subprocess.run")
    # ... invoke the function ...
    mock_run.assert_called_once_with(["ssh", ...], ...)
```

### Testing CLI commands

Use Click's `CliRunner` for in-process testing:

```python
from click.testing import CliRunner
from remo.cli.main import cli

def test_version():
    runner = CliRunner()
    result = runner.invoke(cli, ["--version"])
    assert result.exit_code == 0
    assert "remo" in result.output
```

## Dependency Installation Variants

```bash
pip install -e "."            # Core only (Incus works, no cloud SDKs)
pip install -e ".[aws]"       # Core + boto3
pip install -e ".[hetzner]"   # Core + hcloud
pip install -e ".[all]"       # Core + all provider SDKs
pip install -e ".[all,dev]"   # Everything + pytest, ruff, mypy
```
