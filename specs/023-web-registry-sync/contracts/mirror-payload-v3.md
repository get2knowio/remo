# Contract: mirror payload v3 (023)

`PUT /api/v1/setup/registry` gains version `3`: the exact v2 shape
(mirror-payload-v2.md) plus one required field:

```json
{ "version": 3, "base_generation": 7, "registry": [...], "host_keys": {...}, "workstation": "host/user" }
```

## Precondition semantics

`base_generation` is the mirror generation the sender's merge was computed
against (from `GET /setup/registry`). The service compares it to the current
marker generation **atomically with the apply** (an app-wide lock shared with
the registry-admin mutators):

- match → today's exact apply sequence runs (trust file wholesale, registry,
  legacy-mirror cleanup, marker bump with `origin: "push"`), and the response
  carries the new `mirror_generation`.
- mismatch → `409 {"reason": "generation_conflict", "current_generation": N,
  "last_change": {…} | null}`; the prior mirror is left **byte-intact**. The
  CLI re-reads, re-merges against the same base, retries (bounded at 3).
- missing/invalid `base_generation` on a v3 body → `422 invalid_payload`.

## Supersession of specs/017 research.md R4

R4 chose an advisory warning over an ETag-style precondition when
workstations were the only writer class — any two pushes were full mirrors of
*somebody's* registry, so last-writer-wins was acceptable. The web console is
now a second first-class writer whose changes a push would silently destroy,
so **v3 carries a real precondition**. R4's decision is preserved verbatim
for v1/v2: they remain accepted unconditionally, byte-for-byte — that IS the
deprecated `remo web push` force path. `SUPPORTED_PAYLOAD_VERSIONS = [1, 2, 3]`.
