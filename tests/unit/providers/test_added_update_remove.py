"""Tests for providers/added.py update-in-place + remove() (feature 014, US2).

Covers FR-007 (in-place update, no duplicate), FR-008 (local-only remove, no
network — SC-004), and FR-009 (remove refuses provider-managed hosts).
"""

from __future__ import annotations

from remo_cli.models.host import KnownHost
from remo_cli.providers import added


def _ssh(name: str = "box", host: str = "1.2.3.4", port: str = "22") -> KnownHost:
    return KnownHost(
        type="ssh",
        name=name,
        host=host,
        user="remo",
        instance_id=port,
        access_mode="direct",
    )


# ---------------------------------------------------------------------------
# add() — in-place update (FR-007 / SC-003)
# ---------------------------------------------------------------------------


class TestUpdateInPlace:
    def test_reregister_updates_with_yes(self, mocker) -> None:
        mocker.patch(
            "remo_cli.providers.added.get_known_hosts", return_value=[_ssh()]
        )
        save = mocker.patch("remo_cli.providers.added.save_known_host")
        mocker.patch("remo_cli.providers.added.print_success")
        mocker.patch("remo_cli.providers.added.print_info")

        rc = added.add(name="box", target="dev@9.9.9.9:2222", assume_yes=True)

        assert rc == 0
        # save_known_host replaces the (type, name) line -> no duplicate.
        save.assert_called_once()
        entry = save.call_args.args[0]
        assert entry.host == "9.9.9.9" and entry.instance_id == "2222"

    def test_update_confirmed_interactively(self, mocker) -> None:
        mocker.patch(
            "remo_cli.providers.added.get_known_hosts", return_value=[_ssh()]
        )
        save = mocker.patch("remo_cli.providers.added.save_known_host")
        mocker.patch("remo_cli.providers.added.confirm", return_value=True)
        mocker.patch("remo_cli.providers.added.print_success")
        mocker.patch("remo_cli.providers.added.print_info")

        rc = added.add(name="box", target="dev@9.9.9.9")
        assert rc == 0
        save.assert_called_once()

    def test_update_declined_no_write(self, mocker) -> None:
        mocker.patch(
            "remo_cli.providers.added.get_known_hosts", return_value=[_ssh()]
        )
        save = mocker.patch("remo_cli.providers.added.save_known_host")
        mocker.patch("remo_cli.providers.added.confirm", return_value=False)
        mocker.patch("remo_cli.providers.added.print_info")

        rc = added.add(name="box", target="dev@9.9.9.9")
        assert rc == 1
        save.assert_not_called()


# ---------------------------------------------------------------------------
# remove() (FR-008 / FR-009 / SC-004)
# ---------------------------------------------------------------------------


class TestRemove:
    def test_remove_ssh_host_makes_no_network_call(self, mocker) -> None:
        mocker.patch(
            "remo_cli.providers.added.get_known_hosts", return_value=[_ssh()]
        )
        rm = mocker.patch("remo_cli.providers.added.remove_known_host")
        run = mocker.patch("remo_cli.providers.added.subprocess.run")
        mocker.patch("remo_cli.providers.added.print_success")

        rc = added.remove(name="box", assume_yes=True)

        assert rc == 0
        rm.assert_called_once_with("ssh", "box")
        run.assert_not_called()  # SC-004: no SSH/network call

    def test_remove_declined_no_delete(self, mocker) -> None:
        mocker.patch(
            "remo_cli.providers.added.get_known_hosts", return_value=[_ssh()]
        )
        rm = mocker.patch("remo_cli.providers.added.remove_known_host")
        mocker.patch("remo_cli.providers.added.confirm", return_value=False)
        mocker.patch("remo_cli.providers.added.print_info")

        rc = added.remove(name="box", assume_yes=False)
        assert rc == 1
        rm.assert_not_called()

    def test_remove_refuses_provider_host(self, mocker) -> None:
        existing = KnownHost(type="aws", name="box", host="3.4.5.6", user="remo")
        mocker.patch(
            "remo_cli.providers.added.get_known_hosts", return_value=[existing]
        )
        rm = mocker.patch("remo_cli.providers.added.remove_known_host")
        err = mocker.patch("remo_cli.providers.added.print_error")

        rc = added.remove(name="box", assume_yes=True)

        assert rc == 1
        rm.assert_not_called()
        assert "aws" in " ".join(str(c.args[0]) for c in err.call_args_list)

    def test_remove_not_found(self, mocker) -> None:
        mocker.patch("remo_cli.providers.added.get_known_hosts", return_value=[])
        rm = mocker.patch("remo_cli.providers.added.remove_known_host")
        mocker.patch("remo_cli.providers.added.print_error")

        rc = added.remove(name="ghost", assume_yes=True)
        assert rc == 1
        rm.assert_not_called()
