"""Provider descriptor mechanism (generic, provider-agnostic) — core/provider_registry.py.

This module is the sole dispatch mechanism for provider metadata (data-model.md).
It knows nothing about incus/aws/hetzner/proxmox; provider *data* lives in
``providers/<type>_descriptor.py`` files and is registered via ``register()``,
typically from ``providers/builtin.py``.

Naming note: this is deliberately never called a bare "registry" — that name
is reserved for the host registry (``core/registry.py``, format v2).
"""

from __future__ import annotations

import importlib
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from enum import Enum
from types import ModuleType
from typing import Any

import click

from remo_cli.core.config import DEVCONTAINER_RUNTIMES

from remo_cli.core.errors import MissingDependencyError, PreconditionError

# ---------------------------------------------------------------------------
# Enums / sentinels
# ---------------------------------------------------------------------------


class NameFormat(Enum):
    """How a provider's instance names are structured (drives completion/scoping)."""

    FLAT = "flat"
    HOST_SCOPED = "host_scoped"  # "host/container"


class CompletionKind(Enum):
    """Shell-completion behavior for an OptionSpec/positional argument."""

    NONE = "none"
    INSTANCE_NAME = "instance_name"


class _LoginUserSentinel:
    """Marks a descriptor's default instance name as "the current login user"."""

    def __repr__(self) -> str:
        return "LOGIN_USER"


LOGIN_USER = _LoginUserSentinel()
DefaultName = str | _LoginUserSentinel


def resolve_default_name(default: DefaultName) -> str:
    """Resolve a ``DefaultName`` to the literal string the CLI should default to.

    ``LOGIN_USER`` resolves to ``""`` for CLI-default purposes (today's AWS/
    Hetzner behavior: the option's click default is empty; the provider fills
    in ``$USER`` downstream). Callers that want the *displayed* default name
    (for help text) should special-case ``LOGIN_USER`` themselves.
    """
    if isinstance(default, _LoginUserSentinel):
        return ""
    return default


# ---------------------------------------------------------------------------
# OptionSpec / CommandSpec
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class OptionSpec:
    """One CLI option. Shared options are declared once in the catalog below
    and reused (optionally via ``dataclasses.replace`` for a different
    per-command ``default``/``required``) so identical flags stay identical
    (SC-002)."""

    name: str  # e.g. "--volume-size"; may be a flag pair "--a/--b"
    param: str  # python kwarg name; forces click's parameter dest
    short: str | None = None  # e.g. "-v"
    type: Any = str  # click type; ignored when is_flag=True
    is_flag: bool = False
    multiple: bool = False
    default: Any = None
    required: bool = False
    help: str = ""
    completion: CompletionKind = CompletionKind.NONE


@dataclass(frozen=True)
class ArgumentSpec:
    """Declarative positional argument for descriptor-declared commands (spec 021, FR-005)."""

    name: str  # click param name == impl kwarg name
    default: str | None = None
    required: bool = True
    completion: CompletionKind = CompletionKind.NONE


@dataclass(frozen=True)
class CommandSpec:
    """A provider-specific extra command (AWS stop/start/reboot, bootstrap, ...)."""

    name: str
    help: str
    impl: str  # function name in the provider implementation module
    options: tuple[OptionSpec, ...] = ()
    confirmable: bool = False  # injects --yes/-y -> auto_confirm kwarg
    target: ArgumentSpec | None = None  # positional target, prepended before options


@dataclass(frozen=True)
class SshProxyPlan:
    """What ``ConnectionSpec.proxy_hook`` returns for a proxied SSH connection."""

    proxy_command: str
    ssh_target: str
    extra_opts: tuple[str, ...] = ()


@dataclass(frozen=True)
class ConnectionSpec:
    """How to reach a provider's instances over SSH."""

    mode_field_aware: bool = False
    proxy_hook: str | None = None  # dotted path: (KnownHost) -> SshProxyPlan | None


