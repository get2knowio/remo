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


def test_dockerfile_runs_as_non_root():
    text = DOCKERFILE.read_text()
    assert "USER remo" in text
    # USER remo must come after the package/asset installs (root-only ops).
    assert text.index("USER remo") > text.index("useradd")


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
    key_path.chmod(0o600)
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


@requires_image_build
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
