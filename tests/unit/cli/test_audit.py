"""US4 T070: `remo audit` CLI — exit code 8 when audit log missing."""

from __future__ import annotations

from click.testing import CliRunner

from remo_cli.cli.audit import audit_command
from remo_cli.core import audit as audit_core
from remo_cli.models.host import KnownHost


def test_audit_exit_8_when_log_missing(tmp_config_dir, mocker):
    # Pre-register the instance in known_hosts so the CLI can resolve it.
    from remo_cli.core.known_hosts import save_known_host
    save_known_host(KnownHost(type="hetzner", name="web-1", host="1.2.3.4", user="remo"))

    mocker.patch(
        "remo_cli.cli.audit.audit_core.fetch",
        side_effect=audit_core.AuditError("audit log not found at /var/log/remo-broker/audit.log"),
    )

    runner = CliRunner()
    r = runner.invoke(audit_command, ["web-1"])
    assert r.exit_code == 8, r.output


def test_audit_invalid_since_exits_2(tmp_config_dir, mocker):
    from remo_cli.core.known_hosts import save_known_host
    save_known_host(KnownHost(type="hetzner", name="web-1", host="1.2.3.4", user="remo"))

    runner = CliRunner()
    r = runner.invoke(audit_command, ["web-1", "--since", "eternity"])
    assert r.exit_code == 2


def test_audit_unknown_instance(tmp_config_dir):
    runner = CliRunner()
    r = runner.invoke(audit_command, ["nope"])
    assert r.exit_code != 0
    assert "not found" in r.output.lower()


def test_audit_json_output(tmp_config_dir, mocker):
    from remo_cli.core.known_hosts import save_known_host
    save_known_host(KnownHost(type="hetzner", name="web-1", host="1.2.3.4", user="remo"))

    line = audit_core.AuditLine(
        ts="2026-05-25T10:00:00Z",
        project="foo",
        secret="github_token",
        decision="allow",
        reason="in-manifest",
        cache="miss",
        raw={"ts": "2026-05-25T10:00:00Z", "decision": "allow"},
    )
    mocker.patch("remo_cli.cli.audit.audit_core.fetch", return_value=[line])

    runner = CliRunner()
    r = runner.invoke(audit_command, ["web-1", "--json"])
    assert r.exit_code == 0, r.output
    assert "allow" in r.output
