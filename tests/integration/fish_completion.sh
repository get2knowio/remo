#!/usr/bin/env bash
# Execute remo's fish completion in a real fish shell.
#
# Every other completion test in this repo asserts on the *text* of the
# generated script. That is not enough: the bug this exists to catch
# (a2542e9, and the near-miss in #112) was a script that read fine and blew up
# with a screenful of `string split: missing argument` the moment fish ran it.
# Nothing short of running fish finds that class of defect.
#
# Usage:
#   ./tests/integration/fish_completion.sh
#
# Requires: fish, and a `remo` on PATH (the completion script shells out to it
# by name, so `uv run remo` is not sufficient).
set -uo pipefail

red()   { printf '\033[0;31m%s\033[0m\n' "$*" >&2; }
green() { printf '\033[0;32m%s\033[0m\n' "$*"; }
blue()  { printf '\033[0;34m%s\033[0m\n' "$*"; }

FAILURES=0

# --- Preflight -------------------------------------------------------------
if ! command -v fish &>/dev/null; then
    red "fish not found. Install it: apt-get install fish / brew install fish"
    exit 1
fi
if ! command -v remo &>/dev/null; then
    red "remo not found on PATH."
    red "The completion script invokes 'remo' by name, so an activated venv"
    red "or 'uv tool install .' is required — 'uv run remo' will not do."
    exit 1
fi

blue "fish:  $(fish --version)"
blue "remo:  $(remo --version)"

# fish loads a command's completion file only when it can resolve the command
# itself. If `remo` is missing from *fish's* PATH, the whole script is ignored
# in silence and fish falls back to listing files — the exact symptom this
# suite exists to catch, and indistinguishable from a broken script unless you
# check for it explicitly. Rather than trust PATH to survive into the fish
# subprocess, put remo's directory there deliberately.
REMO_BIN_DIR="$(cd "$(dirname "$(command -v remo)")" && pwd)"
blue "remo dir: $REMO_BIN_DIR"

# Run one fish command with remo guaranteed on PATH.
fish_run() {
    fish -c "set -x PATH '$REMO_BIN_DIR' \$PATH; $1"
}

if ! fish_run 'type -q remo'; then
    red "FAIL: fish cannot resolve 'remo' even with $REMO_BIN_DIR on PATH."
    red "  bash PATH: $PATH"
    red "  fish PATH: $(fish_run 'echo $PATH')"
    exit 1
fi

# Informational: does fish inherit enough PATH to find remo on its own? CI has
# been observed failing here while bash resolved remo fine, and forcing PATH
# above papers over that. Report it rather than hide it — a "no" is the whole
# explanation for a file-listing bug report, so it belongs in the log.
if fish -c 'type -q remo'; then
    blue "note: fish resolves remo from the inherited PATH."
else
    blue "note: fish does NOT resolve remo from the inherited PATH (forced above)."
    blue "      inherited fish PATH: $(fish -c 'echo $PATH' | tr ' ' ':')"
fi

# --- Isolated HOME ---------------------------------------------------------
# Never write into the caller's real ~/.config/fish/completions.
FISH_HOME="$(mktemp -d)"
trap 'rm -rf "$FISH_HOME"' EXIT
export HOME="$FISH_HOME"

blue "Installing completion into $FISH_HOME ..."
if ! remo completion install fish; then
    red "FAIL: 'remo completion install fish' exited non-zero"
    exit 1
fi

# Ask fish where it actually reads completions from, rather than assuming.
# remo and fish must agree on this: fish honours $XDG_CONFIG_HOME, and a remo
# that hardcodes ~/.config writes somewhere fish never looks — which fish
# reports only by silently completing filenames. That exact mismatch is what
# this job caught on its first run.
FISH_COMPLETIONS_DIR="$(fish_run 'echo $__fish_config_dir')/completions"
SCRIPT="$FISH_COMPLETIONS_DIR/remo.fish"
if [[ ! -f "$SCRIPT" ]]; then
    red "FAIL: no completion script where fish reads them."
    red "  fish reads:  $FISH_COMPLETIONS_DIR"
    red "  remo wrote:  $(find "$FISH_HOME" "${XDG_CONFIG_HOME:-$FISH_HOME}" \
                          -name 'remo.fish' 2>/dev/null | head -3 | tr '\n' ' ')"
    red "  XDG_CONFIG_HOME=${XDG_CONFIG_HOME:-<unset>}  HOME=$HOME"
    exit 1
