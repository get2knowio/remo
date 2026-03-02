"""remo cp command - Copy files to/from a remote environment."""

from __future__ import annotations

import os
import re
import sys

import click

from remo_cli.core.known_hosts import resolve_remo_host_by_name
from remo_cli.core.output import print_error, print_info
from remo_cli.core.rsync import transfer
from remo_cli.core.ssh import build_ssh_opts, resolve_remo_host

# Pattern for named remote specs: name must be 2+ chars starting with
# alphanumeric, followed by colon and a non-empty path.  Single-letter
# prefixes are excluded to avoid matching Windows drive letters like C:.
_NAMED_REMOTE_RE = re.compile(r"^([a-zA-Z0-9][a-zA-Z0-9._-]+):(.+)$")


def parse_remote_spec(arg: str) -> tuple[str, str, str]:
    """Parse a single path argument for colon notation.

    Returns
    -------
    tuple[str, str, str]
        ``(spec_type, env_name, path)`` where *spec_type* is ``"local"`` or
        ``"remote"``, *env_name* is the environment name (empty string for
        bare-colon specs), and *path* is the file path component.
    """
    # :path -> remote with bare colon (auto-select env)
    if arg.startswith(":"):
        return ("remote", "", arg[1:])

    # name:path -> remote with named env
    m = _NAMED_REMOTE_RE.match(arg)
    if m:
        return ("remote", m.group(1), m.group(2))

    # Everything else -> local
    return ("local", "", arg)


@click.command()
@click.option("-r", "--recursive", is_flag=True, default=False, help="Copy directories recursively")
@click.option("--progress", is_flag=True, default=False, help="Show transfer progress")
@click.argument("args", nargs=-1, required=True)
def cp(recursive: bool, progress: bool, args: tuple[str, ...]) -> None:
    """Copy files to/from a remo environment.

    \b
    Use colon notation for remote paths:
      :path           Remote path (auto-select environment)
      name:path       Remote path on named environment

    \b
    Upload:   remo cp ./file.txt :/tmp/
    Download: remo cp :/var/log/app.log ./
    """
    # --- Phase 1: Validate positional args (need at least 2) ---
    if len(args) < 2:
        print_error("Expected at least 2 arguments: source(s) and destination.")
        click.echo()
        click.echo("Usage: remo cp [options] <source>... <destination>")
        click.echo("Run 'remo cp --help' for examples.")
        sys.exit(1)

    # --- Phase 2: Last arg = destination, rest = sources ---
    dest_arg = args[-1]
    source_args = args[:-1]

    # --- Phase 3: Parse all specs ---
    src_specs = [parse_remote_spec(s) for s in source_args]
    dest_type, dest_env_name, dest_path = parse_remote_spec(dest_arg)

    # --- Phase 4: Determine direction and validate consistency ---
    has_remote_src = any(spec[0] == "remote" for spec in src_specs)
    has_local_src = any(spec[0] == "local" for spec in src_specs)

    if has_remote_src and has_local_src:
        print_error("Cannot mix local and remote sources.")
        click.echo("All sources must be either local or remote.")
        sys.exit(1)

    if has_remote_src and dest_type == "remote":
        print_error("Cannot copy from remote to remote.")
        click.echo("One side must be local.")
        sys.exit(1)

    if not has_remote_src and dest_type == "local":
        print_error("No remote side specified.")
        click.echo()
        click.echo("Use colon notation for remote paths:")
        click.echo("  remo cp ./file.txt :/tmp/        # upload")
        click.echo("  remo cp :/var/log/app.log ./     # download")
        click.echo()
        click.echo("Run 'remo cp --help' for more examples.")
        sys.exit(1)

    if has_remote_src:
        direction = "download"
        # Validate all remote sources reference the same environment.
        remote_env_name = ""
        for spec_type, env_name, _path in src_specs:
            if spec_type == "remote":
                if not remote_env_name:
                    remote_env_name = env_name
                elif env_name != remote_env_name:
                    print_error("All remote sources must reference the same environment.")
                    sys.exit(1)
        src_paths = [spec[2] for spec in src_specs]
    else:
        direction = "upload"
        remote_env_name = dest_env_name
        src_paths = [spec[2] for spec in src_specs]

    # --- Phase 5: Resolve environment ---
    if remote_env_name:
        host = resolve_remo_host_by_name(remote_env_name)
    else:
        host = resolve_remo_host()

    # --- Phase 6: Build SSH opts ---
    ssh_opts, ssh_target = build_ssh_opts(host)

    # --- Phase 7: Validate local sources (upload only) ---
    if direction == "upload":
        for src in src_paths:
            if not os.path.exists(src):
                print_error(f"Source not found: {src}")
                sys.exit(1)
            if os.path.isdir(src) and not recursive:
                print_error(f"'{src}' is a directory. Use -r to copy directories.")
                sys.exit(1)

    # --- Phase 8: Build and execute rsync ---
    if direction == "upload":
        print_info(f"Uploading to {host.type}: {host.name}...")
        rc = transfer(
            ssh_opts=ssh_opts,
            ssh_target=ssh_target,
            sources=src_paths,
            dest=f"{ssh_target}:{dest_path}",
            recursive=recursive,
            progress=progress,
        )
    else:
        print_info(f"Downloading from {host.type}: {host.name}...")
        remote_sources = [f"{ssh_target}:{p}" for p in src_paths]
        rc = transfer(
            ssh_opts=ssh_opts,
            ssh_target=ssh_target,
            sources=remote_sources,
            dest=dest_path,
            recursive=recursive,
            progress=progress,
        )

    if rc != 0:
        sys.exit(rc)
