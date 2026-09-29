"""The `auto` devcontainer runtime and its host-side resolution (spec 026).

``ansible/tasks/resolve_devcontainer_runtime.yml`` turns the request
(``auto`` | ``deacon`` | ``devcontainer``) into the runtime a host actually
gets, and ``tasks/configure_dev_tools.yml`` wires everything off that fact.
Two kinds of check:

* structural — the resolver runs first, both runtime roles are gated on the
  effective value and the ``configure_devcontainers`` toggle, the marker is
  written after ``user_setup`` and only when the toggle is on, the warning
  fires only for deacon on a nested-overlayfs host, and every registered
  access is defensive (Principle V);
* rendered — the resolver's Jinja expressions are evaluated with jinja2 for
  every host shape the spec names, because reading YAML proves nothing
  about what Jinja does with it.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml
from jinja2 import Environment

REPO_ROOT = Path(__file__).resolve().parents[2]
RESOLVER = REPO_ROOT / "ansible" / "tasks" / "resolve_devcontainer_runtime.yml"
CONFIGURE = REPO_ROOT / "ansible" / "tasks" / "configure_dev_tools.yml"
USER_SETUP_DEFAULTS = REPO_ROOT / "ansible" / "roles" / "user_setup" / "defaults" / "main.yml"

RESOLVER_TASKS: list[dict[str, Any]] = yaml.safe_load(RESOLVER.read_text())
CONFIGURE_TASKS: list[dict[str, Any]] = yaml.safe_load(CONFIGURE.read_text())
DEFAULTS: dict[str, Any] = yaml.safe_load(USER_SETUP_DEFAULTS.read_text())


def _named(tasks: list[dict[str, Any]], name: str) -> dict[str, Any]:
    matches = [t for t in tasks if t.get("name") == name]
    assert len(matches) == 1, f"expected exactly one task named {name!r}, got {len(matches)}"
    return matches[0]


def _when(task: dict[str, Any]) -> list[str]:
    when = task.get("when", [])
    return [when] if isinstance(when, str) else list(when)


# ---------------------------------------------------------------------------
# Structural
# ---------------------------------------------------------------------------


def test_resolver_runs_first_in_configure_dev_tools() -> None:
    first = CONFIGURE_TASKS[0]
    assert first["ansible.builtin.include_tasks"] == "tasks/resolve_devcontainer_runtime.yml"


def test_runtime_roles_are_gated_on_effective_runtime_and_toggle() -> None:
    ref = _named(CONFIGURE_TASKS, "Install Dev Containers CLI (reference devcontainer runtime)")
    deacon = _named(CONFIGURE_TASKS, "Install deacon (devcontainer runtime)")
    assert ref["ansible.builtin.include_role"]["name"] == "devcontainers"
    assert deacon["ansible.builtin.include_role"]["name"] == "deacon"
    for task, op in ((ref, "!="), (deacon, "==")):
        conds = _when(task)
        assert "configure_devcontainers | default(true) | bool" in conds
        assert any(
            f"(devcontainer_runtime_effective | default(devcontainer_runtime)) {op} 'deacon'" == c
            for c in conds
        ), conds


def test_marker_is_written_after_user_setup_only_with_the_toggle() -> None:
    names = [t.get("name") for t in CONFIGURE_TASKS]
    marker = _named(CONFIGURE_TASKS, "Record the devcontainer runtime for this host")
    assert names.index("Record the devcontainer runtime for this host") > names.index(
        "Configure user environment"
    )
    copy = marker["ansible.builtin.copy"]
    assert copy["dest"] == "/home/{{ remo_user }}/.remo-devcontainer-runtime"
    assert copy["content"] == "{{ devcontainer_runtime_effective | default(devcontainer_runtime) }}\n"
    assert copy["owner"] == "{{ remo_user }}" and copy["mode"] == "0644"
    assert _when(marker) == ["configure_devcontainers | default(true) | bool"]


def test_warning_fires_only_for_deacon_on_a_nested_overlayfs_host() -> None:
    warning = next(t for t in CONFIGURE_TASKS if str(t.get("name", "")).startswith("Devcontainer runtime deacon on a nested-overlayfs host"))
    conds = _when(warning)
    assert "(devcontainer_runtime_effective | default(devcontainer_runtime)) == 'deacon'" in conds
    assert "docker_nested_overlayfs | default(false) | bool" in conds
    assert "docs/nested-overlayfs.md" in warning["name"]


def test_resolver_registered_accesses_are_defensive() -> None:
    text = RESOLVER.read_text()
    for var in (
        "devcontainer_runtime_marker_stat.stat",
        "devcontainer_runtime_marker.content",
        "devcontainer_runtime_npm_prefix.rc",
        "devcontainer_runtime_npm_prefix.stdout",
        "devcontainer_runtime_reference_cli.stat",
    ):
        for line in text.splitlines():
            if var in line and "is defined" not in line:
                assert "default(" in line, line
    assert re.search(r"\.rc\s*==\s*0", text) is None or "default(" in text


def test_resolver_probes_never_fail_the_play() -> None:
    for name in (
        "Probe the recorded devcontainer runtime marker",
        "Read the recorded devcontainer runtime marker",
        "Find the npm global prefix (legacy reference-CLI probe)",
        "Probe for an already-installed reference devcontainer CLI",
    ):
        task = _named(RESOLVER_TASKS, name)
        assert task.get("failed_when") is False, name
    npm = _named(RESOLVER_TASKS, "Find the npm global prefix (legacy reference-CLI probe)")
    assert npm.get("changed_when") is False


def test_resolver_decision_is_in_a_task_name() -> None:
    decision = [t for t in RESOLVER_TASKS if str(t.get("name", "")).startswith("Devcontainer runtime: ")]
    assert len(decision) == 1
    assert "{{ devcontainer_runtime_effective }}" in decision[0]["name"]
    assert "{{ devcontainer_runtime_source }}" in decision[0]["name"]


# ---------------------------------------------------------------------------
# Rendered rules
# ---------------------------------------------------------------------------


def _b64decode(value: str) -> str:
    import base64

    return base64.b64decode(value).decode()


def _resolve(
    *,
    request: str = "auto",
    marker: str | None = None,
    reference_cli: bool = False,
    nested: bool = False,
) -> tuple[str, str]:
    """Evaluate the resolver's set_fact expressions the way Ansible would."""
    import base64

    env = Environment(autoescape=False)
    env.filters["b64decode"] = _b64decode
    # Ansible's `bool` filter is not a jinja2 builtin; mirror its truthiness.
    env.filters["bool"] = lambda v: (
        str(v).strip().lower() in ("true", "1", "yes", "on") if isinstance(v, str) else bool(v)
    )
    facts: dict[str, Any] = {
        "devcontainer_runtime": request,
        "docker_nested_overlayfs": nested,
        "devcontainer_runtime_marker": (
            {"content": base64.b64encode(marker.encode()).decode()} if marker is not None else {}
        ),
        "devcontainer_runtime_reference_cli": {"stat": {"exists": reference_cli}},
    }
    decode = _named(RESOLVER_TASKS, "Decode the recorded devcontainer runtime marker")
    facts["devcontainer_runtime_recorded"] = env.from_string(
        decode["ansible.builtin.set_fact"]["devcontainer_runtime_recorded"]
    ).render(**facts).strip()
    resolve = _named(RESOLVER_TASKS, "Resolve the effective devcontainer runtime")["ansible.builtin.set_fact"]
    effective = env.from_string(resolve["devcontainer_runtime_effective"]).render(**facts).strip()
    source = env.from_string(resolve["devcontainer_runtime_source"]).render(**facts).strip()
    return effective, source


