# Contract: Error Taxonomy & Exit-Code Mapping

**Module**: `core/errors.py` (stdlib-only). **Translation boundary**: the CLI factory's `provider_command` wrapper and the core drivers (`run_sync`) — nowhere else calls `sys.exit` for provider outcomes.

## Taxonomy

| Error | exit_code | Meaning | Message MUST include |
|-------|-----------|---------|----------------------|
| `ProviderError` (base) | 1 | Generic provider failure | What failed |
| `MissingDependencyError` | 1 | Optional SDK not installed | The install command (`uv sync --extra aws` / `pip install 'remo-cli[aws]'`) |
| `PreconditionError` | 1 | Invalid input, entry not found, wrong state, added-host guard, **unknown provider type** | What was expected, what was found |
| `OperationFailedError` | 1 | Playbook/subprocess/API failure | The operation and underlying rc/API error |
| `UserAbortedError` | 3 | User declined confirmation | (standard "Aborted." message) |

Exit-code meanings preserved (FR-003): `0` success · `1` failure · `2` Click usage error (reserved) · `3` user abort.

## Boundary behavior

```
provider verb raises ProviderError
  → provider_command wrapper: print_error(str(exc)); sys.exit(exc.exit_code)
```

- Exactly one wrapper; applied by the factory to every generated command callback.
- `run_sync` keeps returning `EXIT_OK/EXIT_FAILURE/EXIT_ABORTED`; the factory maps its return to `sys.exit(rc)`. Its probe input raises `ProbeError` (unchanged Spec-016 seam).
- The web service catches `ProviderError` (replacing today's mixed rc/`RuntimeError` handling); it never sees `SystemExit`.
- Unexpected non-`ProviderError` exceptions propagate as tracebacks (bugs stay loud).

## Prohibitions (CI-enforced, SC-003)

- No `sys.exit` anywhere in `src/remo_cli/providers/` (currently 15 sites; AWS `stop/start/reboot/info` are the main offenders).
- No bare `RuntimeError` for user-facing failures in the providers layer (currently 18 sites) — each becomes the matching typed error.
- Enforced by AST-based architecture test + ruff config (no per-file ignores for the providers package).

## Documented normalization

Today `create`/`update` exit with the raw ansible-playbook rc (may be ≥2). After this feature: any nonzero rc → `OperationFailedError` → exit **1**, rc quoted in the message. CHANGELOG entry required.
