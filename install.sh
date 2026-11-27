#!/bin/bash
# remo installer.
#
# One path for every release (spec 031): the installer reads the release's
# manifest.json from GitHub Releases, downloads the wheels it lists, verifies
# their SHA-256, and installs remo-cli (plus any plugins and the `web` extra
# you ask for) from those verified files in a single uv/pipx command. Stable
# and pre-release (rc-) releases differ only in which manifest is read.
# GitHub Releases is remo's only package channel: no remo build, stable or
# pre-release, plugin or not, is ever uploaded to PyPI (constitution principle
# IX). An install that came from PyPI switches by running this script once.
#
# Usage: see `--help`. URLs are written out in full because a shortened one
# cannot be pasted:
#   curl -fsSL https://raw.githubusercontent.com/get2knowio/remo/main/install.sh | bash
#   ...the same, with: -s -- --with ec2,web | -s -- --prerelease 5.0.0rc1 --with ec2

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

# Lists are newline-separated strings (bash 3.2 has no associative arrays).
WITH_LIST=""; WITH_FLAG=false
WITHOUT_LIST=""; WITHOUT_FLAG=false
PKG_LIST=""
URL_LIST=""

# Anything that reaches the install record or a command line matches this.
# It rules out quotes, backslashes, spaces and shell metacharacters, so the
# record needs no JSON escaping and a value cannot smuggle in a flag.
VALUE_RE='^[A-Za-z0-9._:/@+=,~%-]+$'

need_value() {
    if [ -z "${2:-}" ]; then
        print_error "$1 needs a value ($3)"
        exit 1
    fi
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --version)
            need_value "$1" "${2:-}" "e.g. --version 5.0.0"
            VERSION="$2"
            shift 2
            ;;
        # Both spellings: `--prerelease` matches try-hola/hola's cli-install.sh
        # and uv's own flag, `--pre-release` is what this script shipped with.
        --prerelease|--pre-release)
            PRE_RELEASE=true
            # Optional inline version: `--prerelease 5.0.0rc1`. Bare
            # `--prerelease` asks GitHub for the newest one.
            if [ -n "${2:-}" ] && [[ "$2" != -* ]]; then
                VERSION="$2"
                shift 2
            else
                shift
            fi
            ;;
        --with)
            need_value "$1" "${2:-}" "e.g. --with ec2,web"
            WITH_LIST="${WITH_LIST}
$(printf '%s' "$2" | tr ',' '\n')"
            WITH_FLAG=true
            shift 2
            ;;
        --without)
            need_value "$1" "${2:-}" "e.g. --without web"
            WITHOUT_LIST="${WITHOUT_LIST}
$(printf '%s' "$2" | tr ',' '\n')"
            WITHOUT_FLAG=true
            shift 2
            ;;
        --with-pkg)
            need_value "$1" "${2:-}" "e.g. --with-pkg somepkg==1.2"
            PKG_LIST="${PKG_LIST}
$2"
            shift 2
            ;;
        --with-url)
            need_value "$1" "${2:-}" "an https:// wheel URL"
            URL_LIST="${URL_LIST}
$2"
            shift 2
            ;;
        --dry-run)
            DRY_RUN=true
            shift
            ;;
        --help|-h)
            cat << 'EOF'
remo installer: every release (stable and pre-release) from GitHub Releases

USAGE:
    curl -fsSL https://raw.githubusercontent.com/get2knowio/remo/main/install.sh \
      | bash

    # Options need `--`, which is how arguments reach a script piped into bash.
    curl -fsSL https://raw.githubusercontent.com/get2knowio/remo/main/install.sh \
      | bash -s -- [OPTIONS]

OPTIONS:
    --version <version>     Install a specific release. A pre-release version
                            (5.0.0rc1) implies --prerelease.
    --prerelease [<ver>]    Install a pre-release. With no version, the newest
                            GitHub pre-release is used. `--pre-release` is
                            accepted as well.
    --with <list>           Also install these, comma-separated and repeatable:
                            `web` (the browser console) and/or any provider
                            plugin the release ships (e.g. ec2, hetzner). Names
                            come from the release's manifest.
    --without <list>        Remove these from what is already recorded.
    --with-pkg <spec>       Also install a third-party package from an index
                            (repeatable), e.g. --with-pkg some-plugin==1.2.
    --with-url <url>        Also install a third-party wheel from an https URL
                            (repeatable).
    --dry-run               Print the install command without running it.
    --help                  Show this help message

ENVIRONMENT:
    REMO_VERSION            Same as --version. A flag overrides it.
    REMO_PRERELEASE         Set to "true" for the same effect as --prerelease.
    REMO_WITH               Same as --with. A flag overrides it.
    REMO_WITHOUT            Same as --without. A flag overrides it.
    REMO_REPO_SLUG          owner/repo to install from (default: get2knowio/remo)
    REMO_HOME               Where the install record (install.json) lives
                            (default: ~/.config/remo)

Every release carries its wheels and a manifest.json (with SHA-256 digests) as
GitHub release assets. This script downloads the wheels, verifies each digest,
and installs from those verified files. GitHub Releases is the only channel:
remo is never published to PyPI (the last versions there stay frozen). A PyPI
install of remo switches over by running this script once. Re-running is additive: what a previous run installed (see
install.json in the config directory) is kept unless you pass --without.

