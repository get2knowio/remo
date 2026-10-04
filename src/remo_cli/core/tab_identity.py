"""Terminal-tab identity and the one-way key derived from it (spec 028 R3/R4).

Pure: reads an environment *mapping* handed in by the caller and does no I/O,
so the precedence rules are table-testable. The identity string itself never
leaves the workstation — only the HMAC-derived key does (FR-002).
"""

from __future__ import annotations

import hashlib
import hmac
import re
from collections.abc import Mapping
from dataclasses import dataclass

#: Innermost multiplexer first: what the user perceives as "this tab".
TAB_VARIABLES: tuple[str, ...] = (
    "TMUX_PANE",
    "WEZTERM_PANE",
    "KITTY_WINDOW_ID",
    "ITERM_SESSION_ID",
    "TERM_SESSION_ID",
    "WT_SESSION",
)

#: The host's entire input validation for a key (safe as a filename).
TAB_KEY_RE = re.compile(r"^[0-9a-f]{32}$")


@dataclass(frozen=True)
class TabIdentity:
    variable: str
    value: str

    @property
    def identity(self) -> str:
        return f"{self.variable}={self.value}"


def detect_tab_identity(env: Mapping[str, str]) -> TabIdentity | None:
    """First set tab variable wins; empty values count as unset (FR-003)."""
    for variable in TAB_VARIABLES:
        value = env.get(variable, "")
        if not value:
            continue
        if variable == "TMUX_PANE":
            # tmux pane ids (%N) repeat across servers; the socket path does
            # not. $TMUX is "<socket>,<server pid>,<session>" — the pid
            # changes when the server restarts, so only field 1 is used.
            socket = env.get("TMUX", "").split(",", 1)[0]
            value = f"{socket}:{value}"
        return TabIdentity(variable, value)
    return None


def derive_tab_key(identity: TabIdentity, secret: bytes) -> str:
    # surrogateescape: os.environ carries undecodable bytes (say, a non-UTF-8
    # tmux socket path) as lone surrogates, which a strict encode would turn
    # into a traceback that blocks `remo shell` (R5). This round-trips them to
    # the original bytes and leaves every valid-UTF-8 identity's key unchanged.
    data = identity.identity.encode("utf-8", "surrogateescape")
    digest = hmac.new(secret, data, hashlib.sha256).hexdigest()
    return digest[:32]
