# Contract: Project index and `remo-host projects` verbs

Owned by the host scripts. Same file and verbs on every host (local and
remote); the protocol version stays **1** and every existing verb's JSON shape
is unchanged.

## File

`~/.local/state/remo/projects.tsv` — directory mode 0700, file mode 0600.
One record per line: `<name>\t<absolute-path>\n`. No header. Written with
`mktemp` in the same directory + `mv -f` (atomic on Linux and macOS).

Validation at write time:
- name: the existing bash `validate_project_name` in `remo-host` (twin of
  `core/validation.validate_project_name`) — rejects empty, control
  characters (so tab/newline), absolute, `/`, `..`, leading dot.
- path: must be absolute after resolution; must not contain control
  characters; must exist and be a directory at `add` time.

## Resolution (`remo-lib.sh`, sourced by every script)

| Function | Behaviour |
|---|---|
| `remo_project_list` | prints `name<TAB>path<TAB>source<TAB>status` for explicit (`source=index`) ∪ implicit (`source=dir`, each non-hidden direct subdirectory of `PROJECTS_DIR` when set and present); explicit wins a name clash (the implicit one is printed with `status=shadowed`); `status` ∈ `ok`, `missing`, `shadowed` |
| `remo_project_path NAME` | prints the path for NAME (explicit first, then implicit); exit 3 if unknown |
| `remo_project_add PATH [NAME]` / `remo_project_remove NAME` | the only writers |

Every former `"$PROJECTS_DIR/$name"` site uses `remo_project_path`.

## Verbs (additive)

### `remo-host projects add --path PATH [--name NAME] --json`

Success (exit 0):
```json
{"name": "a", "path": "/Users/me/code/a", "status": "ok"}
```
Errors (exit 3, stderr message, no JSON on stdout):
- name invalid → `invalid project name: <reason>`
- name taken → `project 'a' already exists at /Users/me/code/a — use --name to pick another`
- path already indexed → `/Users/me/code/a is already registered as 'a'`
- path missing / not a directory → `no such directory: PATH`

### `remo-host projects remove --project NAME --json`

Removes the record only; files untouched. Exit 3 if NAME is not an explicit
record (an implicit `PROJECTS_DIR` project cannot be "removed" — the message
says to move or delete the folder instead).
```json
{"name": "a", "removed": true}
```

### `remo-host projects list --json`

```json
{"projects": [
  {"name": "a", "path": "/Users/me/code/a", "source": "index", "status": "ok"},
  {"name": "b", "path": "/Users/me/work/b", "source": "index", "status": "missing"},
  {"name": "c", "path": "/home/remo/projects/c", "source": "dir", "status": "ok"}
]}
```

### Changed behaviour of existing verbs (shapes unchanged)

| Verb | Change |
|---|---|
| `sessions list --json` | enumerates `remo_project_list` (status `ok` only); a project's `path` may be outside `PROJECTS_DIR` |
| `sessions attach --project N` | resolves N via `remo_project_path` |
| `projects clone` | target = `PROJECTS_DIR/<name>`, or `~/projects/<name>` (created) when `PROJECTS_DIR` is empty; appends an index record on success |
| `projects delete --project N` | deletes the checkout (existing) and drops N's record |
| `projects rebuild --project N` | resolves N via `remo_project_path` |
| `capabilities --json` | advertises `projects.add`, `projects.remove`, `projects.list` in `operations` |

## Menu

`project-menu` lists `remo_project_list`; a `missing` entry is shown with
`✗ missing` and, when selected, prints the fix (`remo local add <new-path>
--name <name>` locally, or `remo-host projects remove --project <name>`)
instead of launching.