fi
green "PASS: completion script installed where fish reads it ($FISH_COMPLETIONS_DIR)"

# --- Helper ----------------------------------------------------------------
# Runs one completion in real fish and checks three things at once:
#   1. fish exits 0
#   2. nothing is written to stderr  <-- the actual regression guard
#   3. every expected candidate appears (when any are given)
#
# (2) is the important one. The original bug did not fail the shell; it
# scrolled errors past the user while still "working", which is exactly the
# shape a text-only assertion sails straight through.
check() {
    local desc="$1" cmdline="$2"; shift 2
    local expected=("$@")
    local errfile out rc
    errfile="$(mktemp)"

    out="$(fish_run "complete -C '$cmdline'" 2>"$errfile")"
    rc=$?

    local problems=()
    [[ $rc -ne 0 ]] && problems+=("fish exited $rc")

    if [[ -s "$errfile" ]]; then
        problems+=("wrote to stderr:")
        problems+=("$(sed 's/^/      | /' "$errfile" | head -20)")
    fi

    local want
    for want in "${expected[@]}"; do
        grep -qE "^${want}(\s|\$)" <<<"$out" || problems+=("missing candidate: ${want}")
    done

    rm -f "$errfile"

    if [[ ${#problems[@]} -eq 0 ]]; then
        green "PASS: ${desc}"
        return 0
    fi

    red "FAIL: ${desc}  (complete -C '${cmdline}')"
    local p
    for p in "${problems[@]}"; do red "    ${p}"; done
    [[ -n "$out" ]] && red "    got: $(tr '\n' ' ' <<<"$out" | cut -c1-200)"
    FAILURES=$((FAILURES + 1))
    return 1
}

# --- Checks ----------------------------------------------------------------
blue "Running completions in fish ..."

# Top-level: the case the user hits first, and the one that regressed.
check "top-level subcommands" "remo " \
    add aws completion cp hetzner incus proxmox remove shell web

# One level down, through the descriptor-generated provider factory.
check "incus subcommands" "remo incus " \
    create destroy upgrade resize tag list sync info host

# `tag` is generated only when the provider supports a managed marker, so AWS
# must NOT offer it — a completion that suggests a nonexistent command is the
# same broken-remedy class of bug as a printed one.
#
# Anchored on a positive first: a bare "no tag here" assertion also passes when
# completion is broken outright (a directory listing contains no `tag` either),
# which would make this check quietly worthless exactly when it matters.
check "aws subcommands present" "remo aws " create destroy upgrade resize
aws_out="$(fish_run "complete -C 'remo aws '" 2>/dev/null)"
if grep -qE '^tag(\s|$)' <<<"$aws_out"; then
    red "FAIL: 'remo aws' offers 'tag', which AWS does not implement"
    FAILURES=$((FAILURES + 1))
else
    green "PASS: 'remo aws' correctly omits 'tag'"
fi

# No candidates match. Click emits a bare newline here (verified), and on the
# fish versions behind a2542e9 that became one empty loop iteration and a wall
# of `string split: missing argument` / `test: Missing argument at index 3`.
#
# Caveat, so nobody over-trusts this check: it does NOT reproduce that bug on
# fish 3.7, which strips the trailing empty from command substitution before
# the loop ever sees it. Reverting both hardening changes was measured against
# this suite and it still passed. What this asserts is silence on *this* fish;
# it is a regression guard for older ones, not proof the guards are load-
# bearing here. Exercising them properly would need an older fish in the
# matrix.
check "no-match input produces no errors" "remo zzzznosuchcommand"

# Same failure mode one level deeper.
check "no-match subcommand produces no errors" "remo incus zzzznosuchcommand"

# --- Result ----------------------------------------------------------------
echo ""
if [[ $FAILURES -eq 0 ]]; then
    green "All fish completion checks passed."
    exit 0
fi
red "${FAILURES} fish completion check(s) failed."
exit 1
