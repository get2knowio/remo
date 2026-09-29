# Deferred issues: 026-deacon-default-runtime

Filed 2026-09-29 as get2knowio/remo#216 (nested-overlayfs proof/shim), #217 (manual gate), #218 (pin bump); `docs/nested-overlayfs.md` references #216.

## 1. Prove deacon on a nested-overlayfs (OrbStack) host, or ship a deacon shim, then drop the conditional default

Spec 026 flips the default devcontainer runtime to deacon **except** on hosts whose kernel refuses nested overlayfs (`docker_nested_overlayfs`), which keep the reference CLI because only it has the build shim (`roles/nested_docker`). deacon's v0.4.0 source shows image builds go through `docker buildx build` (honours `BUILDX_BUILDER`), Compose builds through `docker compose build`, and updateUID through `docker exec` (no plain `docker build`), so the candidate environment is `BUILDX_BUILDER=remo-native` plus `DOCKER_BUILDKIT=1 COMPOSE_BAKE=1` for Compose projects — simpler than the reference CLI's shim. None of this has been run on a real affected host.

- [ ] On an OrbStack machine, `remo configure … --devcontainer-runtime deacon`, export the candidate environment, build an image-based and a Compose-based devcontainer; record outcomes in `docs/nested-overlayfs.md`.
- [ ] If it works with the environment only: add a `deacon` shim (or set the env in `project-launch`/`devshell` for deacon on such hosts) and remove rule 4 from `ansible/tasks/resolve_devcontainer_runtime.yml`.
- [ ] If it does not: file the deacon issue(s) upstream (remo must not reimplement lifecycle semantics) and keep the conditional.

## 2. Manual gate for spec 026 (SC-005)

- [ ] Fresh host with the default → `~/.remo-devcontainer-runtime` = `deacon`, `deacon --version` = 0.4.0, `remo-host capabilities --json` lists `projects.rebuild`, `remo shell -p` launches, `remo web` attaches, console Rebuild works.
- [ ] `remo <type> upgrade` / `remo configure` again → deacon role reports no change; marker unchanged.
- [ ] Legacy host (reference CLI, no marker) → stays on the reference CLI after upgrade; marker `devcontainer`.
- [ ] Explicit switch on a host with a running project → documented procedure holds (stop, switch, `.devcontainer-rebuild`, old containers listed).
- [ ] The nested-overlayfs run from issue 1.

## 3. Bump the deacon pin past v0.4.0 when the next stable release ships

deacon #688 (racing `up --remove-existing-container` vs `down`) was fixed 2026-08-26, after v0.4.0 (2026-08-16). remo never runs the two concurrently, so it is not on remo's path, but the next stable release closes the gap; bump `ansible/roles/deacon/defaults/main.yml` and re-run `tests/ansible/test_deacon_role.py` plus the flag verification in research R2.
