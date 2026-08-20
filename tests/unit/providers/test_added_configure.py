"""`remo configure` — the provider-neutral configure path for added SSH hosts.

A host registered with ``remo add`` could be shelled into but never
*configured*, so it had no ``remo-host`` and appeared in ``remo web``
permanently badged ``no_remo_host`` with zero session targets.
``providers/added.py::configure`` closes that by running the generic
``ssh_configure.yml`` play against it.

The assertions here concentrate on the two things that can go wrong quietly:

* **the extra-vars contract** — only ``remo_ssh_*`` names may cross into
  Ansible. An ``ansible_port`` emitted here would be an extra-var, which is the
  highest-precedence source, so it would apply to *every* host in the run and
  could not be overridden. The playbook owns that mapping;
* **the refusal paths** — every one of them must raise a typed error naming the
  command to run instead, since the alternative is configuring the wrong host
  with the wrong play and reporting success.

Ansible is never invoked: ``run_playbook`` is patched on the provider module's
own imported reference, matching `test_aws_upgrade_resize.py`.
"""

from __future__ import annotations

import pytest

from remo_cli.core.errors import (
    MissingDependencyError,
    OperationFailedError,
    PreconditionError,
    UserAbortedError,
)
from remo_cli.core.known_hosts import save_known_host
from remo_cli.models.host import KnownHost
from remo_cli.providers import added as providers_added


@pytest.fixture(autouse=True)
def _no_network(mocker, tmp_config_dir):
    """Belt-and-suspenders: nothing here may shell out for real."""
    mocker.patch("subprocess.run", side_effect=AssertionError("subprocess.run called"))


@pytest.fixture(autouse=True)
def _reachable(mocker):
    """Default the pre-flight to success; the failure case overrides it."""
    return mocker.patch(
        "remo_cli.providers.added.verify_reachable", return_value=(True, None)
    )


@pytest.fixture
def run_playbook(mocker):
    mocker.patch(
        "remo_cli.core.ansible_runner.build_configure_extra_vars", return_value=[]
    )
    return mocker.patch(
        "remo_cli.core.ansible_runner.run_playbook", return_value=0
    )


def _register(
    name: str = "mbp",
    *,
    user: str = "remo",
    host: str = "10.0.0.5",
    port: str = "22",
    identity: str = "",
    type_: str = "ssh",
) -> KnownHost:
    entry = KnownHost(
        type=type_,
        name=name,
        host=host,
        user=user,
        instance_id=port,
        access_mode="direct",
        region=identity,
    )
    save_known_host(entry)
    return entry


class TestExtraVarsContract:
    def test_passes_host_user_and_port_and_the_generic_playbook(self, run_playbook):
        _register(port="2222")

        providers_added.configure(name="mbp", assume_yes=True)

        playbook, extra_vars = run_playbook.call_args.args[:2]
        assert playbook == "ssh_configure.yml"
        assert extra_vars == [
            "-e",
            "remo_ssh_host=10.0.0.5",
            "-e",
            "remo_ssh_user=remo",
            "-e",
            "remo_ssh_port=2222",
        ]

    def test_passes_the_identity_only_when_one_is_stored(self, run_playbook):
        _register(identity="~/.ssh/mbp_ed25519")

        providers_added.configure(name="mbp", assume_yes=True)

        extra_vars = run_playbook.call_args.args[1]
        assert "remo_ssh_identity=~/.ssh/mbp_ed25519" in extra_vars

    def test_omits_the_identity_var_entirely_when_none_is_stored(self, run_playbook):
        # Not an empty value: the playbook's `| default(omit)` must see the var
        # as genuinely absent, or ssh is handed an empty IdentityFile.
        _register(identity="")

        providers_added.configure(name="mbp", assume_yes=True)

        extra_vars = run_playbook.call_args.args[1]
        assert not any("remo_ssh_identity" in v for v in extra_vars)

    def test_never_emits_an_ansible_prefixed_extra_var(self, run_playbook):
        _register(port="2222", identity="~/.ssh/k")

        providers_added.configure(name="mbp", assume_yes=True)

        extra_vars = run_playbook.call_args.args[1]
        assert not any(v.startswith("ansible_") for v in extra_vars), (
            "extra-vars are the highest-precedence source: an ansible_* name "
            "here would apply to every host in the run, unoverridable"
        )

    def test_defaults_the_port_from_the_registry_when_unset(self, run_playbook):
        _register(port="")

        providers_added.configure(name="mbp", assume_yes=True)

        assert "remo_ssh_port=22" in run_playbook.call_args.args[1]