Installs with uv, or pipx if that is what you already have. Plain `pip` is not
used: on distributions that mark their Python externally-managed (PEP 668,
e.g. Ubuntu 24.04) it refuses to install anything.

EXAMPLES:
    # Each block below is self-contained: select it and paste it. The URL is
    # written out every time on purpose, because a shortened URL cannot be
    # pasted and a shared $URL variable breaks if you copy only one block.

    # Latest stable
    curl -fsSL https://raw.githubusercontent.com/get2knowio/remo/main/install.sh \
      | bash

    # Latest stable, with the EC2 plugin and the web console
    curl -fsSL https://raw.githubusercontent.com/get2knowio/remo/main/install.sh \
      | bash -s -- --with ec2,web

    # A specific stable version
    curl -fsSL https://raw.githubusercontent.com/get2knowio/remo/main/install.sh \
      | bash -s -- --version 5.0.0

    # Newest pre-release
    curl -fsSL https://raw.githubusercontent.com/get2knowio/remo/main/install.sh \
      | bash -s -- --prerelease

    # A specific pre-release, with the EC2 plugin
    curl -fsSL https://raw.githubusercontent.com/get2knowio/remo/main/install.sh \
      | bash -s -- --prerelease 5.0.0rc1 --with ec2

    # See what it would run, without running it
    curl -fsSL https://raw.githubusercontent.com/get2knowio/remo/main/install.sh \
      | bash -s -- --prerelease --dry-run
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

# A flag beats its env twin.
if [ "${WITH_FLAG}" = false ] && [ -n "${REMO_WITH:-}" ]; then
    WITH_LIST="$(printf '%s' "${REMO_WITH}" | tr ',' '\n')"
fi
if [ "${WITHOUT_FLAG}" = false ] && [ -n "${REMO_WITHOUT:-}" ]; then
    WITHOUT_LIST="$(printf '%s' "${REMO_WITHOUT}" | tr ',' '\n')"
fi

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

