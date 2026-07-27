// Sanity guard (FR-020) for a console-only contributor who never touches the
// Python side: the checked-in OpenAPI artifact that `client.ts`'s generated
// types ultimately derive from must exist and be parseable JSON with the
// top-level shape an OpenAPI 3.x document is expected to have. This is
// deliberately not a schema-drift check (that's `check:types-fresh` /
// `tests/unit/test_schema_drift.py`) — just "is the source artifact present
// and not corrupt", so a missing/garbled file fails loudly here instead of
// surfacing as a confusing downstream type error.
//
// Node built-ins (fs, path, url) work fine inside Vitest test files even
// though `environment: "jsdom"` is configured for component tests — jsdom
// only sandboxes the browser globals, not the Node module resolution the
// test file itself runs under.

import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

const __dirname = dirname(fileURLToPath(import.meta.url));
const OPENAPI_JSON_PATH = join(__dirname, "generated", "openapi.json");

describe("checked-in OpenAPI artifact", () => {
  it("is present and readable", () => {
    let raw: string;
    try {
      raw = readFileSync(OPENAPI_JSON_PATH, "utf8");
    } catch (err) {
      throw new Error(
        `Expected the checked-in OpenAPI artifact at src/api/generated/openapi.json ` +
          `to exist, but it could not be read: ${err instanceof Error ? err.message : err}. ` +
          "Generate it with `uv run python scripts/export_openapi.py` from the repo root. " +
          "See docs/maintaining-generated-types.md.",
      );
    }
    expect(raw.length).toBeGreaterThan(0);
  });

  it("is parseable JSON shaped like an OpenAPI document", () => {
    const raw = readFileSync(OPENAPI_JSON_PATH, "utf8");

    let doc: unknown;
    try {
      doc = JSON.parse(raw);
    } catch (err) {
      throw new Error(
        `src/api/generated/openapi.json is not valid JSON: ` +
          `${err instanceof Error ? err.message : err}. Regenerate it with ` +
          "`uv run python scripts/export_openapi.py` from the repo root. " +
          "See docs/maintaining-generated-types.md.",
      );
    }

    expect(doc).toBeTypeOf("object");
    expect(doc).not.toBeNull();
    const record = doc as Record<string, unknown>;

    expect(record, "missing top-level 'openapi' version field").toHaveProperty("openapi");
    expect(typeof record.openapi).toBe("string");

    expect(record, "missing top-level 'paths' field").toHaveProperty("paths");
    expect(record.paths).toBeTypeOf("object");

    expect(record, "missing top-level 'components' field").toHaveProperty("components");
    expect(record.components).toBeTypeOf("object");
    const components = record.components as Record<string, unknown>;
    expect(components, "'components' has no 'schemas' — nothing for openapi-typescript to generate")
      .toHaveProperty("schemas");
  });
});
