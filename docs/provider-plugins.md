# Provider plugins

remo's providers are declared, not special-cased: every provider is a
metadata-only **descriptor** registered with `core/provider_registry.py`, and
the CLI groups, `remo shell`, completion, snapshots, the registry and the web
console all operate on descriptors. Since spec 027 that registration is open to
**any installed Python distribution** through the `remo.providers` entry-point
group — no edit to remo is needed to add a provider.

Provider API version: **1** (`remo_cli.core.provider_registry.PROVIDER_API_VERSION`).
Compatibility across remo versions is not promised: pin the exact remo version
your plugin was built against.

## What a provider distribution contains

```
acme-provider/
├── pyproject.toml
└── src/acme_provider/
    ├── __init__.py
    ├── descriptor.py     # metadata only — no SDK imports
    └── provider.py       # the implementation, imported lazily on first use
```

### 1. The descriptor module (metadata only)

```python
# src/acme_provider/descriptor.py
from remo_cli.core.provider_registry import (
    NAME, REGION, ConnectionSpec, NameFormat, ProviderDescriptor,
)

REMO_PROVIDER_API_VERSION = 1   # the API this plugin targets

DESCRIPTOR = ProviderDescriptor(
    type_name="acme",                       # lowercase; the CLI group name and registry `type`
    display_name="Acme",
    default_instance_name="acme1",
    name_format=NameFormat.FLAT,            # or HOST_SCOPED for "host/container" names
    registry_fields=(("instance_id", "box_id"), ("region", "zone")),
    connection=ConnectionSpec(),            # see "SSH connections" below
    implementation="acme_provider.provider",
    create_options=(NAME, REGION),
    sync_options=(REGION,),                 # declares `--region` for `remo acme sync`
    region_scoped_sync=True,                # sync scopes by region (like AWS)
)
```

Rules the built-ins follow and plugins must too:

- **No SDK imports in the descriptor module.** It is imported at discovery
  time by every remo command, including `--help` and shell completion.
- `registry_fields` maps `KnownHost` attributes (`instance_id`, `region`) to
  the nested JSON keys under `registry.json`'s `"<type_name>": {...}` block.
  The registry serializes **and parses** through this declaration, so your
  entries round-trip on any machine that has the plugin installed. On a machine
  without it, the entry is preserved verbatim and reported, never dropped.
- `registry_legacy_keys` (optional) lists `(old_json_key, attribute)` pairs to
  read when the current key is absent — a lazy key migration.