# ---------------------------------------------------------------------------
# List helpers. Lists are newline-separated strings, always kept sorted and
# de-duplicated, because bash 3.2 (macOS) has neither mapfile nor `declare -A`.
# ---------------------------------------------------------------------------
list_norm()   { printf '%s\n' "$1" | grep -v '^$' | sort -u || true; }
list_union()  { list_norm "$1
$2"; }
list_has()    { printf '%s\n' "$1" | grep -qxF -- "$2"; }
list_remove() { list_norm "$(printf '%s\n' "$1" | grep -vxF -- "$2" || true)"; }
list_count()  { printf '%s\n' "$1" | grep -vc '^$' || true; }

json_list() {
    printf '%s\n' "$1" | grep -v '^$' | awk 'BEGIN{printf "["} {printf "%s\"%s\"", (c++ ? "," : ""), $0} END{print "]"}'
}

# ---------------------------------------------------------------------------
# Scratch space: the manifest and the verified wheels live here, and are what
# gets installed ("what is checked is what is installed").
# ---------------------------------------------------------------------------
WORKDIR=""
make_workdir() {
    WORKDIR="$(mktemp -d "${TMPDIR:-/tmp}/remo-install.XXXXXX")"
    trap 'rm -rf "${WORKDIR}"' EXIT
}

# ---------------------------------------------------------------------------
# Release lookup through the GitHub API (#290).
#
# A GitHub release exists before its assets do: release-please publishes
# vX.Y.Z at once, and release.yml attaches the wheels and manifest.json only
# after its tests and build pass, or never, if it fails. So "newest release" is
# not enough here: the answer is the newest release on the channel that
# already carries a manifest.json. core/version.py's get_latest_github_release
# applies the same rule for `remo self-update` and the update notice.
#
# Unauthenticated: 60 requests/hour per IP, and no `gh` login to arrange on a
# fresh machine. No JSON parser either, so nothing here can be missing on a
# fresh box: grep/sed/awk only, the same shape as try-hola/hola's
# cli-install.sh.
# ---------------------------------------------------------------------------
API_BODY=""

fetch_release_list() {
    # No -f: a 403 body is how GitHub says the rate limit is spent, and that
    # deserves its own message.
    API_BODY=$(curl -sSL "https://api.github.com/repos/${REPO}/releases?per_page=20" 2>/dev/null) || API_BODY=""
}

# Only an error body counts: a successful listing carries every release's
# notes, and a note that merely mentions a rate limit must not turn into a
# refusal. GitHub's error is `"message": "API rate limit exceeded for …"`.
api_rate_limited() {
    printf '%s' "${API_BODY}" | grep -qiE '"message": *"[^"]*rate limit'
}

# channel_releases CHANNEL -> "<tag> yes|no" per release on CHANNEL, newest
# first (the API's own order); "yes" when it carries a manifest.json.
#
# Stable is a `v` tag that is neither a draft nor a pre-release; prerelease an
# `rc-` tag flagged as one. Splitting on commas copes with GitHub's pretty and
# compact layouts alike, and a key is matched only at the start of a fragment:
# release notes are one JSON string whose quotes are escaped, so text in them
# can never pass for a key. draft/prerelease follow tag_name in each release
# object, so they belong to the tag most recently seen. The manifest is
# recognised by its download url, which names the tag itself
# (.../releases/download/<tag>/manifest.json), so that part does not depend on
# field order at all; a draft's assets live under an `untagged-` url.
channel_releases() {
    printf '%s\n' "${API_BODY}" | tr ',' '\n' | awk -v channel="$1" '
        function flush() {
            if (tag != "") { order[n++] = tag; dr[tag] = draft; pr[tag] = pre }
        }
        /^[ \t\[{]*"tag_name"[ \t]*:/ {
            flush()
            tag = $0
            sub(/^[^:]*:[ \t]*"/, "", tag)
            sub(/".*/, "", tag)
            draft = 0; pre = 0
            next
        }
        /^[ \t]*"draft"[ \t]*:[ \t]*true/ { draft = 1; next }
        /^[ \t]*"prerelease"[ \t]*:[ \t]*true/ { pre = 1; next }
        /^[ \t\[{]*"browser_download_url"[ \t]*:.*\/releases\/download\/[^"\/]+\/manifest\.json"/ {
            u = $0
            sub(/\/manifest\.json".*/, "", u)
            sub(/.*\//, "", u)
            has[u] = 1
            next
        }
        END {
            flush()
            for (i = 0; i < n; i++) {
                t = order[i]
                if (dr[t]) continue
                if (channel == "stable") {
                    if (pr[t] || t !~ /^v/) continue
                } else {
                    if (!pr[t] || t !~ /^rc-/) continue
                }
                print t " " (has[t] ? "yes" : "no")
            }
        }'
}

# newest_installable CHANNEL -> the newest tag on CHANNEL that carries a
# manifest.json, on stdout, or nothing. When a newer release is still being
# published, a note on stderr names it and the version installed instead.
newest_installable() {
    local listing newest chosen
    listing="$(channel_releases "$1")"
    newest="$(printf '%s\n' "${listing}" | awk 'NF { print $1; exit }')"
    chosen="$(printf '%s\n' "${listing}" | awk '$2 == "yes" { print $1; exit }')"
    if [ -z "${chosen}" ]; then
        return 0
    fi
    if [ "${newest}" != "${chosen}" ]; then
        print_warning "Note: ${newest} is the newest release but is still being published (its manifest.json is not attached yet)." >&2
        print_warning "      Installing ${chosen} instead. Run the installer again later to get ${newest}." >&2
    fi
    printf '%s' "${chosen}"
}

# The newest release on CHANNEL, installable or not (for the error messages).
newest_listed() {
    channel_releases "$1" | awk 'NF { print $1; exit }'
}

# Newest GitHub pre-release that can be installed, as a bare version
# (`rc-5.0.0rc1` -> `5.0.0rc1`). /releases/latest deliberately skips
# pre-releases, so the API list is the only option.
resolve_latest_prerelease() {
    local tag pending
    fetch_release_list
    tag="$(newest_installable prerelease)"
    if [ -n "${tag}" ]; then
        printf '%s' "${tag#rc-}"
        return 0
    fi

    pending="$(newest_listed prerelease)"
    if api_rate_limited; then
        print_error "GitHub's API rate limit is exhausted, so the newest pre-release cannot be looked up."
        echo "  Wait a while, or name the version: --prerelease 5.0.0rc1" >&2
    elif [ -n "${pending}" ]; then
        print_error "No recent pre-release carries a manifest.json yet (${pending} is still being published)."
        echo "  Try again in a few minutes, or see https://github.com/${REPO}/releases" >&2
    else
        # Deliberately NOT hola's warn-and-fall-back-to-stable: installing the
        # stable release when a pre-release was asked for is the exact silent
        # substitution this rewrite exists to remove.
        print_error "Could not determine the newest pre-release from GitHub."
        echo "  Check your connection, or name the version: --prerelease 5.0.0rc1" >&2
    fi
    return 1
}

# The default (latest stable) manifest could not be downloaded, most often
# because the newest release is still being published. Sets EXPECTED_TAG to
# the newest stable release that carries a manifest, or explains and exits.
# Never used for a version the user named: that one is installed or refused.
fallback_to_installable_stable() {
    local url="$1" tag pending
    fetch_release_list
    tag="$(newest_installable stable)"
    if [ -n "${tag}" ]; then
        EXPECTED_TAG="${tag}"
        return 0
    fi
    pending="$(newest_listed stable)"
    print_error "Could not download the manifest for the latest release: ${url}"
    if api_rate_limited; then
        echo "  GitHub's API rate limit is exhausted, so an earlier release cannot be looked up." >&2
        echo "  Wait a while, or name the version: --version 5.0.0" >&2
    elif [ -n "${pending}" ]; then
        echo "  No recent stable release carries a manifest.json yet (${pending} is still being published)." >&2
        echo "  Try again in a few minutes, or see https://github.com/${REPO}/releases" >&2
    else
        echo "  No stable release with a manifest.json was found." >&2
        echo "  Check your connection, or see https://github.com/${REPO}/releases" >&2
    fi
    exit 1
}

# ---------------------------------------------------------------------------
# The release manifest. Written by scripts/release_manifest.py in a fixed
# line-oriented layout (header line, one component per line), so grep/sed is
# enough here: see specs/031-install-path/contracts/release-manifest.md.
# ---------------------------------------------------------------------------
EXPECTED_TAG=""     # the tag we asked for; empty for "latest stable"
MANIFEST=""
MANIFEST_TAG=""
MANIFEST_CHANNEL=""

manifest_url() {
    if [ "${PRE_RELEASE}" = true ]; then
        printf 'https://github.com/%s/releases/download/%s/manifest.json' "${REPO}" "${EXPECTED_TAG}"
    elif [ -n "${EXPECTED_TAG}" ]; then
        printf 'https://github.com/%s/releases/download/%s/manifest.json' "${REPO}" "${EXPECTED_TAG}"
    else
        printf 'https://github.com/%s/releases/latest/download/manifest.json' "${REPO}"
    fi
}

# `"key":"value"` from the header line.
manifest_header() {
    head -1 "${MANIFEST}" | sed -nE "s/.*\"$1\":\"([^\"]*)\".*/\\1/p"
}

component_line() {
    grep -F "{\"name\":\"$1\"," "${MANIFEST}" | head -1 || true
}

# component_field NAME KEY
component_field() {
    component_line "$1" | sed -nE "s/.*\"$2\":\"([^\"]*)\".*/\\1/p"
}

plugin_names() {
    grep -F '"kind":"plugin"' "${MANIFEST}" | sed -nE 's/^\{"name":"([^"]*)".*/\1/p' || true
}

fetch_manifest() {
    local url what
    url="$(manifest_url)"
    MANIFEST="${WORKDIR}/manifest.json"
    if [ -n "${EXPECTED_TAG}" ]; then
        what="release ${EXPECTED_TAG}"
    else
        what="the latest release"
    fi
    local fetched=false
    if curl -fsSL "${url}" -o "${MANIFEST}" 2>/dev/null; then
        fetched=true
    elif [ -z "${EXPECTED_TAG}" ]; then
        # Latest stable only: fall back to the newest stable release that is
        # installable already (#290), and fetch that one by its tag, which
        # also brings the tag check below into play.
        fallback_to_installable_stable "${url}"
        url="$(manifest_url)"
        what="release ${EXPECTED_TAG}"
        if curl -fsSL "${url}" -o "${MANIFEST}" 2>/dev/null; then
            fetched=true
        fi
    fi
    if [ "${fetched}" != true ]; then
        print_error "Could not download the manifest for ${what}: ${url}"
        echo "  That release may not exist, or may predate the manifest-based installer." >&2
        echo "  Try a newer version, or see https://github.com/${REPO}/releases" >&2
        exit 1
    fi

    MANIFEST_TAG="$(manifest_header tag)"
    MANIFEST_CHANNEL="$(manifest_header channel)"
    if [ -z "$(component_line remo-cli)" ] || [ -z "${MANIFEST_TAG}" ]; then
        print_error "The manifest at ${url} is not in the expected format."
        exit 1
    fi
    if [ -n "${EXPECTED_TAG}" ] && [ "${MANIFEST_TAG}" != "${EXPECTED_TAG}" ]; then
        print_error "The manifest says it is for ${MANIFEST_TAG}, but ${EXPECTED_TAG} was requested."
        exit 1
    fi
    case "${MANIFEST_TAG}:${MANIFEST_CHANNEL}" in
        v*:stable|rc-*:prerelease) ;;
        *) print_error "The manifest's tag '${MANIFEST_TAG}' and channel '${MANIFEST_CHANNEL}' disagree."; exit 1 ;;
    esac
    if [ "${PRE_RELEASE}" = true ] && [ "${MANIFEST_CHANNEL}" != "prerelease" ]; then
        print_error "A pre-release was requested but ${MANIFEST_TAG} is not one."
        exit 1
    fi
    # The tag must name exactly the release the manifest describes, and the
    # remo-cli wheel must be that release: otherwise the urls (and the
    # install.sh self-update fetches) belong to a different version.
    local release cli_version want_tag
    release="$(manifest_header release)"
    cli_version="$(component_field remo-cli version)"
    case "${MANIFEST_CHANNEL}" in
        prerelease) want_tag="rc-${release}" ;;
        *) want_tag="v${release}" ;;
    esac
    if [ -z "${release}" ] || [ "${MANIFEST_TAG}" != "${want_tag}" ]; then
        print_error "The manifest's tag '${MANIFEST_TAG}' does not name its release '${release}'."
        exit 1
    fi
    if [ "${cli_version}" != "${release}" ]; then
        print_error "The manifest's remo-cli version '${cli_version}' is not its release '${release}'."
        exit 1
    fi

    # Every artifact must live under this release: a manifest that points
    # anywhere else is refused outright.
    local prefix="https://github.com/${REPO}/releases/download/${MANIFEST_TAG}/" line name url_of file sha
    while IFS= read -r line; do
        name="$(printf '%s' "${line}" | sed -nE 's/^\{"name":"([^"]*)".*/\1/p')"
        [ -n "${name}" ] || continue
        url_of="$(component_field "${name}" url)"
        file="$(component_field "${name}" file)"
        sha="$(component_field "${name}" sha256)"
        case "${url_of}" in
            "${prefix}"*) ;;
            *) print_error "The manifest lists '${name}' at ${url_of}, outside ${prefix}"; exit 1 ;;
        esac
        if ! [[ "${file}" =~ ^[A-Za-z0-9._+-]+\.whl$ ]] || [ "${url_of}" != "${prefix}${file}" ]; then
            print_error "The manifest entry for '${name}' has an unexpected file name '${file}'."
            exit 1
        fi
        if ! [[ "${sha}" =~ ^[0-9a-f]{64}$ ]]; then
            print_error "The manifest entry for '${name}' has no valid sha256."
            exit 1
        fi
    done < <(grep -F '{"name":"' "${MANIFEST}")
}

sha256_of() {
    if command -v sha256sum &>/dev/null; then
        sha256sum "$1" | awk '{print $1}'
    else
        shasum -a 256 "$1" | awk '{print $1}'
    fi
}

require_sha_tool() {
    if ! command -v sha256sum &>/dev/null && ! command -v shasum &>/dev/null; then
        print_error "Neither sha256sum nor shasum is available, so the download cannot be verified."
        echo "  Install one (GNU coreutils provides sha256sum; macOS ships shasum) and re-run." >&2
        exit 1
    fi
}

# download_and_verify NAME: leaves the verified wheel in WORKDIR.
download_and_verify() {
    local name="$1" url file want got
    url="$(component_field "${name}" url)"
    file="$(component_field "${name}" file)"
    want="$(component_field "${name}" sha256)"
    print_info "Downloading ${file}..."
    if ! curl -fsSL "${url}" -o "${WORKDIR}/${file}"; then
        print_error "Could not download ${url}"
        exit 1
    fi
    got="$(sha256_of "${WORKDIR}/${file}")"
    if [ "${got}" != "${want}" ]; then
        print_error "Checksum mismatch for ${file}: expected ${want}, got ${got}. Nothing was installed."
        exit 1
    fi
}

# Where a component's wheel is installed from: the verified local copy on a real
# run, the manifest URL on --dry-run (which downloads nothing).
component_location() {
    if [ "${DRY_RUN}" = true ]; then
        component_field "$1" url
    else
        printf 'file://%s/%s' "${WORKDIR}" "$(component_field "$1" file)"
    fi
}

# ---------------------------------------------------------------------------
# The install record: <CONFIG_DIR>/install.json (contracts/install-record.md).
# ---------------------------------------------------------------------------
RECORD="${CONFIG_DIR}/install.json"
REC_EXTRAS=""; REC_PLUGINS=""; REC_PKG=""; REC_URL=""

rec_list() {
    grep -E "^\"$1\":\\[" "${RECORD}" | head -1 \
        | sed -E 's/^"[a-z_]+":\[(.*)\],?\}?$/\1/; s/","/\
/g; s/^"//; s/"$//' | grep -v '^$' || true
}

read_record() {
    [ -f "${RECORD}" ] || return 0
    local ok=true v
    [ "$(head -1 "${RECORD}")" = '{"format":1,' ] || ok=false
    if [ "${ok}" = true ]; then
        REC_EXTRAS="$(rec_list extras)"; REC_PLUGINS="$(rec_list plugins)"
        REC_PKG="$(rec_list with_pkg)"; REC_URL="$(rec_list with_url)"
        while IFS= read -r v; do
            [ -z "${v}" ] && continue
            [[ "${v}" =~ ${VALUE_RE} ]] || ok=false
        done <<EOF
${REC_EXTRAS}
${REC_PLUGINS}
${REC_PKG}
${REC_URL}
EOF
    fi
    if [ "${ok}" = false ]; then
        REC_EXTRAS=""; REC_PLUGINS=""; REC_PKG=""; REC_URL=""
        print_warning "Ignoring ${RECORD}: unreadable or an unknown format. Installing exactly what was asked for." >&2
    fi
}

write_record() {
    if [ ! -d "${CONFIG_DIR}" ]; then
        mkdir -p "${CONFIG_DIR}" && chmod 700 "${CONFIG_DIR}"
    fi
    [ -d "${CONFIG_DIR}" ] || return 0
    local tmp="${RECORD}.tmp.$$"
    {
        printf '{"format":1,\n'
        printf '"repo":"%s",\n' "${REPO}"
        printf '"channel":"%s",\n' "${MANIFEST_CHANNEL}"
        printf '"version":"%s",\n' "${REL_VERSION}"
        printf '"installer":"%s",\n' "${INSTALLER}"
        printf '"extras":%s,\n' "$(json_list "${SET_EXTRAS}")"
        printf '"plugins":%s,\n' "$(json_list "${SET_PLUGINS}")"
        printf '"with_pkg":%s,\n' "$(json_list "${SET_PKG}")"
        printf '"with_url":%s,\n' "$(json_list "${SET_URL}")"
        printf '"updated_at":"%s"}\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    } > "${tmp}"
    chmod 644 "${tmp}"
    mv "${tmp}" "${RECORD}"
}

# ---------------------------------------------------------------------------
# The set to install: (recorded + requested) - removed.
# ---------------------------------------------------------------------------
SET_EXTRAS=""; SET_PLUGINS=""; SET_PKG=""; SET_URL=""
REL_VERSION=""

# pkg_spec_named LIST NAME: the recorded --with-pkg spec whose name is NAME.
pkg_spec_named() {
    printf '%s\n' "$1" | awk -v n="$2" '{ s = $0; sub(/[=~@].*/, "", s); if (s == n) { print $0; exit } }'
}

compute_set() {
    local accepted req_plugins="" req_extras="" name pkg_spec
    accepted="$(plugin_names)"

    while IFS= read -r name; do
        [ -z "${name}" ] && continue
        if [ "${name}" = "web" ]; then
            req_extras="web"
        elif list_has "${accepted}" "${name}"; then
            req_plugins="${req_plugins}
${name}"
        elif list_has "${REC_PLUGINS}" "${name}"; then
            # Already recorded, so the drop below warns about it. `remo
            # self-update` re-passes the recorded set as --with; refusing here
            # would fail every update to a release that no longer ships it.
            :
        else
            print_error "Unknown --with name '${name}'."
            echo "  Accepted for ${MANIFEST_TAG}: web$(printf '%s\n' "${accepted}" | grep -v '^$' | sed 's/^/, /' | tr -d '\n')" >&2
            exit 1
        fi
    done <<EOF
${WITH_LIST}
EOF

    # A recorded plugin this release no longer ships is dropped with a warning
    # rather than failing every future update.
    local kept=""
    while IFS= read -r name; do
        [ -z "${name}" ] && continue
        if list_has "${accepted}" "${name}"; then
            kept="${kept}
${name}"
        else
            print_warning "Recorded plugin '${name}' is not part of ${MANIFEST_TAG}; dropping it." >&2
        fi
    done <<EOF
${REC_PLUGINS}
EOF

    SET_EXTRAS="$(list_union "${REC_EXTRAS}" "${req_extras}")"
    SET_PLUGINS="$(list_union "${kept}" "${req_plugins}")"
    SET_PKG="$(list_union "${REC_PKG}" "${PKG_LIST}")"
    SET_URL="$(list_union "${REC_URL}" "${URL_LIST}")"

    while IFS= read -r name; do
        [ -z "${name}" ] && continue
        if list_has "${SET_EXTRAS}" "${name}"; then
            SET_EXTRAS="$(list_remove "${SET_EXTRAS}" "${name}")"
        elif list_has "${SET_PLUGINS}" "${name}"; then
            SET_PLUGINS="$(list_remove "${SET_PLUGINS}" "${name}")"
        elif list_has "${SET_PKG}" "${name}"; then
            SET_PKG="$(list_remove "${SET_PKG}" "${name}")"
        elif pkg_spec="$(pkg_spec_named "${SET_PKG}" "${name}")" && [ -n "${pkg_spec}" ]; then
            # `--without foo` for a recorded `foo==1.0`: the name is what a
            # user remembers, not the exact spec they once typed.
            SET_PKG="$(list_remove "${SET_PKG}" "${pkg_spec}")"
        elif list_has "${SET_URL}" "${name}"; then
            SET_URL="$(list_remove "${SET_URL}" "${name}")"
        else
            if [ "${DRY_RUN}" = true ]; then
                print_info "'${name}' is not in the installed set; nothing to remove." >&2
            else
                print_info "'${name}' is not in the installed set; nothing to remove."
            fi
        fi
    done <<EOF
${WITHOUT_LIST}
EOF
}

validate_additions() {
    local v
    while IFS= read -r v; do
        [ -z "${v}" ] && continue
        if ! [[ "${v}" =~ ${VALUE_RE} ]]; then
            print_error "--with-pkg value '${v}' contains characters that are not allowed."
            exit 1
        fi
    done <<EOF
${PKG_LIST}
EOF
    while IFS= read -r v; do
        [ -z "${v}" ] && continue
        if ! [[ "${v}" =~ ^https://[A-Za-z0-9._:/@+=,~%-]+$ ]]; then
            print_error "--with-url needs an https:// URL without spaces or quotes (got '${v}')."
            exit 1
        fi
    done <<EOF
${URL_LIST}
EOF
}

# ---------------------------------------------------------------------------
# Build and run the install command(s).
# ---------------------------------------------------------------------------
CMDS=()   # each element is one command, its arguments joined by \x1f

# add_cmd ARG...  (stores one command)
add_cmd() {
    local IFS=$'\037'
    CMDS+=("$*")
}

build_commands() {
    local spec extras="" name dist
    spec="${PACKAGE}"
    if [ -n "${SET_EXTRAS}" ]; then
        extras="[$(printf '%s\n' "${SET_EXTRAS}" | grep -v '^$' | tr '\n' ',' | sed 's/,$//')]"
    fi
    spec="${PACKAGE}${extras} @ $(component_location remo-cli)"

    case "${INSTALLER}" in
        uv)
            local args=(uv tool install --force "${spec}")
            while IFS= read -r name; do
                [ -z "${name}" ] && continue
                dist="$(component_field "${name}" dist)"
                args+=(--with "${dist} @ $(component_location "${name}")")
            done <<EOF
${SET_PLUGINS}
EOF
            while IFS= read -r name; do
                [ -z "${name}" ] && continue
                args+=(--with "${name}")
            done <<EOF
${SET_PKG}
${SET_URL}
EOF
            add_cmd "${args[@]}"
            ;;
        pipx)
            add_cmd pipx install --force "${spec}"
            while IFS= read -r name; do
                [ -z "${name}" ] && continue
                dist="$(component_field "${name}" dist)"
                add_cmd pipx inject "${PACKAGE}" "${dist} @ $(component_location "${name}")"
            done <<EOF
${SET_PLUGINS}
EOF
            while IFS= read -r name; do
                [ -z "${name}" ] && continue
                add_cmd pipx inject "${PACKAGE}" "${name}"
            done <<EOF
${SET_PKG}
${SET_URL}
EOF
            ;;
        *) print_error "No installer selected (internal error)."; exit 1 ;;
    esac
}

# pipx only, run once the new remo-cli is in place and before any inject.
#
# `pipx install --force` reuses the existing venv, so every package injected
# by an earlier run survives it: without this, `--without ec2` (or a plugin a
# release dropped) would leave the plugin installed while the record says it is
# gone. And pip treats a local wheel whose version is already installed as
# satisfied, so a plugin whose version did not change between releases would
# never get the new release's code. Uninject both kinds first; the injects that
# follow then install exactly the set. Best-effort: on a fresh venv there is
# nothing to uninject, and that is not an error.
pipx_prune_injected() {
    local name dist p f
    while IFS= read -r name; do
        [ -z "${name}" ] && continue
        dist="$(component_field "${name}" dist)"
        [ -n "${dist}" ] || dist="remo-provider-${name}"
        if list_has "${SET_PLUGINS}" "${name}"; then
            # re-injected next, so its dependencies can stay
            pipx uninject --leave-deps "${PACKAGE}" "${dist}" >/dev/null 2>&1 || true
        else
            pipx uninject "${PACKAGE}" "${dist}" >/dev/null 2>&1 || true
        fi
    done <<EOF
$(list_union "${REC_PLUGINS}" "${SET_PLUGINS}")
EOF
    while IFS= read -r p; do
        [ -z "${p}" ] && continue
        list_has "${SET_PKG}" "${p}" && continue
        pipx uninject "${PACKAGE}" "${p%%[=~@]*}" >/dev/null 2>&1 || true
    done <<EOF
${REC_PKG}
EOF
    while IFS= read -r p; do
        [ -z "${p}" ] && continue
        list_has "${SET_URL}" "${p}" && continue
        f="${p##*/}"
        pipx uninject "${PACKAGE}" "${f%%-*}" >/dev/null 2>&1 || true
    done <<EOF
${REC_URL}
EOF
}

# Print a stored command: arguments with whitespace are quoted.
format_cmd() {
    local IFS=$'\037' out="" a
    # shellcheck disable=SC2206
    local parts=($1)
    for a in "${parts[@]}"; do
        case "${a}" in
            *" "*) out="${out}${out:+ }\"${a}\"" ;;
            *)     out="${out}${out:+ }${a}" ;;
        esac
    done
    printf '%s' "${out}"
}

# Currently-installed version, or empty if not installed. Best-effort: never
# fails the install, and both spellings are normalised (uv prints `v4.4.0`,
# pipx prints `4.4.0`).
#
# Exists so a run is legible. The silent-no-upgrade bug this guards against was
# invisible precisely because nothing reported the before and after: uv printed
# "Installed 2 executables" either way.
installed_version() {
    local v=""
    case "${INSTALLER}" in
        uv)   v=$(uv tool list 2>/dev/null | awk -v p="${PACKAGE}" '$1==p {print $2; exit}') ;;
        pipx) v=$(pipx list --short 2>/dev/null | awk -v p="${PACKAGE}" '$1==p {print $2; exit}') ;;
    esac
    printf '%s' "${v#v}"
}

# A copy of remo under the *other* tool manager would shadow or be shadowed by
# the new one; remove it, but only once the new install is in place.
cleanup_other_manager() {
    case "${INSTALLER}" in
        uv)
            if command -v pipx &>/dev/null \
               && [ -n "$(pipx list --short 2>/dev/null | awk -v p="${PACKAGE}" '$1==p {print $1; exit}')" ]; then
                print_info "Removing the older pipx copy of ${PACKAGE}..."
                pipx uninstall "${PACKAGE}" || print_warning "Could not remove the pipx copy; run: pipx uninstall ${PACKAGE}"
            fi
            ;;
        pipx)
            if command -v uv &>/dev/null \
               && [ -n "$(uv tool list 2>/dev/null | awk -v p="${PACKAGE}" '$1==p {print $1; exit}')" ]; then
                print_info "Removing the older uv copy of ${PACKAGE}..."
                uv tool uninstall "${PACKAGE}" || print_warning "Could not remove the uv copy; run: uv tool uninstall ${PACKAGE}"
            fi
            ;;
    esac
}

