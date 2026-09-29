#!/bin/bash
# remo installer.
#
# Two channels, because remo's pre-releases deliberately never reach PyPI
# (constitution principle IX):
#   stable      -> PyPI, resolved by uv/pipx
#   pre-release -> the wheel attached to a GitHub pre-release, by direct URL
#
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/get2knowio/remo/main/install.sh | bash
#   curl -fsSL .../install.sh | bash -s -- --version 4.3.6
#   curl -fsSL .../install.sh | bash -s -- --prerelease
#   curl -fsSL .../install.sh | bash -s -- --prerelease 4.4.0rc3

set -e

PACKAGE="remo-cli"
# Env overrides mirror try-hola/hola's cli-install.sh, so the two installers
# answer to the same shapes. No REMO_INSTALL_DIR counterpart: uv and pipx own
# where a tool lands, and second-guessing them would only break `uv tool list`.
REPO="${REMO_REPO_SLUG:-get2knowio/remo}"

# PEP 440 pre-release/dev forms. Kept identical to the version validation in
# .github/workflows/dev-build.yml, which decides which versions can exist as a
# GitHub pre-release at all; tests/unit/test_install_script.py pins the two
# together so neither can drift from the other.
PRERELEASE_RE='^[0-9]+\.[0-9]+\.[0-9]+((a|b|rc)[0-9]+|\.dev[0-9]+)$'
OLD_INSTALL_DIR="${HOME}/.remo"
OLD_SYMLINK="${HOME}/.local/bin/remo"
CONFIG_DIR="${REMO_HOME:-${XDG_CONFIG_HOME:-$HOME/.config}/remo}"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
BLUE='\033[0;34m'
BOLD='\033[1m'
NC='\033[0m'

print_error()   { echo -e "${RED}Error:${NC} $1" >&2; }
print_success() { echo -e "${GREEN}$1${NC}"; }
print_info()    { echo -e "${BLUE}$1${NC}"; }
print_warning() { echo -e "${YELLOW}$1${NC}"; }

# Parse arguments. Environment variables supply the defaults; an explicit flag
# always wins over them.
VERSION="${REMO_VERSION:-}"
PRE_RELEASE="${REMO_PRERELEASE:-false}"
DRY_RUN=false

while [[ $# -gt 0 ]]; do
    case "$1" in
        --version)
            if [ -z "${2:-}" ]; then
                print_error "--version needs a value (e.g. --version 4.3.6)"
                exit 1
            fi
            VERSION="$2"
            shift 2
            ;;
        # Both spellings: `--prerelease` matches try-hola/hola's cli-install.sh
        # and uv's own flag, `--pre-release` is what this script shipped with.
        --prerelease|--pre-release)
            PRE_RELEASE=true
            # Optional inline version: `--prerelease 4.4.0rc3`. Bare
            # `--prerelease` asks GitHub for the newest one.
            if [ -n "${2:-}" ] && [[ "$2" != -* ]]; then
                VERSION="$2"
                shift 2
            else
                shift
            fi
            ;;
        --dry-run)
            DRY_RUN=true
            shift
            ;;
        --help|-h)
            cat << 'EOF'
remo installer — stable from PyPI, pre-releases from GitHub releases

USAGE:
    curl -fsSL https://raw.githubusercontent.com/get2knowio/remo/main/install.sh | bash
    curl -fsSL .../install.sh | bash -s -- [OPTIONS]

OPTIONS:
    --version <version>     Install a specific version. A final version
                            (4.3.6) comes from PyPI; a pre-release version
                            (4.4.0rc3) comes from its GitHub pre-release.
    --prerelease [<ver>]    Install a pre-release. With no version, the newest
                            GitHub pre-release is used. `--pre-release` is
                            accepted as well.
    --dry-run               Print the install command without running it.
    --help                  Show this help message

ENVIRONMENT:
    REMO_VERSION            Same as --version. A flag overrides it.
    REMO_PRERELEASE         Set to "true" for the same effect as --prerelease.
    REMO_REPO_SLUG          owner/repo to install from (default: get2knowio/remo)

