---
name: "speckit-next"
description: "End-to-end pipeline for one spec: take a GitHub issue, spec-prompt file, spec number or Notion queue row as the prompt, confirm it with the user, then specify → clarify (auto-accept) → plan → tasks → analyze (auto-remediate) → implement (sonnet subagent) → code-review xhigh --fix (opus subagent) → commit, push, PR → opus subagents fix any issues filed during the run. Unattended after the single confirmation. Project-neutral: resolves the repo's own test/CI/branch conventions at Step 0."
argument-hint: "Optional: a GitHub issue (#N, N, or issue URL), a spec-prompt path, a spec number, or a Notion prompt name/URL — instead of auto-detecting the next queued prompt"
compatibility: "Requires a spec-kit project (.specify/ directory and the speckit-* skill family)"
metadata:
  author: "earlybird"
  origin: "Generalized from the EarlyBird speckit-next skill; the project-specific original lives in Notion under Home / Standards."
user-invocable: true
disable-model-invocation: false
---

# Speckit Next: drive the next spec prompt end to end, unattended

Run the entire spec lifecycle for the next queued spec prompt with exactly
ONE user interaction: confirming the detected spec prompt (Step 0.5). After
that confirmation the user is not watching — never stop to ask a question
that a documented recommendation or defensible default can answer. Only stop
for destructive actions or a genuine blocker (say exactly what it is).

## Step 0.0 — Resolve the project profile (do this first, every run)

This skill is project-neutral: it names commands and conventions by role and
binds them to this repo before doing anything else. Bind them once, state the
resolved profile in one short block at the start of the run, and reuse it
throughout.

If `.claude/speckit-next.json` exists, it is authoritative — read it and skip
derivation. Its shape (every key optional):

    {
      "testAll":       "bun run test",
      "testParallel":  "bun run test",
      "testTargeted":  "bun --cwd packages/server test <files>",
      "ciLocal":       null,
      "branchFormat":  "NNN-slug",
      "commitFormat":  "feat(scope): summary (spec NNN)",
      "commitTrailer": "Co-Authored-By: ...",
      "specsDir":      "specs",
      "promptGlobs":   ["specs/phase*/SP-*.md", "specs/prompts/*.md"],
      "queue":         { "pageUrl": "...", "dataSource": "collection://..." },
      "invariants":    ["..."]
    }

Otherwise derive each and say what you derived it from:

| Role | Derive from |
| --- | --- |
| **`testAll`** / **`testParallel`** | CLAUDE.md's commands section first, then the package manifest's scripts (`package.json`, `pyproject.toml`, `Cargo.toml`, `Makefile`). Prefer the runner's own parallel flag (`pytest -n auto`, `cargo test`, `go test ./...`, `vitest`/`bun test` are parallel already). If the runner has no parallel mode, use the serial command and drop the "always parallelize" rule — do not invent flags. |
| **`testTargeted`** | The same runner scoped to files/packages (e.g. `bun --cwd packages/x test`, `pytest path/to/test_x.py`). |
| **`ciLocal`** | A local CI gate script if one exists (`scripts/ci_local.sh`, `make ci`, `bun run ci`, a `lint`+`typecheck`+`build` chain named in CLAUDE.md). **If none exists, drop every ci_local step below** — do not substitute the full test suite for it. |
| **`branchFormat`** / **`commitFormat`** / **`commitTrailer`** | CLAUDE.md conventions, then `git log --oneline -30` for the actual house style. Match what the repo does, not what this file's examples show. |
| **`specsDir`** / **`promptGlobs`** | `.specify/` config, then the real layout (`ls specs/`). Prompt files may be flat (`specs/prompts/`), phase-bucketed (`specs/phase*/SP-*.md`), or absent entirely — absent is fine, the Notion queue covers it. |
| **`queue`** | A Spec Prompts database URL/data-source recorded in `.claude/speckit-next.json`, CLAUDE.md, or the project's docs. **Never guess a database id.** If none is configured, skip the Notion path entirely and use the repo fallback — say so in the report rather than searching Notion blindly. |
| **`invariants`** | CLAUDE.md's hard rules verbatim — the package manager it mandates, forbidden operations, "deferred work becomes issues, never inline TODOs", architectural constraints. These bind every subagent you spawn; pass them through in each prompt. |

