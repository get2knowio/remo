"""The Proxmox node login: recorded honestly, and never clobbered (#106, #107).

Two defects, one field. Proxmox stores the SSH login for the *hypervisor node*
in the `region` slot (`proxmox.host_user` in registry.json):

* **#106** — create and the probe both wrote `region=host_user or "root"`.
  Create never defaults the login to root: with no `--host-user` it runs a bare
  `ssh <host>` and lets ssh_config decide. So a container created as *you* was
  recorded as *root*, and every later verb read that back and connected as root
  — which fails outright under an SSH policy that forbids root logins.
* **#107** — the probe never set `DiscoveredHost.observed`, so it got the legacy
  "every non-empty field was observed" semantics that `observed` was introduced
  (in #87) to replace. Combined with the never-empty `"root"` above, a
  discovered value *always* won, silently reverting a hand-edited `host_user` on
  the next `sync`.

Also covers the audit #107 asked for: the Incus and Hetzner probes had the same
undeclared shape, and the registry read that keeps pre-rename files working.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from remo_cli.core.reconcile import SyncScope, merge_entry
from remo_cli.core.registry import read_registry
from remo_cli.models.host import KnownHost
from remo_cli.providers import hetzner as providers_hetzner
from remo_cli.providers import incus as providers_incus
from remo_cli.providers import proxmox as providers_proxmox


def _completed(rc: int, stdout: str = "", stderr: str = "") -> MagicMock:
    cp = MagicMock()
    cp.returncode = rc
    cp.stdout = stdout
    cp.stderr = stderr
    return cp


_PCT_LIST = (
    "VMID       Status     Lock         Name\n"
    "100        running                 dev1\n"
)
_TAG_DUMP = "@@@/etc/pve/lxc/100.conf\ntags: remo\n"


@pytest.fixture
def scope() -> SyncScope:
    return SyncScope(type="proxmox", host="lab1", region="")


@pytest.fixture
def wire_proxmox_ssh(mocker):
    def run(argv, *args, **kwargs):
        command = argv[-1]
        if "pct list" in command:
            return _completed(0, _PCT_LIST)
        return _completed(0, _TAG_DUMP)

    return mocker.patch("remo_cli.providers.proxmox.subprocess.run", side_effect=run)


class TestCreateRecordsTheLoginItUsed:
    """#106: the registry must agree with how create actually reached the node."""

    @pytest.fixture
    def _created(self, mocker):
        mocker.patch("remo_cli.providers.proxmox.validate_name")
        mocker.patch("remo_cli.providers.proxmox.guard_not_added_ssh_host")
        mocker.patch("remo_cli.providers.proxmox.remove_known_host")
        mocker.patch("remo_cli.providers.proxmox.run_playbook", return_value=0)
        mocker.patch("remo_cli.providers.proxmox._resolve_vmid", return_value="100")
        mocker.patch("remo_cli.providers.proxmox._apply_managed_marker", return_value=(True, ""))
        return mocker.patch("remo_cli.providers.proxmox.save_known_host")

    def test_no_host_user_records_no_host_user(self, _created):
        providers_proxmox.create(name="dev1", host="lab1")

        entry = _created.call_args.args[0]
        assert entry.region == "", (
            "create runs a bare `ssh lab1` when --host-user is omitted; recording "
            "'root' makes every later verb connect as someone create never used"
        )

    def test_explicit_host_user_is_recorded_verbatim(self, _created):
        providers_proxmox.create(name="dev1", host="lab1", host_user="paul")

        assert _created.call_args.args[0].region == "paul"


class TestProbeDeclaresWhatItObserved:
    """#107: `observed` is the whole point of #87 — the probe has to fill it."""

    def test_proxmox_omits_region_when_no_user_was_given(
        self, scope, wire_proxmox_ssh
    ):
        result = providers_proxmox._probe(scope, host_user="", use_ip=False, include_all=False)

        observed = result.hosts[0].observed
        assert observed is not None, "observed=None falls back to the legacy semantics"
        assert "region" not in observed

    def test_proxmox_includes_region_when_user_was_given(self, scope, wire_proxmox_ssh):
        result = providers_proxmox._probe(
            scope, host_user="paul", use_ip=False, include_all=False
        )

        assert "region" in (result.hosts[0].observed or frozenset())

    def test_incus_omits_instance_id_when_no_user_was_given(self, mocker):
        """Incus keeps the host login in `instance_id` — same field, same rule."""
        mocker.patch(
            "remo_cli.providers.incus._list_containers_with_marker",
            return_value=[("dev1", True)],
        )
        result = providers_incus._probe(
            SyncScope(type="incus", host="lab1", region=""),
            host_user="",
            use_ip=False,
            include_all=False,
        )

        observed = result.hosts[0].observed
        assert observed is not None
        assert "instance_id" not in observed
        assert "host" in observed

    def test_incus_includes_instance_id_when_user_was_given(self, mocker):
        mocker.patch(
            "remo_cli.providers.incus._list_containers_with_marker",
            return_value=[("dev1", True)],
        )
        result = providers_incus._probe(
            SyncScope(type="incus", host="lab1", region=""),
            host_user="paul",
            use_ip=False,
            include_all=False,
        )

        assert "instance_id" in (result.hosts[0].observed or frozenset())

    def test_hetzner_observes_only_the_address(self, mocker):
        mocker.patch(
            "remo_cli.providers.hetzner._hetzner_api_paged",
            return_value=(
                [
                    {
                        "name": "dev1",
                        "public_net": {"ipv4": {"ip": "1.2.3.4"}},
                        "labels": {"remo": "true"},
                        "status": "running",
                    }
                ],
                True,
            ),
        )
        result = providers_hetzner._probe(
            SyncScope(type="hetzner", host="", region=""), include_all=False
        )

        assert result.hosts[0].observed == frozenset({"host"})


