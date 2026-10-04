"""`remo resume` decision table and host helpers (spec 028, data-model.md)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from remo_cli.core.remo_host_client import (
    MalformedResponseError,
    ProjectEntry,
    RemoHostCommandError,
    SshTransportError,
    TabLookup,
    TabLookupUnsupported,
    ZellijState,
)
from remo_cli.core.resume import (
    ResumeReason,
    decide_resume,
    is_project_live,
    needs_project_liveness,
    resume_message,
    run_lookup,
)
from remo_cli.core.tab_records import TabRecord
from remo_cli.models.host import KnownHost

HINT = "remo configure H"
NOW = datetime(2026, 10, 4, tzinfo=UTC)


def rec(host: str = "H", project: str | None = None) -> TabRecord:
    return TabRecord(host=host, project=project, recorded_at=NOW)


def hit(state: ZellijState, project: str = "A") -> TabLookup:
    return TabLookup(project, 1700000000, state)


MISS = TabLookup(None, None, None)


def decide(**overrides):
    args = dict(
        identity_present=True,
        record=rec(),
        requested_name=None,
        host_registered=True,
        lookup=None,
        lookup_status="ok",
        record_project_live=None,
    )
    args.update(overrides)
    return decide_resume(**args)


class TestSuccessRows:
    def test_row5_lookup_entry_active_attaches_entry_project(self):
        d = decide(lookup=hit(ZellijState.ACTIVE, "A"))
        assert (d.action, d.host_name, d.project, d.reason) == ("attach", "H", "A", None)
        assert resume_message(d) == "Resuming A on H"

    def test_host_entry_overrides_workstation_project(self):
        d = decide(
            record=rec(project="OLD"),
            lookup=hit(ZellijState.ACTIVE, "NEW"),
            record_project_live=True,
        )
        assert (d.action, d.project) == ("attach", "NEW")

    @pytest.mark.parametrize("status", ["unsupported", "failed", "ok"])
    def test_row7_record_project_active_attaches(self, status):
        lookup = MISS if status == "ok" else None
        d = decide(
            record=rec(project="A"), lookup=lookup, lookup_status=status, record_project_live=True
        )
        assert (d.action, d.host_name, d.project, d.reason) == ("attach", "H", "A", None)
        assert resume_message(d) == "Resuming A on H"

    def test_name_equal_to_record_host_resumes(self):
        d = decide(requested_name="H", lookup=hit(ZellijState.ACTIVE))
        assert d.action == "attach"


class TestFallbackRows:
    def test_row1_no_identity(self):
        d = decide(identity_present=False, record=None, requested_name="X")
        assert (d.action, d.reason, d.requested_name) == ("shell", ResumeReason.NO_IDENTITY, "X")
        assert resume_message(d) == "This terminal exposes no tab identity — opening remo shell"

    def test_row1_wins_over_everything(self):
        d = decide(identity_present=False, requested_name="Z", host_registered=False)
        assert d.reason is ResumeReason.NO_IDENTITY

    def test_row2_no_record(self):
        d = decide(record=None)
        assert (d.action, d.reason) == ("shell", ResumeReason.NO_RECORD)
        assert resume_message(d) == "Nothing recorded for this tab — opening remo shell"

    def test_row3_name_mismatch(self):
        d = decide(requested_name="OTHER")
        assert (d.action, d.reason, d.requested_name) == (
            "shell", ResumeReason.NAME_MISMATCH, "OTHER",
        )
        assert resume_message(d) == (
            "This tab last used H, not OTHER — opening remo shell OTHER"
        )

    def test_row4_host_gone(self):
        d = decide(host_registered=False)
        assert (d.action, d.reason) == ("shell", ResumeReason.HOST_GONE)
        assert resume_message(d) == "H is no longer registered — opening remo shell"

    def test_row3_precedes_row4(self):
        d = decide(requested_name="OTHER", host_registered=False)
        assert d.reason is ResumeReason.NAME_MISMATCH

    def test_row6_lookup_entry_not_active(self):
        for state in (ZellijState.EXITED, ZellijState.ABSENT):
            d = decide(lookup=hit(state, "A"))
            assert (d.action, d.host_name, d.project, d.reason) == (
                "menu", "H", "A", ResumeReason.SESSION_NOT_LIVE,
            )
            assert resume_message(d) == "A is no longer running on H — opening its project menu"

    @pytest.mark.parametrize("live", [False, None])
    def test_row8_record_project_not_active(self, live):
        d = decide(
            record=rec(project="A"), lookup=None, lookup_status="unsupported",
            record_project_live=live,
        )
        assert (d.action, d.project, d.reason) == ("menu", "A", ResumeReason.SESSION_NOT_LIVE)

    def test_row9_unsupported_no_record_project(self):
        d = decide(lookup=None, lookup_status="unsupported")
        assert (d.action, d.reason) == ("menu", ResumeReason.HOST_NOT_UPGRADED)
        msg = resume_message(d, upgrade_hint=HINT)
        assert msg == (
            "H can't resume a tab's project yet — run 'remo configure H' "
            "— opening its project menu"
        )

    @pytest.mark.parametrize("status", ["failed", "skipped"])
    def test_row10_failed_no_record_project(self, status):
        d = decide(lookup=None, lookup_status=status)
        assert (d.action, d.reason) == ("menu", ResumeReason.LOOKUP_FAILED)
        assert resume_message(d) == (
            "Couldn't ask H which project this tab used — opening its project menu"
        )

    @pytest.mark.parametrize("project", ["A", None])
    @pytest.mark.parametrize("live", [True, False, None])
    def test_row6a_unreachable_is_menu_and_never_attaches(self, project, live):
        """#247: an unreachable host's remembered project is never attached
        unverified, and the line says the host didn't answer, not that the
        project stopped."""
        d = decide(
            record=rec(project=project), lookup=None, lookup_status="unreachable",
            record_project_live=live,
        )
        assert (d.action, d.host_name, d.reason) == ("menu", "H", ResumeReason.LOOKUP_FAILED)
        assert resume_message(d) == (
            "Couldn't ask H which project this tab used — opening its project menu"
        )

    def test_row11_supported_no_entry_no_record_project(self):
        d = decide(lookup=MISS, lookup_status="ok")
        assert (d.action, d.reason) == ("menu", ResumeReason.NO_RECORD)
        assert resume_message(d) == (
            "Nothing recorded for this tab on H — opening its project menu"
        )

    def test_never_attaches_a_project_that_is_not_active(self):
        for state in (None, ZellijState.EXITED, ZellijState.ABSENT):
            for live in (None, False):
                for status in ("ok", "unsupported", "failed", "unreachable", "skipped"):
                    lookup = None if state is None else hit(state)
                    d = decide(
                        record=rec(project="R"), lookup=lookup, lookup_status=status,
                        record_project_live=live,
                    )
                    assert d.action != "attach"


class TestNeedsProjectLiveness:
    def test_only_when_record_has_project_and_host_did_not_name_one(self):
        assert not needs_project_liveness(lookup=hit(ZellijState.ACTIVE), lookup_status="ok", record=rec("H", "A"))
        assert not needs_project_liveness(lookup=hit(ZellijState.EXITED), lookup_status="ok", record=rec("H", "A"))
        assert not needs_project_liveness(lookup=None, lookup_status="unsupported", record=rec("H", None))
        assert not needs_project_liveness(lookup=None, lookup_status="unsupported", record=None)
        assert needs_project_liveness(lookup=None, lookup_status="unsupported", record=rec("H", "A"))
        assert needs_project_liveness(lookup=MISS, lookup_status="ok", record=rec("H", "A"))
        assert needs_project_liveness(lookup=None, lookup_status="failed", record=rec("H", "A"))

    def test_transport_failure_skips_liveness(self):
        """#247: a second call over a transport that just failed can't succeed."""
        assert not needs_project_liveness(
            lookup=None, lookup_status="unreachable", record=rec("H", "A")
        )


