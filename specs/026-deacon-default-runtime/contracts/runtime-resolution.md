# Contract: runtime resolution (`ansible/tasks/resolve_devcontainer_runtime.yml`)

Included first by `tasks/configure_dev_tools.yml` (before `user_setup`). Inputs: `devcontainer_runtime` (default `auto`), `remo_user`, `docker_nested_overlayfs` (group_vars, fact-derived). Outputs (set_fact): `devcontainer_runtime_effective` ∈ {`deacon`,`devcontainer`}, `devcontainer_runtime_source` ∈ {`explicit`,`marker`,`legacy`,`nested-overlayfs`,`default`}.

Rules, first match wins:

| # | Condition | Effective | Source |
|---|-----------|-----------|--------|
| 1 | `devcontainer_runtime in ['deacon','devcontainer']` | that value | `explicit` |
| 2 | marker file exists and trimmed content ∈ {`deacon`,`devcontainer`} | content | `marker` |
| 3 | reference CLI installed: `npm prefix -g` rc 0 and `<prefix>/bin/devcontainer` is a file | `devcontainer` | `legacy` |
| 4 | `docker_nested_overlayfs \| default(false) \| bool` | `devcontainer` | `nested-overlayfs` |
| 5 | otherwise | `deacon` | `default` |

Probes: `stat` on the marker; `slurp` only when it exists; `command: npm prefix -g` with `failed_when: false`, `changed_when: false`; `stat` on the CLI path only when the prefix command succeeded. Every registered access uses `| default()`. A `debug`-free task named `"Devcontainer runtime: <effective> (<source>)"` (an `ansible.builtin.assert` with `quiet: true` that always passes, or a `set_fact` whose name is templated) surfaces the decision in the filtered runner.

Marker write (in `configure_dev_tools.yml`, after `include_role: user_setup`, when `configure_devcontainers | default(true) | bool`): `ansible.builtin.copy` `content: "{{ devcontainer_runtime_effective }}\n"`, `dest: /home/{{ remo_user }}/.remo-devcontainer-runtime`, `owner/group: remo_user`, `mode: '0644'`.

Warning (after the runtime roles): task named `"Devcontainer runtime deacon on a nested-overlayfs host is NOT verified — Compose builds may fail; see docs/nested-overlayfs.md"` (`ansible.builtin.debug`), `when: devcontainer_runtime_effective == 'deacon' and (docker_nested_overlayfs | default(false) | bool)`.

Consumers must read `devcontainer_runtime_effective | default(devcontainer_runtime)` and treat anything other than `deacon` as the reference CLI.