# ---------------------------------------------------------------------------
# ProviderDescriptor
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ProviderDescriptor:
    """Pure metadata describing a provider. No SDK imports (FR-024)."""

    type_name: str
    display_name: str
    default_instance_name: DefaultName
    name_format: NameFormat
    # (KnownHost attribute name, registry v2 nested JSON key) pairs this
    # type serializes when non-empty (drives core/registry.py's per-type
    # field map). e.g. Proxmox: (("instance_id", "vmid"), ("region", "host_user")).
    registry_fields: tuple[tuple[str, str], ...]
    connection: ConnectionSpec
    implementation: str  # dotted module path, imported lazily
    # (legacy registry v2 nested JSON key, KnownHost attribute name) pairs
    # read on parse when the attribute is still empty after registry_fields
    # (027): e.g. Proxmox's pre-rename ``node_user`` -> ``region``. Never
    # written back; the next write emits the current key, so the lazy
    # migration keeps working with no type literal in core/registry.py.
    registry_legacy_keys: tuple[tuple[str, str], ...] = ()
    # True when `remo <type> sync` scopes by region rather than by host or
    # project (AWS): drives core/reconcile.SyncScope's region matching (027).
    region_scoped_sync: bool = False
    # ``str.format`` template for SyncScope.describe() with ``{host}`` /
    # ``{region}`` placeholders; ``None`` -> "<display name lower> (all
    # servers in project)". Keeps the per-provider wording out of core (027).
    sync_scope_description: str | None = None
    create_options: tuple[OptionSpec, ...] = field(default_factory=tuple)
    upgrade_options: tuple[OptionSpec, ...] = field(default_factory=tuple)
    resize_dimensions: tuple[OptionSpec, ...] = field(default_factory=tuple)
    resize_options: tuple[OptionSpec, ...] = field(default_factory=tuple)
    tag_options: tuple[OptionSpec, ...] = field(default_factory=tuple)
    destroy_options: tuple[OptionSpec, ...] = field(default_factory=tuple)
    sync_options: tuple[OptionSpec, ...] = field(default_factory=tuple)
    info_options: tuple[OptionSpec, ...] = field(default_factory=tuple)
    # True when the provider backfills a remo-managed marker on `create`/`tag`
    # (incus/proxmox config keys and tags, hetzner labels). AWS has no backfill.
    # Drives the post-migration tagging notice and `tag` command generation;
    # keeps the type literals here rather than in core/ (Principle II).
    supports_managed_marker: bool = False
    snapshot_region_scoped: bool = False
    snapshot_async: bool = False  # True when creation is async and status is meaningful (AWS/Hetzner)
    extra_commands: tuple[CommandSpec, ...] = field(default_factory=tuple)
    host_commands: tuple[CommandSpec, ...] = field(default_factory=tuple)
    sdk_extra: str | None = None

    def __post_init__(self) -> None:
        if not self.type_name or self.type_name != self.type_name.lower():
            raise ValueError(f"type_name must be a nonempty lowercase string: {self.type_name!r}")
        for command_name, options in (
            ("create", self.create_options),
            ("upgrade", self.upgrade_options),
            ("resize", self.resize_dimensions + self.resize_options),
            ("tag", self.tag_options),
            ("destroy", self.destroy_options),
            ("sync", self.sync_options),
            ("info", self.info_options),
        ):
            names = [opt.name for opt in options]
            if len(names) != len(set(names)):
                raise ValueError(f"{self.type_name}: duplicate option names in {command_name}: {names}")
        for spec in (*self.extra_commands, *self.host_commands):
            names = [opt.name for opt in spec.options]
            if len(names) != len(set(names)):
                raise ValueError(
                    f"{self.type_name}: duplicate option names in command {spec.name!r}: {names}"
                )


class UnknownProviderError(PreconditionError):
    """Raised by lookups for an unregistered/unknown provider type (FR-006)."""


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

#: The provider API this remo exposes to out-of-tree providers (027 FR-005).
#: A plugin declares the version it targets as a ``REMO_PROVIDER_API_VERSION``
#: module attribute; a mismatch warns and still registers. Bump by hand when
#: ProviderDescriptor / the Provider Protocol change shape.
PROVIDER_API_VERSION = 1

#: Source label for the four descriptors registered by providers/builtin.py.
BUILTIN_SOURCE = "builtin"

