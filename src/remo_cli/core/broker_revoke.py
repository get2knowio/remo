"""Pre-destroy bootstrap-token revocation hook (FR-020).

Called by each provider's `destroy()` before the provider-side delete API call.
If revocation fails, abort destroy unless `force=True` is passed.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

from remo_cli.core.broker_config import get_admin_sa_fnox_key, get_backend
from remo_cli.core.output import print_error, print_info, print_warning
from remo_cli.models.host import KnownHost


class TokenLookupError(RuntimeError):
    """Raised when the backend lookup for an existing token_id fails (network /
    HTTP / parse error). Distinct from "no token to revoke" which is signalled
    by `_lookup_token_id` returning None.
    """


def revoke_before_destroy(host: KnownHost, *, force: bool = False) -> bool:
    """Revoke the bootstrap token at the backend BEFORE the instance is deleted.

    Returns True on success (or no-op when no token_id is registered).
    Returns False on revocation failure unless `force` is set.

    Per FR-020 / contracts/cli-surface.md:
      - exit code 5 (revocation failed, --force not provided) is the caller's
        responsibility — this helper just returns the bool.
    """
    backend = get_backend()
    if not backend:
        # No broker backend configured → nothing to revoke.
        return True

    try:
        token_id = _lookup_token_id(host)
    except TokenLookupError as exc:
        if force:
            print_warning(
                f"{host.name}: token lookup failed ({exc}); proceeding with destroy "
                "due to --force. Any minted bootstrap token will live until its TTL "
                "expires or the backend session is otherwise invalidated."
            )
            return True
        print_error(
            f"{host.name}: token lookup failed: {exc}. "
            "Refusing to destroy without --force (would risk orphaning a usable token)."
        )
        return False

    if not token_id:
        # Pre-feature instance, or token not yet minted. Skip gracefully.
        return True

    from remo_cli.providers import broker as broker_mod  # noqa: PLC0415

    print_info(f"Revoking bootstrap token at {backend} for {host.name}...")
    try:
        broker_mod.revoke_bootstrap_token(
            backend,
            token_id=token_id,
            admin_sa_fnox_key=get_admin_sa_fnox_key(),
        )
    except broker_mod.BackendError as exc:
        if force:
            print_warning(
                f"{host.name}: revocation failed ({exc}); proceeding with destroy "
                "due to --force. The leaked token will live until its TTL expires "
                "or the backend session is otherwise invalidated."
            )
            return True
        print_error(
            f"{host.name}: revocation failed: {exc}. "
            "Refusing to destroy without --force (would orphan a usable token)."
        )
        return False

    print_info(f"{host.name}: bootstrap token revoked.")
    return True


def _lookup_token_id(host: KnownHost) -> str | None:
    """Find the backend-side token identifier for `host` (provider tags / labels).

    The lookup mirrors the cadence-metadata read in `cli/rotate.py`.

    Returns:
        - The token_id string when the backend has one recorded.
        - None when the host has no token to revoke (no server found by that
          name, no `remo_bootstrap_token_id` label key — i.e. "no token minted
          yet" or "pre-feature instance").

    Raises:
        TokenLookupError: when the backend lookup itself fails (network, HTTP,
            JSON parse). Callers (`revoke_before_destroy`) treat this as an
            error that blocks destroy unless --force is passed; never as a
            silent "skip".
    """
    if host.type == "hetzner":
        from remo_cli.providers.hetzner import _hetzner_server_id, _get_hetzner_api_token  # noqa: PLC0415
        import json as _json
        import urllib.request as _ur
        sid = _hetzner_server_id(host.name)
        tok = _get_hetzner_api_token()
        if not (sid and tok):
            # No server by that name in the Hetzner project, or no API token
            # configured locally. Either way, nothing to revoke.
            return None
        req = _ur.Request(
            f"https://api.hetzner.cloud/v1/servers/{sid}",
            headers={"Authorization": f"Bearer {tok}"},
        )
        try:
            with _ur.urlopen(req, timeout=10) as resp:
                payload = _json.loads(resp.read().decode())
        except Exception as exc:  # noqa: BLE001
            raise TokenLookupError(
                f"hetzner labels read failed for {host.name}: {exc}"
            ) from exc
        labels = (payload.get("server") or {}).get("labels") or {}
        return labels.get("remo_bootstrap_token_id")
    if host.type == "aws":
        # AWS revoke address = the per-instance role/profile name (role == profile
        # by construction in `aws._ensure_broker_instance_role`). Per-instance,
        # not per-developer, so destroying one instance's role can't break IMDS
        # creds on a sibling instance owned by the same developer.
        from remo_cli.providers.broker import _safe_role_slug  # noqa: PLC0415
        dev_id = os.environ.get("REMO_DEV_ID", "") or os.environ.get("USER", "remo")
        return f"remo-broker-instance-{dev_id}-{_safe_role_slug(host.name)}"
    # Incus/Proxmox: token_id storage is in container config — defer to a
    # future enhancement once the on-node helper persists it.
    return None


# Passive overdue-rotation reminder (FR-021, T083a) ---------------------------


def overdue_reminders() -> list[str]:
    """Return a list of one-line yellow reminders for overdue instances.

    Called from `cli/main.py` post-command hook (per T083a). Short-circuits
    when REMO_QUIET=1 is set or known_hosts is empty.
    """
    if os.environ.get("REMO_QUIET") == "1":
        return []

    out: list[str] = []
    from remo_cli.cli.rotate import _read_rotation_metadata, _is_overdue  # noqa: PLC0415
    try:
        from remo_cli.core.known_hosts import get_known_hosts
        for host in get_known_hosts():
            try:
                cadence, last, _ = _read_rotation_metadata(host)
            except Exception:  # noqa: BLE001
                continue
            if cadence <= 0:
                continue
            if not _is_overdue(cadence, last):
                continue
            if last is None:
                out.append(
                    f"{host.name}: bootstrap token rotation overdue "
                    f"(no rotation recorded; cadence={cadence}d). "
                    "Run `remo rotate-bootstrap` to refresh."
                )
            else:
                age_days = (datetime.now(timezone.utc) - last).days
                out.append(
                    f"{host.name}: bootstrap token rotation overdue "
                    f"(age={age_days}d ≥ cadence={cadence}d). "
                    "Run `remo rotate-bootstrap` to refresh."
                )
    except Exception:  # noqa: BLE001
        pass
    return out
