# Phase 1 Data Model: Dependency, Dead-Code & Documentation Hygiene

This feature introduces **no runtime entities** — no registry schema change, no new state files, no new
dataclasses in `src/remo_cli/`. The entities below are build-time and test-time constructs: the
vocabulary the drift check operates on, plus the two existing declaration shapes this feature annotates
and one it deletes.

---

## 1. `DocumentedStructureEntry` (parsed, in-memory only)

A single source path claimed by an orientation document's `## Project Structure` block. Produced by the
parser in `tests/unit/test_docs_structure.py`; never persisted.

| Field | Type | Notes |
|---|---|---|
| `path` | `str` | Repo-relative POSIX path, reconstructed from tree indentation (e.g. `src/remo_cli/core/registry.py`) |
| `line_no` | `int` | 1-indexed line within the source document, used to make failure output actionable (FR-019) |
| `document` | `str` | Which orientation file it came from — `CLAUDE.md` or `AGENTS.md` |

**Derivation rules** (normative form in [`contracts/docs-structure-check.md`](./contracts/docs-structure-check.md)):

- Depth is `len(prefix) // 4` where `prefix` is everything before the `── ` marker.
- A line's parent is the most recent entry at `depth - 1`.
- Everything from the first `#` onward is a description, not part of the path.
- A trailing `/` marks a directory; directories contribute to path reconstruction but are not themselves
  entries.

**Validation rules**:

- Exactly one path per line. A line containing `" / "` between two names is a **format error**, reported
  distinctly from a drift finding, because it silently under-reports coverage. Two such lines exist in
  `CLAUDE.md` today and are corrected by this feature (research R4).
- `path` must be unique within a document. A duplicate is a format error.

---

## 2. `DriftFinding` (produced, in-memory only)

One discrepancy between the documented structure and the tree. The check's entire output is a list of
these.

| Field | Type | Notes |
|---|---|---|
| `kind` | `"phantom" \| "undocumented" \| "stale_exclusion"` | See below |
| `path` | `str` | Repo-relative POSIX path |
| `document` | `str \| None` | Set for `phantom`; `None` for `undocumented` (the tree, not a document, is the source) |
| `hint` | `str` | Remediation sentence naming the procedure doc (FR-019) |

**Kinds**:

| Kind | Condition | Meaning |
|---|---|---|
| `phantom` | `path ∈ documented` and `path ∉ actual` | Documented file does not exist. Today: `cli/init_cmd.py`, `core/init.py`. |
| `undocumented` | `path ∈ actual`, `path ∉ documented`, `path ∉ excluded` | Real module nobody documented. Today: 9 files (research R4). |
| `stale_exclusion` | `path ∈ excluded` and `path ∉ actual` | An exclusion entry outlived its file — the 018 anti-rot guard. |

A fourth condition, `path ∈ excluded ∧ path ∈ documented`, is also a failure: a file cannot be both
deliberately undocumented and documented. Reported as a `stale_exclusion` variant.

**State**: none. The check is a pure function of (document text, filesystem) and is re-runnable with
identical results (Principle III).

---

## 3. `ExclusionSet` (source-controlled constant)

A frozenset of repo-relative paths deliberately absent from the structure documentation, declared in
`tests/unit/test_docs_structure.py` alongside a one-line reason per entry.

| Property | Value |
|---|---|
| Expected initial contents | The **seven** package-marker `__init__.py` files under `src/remo_cli/` (`cli/`, `cli/providers/`, `core/`, `models/`, `providers/`, `web/`, `web/api/`), **excluding** `src/remo_cli/__init__.py`, which carries real meaning ("Version from `importlib.metadata`") and stays documented |
| Invariant | Every member must exist on disk (`stale_exclusion` guard) |
| Invariant | No member may also appear in a structure block |
| Growth policy | Adding an entry requires a reason comment; this is the FR-020 escape hatch for a deliberately undocumented file |

Modelled directly on the transitional allowlists in `tests/unit/test_architecture.py`, where a stale
entry fails the build just as loudly as a new violation.

---

## 4. `DependencyDeclaration` (annotated, not modelled in code)

An entry in `pyproject.toml`'s `dependencies` or `optional-dependencies`. This feature adds no
structure — it adds a required *comment* shape so the rationale lives at the declaration site (FR-001).

| Attribute | Where it lives | Example |
|---|---|---|
| Package name | the declaration itself | `hcloud` |
| Install profile | which array it sits in | unconditional `dependencies` |
| Consumer | **new** comment | `hetzner.hcloud` Ansible collection |
| Rationale | **new** comment | imported by collection modules under `ansible_playbook_python` |

**Rule (FR-001)**: a dependency needs the comment **iff** it is not imported anywhere in
`src/remo_cli/`. Applies to exactly two packages today — `hcloud` and `boto3` — plus `httpx2` on the dev
side, whose comment exists but must gain the not-a-typo note (FR-007, research R2). `click`,
`InquirerPy`, and `ansible-core` are directly imported or invoked and need nothing.

**Unchanged by this feature**: every package's required-versus-optional status (FR-004a). No dependency
moves between arrays. Footprint before == footprint after (SC-005a).

---

## 5. `DeprecatedOption` — **deleted**

`core/provider_registry.py:106-112`. Introduced by 018 as a one-release deprecation carrier; its only
instance is `CREATE_YES_DEPRECATION`, referenced by all four descriptors.

```python
@dataclass(frozen=True)
class DeprecatedOption:
    name: str
    notice: str
    removal_release: str = "next release"
```

Removed entirely along with the `deprecated_options` field on `ProviderDescriptor` (FR-025, research
R6) — the notice never reached a released version, so there is nothing to wind down. Should a future
feature need staged deprecation, git history has the shape.

**Not affected**: the shared `YES` `OptionSpec` (`provider_registry.py:306`) survives untouched. It
remains injected into `destroy`, `sync`, `snapshot restore`, and `snapshot delete` — only the `create`
injection at `factory.py:161` goes.

---

## Relationships

```text
CLAUDE.md ──┐
            ├─ parsed ──> [DocumentedStructureEntry]
AGENTS.md ──┘                      │
                                   ├── diffed against ──> filesystem (src/remo_cli/**/*.py)
                                   │                              │
                       ExclusionSet ──── filters ─────────────────┘
                                   │
                                   └──> [DriftFinding] ──> pytest failure naming paths + procedure doc

pyproject.toml ──> [DependencyDeclaration] ──> comment rule (FR-001), no structural change

ProviderDescriptor ─X─> DeprecatedOption   (edge deleted)
ProviderDescriptor ────> OptionSpec YES     (edge retained for destroy/sync/snapshot, dropped for create)
```

## Traceability

| Entity | Requirements |
|---|---|
| `DocumentedStructureEntry` | FR-011, FR-017 |
| `DriftFinding` | FR-018, FR-019, FR-021 |
| `ExclusionSet` | FR-020 |
| `DependencyDeclaration` | FR-001, FR-002, FR-004, FR-004a, FR-007 |
| `DeprecatedOption` (deleted) | FR-025 |
