#!/bin/sh
# Idempotent installer for the remo-notifier-source Feature (spec 009 US3, T023).
# Re-running produces identical state (Constitution III): it ensures curl, installs
# the connector, and renders the resolved options into an env file the entrypoint
# sources at start.
set -eu

DEST="/usr/local/share/remo-notifier-source"
HERE="$(cd "$(dirname "$0")" && pwd)"

# Ensure curl is present (the connector's only hard runtime dependency).
if ! command -v curl >/dev/null 2>&1; then
	if command -v apt-get >/dev/null 2>&1; then
		apt-get update -y
		apt-get install -y --no-install-recommends curl
		rm -rf /var/lib/apt/lists/*
	elif command -v apk >/dev/null 2>&1; then
		apk add --no-cache curl
	else
		echo "remo-notifier-source: curl is required but no known package manager was found" >&2
		exit 1
	fi
fi

mkdir -p "$DEST"
install -m 0755 "$HERE/scripts/remo-source-connect.sh" "$DEST/remo-source-connect.sh"

# Devcontainer option values arrive as uppercased env vars during install. Render
# them into REMO_SOURCE_* the connector reads. The file may hold an inline key, so
# keep it private (0600); prefer apiKeyFile to avoid writing a secret here at all.
umask 077
cat >"$DEST/source.env" <<EOF
REMO_SOURCE_NOTIFIER_ADDRESS='${NOTIFIERADDRESS:-172.17.0.1:18181}'
REMO_SOURCE_AGENTSH_API_URL='${AGENTSHAPIURL:-}'
REMO_SOURCE_AGENTSH_PORT='${AGENTSHPORT:-8080}'
REMO_SOURCE_API_KEY='${APIKEY:-}'
REMO_SOURCE_API_KEY_FILE='${APIKEYFILE:-}'
REMO_SOURCE_ID='${SOURCEID:-}'
REMO_SOURCE_LABELS='${LABELS:-}'
EOF
chmod 0600 "$DEST/source.env"

echo "remo-notifier-source: installed connector to $DEST"
