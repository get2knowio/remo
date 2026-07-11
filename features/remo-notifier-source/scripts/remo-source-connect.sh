#!/bin/sh
# remo-notifier-source connector (spec 009 US3 / FR-012/FR-016/FR-017).
#
# Holds a presence connection open to the host notifier's POST /v1/sources for
# the container's lifetime. While the stream is open this source is registered
# and polled; when it drops (notifier restart, network blip, 503 at_capacity) the
# connector reconnects with full-jitter exponential backoff. It sends NO
# application heartbeat and NO periodic re-register — re-opening a dropped
# connection is its only job (the notifier's keepalive/idle timeout is the
# liveness mechanism). The container stopping drops the connection and
# de-registers the source.
#
# Configuration comes from REMO_SOURCE_* env vars (rendered by install.sh into an
# env file the entrypoint sources). Set REMO_SOURCE_DRY_RUN=1 to print the
# registration JSON and POST target and exit (debugging only).
set -eu

ENV_FILE="${REMO_SOURCE_ENV_FILE:-/usr/local/share/remo-notifier-source/source.env}"
if [ -f "$ENV_FILE" ]; then
	# shellcheck source=/dev/null
	. "$ENV_FILE"
fi

NOTIFIER_ADDRESS="${REMO_SOURCE_NOTIFIER_ADDRESS:-172.17.0.1:18181}"
AGENTSH_API_URL="${REMO_SOURCE_AGENTSH_API_URL:-}"
AGENTSH_SCHEME="${REMO_SOURCE_AGENTSH_SCHEME:-http}"
AGENTSH_PORT="${REMO_SOURCE_AGENTSH_PORT:-8080}"
API_KEY="${REMO_SOURCE_API_KEY:-}"
API_KEY_FILE="${REMO_SOURCE_API_KEY_FILE:-}"
SOURCE_ID="${REMO_SOURCE_ID:-}"
LABELS="${REMO_SOURCE_LABELS:-}"

# Conventional approver-key path agentsh is expected to write to. Used only when
# neither apiKey nor apiKeyFile is set, so a uniform host overlay needs no
# per-container secret config (issue #42).
DEFAULT_API_KEY_FILE="${REMO_SOURCE_DEFAULT_API_KEY_FILE:-/run/secrets/agentsh_approver_key}"

log() { echo "remo-notifier-source: $*" >&2; }

# Minimal JSON string escaping (backslash, then double-quote).
json_str() {
	s=$(printf '%s' "$1" | sed -e 's/\\/\\\\/g' -e 's/"/\\"/g')
	printf '"%s"' "$s"
}

# Comma-separated key=value -> JSON object (empty -> {}).
labels_json() {
	raw="$1"
	[ -z "$raw" ] && { printf '{}'; return; }
	out=""
	oldifs=$IFS
	IFS=','
	for pair in $raw; do
		IFS=$oldifs
		key=${pair%%=*}
		val=${pair#*=}
		[ -z "$key" ] && continue
		entry="$(json_str "$key"):$(json_str "$val")"
		if [ -z "$out" ]; then out="$entry"; else out="$out,$entry"; fi
		IFS=','
	done
	IFS=$oldifs
	printf '{%s}' "$out"
}

read_key() {
	if [ -n "$API_KEY" ]; then
		printf '%s' "$API_KEY"
	elif [ -n "$API_KEY_FILE" ] && [ -r "$API_KEY_FILE" ]; then
		# Read the inline key at connect time so it need not sit in devcontainer.json.
		tr -d '\n' <"$API_KEY_FILE"
	fi
}

# Derive per-container values from convention so a single uniform host overlay
# works with no per-project config (issue #42). The only genuine variable is the
# container's own network name; everything else follows by convention. NOTE: this
# is correct only when hostname == the notifier-resolvable network name (alias),
# which the host/launch layer must pin (issue #42 §2.2).
resolve_defaults() {
	[ -n "$SOURCE_ID" ] || SOURCE_ID="$(hostname)"
	if [ -z "$AGENTSH_API_URL" ]; then
		AGENTSH_API_URL="${AGENTSH_SCHEME}://${SOURCE_ID}:${AGENTSH_PORT}"
		log "derived agentshApiUrl=$AGENTSH_API_URL"
	fi
	if [ -z "$API_KEY" ] && [ -z "$API_KEY_FILE" ] && [ -r "$DEFAULT_API_KEY_FILE" ]; then
		API_KEY_FILE="$DEFAULT_API_KEY_FILE"
		log "using conventional apiKeyFile=$API_KEY_FILE"
	fi
}

preflight() {
	missing=""
	[ -n "$NOTIFIER_ADDRESS" ] || missing="$missing notifierAddress"
	[ -n "$AGENTSH_API_URL" ] || missing="$missing agentshApiUrl"
	if [ -z "$API_KEY" ] && { [ -z "$API_KEY_FILE" ] || [ ! -r "$API_KEY_FILE" ]; }; then
		missing="$missing apiKey-or-readable-apiKeyFile"
	fi
	if [ -n "$missing" ]; then
		log "missing required option(s):$missing"
		exit 1
	fi
}

build_json() {
	key="$1"
	printf '{"source_id":%s,"api_url":%s,"api_key":%s,"labels":%s}' \
		"$(json_str "$SOURCE_ID")" \
		"$(json_str "$AGENTSH_API_URL")" \
		"$(json_str "$key")" \
		"$(labels_json "$LABELS")"
}

main() {
	resolve_defaults
	preflight
	url="http://$NOTIFIER_ADDRESS/v1/sources"

	if [ -n "${REMO_SOURCE_DRY_RUN:-}" ]; then
		key="$(read_key)"
		echo "POST $url"
		build_json "$key"
		echo
		exit 0
	fi

	backoff=1
	while true; do
		key="$(read_key)"
		json="$(build_json "$key")"
		log "connecting to $url as source '$SOURCE_ID'"
		# Hold the streamed keepalive response open; --no-buffer streams ticks.
		curl --no-buffer -sS -X POST "$url" \
			-H 'content-type: application/json' \
			-d "$json" || true
		# Reconnect with full-jitter exponential backoff (base 1s, factor 2, cap 30s).
		delay=$(awk -v b="$backoff" 'BEGIN { srand(); printf "%.2f", b * rand() }')
		log "connection ended; reconnecting in ${delay}s"
		sleep "$delay"
		backoff=$((backoff * 2))
		[ "$backoff" -gt 30 ] && backoff=30
	done
}

main "$@"
