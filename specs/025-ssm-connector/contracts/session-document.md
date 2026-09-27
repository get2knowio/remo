# Contract: `remo-attach` session document, v1

**Owner**: this repository. Consumers hard-code everything here. A change to any item bumps `DOCUMENT_VERSION`, ships a new document, and is named in the release notes.

## The document (shipped verbatim as `src/remo_cli/core/remo_attach_document.json`; `remo connector document` prints it)

```json
{
  "schemaVersion": "1.0",
  "description": "remo-attach v1: attach the caller to an exposed Remo project session via a remo connector (https://github.com/get2knowio/remo, docs/ssm-connector.md)",
  "sessionType": "InteractiveCommands",
  "parameters": {
    "target": {
      "type": "String",
      "description": "base64url (no padding) of {\"v\":1,\"host\":\"<host>\",\"project\":\"<project>\"}",
      "allowedPattern": "^[A-Za-z0-9_-]{1,1024}$",
      "maxChars": 1024
    }
  },
  "properties": {
    "linux": {
      "commands": "exec /opt/remo-connector/bin/remo connector attach -- {{ target }}",
      "runAsElevated": false
    }
  }
}
```

Create it in an account: `remo connector document > remo-attach.json && aws ssm create-document --name remo-attach --document-type Session --content file://remo-attach.json`.

## Parameter `target`

- Exactly one parameter. Value: unpadded base64url of the compact UTF-8 JSON object `{"v":1,"host":H,"project":P}`.
- `H`: a registry host name, `^[a-zA-Z0-9][a-zA-Z0-9._/-]*$`, ≤ 63 characters.
- `P`: a Remo project name: no control characters, no `/`, not starting with `.`, not `..`; ≤ 255 bytes UTF-8. Spaces, Unicode, quotes and a leading `-` are legal.
- Encoded length 1–1024; alphabet `A–Z a–z 0–9 - _` only. Anything else is rejected by SSM before any command runs.
- Client encoding (reference): `base64.urlsafe_b64encode(json.dumps(obj, separators=(",",":"), ensure_ascii=False).encode()).rstrip(b"=")`.

## Invocation

```
aws ssm start-session --region R --target mi-… --document-name remo-attach --parameters target=<encoded>
```

## Outcomes seen by the client

1. **Attached**: the PTY stream is the project session. Resize is honored; closing the client leaves the session running.
2. **Refused before attach**: exactly one line, then the session ends:

   ```
   remo-connector-error: <code> <message>
   ```

   | code | meaning | typical fix (in the message) |
   |------|---------|------------------------------|
   | `bad-target` | not decodable / not the v1 object shape | re-encode the target |
   | `unsupported-version` | `v` is not 1 | use a client that speaks document v1 |
   | `invalid-name` | host or project fails Remo's validators or the caps | fix the name |
   | `not-exposed` | the host/project pair is not in the connector's exposure set (or the set is empty) | ask the operator to `remo connector enroll … --expose HOST/PROJECT` |
   | `run-as-not-in-effect` | the launcher runs as uid 0 or `ssm-user` | enable Run As with the connector's run-as user (preference or `SSMSessionRunAs` tag) |
   | `config-unreadable` | a connector file is missing/unreadable/unparseable (path named) | re-run enrollment |
   | `ssh-missing` | no `ssh` on the connector's PATH | install `openssh-client` |

   Clients classify by matching `^remo-connector-error: (\S+) (.*)$` on any line of the stream; the exit code is not delivered by SSM.
3. **SSH-level failure after attach began**: OpenSSH's own output (e.g. host key mismatch, connection refused); no error line is produced.

## Connector-side layout the document depends on

- Launcher at `/opt/remo-connector/bin/remo` (system-wide, readable by any user so the refusal in outcome 2 fires for the wrong user too).
- State under `/var/lib/remo-connector/` (0700, run-as user): `registry.json`, `exposure.json`, `id_ed25519[.pub]`, `known_hosts`, `connector.json`, `ssh/`.
- Managed nodes carry the tag `remo:role=connector` (applied through the hybrid activation's `--tags`).

## Versioning

`v` in the target and `DOCUMENT_VERSION` in code move together. The launcher accepts the set `{1}`; a client sending a newer `v` receives `unsupported-version`. The release that first ships this file is tagged and its notes state "ships remo-attach document v1".
