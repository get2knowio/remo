# Quickstart: validating the local host

## 1. Automated (any Linux dev box / CI)

```bash
uv sync --all-extras --no-extra hetzner --no-extra ec2
uv run --no-sync pytest tests/unit/core/test_host_access.py \
  tests/unit/providers/test_local.py tests/unit/cli/test_local_cmd.py \
  tests/unit/test_ansible_templates.py tests/unit/test_project_index.py \
  tests/unit/test_local_setup_playbook.py -q
uv run --no-sync pytest -q          # full core suite: SSH argv byte-identity included
```

Expected: all pass. The SSH characterization tests
(`tests/unit/core/test_ssh*.py`, `test_resume.py`, `test_remo_host_client.py`)
pass unchanged.

## 2. Host scripts on macOS (CI job `host-scripts-macos`)

```bash
brew install bash zellij fzf
uv run --no-sync pytest tests/unit/test_ansible_templates.py \
  tests/unit/test_project_index.py tests/unit/test_project_launch_tab_record.py \
  tests/unit/test_zellij_socket_dir.py -q
```

Expected: pass on `macos-latest` (BSD `sed`/`date`/`df`/`find`, no
`/run/user`, no `setsid`/`timeout`), with host stats reporting Darwin values or
`unavailable`.

## 3. Manual acceptance (on a Mac — the spec's gate)

Prereqs: Docker Desktop or OrbStack running; `brew install bash zellij fzf
deacon` (or the reference `devcontainer` CLI); macOS Remote Login **off**.

```bash
uv tool install "remo-cli @ <branch wheel or git ref>"
remo local check                       # all required tools present
remo local setup                       # installs scripts; registers host "local"
remo local setup                       # second run: "Already up to date"
git diff --no-index /dev/null ~/.zshrc # unchanged (compare to a pre-run copy)
remo local add ~/code/a
remo local add ~/work/b
remo local list                        # a, b — both ok, added in place
remo shell                             # menu lists a and b
#  → pick a: lands in a's zellij session inside its devcontainer
#  → detach (Ctrl-o d)
remo resume                            # same tab: back in a, no prompt
remo shell local -p b --exec "git status"   # runs inside b's devcontainer
remo shell local -p b --detach         # returns; b's session running
remo shell local -L 8080:8080          # refused: ports are already local
remo cp ./x local:/tmp                 # refused: copy files directly
remo shell <a remote host>             # unchanged behaviour
remo web push <deployment>             # payload omits the local host
```

Retiring the VM: copy or clone each project to the Mac, `remo local add` it,
then `remo remove <vm-name>`.