remo's pre-releases are never published to PyPI, so they install from the wheel
attached to their GitHub pre-release instead. Note that uv's own
`--prerelease allow` does not help: it widens which PyPI versions are
acceptable, not where uv looks, so it still lands on the latest stable.

Installs with uv, or pipx if that is what you already have. Plain `pip` is not
used: on distributions that mark their Python externally-managed (PEP 668,
e.g. Ubuntu 24.04) it refuses to install anything.

EXAMPLES:
    # Latest stable
    curl -fsSL https://raw.githubusercontent.com/get2knowio/remo/main/install.sh | bash

    # A specific stable version
    curl -fsSL .../install.sh | bash -s -- --version 4.3.6

    # Newest pre-release
    curl -fsSL .../install.sh | bash -s -- --prerelease

    # A specific pre-release
    curl -fsSL .../install.sh | bash -s -- --prerelease 4.4.0rc3
EOF
            exit 0
            ;;
        *)
            print_error "Unknown option: $1"
            echo "  Run with --help to see the available options." >&2
            exit 1
            ;;
    esac
done

# `--prerelease 4.3.6` is a contradiction, and silently installing the stable
# release instead is exactly the failure this rewrite exists to remove.
if [ "${PRE_RELEASE}" = true ] && [ -n "${VERSION}" ] && ! [[ "${VERSION}" =~ ${PRERELEASE_RE} ]]; then
    print_error "--prerelease was given the final version '${VERSION}'."
    echo "  Pre-release versions look like 4.4.0rc3, 4.4.0b2 or 4.4.0.dev5." >&2
    echo "  For a final version, drop --prerelease: --version ${VERSION}" >&2
    exit 1
fi

# Native Windows. `.sh` already turns most of these away, but Git Bash / MSYS2
# runs this script quite happily and would install a *Windows* Python, so the
# failure would land later, after uv had already done its work. remo needs
# fcntl, which Windows Python has no equivalent of, and it drives Ansible,
# which does not support Windows as a control node. See src/remo_cli/core/
# platform.py for the same refusal at CLI startup.
case "$(uname -s 2>/dev/null || echo unknown)" in
    MINGW*|MSYS*|CYGWIN*)
        print_error "remo requires Linux or macOS and cannot run natively on Windows."
        echo "" >&2
        echo "  You appear to be in Git Bash, MSYS2 or Cygwin. Installing here would" >&2
        echo "  succeed and then fail on the first command." >&2
        echo "" >&2
        echo "  Use WSL2 instead, and run this installer inside the Linux distribution:" >&2
        echo "" >&2
        echo "      wsl --install -d Ubuntu" >&2
        echo "" >&2
        echo "  More on WSL2: https://learn.microsoft.com/windows/wsl/install" >&2
        exit 1
        ;;
esac

# Pick the tool that will do the install.
#
# uv is preferred and bootstrapped when absent. pipx is accepted when it is
# already there, so someone who has deliberately standardised on it does not
# get a second tool manager installed behind their back. Plain `pip` is
# deliberately absent: both of these install a CLI into its own managed
# environment, whereas `pip install` targets whatever Python is on PATH, and on
# a PEP 668 distribution (Ubuntu 24.04, Debian 12, Fedora 38+) that refuses
# outright with `error: externally-managed-environment`.
INSTALLER=""
choose_installer() {
    if command -v uv &>/dev/null; then
        INSTALLER="uv"
        print_info "Found uv: $(uv --version)"
        return
    fi

    if command -v pipx &>/dev/null; then
        INSTALLER="pipx"
        print_info "Found pipx: $(pipx --version). Using it rather than installing uv."
        return
    fi

    INSTALLER="uv"
    # --dry-run must not change the machine, so report the intent instead.
    if [ "${DRY_RUN}" = true ]; then
        print_info "Neither uv nor pipx found; would install uv."
        return
    fi
    install_uv
}

# Install uv if not present
install_uv() {
    print_info "Installing uv..."
    curl -LsSf https://astral.sh/uv/install.sh | sh

    # Source the env so uv is available in this session
    if [ -f "${HOME}/.local/bin/env" ]; then
        # shellcheck disable=SC1091
        . "${HOME}/.local/bin/env"
    fi

    if ! command -v uv &>/dev/null; then
        # Try adding to PATH directly
        export PATH="${HOME}/.local/bin:${PATH}"
    fi

    if ! command -v uv &>/dev/null; then
        print_error "uv installation succeeded but 'uv' not found in PATH."
        echo "  Try opening a new terminal and re-running the installer."
        exit 1
    fi

    print_success "uv installed successfully."
}

