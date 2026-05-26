# Contract: Bootstrap-Token Delivery per Provider

Date: 2026-05-25
Branch: 005-credential-broker

Each provider's bootstrap-token delivery mechanism is a separate transport-level contract. All four must satisfy:

- Token never appears in cloud-init user-data visible via the provider's console / metadata API (FR-005, US3).
- Token never appears in process argv / shell history / log lines on either side of the wire.
- Delivery is atomic from the broker's perspective (broker observes either pre-token or final-token, never half-written).
- Failure modes surface a single actionable error message (Principle IV).

## AWS — IAM instance profile

| Aspect | Detail |
|---|---|
| Trigger | `remo aws create` after `boto3 ec2.run_instances`. |
| Identity primitive | Instance profile attached to EC2 instance; role assumable via IMDSv2 `iam/security-credentials/<role-name>`. |
| On-disk token? | No. |
| Scoping | Role's inline policy allows `secretsmanager:GetSecretValue` on `arn:aws:secretsmanager:*:*:secret:remo/<dev>/*` only (research R3). |
| Idempotency | `iam:GetRole`/`iam:CreateRole`; instance-profile creation skipped if exists. |
| Role/profile naming | `remo-broker-instance-<dev_id>-<safe_instance_id>` (per-instance, **not** per-developer). Role and instance-profile share the name. `safe_instance_id` is the human-friendly resource name from `remo aws create` sanitized to `[A-Za-z0-9_-]` and truncated to 32 chars. Per-instance scoping is mandatory: revocation attaches a deny-all policy and deletes the role, which would break IMDS creds on sibling instances if any role were shared. |
| Revoke at destroy | After `ec2.terminate_instances`: update assume-role policy to deny-all, then delete the role+profile. ≤60 s STS propagation budget (SC-005). |

## Hetzner — SSH push

| Aspect | Detail |
|---|---|
| Trigger | `remo hetzner create` after `_wait_for_ssh()` returns successfully. |
| Identity primitive | Per-instance backend sub-token (1Password SCIM token, Vault AppRole secret-id, etc.). |
| Transport | `ssh root@<ip> 'install -D -m 0400 -o root -g root /dev/stdin /etc/remo-broker/bootstrap-token'` with token bytes on stdin. |
| On-disk token? | Yes — `/etc/remo-broker/bootstrap-token` (mode 0400, root). TPM2 sealing optional (OQ-6). |
| Argv leak prevention | Token only on stdin; no `--option <token>` form anywhere. |
| Revoke at destroy | Backend revocation call BEFORE `hcloud server delete`. If revocation fails, abort destroy. |

## Incus — Node bind-mount

| Aspect | Detail |
|---|---|
| Trigger | `remo incus create` after container `start`. |
| Identity primitive | Per-instance backend sub-token. |
| On-node storage | `/var/lib/remo-broker/instance-tokens/<dev>/<instance>` (mode 0400, root). |
| Mount mechanism | `lxc config device add <instance> remo-broker-token disk source=… path=/etc/remo-broker/bootstrap-token readonly=true`. |
| On-disk token in container? | Yes, but RO bind-mount from outside — container cannot write. |
| Revoke at destroy | Backend revocation call BEFORE `incus delete`. Node-side token file removed in the same step. |

## Proxmox — Node bind-mount (LXC single-file mp)

| Aspect | Detail |
|---|---|
| Trigger | `remo proxmox create` after container `start`. |
| Identity primitive | Per-instance backend sub-token. |
| On-node storage | `/var/lib/remo-broker/instance-tokens/<dev>/<vmid>` (mode 0400, root). |
| Mount mechanism | `pct set <vmid> -mp0 /var/lib/remo-broker/instance-tokens/<dev>/<vmid>,mp=/etc/remo-broker/bootstrap-token,ro=1`. |
| Revoke at destroy | Backend revocation BEFORE `pct destroy`. Node-side file removed in the same step. |

## Shared Ansible role: `bootstrap_token_*`

Three sibling roles named after the transport (`bootstrap_token_imds`, `bootstrap_token_file`, `bootstrap_token_mount`) are included by the corresponding `*_configure.yml` playbook. Each:

- Asserts the expected on-instance state (file exists with correct mode, OR IMDS reachable + role attached).
- Reads back the broker's `systemctl is-active` and `journalctl -u remo-broker --no-pager -n 10` for diagnostic capture.
- On failure, fails the playbook with a message identifying the missing primitive ("bootstrap token file absent at /etc/remo-broker/bootstrap-token — has `bootstrap_token_file` run?").
