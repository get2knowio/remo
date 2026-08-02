"""Generates provider CLI command groups from registered descriptors.

``build_provider_group(descriptor)`` builds the shared commands
(create/destroy/upgrade/list/info/sync), the descriptor-gated ones
(``resize`` iff ``resize_dimensions``, ``tag`` iff ``supports_managed_marker``,
a ``host`` subgroup iff ``host_commands``), the ``snapshot`` subgroup, and any
descriptor-declared ``extra_commands`` (stop/start/reboot). Every callback is
wrapped by :func:`provider_command`, the single CLI-layer exit-code
translation boundary (contracts/errors.md).
"""

from __future__ import annotations

import dataclasses
import functools
import sys
from collections.abc import Callable
from typing import Any

import click

from remo_cli.core.errors import PreconditionError, ProviderError, UserAbortedError
from remo_cli.core.known_hosts import get_known_hosts
from remo_cli.core.lifecycle import run_destroy
from remo_cli.core.output import confirm
from remo_cli.core.provider_registry import (
    ALL_FLAG,
    DRY_RUN,
    NAME,
    ONLY,
    REGION,
    SKIP,
    VERBOSE,
    VOLUME_SIZE,
    YES,
    ArgumentSpec,
    CommandSpec,
    CompletionKind,
    NameFormat,
    OptionSpec,
    ProviderDescriptor,
    get_provider,
    resolve_default_name,
)
from remo_cli.core.snapshot import (
    format_snapshot_table,
    generate_default_name,
    list_all_snapshots,
    validate_name as validate_snapshot_name,
)
from remo_cli.core.web_drift import emit_out_of_date_notice
from remo_cli.models.host import KnownHost

# ---------------------------------------------------------------------------
# provider_command: the single exit-code translation boundary
# ---------------------------------------------------------------------------


def provider_command(fn: Callable[..., Any]) -> Callable[..., None]:
    """Wrap a command callback: catch ``ProviderError`` -> print + exit(exit_code).

    An int return value from *fn* is forwarded to ``sys.exit`` — this is the
    ``sync`` command's sanctioned path: ``core.reconcile.run_sync`` is a core
    driver and keeps returning ``EXIT_OK``/``EXIT_FAILURE``/``EXIT_ABORTED``
    directly (contracts/errors.md), never raising for its own outcome. Every
    other generated command's impl now returns ``None`` on success and raises
    instead (create/destroy/upgrade/resize/tag/info/host commands/extra_commands
    all migrated off int returns in Phases 3/5/6)."""

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> None:
        from remo_cli.core.output import print_error

        try:
            result = fn(*args, **kwargs)
        except ProviderError as exc:
            print_error(str(exc))
            sys.exit(exc.exit_code)
        if isinstance(result, int):
            sys.exit(result)

    return wrapper


# ---------------------------------------------------------------------------
# OptionSpec -> click.Option / click.Argument
# ---------------------------------------------------------------------------


def _make_name_completer(descriptor: ProviderDescriptor) -> Callable[..., list[str]]:
    def _complete(ctx: click.Context, param: click.Parameter, incomplete: str) -> list[str]:
        names = []
        for entry in get_known_hosts(type_filter=descriptor.type_name):
            name = entry.name
            if descriptor.name_format is NameFormat.HOST_SCOPED and "/" in name:
                name = name.split("/", 1)[1]
            names.append(name)
        return [n for n in names if n.startswith(incomplete)]

    return _complete


def _click_option(option: OptionSpec, descriptor: ProviderDescriptor) -> click.Option:
    decls = [option.name]
    if option.short:
        decls.append(option.short)
    decls.append(option.param)

    kwargs: dict[str, Any] = {"help": option.help}
    if option.completion is CompletionKind.INSTANCE_NAME:
        kwargs["shell_complete"] = _make_name_completer(descriptor)

    if option.is_flag:
        kwargs["is_flag"] = True
        kwargs["default"] = bool(option.default)
        return click.Option(decls, **kwargs)

    kwargs["type"] = option.type
    if option.multiple:
        kwargs["multiple"] = True
    if option.required:
        kwargs["required"] = True
    else:
        kwargs["default"] = option.default
    return click.Option(decls, **kwargs)


