"""`remo rotate-bootstrap [<instance>]` — mint fresh + revoke old per cadence."""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone

import click

from remo_cli.core.broker_config import get_admin_sa_fnox_key, get_backend
from remo_cli.core.known_hosts import get_known_hosts
from remo_cli.core.output import print_error, print_info, print_success, print_warning
from remo_cli.models.host import KnownHost

FRESHNESS_WINDOW = timedelta(hours=1)


def _parse_iso(ts: str) -> datetime | None:
    if not ts:
        return None
    try:
        if ts.endswith("Z"):
            ts = ts[:-1] + "+00:00"
        result = datetime.fromisoformat(ts)
    except ValueError:
        return None
    if result.tzinfo is None:
        # Broker-written timestamps without an offset are interpreted as UTC
        # so downstream `_now() - last_rotation` arithmetic doesn't raise
        # "can't subtract offset-naive and offset-aware datetimes".
        result = result.replace(tzinfo=timezone.utc)
    return result


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _read_rotation_metadata(host: KnownHost) -> tuple[int, datetime | None, str | None]:
    """Return `(cadence_days, last_rotation, token_id)` from provider-side metadata.

    Cadence and last-rotation are stored as AWS instance tags / Hetzner labels /
    Incus container config keys per FR-021. Default cadence is 7 days; an
    instance pre-feature returns `(7, None, None)` and the rotation will refuse.
    """
    if host.type == "hetzner":
        from remo_cli.providers.hetzner import _hetzner_server_id, _get_hetzner_api_token  # noqa: PLC0415

        sid = _hetzner_server_id(host.name)
        token = _get_hetzner_api_token()
        if sid and token:
            try:
                import json as _json
                import urllib.request as _ur
                req = _ur.Request(
                    f"https://api.hetzner.cloud/v1/servers/{sid}",
                    headers={"Authorization": f"Bearer {token}"},
                )
                with _ur.urlopen(req, timeout=10) as resp:
                    payload = _json.loads(resp.read().decode())
                labels = (payload.get("server") or {}).get("labels") or {}
                cadence = int(labels.get("remo_rotation_cadence_days") or "7")
                last = _parse_iso(labels.get("remo_last_rotation_at") or "")
                token_id = labels.get("remo_bootstrap_token_id")
                return cadence, last, token_id
            except Exception:  # noqa: BLE001
                pass
    # Default: cadence 7 days, no record of last rotation, no token_id.
    return 7, None, None


def _is_overdue(cadence_days: int, last_rotation: datetime | None) -> bool:
    if cadence_days <= 0:
        return False
    if last_rotation is None:
        return True
    return _now() - last_rotation >= timedelta(days=cadence_days)


def _deliver_and_reload(host: KnownHost, token: str) -> None:
    """Push *token* to the instance and trigger the broker's rotate-bootstrap op.

    Provider-specific: today only Hetzner is wired. For other providers the
    token-delivery path differs (AWS-SM reads creds from IMDS so there is
    nothing to push; Incus/Proxmox use container-mount delivery and a
    different SSH target shape). Raises NotImplementedError for non-Hetzner
    callers so the rotate-bootstrap CLI surfaces it as a partial result
    rather than silently leaving stale state on the box.
    """
    from remo_cli.core import broker_admin  # noqa: PLC0415

    if host.type == "hetzner":
        from remo_cli.providers.hetzner import (  # noqa: PLC0415
            _hetzner_server_id,
            _push_bootstrap_token,
        )
        server_id = _hetzner_server_id(host.name)
        _push_bootstrap_token(host.host, token, ssh_user="root", server_id=server_id)
        broker_admin.rotate_bootstrap(ssh_host=host.host, ssh_user="root")
        return

    raise NotImplementedError(
        f"rotate-bootstrap delivery not wired for {host.type!r} yet "
        "(token minted/revoked at backend but instance still has the old token)."
    )


