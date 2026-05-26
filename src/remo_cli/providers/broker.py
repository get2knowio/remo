"""Shared install / mint / revoke helpers for the remo-broker daemon.

Backend-specific implementations are dispatched by string key. Each implementation
must be idempotent (rotating/revoking an already-revoked token returns success).

The broker daemon itself lives in the `get2knowio/remo-broker` repository. This
module only handles the laptop-side admin operations (sub-token mint + revoke).
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

SUPPORTED_BACKENDS: tuple[str, ...] = ("1password", "vault", "aws-sm", "age-git")


def _safe_role_slug(instance_id: str) -> str:
    """Sanitize a remo resource name to a fragment safe for IAM role names.

    Why: IAM role names accept `[A-Za-z0-9+=,.@_-]` up to 64 chars total. We
    compose `remo-broker-instance-<dev>-<slug>`, so we strip the instance slug
    to the conservative `[A-Za-z0-9_-]` subset and cap it at 32 chars to leave
    headroom for the developer id prefix.
    """
    return re.sub(r"[^A-Za-z0-9_-]", "-", instance_id)[:32]


class BackendError(RuntimeError):
    """Raised when a backend mint/revoke call fails or the backend is unsupported."""


def _check_backend(backend: str) -> None:
    if backend not in SUPPORTED_BACKENDS:
        raise BackendError(
            f"unsupported backend {backend!r}; expected one of {SUPPORTED_BACKENDS}"
        )


def _resolve_admin_sa(admin_sa: str | None, admin_sa_fnox_key: str | None) -> str:
    """Resolve the admin SA token. Either passed directly or read from fnox."""
    if admin_sa:
        return admin_sa
    if not admin_sa_fnox_key:
        raise BackendError(
            "no admin SA token provided. Pass `admin_sa=` or `admin_sa_fnox_key=`."
        )
    from remo_cli.core import fnox  # local import to avoid cycles
    try:
        return fnox.get(admin_sa_fnox_key)
    except fnox.FnoxError as exc:
        raise BackendError(f"could not read admin SA from fnox: {exc}") from exc


# ---------------------------------------------------------------------------
# 1Password — SCIM ServiceAccountTokens
# ---------------------------------------------------------------------------


def _onepassword_mint(admin_sa: str, instance_id: str, dev_id: str) -> dict[str, str]:
    """Mint a SCIM-managed Service Account token via the 1Password Connect API.

    The admin_sa here is the SCIM-permissioned admin token. The minted token's
    backend-side identifier is the SCIM ID returned by the create call.
    Implementation note: 1Password's Connect API surface evolves; this code
    targets the v2/ServiceAccountTokens shape current as of 2026-05.
    """
    url = "https://my.1password.com/api/v2/ServiceAccountTokens"
    body = json.dumps(
        {
            "name": f"remo-broker/{dev_id}/{instance_id}",
            "scope": "read",
            "tags": ["remo-broker", f"dev:{dev_id}", f"instance:{instance_id}"],
        }
    ).encode()
    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {admin_sa}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            payload = json.loads(resp.read().decode())
    except urllib.error.URLError as exc:
        raise BackendError(f"1Password mint failed: {exc}") from exc
    token = payload.get("token") or payload.get("value") or ""
    token_id = str(payload.get("id") or payload.get("scim_id") or "")
    if not token or not token_id:
        # Never include payload values in the message — the token may be present
        # under an unexpected key during schema drift. Only surface key names.
        raise BackendError(
            f"1Password mint returned no token/id; payload keys: {sorted(payload.keys())}"
        )
    return {"token": token, "token_id": token_id}


def _onepassword_revoke(admin_sa: str, token_id: str) -> None:
    url = f"https://my.1password.com/api/v2/ServiceAccountTokens/{urllib.parse.quote(token_id)}"
    req = urllib.request.Request(
        url,
        method="DELETE",
        headers={"Authorization": f"Bearer {admin_sa}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            resp.read()
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return  # Already revoked — idempotent.
        raise BackendError(f"1Password revoke failed: {exc}") from exc
    except urllib.error.URLError as exc:
        raise BackendError(f"1Password revoke failed: {exc}") from exc


# ---------------------------------------------------------------------------
# HashiCorp Vault / OpenBao — token-create + token-revoke-accessor
# ---------------------------------------------------------------------------


def _vault_addr(extra: dict[str, Any] | None) -> str:
    if extra and extra.get("vault_addr"):
        return str(extra["vault_addr"])
    import os
    return os.environ.get("VAULT_ADDR", "http://127.0.0.1:8200")


def _vault_mint(
    admin_sa: str, instance_id: str, dev_id: str, extra: dict[str, Any] | None
) -> dict[str, str]:
    url = f"{_vault_addr(extra)}/v1/auth/token/create"
    body = json.dumps(
        {
            "display_name": f"remo-broker-{dev_id}-{instance_id}",
            "policies": ["remo-broker"],
            "ttl": "168h",  # 7 days; rotation refreshes per FR-021.
        }
    ).encode()
    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={"X-Vault-Token": admin_sa, "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            payload = json.loads(resp.read().decode())
    except urllib.error.URLError as exc:
        raise BackendError(f"Vault mint failed: {exc}") from exc
    auth = payload.get("auth") or {}
    token = auth.get("client_token") or ""
    accessor = auth.get("accessor") or ""
    if not token or not accessor:
        # Never include payload values in the message — the token may be present
        # under an unexpected key during schema drift. Only surface key names.
        raise BackendError(
            f"Vault mint returned no token/accessor; "
            f"payload keys: {sorted(payload.keys())}; auth keys: {sorted(auth.keys())}"
        )
    return {"token": token, "token_id": accessor}


def _vault_revoke(admin_sa: str, accessor: str, extra: dict[str, Any] | None) -> None:
    url = f"{_vault_addr(extra)}/v1/auth/token/revoke-accessor"
    body = json.dumps({"accessor": accessor}).encode()
    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={"X-Vault-Token": admin_sa, "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            resp.read()
    except urllib.error.HTTPError as exc:
        if exc.code in {400, 404}:
            return  # Vault returns 400 on already-revoked accessor.
        raise BackendError(f"Vault revoke failed: {exc}") from exc
    except urllib.error.URLError as exc:
        raise BackendError(f"Vault revoke failed: {exc}") from exc


# ---------------------------------------------------------------------------
# AWS Secrets Manager — revocation handled by IAM role manipulation
# ---------------------------------------------------------------------------


def _aws_sm_mint(instance_id: str, dev_id: str) -> dict[str, str]:
    """For AWS, the "token" is the IAM instance profile name; no on-disk file.

    Returns a sentinel; the actual delivery happens via
    `providers.aws._ensure_broker_instance_role`. We hand back the role name
    as token_id so the revoke path can find it later.

    Why per-instance: a single developer can have multiple concurrent remo
    instances; sharing one role across them means destroying one would attach
    the deny-all policy + delete the role and break IMDS creds on the others.
    `instance_id` here is the human-friendly resource name passed by the caller.
    """
    safe = _safe_role_slug(instance_id)
    return {
        "token": "",  # No on-disk token for AWS — IMDS gives the role creds.
        "token_id": f"remo-broker-instance-{dev_id}-{safe}",
    }


def _aws_sm_revoke(token_id: str) -> None:
    """Attach a deny-all inline policy to the role and tear it down.

    The actual destruction must happen AFTER `ec2.terminate_instances` (see
    research R3) so EC2 isn't holding the role attachment. The caller in
    `cli/destroy.py` is responsible for the ordering.
    """
    try:
        import boto3  # noqa: PLC0415
    except ImportError:
        raise BackendError(
            "boto3 is required for AWS Secrets Manager revocation. "
            "Install with `uv pip install boto3` or `uv sync --extra aws`."
        )
    iam = boto3.client("iam")
    # Belt-and-suspenders: if the role is already gone (idempotent re-revoke,
    # or never minted), skip the deny-all + delete dance silently.
    try:
        iam.get_role(RoleName=token_id)
    except iam.exceptions.NoSuchEntityException:
        return
    from remo_cli.providers import aws as aws_provider
    aws_provider._attach_broker_deny_all_policy(iam, token_id)  # noqa: SLF001
    aws_provider._delete_broker_instance_role(iam, token_id, token_id)  # noqa: SLF001


# ---------------------------------------------------------------------------
# age + git — no per-instance revocation (FR-003 warning)
# ---------------------------------------------------------------------------


def _age_git_mint(instance_id: str, dev_id: str) -> dict[str, str]:
    raise BackendError(
        "age + git backend has no per-instance minting primitive. "
        "Re-run `remo init` with a different backend, or accept the "
        "laptop-unlock-per-session downgrade by storing the token in fnox."
    )


def _age_git_revoke(token_id: str) -> None:  # noqa: ARG001
    # No-op with a warning. The token lives only inside the age-encrypted file;
    # removing the recipient key + re-encrypting is a manual operator step.
    return


# ---------------------------------------------------------------------------
# Public dispatchers
# ---------------------------------------------------------------------------


def mint_bootstrap_token(
    backend: str,
    *,
    instance_id: str,
    dev_id: str,
    admin_sa: str | None = None,
    admin_sa_fnox_key: str | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, str]:
    """Mint a fresh per-instance sub-token at the configured backend.

    Returns ``{"token": <secret string>, "token_id": <backend-side identifier>}``.
    The ``token_id`` is what gets persisted in provider tags / labels so the
    revoke path can address the token later.
    """
    _check_backend(backend)
    if backend == "aws-sm":
        return _aws_sm_mint(instance_id=instance_id, dev_id=dev_id)
    if backend == "age-git":
        return _age_git_mint(instance_id=instance_id, dev_id=dev_id)

    admin = _resolve_admin_sa(admin_sa, admin_sa_fnox_key)
    if backend == "1password":
        return _onepassword_mint(admin, instance_id=instance_id, dev_id=dev_id)
    if backend == "vault":
        return _vault_mint(admin, instance_id=instance_id, dev_id=dev_id, extra=extra)
    raise BackendError(f"no mint impl for {backend!r}")


def revoke_bootstrap_token(
    backend: str,
    *,
    token_id: str,
    admin_sa: str | None = None,
    admin_sa_fnox_key: str | None = None,
    extra: dict[str, Any] | None = None,
) -> None:
    """Revoke a sub-token at the configured backend. Idempotent on already-revoked tokens."""
    _check_backend(backend)
    if backend == "aws-sm":
        _aws_sm_revoke(token_id)
        return
    if backend == "age-git":
        _age_git_revoke(token_id)
        return

    admin = _resolve_admin_sa(admin_sa, admin_sa_fnox_key)
    if backend == "1password":
        _onepassword_revoke(admin, token_id)
        return
    if backend == "vault":
        _vault_revoke(admin, token_id, extra=extra)
        return
    raise BackendError(f"no revoke impl for {backend!r}")