def _instance_argument(
    descriptor: ProviderDescriptor, *, required: bool = True, param: str = "instance"
) -> click.Argument:
    kwargs: dict[str, Any] = {"shell_complete": _make_name_completer(descriptor)}
    if not required:
        kwargs["required"] = False
        kwargs["default"] = None
    return click.Argument([param], **kwargs)


def _target_argument(target: ArgumentSpec, descriptor: ProviderDescriptor) -> click.Argument:
    kwargs: dict[str, Any] = {"required": target.required}
    if not target.required:
        kwargs["default"] = target.default
    if target.completion is CompletionKind.INSTANCE_NAME:
        kwargs["shell_complete"] = _make_name_completer(descriptor)
    return click.Argument([target.name], **kwargs)


def _name_help(descriptor: ProviderDescriptor) -> str:
    if isinstance(descriptor.default_instance_name, str):
        return f"Instance name (default: {descriptor.default_instance_name})."
    return "Instance name (defaults to $USER)."


def _a_or_an(display_name: str) -> str:
    return "an" if display_name[:1].lower() in "aeiou" else "a"


def _name_option(descriptor: ProviderDescriptor, *, completable: bool) -> OptionSpec:
    return dataclasses.replace(
        NAME,
        default=resolve_default_name(descriptor.default_instance_name),
        help=_name_help(descriptor),
        completion=CompletionKind.INSTANCE_NAME if completable else CompletionKind.NONE,
    )


# ---------------------------------------------------------------------------
# Shared commands
# ---------------------------------------------------------------------------


def _build_create(descriptor: ProviderDescriptor) -> click.Command:
    name_opt = _name_option(descriptor, completable=False)
    options = [name_opt, VOLUME_SIZE, ONLY, SKIP, *descriptor.create_options, VERBOSE]
    params: list[click.Parameter] = [_click_option(o, descriptor) for o in options]

    def run(**kwargs: Any) -> int | None:
        module = get_provider(descriptor.type_name)
        rc: int | None = module.create(**kwargs)
        # create() always returns None now (raises OperationFailedError on
        # failure); rc is checked for backward compatibility with any future
        # provider that still returns an int.
        if rc is None or rc == 0:
            emit_out_of_date_notice()
        return rc

    return click.Command(
        "create",
        help=f"Create {_a_or_an(descriptor.display_name)} {descriptor.display_name} instance.",
        params=params,
        callback=provider_command(run),
    )


def _resolve_entry_for_destroy(descriptor: ProviderDescriptor, display_name: str, kwargs: dict[str, Any]) -> KnownHost:
    """Find *display_name*'s registry entry, or build a minimal stub.

    The stub covers destroying an instance that was never synced/registered
    (HOST_SCOPED providers accept an explicit ``--host``/``--host-user``/
    ``--host-user`` for this, same as today). The user-flag hint is routed
    only into the KnownHost attribute that actually stores the host SSH user
    for this provider — found by locating the ``registry_fields`` entry whose
    JSON key ends in ``_user`` (``instance_id``/``host_user`` for Incus;
    ``region``/``host_user`` for Proxmox) and reading ``kwargs`` under that
    same JSON key, which equals the click param name by construction
    (research D7). It must NOT land in a non-user slot: Proxmox's
    ``instance_id`` holds the VMID, and a user value there would be forwarded
    to teardown as a bogus ``container_vmid``.
    """
    entries = get_known_hosts(type_filter=descriptor.type_name)
    if descriptor.name_format is NameFormat.HOST_SCOPED:
        for entry in entries:
            if "/" in entry.name and entry.name.endswith(f"/{display_name}"):
                return entry
        host_hint = kwargs.get("host") or "localhost"
        user_attr = ""
        user_hint = ""
        for attr, json_key in descriptor.registry_fields:
            if json_key.endswith("_user"):
                user_attr = attr
                user_hint = kwargs.get(json_key) or ""
                break
        return KnownHost(
            type=descriptor.type_name,
            name=f"{host_hint}/{display_name}",
            host="",
            user="remo",
            instance_id=user_hint if user_attr == "instance_id" else "",
            region=user_hint if user_attr == "region" else "",
        )
    for entry in entries:
        if entry.name == display_name:
            return entry
    return KnownHost(type=descriptor.type_name, name=display_name, host="", user="remo")


