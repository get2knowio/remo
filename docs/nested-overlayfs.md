# Building containers where nested overlayfs is refused (OrbStack)

Some hosts run remo perfectly — `remo configure` completes, `remo-host`
responds, `remo web` shows the instance healthy — and then cannot build a single
devcontainer. Every BuildKit build dies on the first `RUN`:

```text
ERROR: failed to build: failed to solve: process "/bin/sh -c …" did not complete successfully:
mount source: "overlay", target: "/var/lib/docker/buildkit/containerd-overlayfs/cachemounts/buildkit1256162528",
fstype: overlay, … err: operation not permitted
```

This page explains why, what `remo configure` does about it, and what it cannot
fix. Filed as [#160](https://github.com/get2knowio/remo/issues/160) (the
builder) and [#171](https://github.com/get2knowio/remo/issues/171) (the Compose
half).

## Which hosts

An [OrbStack](https://orbstack.dev) "machine" is a container on a shared kernel,
not a VM — its root is a btrfs subvolume under `/scon/containers/<id>/rootfs`.
Docker installed inside one is therefore *nested*, and a nested overlayfs mount
is refused by the kernel. Docker 29 makes the containerd snapshotter the
default, and its cache mount is an overlay mount, so a freshly-configured host
lands on the broken path with nothing to steer it. (containerd v1 worked only
because it fell back to another snapshotter.)

remo detects this from the kernel release:

```text
OrbStack machine : 7.0.11-orbstack-00360-gc9bc4d96ac70
Proxmox guest    : 7.0.2-2-pve
```

The variable is `docker_nested_overlayfs` (`ansible/group_vars/all.yml`). Force
the workarounds on elsewhere with:

```bash
remo configure NAME --verbose   # to see what it does
# or, driving the playbook directly:
./run.sh ssh_configure.yml -e docker_nested_overlayfs=true …
```

`/mnt/mac` is deliberately *not* used as a signal: it is absent when the machine
is created with `--isolated`, so keying off it would silently skip the fix on
exactly the configuration a user picks for a cleaner VM.

## What `remo configure` does

Two things, both from `ansible/roles/nested_docker/`, both scoped to the account
you registered (buildx state lives in `~/.docker/buildx`, so a builder created
for root would leave `project-launch` just as broken).

### 1. A `native`-snapshotter buildx builder

```bash
docker buildx create --name remo-native --driver docker-container \
    --config ~/.config/buildkit/buildkitd.toml --bootstrap --use
```

with:

```toml
[worker.oci]
  snapshotter = "native"
```

`native` copies layers instead of overlaying them. **This is materially
slower** — the export stage of one devcontainer build took 125s, and the
resulting 8.86 GB image several minutes end to end. That is the price of
building at all on this platform, not a misconfiguration.

`--use` makes it your default builder so a hand-run `docker buildx build` works
too. The shim below does not depend on that, so switching your default back is
safe.

### 2. A `devcontainer` shim at `/usr/local/bin/devcontainer`

The devcontainer CLI shells out to *two* different builders, and here their
requirements are mutually exclusive:

| step | runs | needs |
|---|---|---|
| Features build, image-based | `docker buildx build` | the `remo-native` builder (ignores `DOCKER_BUILDKIT`) |
| Features build, Compose | `docker compose build` | `DOCKER_BUILDKIT=1` **and** `COMPOSE_BAKE=1` |
| updateUID | plain `docker build` | `DOCKER_BUILDKIT=0` |

`docker compose build` *honours* `DOCKER_BUILDKIT=0` and drops to the classic
builder, which rejects the `additional_contexts` the CLI injects in order to
install Features:

```text
the classic builder doesn't support additional contexts, set DOCKER_BUILDKIT=1 to use BuildKit
```

So the one setting that rescues `updateUID` is the one that kills Compose
projects. No global value is correct for both — the shim picks per invocation,
by looking for `"dockerComposeFile"` in the project's `devcontainer.json`:

* **Compose project** → `DOCKER_BUILDKIT=1 COMPOSE_BAKE=1`, plus
  `--update-remote-user-uid-default never` on `up`/`build` (see the UID note
  below). Never on `exec` — the CLI rejects the flag there, and `project-launch`
  uses `exec`.
* **Anything else** → `DOCKER_BUILDKIT=0`, arguments untouched.
* **Both** → `BUILDX_BUILDER=remo-native`.

An explicit `--update-remote-user-uid-default` of your own is respected, not
duplicated.

#### Why `/usr/local/bin` and not `~/.local/bin`

`~/.local/bin` is added to `PATH` *inside* `~/.bashrc`, which returns early for
non-interactive shells:

```bash
case $- in
    *i*) ;;
      *) return;;
esac
```

So `ssh host 'devcontainer up …'` — what `remo web` and `remo shell -c` actually
do — would never see a shim installed there, while every interactive test of it
passed. `/usr/local/bin` is on the default `PATH` from `/etc/environment` and
precedes `/usr/bin`, so it covers interactive, login, auto-start and
non-interactive invocations alike.

The shim calls the real CLI by absolute path (`/usr/bin/devcontainer`).
`command devcontainer` would resolve through `PATH`, find the shim again, and
recurse.

#### The UID note

`updateUID` only remaps the container user when the host and image UIDs differ.
remo's `user_setup` pins the workspace account to **UID 1000** precisely so it
matches the `vscode`/`node` user in the standard devcontainer base images — so
the stage is normally a no-op that fails while mounting an overlay in order to
change nothing. That is why skipping it is safe.

If you are running as some other UID, the shim **does not** skip it — skipping
would leave bind-mounted files unwritable inside the container. It prints why
and lets the build proceed. See the UID-1000 discussion in
[`docs/examples/orbstack-cloud-init.yaml`](examples/orbstack-cloud-init.yaml).

## What this does not fix

### `apt` inside a running container

Overlayfs also breaks dpkg *inside* containers on such a host:

```text
dpkg: error processing archive .../fonts-ipafont-gothic_00303-23_all.deb (--unpack):
 unable to install new version of './usr/share/doc/fonts-ipafont-gothic': Invalid cross-device link
```

Concretely, `playwright install --with-deps` fails, so **E2E browser tests
cannot run** on an affected host. This is easy to miss: a well-written
`postCreateCommand` treats it as non-fatal, so `devcontainer up` still reports
`{"outcome":"success"}` while `~/.cache/ms-playwright` is silently empty. No
builder configuration fixes it.

### A stale global `DOCKER_BUILDKIT`

If you applied the `DOCKER_BUILDKIT=0` workaround by hand before this landed —
typically in `/etc/environment`, `~/.bash_profile` or `~/.bashrc` — `remo
configure` **names the files but does not edit them**. They are your lines, in
files remo has no managed block in. The shim sets the variable per invocation,
so devcontainers work regardless; but a stale global still breaks a hand-run
`docker compose build`, so remove it:

```bash
sudo sed -i '/DOCKER_BUILDKIT/d' /etc/environment
sed -i '/DOCKER_BUILDKIT/d' ~/.bashrc ~/.bash_profile
```

### The `deacon` runtime

The shim wraps `@devcontainers/cli` only. A host provisioned with
`--devcontainer-runtime deacon` gets the `remo-native` builder but no shim, and
`remo configure` says so. `deacon` has a different flag set and has not been
evaluated on an affected host.

## Verifying

```bash
docker buildx inspect remo-native          # exists, docker-container driver
command -v devcontainer                    # /usr/local/bin/devcontainer
devcontainer up --workspace-folder .       # no env vars, no flags
```

A Compose project should now report:

```json
{"outcome":"success","composeProjectName":"site_devcontainer","remoteUser":"node", …}
```

Check the non-interactive path too, since that is the one the console uses:

```bash
ssh host 'cd ~/projects/site && devcontainer up --workspace-folder .'
```
