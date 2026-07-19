"""Runtime configuration for the Remo web service."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from remo_cli.core.config import get_remo_home_readonly

# Plain stdlib dataclass rather than pydantic's BaseSettings: pydantic is
# already a transitive dependency of the `web` extra (via FastAPI), but this
# module has no other reason to require pydantic v2's settings machinery, and
# a dataclass keeps config loading trivially testable without instantiating
# any pydantic model classes.

_PREFIX = "REMO_WEB_"


def _env_str(name: str, default: str) -> str:
    return os.environ.get(_PREFIX + name, default)


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(_PREFIX + name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(_PREFIX + name)
    if raw is None or not raw.strip():
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _env_list(name: str, default: list[str]) -> list[str]:
    raw = os.environ.get(_PREFIX + name)
    if raw is None or not raw.strip():
        return list(default)
    return [item.strip() for item in raw.split(",") if item.strip()]


def _default_frontend_dist_dir() -> Path:
    """Resolve the built frontend's ``dist/`` directory.

    ``src/remo_cli/web/config.py`` -> repo_root/frontend/dist. Overridable via
    ``REMO_WEB_FRONTEND_DIST_DIR`` (e.g. for container images that place the
    build output elsewhere).
    """
    override = os.environ.get(_PREFIX + "FRONTEND_DIST_DIR")
    if override:
        return Path(override)
    return Path(__file__).resolve().parent.parent.parent.parent / "frontend" / "dist"


@dataclass
class WebSettings:
    """Runtime configuration for the Remo web service.

    Every field is resolved from an environment variable prefixed
    ``REMO_WEB_`` at instantiation time, with a safe default when the
    variable is unset — so ``WebSettings()`` works with zero env vars set
    (local dev/tests), while a container can override everything via env
    alone (12-factor style).
    """

    bind_host: str = field(default_factory=lambda: _env_str("BIND_HOST", "127.0.0.1"))
    bind_port: int = field(default_factory=lambda: _env_int("BIND_PORT", 8080))

    # Discovery (US1): bounded concurrency + per-host timeout + result cache.
    discovery_concurrency: int = field(
        default_factory=lambda: _env_int("DISCOVERY_CONCURRENCY", 8)
    )
    discovery_timeout_s: float = field(
        default_factory=lambda: _env_float("DISCOVERY_TIMEOUT_S", 10.0)
    )
    discovery_cache_ttl_s: float = field(
        default_factory=lambda: _env_float("DISCOVERY_CACHE_TTL_S", 30.0)
    )

    # Terminal caps (Clarifications Q3 defaults: 32 global / 16 per-client).
    terminal_cap_global: int = field(
        default_factory=lambda: _env_int("TERMINAL_CAP_GLOBAL", 32)
    )
    terminal_cap_per_client: int = field(
        default_factory=lambda: _env_int("TERMINAL_CAP_PER_CLIENT", 16)
    )

    # Single-use WS token TTL (Clarifications Q4 default: 30s).
    ws_token_ttl_s: float = field(default_factory=lambda: _env_float("WS_TOKEN_TTL_S", 30.0))

    # Host/Origin allowlists (FR-048). Safe localhost-only defaults for dev;
    # production deployments MUST set these explicitly (no wildcard, ever).
    allowed_hosts: list[str] = field(
        default_factory=lambda: _env_list("ALLOWED_HOSTS", ["127.0.0.1", "localhost"])
    )
    allowed_origins: list[str] = field(
        default_factory=lambda: _env_list(
            "ALLOWED_ORIGINS",
            ["http://127.0.0.1:8080", "http://localhost:8080"],
        )
    )

    # Directory for SSH ControlMaster sockets (R3/R5). The web service points
    # this at a writable tmpfs (default /run/remo-ssh) so SSH key/config
    # mounts can stay read-only. Passed through to
    # core.ssh.build_ssh_base_cmd(..., control_dir=...) / exported as
    # $REMO_SSH_CONTROL_DIR by the process bootstrap (T052).
    ssh_control_dir: str = field(
        default_factory=lambda: _env_str("SSH_CONTROL_DIR", "/run/remo-ssh")
    )

    # Directory the built frontend SPA is served from (same-origin, FR-038).
    frontend_dist_dir: Path = field(default_factory=_default_frontend_dist_dir)

    # -- Ephemeral device pairing (012-web-adopt-pairing) -------------------
    #
    # The static REMO_WEB_API_TOKEN gate of 011 is removed (FR-021). The
    # /api/v1/setup surface is now dormant (404) unless a live pairing session
    # exists; a session is minted from the adopt page and gated by operator
    # authentication (see web/pairing.py + web/operator_auth.py).

    # Sliding idle TTL for a pairing session, seconds (FR-002, default 15 min).
    pairing_ttl_s: float = field(default_factory=lambda: _env_float("PAIRING_TTL_S", 900.0))

    # Operator-authentication mode gating the browser mint endpoint (FR-009):
    #   "forward" -> require a trusted proxy-injected identity header
    #                (forward_auth_header MUST be set; fail-fast otherwise).
    #   "none"    -> network-restricted posture: mint without a credential
    #                (loud, explicit opt-in for loopback/dev, FR-013).
    #   ""        -> unset: minting is disabled (mint returns 403; fail closed).
    operator_auth: str = field(default_factory=lambda: _env_str("OPERATOR_AUTH", "").strip())

    # Trusted forward-auth identity header name (FR-009). No baked-in default:
    # it varies by proxy (X-Forwarded-User, Remote-User, ...) and enabling
    # forward auth without naming it is a fail-fast config error.
    forward_auth_header: str = field(
        default_factory=lambda: _env_str("FORWARD_AUTH_HEADER", "").strip()
    )

    # Service identity state directory (011-web-adopt, research R1).
    # Everything the adopted service owns lives under
    # <REMO_HOME>/web-identity/: id_ed25519 + id_ed25519.pub (service
    # keypair), known_hosts (service-managed SSH host keys), state.json
    # (deployment id + adoption metadata). Resolved once at instantiation via
    # the read-only-safe REMO_HOME accessor (no mkdir side effect).
    web_identity_dir: Path = field(
        default_factory=lambda: get_remo_home_readonly() / "web-identity"
    )

    # -- Service identity paths (research R1 layout) -----------------------

    @property
    def service_private_key_path(self) -> Path:
        return self.web_identity_dir / "id_ed25519"

    @property
    def service_public_key_path(self) -> Path:
        return self.web_identity_dir / "id_ed25519.pub"

    @property
    def service_known_hosts_path(self) -> Path:
        return self.web_identity_dir / "known_hosts"

    @property
    def service_state_path(self) -> Path:
        return self.web_identity_dir / "state.json"

    # -- Resolved SSH options for web call sites (research R6) -------------
    #
    # Adopted mode -> the service's own identity/known_hosts under
    # web-identity/; every other mode (mounted, unconfigured, broken) ->
    # None, which leaves core.ssh.build_ssh_opts()'s argv byte-identical to
    # today's (ambient ~/.ssh defaults -- FR-005/FR-023 regression safety).
    # Computed on demand from the detected configuration state, never stored,
    # so a registry PUT that flips the state to adopted is picked up by the
    # next SSH invocation without a restart.

    @property
    def ssh_identity_file(self) -> str | None:
        if self._service_identity_active():
            return str(self.service_private_key_path)
        return None

    @property
    def ssh_known_hosts_file(self) -> str | None:
        if self._service_identity_active():
            return str(self.service_known_hosts_path)
        return None

    def _service_identity_active(self) -> bool:
        # Lazy import: web.state imports WebSettings from this module at
        # module level; deferring the reverse import to call time keeps the
        # cycle harmless.
        from remo_cli.web.state import ConfigurationState, detect_state

        return detect_state(self) is ConfigurationState.ADOPTED
