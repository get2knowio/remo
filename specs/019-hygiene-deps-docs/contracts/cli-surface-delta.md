# Contract: CLI Surface Delta

**Implements**: FR-025, FR-026
**Baseline**: `tests/unit/cli/surface_baseline.py`, enforced by `tests/unit/cli/test_surface_preservation.py`

018 froze the entire hand-written CLI surface as a preservation reference. This feature makes **exactly
one** intentional change to it. Everything else must remain byte-identical.

---

## 1. The one break

| Command | Before | After |
|---|---|---|
| `remo incus create` | accepts `--yes` / `-y` (no effect; prints a deprecation notice) | flag removed — `Error: No such option: --yes`, exit 2 |
| `remo hetzner create` | same | same |
| `remo aws create` | same | same |
| `remo proxmox create` | same | same |

**Exit code on use**: 2 (Click's `UsageError`), not the taxonomy's 0/1/3. This is Click's standard
unknown-option behavior and requires no code in `provider_command`.

**Why now**: the flag has never had any effect in any released version. 018 converted it from
silently-ignored to deprecated-with-notice, but 018 is unreleased (`v2.2.0` predates it), so no user has
ever seen the notice. Shipping the notice and its withdrawal in one release serves nobody. See research
R6.

---

## 2. What must NOT change

The shared `YES` `OptionSpec` (`core/provider_registry.py:306`) is **retained**. It is still injected
into four command families, and all of them use it functionally:

| Command | `--yes` behavior | Status |
|---|---|---|
| `<provider> destroy` | skips the destroy confirmation | **unchanged** |
| `<provider> sync` | satisfies the removal consent gate | **unchanged** |
| `<provider> snapshot restore` | skips confirmation | **unchanged** |
| `<provider> snapshot delete` | skips confirmation | **unchanged** |
| `remo remove` | skips confirmation (`cli/added.py:93`) | **unchanged** |

Deleting `YES` itself, or removing it from any of the above, is out of scope and would be a regression.

Also unchanged: every other option on `create` (`--name`, `--volume-size`, `--only`, `--skip`,
`--verbose`, and each provider's own `create_options`), and the `create` callback's post-success
`emit_out_of_date_notice()` behavior.

---

## 3. Code changes

| File | Change |
|---|---|
| `cli/providers/factory.py:161` | delete `params.append(_click_option(YES, descriptor))` from `_build_create` |
| `cli/providers/factory.py:164-171` | delete the `used_yes = kwargs.pop("auto_confirm", False)` block and the `descriptor.deprecated_options` notice loop |
| `core/provider_registry.py:106-112` | delete the `DeprecatedOption` dataclass |
| `core/provider_registry.py:159` | delete the `deprecated_options` field from `ProviderDescriptor` |
| `providers/incus_descriptor.py:95`, `hetzner_descriptor.py:50`, `proxmox_descriptor.py:118`, `aws_descriptor.py:99` | delete `deprecated_options=(CREATE_YES_DEPRECATION,)` |
| wherever `CREATE_YES_DEPRECATION` is defined | delete the constant |

`_build_create.run` keeps its `rc`/`emit_out_of_date_notice()` logic verbatim — only the two `--yes`
lines leave.

---

## 4. Baseline update

`tests/unit/cli/surface_baseline.py` must drop `"--yes"` and `"-y"` from the `create` list of all four
providers. Its docstring should note that the create entries diverge from the 2026-07-26 capture by this
one deliberate removal, so a future reader does not treat the divergence as corruption.

This is the FR-026 carve-out: the only sanctioned behavioral-test edit in this feature.

---

## 5. Verification

| ID | Check | Expected |
|---|---|---|
| **V-1** | `remo <p> create --yes` for each of the four providers | exit 2, `No such option: --yes` |
| **V-2** | `remo <p> create --help` for each | no `--yes` / `-y` line |
| **V-3** | `remo <p> destroy --help`, `sync --help`, `snapshot restore --help`, `snapshot delete --help` | `--yes` / `-y` **still present** |
| **V-4** | `remo remove --help` | `--yes` still present |
| **V-5** | `tests/unit/cli/test_surface_preservation.py` | passes against the updated baseline |
| **V-6** | Full suite | passes; no behavioral assertion rewritten except the baseline (SC-010) |
| **V-7** | `grep -rn "deprecated_options\|DeprecatedOption\|CREATE_YES" src/` | no hits |

---

## 6. Release note

Breaking change, to appear in the release that carries this feature:

> **Removed**: the `--yes` / `-y` flag on `remo <provider> create`. It never had any effect — creation
> has no confirmation prompt to skip. Remove it from scripts; no replacement is needed. `--yes` continues
> to work on `destroy`, `sync`, `snapshot restore`, `snapshot delete`, and `remo remove`.

Because the repo uses release-please, this belongs in the commit body as a `BREAKING CHANGE:` trailer so
it lands in the generated changelog.
