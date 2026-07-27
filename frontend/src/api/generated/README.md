# Generated — do not hand-edit

Every file in this directory is generated from the FastAPI service's contract. None of them is
ever hand-edited; a fix always starts in the service (or, for `.d.ts` files, in the checked-in
`.json` artifact they are generated from), followed by regeneration.

| File | Regenerate with |
|---|---|
| `openapi.json` | `uv run python scripts/export_openapi.py` |
| `terminal-frames.json` | `uv run python scripts/export_openapi.py` |
| `schema.d.ts` | `npm run generate:types` (from `frontend/`) |
| `terminal-frames.d.ts` | `npm run generate:types` (from `frontend/`) |

See `docs/maintaining-generated-types.md` for the full contributor guide, including how to read a
drift-check failure.