- `ConnectionSpec` says how remo reaches your instances; see
  [Connection hooks](#connection-hooks).
- `sync_scope_description` (optional) is a `str.format` template with `{host}`
  / `{region}` used by `remo <type> sync`'s plan header.
- Shared options (`NAME`, `HOST`, `REGION`, `VOLUME_SIZE`, …) come from the
  catalog in `core/provider_registry.py`; reuse them so flags stay identical
  across providers (`dataclasses.replace` for a different default).

### 2. The implementation module

Imported lazily by `get_provider()` the first time a verb needs it. It must
satisfy `remo_cli.core.provider_protocol.Provider` — `update_entry`,
`teardown`, `probe`, `snapshot_create/restore/delete/list` — plus the
functions the generated verbs call: `create`, `destroy` (via `teardown`),
`upgrade`, `resize`, `list_hosts`, `info`, `sync` (via `probe`), and any
`CommandSpec.impl` you declare. Raise `remo_cli.core.errors` types
(`PreconditionError`, `OperationFailedError`, `MissingDependencyError`,
`UserAbortedError`); never call `sys.exit`. Registry writes go through
`core.known_hosts` / `core.registry`.

If your implementation needs an optional SDK, importing it lazily inside the
implementation module is enough: an `ImportError` there becomes a
`MissingDependencyError` naming `sdk_extra` when set.

### 3. The entry point

```toml
# pyproject.toml
[project.entry-points."remo.providers"]
acme = "acme_provider.descriptor:DESCRIPTOR"
```

The object may also be a zero-argument callable that returns the descriptor.

### Connection hooks

Everything remo does with a host — `remo shell`, `remo resume`, `remo cp`,
discovery, the web terminal — goes over SSH. `ConnectionSpec` is how a provider
changes that path without core knowing its name. Both hooks are dotted paths,
imported only when a host of your type is being connected to, so your
descriptor stays importable without your SDK:

```python
from remo_cli.core.provider_registry import ConnectionSpec, SshProxyPlan

connection=ConnectionSpec(
    mode_field_aware=True,
    proxy_hook="acme_provider.provider.ssh_proxy_hook",
    pre_connect_hook="acme_provider.provider.start_if_stopped",
)
```

- `mode_field_aware=True` opts the type into the registry's `access` field
  (`direct` / `ssm`); without it every entry is `direct`.
- `proxy_hook` — `(KnownHost) -> SshProxyPlan | None`. Return `None` to connect
  straight to `user@host`. Return an `SshProxyPlan(proxy_command, ssh_target,
  extra_opts=())` and every ssh argv remo builds for that host uses
  `-o ProxyCommand=<proxy_command>`, connects to `ssh_target` instead of
  `user@host`, and appends `extra_opts`. The built-in AWS provider's
  `ssh_proxy_hook` (an `aws ssm start-session` ProxyCommand) is the worked
  example.
- `pre_connect_hook` — `(KnownHost) -> KnownHost`. Runs before `remo shell`
  and `remo resume` talk to the host. Use it to start a stopped instance and
  return the refreshed entry (a new address); return the host unchanged when
  there is nothing to do. Raise `remo_cli.core.errors` types — a
  `PreconditionError` is printed and becomes exit 1. AWS's
  `auto_start_aws_if_stopped` is the worked example.

Both are SSH-shaped. A provider whose instances are not reachable over SSH at
all has no seam yet (#223).

### 4. Shipping Ansible playbooks

A provider that provisions or configures hosts ships its own playbooks inside
its package and runs them with
`remo_cli.core.ansible_runner.run_playbook(name, extra_vars, playbook_dir=DIR)`:

```python
PLAYBOOK_DIR = Path(__file__).resolve().parent / "ansible"
rc = run_playbook("acme_site.yml", extra_vars, verbose=verbose, playbook_dir=PLAYBOOK_DIR)
```

What remo does with `playbook_dir`:

- Installs `DIR/requirements.yml` (if present) once, and again whenever it
  changes, under a marker of its own — after remo's own requirements, so list
  only the collections you add.
- Runs `ansible-playbook DIR/<name>` **from remo's `ansible/` directory**, so
  remo's `ansible.cfg` applies and remo's shared roles (`user_setup`, `docker`,
  `zellij`, …) resolve by name. Roles in `DIR/roles/` resolve too (Ansible looks
  next to the playbook).
- Passes `remo_core_ansible_dir` (an extra var) pointing at remo's `ansible/`.

What your playbooks must do, because Ansible resolves relative paths and
`group_vars/` against the playbook's own directory, not remo's:

- Reach remo's shared tasks absolutely:
  `include_tasks: "{{ remo_core_ansible_dir }}/tasks/configure_dev_tools.yml"`.
- Load remo's shared defaults (`remo_user`, `nodejs_version`, …) in every play:
  `vars_files: ["{{ remo_core_ansible_dir }}/group_vars/all.yml"]`.
- Put your own defaults in `DIR/group_vars/all.yml`, namespaced (`acme_*`).
  `vars_files` outranks `group_vars`, so a name you share with remo's file
  would silently take remo's value.

For remo's own maintainers, the other half of the contract: a shared task file
in remo's `ansible/tasks/` that includes another must spell it
`"{{ remo_core_ansible_dir | default(playbook_dir) }}/tasks/x.yml"`, never
`tasks/x.yml` — a relative include resolves against the plugin's playbook
directory and fails only at run time (`--syntax-check` does not follow dynamic
includes). `tests/ansible/test_shared_tasks_location_independent.py` enforces it.

The Hetzner provider (`plugins/hetzner` in remo's repository) is the
complete, production example, including tests that pin this contract
(`tests/ansible/test_playbook_layout.py`).

## Discovery rules

- Built-ins register first (incus, proxmox, aws), then entry points
  in **entry-point-name order**, once per process.
- A plugin that fails to load (import error, wrong object type, descriptor
  validation error) is **skipped with one warning** naming the distribution
  and entry point. remo keeps working; the command's exit code is unaffected.
- A duplicate `type_name` (against a built-in or an earlier plugin) keeps the
  **first** registration and skips the later one with a warning naming both
  distributions.
- A different or missing `REMO_PROVIDER_API_VERSION` warns and still registers.
- `REMO_DISABLE_PROVIDER_PLUGINS=1` skips entry-point discovery entirely.

## Seeing what was discovered

```bash
remo providers          # TYPE / NAME / SOURCE / STATUS, plus skipped plugins with reasons
remo web check          # a `provider_plugins` line (always PASS; lists loaded and skipped)
```

## The web console

The console's OpenAPI contract enumerates only the built-in types
(`KnownProviderType`); a plugin's instances are served with their type as a
plain string and the console presents them through its runtime fallback. That
is by design (spec 020 FR-004a): installing a plugin must never change the
generated artifacts.

The service shows a host only when its type's provider is installed *in the
service's own environment*. The published `remo-web` image installs every
first-party plugin; a third-party plugin needs an image built with it
installed, or its hosts are left out of the console (the registry keeps them).

## First-party plugins

Hetzner ships this way: `plugins/hetzner` is the `remo-provider-hetzner`
distribution, a uv workspace member of remo's repository with its own
version, wheel, entry point and test suite, installed by `remo-cli[hetzner]`.
It gets nothing a third-party plugin cannot: it is discovered through the same
entry point, its type is an off-union string to the web console (the console
keeps its colour through a hand-written map), and `REMO_DISABLE_PROVIDER_PLUGINS=1`
hides it like any other plugin.

## Testing your plugin

The in-repo fixture distribution `tests/fixtures/remo_fixture_provider/` is a
complete, minimal example (descriptor + implementation + entry point); remo's
own suite installs it through the `dev` dependency group (`uv sync`) and runs
`tests/integration/test_fixture_plugin_e2e.py` against it. Copy it, rename the
type, and start from there.
