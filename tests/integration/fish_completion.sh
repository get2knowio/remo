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

SCRIPT="$FISH_HOME/.config/fish/completions/remo.fish"
if [[ ! -f "$SCRIPT" ]]; then
    red "FAIL: no completion script at $SCRIPT"
    exit 1
fi
green "PASS: completion script installed"

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

    out="$(fish -c "complete -C '$cmdline'" 2>"$errfile")"
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
if fish -c "complete -C 'remo aws '" 2>/dev/null | grep -qE '^tag(\s|$)'; then
    red "FAIL: 'remo aws' offers 'tag', which AWS does not implement"
    FAILURES=$((FAILURES + 1))
else
    green "PASS: 'remo aws' correctly omits 'tag'"
fi

# The original defect, reproduced directly: no candidates match, Click emits a
# bare newline, and an unguarded script turns that into a wall of
# `string split: missing argument` / `test: Missing argument at index 3`.
# No expected candidates here — an empty result is the correct answer. What is
# asserted is silence.
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
