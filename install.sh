#!/bin/bash
# remo installer
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/get2knowio/remo/main/install.sh | bash
#   curl -fsSL .../install.sh | bash -s -- --version v1.0.0
#   curl -fsSL .../install.sh | bash -s -- --pre-release
#   curl -fsSL .../install.sh | bash -s -- --branch feat/my-feature

set -e

REPO="get2knowio/remo"
INSTALL_DIR="${REMO_INSTALL_DIR:-$HOME/.remo}"
BIN_DIR="${REMO_BIN_DIR:-$HOME/.local/bin}"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
BLUE='\033[0;34m'
NC='\033[0m'

print_error() { echo -e "${RED}Error:${NC} $1" >&2; }
print_success() { echo -e "${GREEN}$1${NC}"; }
print_info() { echo -e "${BLUE}$1${NC}"; }
print_warning() { echo -e "${YELLOW}$1${NC}"; }

# Parse arguments
VERSION=""
BRANCH=""
PRE_RELEASE=false

while [[ $# -gt 0 ]]; do
    case "$1" in
        --version)
            VERSION="$2"
            shift 2
            ;;
        --branch)
            BRANCH="$2"
            shift 2
            ;;
        --pre-release)
            PRE_RELEASE=true
            shift
            ;;
        --help|-h)
            cat << 'EOF'
remo installer

USAGE:
    curl -fsSL .../install.sh | bash
    curl -fsSL .../install.sh | bash -s -- [OPTIONS]

OPTIONS:
    --version <version>   Install specific version (e.g., v1.0.0)
    --pre-release         Install latest pre-release version
    --branch <branch>     Install from specific branch (for development)
    --help                Show this help message

ENVIRONMENT:
    REMO_INSTALL_DIR      Installation directory (default: ~/.remo)
    REMO_BIN_DIR          Binary directory (default: ~/.local/bin)

EXAMPLES:
    # Install latest stable
    curl -fsSL .../install.sh | bash

    # Install specific version
    curl -fsSL .../install.sh | bash -s -- --version v1.0.0

    # Install latest pre-release
    curl -fsSL .../install.sh | bash -s -- --pre-release

    # Install from branch
    curl -fsSL .../install.sh | bash -s -- --branch main
EOF
            exit 0
            ;;
        *)
            print_error "Unknown option: $1"
            exit 1
            ;;
    esac
done

# Check for required tools
check_requirements() {
    local missing=()

    command -v git &>/dev/null || missing+=("git")
    command -v curl &>/dev/null || missing+=("curl")
    command -v python3 &>/dev/null || missing+=("python3")

    if [ ${#missing[@]} -gt 0 ]; then
        print_error "Missing required tools: ${missing[*]}"
        echo "Please install them and try again."
        exit 1
    fi
}

# Get latest release tag from GitHub API
get_latest_release() {
    local include_prerelease="$1"
    local releases

    releases=$(curl -fsSL "https://api.github.com/repos/${REPO}/releases?per_page=20")

    if [ "$include_prerelease" = "true" ]; then
        # Get latest pre-release
        echo "$releases" | grep -o '"tag_name": "[^"]*"' | head -1 | cut -d'"' -f4
    else
        # Get latest stable (non-prerelease)
        # Filter out releases marked as prerelease or with -rc, -beta, -alpha in tag
        echo "$releases" | python3 -c "
import sys, json
releases = json.load(sys.stdin)
for r in releases:
    if not r.get('prerelease', False) and not r.get('draft', False):
        tag = r.get('tag_name', '')
        if '-rc' not in tag and '-beta' not in tag and '-alpha' not in tag:
            print(tag)
            break
" 2>/dev/null || echo ""
    fi
}

# Determine what to install
determine_version() {
    if [ -n "$BRANCH" ]; then
        echo "branch:$BRANCH"
        return
    fi

    if [ -n "$VERSION" ]; then
        echo "tag:$VERSION"
        return
    fi

    print_info "Checking for latest release..."

    local tag
    if [ "$PRE_RELEASE" = "true" ]; then
        tag=$(get_latest_release true)
        if [ -z "$tag" ]; then
            print_error "No pre-release found"
            exit 1
        fi
        print_info "Latest pre-release: $tag"
    else
        tag=$(get_latest_release false)
        if [ -z "$tag" ]; then
            print_warning "No stable release found, falling back to main branch"
            echo "branch:main"
            return
        fi
        print_info "Latest stable release: $tag"
    fi

    echo "tag:$tag"
}

# Install remo
install_remo() {
    local ref="$1"
    local ref_type="${ref%%:*}"
    local ref_value="${ref#*:}"

    echo ""
    print_info "Installing remo..."
    echo ""

    # Remove existing installation
    if [ -d "$INSTALL_DIR" ]; then
        print_info "Removing existing installation..."
        rm -rf "$INSTALL_DIR"
    fi

    # Clone repository
    print_info "Downloading remo..."
    if [ "$ref_type" = "branch" ]; then
        git clone --depth 1 --branch "$ref_value" "https://github.com/${REPO}.git" "$INSTALL_DIR" 2>/dev/null
    else
        git clone --depth 1 --branch "$ref_value" "https://github.com/${REPO}.git" "$INSTALL_DIR" 2>/dev/null
    fi

    # Record installed version info
    echo "$ref" > "$INSTALL_DIR/.installed-ref"

    # Run remo init
    print_info "Initializing remo..."
    cd "$INSTALL_DIR"
    ./remo init

    # Create bin directory if needed
    mkdir -p "$BIN_DIR"

    # Create symlink
    if [ -L "$BIN_DIR/remo" ]; then
        rm "$BIN_DIR/remo"
    fi
    ln -s "$INSTALL_DIR/remo" "$BIN_DIR/remo"

    # Check if BIN_DIR is in PATH
    if [[ ":$PATH:" != *":$BIN_DIR:"* ]]; then
        print_warning "Note: $BIN_DIR is not in your PATH"
        echo ""
        echo "Add it to your shell profile:"
        echo ""
        echo "  # For bash (~/.bashrc):"
        echo "  export PATH=\"\$HOME/.local/bin:\$PATH\""
        echo ""
        echo "  # For zsh (~/.zshrc):"
        echo "  export PATH=\"\$HOME/.local/bin:\$PATH\""
        echo ""
    fi

    # Success message
    echo ""
    print_success "=============================================="
    print_success "  remo installed successfully!"
    print_success "=============================================="
    echo ""

    local version
    if [ -f "$INSTALL_DIR/VERSION" ]; then
        version=$(cat "$INSTALL_DIR/VERSION")
    else
        version="$ref_value"
    fi
    echo "  Version:  $version"
    echo "  Location: $INSTALL_DIR"
    echo "  Binary:   $BIN_DIR/remo"
    echo ""
    echo "Get started:"
    echo "  remo --help"
    echo "  remo incus --help"
    echo "  remo hetzner --help"
    echo ""
}

# Main
main() {
    echo ""
    print_info "remo installer"
    echo ""

    check_requirements

    local ref
    ref=$(determine_version)

    install_remo "$ref"
}

main "$@"