@pytest.fixture
def host():
    return KnownHost(type="hetzner", name="H", host="1.2.3.4", user="remo")


class TestRunLookup:
    KEY = "0" * 32

    def test_budget_and_batchmode(self, mocker, host):
        base = ["ssh", "-o", "ControlMaster=auto", "remo@1.2.3.4"]
        build = mocker.patch("remo_cli.core.resume.build_ssh_base_cmd", return_value=base)
        found = hit(ZellijState.ACTIVE)
        lookup = mocker.patch("remo_cli.core.resume.lookup_tab", return_value=found)

        assert run_lookup(host, self.KEY) == (found, "ok")

        build.assert_called_once_with(host, multiplex=True)
        prefix = lookup.call_args.args[0]
        assert prefix[0] == "ssh"
        assert prefix[1:3] == ["-o", "BatchMode=yes"]
        assert prefix[3:] == base[1:]
        assert lookup.call_args.args[1] == self.KEY
        assert lookup.call_args.kwargs["timeout"] == 5.0

    def test_unsupported(self, mocker, host):
        mocker.patch("remo_cli.core.resume.build_ssh_base_cmd", return_value=["ssh", "t"])
        mocker.patch("remo_cli.core.resume.lookup_tab", side_effect=TabLookupUnsupported("old"))
        assert run_lookup(host, self.KEY) == (None, "unsupported")

    @pytest.mark.parametrize(
        "exc", [SshTransportError("timeout"), SshTransportError("no route", returncode=255)]
    )
    def test_transport_error_is_unreachable(self, mocker, host, exc):
        mocker.patch("remo_cli.core.resume.build_ssh_base_cmd", return_value=["ssh", "t"])
        mocker.patch("remo_cli.core.resume.lookup_tab", side_effect=exc)
        assert run_lookup(host, self.KEY) == (None, "unreachable")

    @pytest.mark.parametrize(
        "exc", [MalformedResponseError("x"), RemoHostCommandError(3, "boom", verb="sessions lookup")]
    )
    def test_any_other_client_error_is_failed(self, mocker, host, exc):
        mocker.patch("remo_cli.core.resume.build_ssh_base_cmd", return_value=["ssh", "t"])
        mocker.patch("remo_cli.core.resume.lookup_tab", side_effect=exc)
        assert run_lookup(host, self.KEY) == (None, "failed")


