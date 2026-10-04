"""remo resume command - put a terminal tab back where it was (spec 028)."""

from __future__ import annotations

import click


@click.command("resume")
@click.argument("name", required=False, default=None)
@click.option(
    "-L",
    "tunnels",
    multiple=True,
    help="Forward port: PORT or LOCAL:REMOTE",
)
@click.option(
    "--no-open",
    is_flag=True,
    default=False,
    help="Skip auto-opening browser for tunneled ports",
)
@click.option(
    "--no-update-check",
    is_flag=True,
    default=False,
    help="Skip remote version check before connecting",
)
@click.option(
    "--forget",
    is_flag=True,
    default=False,
    help="Delete this tab's resume record and exit (no connection)",
)
@click.option(
    "--forget-all",
    "forget_all",
    is_flag=True,
    default=False,
    help="Delete every resume record, rotate the tab secret, and exit",
)
def resume(
    name: str | None,
    tunnels: tuple[str, ...],
    no_open: bool,
    no_update_check: bool,
    forget: bool,
    forget_all: bool,
) -> None:
    """Reattach this terminal tab to the project session it last used.

    Every `remo shell` from a tab remembers its host (and `-p` project) under a
    one-way key; after a dropped connection, `remo resume` in the same tab asks
    that host which project the tab was attached to and reattaches it, with no
    picker. Tabs are told apart by the first of TMUX_PANE, WEZTERM_PANE,
    KITTY_WINDOW_ID, ITERM_SESSION_ID, TERM_SESSION_ID or WT_SESSION that the
    terminal sets.

    It is never worse than `remo shell`: with no tab identity, no record, a
    different NAME, a removed host, or a session that is no longer running, it
    prints one line saying why and falls back to `remo shell` (or the host's
    project menu) -- it never creates a session or lands in another tab's
    project. Exact resume needs the host's tools upgraded once (`remo configure
    NAME`, or `remo <provider> upgrade NAME`); until then the workstation's own
    memory of a `-p` project is used and the host picker is still skipped.

    NAME, when given, resumes only if this tab last used NAME.

    Examples:

      remo resume
      remo resume --forget
    """
    if forget or forget_all:
        if forget and forget_all:
            raise click.UsageError("--forget and --forget-all cannot be combined")
        if name is not None or tunnels:
            raise click.UsageError("--forget/--forget-all take no NAME or -L")
        _forget(forget_all)
        return

    import os  # noqa: PLC0415

    from remo_cli.cli.shell import (  # noqa: PLC0415
        auto_start_host,
        connect_to_host,
        tab_recorder,
        this_tab_key,
        upgrade_command_hint,
    )
    from remo_cli.core import tab_records  # noqa: PLC0415
    from remo_cli.core.known_hosts import find_remo_host_by_name, get_known_hosts  # noqa: PLC0415
    from remo_cli.core.output import print_info, print_warning  # noqa: PLC0415
    from remo_cli.core.resume import (  # noqa: PLC0415
        LookupStatus,
        ResumeReason,
        decide_resume,
        is_project_live,
        needs_project_liveness,
        resume_message,
        run_lookup,
    )
    from remo_cli.core.ssh import resolve_remo_host  # noqa: PLC0415
    from remo_cli.core.tab_identity import derive_tab_key, detect_tab_identity  # noqa: PLC0415

    identity = detect_tab_identity(os.environ)
    key: str | None = None
    record = None
    store_error = False
    if identity is not None:
        try:
            key = derive_tab_key(identity, tab_records.get_secret())
            record = tab_records.load(key)
        except tab_records.TabRecordError as e:
            # The warning is the one line; carry on as plain `remo shell`.
            print_warning(f"Could not read this tab's resume record: {e} — opening remo shell")
            identity = None
            key = None
            store_error = True

    # Exact-name scan: resolve_remo_host_by_name raises SystemExit on a miss,
    # and a removed host must fall back, not exit (FR-012).
    registered = None
    if record is not None:
        registered = next((h for h in get_known_hosts() if h.name == record.host), None)
        # NAME resolves the way `remo shell NAME` resolves it, so a HOST_SCOPED
        # short name ("dev" for "node/dev") is the recorded host, not a
        # mismatch. An unknown NAME is left as typed for the shell fallback to
        # report exactly as `remo shell NAME` would.
        if name is not None and name != record.host:
            named = find_remo_host_by_name(name)
            if named is not None:
                name = named.name

    lookup = None
    auto_started = False
    lookup_status: LookupStatus = "skipped"
    record_project_live: bool | None = None
    if (
        key is not None
        and record is not None
        and registered is not None
        and (name is None or name == record.host)
    ):
        # Start a stopped instance first, exactly as `remo shell` would, so
        # the lookup reaches a running box at its current address instead of
        # timing out into a LOOKUP_FAILED fallback (FR-014).
        registered = auto_start_host(registered)
        auto_started = True
        lookup, lookup_status = run_lookup(registered, key)
        if needs_project_liveness(lookup=lookup, lookup_status=lookup_status, record=record):
            assert record.project is not None
            record_project_live = is_project_live(registered, record.project)

    decision = decide_resume(
        identity_present=identity is not None,
        record=record,
        requested_name=name,
        host_registered=registered is not None,
        lookup=lookup,
        lookup_status=lookup_status,
        record_project_live=record_project_live,
    )

    upgrade_hint = None
    if decision.reason is ResumeReason.HOST_NOT_UPGRADED and registered is not None:
        upgrade_hint = upgrade_command_hint(registered)
    if not store_error:
        print_info(resume_message(decision, upgrade_hint=upgrade_hint))

    if decision.action == "shell":
        host = resolve_remo_host(decision.requested_name)
        # An unreadable store was already reported; don't warn twice.
        fallback_key = None if store_error else this_tab_key()
        connect_to_host(
            host,
            tunnels=tunnels,
            no_open=no_open,
            no_update_check=no_update_check,
            tab_key=fallback_key,
            # Recorded only once ssh is about to run (issue #248).
            on_connect=tab_recorder(fallback_key, host.name, None),
        )
        return

    assert registered is not None and record is not None and key is not None
    attach_project = decision.project if decision.action == "attach" else None
    # Refresh the record like any other interactive connection would, so a tab
    # that only ever resumes is not pruned after 30 days (FR-016 is about stale
    # tabs, not busy ones). A successful attach also updates the project; the
    # menu path keeps the remembered one. Best-effort and silent: the one line
    # has already been printed (FR-013). Written only once ssh is about to
    # run, never before an upgrade prompt the user can still decline, so a
    # connection that never happened cannot rewrite the record (issue #248).
    refresh = tab_recorder(key, registered.name, attach_project or record.project, quiet=True)
    connect_to_host(
        registered,
        tunnels=tunnels,
        no_open=no_open,
        no_update_check=no_update_check,
        project=attach_project,
        tab_key=key,
        auto_started=auto_started,
        on_connect=refresh,
    )


def _forget(everything: bool) -> None:
    import os  # noqa: PLC0415

    from remo_cli.core import tab_records  # noqa: PLC0415
    from remo_cli.core.output import print_error, print_info  # noqa: PLC0415
    from remo_cli.core.tab_identity import derive_tab_key, detect_tab_identity  # noqa: PLC0415

    try:
        if everything:
            tab_records.forget_all()
            print_info("Forgot every tab's resume record and rotated the tab secret")
            return
        identity = detect_tab_identity(os.environ)
        if identity is None:
            print_info("This terminal exposes no tab identity — nothing to forget")
            return
        key = derive_tab_key(identity, tab_records.get_secret())
        if tab_records.forget(key):
            print_info("Forgot this tab's resume record")
        else:
            print_info("Nothing recorded for this tab")
    except tab_records.TabRecordError as e:
        print_error(f"Could not update resume records: {e}")
        raise SystemExit(1) from e