class TestRefusals:
    def test_unregistered_name_points_at_remo_add(self, run_playbook):
        with pytest.raises(PreconditionError, match="remo add"):
            providers_added.configure(name="nosuch", assume_yes=True)
        run_playbook.assert_not_called()

    def test_provider_host_names_that_providers_upgrade_verb(self, run_playbook):
        _register(name="web1", type_="hetzner")

        with pytest.raises(PreconditionError, match="remo hetzner upgrade web1"):
            providers_added.configure(name="web1", assume_yes=True)
        run_playbook.assert_not_called()

    def test_root_is_refused_before_anything_runs(self, run_playbook):
        # user_setup pins the workspace account to UID 1000; doing that to root
        # would break the host.
        _register(user="root")

        with pytest.raises(PreconditionError, match="root"):
            providers_added.configure(name="mbp", assume_yes=True)
        run_playbook.assert_not_called()

    def test_declining_the_prompt_aborts_without_configuring(self, mocker, run_playbook):
        _register()
        mocker.patch("remo_cli.providers.added.confirm", return_value=False)

        with pytest.raises(UserAbortedError):
            providers_added.configure(name="mbp")
        run_playbook.assert_not_called()

    def test_prompt_defaults_to_no(self, mocker, run_playbook):
        # This play apt-upgrades the system and grants passwordless sudo on a
        # machine remo does not own; a stray Enter must not start it.
        _register()
        confirm = mocker.patch("remo_cli.providers.added.confirm", return_value=False)

        with pytest.raises(UserAbortedError):
            providers_added.configure(name="mbp")
        assert confirm.call_args.kwargs["default"] is False

    def test_unreachable_host_reports_ssh_stderr_and_configures_nothing(
        self, run_playbook, _reachable
    ):
        _register(port="2222")
        _reachable.return_value = (False, "Connection refused")

        with pytest.raises(PreconditionError, match="Connection refused"):
            providers_added.configure(name="mbp", assume_yes=True)
        run_playbook.assert_not_called()

    def test_jinja_in_a_stored_field_is_refused(self, run_playbook):
        # Extra-var values are templated on the CONTROL NODE, so a registry
        # entry carrying `{{ lookup('pipe', ...) }}` would execute locally.
        # Entries written before `remo add` rejected these are covered here.
        _register(identity="{{ lookup('pipe', 'id') }}")

        with pytest.raises(PreconditionError, match="Jinja"):
            providers_added.configure(name="mbp", assume_yes=True)
        run_playbook.assert_not_called()


class TestPlaybookOutcome:
    def test_nonzero_rc_raises_operation_failed(self, run_playbook):
        _register()
        run_playbook.return_value = 2

        with pytest.raises(OperationFailedError, match="rc=2"):
            providers_added.configure(name="mbp", assume_yes=True)

    def test_missing_ansible_playbook_names_the_install_command(self, run_playbook):
        _register()
        run_playbook.side_effect = FileNotFoundError("ansible-playbook")

        with pytest.raises(MissingDependencyError, match="ansible-core"):
            providers_added.configure(name="mbp", assume_yes=True)

    def test_tool_selection_flags_reach_the_shared_builder(self, mocker, run_playbook):
        _register()
        build = mocker.patch(
            "remo_cli.core.ansible_runner.build_configure_extra_vars",
            return_value=["-e", "configure_docker=false"],
        )

        providers_added.configure(
            name="mbp", tools_skip=("docker",), assume_yes=True
        )

        assert build.call_args.args == ((), ("docker",))
        assert "configure_docker=false" in run_playbook.call_args.args[1]


class TestIdentityEnvFallback:
    """$REMO_SSH_IDENTITY_FILE (023): when the entry stores no identity, the
    web service's job runner supplies the service key via the environment."""

    def test_env_identity_used_when_entry_has_none(self, run_playbook, monkeypatch):
        monkeypatch.setenv("REMO_SSH_IDENTITY_FILE", "/svc/web-identity/id_ed25519")
        _register(identity="")

        providers_added.configure(name="mbp", assume_yes=True)

        extra_vars = run_playbook.call_args.args[1]
        assert "remo_ssh_identity=/svc/web-identity/id_ed25519" in extra_vars

    def test_stored_identity_beats_env(self, run_playbook, monkeypatch):
        monkeypatch.setenv("REMO_SSH_IDENTITY_FILE", "/svc/web-identity/id_ed25519")
        _register(identity="~/.ssh/mbp_ed25519")

        providers_added.configure(name="mbp", assume_yes=True)

        extra_vars = run_playbook.call_args.args[1]
        assert "remo_ssh_identity=~/.ssh/mbp_ed25519" in extra_vars
        assert not any("/svc/web-identity" in v for v in extra_vars)

    def test_unset_env_emits_no_identity_var(self, run_playbook, monkeypatch):
        monkeypatch.delenv("REMO_SSH_IDENTITY_FILE", raising=False)
        _register(identity="")

        providers_added.configure(name="mbp", assume_yes=True)

        extra_vars = run_playbook.call_args.args[1]
        assert not any("remo_ssh_identity" in v for v in extra_vars)

    def test_unsafe_env_identity_is_rejected(self, run_playbook, monkeypatch):
        # Env value rides into an extra-var Ansible templates on the control
        # node, so it gets the same Jinja/shell-metacharacter screen as
        # registry fields.
        monkeypatch.setenv("REMO_SSH_IDENTITY_FILE", "/tmp/{{ evil }}")
        _register(identity="")

        with pytest.raises(PreconditionError, match="REMO_SSH_IDENTITY_FILE"):
            providers_added.configure(name="mbp", assume_yes=True)
        run_playbook.assert_not_called()