def _build_destroy(descriptor: ProviderDescriptor) -> click.Command:
    name_opt = _name_option(descriptor, completable=True)
    options = [name_opt, *descriptor.destroy_options, YES, VERBOSE]
    params: list[click.Parameter] = [_click_option(o, descriptor) for o in options]

    def run(**kwargs: Any) -> None:
        display_name = kwargs.pop("name")
        auto_confirm = kwargs.pop("auto_confirm")
        verbose = kwargs.pop("verbose")
        # Remaining kwargs are descriptor.destroy_options' params (e.g.
        # host/user/purge/remove_storage/remove_volume) forwarded to teardown.
        module = get_provider(descriptor.type_name)
        entry = _resolve_entry_for_destroy(descriptor, display_name, kwargs)

        run_destroy(
            entry,
            type_name=descriptor.type_name,
            display_name=display_name,
            provider_label=descriptor.display_name,
            teardown=lambda: module.teardown(entry, verbose=verbose, **kwargs),
            list_snapshots=lambda: module.snapshot_list(entry),
            delete_snapshot=lambda snap: module.snapshot_delete(entry, snap.name),
            auto_confirm=auto_confirm,
            show_status=descriptor.snapshot_async,
        )
        emit_out_of_date_notice()

    return click.Command(
        "destroy",
        help=f"Destroy {_a_or_an(descriptor.display_name)} {descriptor.display_name} instance.",
        params=params,
        callback=provider_command(run),
    )


def _build_upgrade(descriptor: ProviderDescriptor) -> click.Command:
    options = [*descriptor.upgrade_options, ONLY, SKIP, VERBOSE]
    params: list[click.Parameter] = [_instance_argument(descriptor, param="name")]
    params.extend(_click_option(o, descriptor) for o in options)

    def run(**kwargs: Any) -> None:
        module = get_provider(descriptor.type_name)
        module.upgrade(**kwargs)

    return click.Command(
        "upgrade",
        help=f"Refresh dev tools on {_a_or_an(descriptor.display_name)} {descriptor.display_name} instance.",
        params=params,
        callback=provider_command(run),
    )


def _build_resize(descriptor: ProviderDescriptor) -> click.Command:
    options = [*descriptor.resize_dimensions, *descriptor.resize_options, VERBOSE]
    params: list[click.Parameter] = [_instance_argument(descriptor, param="name")]
    params.extend(_click_option(o, descriptor) for o in options)
    dimension_params = [opt.param for opt in descriptor.resize_dimensions]
    dimension_flags = ", ".join(opt.name for opt in descriptor.resize_dimensions)

    def run(**kwargs: Any) -> None:
        if not any(kwargs.get(p) for p in dimension_params):
            raise PreconditionError(
                f"resize requires at least one dimension flag: {dimension_flags}"
            )
        module = get_provider(descriptor.type_name)
        module.resize(**kwargs)

    return click.Command(
        "resize",
        help=f"Resize {_a_or_an(descriptor.display_name)} {descriptor.display_name} instance.",
        params=params,
        callback=provider_command(run),
    )


def _build_tag(descriptor: ProviderDescriptor) -> click.Command:
    params: list[click.Parameter] = [_instance_argument(descriptor, param="name")]
    params.extend(_click_option(o, descriptor) for o in descriptor.tag_options)

    def run(**kwargs: Any) -> None:
        module = get_provider(descriptor.type_name)
        module.tag(**kwargs)

    return click.Command(
        "tag",
        help=f"Mark {_a_or_an(descriptor.display_name)} {descriptor.display_name} instance as remo-managed.",
        params=params,
        callback=provider_command(run),
    )


