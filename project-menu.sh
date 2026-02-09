#!/bin/bash
# project-menu - TUI menu for selecting/managing project zellij sessions
# Local test version

set -e

PROJECTS_DIR="/home/remo/projects"

# Get list of active zellij sessions
get_active_sessions() {
    if command -v zellij &> /dev/null; then
        # Strip ANSI color codes and get session names
        zellij list-sessions 2>/dev/null | sed 's/\x1b\[[0-9;]*m//g' | awk '{print $1}' || true
    fi
}

# Get list of project directories
get_project_dirs() {
    if [ -d "$PROJECTS_DIR" ]; then
        find "$PROJECTS_DIR" -maxdepth 1 -mindepth 1 -type d -exec basename {} \; 2>/dev/null | sort
    fi
}

# Build menu options
build_menu_options() {
    local active_sessions
    local project_dirs
    local options=()

    # Get active sessions as array
    mapfile -t active_sessions < <(get_active_sessions)

    # Get all project directories
    mapfile -t project_dirs < <(get_project_dirs)

    # Create associative array for quick lookup of active sessions
    declare -A active_map
    for session in "${active_sessions[@]}"; do
        [ -n "$session" ] && active_map["$session"]=1
    done

    # Build options: show projects, marking active zellij sessions
    for dir in "${project_dirs[@]}"; do
        if [ -n "${active_map[$dir]}" ]; then
            options+=("$dir - active")
        else
            options+=("$dir")
        fi
    done

    # Special options
    options+=("[Clone new repo]")
    options+=("[Exit to shell]")

    printf '%s\n' "${options[@]}"
}

# Display menu and get selection
show_menu() {
    local options
    options=$(build_menu_options)

    # Count the number of project entries (excluding Clone and Exit options)
    local project_count
    project_count=$(echo "$options" | grep -c -v '^\[' || true)

    echo "$options" | fzf --reverse --no-info \
        --expect=d \
        --header="Select a project (1-9, up/down, Enter | c=clone, d=delete, x=exit):" \
        --pointer=">" \
        --prompt="" \
        --color="header:bold" \
        --bind="1:pos(1)+accept,2:pos(2)+accept,3:pos(3)+accept,4:pos(4)+accept,5:pos(5)+accept" \
        --bind="6:pos(6)+accept,7:pos(7)+accept,8:pos(8)+accept,9:pos(9)+accept" \
        --bind="c:pos($((project_count + 1)))+accept" \
        --bind="x:pos($((project_count + 2)))+accept"
}

# Handle cloning a new repo
handle_clone() {
    local repo_url
    local repo_name

    echo ""
    echo -n "Enter GitHub repo URL (or owner/repo): "
    read -r repo_url

    if [ -z "$repo_url" ]; then
        echo "Cancelled."
        return 1
    fi

    # Extract repo name from URL or owner/repo format
    repo_name=$(basename "$repo_url" .git)

    local target_dir="$PROJECTS_DIR/$repo_name"

    if [ -d "$target_dir" ]; then
        echo "Error: Directory $target_dir already exists"
        sleep 2
        return 1
    fi

    echo "Cloning $repo_url to $target_dir..."

    # Use gh for better auth handling
    if command -v gh &> /dev/null; then
        gh repo clone "$repo_url" "$target_dir"
    else
        git clone "$repo_url" "$target_dir"
    fi

    echo "Cloned successfully!"
    sleep 1

    # Return the repo name to launch
    echo "$repo_name"
}

