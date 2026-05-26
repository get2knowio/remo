"""AWS EC2 provider business logic for remo.

Manages the lifecycle of AWS EC2 instances: create, destroy, and update
(re-configure dev tools).  Also provides IAM instance profile selection for
SSM connectivity and auto-start for stopped instances.

All functions are pure business logic with no Click imports; CLI argument
handling lives in the ``cli`` layer.  ``boto3`` is always lazy-imported so
that the rest of the CLI works without it installed.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import time

from remo_cli.core.ansible_runner import run_playbook
from remo_cli.core.known_hosts import (
    clear_known_hosts_by_type,
    get_aws_region,
    get_known_hosts,
    remove_known_host,
    save_known_host,
)
from datetime import datetime, timezone

from remo_cli.core.output import confirm, print_error, print_info, print_success, print_warning
from remo_cli.core.snapshot import (
    handle_destroy_snapshot_cleanup,
    validate_name as validate_snapshot_name,
)
from remo_cli.core.ssh import detect_timezone, require_session_manager_plugin
from remo_cli.core.validation import build_tool_args, parse_volume_size, validate_name
from remo_cli.core.version import get_current_version
from remo_cli.models.host import KnownHost
from remo_cli.models.snapshot import Snapshot, SnapshotStatus


def auto_start_aws_if_stopped(host: KnownHost) -> KnownHost:
    """Start an AWS instance if it is stopped, then return the updated host.

    Only acts when ``host.type == "aws"`` and ``host.instance_id`` is set.
    Queries the EC2 instance state via boto3.  If the instance is stopped it
    is started, and the function waits for the instance to be running and the
    SSM agent to come online.  The known-hosts registry is updated with the
    new public IP and the refreshed :class:`KnownHost` is returned.

    Parameters
    ----------
    host:
        The host entry to check and potentially start.

    Returns
    -------
    KnownHost
        The original host if no action was needed, or the refreshed host with
        updated IP after starting.

    Raises
    ------
    SystemExit
        If the instance is in the ``"stopping"`` state, or if boto3 is not
        available.
    """
    if host.type != "aws" or not host.instance_id:
        return host

    # Lazy import so boto3 is only required when actually needed
    try:
        import boto3  # noqa: PLC0415
    except ImportError:
        # Mirror the bash behavior: silently return when boto3 is missing
        return host

    region = get_aws_region(host.name)
    profile = os.environ.get("AWS_PROFILE") or None
    session = boto3.Session(region_name=region, profile_name=profile)
    ec2 = session.client("ec2")

    # Query instance state
    response = ec2.describe_instances(InstanceIds=[host.instance_id])
    inst_state = ""
    for reservation in response.get("Reservations", []):
        for inst in reservation.get("Instances", []):
            inst_state = inst["State"]["Name"]
            break

    if inst_state == "stopped":
        print_warning(f"Instance {host.instance_id} is stopped. Starting it...")

        # Start instance
        ec2.start_instances(InstanceIds=[host.instance_id])
        print_info("Waiting for instance to start...")

        waiter = ec2.get_waiter("instance_running")
        waiter.wait(InstanceIds=[host.instance_id])

        # Wait for SSM agent to come online
        print_info("Waiting for SSM agent...")
        ssm = session.client("ssm")
        for _ in range(30):
            resp = ssm.describe_instance_information(
                Filters=[{"Key": "InstanceIds", "Values": [host.instance_id]}]
            )
            info_list = resp.get("InstanceInformationList", [])
            if info_list and info_list[0].get("PingStatus") == "Online":
                print_info("SSM agent online.")
                break
            time.sleep(2)
        else:
            print_warning(
                "SSM agent did not come online within 60s. It may need more time."
            )

        # Re-describe to get new public IP
        response = ec2.describe_instances(InstanceIds=[host.instance_id])
        new_ip = ""
        for reservation in response.get("Reservations", []):
            for inst in reservation.get("Instances", []):
                new_ip = inst.get("PublicIpAddress", "")
                break

        print_success(f"Instance {host.instance_id} started successfully.")

        # Update known_hosts with new IP
        updated_host = KnownHost(
            type="aws",
            name=host.name,
            host=new_ip or host.instance_id,
            user=host.user,
            instance_id=host.instance_id,
            access_mode=host.access_mode or "ssm",
            region=region,
        )
        save_known_host(updated_host)

        # Re-read from registry and return the updated entry
        for h in get_known_hosts(type_filter="aws"):
            if h.name == host.name:
                return h

        # Fallback: return the locally-constructed host
        return updated_host

    elif inst_state == "stopping":
        raise SystemExit(
            f"Error: Instance {host.instance_id} is currently stopping. "
            "Please wait and try again."
        )

    # running or any other state: return unchanged
    return host


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _require_boto3():  # noqa: ANN202
    """Lazy-import and return the ``boto3`` module, or exit with guidance."""
    try:
        import boto3  # noqa: PLC0415

        return boto3
    except ImportError:
        print_error(
            "boto3 is not installed.  Try reinstalling remo:\n"
            "  uv tool install remo-cli"
        )
        sys.exit(1)


def _boto3_session(region: str):  # noqa: ANN202
    """Return a ``boto3.Session`` for *region* using ambient credentials."""
    boto3 = _require_boto3()
    profile = os.environ.get("AWS_PROFILE") or None
    return boto3.Session(region_name=region, profile_name=profile)


def _get_running_instance(resource_name: str, region: str) -> dict | None:
    """Describe the running remo EC2 instance matching *resource_name*.

    Returns the first matching instance dict, or ``None``.
    """
    session = _boto3_session(region)
    ec2 = session.client("ec2")

    response = ec2.describe_instances(
        Filters=[
            {"Name": "tag:Name", "Values": [f"remo-{resource_name}"]},
            {"Name": "tag:remo", "Values": ["true"]},
            {"Name": "instance-state-name", "Values": ["running"]},
        ]
    )

    for reservation in response.get("Reservations", []):
        for instance in reservation.get("Instances", []):
            return instance
    return None


def _effective_region(region: str) -> str:
    """Return the region to use, falling back through environment variables."""
    return (
        region
        or os.environ.get("AWS_REGION")
        or os.environ.get("AWS_DEFAULT_REGION")
        or "us-west-2"
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def select_ssm_instance_profile(
    resource_name: str,
    region: str = "us-west-2",
) -> str:
    """Find or create an IAM instance profile with SSM access.

    Resolution order:

    1. If exactly one existing instance profile is found whose role has the
       ``AmazonSSMManagedInstanceCore`` policy attached, auto-select it.
    2. If none are found, create a new IAM role + instance profile.
    3. If multiple are found, offer a picker (requires ``fzf``).

    Returns the instance profile name.
    """
    session = _boto3_session(region)
    iam = session.client("iam")

    SSM_POLICY_ARN = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"

    # Discover roles with SSM policy
    roles: list[str] = []
    try:
        resp = iam.list_entities_for_policy(
            PolicyArn=SSM_POLICY_ARN, EntityFilter="Role"
        )
        roles = [r["RoleName"] for r in resp.get("PolicyRoles", [])]
    except Exception as exc:
        print_warning(f"Could not list SSM IAM roles: {exc}")

    # Find instance profiles for each role
    profiles: list[dict[str, str]] = []
    for role in roles:
        try:
            resp = iam.list_instance_profiles_for_role(RoleName=role)
            for ip in resp.get("InstanceProfiles", []):
                profiles.append(
                    {
                        "name": ip["InstanceProfileName"],
                        "role": role,
                        "arn": ip["Arn"],
                    }
                )
        except Exception:
            pass

    # Decision tree
    if len(profiles) == 1:
        selected = profiles[0]["name"]
        print_info(f"Auto-selected IAM instance profile: {selected}")
        return selected

    if len(profiles) == 0:
        print_info("No existing SSM instance profiles found. Creating one...")
        return _create_ssm_resources(iam, resource_name)

    # Multiple profiles -- use fzf picker
    if not shutil.which("fzf"):
        print_error("Multiple IAM profiles found but fzf is not installed.")
        print("Available profiles:")
        for p in profiles:
            print(f"  {p['name']} (role: {p['role']})")
        sys.exit(1)

    import subprocess

    options = [f"{p['name']} (role: {p['role']})" for p in profiles]
    options.append("Create new SSM role and profile")

    result = subprocess.run(
        ["fzf", "--prompt=Select IAM instance profile: ", "--height=10", "--reverse"],
        input="\n".join(options),
        capture_output=True,
        text=True,
    )

    choice = result.stdout.strip()
    if not choice:
        print("No selection made.")
        sys.exit(0)

    if choice == "Create new SSM role and profile":
        return _create_ssm_resources(iam, resource_name)

    # Extract profile name (first word before the parenthetical)
    selected = choice.split()[0]
    return selected


def _ensure_broker_instance_role(
    iam,  # noqa: ANN001
    dev_id: str,
    region: str,
) -> tuple[str, str]:
    """Idempotently create the per-developer broker role + instance profile.

    Returns ``(role_name, profile_name)``. The role grants:
      - sts:AssumeRole from ec2.amazonaws.com (trust policy)
      - AmazonSSMManagedInstanceCore (managed) — keeps SSM access working
      - secretsmanager:GetSecretValue on arn:aws:secretsmanager:*:*:secret:remo/<dev>/* (inline)

    Per research R3 + contracts/bootstrap-delivery.md.
    """
    role_name = f"remo-broker-instance-{dev_id}"
    profile_name = role_name  # Same name simplifies tear-down lookup.

    assume_role_policy = json.dumps(
        {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Principal": {"Service": "ec2.amazonaws.com"},
                    "Action": "sts:AssumeRole",
                }
            ],
        }
    )

    broker_policy = json.dumps(
        {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Action": ["secretsmanager:GetSecretValue", "secretsmanager:DescribeSecret"],
                    "Resource": f"arn:aws:secretsmanager:*:*:secret:remo/{dev_id}/*",
                }
            ],
        }
    )

    # Role
    try:
        iam.get_role(RoleName=role_name)
    except iam.exceptions.NoSuchEntityException:
        iam.create_role(
            RoleName=role_name,
            AssumeRolePolicyDocument=assume_role_policy,
            Description=f"remo broker access for developer {dev_id}",
            Tags=[
                {"Key": "remo", "Value": "true"},
                {"Key": "remo:dev_id", "Value": dev_id},
                {"Key": "remo:region", "Value": region},
            ],
        )

    # Managed policy: SSM (idempotent — attach_role_policy is safe to re-call)
    try:
        iam.attach_role_policy(
            RoleName=role_name,
            PolicyArn="arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore",
        )
    except Exception:
        # Already attached → ignore.
        pass

    # Inline policy: scoped secretsmanager access
    iam.put_role_policy(
        RoleName=role_name,
        PolicyName="remo-broker-secretsmanager-scoped",
        PolicyDocument=broker_policy,
    )

    # Instance profile
    try:
        iam.get_instance_profile(InstanceProfileName=profile_name)
    except iam.exceptions.NoSuchEntityException:
        iam.create_instance_profile(
            InstanceProfileName=profile_name,
            Tags=[
                {"Key": "remo", "Value": "true"},
                {"Key": "remo:dev_id", "Value": dev_id},
            ],
        )
        # IAM eventual consistency.
        time.sleep(10)

    # Attach role to profile (idempotent)
    try:
        resp = iam.get_instance_profile(InstanceProfileName=profile_name)
        attached = [r.get("RoleName") for r in resp.get("InstanceProfile", {}).get("Roles", [])]
        if role_name not in attached:
            iam.add_role_to_instance_profile(
                InstanceProfileName=profile_name, RoleName=role_name
            )
    except Exception as exc:
        print_warning(f"Could not verify role-to-profile attachment: {exc}")

    return role_name, profile_name


def _attach_broker_deny_all_policy(iam, role_name: str) -> None:  # noqa: ANN001
    """Revocation primitive: replace inline policy with deny-all on every action.

    Per research R9: revocation = STS deny-all update + (post-terminate) role/profile delete.
    """
    deny_all = json.dumps(
        {
            "Version": "2012-10-17",
            "Statement": [{"Effect": "Deny", "Action": "*", "Resource": "*"}],
        }
    )
    iam.put_role_policy(
        RoleName=role_name,
        PolicyName="remo-broker-secretsmanager-scoped",
        PolicyDocument=deny_all,
    )


def _delete_broker_instance_role(iam, role_name: str, profile_name: str) -> None:  # noqa: ANN001
    """Tear down the per-developer broker role + profile after `terminate_instances`."""
    try:
        iam.remove_role_from_instance_profile(
            InstanceProfileName=profile_name, RoleName=role_name
        )
    except Exception:
        pass
    try:
        iam.delete_instance_profile(InstanceProfileName=profile_name)
    except Exception:
        pass
    for policy_name in ("remo-broker-secretsmanager-scoped",):
        try:
            iam.delete_role_policy(RoleName=role_name, PolicyName=policy_name)
        except Exception:
            pass
    try:
        iam.detach_role_policy(
            RoleName=role_name,
            PolicyArn="arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore",
        )
    except Exception:
        pass
    try:
        iam.delete_role(RoleName=role_name)
    except Exception:
        pass


def _create_ssm_resources(iam, resource_name: str) -> str:  # noqa: ANN001
    """Create a new IAM role and instance profile for SSM.

    Returns the instance profile name.
    """
    role_name = f"remo-{resource_name}-ssm-role"
    ip_name = f"remo-{resource_name}-ssm-profile"

    assume_role_policy = json.dumps(
        {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Principal": {"Service": "ec2.amazonaws.com"},
                    "Action": "sts:AssumeRole",
                }
            ],
        }
    )

    SSM_POLICY_ARN = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"

    try:
        print_info("Creating IAM role and instance profile for SSM...")

        iam.create_role(
            RoleName=role_name,
            AssumeRolePolicyDocument=assume_role_policy,
            Description="remo SSM Session Manager access role",
            Tags=[
                {"Key": "remo", "Value": "true"},
                {"Key": "remo_resource_name", "Value": resource_name},
            ],
        )

        iam.attach_role_policy(
            RoleName=role_name,
            PolicyArn=SSM_POLICY_ARN,
        )

        iam.create_instance_profile(
            InstanceProfileName=ip_name,
            Tags=[
                {"Key": "remo", "Value": "true"},
                {"Key": "remo_resource_name", "Value": resource_name},
            ],
        )

        iam.add_role_to_instance_profile(
            InstanceProfileName=ip_name,
            RoleName=role_name,
        )

        # Wait for IAM to propagate
        time.sleep(10)

        print_success(f"Created IAM role and instance profile: {ip_name}")
        return ip_name

    except Exception as exc:
        print_error(f"Failed to create IAM resources: {exc}")
        print("")
        print(
            "You may need to create an IAM instance profile manually with the\n"
            "AmazonSSMManagedInstanceCore policy attached, then re-run with "
            "--iam-profile <name>."
        )
        sys.exit(1)


def create(
    name: str = "",
    instance_type: str = "",
    region: str = "",
    volume_size: str = "",
    use_spot: bool = False,
    iam_profile: str = "",
    tools_only: tuple[str, ...] = (),
    tools_skip: tuple[str, ...] = (),
    verbose: bool = False,
) -> int:
    """Create a new AWS EC2 instance and configure it with dev tools.

    Returns the ansible-playbook exit code (0 on success).
    """
    if name:
        validate_name(name, "instance name")
    volume_size = parse_volume_size(volume_size)

    print_info("Creating AWS EC2 instance...")

    resource_name = name or os.environ.get("USER", "remo")
    effective_region = _effective_region(region)

    # SSM pre-checks
    require_session_manager_plugin()

    # Determine IAM instance profile
    iam_created = False
    backend = os.environ.get("REMO_BROKER_BACKEND", "")
    dev_id = os.environ.get("REMO_DEV_ID", "") or os.environ.get("USER", "remo")
    if iam_profile:
        print_info(f"Using provided IAM instance profile: {iam_profile}")
        selected_profile = iam_profile
    elif backend:
        # Phase 3, US1: per-developer broker instance profile (research R3).
        session = _boto3_session(effective_region)
        iam = session.client("iam")
        _role_name, profile_name = _ensure_broker_instance_role(
            iam, dev_id=dev_id, region=effective_region
        )
        selected_profile = profile_name
        iam_created = True
        print_info(f"Using broker IAM instance profile: {selected_profile}")
    else:
        selected_profile = select_ssm_instance_profile(resource_name, effective_region)
        iam_created = True  # May or may not have been created; safe default

    extra_vars: list[str] = [
        "-e", f"aws_iam_instance_profile={selected_profile}",
        "-e", f"aws_iam_created={'true' if iam_created else 'false'}",
    ]

    if name:
        extra_vars.extend(["-e", f"aws_resource_name={name}"])
    if instance_type:
        extra_vars.extend(["-e", f"aws_instance_type={instance_type}"])
    if region:
        extra_vars.extend(["-e", f"aws_region={region}"])
    if volume_size:
        extra_vars.extend(["-e", f"aws_ebs_size={volume_size}"])
    if use_spot:
        extra_vars.extend(["-e", "aws_use_spot=true"])

    tz = detect_timezone()
    if tz:
        extra_vars.extend(["-e", f"timezone={tz}"])

    extra_vars.extend(build_tool_args(tools_only, tools_skip))

    current = get_current_version()
    if current != "unknown":
        extra_vars.extend(["-e", f"remo_version={current}"])

    rc = run_playbook("aws_site.yml", extra_vars, verbose=verbose)

    if rc != 0:
        return rc

    # Save to known_hosts on success -- get the instance IP and ID.
    instance = _get_running_instance(resource_name, effective_region)

    if instance:
        instance_ip = instance.get("PublicIpAddress", "")
        instance_id = instance.get("InstanceId", "")

        if instance_ip or instance_id:
            save_known_host(
                KnownHost(
                    type="aws",
                    name=resource_name,
                    host=instance_ip or instance_id,
                    user="remo",
                    instance_id=instance_id,
                    access_mode="ssm",
                    region=effective_region,
                )
            )
    else:
        print_warning(
            "Could not detect instance IP. Run 'remo aws info' to register the host."
        )

    # Print post-create summary.
    instance_id_str = (
        instance.get("InstanceId", "") if instance else ""
    )
    instance_ip_str = (
        instance.get("PublicIpAddress", "N/A") if instance else "N/A"
    )

    print("")
    print_success("==================================================")
    print_success("  AWS instance created successfully!")
    print_success("==================================================")
    print("")
    print(f"  Name:       remo-{resource_name}")
    instance_type_str = instance_type or "m6a.large"
    if instance_id_str:
        instance_type_str += f" ({instance_id_str})"
    print(f"  Instance:   {instance_type_str}")
    print(f"  Region:     {effective_region}")
    print(f"  IP:         {instance_ip_str}")
    print("  Access:     ssm")
    print(f"  Storage:    {volume_size or '20'} GB EBS (gp3)")
    print("")
    print("  Connect:  remo shell")
    print_success("==================================================")
    print("")

    return rc


def destroy(
    name: str = "",
    auto_confirm: bool = False,
    remove_storage: bool = False,
    verbose: bool = False,
) -> int:
    """Destroy an AWS EC2 instance.

    Returns the ansible-playbook exit code (0 on success).
    """
    if name:
        validate_name(name, "instance name")

    if remove_storage:
        print_warning(
            "WARNING: --remove-storage will destroy all data on the storage volume!"
        )

    resource_name = name or os.environ.get("USER", "remo")
    region = get_aws_region(resource_name)

    # FR-020 through FR-023: surface remo-managed EBS snapshots before destroy.
    # Failure to enumerate (instance already gone, no creds, etc.) downgrades
    # to a warning so the destroy itself can still proceed.
    try:
        _pre = snapshot_list(instance_name=resource_name, region=region)
    except Exception as e:  # noqa: BLE001
        print_warning(
            f"Could not list snapshots before destroy ({e}); "
            f"proceeding without snapshot cleanup."
        )
        _pre = []
    handle_destroy_snapshot_cleanup(
        provider_label="AWS",
        instance=resource_name,
        snapshots=_pre,
        delete_one=lambda snap: snapshot_delete(
            instance_name=resource_name,
            snap_name=snap.name,
            region=region,
            auto_confirm=True,
        ),
        auto_confirm=auto_confirm,
        show_status=True,
    )

    if not auto_confirm:
        prompt = (
            f"Destroy AWS EC2 instance for 'remo-{resource_name}' in {region}? "
            "This cannot be undone."
        )
        if not confirm(prompt):
            print_info("Aborted.")
            return 0

    print_info("Destroying AWS EC2 instance...")

    extra_vars: list[str] = []

    if name:
        extra_vars.extend(["-e", f"aws_resource_name={name}"])
    extra_vars.extend(
        ["-e", f"remove_storage={'true' if remove_storage else 'false'}"]
    )
    extra_vars.extend(["-e", f"aws_region={region}"])

    rc = run_playbook("aws_teardown.yml", extra_vars, verbose=verbose)

    # Remove from known_hosts.
    remove_known_host("aws", resource_name)

    return rc


def update(
    name: str = "",
    volume_size: str = "",
    tools_only: tuple[str, ...] = (),
    tools_skip: tuple[str, ...] = (),
    verbose: bool = False,
) -> int:
    """Re-configure dev tools on an existing AWS EC2 instance.

    Queries boto3 for the running instance to get current IP and instance
    ID, updates the known-hosts registry, then runs the configure playbook.
    When *volume_size* is provided, grow the EBS volume and the filesystem
    first (idempotent — no-op when sizes match).

    Returns the ansible-playbook exit code (0 on success).
    """
    if name:
        validate_name(name, "instance name")
    volume_size = parse_volume_size(volume_size)

    resource_name = name or os.environ.get("USER", "remo")
    region = get_aws_region(resource_name)

    # Query boto3 for running instance info.
    instance = _get_running_instance(resource_name, region)

    if not instance:
        print_error(f"Could not find running AWS instance for '{resource_name}'")
        print(f"Run 'remo aws info --name {resource_name}' to check instance status.")
        sys.exit(1)

    instance_ip = instance.get("PublicIpAddress", "")
    instance_id = instance.get("InstanceId", "")

    # Update known_hosts with current info.
    save_known_host(
        KnownHost(
            type="aws",
            name=resource_name,
            host=instance_ip or instance_id,
            user="remo",
            instance_id=instance_id,
            access_mode="ssm",
            region=region,
        )
    )

    if volume_size:
        print_info(f"Resizing EBS volume for {instance_id} to {volume_size}GB...")
        resize_vars: list[str] = [
            "-e", f"aws_resource_name={resource_name}",
            "-e", f"aws_instance_id={instance_id}",
            "-e", f"aws_region={region}",
            "-e", f"volume_size={volume_size}",
        ]
        rc = run_playbook("aws_resize.yml", resize_vars, verbose=verbose)
        if rc != 0:
            return rc

    extra_vars: list[str] = [
        "-e", "aws_access_mode=ssm",
        "-e", f"aws_instance_id={instance_id}",
        "-e", f"instance_ip={instance_id}",
    ]

    extra_vars.extend(build_tool_args(tools_only, tools_skip))

    tz = detect_timezone()
    if tz:
        extra_vars.extend(["-e", f"timezone={tz}"])

    current = get_current_version()
    if current != "unknown":
        extra_vars.extend(["-e", f"remo_version={current}"])

    print_info(f"Updating AWS instance {instance_id} via SSM...")

    return run_playbook("aws_configure.yml", extra_vars, verbose=verbose)


# ---------------------------------------------------------------------------
# Instance lookup helper
# ---------------------------------------------------------------------------


def _find_remo_instance(
    resource_name: str, region: str, states: list[str] | None = None
) -> dict | None:
    """Find a remo EC2 instance by resource name and optional state filter."""
    session = _boto3_session(region)
    ec2 = session.client("ec2")
    filters = [
        {"Name": "tag:Name", "Values": [f"remo-{resource_name}"]},
        {"Name": "tag:remo", "Values": ["true"]},
    ]
    if states:
        filters.append({"Name": "instance-state-name", "Values": states})
    response = ec2.describe_instances(Filters=filters)
    for reservation in response.get("Reservations", []):
        for instance in reservation.get("Instances", []):
            return instance
    return None


# ---------------------------------------------------------------------------
# List / Sync / Stop / Start / Reboot / Info
# ---------------------------------------------------------------------------


def list_hosts() -> None:
    """Print a formatted table of all registered AWS instances."""
    hosts = get_known_hosts(type_filter="aws")

    if not hosts:
        print_info("No AWS instances registered.")
        print("Hint: Use 'remo aws create' to create a new instance,")
        print("      or 'remo aws sync' to import existing instances.")
        return

    # Header
    print(f"{'NAME':<20} {'INSTANCE':<20} CONNECT")
    print(f"{'----':<20} {'--------':<20} -------")

    for host in hosts:
        instance_id = host.instance_id or "N/A"
        print(f"{host.name:<20} {instance_id:<20} remo shell")


def sync(region: str = "") -> None:
    """Sync local known-hosts registry with running AWS EC2 instances.

    Queries EC2 for instances tagged ``remo=true`` that are currently
    running, clears all existing AWS entries in the registry, and
    re-registers each discovered instance.
    """
    _require_boto3()
    effective_region = _effective_region(region)
    session = _boto3_session(effective_region)
    ec2 = session.client("ec2")

    print_info(f"Syncing AWS instances in region {effective_region}...")

    response = ec2.describe_instances(
        Filters=[
            {"Name": "tag:remo", "Values": ["true"]},
            {"Name": "instance-state-name", "Values": ["running"]},
        ]
    )

    instances: list[dict] = []
    for reservation in response.get("Reservations", []):
        for instance in reservation.get("Instances", []):
            instances.append(instance)

    # Clear all existing AWS entries before re-populating.
    clear_known_hosts_by_type("aws")

    if not instances:
        print_info("No running remo instances found.")
        return

    for instance in instances:
        tags = {
            t["Key"]: t["Value"] for t in instance.get("Tags", [])
        }
        name_tag = tags.get("Name", "")
        # Strip the remo- prefix from the Name tag.
        resource_name = name_tag.removeprefix("remo-") if name_tag else ""
        if not resource_name:
            continue

        ip = instance.get("PublicIpAddress", "")
        instance_id = instance.get("InstanceId", "")
        access_mode = tags.get("remo_access_mode", "ssm")

        save_known_host(
            KnownHost(
                type="aws",
                name=resource_name,
                host=ip or instance_id,
                user="remo",
                instance_id=instance_id,
                access_mode=access_mode,
                region=effective_region,
            )
        )
        print_success(f"  Registered: {resource_name} ({instance_id})")

    print_success(f"Synced {len(instances)} instance(s).")


def stop(name: str = "", auto_confirm: bool = False) -> None:
    """Stop an AWS EC2 instance.

    Finds the instance by its remo tags, confirms with the user (unless
    *auto_confirm* is ``True``), stops it, and waits for the stopped state.
    """
    _require_boto3()

    resource_name = name or os.environ.get("USER", "remo")
    if name:
        validate_name(name, "instance name")
    region = get_aws_region(resource_name)

    instance = _find_remo_instance(
        resource_name, region,
        states=["pending", "running", "stopping", "stopped"],
    )

    if not instance:
        print_error(f"No AWS instance found for '{resource_name}'.")
        sys.exit(1)

    instance_id = instance["InstanceId"]
    state = instance["State"]["Name"]

    if state == "stopped":
        print_info(f"Instance {instance_id} is already stopped.")
        return

    if state in ("stopping", "pending"):
        print_error(
            f"Instance {instance_id} is currently {state}. Please wait and try again."
        )
        sys.exit(1)

    if not auto_confirm:
        if not confirm(f"Stop instance {instance_id} (remo-{resource_name})?"):
            print_info("Aborted.")
            return

    print_info(f"Stopping instance {instance_id}...")

    session = _boto3_session(region)
    ec2 = session.client("ec2")
    ec2.stop_instances(InstanceIds=[instance_id])

    print_info("Waiting for instance to stop...")
    waiter = ec2.get_waiter("instance_stopped")
    waiter.wait(InstanceIds=[instance_id])

    print_success(f"Instance {instance_id} stopped.")


def start(name: str = "") -> None:
    """Start a stopped AWS EC2 instance.

    Starts the instance, waits for it to reach the running state, waits
    for the SSM agent to come online, then updates the known-hosts
    registry with the new public IP.
    """
    _require_boto3()

    resource_name = name or os.environ.get("USER", "remo")
    if name:
        validate_name(name, "instance name")
    region = get_aws_region(resource_name)

    instance = _find_remo_instance(
        resource_name, region,
        states=["pending", "running", "stopping", "stopped"],
    )

    if not instance:
        print_error(f"No AWS instance found for '{resource_name}'.")
        sys.exit(1)

    instance_id = instance["InstanceId"]
    state = instance["State"]["Name"]

    if state == "running":
        print_info(f"Instance {instance_id} is already running.")
        return

    if state in ("stopping", "pending"):
        print_error(
            f"Instance {instance_id} is currently {state}. Please wait and try again."
        )
        sys.exit(1)

    print_info(f"Starting instance {instance_id}...")

    session = _boto3_session(region)
    ec2 = session.client("ec2")
    ec2.start_instances(InstanceIds=[instance_id])

    print_info("Waiting for instance to start...")
    waiter = ec2.get_waiter("instance_running")
    waiter.wait(InstanceIds=[instance_id])

    # Wait for SSM agent to come online.
    print_info("Waiting for SSM agent...")
    ssm = session.client("ssm")
    for _ in range(30):
        resp = ssm.describe_instance_information(
            Filters=[{"Key": "InstanceIds", "Values": [instance_id]}]
        )
        info_list = resp.get("InstanceInformationList", [])
        if info_list and info_list[0].get("PingStatus") == "Online":
            print_info("SSM agent online.")
            break
        time.sleep(2)
    else:
        print_warning(
            "SSM agent did not come online within 60s. It may need more time."
        )

    # Re-describe to get new public IP.
    response = ec2.describe_instances(InstanceIds=[instance_id])
    new_ip = ""
    for reservation in response.get("Reservations", []):
        for inst in reservation.get("Instances", []):
            new_ip = inst.get("PublicIpAddress", "")
            break

    # Determine access mode from tags.
    tags = {t["Key"]: t["Value"] for t in instance.get("Tags", [])}
    access_mode = tags.get("remo_access_mode", "ssm")

    # Update known_hosts with new IP.
    save_known_host(
        KnownHost(
            type="aws",
            name=resource_name,
            host=new_ip or instance_id,
            user="remo",
            instance_id=instance_id,
            access_mode=access_mode,
            region=region,
        )
    )

    print_success(f"Instance {instance_id} started successfully.")


def reboot(name: str = "", auto_confirm: bool = False) -> None:
    """Reboot a running AWS EC2 instance.

    The instance must be in the ``running`` state.  Asks for confirmation
    unless *auto_confirm* is ``True``, then reboots and waits for the
    instance status check to pass.
    """
    _require_boto3()

    resource_name = name or os.environ.get("USER", "remo")
    if name:
        validate_name(name, "instance name")
    region = get_aws_region(resource_name)

    instance = _find_remo_instance(
        resource_name, region,
        states=["pending", "running", "stopping", "stopped"],
    )

    if not instance:
        print_error(f"No AWS instance found for '{resource_name}'.")
        sys.exit(1)

    instance_id = instance["InstanceId"]
    state = instance["State"]["Name"]

    if state != "running":
        print_error(
            f"Instance {instance_id} is {state}. Can only reboot a running instance."
        )
        sys.exit(1)

    if not auto_confirm:
        if not confirm(f"Reboot instance {instance_id} (remo-{resource_name})?"):
            print_info("Aborted.")
            return

    print_info(f"Rebooting instance {instance_id}...")

    session = _boto3_session(region)
    ec2 = session.client("ec2")
    ec2.reboot_instances(InstanceIds=[instance_id])

    print_info("Waiting for instance status check...")
    waiter = ec2.get_waiter("instance_status_ok")
    waiter.wait(InstanceIds=[instance_id])

    print_success(f"Instance {instance_id} rebooted successfully.")


def info(name: str = "") -> None:
    """Print detailed information about an AWS EC2 instance.

    Also registers the instance in known-hosts if it is not already
    present.
    """
    _require_boto3()

    resource_name = name or os.environ.get("USER", "remo")
    if name:
        validate_name(name, "instance name")
    region = get_aws_region(resource_name)

    instance = _find_remo_instance(
        resource_name, region,
        states=["pending", "running", "stopping", "stopped"],
    )

    if not instance:
        print_error(f"No AWS instance found for '{resource_name}'.")
        sys.exit(1)

    instance_id = instance["InstanceId"]
    state = instance["State"]["Name"]
    instance_type = instance.get("InstanceType", "N/A")
    public_ip = instance.get("PublicIpAddress", "N/A")
    public_dns = instance.get("PublicDnsName", "N/A")
    launch_time = instance.get("LaunchTime", "N/A")
    tags = {t["Key"]: t["Value"] for t in instance.get("Tags", [])}
    access_mode = tags.get("remo_access_mode", "ssm")

    # Best-effort lookup of vCPU / memory from the instance type spec.
    cores: str = "?"
    memory_mib: str = "?"
    try:
        session = _boto3_session(region)
        type_resp = session.client("ec2").describe_instance_types(
            InstanceTypes=[instance_type]
        )
        type_info = (type_resp.get("InstanceTypes") or [{}])[0]
        vcpu_info = type_info.get("VCpuInfo") or {}
        memory_info = type_info.get("MemoryInfo") or {}
        if vcpu_info.get("DefaultVCpus"):
            cores = str(vcpu_info["DefaultVCpus"])
        if memory_info.get("SizeInMiB"):
            memory_mib = str(memory_info["SizeInMiB"])
    except Exception:
        # Non-fatal — fall back to '?'.
        pass

    # Best-effort lookup of the persistent EBS volume size.
    ebs_size: str = "(none)"
    try:
        ebs_name = f"remo-{resource_name}-home"
        ebs_resp = session.client("ec2").describe_volumes(
            Filters=[
                {"Name": "tag:Name", "Values": [ebs_name]},
                {"Name": "tag:remo", "Values": ["true"]},
            ],
        )
        volumes = ebs_resp.get("Volumes", [])
        if volumes:
            ebs_size = f"{volumes[0].get('Size', '?')} GB ({ebs_name})"
    except Exception:
        pass

    print("")
    print(f"  Name:         remo-{resource_name}")
    print(f"  Instance ID:  {instance_id}")
    print(f"  State:        {state}")
    print(f"  Type:         {instance_type}")
    print(f"  Region:       {region}")
    print(f"  Public IP:    {public_ip}")
    print(f"  Public DNS:   {public_dns}")
    print(f"  Launch Time:  {launch_time}")
    print(f"  Access Mode:  {access_mode}")
    print(f"  Cores:        {cores}")
    print(f"  Memory:       {memory_mib} MiB")
    print(f"  EBS volume:   {ebs_size}")
    print("")

    # Register in known_hosts if not already present.
    existing_hosts = get_known_hosts(type_filter="aws")
    already_registered = any(h.name == resource_name for h in existing_hosts)

    if not already_registered and state == "running":
        ip = instance.get("PublicIpAddress", "")
        save_known_host(
            KnownHost(
                type="aws",
                name=resource_name,
                host=ip or instance_id,
                user="remo",
                instance_id=instance_id,
                access_mode=access_mode,
                region=region,
            )
        )
        print_info(f"Registered '{resource_name}' in known hosts.")


# ---------------------------------------------------------------------------
# Snapshots
# ---------------------------------------------------------------------------


def _get_root_volume_info(
    ec2, instance_id: str
) -> tuple[str, int, str, str, str]:
    """Return ``(volume_id, size_gib, az, device_name, volume_type)`` for the
    instance's root EBS volume.

    Raises :class:`RuntimeError` if the lookup fails.
    """
    resp = ec2.describe_instances(InstanceIds=[instance_id])
    instances = [
        inst
        for r in resp.get("Reservations", [])
        for inst in r.get("Instances", [])
    ]
    if not instances:
        raise RuntimeError(f"No EC2 instance found for id {instance_id}")
    inst = instances[0]
    root_device = inst.get("RootDeviceName")
    az = inst.get("Placement", {}).get("AvailabilityZone", "")
    volume_id = ""
    for mapping in inst.get("BlockDeviceMappings", []):
        if mapping.get("DeviceName") == root_device:
            volume_id = mapping.get("Ebs", {}).get("VolumeId", "")
            break
    if not volume_id:
        raise RuntimeError(
            f"Could not locate root volume for instance {instance_id}"
        )
    vol_resp = ec2.describe_volumes(VolumeIds=[volume_id])
    volumes = vol_resp.get("Volumes", [])
    if not volumes:
        raise RuntimeError(f"describe_volumes returned nothing for {volume_id}")
    vol = volumes[0]
    return (
        volume_id,
        int(vol.get("Size", 0)),
        az,
        root_device or "/dev/sda1",
        vol.get("VolumeType", "gp3"),
    )


def _tags_to_dict(tags: list[dict] | None) -> dict[str, str]:
    return {t.get("Key", ""): t.get("Value", "") for t in (tags or [])}


def _aws_state_to_status(state: str) -> SnapshotStatus:
    if state in {"pending", "creating"}:
        return SnapshotStatus.PENDING
    if state == "completed":
        return SnapshotStatus.AVAILABLE
    return SnapshotStatus.FAILED


def _list_snapshots_for_volume(
    ec2, volume_id: str, instance_name: str
) -> list[Snapshot]:
    """Return remo-managed snapshots whose source is *volume_id*.

    Scoping by volume-id satisfies FR-027 (provider-side identity scope);
    the additional ``tag:remo=true`` filter satisfies FR-026.
    """
    resp = ec2.describe_snapshots(
        Filters=[
            {"Name": "volume-id", "Values": [volume_id]},
            {"Name": "tag:remo", "Values": ["true"]},
        ],
        OwnerIds=["self"],
    )
    snapshots: list[Snapshot] = []
    for snap in resp.get("Snapshots", []):
        tags = _tags_to_dict(snap.get("Tags"))
        user_name = tags.get("remo-snapshot-name") or snap.get("SnapshotId", "")
        started = snap.get("StartTime")
        if isinstance(started, datetime):
            created_at = (
                started.astimezone(timezone.utc)
                if started.tzinfo
                else started.replace(tzinfo=timezone.utc)
            )
        else:
            created_at = datetime.fromtimestamp(0, tz=timezone.utc)
        snapshots.append(
            Snapshot(
                provider="aws",
                instance_name=instance_name,
                name=user_name,
                backend_id=snap.get("SnapshotId", ""),
                created_at=created_at,
                size_bytes=int(snap.get("VolumeSize", 0)) * (1024**3),
                description=snap.get("Description", ""),
                status=_aws_state_to_status(snap.get("State", "")),
            )
        )
    return snapshots


def snapshot_create(
    instance_name: str,
    snap_name: str,
    description: str = "",
    region: str = "",
) -> int:
    """Create an EBS snapshot of *instance_name*'s root volume.

    Returns 0 after the provider accepts the request (no polling — per
    FR-004 / Q1).
    """
    validate_snapshot_name(snap_name)

    region = get_aws_region(instance_name) if not region else region
    instance = _get_running_instance(instance_name, region)
    if instance is None:
        print_error(
            f"No running AWS EC2 instance found for '{instance_name}' in {region}."
        )
        return 1
    instance_id = instance.get("InstanceId", "")
    if not instance_id:
        print_error(f"Could not determine InstanceId for '{instance_name}'.")
        return 1

    session = _boto3_session(region)
    ec2 = session.client("ec2")

    try:
        volume_id, _, _, _, _ = _get_root_volume_info(ec2, instance_id)
    except RuntimeError as e:
        print_error(str(e))
        return 1

    existing = _list_snapshots_for_volume(ec2, volume_id, instance_name)
    if any(s.name == snap_name for s in existing):
        print_error(
            f"Snapshot '{snap_name}' already exists for aws instance '{instance_name}'."
        )
        return 1

    try:
        resp = ec2.create_snapshot(
            VolumeId=volume_id,
            Description=description or f"remo snapshot of {instance_name}",
            TagSpecifications=[
                {
                    "ResourceType": "snapshot",
                    "Tags": [
                        {"Key": "remo", "Value": "true"},
                        {"Key": "remo-snapshot-name", "Value": snap_name},
                        {"Key": "remo-instance", "Value": instance_name},
                    ],
                }
            ],
        )
    except Exception as e:  # noqa: BLE001 — boto3 raises ClientError, surface verbatim
        print_error(f"ec2.create_snapshot failed: {e}")
        return 1

    snap_id = resp.get("SnapshotId", "")
    print_info(
        f"Snapshot '{snap_name}' creation started for {instance_name} "
        f"({snap_id}). This will take several minutes. "
        f"Run `remo aws snapshot list {instance_name}` to check status."
    )
    return 0


def _wait_for_instance_state(
    ec2, instance_id: str, target: str, timeout: int = 600
) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        r = ec2.describe_instances(InstanceIds=[instance_id])
        for reservation in r.get("Reservations", []):
            for inst in reservation.get("Instances", []):
                state = inst.get("State", {}).get("Name", "")
                if state == target:
                    return True
        time.sleep(5)
    return False


def _wait_for_volume_state(
    ec2, volume_id: str, target: str, timeout: int = 600
) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        r = ec2.describe_volumes(VolumeIds=[volume_id])
        for vol in r.get("Volumes", []):
            if vol.get("State") == target:
                return True
        time.sleep(3)
    return False


def snapshot_restore(
    instance_name: str,
    snap_name: str,
    region: str = "",
    auto_confirm: bool = False,
) -> int:
    """In-place AWS restore via volume swap (FR-013, FR-016, FR-029, FR-030).

    Steps (per research.md):
      1. Look up instance + root volume.
      2. Look up snapshot; require AVAILABLE (FR-028).
      3. Confirm with downtime warning.
      4. Stop instance → wait stopped.
      5. Detach root volume → wait available.
      6. Create new volume from snapshot at MAX(current, snapshot) size.
      7. Attach new volume at the original device → wait in-use.
      8. Start instance → wait running.
      9. Tag old volume ``remo-restore-orphan=<timestamp>`` and keep it (FR-030).
    """
    region = get_aws_region(instance_name) if not region else region

    # We need the instance even if it's stopped; describe directly.
    session = _boto3_session(region)
    ec2 = session.client("ec2")

    resp = ec2.describe_instances(
        Filters=[
            {"Name": "tag:Name", "Values": [f"remo-{instance_name}"]},
            {"Name": "tag:remo", "Values": ["true"]},
        ]
    )
    instances = [
        inst
        for r in resp.get("Reservations", [])
        for inst in r.get("Instances", [])
    ]
    if not instances:
        print_error(f"No AWS EC2 instance found for '{instance_name}' in {region}.")
        return 1
    inst = instances[0]
    instance_id = inst.get("InstanceId", "")

    try:
        old_vol_id, cur_size, az, device, vol_type = _get_root_volume_info(
            ec2, instance_id
        )
    except RuntimeError as e:
        print_error(str(e))
        return 1

    existing = _list_snapshots_for_volume(ec2, old_vol_id, instance_name)
    target = next((s for s in existing if s.name == snap_name), None)
    if target is None:
        print_error(
            f"Snapshot '{snap_name}' not found for aws instance '{instance_name}'."
        )
        return 1
    if target.status is SnapshotStatus.PENDING:
        print_error(
            f"Snapshot '{snap_name}' is still pending; "
            f"check `remo aws snapshot list {instance_name}` for status."
        )
        return 1
    if target.status is not SnapshotStatus.AVAILABLE:
        print_error(
            f"Snapshot '{snap_name}' is {target.status.value}; cannot restore."
        )
        return 1

    snap_id = target.backend_id
    snap_size_gib = (target.size_bytes or 0) // (1024**3)
    new_size = max(cur_size, snap_size_gib)

    if not auto_confirm:
        if not confirm(
            f"Restore '{snap_name}' to {instance_name}? "
            f"Instance will be stopped, root volume swapped, and restarted — "
            f"typically 2-5 minutes of downtime.",
            default=False,
        ):
            print_info("Aborted.")
            return 1

    try:
        # 4. Stop
        print_info(f"Stopping instance {instance_id}...")
        ec2.stop_instances(InstanceIds=[instance_id])
        if not _wait_for_instance_state(ec2, instance_id, "stopped"):
            raise RuntimeError("timed out waiting for instance to stop")

        # 5. Detach root
        print_info(f"Detaching root volume {old_vol_id}...")
        ec2.detach_volume(VolumeId=old_vol_id)
        if not _wait_for_volume_state(ec2, old_vol_id, "available"):
            raise RuntimeError(
                f"timed out waiting for {old_vol_id} to detach"
            )

        # 6. Create new volume from snapshot
        print_info(
            f"Creating new volume from {snap_id} at {new_size} GiB in {az}..."
        )
        cv = ec2.create_volume(
            SnapshotId=snap_id,
            AvailabilityZone=az,
            Size=new_size,
            VolumeType=vol_type,
            TagSpecifications=[
                {
                    "ResourceType": "volume",
                    "Tags": [
                        {"Key": "remo", "Value": "true"},
                        {"Key": "Name", "Value": f"remo-{instance_name}"},
                    ],
                }
            ],
        )
        new_vol_id = cv.get("VolumeId", "")
        if not _wait_for_volume_state(ec2, new_vol_id, "available"):
            raise RuntimeError(
                f"timed out waiting for new volume {new_vol_id} to become available"
            )

        # 7. Attach new volume
        print_info(f"Attaching {new_vol_id} as {device}...")
        ec2.attach_volume(
            VolumeId=new_vol_id, InstanceId=instance_id, Device=device
        )
        if not _wait_for_volume_state(ec2, new_vol_id, "in-use"):
            raise RuntimeError(
                f"timed out waiting for {new_vol_id} to attach"
            )

        # 8. Start
        print_info(f"Starting instance {instance_id}...")
        ec2.start_instances(InstanceIds=[instance_id])
        if not _wait_for_instance_state(ec2, instance_id, "running"):
            raise RuntimeError("timed out waiting for instance to start")

        # 9. Tag old volume as orphan (FR-030)
        now = datetime.now(tz=timezone.utc).isoformat(timespec="seconds")
        try:
            ec2.create_tags(
                Resources=[old_vol_id],
                Tags=[{"Key": "remo-restore-orphan", "Value": now}],
            )
        except Exception as e:  # noqa: BLE001
            print_warning(f"Failed to tag orphan volume {old_vol_id}: {e}")

    except Exception as e:  # noqa: BLE001
        print_error(
            f"Restore failed: {e}. The pre-restore root volume '{old_vol_id}' "
            f"is preserved in {az}. To recover, re-attach manually:\n"
            f"  aws ec2 attach-volume --region {region} "
            f"--volume-id {old_vol_id} --instance-id {instance_id} "
            f"--device {device}\nThen: aws ec2 start-instances --instance-ids "
            f"{instance_id}"
        )
        return 1

    print_info(
        f"Restored '{snap_name}' to {instance_name}. "
        f"You can reconnect with: remo shell {instance_name}"
    )
    print_info(
        f"Note: pre-restore root volume {old_vol_id} is preserved with tag "
        f"`remo-restore-orphan`. After verifying the restore, delete it with:\n"
        f"  aws ec2 delete-volume --region {region} --volume-id {old_vol_id}"
    )
    if new_size > snap_size_gib and snap_size_gib > 0:
        print_info(
            f"Filesystem currently occupies {snap_size_gib} GiB on a "
            f"{new_size} GiB volume; grow it inside the instance with: "
            f"sudo resize2fs $(findmnt -no SOURCE /)"
        )
    return 0


def snapshot_list(instance_name: str, region: str = "") -> list[Snapshot]:
    """Return remo-managed snapshots for *instance_name*'s root volume.

    Raises :class:`RuntimeError` when the instance lookup fails so the CLI
    can surface the error and exit 1 (FR-011).
    """
    region = get_aws_region(instance_name) if not region else region
    session = _boto3_session(region)
    ec2 = session.client("ec2")
    resp = ec2.describe_instances(
        Filters=[
            {"Name": "tag:Name", "Values": [f"remo-{instance_name}"]},
            {"Name": "tag:remo", "Values": ["true"]},
        ]
    )
    instances = [
        inst
        for r in resp.get("Reservations", [])
        for inst in r.get("Instances", [])
    ]
    if not instances:
        raise RuntimeError(
            f"No AWS EC2 instance found for '{instance_name}' in {region}."
        )
    instance_id = instances[0].get("InstanceId", "")
    volume_id, _, _, _, _ = _get_root_volume_info(ec2, instance_id)
    return _list_snapshots_for_volume(ec2, volume_id, instance_name)


def snapshot_delete(
    instance_name: str,
    snap_name: str,
    region: str = "",
    auto_confirm: bool = False,
) -> int:
    """Delete a remo-managed AWS snapshot by its user-facing name."""
    region = get_aws_region(instance_name) if not region else region
    session = _boto3_session(region)
    ec2 = session.client("ec2")

    # Find instance → volume → snapshots, then filter by user-facing name.
    resp = ec2.describe_instances(
        Filters=[
            {"Name": "tag:Name", "Values": [f"remo-{instance_name}"]},
            {"Name": "tag:remo", "Values": ["true"]},
        ]
    )
    instances = [
        inst
        for r in resp.get("Reservations", [])
        for inst in r.get("Instances", [])
    ]
    if not instances:
        print_error(
            f"No AWS EC2 instance found for '{instance_name}' in {region}."
        )
        return 1
    instance_id = instances[0].get("InstanceId", "")
    try:
        old_vol_id, _, _, _, _ = _get_root_volume_info(ec2, instance_id)
    except RuntimeError as e:
        print_error(str(e))
        return 1

    existing = _list_snapshots_for_volume(ec2, old_vol_id, instance_name)
    target = next((s for s in existing if s.name == snap_name), None)
    if target is None:
        print_error(
            f"Snapshot '{snap_name}' not found for aws instance '{instance_name}'."
        )
        return 1
    if target.status is SnapshotStatus.PENDING:
        print_error(
            f"Snapshot '{snap_name}' is still pending; "
            f"check `remo aws snapshot list {instance_name}` for status."
        )
        return 1

    if not auto_confirm:
        if not confirm(
            f"Delete snapshot '{snap_name}' of {instance_name}?",
            default=False,
        ):
            print_info("Aborted.")
            return 1

    try:
        ec2.delete_snapshot(SnapshotId=target.backend_id)
    except Exception as e:  # noqa: BLE001
        print_error(f"ec2.delete_snapshot failed: {e}")
        return 1

    print_info(f"Deleted snapshot '{snap_name}' of {instance_name}.")
    return 0