def _build_list(descriptor: ProviderDescriptor) -> click.Command:
    def run() -> None:
        module = get_provider(descriptor.type_name)
        module.list_hosts()

    return click.Command(
        "list",
        help=f"List registered {descriptor.display_name} instances.",
        params=[],
        callback=provider_command(run),
    )


def _build_info(descriptor: ProviderDescriptor) -> click.Command:
    name_opt = _name_option(descriptor, completable=True)
    options = [name_opt, *descriptor.info_options]
    params: list[click.Parameter] = [_click_option(o, descriptor) for o in options]

    def run(**kwargs: Any) -> int | None:
        module = get_provider(descriptor.type_name)
        return module.info(**kwargs)  # type: ignore[no-any-return]

    return click.Command(
        "info",
        help=f"Show details for {_a_or_an(descriptor.display_name)} {descriptor.display_name} instance.",
        params=params,
        callback=provider_command(run),
    )


def _build_sync(descriptor: ProviderDescriptor) -> click.Command:
    options = [*descriptor.sync_options, ALL_FLAG, YES, DRY_RUN]
    params: list[click.Parameter] = [_click_option(o, descriptor) for o in options]

    def run(**kwargs: Any) -> int:
        module = get_provider(descriptor.type_name)
        return module.sync(**kwargs)  # type: ignore[no-any-return]

    return click.Command(
        "sync",
        help=f"Discover {descriptor.display_name} instances and reconcile the registry.",
        params=params,
        callback=provider_command(run),
    )


def _build_extra_command(descriptor: ProviderDescriptor, spec: CommandSpec) -> click.Command:
    options = list(spec.options)
    if spec.confirmable:
        options = [*options, YES]
    params: list[click.Parameter] = []
    if spec.target is not None:
        params.append(_target_argument(spec.target, descriptor))
    params.extend(_click_option(o, descriptor) for o in options)

    def run(**kwargs: Any) -> int | None:
        module = get_provider(descriptor.type_name)
        impl = getattr(module, spec.impl)
        return impl(**kwargs)  # type: ignore[no-any-return]

    return click.Command(spec.name, help=spec.help, params=params, callback=provider_command(run))


def _build_host_group(descriptor: ProviderDescriptor) -> click.Group:
    group = click.Group("host", help="Operate on the hypervisor host, not an instance.")
    for spec in descriptor.host_commands:
        group.add_command(_build_extra_command(descriptor, spec))
    return group


# ---------------------------------------------------------------------------
# Snapshot subgroup (entry-based Protocol Part A verbs)
# ---------------------------------------------------------------------------


def _resolve_entry(descriptor: ProviderDescriptor, display_name: str) -> KnownHost:
    entries = get_known_hosts(type_filter=descriptor.type_name)
    if descriptor.name_format is NameFormat.HOST_SCOPED:
        for entry in entries:
            if "/" in entry.name and entry.name.endswith(f"/{display_name}"):
                return entry
    else:
        for entry in entries:
            if entry.name == display_name:
                return entry
    raise PreconditionError(
        f"No {descriptor.type_name} registry entry found for '{display_name}'. "
        f"Use `remo {descriptor.type_name} sync` to register it first."
    )


def _with_region_override(entry: KnownHost, region: str) -> KnownHost:
    return dataclasses.replace(entry, region=region) if region else entry


def _validate_snapshot_name_cb(
    ctx: click.Context, param: click.Parameter, value: str | None
) -> str | None:
    if value is not None:
        validate_snapshot_name(value)
    return value