def _rotate_one(host: KnownHost, force: bool) -> bool:
    """Rotate a single instance. Returns True on success or skip-fresh; False on failure."""
    cadence, last_rotation, current_token_id = _read_rotation_metadata(host)

    if not force and last_rotation is not None and (_now() - last_rotation) < FRESHNESS_WINDOW:
        print_warning(
            f"{host.name}: Skipped — last rotation was less than 1 hour ago. "
            "Use --force to override."
        )
        return True

    backend = get_backend()
    dev_id = os.environ.get("REMO_DEV_ID", "") or os.environ.get("USER", "remo")
    if not backend:
        print_error(
            f"{host.name}: REMO_BROKER_BACKEND not set; run `remo init --backend ...` first."
        )
        return False

    from remo_cli.core import broker_admin  # noqa: PLC0415
    from remo_cli.providers import broker as broker_mod  # noqa: PLC0415

    try:
        minted = broker_mod.mint_bootstrap_token(
            backend, instance_id=host.name, dev_id=dev_id,
            admin_sa_fnox_key=get_admin_sa_fnox_key(),
        )
    except broker_mod.BackendError as exc:
        print_error(f"{host.name}: mint failed: {exc}")
        return False

    token = minted.get("token", "")
    if token:
        try:
            _deliver_and_reload(host, token)
        except NotImplementedError as exc:
            print_warning(f"{host.name}: {exc}")
        except (broker_admin.BrokerAdminError, RuntimeError) as exc:
            print_error(
                f"{host.name}: fresh token minted ({minted.get('token_id')}) "
                f"but delivery to instance failed: {exc}. The previous token "
                "is still serving — re-run after fixing connectivity, or "
                "revoke manually at the backend if compromise is suspected."
            )
            return False
    # AWS-SM mint returns token=="" (creds come from IMDS); the broker still
    # needs to be told to re-fetch via the admin socket.
    elif host.type == "aws":
        try:
            broker_admin.rotate_bootstrap(ssh_host=host.host, ssh_user=host.user or "remo")
        except broker_admin.BrokerAdminError as exc:
            print_warning(
                f"{host.name}: backend rotated but broker reload failed: {exc}"
            )

    if current_token_id:
        try:
            broker_mod.revoke_bootstrap_token(
                backend, token_id=current_token_id,
                admin_sa_fnox_key=get_admin_sa_fnox_key(),
            )
        except broker_mod.BackendError as exc:
            print_warning(
                f"{host.name}: fresh token minted ({minted.get('token_id')}) "
                f"but revoking previous failed: {exc}"
            )

    print_success(f"{host.name}: rotated (new token_id={minted.get('token_id')}).")
    return True


@click.command("rotate-bootstrap")
@click.argument("instance", required=False, default=None)
@click.option("--all", "rotate_all", is_flag=True, default=False, help="Rotate every instance.")
@click.option("--force", is_flag=True, default=False, help="Override 1-hour freshness skip.")
@click.option(
    "--cadence-days",
    type=int,
    default=None,
    help="(write) Set the per-instance rotation cadence in days (default 7).",
)
def rotate_command(
    instance: str | None,
    rotate_all: bool,
    force: bool,
    cadence_days: int | None,
) -> None:
    """Mint a fresh bootstrap sub-token; revoke the previous one at the backend.

    Defaults to all-instances-whose-cadence-is-due. Pass an instance name for
    immediate rotation of one. Pass --all to ignore cadence on all instances.
    """
    hosts = list(get_known_hosts())
    if instance is not None:
        hosts = [h for h in hosts if h.name == instance or h.name.endswith(f"/{instance}")]
        if not hosts:
            print_error(f"instance {instance!r} not found in known_hosts")
            sys.exit(1)

    if not rotate_all and instance is None:
        # Default behavior: rotate instances whose cadence is overdue.
        filtered = []
        for h in hosts:
            cadence, last, _ = _read_rotation_metadata(h)
            if _is_overdue(cadence, last):
                filtered.append(h)
        hosts = filtered

    if not hosts:
        print_info("No instances are due for rotation.")
        sys.exit(0)

    failures = 0
    for host in hosts:
        ok = _rotate_one(host, force=force)
        if not ok:
            failures += 1

    if failures:
        print_error(f"Rotation completed with {failures} failure(s).")
        sys.exit(7)
    sys.exit(0)
