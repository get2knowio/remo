# Contract: Provider Protocol & Verb Signatures

**Consumers**: generated CLI (factory), `remo shell` update path, shared destroy/snapshot templates, sync engine, web service.
**Implementors**: `providers/{incus,hetzner,aws,proxmox}.py` modules (modules-as-protocol-implementations), any future provider.

## Part A — Typed Protocol (uniform, entry-based)

Defined in `core/provider_protocol.py`; every registered provider module MUST satisfy it (mypy static check + runtime conformance test).

```python
class Provider(Protocol):
    def update_entry(self, entry: KnownHost, *, verbose: bool = False) -> None: ...
    def teardown(self, entry: KnownHost, *, verbose: bool = False, **provider_opts: object) -> None: ...
    def probe(self, scope: SyncScope, **opts: object) -> ProbeResult: ...
    def snapshot_create(self, entry: KnownHost, snapshot_name: str) -> None: ...
    def snapshot_restore(self, entry: KnownHost, snapshot_name: str) -> None: ...
    def snapshot_delete(self, entry: KnownHost, snapshot_name: str) -> None: ...
    def snapshot_list(self, entry: KnownHost) -> list[Snapshot]: ...
```

(`self` is notation only — implementors are modules exporting these as free functions.)

Rules:

- **R-A1**: Success returns the annotated value; failure raises a `core/errors.py` taxonomy error (see `errors.md`). Never `sys.exit`, never bare `RuntimeError`, never returned exit codes.
- **R-A2**: `update_entry`/snapshot verbs receive a **resolved registry entry**; all name-format knowledge (`host/container` split, vmid from `instance_id`, user-from-`region` for Proxmox) lives inside the provider. Callers never parse names.
- **R-A3**: `teardown` performs provider destruction only. Guard, snapshot pre-cleanup, confirmation, and registry removal are the shared template's job (`lifecycle.md` ordering) — implementors MUST NOT duplicate them.
- **R-A4**: `probe` keeps Spec-016 semantics verbatim: read-only, every in-scope host marked or not, `complete` truthful, `ProbeError` on enumeration failure. New: populate `DiscoveredHost.observed` per `sync-merge.md` when a field's value is defaulted rather than observed.
- **R-A5**: `snapshot_list` is public on every provider (eliminates Incus/Proxmox private reach-ins).

## Part B — Descriptor-declared verbs (heterogeneous, CLI-facing)

`create`, `destroy`-extras, `update`, and each `CommandSpec.impl` (e.g. AWS `stop`) keep provider-natural keyword signatures. Contract:

- **R-B1**: For each generated command, the impl function's keyword parameters MUST exactly match the descriptor's declared `OptionSpec.param` names (plus nothing else required). Verified by `inspect.signature` in the conformance suite — a descriptor/impl mismatch is a test failure, not a runtime surprise.
- **R-B2**: Same error rules as R-A1. Functions return `None`; a nonzero playbook/API result raises `OperationFailedError` (rc preserved in the message; process exit normalized to 1 — documented in CHANGELOG).
- **R-B3**: Confirmation prompts inside verbs use injected `auto_confirm: bool` (factory-supplied from `--yes/-y`); declining raises `UserAbortedError`.
- **R-B4**: Optional SDK access goes through the provider's lazy-import guard raising `MissingDependencyError` naming `sdk_extra`.

## Part C — Registration

- **R-C1**: A provider registers exactly one `ProviderDescriptor` via `core/provider_registry.register()`; duplicate `type_name` raises at registration (fail-loud, startup).
- **R-C2**: Descriptor modules import only stdlib + `core/provider_registry` types — no SDKs, no heavy provider module (enforced by the startup-imports test, SC-008).
- **R-C3**: The `ssh` pseudo-type is never registered. Dispatch sites handle it as an explicit exclusion; `provider_registry.get_descriptor("ssh")` raises like any unknown type.

## Conformance gate (FR-022)

`tests/unit/providers/test_provider_conformance.py`, parametrized over `all_descriptors()` **plus** a FakeProvider registered by fixture:

1. Implementation module satisfies Part A (attrs exist, signatures compatible).
2. Every generated command's descriptor options match impl signature (R-B1).
3. No `SystemExit` escapes any verb under induced failure (monkeypatched subprocess/SDK).
4. FakeProvider's full command group mounts with zero modifications to existing CLI files (SC-001 proof).