def _build_snapshot_group(descriptor: ProviderDescriptor) -> click.Group:
    group = click.Group(
        "snapshot",
        help=f"Create / restore / delete snapshots of {descriptor.display_name} instances.",
    )
    region_scoped = descriptor.snapshot_region_scoped

    # --- create ---
    create_params: list[click.Parameter] = [
        _instance_argument(descriptor),
        click.Option(
            ["--name"],
            default=None,
            callback=_validate_snapshot_name_cb,
            help="Snapshot name (default: remo-YYYYMMDD-HHMMSS).",
        ),
        click.Option(
            ["--description"], default="", help="Free-text description shown in `snapshot list`."
        ),
    ]
    if region_scoped:
        create_params.append(_click_option(REGION, descriptor))

    def run_create(instance: str, name: str | None, description: str, region: str = "") -> None:
        entry = _with_region_override(_resolve_entry(descriptor, instance), region)
        snap_name = name or generate_default_name()
        module = get_provider(descriptor.type_name)
        module.snapshot_create(entry, snap_name, description=description)

    group.add_command(
        click.Command(
            "create",
            help="Take a snapshot.",
            params=create_params,
            callback=provider_command(run_create),
        )
    )

    # --- restore / delete (structurally identical) ---
    def _build_mutating(name: str, verb: str) -> click.Command:
        params: list[click.Parameter] = [
            _instance_argument(descriptor),
            click.Argument(["snap_name"]),
            _click_option(YES, descriptor),
        ]
        if region_scoped:
            params.append(_click_option(REGION, descriptor))

        def run(instance: str, snap_name: str, auto_confirm: bool, region: str = "") -> None:
            entry = _with_region_override(_resolve_entry(descriptor, instance), region)
            if not auto_confirm and not confirm(
                f"{verb.capitalize()} snapshot '{snap_name}' of '{instance}'?", default=False
            ):
                raise UserAbortedError("Aborted.")
            module = get_provider(descriptor.type_name)
            getattr(module, f"snapshot_{verb}")(entry, snap_name)

        return click.Command(
            name, help=f"{verb.capitalize()} a snapshot.", params=params, callback=provider_command(run)
        )

    group.add_command(_build_mutating("restore", "restore"))
    group.add_command(_build_mutating("delete", "delete"))

    # --- list ---
    list_params: list[click.Parameter] = [_instance_argument(descriptor, required=False)]
    if region_scoped:
        list_params.append(_click_option(REGION, descriptor))

    def run_list(instance: str | None, region: str = "") -> int | None:
        module = get_provider(descriptor.type_name)
        show_status = descriptor.snapshot_async
        if instance is not None:
            entry = _with_region_override(_resolve_entry(descriptor, instance), region)
            snaps = module.snapshot_list(entry)
            click.echo(format_snapshot_table(snaps, show_status=show_status, instance_label=instance))
            return None
        all_snapshots, any_failure = list_all_snapshots(descriptor.type_name, module.snapshot_list)
        click.echo(format_snapshot_table(all_snapshots, show_status=show_status))
        return 1 if any_failure else 0

    group.add_command(
        click.Command(
            "list", help="List snapshots.", params=list_params, callback=provider_command(run_list)
        )
    )

    return group


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def build_provider_group(descriptor: ProviderDescriptor) -> click.Group:
    """Build the full ``remo <type>`` command group from *descriptor*."""
    group = click.Group(descriptor.type_name, help=f"Manage {descriptor.display_name} instances.")
    for command in (
        _build_create(descriptor),
        _build_destroy(descriptor),
        _build_upgrade(descriptor),
        _build_list(descriptor),
        _build_info(descriptor),
        _build_sync(descriptor),
    ):
        group.add_command(command)
    # `resize`'s only job is to apply a dimension flag, so a provider that
    # declares no dimensions must not advertise the verb -- otherwise every
    # invocation dead-ends in "at least one dimension flag: " with an empty
    # list. Same gating shape as `tag` and the `host` subgroup below.
    if descriptor.resize_dimensions:
        group.add_command(_build_resize(descriptor))
    if descriptor.supports_managed_marker:
        group.add_command(_build_tag(descriptor))
    for spec in descriptor.extra_commands:
        group.add_command(_build_extra_command(descriptor, spec))
    if descriptor.host_commands:
        group.add_command(_build_host_group(descriptor))
    group.add_command(_build_snapshot_group(descriptor))
    return group
