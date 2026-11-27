# Quickstart: validating spec 030

## 1. Automated

```bash
uv sync --all-extras --no-extra hetzner --no-extra ec2
uv run --no-sync pytest tests/unit/core/test_host_probe.py \
  tests/unit/core/test_host_access_ssh_baseline.py tests/unit/test_session_dir_pin.py \
  tests/unit/test_host_info.py tests/unit/providers/test_ssh_resume_optin.py \
  tests/unit/providers/test_projects.py tests/unit/cli/test_projects_cmd.py -q
uv run --no-sync pytest -q
```

## 2. macOS CI (`host-scripts-macos`)

The job's list includes `test_session_dir_pin.py` and `test_host_info.py`.

## 3. Manual (Mac B with Remote Login on, second machine C)

```bash
# on B
remo local setup --allow-ssh-resume     # explains, asks for sudo once
remo local setup --allow-ssh-resume     # "already enabled", no sudo
remo shell local -p X                   # start X; detach
# on C
remo add hostb you@b.local
remo shell hostb                        # menu opens, X shown live; pick X → same session
remo shell hostb -p Y --exec "git status"
remo configure hostb                    # refused: run `remo local setup` on hostb
remo projects list hostb
remo projects add hostb '~/code/z'   # quoted: expanded on hostb, not here
remo resume                             # in a C tab used before → back in its project on B
# on B
remo shell local -p X                   # same session again
remo local setup --no-allow-ssh-resume  # removes the drop-in
```
