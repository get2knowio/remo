# Contract: Best-effort service-key revocation

Supports FR-015..FR-018. Symmetric to the existing `authorize_service_key` / `build_authorize_command` in `core/web_adopt.py`; reuses the operator's ambient SSH access (never the service identity) and the same marker.

## When it runs

During `run_push`, after the per-instance loop and before/around the `PUT`, for every instance in `removed = cached_names − current_names` that:

- has a retained connection tuple in the push cache (v3), AND
- is **direct-access** (`access != "ssm"`).

SSM instances and instances with no cached connection tuple are reported `could_not_revoke` with a "revoke manually" remediation (FR-017) — never attempted, never fatal.

## `build_revoke_command() -> str`

A single POSIX-sh command that removes the service line and nothing else:

```sh
set -e; umask 077
[ -f ~/.ssh/authorized_keys ] || exit 0
tmp="$(mktemp ~/.ssh/.authorized_keys.remo.XXXXXX)"
grep -vF ' remo-web@' ~/.ssh/authorized_keys > "$tmp" || true
chmod 600 "$tmp"
mv "$tmp" ~/.ssh/authorized_keys
```

- Uses the exact `AUTHORIZED_KEYS_MARKER` (`" remo-web@"`) already used for install.
- Removes **only** marker lines; all other authorized keys are preserved (FR-016).
- Missing file → success no-op; re-running against an already-revoked instance → no-op (FR-016, idempotent).
- Atomic (temp-file + `mv`), 0600.

> Note: revocation removes **any** `remo-web@` line (matching install-time filtering), which correctly clears a line from any deployment id. This is the desired lifecycle behavior when an operator removes an instance from a workstation's registry.

## `revoke_service_key(host) -> tuple[bool, str]`

Mirrors `authorize_service_key`: builds `build_ssh_base_cmd(host, extra_opts=["-o","BatchMode=yes","-o","ConnectTimeout=10"])`, appends `build_revoke_command()`, runs with a timeout. Returns `(ok, detail)`; **never raises** for per-instance connection failures. Exit 255 / timeout / OSError → `(False, <reason>)`.

## Reporting (FR-017/FR-018)

Each removed instance yields a `RevocationOutcome` (`revoked` | `could_not_revoke` + detail + remediation), rendered in the push summary alongside adoption outcomes. `could_not_revoke` (unreachable, no access, SSM, no cached tuple, remote error) prints remediation guidance to delete the `remo-web@` line manually and **does not** change the overall exit code — the push still completes (exit 0).
