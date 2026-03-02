#!/bin/bash
# remo installer — installs remo-cli from PyPI via uv
#
# Usage:
#   curl -fsSL https://get2know.io/remo/install.sh | bash
#   curl -fsSL .../install.sh | bash -s -- --version 0.4.0
#   curl -fsSL .../install.sh | bash -s -- --pre-release

set -e

PACKAGE="remo-cli"
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

# Parse arguments
VERSION=""
PRE_RELEASE=false

while [[ $# -gt 0 ]]; do
    case "$1" in
        --version)
            VERSION="$2"
            shift 2
            ;;
        --pre-release)
            PRE_RELEASE=true
            shift
            ;;
        --help|-h)
            cat << 'EOF'
remo installer — installs remo-cli from PyPI via uv

USAGE:
    curl -fsSL https://get2know.io/remo/install.sh | bash
    curl -fsSL .../install.sh | bash -s -- [OPTIONS]

OPTIONS:
    --version <version>   Install specific version (e.g., 0.4.0)
    --pre-release         Allow pre-release versions
    --help                Show this help message

EXAMPLES:
    # Install latest stable
    curl -fsSL https://get2know.io/remo/install.sh | bash

    # Install specific version
    curl -fsSL .../install.sh | bash -s -- --version 0.4.0

    # Install with pre-release versions allowed
    curl -fsSL .../install.sh | bash -s -- --pre-release
EOF
            exit 0
            ;;
        *)
            print_error "Unknown option: $1"
            exit 1
            ;;
    esac
done

# Install uv if not present
ensure_uv() {
    if command -v uv &>/dev/null; then
        print_info "Found uv: $(uv --version)"
        return
    fi

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

# Install remo-cli
install_remo() {
    echo ""
    print_info "Installing ${PACKAGE}..."

    local uv_args=("tool" "install")

    if [ -n "${VERSION}" ]; then
        uv_args+=("${PACKAGE}==${VERSION}")
    else
        uv_args+=("${PACKAGE}")
    fi

    if [ "${PRE_RELEASE}" = true ]; then
        uv_args+=("--prerelease" "allow")
    fi

    if ! uv "${uv_args[@]}"; then
        print_error "Installation failed."
        echo "  Try running manually: uv ${uv_args[*]}"
        exit 1
    fi
}

# Main
main() {
    echo ""
    print_info "remo installer"
    echo ""

    ensure_uv
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

    echo "  Get started:"
    echo "    remo --version"
    echo "    remo init            # Set up SSH keys, Ansible, etc."
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
