"""Pre-destroy bootstrap-token revocation hook (FR-020).

Called by each provider's `destroy()` before the provider-side delete API call.
If revocation fails, abort destroy unless `force=True` is passed.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

from remo_cli.core.output import print_error, print_info, print_warning
from remo_cli.models.host import KnownHost


def revoke_before_destroy(host: KnownHost, *, force: bool = False) -> bool:
    """Revoke the bootstrap token at the backend BEFORE the instance is deleted.

    Returns True on success (or no-op when no token_id is registered).
    Returns False on revocation failure unless `force` is set.

    Per FR-020 / contracts/cli-surface.md:
      - exit code 5 (revocation failed, --force not provided) is the caller's
        responsibility — this helper just returns the bool.
    """
    backend = os.environ.get("REMO_BROKER_BACKEND", "")
    if not backend:
        # No broker backend configured → nothing to revoke.
        return True

    token_id = _lookup_token_id(host)
    if not token_id:
        # Pre-feature instance, or token not yet minted. Skip gracefully.
        return True

    from remo_cli.providers import broker as broker_mod  # noqa: PLC0415

    print_info(f"Revoking bootstrap token at {backend} for {host.name}...")
    try:
        broker_mod.revoke_bootstrap_token(
            backend,
            token_id=token_id,
            admin_sa_fnox_key=os.environ.get("REMO_BROKER_ADMIN_SA_KEY"),
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
    """
    if host.type == "hetzner":
        try:
            from remo_cli.providers.hetzner import _hetzner_server_id, _get_hetzner_api_token  # noqa: PLC0415
            import json as _json
            import urllib.request as _ur
            sid = _hetzner_server_id(host.name)
            tok = _get_hetzner_api_token()
            if sid and tok:
                req = _ur.Request(
                    f"https://api.hetzner.cloud/v1/servers/{sid}",
                    headers={"Authorization": f"Bearer {tok}"},
                )
                with _ur.urlopen(req, timeout=10) as resp:
                    payload = _json.loads(resp.read().decode())
                labels = (payload.get("server") or {}).get("labels") or {}
                return labels.get("remo:bootstrap-token-id")
        except Exception:  # noqa: BLE001
            return None
    if host.type == "aws":
        # AWS revoke address = the per-developer role/profile name. Same string
        # under both because we keep names identical (see
        # `aws._ensure_broker_instance_role`).
        dev_id = os.environ.get("REMO_DEV_ID", "") or os.environ.get("USER", "remo")
        return f"remo-broker-instance-{dev_id}"
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