class TestHandEditedHostUserSurvivesSync:
    """The issue's reproduce steps, as a test: edit `host_user`, sync, re-read."""

    def test_merge_keeps_the_hand_edited_login(self, scope, wire_proxmox_ssh):
        existing = KnownHost(
            type="proxmox",
            name="lab1/dev1",
            host="dev1",
            user="remo",
            instance_id="100",
            access_mode="direct",
            region="paul",  # hand-edited in registry.json
        )
        discovered = providers_proxmox._probe(
            scope, host_user="", use_ip=False, include_all=False
        ).hosts[0]

        merged = merge_entry(existing, discovered.entry, discovered.observed)

        assert merged.region == "paul"

    def test_an_explicit_user_still_wins(self, scope, wire_proxmox_ssh):
        """Passing --host-user is an instruction, not a default — it must apply."""
        existing = KnownHost(
            type="proxmox", name="lab1/dev1", host="dev1", user="remo",
            instance_id="100", access_mode="direct", region="paul",
        )
        discovered = providers_proxmox._probe(
            scope, host_user="root", use_ip=False, include_all=False
        ).hosts[0]

        merged = merge_entry(existing, discovered.entry, discovered.observed)

        assert merged.region == "root"

    def test_legacy_observed_none_would_have_clobbered_it(self):
        """Pins why `observed` matters: without it, the filler value wins."""
        existing = KnownHost(
            type="proxmox", name="lab1/dev1", host="dev1", user="remo",
            instance_id="100", region="paul",
        )
        filler = KnownHost(
            type="proxmox", name="lab1/dev1", host="dev1", user="remo",
            instance_id="100", region="root",
        )

        assert merge_entry(existing, filler, None).region == "root"
        assert merge_entry(existing, filler, frozenset({"host"})).region == "paul"


class TestRegistryKeyRename:
    """`node_user` -> `host_user`, matching Incus and the Ansible var."""

    def _write(self, tmp_config_dir, nested: dict) -> None:
        (tmp_config_dir / "registry.json").write_text(
            json.dumps(
                {
                    "version": 2,
                    "hosts": [
                        {
                            "type": "proxmox",
                            "name": "lab1/dev1",
                            "host": "dev1",
                            "user": "remo",
                            "access": "direct",
                            "proxmox": nested,
                        }
                    ],
                }
            )
        )

    def test_legacy_node_user_is_still_read(self, tmp_config_dir):
        """A registry written before the rename must keep working untouched."""
        self._write(tmp_config_dir, {"vmid": "100", "node_user": "paul"})

        assert read_registry(readonly=True).hosts[0].region == "paul"

    def test_host_user_is_read(self, tmp_config_dir):
        self._write(tmp_config_dir, {"vmid": "100", "host_user": "paul"})

        assert read_registry(readonly=True).hosts[0].region == "paul"

    def test_host_user_wins_when_both_are_present(self, tmp_config_dir):
        self._write(
            tmp_config_dir, {"vmid": "100", "host_user": "paul", "node_user": "root"}
        )

        assert read_registry(readonly=True).hosts[0].region == "paul"

    def test_entries_are_written_under_the_new_key(self, tmp_config_dir):
        from remo_cli.core.known_hosts import save_known_host

        save_known_host(
            KnownHost(
                type="proxmox", name="lab1/dev1", host="dev1", user="remo",
                instance_id="100", access_mode="direct", region="paul",
            )
        )

        nested = json.loads((tmp_config_dir / "registry.json").read_text())["hosts"][0][
            "proxmox"
        ]
        assert nested["host_user"] == "paul"
        assert "node_user" not in nested


class TestDiscoveredHostObservedIsDeclaredEverywhere:
    def test_no_builtin_probe_relies_on_legacy_semantics(self):
        """A probe that forgets `observed` opts back into the #87 bug silently,
        which is exactly how #107 happened. Guard it structurally."""
        import inspect

        from remo_cli.providers import aws as providers_aws

        for module in (providers_proxmox, providers_incus, providers_hetzner, providers_aws):
            source = inspect.getsource(module._probe)
            assert "DiscoveredHost(" in source
            assert "observed=" in source, (
                f"{module.__name__}._probe builds DiscoveredHost without observed=, "
                "falling back to the legacy semantics #87 replaced"
            )