# Clean up old git-based installation
cleanup_old_install() {
    local found_old=false

    if [ -d "${OLD_INSTALL_DIR}" ] && [ -d "${OLD_INSTALL_DIR}/.git" ]; then
        found_old=true
        print_warning "Detected old git-based remo installation at ${OLD_INSTALL_DIR}"
    fi

    if [ -L "${OLD_SYMLINK}" ]; then
        local target
        target=$(readlink -f "${OLD_SYMLINK}" 2>/dev/null || true)
        if [[ "${target}" == "${OLD_INSTALL_DIR}"* ]]; then
            found_old=true
            print_info "Removing old symlink ${OLD_SYMLINK}..."
            rm -f "${OLD_SYMLINK}"
        fi
    fi

    if [ "${found_old}" = true ] && [ -d "${OLD_INSTALL_DIR}" ]; then
        echo ""
        read -r -p "  Remove old git-based installation at ${OLD_INSTALL_DIR}? [Y/n] " answer
        case "${answer:-Y}" in
            [Yy]|"")
                rm -rf "${OLD_INSTALL_DIR}"
                print_success "Removed ${OLD_INSTALL_DIR}"
                ;;
            *)
                print_info "Keeping ${OLD_INSTALL_DIR} — you can remove it later with:"
                echo "  rm -rf ${OLD_INSTALL_DIR}"
                ;;
        esac
    fi
}

# Newest GitHub pre-release, as a bare version (`rc-4.4.0rc3` -> `4.4.0rc3`).
#
# /releases/latest deliberately skips pre-releases, so the full list is the only
# option. Unauthenticated: 60 requests/hour per IP, and no `gh` login to arrange
# on a fresh machine.
resolve_latest_prerelease() {
    # No JSON parser needed, so nothing here can be missing on a fresh box:
    # /releases is newest-first, and an `rc-` tag is only ever created by
    # dev-build.yml's prerelease job, so the first `rc-*` tag_name IS the newest
    # pre-release — the `prerelease` boolean would be redundant. Same grep/sed
    # shape as try-hola/hola's cli-install.sh.
    local tag
    tag=$(curl -fsSL "https://api.github.com/repos/${REPO}/releases?per_page=20" 2>/dev/null \
        | grep '"tag_name"' \
        | sed -E 's/.*"tag_name": *"([^"]+)".*/\1/' \
        | grep -E '^rc-' \
        | head -1) || true

    if [ -z "${tag:-}" ]; then
        # Deliberately NOT hola's warn-and-fall-back-to-stable: installing the
        # stable release when a pre-release was asked for is the exact silent
        # substitution this rewrite exists to remove.
        print_error "Could not determine the newest pre-release from GitHub."
        echo "  Check your connection, or name the version: --prerelease 4.4.0rc3" >&2
        return 1
    fi

    printf '%s' "${tag#rc-}"
}

# The PEP 508 requirement to install.
#
# A pre-release resolves to a direct wheel URL because those versions are never
# on PyPI (constitution IX). This is what `uv tool install --prerelease allow`
# got wrong: it resolves against PyPI, whose newest pre-release predates that
# rule, so it silently installed the current stable and reported success.
install_spec() {
    if [ -n "${VERSION}" ] && [[ "${VERSION}" =~ ${PRERELEASE_RE} ]]; then
        printf '%s @ https://github.com/%s/releases/download/rc-%s/remo_cli-%s-py3-none-any.whl' \
            "${PACKAGE}" "${REPO}" "${VERSION}" "${VERSION}"
    elif [ -n "${VERSION}" ]; then
        printf '%s==%s' "${PACKAGE}" "${VERSION}"
    else
        printf '%s' "${PACKAGE}"
    fi
}

