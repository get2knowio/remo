"""Host-info record read by the pre-connect probe (spec 030).

The host side writes ``~/.local/state/remo/host-info`` (``key=value`` lines,
see ``specs/030-local-host-ssh/contracts/host-probe.md``). Parsing is pure and
never raises: this is remote input and an older host simply has no record.
"""

from __future__ import annotations

from dataclasses import dataclass

HOST_KINDS = frozenset({"local", "remote"})


@dataclass(frozen=True)
class HostInfo:
    host_kind: str | None = None
    menu_command: bool = False
    installed_by: str | None = None

    @property
    def is_local_managed(self) -> bool:
        return self.host_kind == "local"

    @classmethod
    def parse(cls, text: str | None) -> HostInfo:
        host_kind: str | None = None
        menu_command = False
        installed_by: str | None = None
        for raw in (text or "").splitlines():
            key, sep, value = raw.strip().partition("=")
            if not sep:
                continue
            key = key.strip()
            value = value.strip()
            if key == "host_kind" and value in HOST_KINDS:
                host_kind = value
            elif key == "menu_command":
                menu_command = value == "1"
            elif key == "installed_by":
                installed_by = value or None
        return cls(host_kind, menu_command, installed_by)
