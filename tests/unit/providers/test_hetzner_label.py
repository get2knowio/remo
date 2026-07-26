"""Tests for the Hetzner managed-label backfill (`_apply_managed_label`, T057/T058).

`create` now applies `remo: "true"` at creation time via Ansible (T028), but a
server created before that change -- or otherwise unlabelled -- needs a
retroactive path. Both `hetzner.hcloud.server` and a raw `PUT /servers/{id}`
treat the supplied label map as authoritative and replace it wholesale, so
`_apply_managed_label` must read-merge rather than overwrite (FR-034). All
HTTP is mocked via `_hetzner_api`; no live Hetzner project is required.
"""

from __future__ import annotations

from remo_cli.core.errors import OperationFailedError, PreconditionError
from remo_cli.providers import hetzner as providers_hetzner


def _server(server_id: int = 42, name: str = "dev1", labels: dict | None = None) -> dict:
    return {"id": server_id, "name": name, "labels": labels if labels is not None else {}}


class TestApplyManagedLabel:
    def test_already_labelled_is_a_noop_no_put(self, mocker):
        get_response = {"servers": [_server(labels={"remo": "true"})]}
        api = mocker.patch.object(
            providers_hetzner, "_hetzner_api", side_effect=[get_response]
        )

        ok, err = providers_hetzner._apply_managed_label("dev1")

        assert ok is True
        assert err == ""
        # Only the GET lookup happened -- no PUT was ever issued.
        assert api.call_count == 1
        assert api.call_args_list[0].args[0] == "GET"

    def test_merge_preserves_unrelated_labels(self, mocker):
        get_response = {"servers": [_server(labels={"env": "prod"})]}
        api = mocker.patch.object(
            providers_hetzner, "_hetzner_api", side_effect=[get_response, {}]
        )

        ok, err = providers_hetzner._apply_managed_label("dev1")

        assert ok is True
        assert err == ""
        assert api.call_count == 2
        method, path, body = api.call_args_list[1].args
        assert method == "PUT"
        assert path == "/servers/42"
        assert body == {"labels": {"env": "prod", "remo": "true"}}

    def test_no_prior_labels_still_writes_remo_true(self, mocker):
        get_response = {"servers": [_server(labels={})]}
        api = mocker.patch.object(
            providers_hetzner, "_hetzner_api", side_effect=[get_response, {}]
        )

        ok, err = providers_hetzner._apply_managed_label("dev1")

        assert ok is True
        assert api.call_args_list[1].args[2] == {"labels": {"remo": "true"}}

    def test_lookup_failure_returns_false_and_never_raises(self, mocker):
        mocker.patch.object(
            providers_hetzner,
            "_hetzner_api",
            side_effect=PreconditionError("No Hetzner server found named 'dev1'."),
        )

        ok, err = providers_hetzner._apply_managed_label("dev1")

        assert ok is False
        assert "dev1" in err

    def test_put_failure_returns_false_and_never_raises(self, mocker):
        get_response = {"servers": [_server(labels={})]}
        mocker.patch.object(
            providers_hetzner,
            "_hetzner_api",
            side_effect=[get_response, OperationFailedError("Hetzner API PUT failed: 503")],
        )

        ok, err = providers_hetzner._apply_managed_label("dev1")

        assert ok is False
        assert "503" in err


class TestUpdateWarnsWithoutFailing:
    def test_update_warns_but_still_proceeds_when_label_backfill_fails(self, mocker):
        mocker.patch.object(
            providers_hetzner, "_apply_managed_label", return_value=(False, "boom")
        )
        mocker.patch.object(
            providers_hetzner, "_lookup_hetzner_host", return_value="1.2.3.4"
        )
        warn = mocker.patch.object(providers_hetzner, "print_warning")
        run_playbook = mocker.patch.object(
            providers_hetzner, "run_playbook", return_value=0
        )

        result = providers_hetzner.update(name="dev1")

        assert result is None
        run_playbook.assert_called_once()
        assert warn.call_count == 1
        assert "dev1" in warn.call_args.args[0]
        assert "boom" in warn.call_args.args[0]