install_remo() {
    build_commands

    local c
    if [ "${DRY_RUN}" = true ]; then
        for c in "${CMDS[@]}"; do
            format_cmd "${c}"
            printf '\n'
        done
        return
    fi

    echo ""
    local before
    before="$(installed_version)"
    if [ -n "${before}" ]; then
        print_info "Installing ${PACKAGE} ${REL_VERSION} (replacing ${before})..."
    else
        print_info "Installing ${PACKAGE} ${REL_VERSION}..."
    fi

    # ALWAYS --force, including a bare run with no version.
    #
    # Without it, `uv tool install` over an existing install keeps the
    # installed version and still prints "Installed 2 executables", so the run
    # looks successful and upgrades nothing. `pipx install` behaves the same.
    # This does re-do work on every run, which is the point: the *outcome* is
    # idempotent (after any run you have what you asked for), and that is what
    # Principle VII asks for.
    local IFS_SAVE="${IFS}" parts first=true
    for c in "${CMDS[@]}"; do
        IFS=$'\037'
        # shellcheck disable=SC2206
        parts=(${c})
        IFS="${IFS_SAVE}"
        if ! "${parts[@]}"; then
            print_error "Installation failed."
            printf '  Try running manually: %s\n' "$(format_cmd "${c}")" >&2
            exit 1
        fi
        if [ "${first}" = true ] && [ "${INSTALLER}" = pipx ]; then
            pipx_prune_injected
        fi
        first=false
    done

    # Say what actually happened. An unchanged version after an upgrade run is
    # the symptom of the bug the --force above exists to prevent, so name it
    # rather than leaving the reader to compare two numbers.
    local after
    after="$(installed_version)"
    if [ -n "${after}" ]; then
        if [ -z "${before}" ]; then
            print_success "Installed ${PACKAGE} ${after}."
        elif [ "${before}" = "${after}" ]; then
            # Same version, but `--with`/`--without` may still have changed
            # what is installed with it (the common "add a plugin" re-run).
            if [ "${SET_EXTRAS}|${SET_PLUGINS}|${SET_PKG}|${SET_URL}" != "${REC_EXTRAS}|${REC_PLUGINS}|${REC_PKG}|${REC_URL}" ]; then
                print_success "Updated what is installed with ${PACKAGE} ${after}."
            else
                print_info "${PACKAGE} ${after} was already installed; nothing changed."
            fi
        else
            print_success "Updated ${PACKAGE}: ${before} -> ${after}."
        fi
    fi

    cleanup_other_manager
    write_record
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

    # `--version 5.0.0rc1` and `--prerelease 5.0.0rc1` are the same request.
    if [ -n "${VERSION}" ] && [[ "${VERSION}" =~ ${PRERELEASE_RE} ]]; then
        PRE_RELEASE=true
    fi

    # The slug reaches every URL and the install record, which is written
    # without JSON escaping, so it gets the same shape check as other values.
    if ! [[ "${REPO}" =~ ^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$ ]]; then
        print_error "REMO_REPO_SLUG must look like owner/name (got '${REPO}')."
        exit 1
    fi

    # Decided now: write_record creates the directory, so asking afterwards
    # would call a brand-new install's config "existing".
    local had_config=false
    if [ -d "${CONFIG_DIR}" ]; then
        had_config=true
    fi

    validate_additions
    PKG_LIST="$(list_norm "${PKG_LIST}")"
    URL_LIST="$(list_norm "${URL_LIST}")"
    WITH_LIST="$(list_norm "${WITH_LIST}")"
    WITHOUT_LIST="$(list_norm "${WITHOUT_LIST}")"

    if [ "${PRE_RELEASE}" = true ] && [ -z "${VERSION}" ]; then
        VERSION="$(resolve_latest_prerelease)" || exit 1
        [ "${DRY_RUN}" = true ] || print_info "Newest pre-release: ${VERSION}"
    fi
    if [ "${PRE_RELEASE}" = true ]; then
        EXPECTED_TAG="rc-${VERSION}"
    elif [ -n "${VERSION}" ]; then
        EXPECTED_TAG="v${VERSION}"
    fi

    # --dry-run prints the command and stops: no uv bootstrap, no wheel
    # download, nothing written. The informational lines go to stderr so stdout
    # is just the command, usable in `$(...)`.
    if [ "${DRY_RUN}" = true ]; then
        choose_installer >&2
    else
        choose_installer
        require_sha_tool
    fi

    make_workdir
    fetch_manifest
    REL_VERSION="$(component_field remo-cli version)"
    read_record
    compute_set

    if [ "${DRY_RUN}" = true ]; then
        install_remo
        exit 0
    fi

    local name
    download_and_verify remo-cli
    while IFS= read -r name; do
        [ -z "${name}" ] && continue
        download_and_verify "${name}"
    done <<EOF
${SET_PLUGINS}
EOF

    cleanup_old_install
    install_remo

    # Success
    echo ""
    print_success "=============================================="
    print_success "  remo installed successfully!"
    print_success "=============================================="
    echo ""

    if [ "${had_config}" = true ]; then
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
