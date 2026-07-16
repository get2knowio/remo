"""Log redaction: defense-in-depth backstop for FR-028 (T055).

FR-028: SSH secrets, AWS secrets, proxy commands, and WebSocket tokens MUST
be redacted from application logs and browser-visible error details.

The PRIMARY guarantee is architectural, not this module: nothing in
`remo_cli.web` (or `core.remo_host_client` / `core.ssh`) ever interpolates a
raw token, proxy command, or private-key value into a `print`/`logging.*`/
`click.echo` call in the first place --

* `web/tokens.py` never logs the raw token value at all (see its module
  docstring and `tests/unit/web/test_tokens.py`).
* `web/api/terminals.py`'s browser-visible WS error frames are built from
  the fixed `_ERROR_MESSAGES` table (keyed by a closed `ErrorClass` enum),
  never from raw exception text.
* `web/check.py`'s per-instance CLI diagnostic deliberately avoids
  `str(exc)` for SSH-transport-layer failures (where ssh's own stderr could
  in principle echo back a `ProxyCommand`), using only the typed
  `code`/`remediation` from `web.discovery`'s classification instead.
* `core/ssh.py`'s `proxy_cmd` (which embeds the AWS SSM region/target/
  document name, never AWS credentials) is only ever used to build argv/
  option strings, never logged.

This module is the best-effort backstop in case that discipline ever slips:
a `logging.Filter` that pattern-matches token/secret-shaped substrings in
the *formatted* message of any record reaching a configured handler and
masks them before they're written anywhere. It complements, and does not
replace, the audit above (see `tests/unit/web/test_log_redaction.py` for a
source-grep regression guard on the audit itself).
"""

from __future__ import annotations

import logging
import re

__all__ = ["RedactingFilter", "configure_logging"]

#: `key=value`-shaped token/credential assignments (query-string style or
#: CLI-flag style), e.g. `ws_token=abc123`, `token=abc123`, `Token: abc123`.
_TOKEN_KV_RE = re.compile(r"(?i)\b((?:ws_)?token)\s*[=:]\s*([^\s&\"']+)")

#: `ProxyCommand=...` (the SSH option embedding the AWS SSM invocation).
_PROXY_COMMAND_RE = re.compile(r"(?i)(ProxyCommand)\s*=\s*(\S+(?:\s+\S+)*)")

#: PEM-encoded private key material, any key type, whole block.
_PRIVATE_KEY_RE = re.compile(
    r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z0-9 ]*PRIVATE KEY-----"
)

_REDACTED = "<redacted>"


class RedactingFilter(logging.Filter):
    """Masks token/proxy-command/private-key-shaped substrings in log records.

    Attach to a `Handler` (not just a `Logger`) so it applies to every record
    that reaches that handler regardless of which logger emitted it --
    `Logger`-level filters are only consulted for records logged directly on
    that logger, not ones propagated up from children (see
    :func:`configure_logging`).
    """

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:  # noqa: BLE001 - never let redaction break logging.
            return True

        redacted = _TOKEN_KV_RE.sub(lambda m: f"{m.group(1)}={_REDACTED}", message)
        redacted = _PROXY_COMMAND_RE.sub(lambda m: f"{m.group(1)}={_REDACTED}", redacted)
        redacted = _PRIVATE_KEY_RE.sub(_REDACTED, redacted)

        if redacted != message:
            record.msg = redacted
            record.args = ()
        return True


def configure_logging(level: int = logging.INFO) -> None:
    """Install the redaction filter on a handler for the `remo_cli` logger tree.

    Idempotent -- safe to call from `create_app()` on every app construction
    (including once per test) without stacking duplicate handlers.
    """
    logger = logging.getLogger("remo_cli")
    logger.setLevel(level)

    already_configured = any(
        isinstance(existing, logging.StreamHandler)
        and any(isinstance(f, RedactingFilter) for f in existing.filters)
        for existing in logger.handlers
    )
    if already_configured:
        return

    handler = logging.StreamHandler()
    handler.addFilter(RedactingFilter())
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logger.addHandler(handler)