Where this file writes `<testAll>`, `<ciLocal>`, `<specsDir>` etc., substitute
the resolved value. Where a resolved value is null, the step that uses it is
skipped, not approximated.

## Step 0 — Pick the spec prompt

If `$ARGUMENTS` names a spec-prompt file, spec number, or Notion prompt (name
or URL), use it — this is the normal way to run the skill, and it takes
precedence over all auto-detection below.

> **A bare number is ambiguous — resolve it before doing anything else.**
> In projects that keep numbered prompt files *and* numbered spec
> directories, the two sequences drift apart: prompt `SP-026-<slug>` may
> target spec dir `029-<slug>`, and the offset is not constant. So "029" can
> mean either the prompt numbered 029 (targeting some later spec dir) or
> spec dir `029-*`.
>
> Resolve by checking `<specsDir>/` FIRST: if `<specsDir>/NNN-*/` exists, the
> number means **that spec directory**. Only if no such directory exists does
> it mean prompt NNN. When both readings are live and the slugs disagree, say
> so and confirm at Step 0.5 rather than guessing — picking the wrong one
> builds the wrong feature three specs downstream.

Otherwise auto-detect, treating **the Notion queue as the queue of record**
(when one is configured) and repo prompt files as the fallback:

1. **Notion queue (primary, only if `queue` resolved in Step 0.0).** Query the
   project's Spec Prompts database via the Notion MCP for rows with
   `Status = "Ready"`, ordered by `Sequence`. The next prompt is the
   lowest-Sequence Ready row whose target `NNN-slug` directory (named in the
   page's header callout) is not yet built in `<specsDir>/`. Fetch the page
   body in full — that body is the prompt.
2. **Housekeeping sweep (same query, no extra cost).** Flip any `Submitted`
   row whose target spec directory now exists on the default branch to
   `Superseded` — the spec stands in its place.
3. **Repo fallback.** If no queue is configured, the Notion MCP is
   unavailable, or the queue has no Ready rows: list prompt files
   (`<promptGlobs>`; each should carry frontmatter naming its target
   `NNN-slug`), list existing numbered specs
   (`ls <specsDir>/ | grep -E '^[0-9]{3}-'`), and pick the lowest-numbered
   prompt whose target spec is **not yet built**.

   > **"Built" means `plan.md` exists — NOT that the directory exists.**
   > Projects that draft specs ahead in bulk leave
   > `<specsDir>/NNN-slug/spec.md` present with no `plan.md` and no
   > `tasks.md`. Those are *unbuilt work waiting to be picked up*, and a
   > directory-existence test skips every one of them — in the project this
   > skill came from, that was 27 specs, the entire remaining backlog. Test
   > `[ -f <specsDir>/NNN-slug/plan.md ]`, never `[ -d <specsDir>/NNN-slug ]`.

   When the target directory already exists with only a `spec.md`, **skip
   Step 1 (Specify)** — the spec is written. Go to Step 1.5.

   Do not trust a hand-maintained roadmap or status index (`docs/ROADMAP.md`
   and friends) as the build signal: those go stale, and have been observed
   claiming a whole range was "queued" while most of it had shipped. Use them
   for phase/dependency context, and `plan.md` for status.
4. **Dedupe.** A prompt may exist in both places (a Notion page and a mirror
   file in the repo). Match on prompt number / target spec dir; until
   submission the **Notion body is authoritative** — if the two diverge, use
   Notion and note the divergence in the run report.
5. Read the chosen prompt fully, plus any GitHub issues it references
   (`gh issue view N`).

## Step 0.5 — Confirm the pick with the user (the ONLY interactive gate)

Before running specify, present the detected prompt for validation using
AskUserQuestion: state the prompt's source (Notion page URL or file path),
its title, the target spec directory (`NNN-slug`), and a one-sentence summary
of what it builds — and offer:

1. **Proceed with <prompt-id> (Recommended)** — continue unattended.
2. **Pick a different prompt** — via the "Other" free-text option the user
   can name another prompt file / spec number; re-run Step 0 with that input
   and confirm once more.

Do not start Step 1 until the user confirms. If the pipeline is being run
truly headless (e.g. from a scheduled agent where no one can answer), skip
the question, proceed with the detected prompt, and flag the unconfirmed pick
prominently in the final report and PR body.

## Step 1 — Specify

- Create a branch per `<branchFormat>` (sequential `NNN` = next free number)
  off the default branch.
- Invoke the `speckit-specify` skill, passing the prompt (the file path, or
  the fetched Notion page body) and a faithful summary of its
  requirements/constraints/issues-to-close.
- Ensure `.specify/feature.json` points at the new `<specsDir>/NNN-slug/`.
- **Mark the queue.** If the prompt came from a Notion database, update its
  row now: `Status → Submitted`, `Spec →` the forge URL of the new spec
  directory. A failed Notion write never blocks the pipeline — note it in the
  final report and keep going.

## Step 1.5 — Reconcile a stale draft (conditional, BEFORE clarify)

**Applies only when the spec already existed** — i.e. Step 0 resolved to a
directory holding a `spec.md` with no `plan.md`, whether the user named it
in `$ARGUMENTS` or auto-detection found it. A spec written by Step 1 in
this same run needs no reconciliation; skip straight to Step 2.

Being told which spec to build does **not** make it current. The
user-named path is in fact the *likelier* one to need this step, because
naming a spec is how a months-old draft gets picked up.

**Why this exists.** A drafted-ahead spec describes the codebase as it was
on its drafting date. Specs built since then move ground underneath it, and
its stalest sentences are the confident ones — "until X ships", "no such
symbol exists", "soft dependency". Clarify cannot catch this: it reasons
about *ambiguity in the spec*, not about *the spec having been overtaken*.
A stale "not yet built" claim silently flips scope, so reconciliation must
run first and clarify then works on a spec that knows what year it is.

### Detect

Indented, not fenced, on purpose: this file gets republished verbatim inside
fenced blocks, and a nested fence would terminate the outer one and corrupt
everything after it.

    DIR=<specsDir>/NNN-slug
    drafted=$(git log --diff-filter=A --format=%ad --date=short -1 -- "$DIR/spec.md")
    # specs whose plan.md landed AFTER this spec was drafted:
    for d in <specsDir>/[0-9]*/; do
      [ -f "$d/plan.md" ] || continue
      p=$(git log --diff-filter=A --format=%ad --date=short -1 -- "$d/plan.md")
      [ "$p" \> "$drafted" ] && basename "$d"
    done

Reconcile when that list is non-empty **and** the spec carries no
`### Session` block dated at or after the newest such build. Otherwise note
"no reconciliation needed (drafted $drafted, nothing built since)" and go to
Step 2.

### Do

Read the spec fully, then verify its **Dependencies** and **Assumptions**
against the code — every claim, not a sample. Delegate a read-only Explore
subagent for the sweep if the spec is large. Check at minimum:

- Every upstream spec it names: does the symbol/module/event it relies on
  exist now, and under the name the spec uses?
- Every "until X ships" / "does not yet exist" / "interim" sentence: is it
  still true? These are the highest-yield lines in the document.
- **Cross-cutting seams that postdate the draft.** Enumerate them rather than
  recalling them: list the ADRs (`docs/adr/`, `docs/decisions/`, or wherever
  this repo keeps them) and the architectural notes in CLAUDE.md that landed
  after the spec's drafting date, and check the spec against each. Registries,
  lints, auth/tenancy boundaries and shared-contract layers are the ones that
  most often invalidate a draft.
- Whether any of it is **already partly built** — a half-shipped feature is
  a different job from a clean build.

Then add a `## Clarifications` section holding a dated `### Session` heading
marked as a reconciliation with shipped work, placed immediately before
`## User Scenarios`. If the repo has reconciled a spec before, match that
precedent; otherwise use this structure:

- A "what shipped underneath" table: change → where (file:line) → effect.
- A ruling per drift, naming the requirement and saying which design
  governs. **Amend superseded clauses in place and mark them; delete
  nothing.** Requirements not addressed keep their original force.
- A "dependencies still verified good" list, with the date and the default
  branch SHA the verification was done at — so the next reader knows exactly
  how stale this reconciliation itself is.

### Stop for the user

Reconciliation is allowed to make **editorial** rulings unattended (this
event now exists, so it is the primary path; that string now lives in the
registry). It is **not** allowed to decide architecture. If a drift opens a
question of the "how does this coexist with an ADR" kind, mark it **OPEN**,
state the options and their blast radii, and **stop and ask the user**, even
in an otherwise unattended run.

This is a deliberate exception to "Step 0.5 is the ONLY interactive gate":
Step 0.5 gates *what* is built, this gates *a decision the pipeline is not
entitled to make*. Auto-accepting an architectural answer in Step 2 is
exactly the failure this step exists to prevent. In a truly headless run
where nobody can answer, **stop** — do not proceed to Step 2 with an OPEN
architectural drift.

## Step 2 — Clarify (auto-accept)

Invoke `speckit-clarify` with the instruction: *"The user is not available
interactively: for every clarification question, choose and accept the
recommended/most-defensible option yourself and encode it into the spec."*
Record every Q→A in the spec's `## Clarifications` session block.

## Step 3 — Plan

Invoke `speckit-plan`. Before writing the plan, ground it in the real code:
delegate a read-only Explore subagent to verify every file/line anchor the
prompt cites and map the actual seams (this prevents scope errors from
research claims that get implementation details subtly wrong). Produce
plan.md, research.md, data-model.md, contracts/, quickstart.md; update the
CLAUDE.md `<!-- SPECKIT START/END -->` plan pointer.

## Step 4 — Tasks

Invoke `speckit-tasks`. All checkboxes start `[ ]` (unchecked). Include test
tasks wherever the spec's FRs demand test-backed verification, and wherever
the project's constitution (`.specify/memory/constitution.md`) requires them.

## Step 5 — Analyze (auto-remediate)

Invoke `speckit-analyze` with: *"The user is not available: after reporting
findings, apply every suggested remediation yourself."* If it runs as a
background fork, WAIT for its completion notification before proceeding.

## Step 6 — Implement (sonnet subagent)

Spawn a **general-purpose subagent with `model: sonnet`** that invokes the
`speckit-implement` skill and executes ALL tasks in tasks.md to completion.
Its prompt must include:
- the spec directory and branch, and pointers to all design artifacts;
- **the project profile from Step 0.0** — the test commands and the repo's
  hard invariants verbatim, plus the feature's own invariants from
  plan.md/spec.md;
- instruction to use parallel subagents for `[P]` tasks, keep sequential
  dependencies, and mark each checkbox `[X]` as completed;
- actually file any `gh issue create` tasks and record issue numbers;
- run every checkpoint's test command, then `<testAll>`, and make it green
  (record pre-existing baseline failures rather than chasing them);
- run tests in the **foreground** — a backgrounded suite stalls the subagent,
  and after a resume its orphaned children never re-invoke it;
- do NOT commit/push — the parent session owns git;
- report per-task status, test counts, deviations, and concerns.

WAIT for its completion notification. If it pauses with work still running,
wait for the follow-up notification rather than duplicating its work.

## Step 7 — Review & fix (opus subagent)

Spawn a **general-purpose subagent with `model: opus`** to run
`/code-review xhigh --fix` on the working tree (if the code-review skill is
unavailable in its context, it should perform an equivalent adversarial
review: hunt real defects in the diff vs the spec's invariants, verify each
finding against the code, fix confirmed ones, and re-run the affected tests).
It must not commit or push. WAIT for its report; re-run `<testAll>` if it
changed anything.

## Step 8 — Commit, push, PR

1. Confirm working tree contains only intended changes (`git status`,
   `git diff --stat`).
2. Run `<ciLocal>` if the project has one (as pre-flight; note in the PR that
   the full run + remote CI green are required before merge — do NOT merge in
   this flow). Skip this step entirely if `ciLocal` is null.
3. Commit everything on the feature branch using `<commitFormat>`, including
   `Closes #N` for every issue the prompt says to close, plus
   `<commitTrailer>`.
4. `git push -u origin <branch>` and `gh pr create` with a body covering:
   what/why, design summary, test evidence (counts), deferred follow-up
   issues filed, and any pre-production gates that still apply (e.g. runs
   that need live credentials or a deployed stack).
5. Report the PR URL, then continue to Step 9 before calling the run done.

## Step 9 — Address issues filed during the run (opus subagents)

Before declaring the run complete, sweep the GitHub issues that Steps 6–7
filed (the implement agent's deferred-work issues and any review residuals
appended to them) and fix the ones that are fixable now:

1. Collect the issue numbers recorded in tasks.md / the subagent reports.
2. Triage each: **addressable in-session** (pure code/test work in this repo)
   vs **genuinely blocked** (needs credentials, a running stack, a product
   decision, or another repo). Leave blocked ones open and say why.
3. **One batch, one gate — never one PR per issue.** Create a single
   integration branch `issue-sweep/<spec-NNN>` off the latest default branch.
   For each addressable issue, spawn a **general-purpose subagent with
   `model: opus` and `isolation: worktree`** (parallel across issues — they
   may touch overlapping files, so isolated worktrees are required). Each
   agent must: read the issue *including comments*, implement with tests per
   the project's conventions, and validate with **`<testTargeted>` only** —
   the suites its change touches, plus any project-specific self-check for a
   subsystem it touched — **NOT `<testAll>`, NOT `<ciLocal>`** (the batch
   pays those once, below). It commits its work as one or more clean commits
   whose message body carries `Closes #N` + `<commitTrailer>`, pushes its own
   short-lived branch, and reports — **never merges, never opens a PR, never
   files new issues** (out-of-scope discoveries go in its report; this pass
   does not recurse — issues spawned by fixes wait for the next run).
4. **Integrate**: merge/cherry-pick each agent's commits onto the
   integration branch in dependency order, resolving overlaps once (agents
   sharing a seam should be told about each other up front, as with any
   coordinated seam). Delete the per-agent branches.
5. **Pay the test tax exactly once, in parallel**: on the integrated branch
   run `<testParallel>` and `<ciLocal>` **while** the branch's single PR runs
   remote CI concurrently. Open ONE PR titled `fix: issue sweep for spec NNN`
   whose body lists every issue with its own `Closes #N` line (the forge
   closes all of them on merge) and summarizes each fix in its own section.
6. Merge that one PR when local + remote are both green (merge-commit or
   squash per repo convention; if one fix in the batch turns out bad, drop
   its commits from the integration branch rather than blocking the batch —
   its issue stays open with the reason noted). Watch the resulting run on
   the default branch.
7. Only then report the run done: main PR + sweep PR merged, issues closed,
   anything left open with its blocker named.

Rationale (measured on a real sweep): three sequential fix PRs cost ~3× the
remote CI job (~25 min each), several serial full local suites (~38 min each
where the parallel runner did it in ~11), and a rebase + full re-CI cycle per
successor PR. Batching pays each of those once; per-issue commits keep
bisectability and per-issue attribution.

## Ground rules (apply throughout)

- **Test economy.** Iterate with `<testTargeted>`; run `<testAll>` only at
  defined gates (end of implement, the review pass if it changed production
  code, once per integration batch) — and when you do, use `<testParallel>`
  if the runner has a parallel mode. Start long local runs and remote CI
  concurrently, not one after the other.
- **The project profile wins over this file's examples.** Every command,
  branch name and invariant comes from Step 0.0. If something here conflicts
  with the repo's CLAUDE.md, the repo is right — and say so in the report.
- **Follow the repo's hard invariants absolutely** — the ones you captured as
  `invariants` in Step 0.0, and pass them to every subagent you spawn.
  Deferred work becomes tracked issues, never inline TODOs.
- **Notion is bookkeeping, never a gate.** A queue makes detection and status
  legible; the specs in the repo are what matter. If no queue is configured
  or the MCP is missing, fall back to repo-file detection; if a queue write
  fails, report it and continue.
- Never mark a task done that wasn't done; report failures verbatim.
- If a stage is genuinely blocked, stop and name the blocker plainly instead
  of improvising around it.

## Known gotchas

- **Gitignored skill directory.** If a repo ignores `.claude/`, a plain
  `git add -A` silently skips a project-local copy of this skill — force-add
  it (`git add -f .claude/skills/speckit-next/SKILL.md`) or others never
  receive it.
- **A numeric argument is ambiguous** where prompt numbers and spec-directory
  numbers drift apart by a non-constant offset. Step 0 resolves it: check
  `<specsDir>/` first, confirm at Step 0.5 when both readings are live.
- **Drafted-ahead specs look "done" to a directory-existence check.**
  Detection must test for `plan.md`, and Step 1.5 exists because such a draft
  describes the codebase as it was on its drafting date, not today.
- **Subagents should run tests in the foreground.** Background test runs stall
  the subagent, and after a resume its orphaned children never re-invoke it.
- **Don't invent a parallel test flag.** If the runner has no parallel mode,
  run it serially and say so — a wrong flag fails the gate at the worst time.