def test_explicit_requests_win() -> None:
    assert _resolve(request="deacon", marker="devcontainer", reference_cli=True, nested=True) == ("deacon", "explicit")
    assert _resolve(request="devcontainer") == ("devcontainer", "explicit")


def test_marker_keeps_a_host_on_its_runtime() -> None:
    assert _resolve(marker="deacon\n", nested=True) == ("deacon", "marker")
    assert _resolve(marker="devcontainer\n") == ("devcontainer", "marker")


def test_invalid_marker_is_ignored() -> None:
    assert _resolve(marker="podman\n", reference_cli=True) == ("devcontainer", "legacy")
    assert _resolve(marker="") == ("deacon", "default")


def test_legacy_host_with_reference_cli_stays() -> None:
    assert _resolve(reference_cli=True) == ("devcontainer", "legacy")


def test_nested_overlayfs_host_keeps_reference_cli() -> None:
    assert _resolve(nested=True) == ("devcontainer", "nested-overlayfs")


def test_fresh_host_gets_deacon() -> None:
    assert _resolve() == ("deacon", "default")


# ---------------------------------------------------------------------------
# user_setup derived vars key on the EFFECTIVE runtime
# ---------------------------------------------------------------------------


def _derived(effective: str | None, request: str = "auto") -> tuple[str, str]:
    env = Environment(autoescape=False)
    ctx: dict[str, Any] = {"devcontainer_runtime": request}
    if effective is not None:
        ctx["devcontainer_runtime_effective"] = effective
    cli = env.from_string(DEFAULTS["devcontainer_cli_bin"]).render(**ctx).strip()
    extra = env.from_string(DEFAULTS["devcontainer_up_extra_args"]).render(**ctx).strip()
    return cli, extra


def test_default_request_is_auto() -> None:
    assert DEFAULTS["devcontainer_runtime"] == "auto"


def test_derived_vars_follow_the_effective_runtime() -> None:
    assert _derived("deacon") == ("deacon", "--trust-workspace-persist")
    assert _derived("devcontainer") == ("devcontainer", "")
    # Explicit request without the resolver (role used directly) still works.
    assert _derived(None, request="deacon") == ("deacon", "--trust-workspace-persist")
    # Unresolved `auto` falls back to the reference CLI: the safe choice.
    assert _derived(None, request="auto") == ("devcontainer", "")