_REGISTRY: dict[str, ProviderDescriptor] = {}
_MODULE_CACHE: dict[str, ModuleType] = {}
#: type_name -> where it came from: BUILTIN_SOURCE or "<distribution> <version>".
_SOURCES: dict[str, str] = {}
_discovered = False


def register(descriptor: ProviderDescriptor, *, source: str = BUILTIN_SOURCE) -> None:
    """Register *descriptor*. Raises ``ValueError`` on duplicate type_name (FR-007).

    *source* records provenance for ``descriptor_source()`` / ``remo providers``
    (027): built-ins keep the default; entry-point discovery passes the
    distribution name and version.
    """
    if descriptor.type_name in _REGISTRY:
        raise ValueError(f"provider type already registered: {descriptor.type_name!r}")
    _REGISTRY[descriptor.type_name] = descriptor
    _SOURCES[descriptor.type_name] = source


def _ensure_discovered() -> None:
    """Lazily register every provider, exactly once per process (027 FR-002).

    Order is fixed: providers/builtin.py first (incus, proxmox, aws, hetzner —
    unchanged since 018), then ``remo.providers`` entry points sorted by name
    (core/provider_plugins.py), unless ``REMO_DISABLE_PROVIDER_PLUGINS`` is
    set. Both steps import by dotted name at call time, so core/ keeps its
    static one-way layering (Principle I) and knows no provider's name.
    """
    global _discovered
    if _discovered:
        return
    _discovered = True
    importlib.import_module("remo_cli.providers.builtin").register_builtins()
    from remo_cli.core.provider_plugins import discover_entry_points  # noqa: PLC0415

    discover_entry_points()


# Pre-027 name, kept for any out-of-tree caller; identical behavior.
_ensure_builtins_imported = _ensure_discovered


def get_descriptor(type_name: str) -> ProviderDescriptor:
    """Return the descriptor for *type_name*. Raises ``UnknownProviderError`` naming
    the type if it isn't registered (FR-006)."""
    _ensure_discovered()
    try:
        return _REGISTRY[type_name]
    except KeyError:
        raise UnknownProviderError(f"Unknown provider type: {type_name!r}") from None


def get_provider(type_name: str) -> ModuleType:
    """Lazily import and memoize the descriptor's implementation module.

    An ``ImportError`` while importing an optional-SDK-backed implementation
    becomes a ``MissingDependencyError`` naming the extra to install.
    """
    descriptor = get_descriptor(type_name)
    cached = _MODULE_CACHE.get(type_name)
    if cached is not None:
        return cached
    try:
        module = importlib.import_module(descriptor.implementation)
    except ImportError as exc:
        if descriptor.sdk_extra:
            # NOTE: descriptor.sdk_extra values (e.g. "aws", "hetzner") name
            # extras that do not currently exist in pyproject.toml's
            # [project.optional-dependencies] — boto3/hcloud are still
            # unconditional runtime dependencies (see pyproject.toml, issue
            # #94). This message is aspirational until #94 introduces those
            # extras; descriptor.sdk_extra itself is left unchanged.
            raise MissingDependencyError(
                f"{descriptor.display_name} support requires the '{descriptor.sdk_extra}' extra. "
                f"Install it with: uv sync --extra {descriptor.sdk_extra} "
                f"(or: pip install 'remo-cli[{descriptor.sdk_extra}]')"
            ) from exc
        raise
    _MODULE_CACHE[type_name] = module
    return module


def all_descriptors() -> tuple[ProviderDescriptor, ...]:
    """All registered descriptors, in registration order (built-ins, then
    entry-point plugins in name order — 027 FR-002)."""
    _ensure_discovered()
    return tuple(_REGISTRY.values())


def builtin_descriptors() -> tuple[ProviderDescriptor, ...]:
    """Only the descriptors registered by providers/builtin.py (027 FR-017).

    The fixed vocabulary that ``web/api/hosts.py::KnownProviderType`` and the
    exact-command-set test pin; plugins never appear here.
    """
    _ensure_discovered()
    return tuple(d for d in _REGISTRY.values() if _SOURCES.get(d.type_name) == BUILTIN_SOURCE)


def descriptor_source(type_name: str) -> str:
    """``"builtin"`` or ``"<distribution> <version>"`` for a registered type (027 FR-012)."""
    get_descriptor(type_name)
    return _SOURCES[type_name]


