"""T039: destroy-ordering regression tests for core/lifecycle.run_destroy.

Ordering is normative (contracts/lifecycle-templates.md): guard -> snapshot
pre-cleanup -> confirm -> teardown -> best-effort registry removal.
"""

from __future__ import annotations

import pytest

from remo_cli.core.errors import OperationFailedError, PreconditionError, UserAbortedError
from remo_cli.core.known_hosts import get_known_hosts, save_known_host
from remo_cli.core.lifecycle import run_destroy
from remo_cli.models.host import KnownHost


@pytest.fixture
def entry() -> KnownHost:
    return KnownHost(type="incus", name="box1", host="1.2.3.4", user="remo")


def _run(entry, *, calls, auto_confirm=True, teardown_raises=None, confirm_result=True, mocker):
    mocker.patch("remo_cli.core.lifecycle.confirm", return_value=confirm_result)

    def teardown():
        calls.append("teardown")
        if teardown_raises:
            raise teardown_raises

    def list_snapshots():
        calls.append("list_snapshots")
        return []

    def delete_snapshot(snap):
        calls.append("delete_snapshot")

    return run_destroy(
        entry,
        type_name=entry.type,
        display_name=entry.name,
        provider_label="Fake",
        teardown=teardown,
        list_snapshots=list_snapshots,
        delete_snapshot=delete_snapshot,
        auto_confirm=auto_confirm,
    )


def test_happy_path_call_order(tmp_config_dir, entry, mocker):
    save_known_host(entry)
    calls: list[str] = []

    _run(entry, calls=calls, auto_confirm=True, mocker=mocker)

    assert calls == ["list_snapshots", "teardown"]
    assert get_known_hosts(type_filter="incus") == []  # removed


def test_decline_confirmation_raises_user_aborted_error_exit_3(tmp_config_dir, entry, mocker):
    save_known_host(entry)
    calls: list[str] = []

    with pytest.raises(UserAbortedError) as exc:
        _run(entry, calls=calls, auto_confirm=False, confirm_result=False, mocker=mocker)

    assert exc.value.exit_code == 3
    assert "teardown" not in calls  # never reached
    # Registry entry survives a declined destroy.
    assert [h.name for h in get_known_hosts(type_filter="incus")] == [entry.name]


def test_registry_removal_runs_even_when_teardown_fails(tmp_config_dir, entry, mocker):
    save_known_host(entry)
    calls: list[str] = []

    with pytest.raises(OperationFailedError):
        _run(entry, calls=calls, auto_confirm=True, teardown_raises=OperationFailedError("boom"), mocker=mocker)

    assert calls == ["list_snapshots", "teardown"]
    # Best-effort removal still happened despite the teardown failure.
    assert get_known_hosts(type_filter="incus") == []


def test_registry_removal_failure_warns_but_does_not_mask_success(tmp_config_dir, entry, mocker, capsys):
    save_known_host(entry)
    calls: list[str] = []
    mocker.patch(
        "remo_cli.core.lifecycle.remove_known_host",
        side_effect=RuntimeError("disk full"),
    )

    # Should not raise even though registry removal itself failed.
    _run(entry, calls=calls, auto_confirm=True, mocker=mocker)

    assert calls == ["list_snapshots", "teardown"]
    assert "disk full" in capsys.readouterr().out


def test_ssh_guard_raises_precondition_error(tmp_config_dir, mocker):
    added_ssh = KnownHost(type="ssh", name="box1", host="1.2.3.4", user="remo")
    save_known_host(added_ssh)
    calls: list[str] = []

    entry = KnownHost(type="incus", name="box1", host="1.2.3.4", user="remo")
    with pytest.raises(PreconditionError, match="manually-registered SSH host"):
        _run(entry, calls=calls, auto_confirm=True, mocker=mocker)

    assert calls == []  # guard fires before anything else