class TestIsProjectLive:
    @staticmethod
    def entry(name: str, state: ZellijState) -> ProjectEntry:
        from remo_cli.core.remo_host_client import DevcontainerRunning

        return ProjectEntry(name, False, state, DevcontainerRunning.UNKNOWN)

    def test_active_is_live(self, mocker, host):
        mocker.patch("remo_cli.core.resume.build_ssh_base_cmd", return_value=["ssh", "t"])
        lister = mocker.patch(
            "remo_cli.core.resume.list_sessions",
            return_value=[self.entry("A", ZellijState.ACTIVE)],
        )
        assert is_project_live(host, "A") is True
        assert lister.call_args.kwargs["timeout"] == 5.0
        assert lister.call_args.args[0][1:3] == ["-o", "BatchMode=yes"]

    @pytest.mark.parametrize("state", [ZellijState.EXITED, ZellijState.ABSENT])
    def test_not_active_is_not_live(self, mocker, host, state):
        mocker.patch("remo_cli.core.resume.build_ssh_base_cmd", return_value=["ssh", "t"])
        mocker.patch("remo_cli.core.resume.list_sessions", return_value=[self.entry("A", state)])
        assert is_project_live(host, "A") is False

    def test_other_project_active_is_not_live(self, mocker, host):
        mocker.patch("remo_cli.core.resume.build_ssh_base_cmd", return_value=["ssh", "t"])
        mocker.patch(
            "remo_cli.core.resume.list_sessions",
            return_value=[self.entry("B", ZellijState.ACTIVE)],
        )
        assert is_project_live(host, "A") is False

    def test_error_is_not_live(self, mocker, host):
        mocker.patch("remo_cli.core.resume.build_ssh_base_cmd", return_value=["ssh", "t"])
        mocker.patch("remo_cli.core.resume.list_sessions", side_effect=SshTransportError("x"))
        assert is_project_live(host, "A") is False
