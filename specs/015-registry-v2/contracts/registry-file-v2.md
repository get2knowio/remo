# Contract: Registry File Format v2

**File**: `${REMO_HOME}/registry.json` (default `~/.config/remo/registry.json`)
**Encoding**: UTF-8, pretty-printed JSON (2-space indent), entries sorted by `(type, name)`, trailing newline.
**Lock sidecar**: `${REMO_HOME}/registry.lock` (never replaced; content irrelevant).
**Legacy backup**: `${REMO_HOME}/known_hosts.v1.bak` (+ `.1`, `.2`… suffixes when a backup already exists).

## JSON Schema (draft 2020-12)

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "title": "remo registry v2",
  "type": "object",
  "required": ["version", "hosts"],
  "properties": {
    "version": { "const": 2 },
    "hosts": {
      "type": "array",
      "items": { "$ref": "#/$defs/hostEntry" }
    }
  },
  "$defs": {
    "hostEntry": {
      "type": "object",
      "required": ["type", "name", "host", "user", "access"],
      "properties": {
        "type": { "type": "string", "minLength": 1 },
        "name": { "type": "string", "minLength": 1 },
        "host": { "type": "string", "minLength": 1 },
        "user": { "type": "string", "minLength": 1 },
        "access": { "enum": ["direct", "ssm"] },
        "incus": {
          "type": "object",
          "properties": { "host_user": { "type": "string" } },
          "additionalProperties": false
        },
        "proxmox": {
          "type": "object",
          "properties": {
            "vmid": { "type": "string" },
            "node_user": { "type": "string" }
          },
          "additionalProperties": false
        },
        "aws": {
          "type": "object",
          "properties": {
            "instance_id": { "type": "string" },
            "region": { "type": "string" }
          },
          "additionalProperties": false
        },
        "ssh": {
          "type": "object",
          "properties": {
            "port": { "type": "integer", "minimum": 1, "maximum": 65535 },
            "identity_file": { "type": "string" }
          },
          "additionalProperties": false
        }
      }
    }
  }
}
```

Additional invariants not expressible in the schema above:

- The nested per-type object's key MUST equal the entry's `type` (an `aws` entry may not carry a `proxmox` object).
- `access: "ssm"` is only valid for `type: "aws"`.
- `(type, name)` is unique across `hosts`.
- Entries with a `type` outside the known set are VALID (readers preserve them verbatim on rewrite and skip them in listings); their unknown nested content is preserved untouched.
- Readers MUST reject the document if `version` > 2 ("written by a newer version") and MUST NOT modify the file.
- No control characters or newlines in any string field.

## Example

```json
{
  "version": 2,
  "hosts": [
    {
      "type": "aws",
      "name": "buildbox",
      "host": "203.0.113.7",
      "user": "remo",
      "access": "ssm",
      "aws": { "instance_id": "i-0abc123def456", "region": "us-east-1" }
    },
    {
      "type": "hetzner",
      "name": "dev1",
      "host": "2001:db8::7",
      "user": "remo",
      "access": "direct"
    },
    {
      "type": "incus",
      "name": "nuc/dev1",
      "host": "dev1.incus",
      "user": "remo",
      "access": "direct",
      "incus": { "host_user": "paul" }
    },
    {
      "type": "proxmox",
      "name": "pve1/dev2",
      "host": "10.0.0.42",
      "user": "remo",
      "access": "direct",
      "proxmox": { "vmid": "104", "node_user": "root" }
    },
    {
      "type": "ssh",
      "name": "nas",
      "host": "nas.lan",
      "user": "admin",
      "access": "direct",
      "ssh": { "port": 2222, "identity_file": "/home/paul/.ssh/id_nas" }
    }
  ]
}
```

Note the second entry: an IPv6 `host` — representable here, corrupting in the legacy colon format. That asymmetry is the point of this contract.

## Legacy format (read/migrate only)

`${REMO_HOME}/known_hosts`, one entry per line: `TYPE:NAME:HOST:USER[:INSTANCE_ID[:ACCESS_MODE[:REGION]]]` with per-type slot overloading. Writers of this format are removed by this feature; readers survive for in-place readonly consumption (web service) and as migration input. Mapping tables: [data-model.md §4](../data-model.md).
