#!/bin/sh
# remo-notifier-netwire — Option A host wiring (issue #42 §2.2).
#
# Joins the single notifier container to a devcontainer's user-defined network(s)
# so a per-container-network topology stays isolated (spokes can't reach each
# other) while the one notifier remains reachable from each. The notifier cannot
# wire itself — it must already be reachable to be registered, and giving it the
# Docker socket is a privilege we reject — so the host does it at container launch.
#
# Idempotent and fail-open: safe to call on every container up/down. A no-op when
# the notifier container is absent (host not configured for approvals) or the
# target container has no user-defined network (nothing to isolate). Disconnect
# only removes the notifier from a network once no other source remains on it.
#
# Usage:
#   remo-notifier-netwire connect    <container>
#   remo-notifier-netwire disconnect <container>
#
# Env:
#   REMO_NOTIFIER_CONTAINER   notifier container name (default: remo-notifier)
#   DOCKER                    docker binary (default: docker) — overridable for tests
set -eu

NOTIFIER="${REMO_NOTIFIER_CONTAINER:-remo-notifier}"
DOCKER="${DOCKER:-docker}"
ACTION="${1:-}"
CONTAINER="${2:-}"

log() { echo "remo-notifier-netwire: $*" >&2; }

# User-defined networks a container is on (one per line). The shared default
# bridge is excluded — joining it provides no isolation.
container_networks() {
	"$DOCKER" inspect -f '{{range $n, $_ := .NetworkSettings.Networks}}{{println $n}}{{end}}' "$1" 2>/dev/null \
		| grep -v -e '^bridge$' -e '^$' || true
}

# Succeeds if a non-notifier, non-<self> container is still attached to <net>.
others_on_network() {
	net="$1"
	self="$2"
	"$DOCKER" network inspect -f '{{range .Containers}}{{println .Name}}{{end}}' "$net" 2>/dev/null \
		| grep -v -e "^$NOTIFIER\$" -e "^$self\$" -e '^$' | grep -q .
}

main() {
	if [ -z "$ACTION" ] || [ -z "$CONTAINER" ]; then
		log "usage: remo-notifier-netwire connect|disconnect <container>"
		exit 2
	fi
	if ! "$DOCKER" inspect "$NOTIFIER" >/dev/null 2>&1; then
		log "notifier '$NOTIFIER' absent; nothing to wire"
		exit 0
	fi
	nets="$(container_networks "$CONTAINER")"
	if [ -z "$nets" ]; then
		log "'$CONTAINER' has no user-defined network; nothing to wire"
		exit 0
	fi
	notifier_nets="$(container_networks "$NOTIFIER")"

	for net in $nets; do
		if printf '%s\n' "$notifier_nets" | grep -Fxq "$net"; then on=yes; else on=no; fi
		case "$ACTION" in
		connect)
			if [ "$on" = yes ]; then
				log "already on '$net'"
			else
				log "connecting notifier to '$net'"
				"$DOCKER" network connect "$net" "$NOTIFIER" || log "connect to '$net' failed (ignored)"
			fi
			;;
		disconnect)
			if [ "$on" = no ]; then
				log "not on '$net'"
			elif others_on_network "$net" "$CONTAINER"; then
				log "keeping notifier on '$net' (other sources still attached)"
			else
				log "disconnecting notifier from '$net'"
				"$DOCKER" network disconnect "$net" "$NOTIFIER" || log "disconnect from '$net' failed (ignored)"
			fi
			;;
		*)
			log "unknown action: $ACTION"
			exit 2
			;;
		esac
	done
}

main "$@"
