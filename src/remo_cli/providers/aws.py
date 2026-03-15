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
from remo_cli.core.output import confirm, print_error, print_info, print_success, print_warning
from remo_cli.core.ssh import detect_timezone, require_session_manager_plugin
from remo_cli.core.validation import build_tool_args, validate_name
from remo_cli.core.version import get_current_version
from remo_cli.models.host import KnownHost


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

    print_info("Creating AWS EC2 instance...")

    resource_name = name or os.environ.get("USER", "remo")
    effective_region = _effective_region(region)

    # SSM pre-checks
    require_session_manager_plugin()

    # Determine IAM instance profile
    iam_created = False
    if iam_profile:
        print_info(f"Using provided IAM instance profile: {iam_profile}")
        selected_profile = iam_profile
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

    print_info("Destroying AWS EC2 instance...")

    resource_name = name or os.environ.get("USER", "remo")
    region = get_aws_region(resource_name)

    extra_vars: list[str] = []

    if name:
        extra_vars.extend(["-e", f"aws_resource_name={name}"])
    extra_vars.extend(["-e", f"auto_confirm={'true' if auto_confirm else 'false'}"])
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
    tools_only: tuple[str, ...] = (),
    tools_skip: tuple[str, ...] = (),
    verbose: bool = False,
) -> int:
    """Re-configure dev tools on an existing AWS EC2 instance.

    Queries boto3 for the running instance to get current IP and instance
    ID, updates the known-hosts registry, then runs the configure playbook.

    Returns the ansible-playbook exit code (0 on success).
    """
    if name:
        validate_name(name, "instance name")

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
