"""Image tests for `docker/Dockerfile` / `docker/entrypoint.sh` /
`docker/compose.example.yml` (T049, 010-web-session-interface US4).

Two tiers, matching the precedent set by
`tests/integration/test_remo_host_e2e.py` for Docker/network-dependent
tests (skip honestly rather than fail when infra isn't available):

1. Structural checks (always run, no Docker required): the Dockerfile
   contains the arch-correct AWS CLI v2 / Session Manager Plugin install
   logic keyed off ``$TARGETARCH`` (not ``dpkg --print-architecture``,
   FR-027/FR-042), no leftover ``TODO(T053)``/``TODO(T054)`` markers,
   `docker/entrypoint.sh` is valid bash and runs `remo web check` as a hard
   gate before `exec remo web serve`, and `compose.example.yml` parses and
   declares the required hardening (non-root, read-only rootfs,
   no-new-privileges, dropped caps, tmpfs, healthcheck against
   `/api/v1/ready`).

2. Real image builds (opt-in, ``REMO_RUN_IMAGE_TESTS=1``): actually invokes
   ``docker buildx build`` for amd64 (and arm64, if multi-platform
   emulation is available), runs the built image with the same hardening
   flags as `compose.example.yml`, and asserts against the *running
   container*: non-root UID, genuinely read-only rootfs, the Session
   Manager Plugin / AWS CLI are present and arch-correct, and
   `/api/v1/ready` both gates on missing mounts (503, correct detail) and
   reports ready (200) once the required mounts are present.

   This tier is opt-in because a multi-arch image build (pulling
   `python:3.11-slim` / `node:20-slim`, installing AWS CLI v2 / SSM plugin,
   `npm ci`, `pip install`, and -- for arm64 -- running most of that under
   QEMU emulation) is a multi-minute, network-heavy operation: not
   appropriate to run unconditionally on every `pytest` invocation / in
   network-restricted CI. Set `REMO_RUN_IMAGE_TESTS=1` to actually exercise
   it (verified working end-to-end while writing this test: both amd64 and
   emulated-arm64 builds succeeded, non-root + read-only rootfs enforcement
   confirmed, AWS CLI v2 + session-manager-plugin present and arch-correct
   for both platforms, `/api/v1/ready` correctly 503s with a missing-mount
   `detail` message and 200s once registry + SSH identity + writable
   `/run/remo-ssh` are all mounted).

011-web-adopt (T035) adds a third group to tier 2: unconfigured-boot tests
(quickstart A/B, SC-006) that start the image with an EMPTY writable bind at
the container's REMO_HOME and no registry/SSH mounts at all, then assert the
service reaches its "awaiting adoption" state (ready 200 `unconfigured`)
within 30s, generates a persistent service identity (same fingerprint across
a container restart, FR-002), and pairing-gates the setup API (012: dormant
404 with no live session; reachable behind a minted code). The pre-existing
RO-mount tests above are intentionally untouched — them still passing IS SC-005.

A fourth group covers the self-healing permissions model: the image starts
as root and its entrypoint chowns a ROOT-OWNED (bind-mounted) config dir and
re-heals the /run/remo-ssh tmpfs on every start, then drops to the non-root
`remo` user via gosu. These tests start the container with a root-owned bind
dir, an option-less `--tmpfs /run/remo-ssh` (no `rw,mode=1777` pin), a
read-only rootfs, `no-new-privileges`, and only the heal-then-drop caps, then
assert: boot reaches `unconfigured`, web-identity/ is generated owned by
`remo`, the app process (PID 1) runs as uid 1000 (not root), and a
`docker restart` keeps the same identity with a still-writable tmpfs.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
import uuid
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
DOCKERFILE = REPO_ROOT / "docker" / "Dockerfile"
ENTRYPOINT = REPO_ROOT / "docker" / "entrypoint.sh"
COMPOSE_FILE = REPO_ROOT / "docker" / "compose.example.yml"

_IMAGE_TAG_PREFIX = "remo-web-image-test"


# ---------------------------------------------------------------------------
# Tier 1: structural checks -- always run, no Docker required.
# ---------------------------------------------------------------------------


def test_dockerfile_has_no_leftover_todo_markers():
    text = DOCKERFILE.read_text()
    assert "TODO(T053)" not in text
    assert "TODO(T005)" not in text


def test_dockerfile_installs_runtime_os_deps():
    text = DOCKERFILE.read_text()
    assert "openssh-client" in text
    assert "curl" in text  # also needed by the compose healthcheck at runtime
    assert "unzip" in text
    assert "--no-install-recommends" in text
    assert "rm -rf /var/lib/apt/lists/*" in text


def test_dockerfile_selects_arch_via_targetarch_not_dpkg():
    text = DOCKERFILE.read_text()
    assert "ARG TARGETARCH" in text
    assert '"$TARGETARCH"' in text
    # The arm64 packaging edge case (FR-027/FR-042): must branch on the
    # buildx-provided TARGET arch, never the build-host arch (which is
    # wrong under cross-compiling buildx). `dpkg --print-architecture` may
    # still appear in an explanatory comment; it must never be *invoked*.
    assert "$(dpkg --print-architecture)" not in text


def test_dockerfile_installs_aws_cli_and_ssm_plugin():
    text = DOCKERFILE.read_text()
    assert "awscli-exe-linux-" in text
    assert "aarch64" in text  # arm64 branch
    assert "x86_64" in text  # amd64 branch
    assert "session-manager-plugin" in text
    assert "ubuntu_${SM_ARCH}" in text
    assert '"arm64"' in text
    assert '"64bit"' in text


def test_dockerfile_installs_remo_cli_with_web_extra():
    text = DOCKERFILE.read_text()
    assert "[web]" in text


def test_dockerfile_sets_frontend_dist_dir_override():
    text = DOCKERFILE.read_text()
    assert "ENV REMO_WEB_FRONTEND_DIST_DIR=" in text
    # Must match the COPY destination used for the built frontend assets.
    dist_env_line = next(
        line for line in text.splitlines() if line.startswith("ENV REMO_WEB_FRONTEND_DIST_DIR=")
    )
    dist_path = dist_env_line.split("=", 1)[1].strip()
    assert dist_path in text.replace(dist_env_line, "")  # referenced by a COPY --from= too


def test_dockerfile_starts_as_root_for_self_healing_entrypoint():
    """The image must NOT hard-pin `USER remo` — it starts as root so the
    entrypoint can self-heal ownership on a bind-mounted, root-owned config
    dir and on the /run/remo-ssh tmpfs before dropping privileges. The drop
    to non-root now happens at runtime (via gosu in docker/entrypoint.sh),
    not via a build-time USER line."""
    text = DOCKERFILE.read_text()
    # No active `USER remo` directive (a commented mention is fine).
    assert not any(
        line.strip().startswith("USER remo") for line in text.splitlines()
    ), "Dockerfile must start as root; the entrypoint drops to `remo` via gosu"
    # gosu must be installed for the entrypoint's privilege drop.
    assert "gosu" in text
    # The remo user must still be created (root-only op) for gosu to drop to.
    assert "useradd" in text
    # HOME is pinned so config-path resolution matches across the root heal
    # pass and the dropped-to `remo` process.
    assert "ENV HOME=/home/remo" in text


def test_entrypoint_self_heals_as_root_then_drops_via_gosu():
    """Root branch: mkdir/chown the config dir and the SSH control tmpfs, then
    `exec gosu` to the unprivileged user (PID 1 / signal semantics preserved)."""
    text = ENTRYPOINT.read_text()
    # Guarded on being UID 0 so an explicit non-root `--user` skips healing.
    assert "id -u" in text
    assert "chown" in text
    # The drop is an exec-form gosu (never backgrounded).
    assert "exec gosu" in text
    # It heals both the config dir and the SSH control (runtime) dir.
    assert "CONFIG_DIR" in text
    assert "CONTROL_DIR" in text


def test_entrypoint_healing_is_best_effort():
    """Healing must never hard-fail startup (a read-only config mount can't be
    chowned; a deployer may drop CAP_CHOWN) — `remo web check` is the real
    gate. Every mkdir/chown/chmod step tolerates failure."""
    text = ENTRYPOINT.read_text()
    heal_lines = [
        ln.strip()
        for ln in text.splitlines()
        if ln.strip().startswith(("mkdir", "chown", "chmod"))
    ]
    assert heal_lines, "expected filesystem-healing lines in the entrypoint"
    for line in heal_lines:
        assert "|| true" in line or "2>/dev/null" in line, line


def test_dockerfile_entrypoint_is_the_finalized_script():
    text = DOCKERFILE.read_text()
    assert "docker/entrypoint.sh" in text
    assert "ENTRYPOINT" in text


def test_entrypoint_is_valid_bash():
    result = subprocess.run(
        ["bash", "-n", str(ENTRYPOINT)], capture_output=True, text=True, timeout=10
    )
    assert result.returncode == 0, result.stderr


def test_entrypoint_has_no_leftover_todo_markers():
    text = ENTRYPOINT.read_text()
    assert "TODO(T054)" not in text
    assert "TODO(T005)" not in text


def test_entrypoint_gates_on_check_then_execs_serve():
    text = ENTRYPOINT.read_text()
    assert "set -euo pipefail" in text
    lines = [ln.strip() for ln in text.splitlines() if ln.strip() and not ln.strip().startswith("#")]
    # `remo web check` must run unguarded (so `set -e` aborts the script on a
    # CONFIG failure) strictly before `exec remo web serve`. It runs with
    # `--skip-instance-checks` so a single unreachable instance can't block
    # startup (FR-006) while bad config/mounts still fail fast.
    check_idx = next(i for i, ln in enumerate(lines) if ln.startswith("remo web check"))
    assert "--skip-instance-checks" in lines[check_idx]
    serve_idx = next(i for i, ln in enumerate(lines) if ln.startswith("exec remo web serve"))
    assert check_idx < serve_idx
    # Must be `exec` (replaces PID 1) so SIGTERM reaches uvicorn directly.
    assert lines[serve_idx].startswith("exec ")


def _load_compose() -> dict:
    return yaml.safe_load(COMPOSE_FILE.read_text())


def test_compose_file_has_no_leftover_todo_markers():
    text = COMPOSE_FILE.read_text()
    assert "TODO(T054)" not in text
    assert "TODO(T005)" not in text


def test_compose_file_parses_as_yaml():
    doc = _load_compose()
    assert "remo-web" in doc["services"]


def test_compose_healthcheck_targets_ready_endpoint():
    service = _load_compose()["services"]["remo-web"]
    test_cmd = service["healthcheck"]["test"]
    joined = " ".join(test_cmd)
    assert "/api/v1/ready" in joined
    assert "curl" in joined


def test_compose_declares_hardening():
    service = _load_compose()["services"]["remo-web"]
    assert service["read_only"] is True
    assert service["user"] == "1000:1000"
    assert "no-new-privileges:true" in service["security_opt"]
    assert service["cap_drop"] == ["ALL"]
    assert "/run/remo-ssh" in service["tmpfs"]
    assert service["restart"] == "unless-stopped"


def test_compose_binds_loopback_by_default():
    service = _load_compose()["services"]["remo-web"]
    ports = service["ports"]
    assert any(str(p).startswith("127.0.0.1:") for p in ports)


def test_compose_mounts_registry_and_ssh_material_readonly():
    service = _load_compose()["services"]["remo-web"]
    volumes = service["volumes"]
    joined = "\n".join(volumes)
    assert "/home/remo/.config/remo:ro" in joined  # registry
    assert "/home/remo/.ssh/" in joined  # SSH material, distinct mounts
    assert all(v.endswith(":ro") for v in volumes if not v.strip().startswith("#"))


def test_compose_adopted_service_self_heals_without_workarounds():
    """The adopted-mode service must rely on the entrypoint's self-healing,
    not on deployer workarounds: a PLAIN tmpfs (no `rw,mode=1777` pin) and no
    `user:` pin (so it starts as root and heals), while keeping the read-only
    rootfs + no-new-privileges posture and granting only the capabilities the
    heal-then-drop needs."""
    svc = _load_compose()["services"]["remo-web-adopted"]
    # Plain, option-less tmpfs — the entrypoint re-heals it on every start.
    assert "/run/remo-ssh" in svc["tmpfs"]
    assert not any("mode=" in t for t in svc["tmpfs"]), (
        "the rw,mode=1777 tmpfs pin should be gone — the entrypoint re-heals "
        "/run/remo-ssh on restart"
    )
    # No `user:` pin: starts as root so the entrypoint can heal the state
    # volume before dropping to `remo`.
    assert "user" not in svc
    # Hardening preserved; caps are drop-ALL + only the heal-then-drop set.
    assert svc["read_only"] is True
    assert "no-new-privileges:true" in svc["security_opt"]
    assert svc["cap_drop"] == ["ALL"]
    for cap in ("CHOWN", "SETUID", "SETGID"):
        assert cap in svc["cap_add"], cap


@pytest.mark.skipif(
    shutil.which("docker") is None, reason="docker compose config requires the docker CLI"
)
def test_compose_config_validates_via_docker_compose():
    """Cheap (no build/pull): `docker compose config` only parses/validates."""
    result = subprocess.run(
        ["docker", "compose", "-f", str(COMPOSE_FILE), "config"],
        capture_output=True,
        text=True,
        timeout=20,
        cwd=str(REPO_ROOT),
    )
    if result.returncode != 0 and "Cannot connect to the Docker daemon" in result.stderr:
        pytest.skip("Docker daemon not reachable")
    assert result.returncode == 0, result.stderr


# ---------------------------------------------------------------------------
# Tier 2: real `docker buildx build` + run (opt-in, REMO_RUN_IMAGE_TESTS=1).
# ---------------------------------------------------------------------------

_RUN_IMAGE_TESTS = os.environ.get("REMO_RUN_IMAGE_TESTS") == "1"


def _docker_buildx_available() -> bool:
    if shutil.which("docker") is None:
        return False
    try:
        subprocess.run(["docker", "buildx", "version"], capture_output=True, timeout=10, check=True)
        subprocess.run(["docker", "info"], capture_output=True, timeout=10, check=True)
    except Exception:
        return False
    return True


_BUILD_INFRA_OK = _RUN_IMAGE_TESTS and _docker_buildx_available()
requires_image_build = pytest.mark.skipif(
    not _BUILD_INFRA_OK,
    reason=(
        "opt-in image-build test: set REMO_RUN_IMAGE_TESTS=1 with a working "
        "`docker buildx` (network access to pull python:3.11-slim / "
        "node:20-slim and install packages) to run this"
    ),
)


def _run_build(platform: str, tag: str, builder: str | None = None) -> subprocess.CompletedProcess:
    cmd = ["docker", "buildx", "build"]
    if builder:
        cmd += ["--builder", builder]
    cmd += [
        "--platform",
        platform,
        "-f",
        str(DOCKERFILE),
        "-t",
        tag,
        "--load",
        str(REPO_ROOT),
    ]
    return subprocess.run(cmd, capture_output=True, text=True, timeout=900)


def _docker_rm(name: str) -> None:
    subprocess.run(["docker", "rm", "-f", name], capture_output=True, timeout=20)


def _docker_rmi(tag: str) -> None:
    subprocess.run(["docker", "rmi", "-f", tag], capture_output=True, timeout=30)


@pytest.fixture(scope="module")
def amd64_image():
    tag = f"{_IMAGE_TAG_PREFIX}:amd64-{uuid.uuid4().hex[:8]}"
    result = _run_build("linux/amd64", tag)
    assert result.returncode == 0, result.stdout + result.stderr
    yield tag
    _docker_rmi(tag)


@pytest.fixture
def registry_and_ssh_mounts(tmp_path):
    """A minimal, valid registry + SSH identity to satisfy `remo web check`."""
    registry_dir = tmp_path / "registry"
    registry_dir.mkdir()
    (registry_dir / "known_hosts").write_text("")

    ssh_dir = tmp_path / "ssh"
    ssh_dir.mkdir()
    key_path = ssh_dir / "id_ed25519"
    subprocess.run(
        ["ssh-keygen", "-t", "ed25519", "-N", "", "-f", str(key_path), "-q"],
        check=True,
        timeout=15,
    )
    # World-readable, not the conventional 0600: the container runs as uid
    # 1000 (`--user 1000:1000`, matching compose.example.yml) while this key
    # is owned by whatever uid runs pytest. Those coincide on a workstation
    # whose user is uid 1000, but not on a GitHub runner (uid 1001) -- there
    # a 0600 key is unreadable to the container, `remo web check`'s
    # os.access(R_OK) probe reports "no SSH private key found", and the
    # entrypoint gate exits before the assertions below ever run. The key is
    # a throwaway generated into tmp_path and never used to authenticate, so
    # loosening the mode costs nothing. Any change here must keep the file
    # readable by a *different* uid than the one running the tests.
    key_path.chmod(0o644)
    return registry_dir, key_path


@requires_image_build
def test_build_amd64_succeeds(amd64_image):
    inspect = subprocess.run(
        ["docker", "image", "inspect", amd64_image], capture_output=True, text=True, timeout=15
    )
    assert inspect.returncode == 0


@requires_image_build
def test_amd64_container_runs_as_non_root_with_readonly_rootfs(
    amd64_image, registry_and_ssh_mounts
):
    registry_dir, key_path = registry_and_ssh_mounts
    name = f"remo-web-test-nonroot-{uuid.uuid4().hex[:8]}"
    try:
        run = subprocess.run(
            [
                "docker",
                "run",
                "-d",
                "--name",
                name,
                "--read-only",
                "--user",
                "1000:1000",
                "--tmpfs",
                "/run/remo-ssh",
                "-v",
                f"{registry_dir}:/home/remo/.config/remo:ro",
                "-v",
                f"{key_path}:/home/remo/.ssh/id_ed25519:ro",
                amd64_image,
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert run.returncode == 0, run.stderr
        _wait_for_running(name)

        id_result = subprocess.run(
            ["docker", "exec", name, "id", "-u"], capture_output=True, text=True, timeout=10
        )
        assert id_result.returncode == 0, id_result.stderr
        assert id_result.stdout.strip() == "1000"

        touch_result = subprocess.run(
            ["docker", "exec", name, "sh", "-c", "touch /this-should-fail"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert touch_result.returncode != 0
        assert "read-only" in (touch_result.stderr + touch_result.stdout).lower()

        tmpfs_result = subprocess.run(
            ["docker", "exec", name, "sh", "-c", "touch /run/remo-ssh/probe"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert tmpfs_result.returncode == 0, tmpfs_result.stderr
    finally:
        _docker_rm(name)


@requires_image_build
def test_amd64_ready_endpoint_reports_ready_with_full_mounts(amd64_image, registry_and_ssh_mounts):
    registry_dir, key_path = registry_and_ssh_mounts
    name = f"remo-web-test-ready-{uuid.uuid4().hex[:8]}"
    try:
        run = subprocess.run(
            [
                "docker",
                "run",
                "-d",
                "--name",
                name,
                "--read-only",
                "--user",
                "1000:1000",
                "--tmpfs",
                "/run/remo-ssh",
                "-v",
                f"{registry_dir}:/home/remo/.config/remo:ro",
                "-v",
                f"{key_path}:/home/remo/.ssh/id_ed25519:ro",
                amd64_image,
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert run.returncode == 0, run.stderr
        _wait_for_running(name)

        payload = _curl_in_container(name, "http://127.0.0.1:8080/api/v1/ready")
        assert payload["status"] == "ready"
        assert payload["checks"]["registry"] == "ok"
        assert payload["checks"]["ssh_identity"] == "ok"

        health_payload = _curl_in_container(name, "http://127.0.0.1:8080/api/v1/health")
        assert health_payload["status"] == "alive"
    finally:
        _docker_rm(name)


@requires_image_build
def test_amd64_entrypoint_gates_hard_on_missing_mounts(amd64_image):
    """No registry/SSH/tmpfs mounted: `remo web check` must fail and the
    entrypoint must abort under `set -e` before ever serving (FR-045/046)."""
    name = f"remo-web-test-gate-{uuid.uuid4().hex[:8]}"
    try:
        run = subprocess.run(
            ["docker", "run", "-d", "--name", name, "--read-only", "--user", "1000:1000", amd64_image],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert run.returncode == 0, run.stderr
        time.sleep(3)

        inspect = subprocess.run(
            ["docker", "inspect", "-f", "{{.State.Running}}", name],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert inspect.stdout.strip() == "false", "container should have exited (check gate failed)"

        exit_code = subprocess.run(
            ["docker", "inspect", "-f", "{{.State.ExitCode}}", name],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert exit_code.stdout.strip() != "0"

        logs = subprocess.run(
            ["docker", "logs", name], capture_output=True, text=True, timeout=10
        )
        assert "FAIL" in (logs.stdout + logs.stderr)
    finally:
        _docker_rm(name)


@requires_image_build
def test_amd64_ready_endpoint_503s_on_missing_mounts(amd64_image):
    """Directly exercises `/api/v1/ready`'s own gating (independent of the
    entrypoint's hard check-then-serve gate) by bypassing the entrypoint and
    invoking `remo web serve` with no mounts at all: 503, correct detail."""
    name = f"remo-web-test-503-{uuid.uuid4().hex[:8]}"
    try:
        run = subprocess.run(
            [
                "docker",
                "run",
                "-d",
                "--name",
                name,
                "--read-only",
                "--user",
                "1000:1000",
                "--tmpfs",
                "/run/remo-ssh",
                "--entrypoint",
                "remo",
                amd64_image,
                "web",
                "serve",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert run.returncode == 0, run.stderr
        _wait_for_running(name)

        payload = _curl_in_container(
            name, "http://127.0.0.1:8080/api/v1/ready", expect_status="503"
        )
        assert payload["status"] == "not_ready"
        assert payload["checks"]["registry"] != "ok"
        assert "detail" in payload
    finally:
        _docker_rm(name)


@requires_image_build
def test_amd64_aws_cli_and_ssm_plugin_present_and_arch_correct(amd64_image):
    result = subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "--entrypoint",
            "sh",
            amd64_image,
            "-c",
            "aws --version && session-manager-plugin --version && dpkg --print-architecture",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert "aws-cli" in result.stdout
    assert result.stdout.strip().splitlines()[-1] == "amd64"


_EM_AARCH64 = 183  # ELF e_machine value for AArch64 (EM_AARCH64)
_EM_X86_64 = 62  # ELF e_machine value for x86-64 (EM_X86_64)

_ARM64_BUILD_ERROR_SIGNATURES = (
    "exec format error",
    "no match for platform",
    "multiple platforms feature",
    "does not support",
)


def _elf_machine(path: Path) -> int:
    """Read the ELF `e_machine` field without executing the file -- this
    sandbox's container *runtime* has no arm64 emulation registered (only
    BuildKit's build-time QEMU does, confirmed empirically: `docker run
    --platform linux/arm64 alpine uname -m` fails with `exec format error`
    even though `docker buildx build --platform linux/arm64` succeeds), so
    arch-correctness for the arm64 image is verified by reading binary
    headers, not by running them."""
    data = path.read_bytes()[:20]
    assert data[:4] == b"\x7fELF", f"not an ELF file: {path}"
    return int.from_bytes(data[18:20], byteorder="little" if data[5] == 1 else "big")


def _ensure_docker_container_builder(name: str) -> bool:
    """The default `docker`-driver builder cannot route non-native
    platforms through QEMU (confirmed empirically: it fails arm64 builds
    with `exec format error`); a `docker-container` driver builder can.
    Reuses `name` if it already exists; otherwise tries to create+bootstrap
    it. Returns False (never raises) if that isn't possible here."""
    inspect = subprocess.run(
        ["docker", "buildx", "inspect", name], capture_output=True, text=True, timeout=15
    )
    if inspect.returncode == 0:
        return True
    create = subprocess.run(
        ["docker", "buildx", "create", "--name", name, "--driver", "docker-container"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if create.returncode != 0:
        return False
    bootstrap = subprocess.run(
        ["docker", "buildx", "inspect", "--bootstrap", name],
        capture_output=True,
        text=True,
        timeout=60,
    )
    return bootstrap.returncode == 0


@requires_image_build
def test_build_and_run_arm64(tmp_path):
    """Builds the arm64 image for real via `docker buildx build` (BuildKit
    has QEMU emulation for build-time RUN steps here, confirmed working),
    then verifies arch-correctness via image metadata + ELF header
    inspection of the installed session-manager-plugin binary -- rather than
    `docker run`, which this sandbox's container runtime cannot execute for
    a foreign architecture (see `_elf_machine` docstring)."""
    tag = f"{_IMAGE_TAG_PREFIX}:arm64-{uuid.uuid4().hex[:8]}"
    builder = os.environ.get("REMO_IMAGE_TEST_ARM64_BUILDER", "multiarch")
    if not _ensure_docker_container_builder(builder):
        pytest.skip(
            f"could not create/bootstrap a docker-container buildx builder "
            f"named {builder!r} (needed to route arm64 builds through QEMU)"
        )
    try:
        result = _run_build("linux/arm64", tag, builder=builder)
        if result.returncode != 0:
            combined = (result.stdout + result.stderr).lower()
            if any(sig in combined for sig in _ARM64_BUILD_ERROR_SIGNATURES):
                pytest.skip(f"arm64 buildx emulation not usable here: {combined[-500:]}")
            raise AssertionError(result.stdout + result.stderr)

        arch = subprocess.run(
            ["docker", "image", "inspect", "-f", "{{.Architecture}}", tag],
            capture_output=True,
            text=True,
            timeout=15,
        )
        assert arch.returncode == 0, arch.stderr
        assert arch.stdout.strip() == "arm64"

        # `docker create` only materializes the container's filesystem; it
        # never executes anything, so it works regardless of runtime-level
        # emulation support.
        name = f"remo-web-arm64-inspect-{uuid.uuid4().hex[:8]}"
        create = subprocess.run(
            ["docker", "create", "--name", name, "--entrypoint", "true", tag],
            capture_output=True,
            text=True,
            timeout=20,
        )
        assert create.returncode == 0, create.stderr
        try:
            smp_local = tmp_path / "session-manager-plugin"
            cp = subprocess.run(
                [
                    "docker",
                    "cp",
                    f"{name}:/usr/local/sessionmanagerplugin/bin/session-manager-plugin",
                    str(smp_local),
                ],
                capture_output=True,
                text=True,
                timeout=20,
            )
            assert cp.returncode == 0, cp.stderr
            assert _elf_machine(smp_local) == _EM_AARCH64

            aws_check = subprocess.run(
                ["docker", "run", "--rm", "--entrypoint", "sh", tag, "-c", "test -x /usr/local/bin/aws"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            # This exec *may* fail with "exec format error" (no runtime
            # emulation, as noted above) -- that outcome is fine and does
            # NOT indicate a packaging problem; only a non-format-error
            # failure would.
            if aws_check.returncode != 0:
                combined = (aws_check.stdout + aws_check.stderr).lower()
                assert "exec format error" in combined, aws_check.stdout + aws_check.stderr
        finally:
            _docker_rm(name)
    finally:
        _docker_rmi(tag)


# ---------------------------------------------------------------------------
# Tier 2, group 3: unconfigured boot (011-web-adopt T035; quickstart A/B,
# SC-006/FR-002/FR-021). Same opt-in gate and hardening flags as above.
# ---------------------------------------------------------------------------

_CONTAINER_REMO_HOME = "/home/remo/.config/remo"
_IDENTITY_DIR = f"{_CONTAINER_REMO_HOME}/web-identity"
_SETUP_STATUS_URL = "http://127.0.0.1:8080/api/v1/setup/status"


@pytest.fixture
def empty_state_dir(tmp_path, amd64_image):
    """An EMPTY writable dir bind-mounted at the container's REMO_HOME.

    Quickstart A uses `docker volume create` + a named volume; a host tmp-dir
    bind is equivalent for the service (an empty writable REMO_HOME) while
    staying deterministic about ownership: chmod 0o777 so uid 1000 (the
    container user) can write even when pytest runs as a different uid — the
    same cross-uid reasoning as `registry_and_ssh_mounts`' 0644 key above.

    Cleanup runs `rm -rf` as root *inside a throwaway container*: the booted
    service writes a 0700, uid-1000-owned `web-identity/` into this dir,
    which the pytest uid cannot necessarily delete from the host side.
    """
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    state_dir.chmod(0o777)
    yield state_dir
    subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "--user",
            "0:0",
            "--entrypoint",
            "sh",
            "-v",
            f"{state_dir}:/state",
            amd64_image,
            "-c",
            "rm -rf /state/web-identity /state/known_hosts",
        ],
        capture_output=True,
        timeout=30,
    )


def _run_unconfigured_container(
    image: str, name: str, state_dir: Path, *, operator_auth: str | None = None
) -> None:
    """`docker run` with compose.example.yml's hardening flags, an empty
    writable state volume at REMO_HOME, and NO registry/SSH mounts.

    The tmpfs mode is pinned explicitly: a bare `--tmpfs` first mounts as
    1777 but is REMOUNTED root-owned 0755 by `docker restart` (verified
    empirically on Docker 29; a long-standing Docker quirk), which would
    make the runtime-dir check fail on the second boot of the FR-002
    restart test below. `rw,mode=1777` pins Docker's own first-boot default
    so every (re)mount is identical.

    ``operator_auth`` sets ``REMO_WEB_OPERATOR_AUTH`` (012): pass ``"none"``
    (network-restricted) so the container can mint a pairing code over HTTP.
    """
    cmd = [
        "docker",
        "run",
        "-d",
        "--name",
        name,
        "--read-only",
        "--user",
        "1000:1000",
        "--tmpfs",
        "/run/remo-ssh:rw,mode=1777",
        "-v",
        f"{state_dir}:{_CONTAINER_REMO_HOME}",
    ]
    if operator_auth is not None:
        cmd += ["-e", f"REMO_WEB_OPERATOR_AUTH={operator_auth}"]
    cmd.append(image)
    run = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    assert run.returncode == 0, run.stderr


def _mint_code_in_container(name: str) -> str:
    """Mint a pairing code via `POST /pairing/mint` inside the container.

    Requires the network-restricted posture (REMO_WEB_OPERATOR_AUTH=none). The
    Origin header must be an allowed origin (127.0.0.1:8080 is a default).
    """
    result = subprocess.run(
        [
            "docker", "exec", name, "curl", "-s",
            "-H", "Origin: http://127.0.0.1:8080",
            "-X", "POST", "http://127.0.0.1:8080/api/v1/pairing/mint",
        ],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return json.loads(result.stdout)["code"]


def _wait_for_ready_payload(name: str, timeout_s: float = 30.0) -> dict:
    """Poll GET /api/v1/ready until it answers 200; the 30s default budget is
    SC-006's bound for reaching the awaiting-adoption state. Unlike
    `_curl_in_container` this tolerates the not-yet-listening window."""
    deadline = time.time() + timeout_s
    last = ""
    while time.time() < deadline:
        result = subprocess.run(
            [
                "docker",
                "exec",
                name,
                "curl",
                "-s",
                "-w",
                "\\n%{http_code}",
                "http://127.0.0.1:8080/api/v1/ready",
            ],
            capture_output=True,
            text=True,
            timeout=15,
        )
        body, _, status = result.stdout.rpartition("\n")
        if result.returncode == 0 and status.strip() == "200" and body:
            return json.loads(body)
        last = result.stdout + result.stderr
        time.sleep(0.5)
    logs = subprocess.run(["docker", "logs", name], capture_output=True, text=True, timeout=10)
    raise AssertionError(
        f"/api/v1/ready did not answer 200 within {timeout_s}s "
        f"(last: {last!r}); container logs:\n{logs.stdout}{logs.stderr}"
    )


def _curl_status_and_json(name: str, url: str, headers: tuple[str, ...] = ()) -> tuple[str, dict]:
    """Like `_curl_in_container`, plus request headers and the status code in
    the return value (the setup-API tests assert on 200 vs 401 vs 404)."""
    cmd = ["docker", "exec", name, "curl", "-s", "-w", "\\n%{http_code}"]
    for header in headers:
        cmd += ["-H", header]
    cmd.append(url)
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
    body, _, status = result.stdout.rpartition("\n")
    return status.strip(), (json.loads(body) if body else {})


def _service_key_fingerprint(name: str) -> str:
    result = subprocess.run(
        ["docker", "exec", name, "ssh-keygen", "-lf", f"{_IDENTITY_DIR}/id_ed25519.pub"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout.split()[1]  # the SHA256:... field


@requires_image_build
def test_amd64_unconfigured_boot_reports_unconfigured_and_generates_identity(
    amd64_image, empty_state_dir
):
    """Quickstart A: empty writable REMO_HOME + token, no registry/key
    mounts. The entrypoint gate must PASS (unconfigured is a passing state,
    SC-006), ready must answer 200 `unconfigured` within 30s, and the boot
    must have generated the service identity into the state volume."""
    name = f"remo-web-test-unconf-{uuid.uuid4().hex[:8]}"
    try:
        _run_unconfigured_container(amd64_image, name, empty_state_dir)
        payload = _wait_for_ready_payload(name)
        assert payload["status"] == "unconfigured"

        probe = subprocess.run(
            [
                "docker",
                "exec",
                name,
                "sh",
                "-c",
                f"test -f {_IDENTITY_DIR}/id_ed25519 && "
                f"test -f {_IDENTITY_DIR}/id_ed25519.pub && "
                f"test -f {_IDENTITY_DIR}/state.json",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if probe.returncode != 0:
            listing = subprocess.run(
                ["docker", "exec", name, "ls", "-laR", _CONTAINER_REMO_HOME],
                capture_output=True,
                text=True,
                timeout=10,
            )
            raise AssertionError(
                f"service identity not generated under {_IDENTITY_DIR}:\n"
                f"{listing.stdout}{listing.stderr}"
            )
    finally:
        _docker_rm(name)


@requires_image_build
def test_amd64_service_identity_survives_container_restart(amd64_image, empty_state_dir):
    """FR-002: the keypair is generated once and NEVER regenerated while the
    key files exist — a restart must come back with the same fingerprint."""
    name = f"remo-web-test-restart-{uuid.uuid4().hex[:8]}"
    try:
        _run_unconfigured_container(amd64_image, name, empty_state_dir)
        assert _wait_for_ready_payload(name)["status"] == "unconfigured"
        fingerprint_before = _service_key_fingerprint(name)

        restart = subprocess.run(
            ["docker", "restart", name], capture_output=True, text=True, timeout=60
        )
        assert restart.returncode == 0, restart.stderr

        assert _wait_for_ready_payload(name)["status"] == "unconfigured"
        assert _service_key_fingerprint(name) == fingerprint_before
    finally:
        _docker_rm(name)


@requires_image_build
def test_amd64_setup_status_pairing_gated(amd64_image, empty_state_dir):
    """012: with a minted pairing code the setup surface is reachable (state
    `unconfigured`); a wrong code gets the dormant 404, never a 401 (SC-004)."""
    name = f"remo-web-test-setup-{uuid.uuid4().hex[:8]}"
    try:
        # Network-restricted posture so the container can mint over HTTP.
        _run_unconfigured_container(amd64_image, name, empty_state_dir, operator_auth="none")
        _wait_for_ready_payload(name)

        code = _mint_code_in_container(name)
        status, payload = _curl_status_and_json(
            name, _SETUP_STATUS_URL, headers=(f"Authorization: Bearer {code}",)
        )
        assert status == "200", payload
        assert payload["state"] == "unconfigured"
        assert payload["public_key_available"] is True
        assert payload["registry_instances"] == 0

        # A wrong code is the dormant 404 (FR-006), never a distinguishable 401.
        status, body = _curl_status_and_json(
            name, _SETUP_STATUS_URL, headers=("Authorization: Bearer wrong-code",)
        )
        assert status == "404", body
        assert body == {"detail": "Not Found"}
    finally:
        _docker_rm(name)


@requires_image_build
def test_amd64_setup_routes_dormant_without_session(amd64_image, empty_state_dir):
    """012: with no live pairing session the setup surface must not exist
    (404, indistinguishable from an unknown route — FR-005) while the service
    still boots and reports `unconfigured` on ready."""
    name = f"remo-web-test-dormant-{uuid.uuid4().hex[:8]}"
    try:
        _run_unconfigured_container(amd64_image, name, empty_state_dir, operator_auth="none")
        payload = _wait_for_ready_payload(name)
        assert payload["status"] == "unconfigured"

        # No mint -> dormant.
        status, body = _curl_status_and_json(name, _SETUP_STATUS_URL)
        assert status == "404", body
        assert body == {"detail": "Not Found"}
    finally:
        _docker_rm(name)


# ---------------------------------------------------------------------------
# Tier 2, group 4: self-healing permissions (root-start heal -> drop). Same
# opt-in gate and hardening flags, plus the heal-then-drop capability set.
# ---------------------------------------------------------------------------

# cap_drop ALL + only what the heal-then-drop entrypoint needs, mirroring the
# `remo-web-adopted` service in compose.example.yml.
_HEAL_RUN_FLAGS = [
    "--read-only",
    "--cap-drop", "ALL",
    "--cap-add", "CHOWN",
    "--cap-add", "DAC_OVERRIDE",
    "--cap-add", "FOWNER",
    "--cap-add", "SETUID",
    "--cap-add", "SETGID",
    "--security-opt", "no-new-privileges:true",
    # Option-less tmpfs (NO rw,mode=1777 pin): the entrypoint re-heals it.
    "--tmpfs", "/run/remo-ssh",
]


@pytest.fixture
def root_owned_state_dir(tmp_path, amd64_image):
    """A root:root 0755 dir bind-mounted at the container's REMO_HOME.

    Reproduces the bind-mount-from-an-app-platform case the self-healing
    entrypoint exists for: a non-root process cannot create web-identity/
    there. Ownership is set (and later cleaned up) via a throwaway root
    container so the test needs no host root — the same technique
    `empty_state_dir` uses for cleanup.
    """
    state_dir = tmp_path / "rootstate"
    state_dir.mkdir()

    def _root_sh(script: str, check: bool) -> None:
        subprocess.run(
            [
                "docker", "run", "--rm", "--user", "0:0", "--entrypoint", "sh",
                "-v", f"{state_dir}:/state", amd64_image, "-c", script,
            ],
            check=check,
            capture_output=True,
            timeout=30,
        )

    _root_sh("chown 0:0 /state && chmod 0755 /state", check=True)
    yield state_dir
    _root_sh("rm -rf /state/web-identity /state/known_hosts", check=False)


def _proc1_uid(name: str) -> str:
    """The real (numeric) uid of PID 1 inside the container, read from
    /proc/1/status (python:3.11-slim ships no `ps`)."""
    result = subprocess.run(
        ["docker", "exec", name, "cat", "/proc/1/status"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    for line in result.stdout.splitlines():
        if line.startswith("Uid:"):
            # "Uid:\treal\teffective\tsaved\tfilesystem"
            return line.split()[1]
    raise AssertionError(f"no Uid line in /proc/1/status:\n{result.stdout}")


@requires_image_build
def test_amd64_root_owned_bind_dir_self_heals_and_app_runs_non_root(
    amd64_image, root_owned_state_dir
):
    """Acceptance: a root-owned bind dir + read-only rootfs + option-less
    tmpfs boots to awaiting-adoption, generates web-identity/ OWNED BY remo,
    and the app process (PID 1) runs as the non-root `remo` user (uid 1000),
    not root — the entrypoint healed as root then dropped via gosu."""
    name = f"remo-web-test-heal-{uuid.uuid4().hex[:8]}"
    try:
        cmd = ["docker", "run", "-d", "--name", name]
        cmd += _HEAL_RUN_FLAGS
        cmd += [
            "-v", f"{root_owned_state_dir}:{_CONTAINER_REMO_HOME}",
            amd64_image,
        ]
        run = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        assert run.returncode == 0, run.stderr

        assert _wait_for_ready_payload(name)["status"] == "unconfigured"

        # The app must run as non-root remo despite starting from root.
        assert _proc1_uid(name) == "1000"

        # web-identity/ was generated and is owned by remo (uid 1000), proving
        # the root-side chown healed the root-owned bind dir.
        owner = subprocess.run(
            ["docker", "exec", name, "stat", "-c", "%u", f"{_IDENTITY_DIR}/id_ed25519"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert owner.returncode == 0, owner.stdout + owner.stderr
        assert owner.stdout.strip() == "1000", owner.stdout
    finally:
        _docker_rm(name)


@requires_image_build
def test_amd64_bare_tmpfs_survives_restart_via_reheal(amd64_image, root_owned_state_dir):
    """Acceptance: with an OPTION-LESS `--tmpfs /run/remo-ssh` (no
    rw,mode=1777 pin), a `docker restart` — which remounts the tmpfs
    root-owned 0755 — still comes back `unconfigured` with the SAME identity
    and a still-writable control dir, because the entrypoint re-heals the
    tmpfs on every start."""
    name = f"remo-web-test-reheal-{uuid.uuid4().hex[:8]}"
    try:
        cmd = ["docker", "run", "-d", "--name", name]
        cmd += _HEAL_RUN_FLAGS
        cmd += [
            "-v", f"{root_owned_state_dir}:{_CONTAINER_REMO_HOME}",
            amd64_image,
        ]
        run = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        assert run.returncode == 0, run.stderr

        assert _wait_for_ready_payload(name)["status"] == "unconfigured"
        fingerprint_before = _service_key_fingerprint(name)

        restart = subprocess.run(
            ["docker", "restart", name], capture_output=True, text=True, timeout=60
        )
        assert restart.returncode == 0, restart.stderr

        # Ready again (the re-heal made the restart-remounted tmpfs writable),
        # same identity (FR-002).
        assert _wait_for_ready_payload(name)["status"] == "unconfigured"
        assert _service_key_fingerprint(name) == fingerprint_before

        # The control dir is writable by the dropped-to remo user after the
        # restart re-heal.
        probe = subprocess.run(
            ["docker", "exec", "--user", "1000:1000", name, "touch", "/run/remo-ssh/probe"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert probe.returncode == 0, probe.stdout + probe.stderr
    finally:
        _docker_rm(name)


@requires_image_build
def test_amd64_explicit_non_root_user_still_boots(amd64_image, empty_state_dir):
    """A deployer that pins an explicit non-root `--user` starts non-root, so
    the entrypoint skips healing (best-effort) and execs directly. With a
    writable state dir it must still boot to `unconfigured` — healing being
    skipped must never be fatal."""
    name = f"remo-web-test-explicituser-{uuid.uuid4().hex[:8]}"
    try:
        cmd = ["docker", "run", "-d", "--name", name, "--user", "1000:1000"]
        cmd += _HEAL_RUN_FLAGS
        cmd += [
            "-v", f"{empty_state_dir}:{_CONTAINER_REMO_HOME}",
            amd64_image,
        ]
        run = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        assert run.returncode == 0, run.stderr
        assert _wait_for_ready_payload(name)["status"] == "unconfigured"
        assert _proc1_uid(name) == "1000"
    finally:
        _docker_rm(name)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _wait_for_running(name: str, timeout_s: float = 15.0) -> None:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        inspect = subprocess.run(
            ["docker", "inspect", "-f", "{{.State.Running}}", name],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if inspect.stdout.strip() == "true":
            time.sleep(1.5)  # give uvicorn a moment to finish startup
            return
        time.sleep(0.5)
    raise AssertionError(f"container {name} did not reach Running state in {timeout_s}s")


def _curl_in_container(name: str, url: str, expect_status: str | None = None) -> dict:
    """Curls from *inside* the container (its own network namespace) --
    mirrors exactly what the compose healthcheck does, and avoids relying on
    host->container port publishing (a separate concern from readiness)."""
    fmt = "\\n%{http_code}"
    result = subprocess.run(
        ["docker", "exec", name, "curl", "-s", "-w", fmt, url],
        capture_output=True,
        text=True,
        timeout=15,
    )
    body, _, status = result.stdout.rpartition("\n")
    if expect_status is not None:
        assert status.strip() == expect_status, result.stdout + result.stderr
    return json.loads(body)