# Handle deleting a project
handle_delete() {
    local project_name="$1"
    local project_dir="$PROJECTS_DIR/$project_name"
    local warnings=()

    echo ""

    if [ ! -d "$project_dir" ]; then
        echo "  Not a project directory, skipping."
        sleep 1
        return 1
    fi

    # Check for uncommitted changes
    if [ -d "$project_dir/.git" ]; then
        local uncommitted
        uncommitted=$(git -C "$project_dir" status --porcelain 2>/dev/null)
        if [ -n "$uncommitted" ]; then
            local change_count
            change_count=$(echo "$uncommitted" | wc -l | tr -d ' ')
            warnings+=("$(printf '\033[33m  ⚠ %s uncommitted change(s)\033[0m' "$change_count")")
        fi

        # Check for unpushed commits
        local ahead
        ahead=$(git -C "$project_dir" rev-list --count @{upstream}..HEAD 2>/dev/null || echo "0")
        if [ "$ahead" -gt 0 ]; then
            warnings+=("$(printf '\033[33m  ⚠ %s unpushed commit(s)\033[0m' "$ahead")")
        fi
    fi

    # Check for active zellij session
    local has_session=false
    if zellij list-sessions 2>/dev/null | sed 's/\x1b\[[0-9;]*m//g' | grep -v 'EXITED' | grep -q "^${project_name}\b"; then
        has_session=true
        warnings+=("$(printf '\033[32m  ⚡ Active zellij session (will be killed)\033[0m')")
    fi

    # Check for running devcontainer
    local container_id=""
    if command -v docker &>/dev/null; then
        container_id=$(docker ps -q --filter "label=devcontainer.local_folder=$project_dir" 2>/dev/null)
    fi
    if [ -n "$container_id" ]; then
        warnings+=("$(printf '\033[36m  🐳 Running devcontainer (will be stopped and removed)\033[0m')")
    fi

    # Display deletion summary
    printf '\033[1m  Delete project: %s\033[0m\n' "$project_name"
    echo ""
    if [ ${#warnings[@]} -gt 0 ]; then
        for w in "${warnings[@]}"; do
            printf '%b\n' "$w"
        done
        echo ""
    fi
    printf '  This will permanently delete %s\n' "$project_dir"
    echo ""
    printf '  Type "delete" to confirm: '
    read -r confirm

    if [ "$confirm" != "delete" ]; then
        echo "  Cancelled."
        sleep 1
        return 1
    fi

    echo ""

    # Stop and remove devcontainer if running
    if [ -n "$container_id" ]; then
        echo "  Stopping devcontainer..."
        docker rm -f "$container_id" &>/dev/null || true
    fi

    # Kill zellij session if active
    if [ "$has_session" = true ]; then
        echo "  Killing zellij session..."
        zellij kill-session "$project_name" &>/dev/null || true
        zellij delete-session "$project_name" &>/dev/null || true
    fi

    # Remove project directory
    echo "  Removing $project_dir..."
    rm -rf "$project_dir"

    printf '\033[32m  ✓ Deleted %s\033[0m\n' "$project_name"
    sleep 1
}

# Launch or attach to a zellij session for a project
launch_session() {
    local project_name="$1"
    local project_dir="$PROJECTS_DIR/$project_name"

    if [ ! -d "$project_dir" ]; then
        echo "Error: Project directory not found: $project_dir"
        sleep 2
        return 1
    fi

    echo "Launching $project_name..."

    # Always use zellij for every project
    # The session starts in the project directory
    # If it has a devcontainer, the shell inside will handle starting it
    cd "$project_dir"
    zellij attach --create "$project_name"

    # Reset terminal state after zellij detach — zellij can leave the tty in
    # raw mode and bracketed paste enabled, breaking arrow keys and paste
    stty sane 2>/dev/null
    printf '\e[?2004l'
}

# Main menu loop
main() {
    # Verify fzf is available
    if ! command -v fzf &> /dev/null; then
        echo "Error: fzf is not installed."
        exit 1
    fi

    clear
    echo ""
    echo "  Remote Coding Server"
    echo "  --------------------"
    echo ""

    while true; do
        # show_menu outputs two lines due to --expect=d:
        # line 1 = key pressed ("d" or empty for normal accept)
        # line 2 = selected item
        local fzf_output
        fzf_output=$(show_menu) || {
            # User pressed Ctrl+C or Escape
            echo ""
            echo "Exiting to shell. Run 'project-menu' to return."
            break
        }
        local key="${fzf_output%%$'\n'*}"
        local selection="${fzf_output#*$'\n'}"

        # Handle delete key
        if [ "$key" = "d" ]; then
            local project_name="${selection% - active}"
            if [[ "$project_name" != \[* ]] && [ -n "$project_name" ]; then
                handle_delete "$project_name"
            fi
            continue
        fi

        case "$selection" in
            "[Clone new repo]")
                local new_repo
                if new_repo=$(handle_clone); then
                    # Launch session for newly cloned repo
                    launch_session "$new_repo"
                fi
                ;;
            "[Exit to shell]")
                echo ""
                echo "Exiting to shell. Run 'project-menu' to return."
                break
                ;;
            *" - active")
                # Extract project name (remove " - active" suffix)
                local project_name="${selection% - active}"
                launch_session "$project_name"
                ;;
            *)
                # Regular project directory
                launch_session "$selection"
                ;;
        esac

        # After detaching from zellij, show menu again
        clear
        echo ""
        echo "  Detached from session"
        echo ""
    done
}

# Run main if executed directly (not sourced)
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    main "$@"
fi
