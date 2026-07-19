# Contract: `remo web adopt` / `remo web push` — 012 delta

The adoption/push **behavior** (registry mirror, host-key scan+verify, key
authorization, verification pass, `--via` tunnel) is **inherited unchanged from
011** (see `specs/011-web-adopt/contracts/cli-web-adopt.md`). 012 changes only
**how the bearer credential is obtained and that nothing is persisted**.

---

## Credential: pairing code, not a static token (FR-018)

Both commands accept the **pairing code** exactly where 011 accepted the token —
no new CLI concept:

- `--token <code>` option (kept for compatibility; the value is a pairing code),
- else `$REMO_API_TOKEN`,
- else a hidden interactive prompt (label updated to **"Pairing code"**).

The code is sent as `Authorization: Bearer <code>` on every setup call, exactly
as the token was.

## No saved credentials (FR-019)

- The `--save` flag is **removed**; there is no "offer to save credentials" step.
- `remo web push` no longer has a saved-credentials fast path: it resolves the
  service URL (arg / `$REMO_API_URL` / prompt) and pairing code (option /
  `$REMO_API_TOKEN` / prompt) **every time**, the same way `adopt` does.
- Removed from `core/web_adopt.py`: `SavedCredentials` url/token fields,
  `save_credentials`, credential-loading, and the `NoSavedCredentialsError`
  fallback in `push`.
- **Retained (optional, non-secret)**: a push cache of deployment id +
  per-instance host-key fingerprints for the "unchanged instance skips
  re-authorization" optimization — no URL or code is ever stored.

## Dormant-404 handling (FR-020)

When a setup call returns the dormant `404` (the code expired, was rotated by a
page reopen, or the session ended), the CLI MUST surface an actionable message:

> The pairing code is no longer valid (the service's setup surface is dormant).
> Reopen the adopt page (or the dashboard's re-sync affordance) to mint a fresh
> code, then retry.

This replaces 011's "wrong token → 401" and "no token configured → 404 setup
disabled" messages, which no longer apply (there is no static token).

## Flow (US1 / US4)

```text
operator (browser, signed in via forward-auth proxy)
  └─ opens adopt page  → SPA POST /pairing/mint  → live session + code (in ref)
  └─ clicks "Copy pairing code" → code on clipboard (never displayed)
operator (workstation)
  └─ remo web adopt <url>   [paste code at prompt]
        → GET /setup/identity, PUT /setup/registry, POST /setup/verify
          (each Bearer <code>; each success touches the sliding TTL)
        → successful apply ENDS the session → setup surface dormant again
  └─ browser flips to dashboard
```

`remo web push` is identical except it is driven from the dashboard's **Pair CLI
to sync** affordance (FR-017) and applies the registry mirror to an already-
adopted service.