def is_provider_type(type_name: str) -> bool:
    """``False`` for ``"ssh"``/unknown types (the ssh pseudo-type is never registered)."""
    _ensure_discovered()
    return type_name in _REGISTRY


@contextmanager
def temporary_registration(descriptor: ProviderDescriptor) -> Iterator[ProviderDescriptor]:
    """Register *descriptor* for the duration of the ``with`` block (test-only)."""
    register(descriptor)
    try:
        yield descriptor
    finally:
        _REGISTRY.pop(descriptor.type_name, None)
        _MODULE_CACHE.pop(descriptor.type_name, None)
        _SOURCES.pop(descriptor.type_name, None)


def reset_discovery_for_tests() -> None:
    """Forget every registration and load record so the next lookup re-runs
    discovery (test-only; 027 R9). Production code never calls this."""
    global _discovered
    _REGISTRY.clear()
    _MODULE_CACHE.clear()
    _SOURCES.clear()
    _discovered = False
    from remo_cli.core.provider_plugins import _reset_records  # noqa: PLC0415

    _reset_records()


# ---------------------------------------------------------------------------
# Canonical shared OptionSpec catalog (contracts/cli-surface.md) — one object
# per shared option, so identical flags are identical objects (SC-002).
# Per-command variations in `default`/`required` use dataclasses.replace().
# ---------------------------------------------------------------------------

NAME = OptionSpec(name="--name", param="name", default="", help="Instance name.")
HOST = OptionSpec(name="--host", param="host", default="", help="SSH host for the remote instance.")
DOMAIN = OptionSpec(name="--domain", param="domain", default="", help="Domain name for the instance.")
IMAGE = OptionSpec(name="--image", param="image", default="", help="Instance image to use.")
CORES = OptionSpec(name="--cores", param="cores", type=int, default=0, help="CPU core limit.")
MEMORY = OptionSpec(name="--memory", param="memory", type=int, default=0, help="Memory limit in MiB.")
VOLUME_SIZE = OptionSpec(
    name="--volume-size", param="volume_size", default="", help="Root/persistent volume size in GB/GiB."
)
ONLY = OptionSpec(
    name="--only", param="tools_only", multiple=True, help="Only install/configure these tools."
)
SKIP = OptionSpec(name="--skip", param="tools_skip", multiple=True, help="Skip these tools.")
USE_IP = OptionSpec(
    name="--use-ip",
    param="use_ip",
    is_flag=True,
    help="Store the instance's IP address in known_hosts instead of its name "
    "(for setups without DNS/MagicDNS).",
)
DEVCONTAINER_RUNTIME = OptionSpec(
    name="--devcontainer-runtime",
    param="devcontainer_runtime",
    type=click.Choice(DEVCONTAINER_RUNTIMES),
    default=None,
    help="Devcontainer runtime to install and invoke. 'auto' (the default) keeps a "
    "host's recorded runtime, keeps the reference CLI on legacy hosts and on hosts "
    "whose kernel refuses nested overlayfs, and picks deacon for every other new "
    "host; 'deacon' is the single-binary Rust runtime; 'devcontainer' is the Node "
    "reference CLI. Overrides REMO_DEVCONTAINER_RUNTIME.",
)
REGION = OptionSpec(name="--region", param="region", default="", help="Provider region.")
LOCATION = OptionSpec(name="--location", param="location", default="", help="Provider location.")
VERBOSE = OptionSpec(name="--verbose", param="verbose", short="-v", is_flag=True, help="Verbose output.")

# Uniform confirmation/sync-plumbing options, injected by the factory for
# every destroy/sync (and, pre-forwarding-drop, create) command (FR-012).
YES = OptionSpec(
    name="--yes", param="auto_confirm", short="-y", is_flag=True, default=False,
    help="Skip the confirmation prompt.",
)
DRY_RUN = OptionSpec(
    name="--dry-run", param="dry_run", is_flag=True, default=False,
    help="Show what would change without writing to the registry.",
)
ALL_FLAG = OptionSpec(
    name="--all", param="include_all", is_flag=True, default=False,
    help="Discover every instance, including those without the remo managed marker.",
)

