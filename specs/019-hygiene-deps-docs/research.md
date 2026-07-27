# Phase 0 Research: Dependency, Dead-Code & Documentation Hygiene

All findings verified against the working tree at `2c0c283` on branch `019-hygiene-deps-docs`.
Every claim below was executed, not inferred.

---

## R1 — `hcloud` and `boto3` are load-bearing through the Ansible layer, not the Python layer

**Decision**: Both stay unconditional runtime dependencies. `pyproject.toml` gains a per-dependency
comment naming the Ansible collection that consumes each, and the interpreter assumption that makes it
work.

**Rationale**: `hcloud` is imported nowhere in `src/remo_cli/` — the Hetzner provider uses raw
`urllib.request`. But `ansible/requirements.yml` pins `hetzner.hcloud >= 6.7.0`, and those modules
`import hcloud` at execution time. Both `ansible/hetzner_site.yml:10` and
`ansible/hetzner_teardown.yml:14` set `ansible_python_interpreter: "{{ ansible_playbook_python }}"` with
the comment *"Use the same Python that runs ansible-playbook (has hcloud installed)"* — i.e. the
collection deliberately relies on the CLI's own environment carrying the SDK. `amazon.aws` +
`community.aws` have the identical relationship with `boto3`, on top of the CLI's own lazy
`import boto3` in `providers/aws.py`.

Removing `hcloud` as the original description proposed would break `hetzner create`, `destroy`, and
`resize` on a clean install.

**Measured footprint** (clean `uv venv`, Python 3.12, no extras; 65 MB baseline `site-packages`):

| Stack | Size | % |
|---|---:|---:|
| `boto3` (botocore 24.6, boto3 1.05, urllib3 0.49, dateutil 0.47, s3transfer 0.35, jmespath 0.08, six 0.04) | 27.1 MB | 42% |
| `hcloud` (hcloud 0.70, idna 0.34, requests 0.26, certifi 0.25, charset_normalizer 0.23) | 1.8 MB | 2.7% |

Cross-checked end-to-end: `click + InquirerPy + ansible-core` alone resolves to 34 MB versus 65 MB for
the full set — a 31 MB delta, 29 MB of it dependencies and 1.3 MB the `remo_cli` package itself.

