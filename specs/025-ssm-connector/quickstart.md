# Quickstart: validating the SSM connector

**Feature**: `025-ssm-connector` | Contracts: [session-document](contracts/session-document.md) · [cli](contracts/cli.md) · [ansible-role](contracts/ansible-role.md)

## A. Gates (no AWS needed)

```bash
uv sync --all-extras
uv run ruff check src/remo_cli
uv run mypy src/remo_cli
uv run pytest --tb=short -q
```

Targeted suites:

```bash
uv run pytest tests/unit/core/test_connector_target.py -q      # SC-001: hostile-name corpus round-trips; raw names fail the pattern
uv run pytest tests/unit/core/test_attach_builder.py -q         # SC-002: web wrapper == core builder; launcher argv parity
uv run pytest tests/unit/providers/test_connector_attach.py -q  # SC-003: every refusal → one error line, rc 1, no exec
uv run pytest tests/unit/providers/test_connector_enroll.py -q  # SC-005: code never in argv; vars file 0600 and deleted
uv run pytest tests/ansible/test_ssm_connector_role.py -q       # role structure: no_log, defaults, guards
uv run pytest tests/unit/core/test_connector_document.py -q     # document ↔ constants agree; loads from package data
uv run pytest tests/unit/test_architecture.py tests/unit/test_docs_structure.py tests/unit/cli/test_main.py -q
```

Web extra absent (FR-010):

```bash
uv sync --extra dev   # no web extra
uv run python -c "import remo_cli.core.attach, remo_cli.core.connector, remo_cli.providers.connector; print('ok')"
uv run remo connector --help
```

## B. Launcher, locally (no AWS)

```bash
export REMO_CONNECTOR_STATE_DIR=$(mktemp -d)
# populate: registry.json (one ssh host), exposure.json, id_ed25519(.pub), known_hosts, ssh/
T=$(uv run python -c "from remo_cli.core.connector import encode_target; print(encode_target('lab', 'remo'))")
uv run remo connector attach -- "$T"            # as a normal user: execs ssh (fails at the network, which is fine)
uv run remo connector attach -- "$(echo -n 'raw name' | base64)"   # → remo-connector-error: bad-target …
sudo -E uv run remo connector attach -- "$T"     # → remo-connector-error: run-as-not-in-effect …
```

## C. Document

```bash
uv run remo connector document | python -m json.tool >/dev/null && echo valid
uv run remo connector document | grep -c '"target"'   # 1 parameter
```

## D. Enrollment (needs a test AWS account and a Debian/Ubuntu host in your registry)

```bash
aws ssm create-activation --region eu-central-1 --iam-role SSMServiceRole \
  --registration-limit 1 --tags Key=remo:role,Value=connector
# note ActivationId; keep ActivationCode for the prompt
remo connector enroll lab --activation-id <id> --region eu-central-1 --expose lab/remo   # prompts for the code
remo connector enroll lab --activation-id <id> --region eu-central-1 --expose lab/remo   # second run: no changes
remo connector status lab
```

Prove the secret never leaked: run the second enrollment with `ps -ef | grep -c '<code>'` in another shell (expect 0 on the workstation) and `grep -r '<code>' /tmp/remo-playbook.* ~/.ansible 2>/dev/null` (expect nothing).

## E. Manual gate (SC-008; record in the tracked issue and docs/ssm-connector.md)

```bash
aws ssm create-document --name remo-attach --document-type Session --content "$(remo connector document)"
# enable Run As = remo-connector (preference or SSMSessionRunAs tag on your principal)
T=$(python3 -c "import base64,json;print(base64.urlsafe_b64encode(json.dumps({'v':1,'host':'lab','project':'remo'},separators=(',',':')).encode()).decode().rstrip('='))")
aws ssm start-session --region eu-central-1 --target mi-… --document-name remo-attach --parameters target=$T
```

Check: same Zellij session as `remo shell -p remo`; resize; Unicode; a full-screen TUI; kill the client, session survives; a non-exposed project → `remo-connector-error: not-exposed …`; `whoami` inside the session shell is the target's Remo user and `ps -o user= -p $PPID` on the connector shows the run-as user. Repeat for a second, non-AWS target; then the LXC (restart the container, confirm the node stays registered) and the Hetzner self-target shapes.

## F. Unenroll

```bash
remo connector unenroll lab --yes
aws ssm deregister-managed-instance --region eu-central-1 --instance-id mi-…
```