# Install remo-cli
install_remo() {
    local spec
    spec="$(install_spec)"

    local cmd=()
    case "${INSTALLER}" in
        uv)   cmd=(uv tool install) ;;
        pipx) cmd=(pipx install) ;;
        *)    print_error "No installer selected (internal error)."; exit 1 ;;
    esac

    # --force only when a version was asked for: switching between versions
    # needs it, while a bare re-run stays the no-op it is today.
    [ -n "${VERSION}" ] && cmd+=(--force)
    cmd+=("${spec}")

    if [ "${DRY_RUN}" = true ]; then
        printf '%s "%s"\n' "${cmd[*]:0:${#cmd[@]}-1}" "${spec}"
        return
    fi

    echo ""
    print_info "Installing ${PACKAGE}${VERSION:+ ${VERSION}}..."

    if ! "${cmd[@]}"; then
        print_error "Installation failed."
        printf '  Try running manually: %s "%s"\n' "${cmd[*]:0:${#cmd[@]}-1}" "${spec}"
        exit 1
    fi
}

# Offer to install shell completion.
#
# Python wheels have no post-install hook, so this installer is the closest
# thing to one. It still asks: writing to a user's rc file is not something to
# do silently just because they ran an installer.
setup_completion() {
    command -v remo &>/dev/null || return 0

    # $SHELL is the login shell — the right answer for "which rc do I edit".
    # The parent process here is always bash (curl | bash), so inspecting it
    # would tell every fish user they run bash.
    local shell_name
    shell_name="$(basename "${SHELL:-}")"
    case "${shell_name}" in
        bash|zsh|fish) ;;
        *) return 0 ;;
    esac

    # fish needs no rc edit — its completions directory is a drop-in.
    if [ "${shell_name}" = "fish" ]; then
        remo completion install fish >/dev/null 2>&1 &&
            print_success "Installed ${shell_name} tab completion."
        echo ""
        return 0
    fi

    # Non-interactive (piped installer with no TTY): write the script but
    # leave the rc file alone, and say what is left to do.
    if [ ! -t 0 ]; then
        remo completion install "${shell_name}" >/dev/null 2>&1 || return 0
        print_info "Shell completion script written. To enable it, run:"
        echo "    remo completion install ${shell_name} --yes"
        echo ""
        return 0
    fi

    local answer
    read -r -p "  Install ${shell_name} tab completion? (adds one line to your rc) [Y/n] " answer
    if [ "${answer}" = "n" ] || [ "${answer}" = "N" ]; then
        print_info "Skipped. Enable it later with: remo completion install"
        echo ""
        return 0
    fi

    if remo completion install "${shell_name}" --yes; then
        print_success "Installed ${shell_name} tab completion."
    fi
    echo ""
}

# Main
main() {
    if [ "${DRY_RUN}" != true ]; then
        echo ""
        print_info "remo installer"
        echo ""
    fi

    if [ "${PRE_RELEASE}" = true ] && [ -z "${VERSION}" ]; then
        VERSION="$(resolve_latest_prerelease)" || exit 1
        [ "${DRY_RUN}" = true ] || print_info "Newest pre-release: ${VERSION}"
    fi

    # --dry-run prints the command and stops: no uv bootstrap, no prompt from
    # cleanup_old_install, nothing written. The informational lines go to stderr
    # so stdout is just the command, usable in `$(...)`.
    if [ "${DRY_RUN}" = true ]; then
        choose_installer >&2
        install_remo
        exit 0
    fi

    choose_installer
    cleanup_old_install
    install_remo

    # Success
    echo ""
    print_success "=============================================="
    print_success "  remo installed successfully!"
    print_success "=============================================="
    echo ""

    if [ -d "${CONFIG_DIR}" ]; then
        echo "  Your existing config in ${CONFIG_DIR}/ was preserved."
        echo ""
    fi

    setup_completion

    echo "  Get started:"
    echo "    remo --version"
    echo "    remo --help"
    echo ""

    # Check if remo is in PATH
    if ! command -v remo &>/dev/null; then
        print_warning "Note: 'remo' is not in your PATH yet."
        echo "  You may need to open a new terminal or add ~/.local/bin to your PATH:"
        echo "  export PATH=\"\$HOME/.local/bin:\$PATH\""
        echo ""
    fi
}

main "$@"
