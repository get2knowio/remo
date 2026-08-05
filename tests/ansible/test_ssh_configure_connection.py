"""Connection contract for `ansible/ssh_configure.yml` (`remo configure`).

Unlike the four provider configure playbooks, this one carries the SSH port and
identity of a host remo did not create, supplied by the CLI from the registry.
Every assertion below defends a way that plumbing can fail *silently* — the
playbook still runs, still reports success, and still leaves the operator with
a host that does not work:

* a `| default(22)` on the port reaches the wrong machine, or nothing;
* a `'~/.ssh/id_rsa'` fallback on the identity offers the wrong key;
* a missing `IdentitiesOnly=yes` lets a busy agent exhaust `MaxAuthTries`
  before the key the operator passed is ever tried;
* `become: false` missing from the sudo probe makes the probe run *through*
  sudo, so it can never detect that sudo is unavailable;
* `hosts: all` picks up `inventory/hosts.yml`'s `hetzner_server`, whose
  `ansible_host` is undefined;
* `remo_user` left at its `group_vars/all.yml` default installs `remo-host`
  into a *second* account, so `remo web` still reports `no_remo_host` after a
  "successful" configure.

These are YAML-shape assertions, not a live run: the rendered ssh argv can only
be confirmed against a real host (see the feature's manual verification steps).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
ANSIBLE_DIR = REPO_ROOT / "ansible"
PLAYBOOK = ANSIBLE_DIR / "ssh_configure.yml"

INVENTORY_GROUP = "remo_ssh_hosts"


@pytest.fixture(scope="module")
def plays() -> list[dict[str, Any]]:
    data = yaml.safe_load(PLAYBOOK.read_text())
    assert isinstance(data, list) and len(data) == 2, (
        "ssh_configure.yml is two plays: localhost builds the inventory, then "
        "the managed play configures the host"
    )
    return data


@pytest.fixture(scope="module")
def playbook_code(plays: list[dict[str, Any]]) -> str:
    """The playbook's *code*, with prose dropped by construction.

    Re-serializing the parsed structure removes every comment, so a whole-file
    search cannot be satisfied — or defeated — by a comment that merely
    discusses the thing being asserted. Both `id_rsa` and
    `ansible.builtin.reboot` appear in this playbook's comments, explaining why
    it uses neither.
    """
    return yaml.safe_dump(plays)


@pytest.fixture(scope="module")
def add_host_args(plays: list[dict[str, Any]]) -> dict[str, Any]:
    for task in plays[0].get("tasks", []):
        if "ansible.builtin.add_host" in task:
            args = task["ansible.builtin.add_host"]
            assert isinstance(args, dict)
            return args
    raise AssertionError("play 1 has no ansible.builtin.add_host task")


class TestConnectionMapping:
    def test_port_is_mapped_and_never_defaulted(self, add_host_args: dict[str, Any]) -> None:
        port = add_host_args["ansible_port"]
        assert "remo_ssh_port" in port
        # The CLI always passes a port (KnownHost.ssh_port supplies its own 22
        # default), so a default here could only ever mask a caller bug — by
        # silently connecting to port 22.
        assert "default" not in port, (
            "a default on remo_ssh_port is how 'silently used port 22' ships; "
            "the assert in play 1 must fail loudly instead"
        )

    def test_identity_omits_rather_than_guessing_a_key(
        self, add_host_args: dict[str, Any]
    ) -> None:
        identity = add_host_args["ansible_ssh_private_key_file"]
        assert "remo_ssh_identity" in identity
        assert "default(omit)" in identity.replace(" ", ""), (
            "'no identity stored' must mean 'let ssh/agent resolve the key', "
            "not 'try this specific path'"
        )

    def test_no_id_rsa_fallback_anywhere_in_the_playbook(
        self, playbook_code: str
    ) -> None:
        # aws_configure.yml defaults ssh_private_key_path to ~/.ssh/id_rsa and
        # incus/proxmox hardcode it in group_vars. Both can offer the wrong key.
        assert "id_rsa" not in playbook_code

    def test_identity_is_paired_with_identities_only(
        self, add_host_args: dict[str, Any]
    ) -> None:
        # ansible-core emits `-o IdentityFile=` for private_key_file but not
        # `-o IdentitiesOnly=yes`; without the pairing, ssh offers every agent
        # key first and dies with "Too many authentication failures" while the
        # key the operator passed is never tried.
        common_args = add_host_args["ansible_ssh_common_args"]
        assert "IdentitiesOnly=yes" in common_args
        assert "remo_ssh_identity" in common_args, (
            "IdentitiesOnly must be conditional on an identity actually being "
            "set, or it would suppress the agent keys a no-identity host needs"
        )

    def test_added_host_name_is_a_colon_free_literal(
        self, add_host_args: dict[str, Any]
    ) -> None:
        # add_host runs `name` through parse_address(), so an IP-with-port name
        # would set ansible_ssh_port behind our back.
        name = add_host_args["name"]
        assert ":" not in name and "{{" not in name

    def test_host_joins_the_named_group(self, add_host_args: dict[str, Any]) -> None:
        assert INVENTORY_GROUP in add_host_args["groups"]

    def test_no_group_vars_file_reintroduces_a_hardcoded_key(self) -> None:
        # This is where `ansible_ssh_private_key_file: ~/.ssh/id_rsa` sneaks
        # back in — it is exactly what group_vars/{incus,proxmox}_containers.yml
        # do today.
        assert not (ANSIBLE_DIR / "group_vars" / f"{INVENTORY_GROUP}.yml").exists()


class TestManagedPlayTargeting:
    def test_targets_the_named_group_not_all(self, plays: list[dict[str, Any]]) -> None:
        # `hosts: all` would load ansible.cfg's inventory/hosts.yml, which
        # defines a `hetzner_server` whose ansible_host is undefined. Hetzner's
        # play only escapes that because its caller passes `-i`.
        assert plays[1]["hosts"] == INVENTORY_GROUP

    def test_gathers_facts_explicitly_after_the_interpreter_probe(
        self, plays: list[dict[str, Any]]
    ) -> None:
        play = plays[1]
        assert play["gather_facts"] is False, (
            "the implicit gather runs a Python module, so on a host with no "
            "python3 it fails first with a message that never names python3"
        )
        pre_task_names = [t.get("name", "") for t in play["pre_tasks"]]
        setup_index = next(
            i for i, t in enumerate(play["pre_tasks"]) if "ansible.builtin.setup" in t
        )
        probe_index = next(
            i for i, n in enumerate(pre_task_names) if "Python 3" in n
        )
        assert probe_index < setup_index

    @pytest.mark.parametrize("probe", ["python3", "sudo -n true"])
    def test_probes_bypass_become(self, plays: list[dict[str, Any]], probe: str) -> None:
        # ansible.cfg sets `become = True` globally. Without an explicit
        # `become: false`, `raw: sudo -n true` runs THROUGH sudo — so it can
        # only ever succeed, and the sudo check tests nothing.
        for task in plays[1]["pre_tasks"]:
            raw = task.get("ansible.builtin.raw", "")
            if probe in raw:
                assert task.get("become") is False, (
                    f"the {probe!r} probe must set become: false explicitly"
                )
                return
        raise AssertionError(f"no raw probe matching {probe!r}")

    def test_configures_the_registered_account(self, plays: list[dict[str, Any]]) -> None:
        # group_vars/all.yml pins remo_user to "remo". Left at that default,
        # user_setup would install remo-host into /home/remo while `remo web`
        # discovery logs in as the registered user and still reports
        # no_remo_host — a configure that reports success and changes nothing
        # the console can see.
        assert plays[1]["vars"]["remo_user"] == "{{ remo_ssh_user }}"

    def test_does_not_reboot(
        self, plays: list[dict[str, Any]], playbook_code: str
    ) -> None:
        # Hetzner/AWS reboot because remo owns those VMs outright. This host is
        # the operator's own machine and may be running their other work.
        assert "ansible.builtin.reboot" not in playbook_code
        names = [t.get("name", "") for t in plays[1].get("post_tasks", [])]
        assert any("Reboot pending" in n for n in names), (
            "a pending reboot must still be reported — and in the task NAME, "
            "since ansible_runner._filter_line suppresses debug bodies"
        )

    def test_refuses_root_before_touching_the_host(
        self, plays: list[dict[str, Any]]
    ) -> None:
        # user_setup pins the workspace account to UID 1000; doing that to root
        # breaks the host.
        asserts = [
            t["ansible.builtin.assert"]["that"]
            for t in plays[0]["tasks"]
            if "ansible.builtin.assert" in t
        ]
        flat = [str(clause) for group in asserts for clause in group]
        assert any("remo_ssh_user != 'root'" in c for c in flat)