**Alternatives considered**:
- *Delete `hcloud`* (the original ask) — rejected: breaks Hetzner provisioning, verified above.
- *Move both to extras* — rejected here, split to [#94](https://github.com/get2knowio/remo/issues/94).
  `core/ansible_runner.py::_ensure_collections()` installs **all** collections unconditionally on the
  first playbook run of any provider (41 MB, including `amazon.aws` 9 MB and `community.aws` 6 MB), so a
  pyproject-only change captures 29 MB of a ~44 MB provider-specific footprint *and* creates a new
  failure mode: collections present, SDK absent, with no preflight in the AWS or Hetzner
  teardown/resize playbooks to catch it.
- *`boto3` optional, `hcloud` hard* — rejected: `hcloud` is 6% of the available savings; the split with
  real value is the opposite one, and it needs the collection scoping to be safe.

---

## R2 — `httpx2` is a real package, not a typo

**Decision**: Keep it. Extend the existing `pyproject.toml` comment to state explicitly that it is not a
misspelling of `httpx`, so the question is not re-opened by the next reader.

**Rationale**: `httpx2` 2.7.0 is published by pydantic (`github.com/pydantic/httpx2`, summary *"The next
generation HTTP client"*). It installs the module `httpx2`, not `httpx`. Starlette 1.3.1's
`starlette/testclient.py` resolves it first:

```python
import httpx2 as httpx      # preferred
...
    import httpx            # fallback
```

Verified in the current environment: `import httpx` raises `ModuleNotFoundError`, `import httpx2`
succeeds, `from starlette.testclient import TestClient` imports cleanly, the full suite collects
(1746 tests), and `tests/integration/test_terminal_attach.py` — which builds a real `TestClient` —
passes. The existing comment in `pyproject.toml` correctly explains *why* a transport is needed but not
*why the name looks wrong*, which is the gap that generated this item.

**Alternatives considered**: *Replace with `httpx`* — rejected: it would work (Starlette falls back) but
would silently downgrade off the transport Starlette prefers, for no reason other than the name looking
unfamiliar.

---

## R3 — The Hetzner module has four HTTP call sites with three different error contracts

**Decision**: Route all four through the existing `_hetzner_api()`, adding a small `swallow`-style
wrapper for the two best-effort sites rather than duplicating request construction. Preserve each site's
observable behavior exactly.

**Rationale**: The module already has the right helper — `_hetzner_api(method, path, body, timeout)` at
`providers/hetzner.py:460`, which raises `PreconditionError` for a missing token and
`OperationFailedError` for HTTP/transport failures, including decoding Hetzner's
`{"error": {"message": ...}}` body. `_hetzner_api_paged()` correctly builds on it. But three call sites
bypass it, and **they are not interchangeable**:

| Site | Missing token | Transport error | Timeout | Message |
|---|---|---|---|---|
| `_hetzner_api` (canonical, `:460`) | raises `PreconditionError` | raises `OperationFailedError` | 30s | `Hetzner API {method} {path} failed: {code} {msg}` |
| `_query_hetzner_server_ip` (`:57`) | returns `""` | returns `""` | 15s | none — fully silent |
| `info()` server lookup (`:296`) | raises `PreconditionError` | raises `OperationFailedError` | 15s | `Hetzner API request failed: {e}` — **different text**, and no HTTPError body decoding |
| `info()` volume lookup (`:328`) | n/a (token checked above) | swallowed, best-effort | 15s | none |

So a naive "replace with `_hetzner_api`" would change three observable behaviors: turn a silent `""`
into an exception in `_query_hetzner_server_ip`, change `info()`'s error string, and change timeouts
15s → 30s. FR-024 forbids all three.

The consolidation therefore keeps `_hetzner_api` as the single request constructor and expresses the
*policy* difference at each call site — pass `timeout=15` explicitly, and wrap the two best-effort sites
in a `try/except ProviderError` that returns the existing fallback. Error-message text for `info()` is
preserved by catching and re-raising with the original string, or by accepting the canonical message as a
deliberate, tested improvement (see the open choice below).

**Testability gain**: `tests/unit/providers/test_hetzner_{sync,label,snapshot}.py` all mock
`_hetzner_api`. The three bypassing sites are consequently **untested at the HTTP layer today** —
consolidation brings them under the existing mock, which is why a new `tests/unit/providers/test_hetzner_http.py`
can cover raise-vs-swallow per site for the first time.

**Open choice for `/speckit-tasks`**: `info()`'s error text is currently
`Hetzner API request failed: {e}` versus the canonical `Hetzner API GET /servers?... failed: ...`. No
test asserts either string. Strict FR-024 reading preserves the old text; the pragmatic reading accepts
the canonical text as strictly more informative and records it as an intentional message change. **Plan
recommends preserving the old text** — FR-024 says "identical error messages" without carve-out, and the
gain is cosmetic.

**Alternatives considered**: *Introduce a new `_HetznerClient` class* — rejected: the module is
function-based throughout, and a class adds an abstraction the four call sites do not need.

---

## R4 — The drift check: a path-reconstructing parser over the fenced structure block

**Decision**: A pytest module `tests/unit/test_docs_structure.py` that parses the ```` ```text ```` block
under `## Project Structure`, reconstructs full paths from the tree-drawing indentation, and diffs the
`src/remo_cli/**/*.py` subset against the filesystem. Explicit exclusion frozenset, with a stale-entry
guard. Runs in CI via the existing pytest job — **no workflow change**.

**Rationale**: `.github/workflows/ci.yml:29` already runs `uv run pytest` across Python 3.11/3.12/3.13,
so a test module is automatically a build gate. 018 established the pattern to copy in
`tests/unit/test_architecture.py`: an allowlist-based gate where *both* an undocumented violation *and*
a stale allowlist entry fail the build, so the list "cannot silently rot into over-permissiveness."

A prototype parser was written and run against the current `CLAUDE.md`. Reconstructing paths from
indentation depth (`len(prefix) // 4`, using the `├──`/`└──` markers and `│   ` continuations) works and
reproduces the drift exactly:

```
GROUPED (format error F-1):  2 lines
PHANTOM (claimed, absent):   2   src/remo_cli/cli/init_cmd.py, src/remo_cli/core/init.py
UNDOCUMENTED:               13   cli/added.py, providers/added.py,
                                 providers/{aws,hetzner,incus,proxmox}.py,
                                 providers/{aws,hetzner,incus,proxmox}_descriptor.py,
                                 web/operator_auth.py, web/api/pairing.py, web/pairing.py
__init__.py exclusions:      7
```

Splitting the two grouped lines documents 8 of the 13 (the four provider implementations and their four
descriptors), leaving **5** to be added by hand.

> **Correction, recorded deliberately**: an earlier basename-regex prototype reported "9 undocumented"
> because it captured only the first `.py` per line and so could not see inside the grouped lines. The
> path-reconstructing parser above is authoritative: **2 phantom / 13 undocumented / 2 format errors**.
> This is itself an argument for the check — a hand count of this section was wrong twice.

**Two formatting constraints the prototype exposed**, both requiring a one-time doc fix:

1. **Grouped lines break path reconstruction.** `CLAUDE.md` currently has
   `├── incus.py / hetzner.py / aws.py / proxmox.py` and the same for the four `*_descriptor.py`
   modules — one line naming four files. Splitting on `" / "` is ambiguous with directory separators, so
   the contract requires **one path per line**. This is why the prototype reported those two lines as
   phantom entries.
2. **`__init__.py` needs a policy, not a blanket exclusion.** `src/remo_cli/__init__.py` *is*
   meaningfully documented ("Version from importlib.metadata"), while the eight package-marker
   `__init__.py` files are noise. A blanket skip would make the documented root entry look phantom. The
   contract therefore uses an explicit `EXCLUDED_FROM_DOCS` frozenset listing exactly the files
   deliberately undocumented, and asserts every excluded path still exists — the 018 stale-allowlist
   guard.

**Scope**: the check covers `src/remo_cli/**/*.py` only. The diagram's `frontend/`, `docker/`, and
`ansible/` sections are illustrative and not exhaustively enumerated; extending the check to them would
force a much larger and lower-value documentation burden.

**Alternatives considered**:
- *Compare basenames as a multiset* — rejected: `added.py` exists in both `cli/` and `providers/`, so
  basenames cannot distinguish a moved file from a correct one.
- *A standalone CI job running a script* — rejected: duplicates infrastructure pytest already provides,
  and would run once instead of across the Python matrix.
- *A `pre-commit` hook* — rejected: not currently used by this repo, and it would not gate PRs from
  contributors who skip hooks.

---

## R5 — `update-agent-context.sh` is a live generator for two sections, and it targets `AGENTS.md` too

**Decision**: Fix the hand-maintained sections directly; write this feature's `plan.md`
**Primary Dependencies** line accurately, because the script appends it verbatim to Active Technologies.
Rewrite `AGENTS.md` by hand, then let the script keep its two managed sections in sync going forward.

**Rationale**: `.specify/scripts/bash/update-agent-context.sh` exists and is a real generator. Inspecting
it:

- It manages exactly two sections: `## Active Technologies` (appends new entries parsed from the current
  `plan.md`) and `## Recent Changes` (prepends, keeping the last 3). It **appends only** — it never
  removes a stale entry.
- It does **not** touch `## Project Structure`, `## Commands`, or `## Code Style`. Those are
  hand-maintained; the template ships them as placeholders (`[ACTUAL STRUCTURE FROM PLANS]`).
- Run with no argument it updates **all existing agent files** — which includes `AGENTS.md`
  (`AGENTS_FILE="$REPO_ROOT/AGENTS.md"`, also the target for the amp/q/bob/opencode agent types).

Three consequences for FR-016:

1. Correcting the existing wrong Active Technologies entry (`"boto3 (AWS, optional), hcloud (Hetzner,
   optional)"`, inherited from 003's plan) by direct edit **is durable** — the script will not re-add it.
2. This feature's own `plan.md` Primary Dependencies line **will** be appended on the next run, so it
   must be correct at authoring time. It is (see Technical Context: "No change… only annotates").
3. The structure section is hand-maintained, which is precisely why it drifted and why R4's check is the
   right enforcement point.

**`AGENTS.md`'s actual state**: it is a fork of `CLAUDE.md` frozen around 2026-01-06. It documents the
package as `src/remo/`, describes the superseded flat-file `known_hosts` registry, and lists three
"notifier sidecar" features (007/008/009) with dependencies — `python-telegram-bot`, `structlog`,
`tomli`, `httpx` — that appear nowhere in this repository and correspond to no `specs/` directory here.
The generator cannot repair this: it only appends. A manual rewrite is required.

**Alternatives considered**: *Delete `AGENTS.md`* — viable and explicitly permitted by FR-015, but
rejected as the default because the script will simply recreate it from the template on the next run
targeting a non-Claude agent, reintroducing placeholder content.

---

## R6 — `create --yes` removal: three code sites, one frozen test baseline

**Decision**: Remove the flag, the `DeprecatedOption` dataclass, and the four descriptor
`deprecated_options=(CREATE_YES_DEPRECATION,)` declarations. Update the frozen CLI surface baseline.

**Rationale**: 018 is unreleased — `git merge-base --is-ancestor 11196dc v2.2.0` returns false, and
`git log v2.2.0..HEAD` shows 018 (`11196dc`) among the unmerged-to-release commits. So the deprecation
notice it added has never been printed by any published version, and if 019 ships in the same release it
never will be.

Sites to change:

| Site | Change |
|---|---|
| `cli/providers/factory.py:161` | drop `params.append(_click_option(YES, descriptor))` from `_build_create` |
| `cli/providers/factory.py:164-171` | drop the `used_yes` pop and the deprecation-notice loop from `_build_create.run` |
| `core/provider_registry.py:106-112` | delete the `DeprecatedOption` dataclass |
| `core/provider_registry.py:159` | delete the `deprecated_options` descriptor field |
| `providers/*_descriptor.py` (×4) | delete `deprecated_options=(CREATE_YES_DEPRECATION,)` and the shared `CREATE_YES_DEPRECATION` constant |
| `tests/unit/cli/surface_baseline.py` | remove `"--yes"`, `"-y"` from all four `create` lists |

**`YES` itself must survive** — `provider_registry.py:306` defines it and `factory.py` still uses it at
`:227` (destroy), `:308` (sync), `:326`, `:413` (snapshot restore/delete). Only the `create` injection
goes.

`tests/unit/cli/surface_baseline.py` is the 018 FR-009 preservation reference consumed by
`tests/unit/cli/test_surface_preservation.py`; it freezes every option string of the pre-refactor CLI.
Editing it is the sanctioned way to record an intentional surface change, and is exactly the exception
FR-026 carves out.

**Alternatives considered**: *Keep the notice one more release* — rejected by the requester after being
presented with the unreleased-018 finding; it would ship and withdraw a deprecation in a single release
for a flag that was inert in every published version.

---

## R7 — Removing `_parse_pct_json` also orphans an import

**Decision**: Delete `_parse_pct_json` (`providers/proxmox.py:856-865`), its explanatory comment block
(`:850-853`), **and** `import json` at `:15`.

**Rationale**: `grep -n "json" src/remo_cli/providers/proxmox.py` returns exactly five hits: the import
at line 15, two inside the doomed function's body (`json.loads`, `json.JSONDecodeError`), and two in
comments/docstring. Once the function goes, `import json` is unused and
`uv run ruff check src/remo_cli` — a required CI job (`.github/workflows/ci.yml:107`) — fails on F401.
The comment retaining it ("kept around for symmetry with providers.incus, may be useful for a future
`--output-format json` flag") is the rationalization that let it survive; `providers/incus.py` has the
analogous parser and *does* call it, so the symmetry argument never applied.

**Alternatives considered**: *Keep it with a `# noqa: F401`-style justification* — rejected: it has no
callers, no tests, and git history preserves it if the hypothetical `--output-format json` flag ever
lands.

---

## Resolved unknowns

| Unknown from Technical Context | Resolution |
|---|---|
| Whether `hcloud`/`boto3` can be dropped | No — R1. Annotate; slimming deferred to #94. |
| Whether `httpx2` is a typo | No — R2. Real pydantic package, Starlette-preferred. |
| Whether Hetzner sites are behaviorally interchangeable | No — R3. Three distinct error contracts; consolidate the constructor, keep the policy per site. |
| Where the drift check lives and how it parses | R4. `tests/unit/test_docs_structure.py`, indentation-based path reconstruction, existing CI pytest job. |
| Whether docs are machine-generated | Partly — R5. Two sections are; the structure section is not. |
| Whether removing `--yes` breaks a frozen contract | Yes, deliberately — R6. `surface_baseline.py` is the sanctioned place to record it. |

**No `NEEDS CLARIFICATION` markers remain.**
