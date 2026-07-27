#!/usr/bin/env node
// Generates `src/api/generated/terminal-frames.d.ts` from the checked-in
// `src/api/generated/terminal-frames.json` frame contract (feature 020,
// User Story 4, T054).
//
// `terminal-frames.json` is NOT an OpenAPI document -- it is the envelope
// `{protocol, frame_version, inbound: <JSON Schema>, outbound: <JSON Schema>}`
// produced by `scripts/export_openapi.py::build_frames_document()` (Pydantic
// `TypeAdapter(...).json_schema()`). `openapi-typescript` v7's JS API
// requires a genuine `{openapi: "3.x", ...}` document, so this script wraps
// the frame schemas in a small synthetic, in-memory OpenAPI 3.1 document
// before handing it to `openapiTS()` -- no file is written for that
// intermediate document, it only ever exists as a JS object.
//
// Every `$defs`-local frame model (`ResizeFrame`, `PingFrame`, `ReadyFrame`,
// `ExitFrame`, `ErrorFrame`, `PongFrame`, plus `ErrorClass`) is hoisted into
// `components.schemas`, alongside a synthetic `InboundFrame`/`OutboundFrame`
// schema per direction that reproduces the discriminated union. Every
// `"#/$defs/X"` ref is rewritten to `"#/components/schemas/X"` to match.
//
// Usable two ways:
//   - CLI:      node scripts/generate-frame-types.mjs
//   - Imported: import { generateFrameTypes } from "./generate-frame-types.mjs"
//               (used by check-types-fresh.mjs to regenerate into a temp path)

import { readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import openapiTS, { astToString } from "openapi-typescript";

const __dirname = dirname(fileURLToPath(import.meta.url));
const frontendRoot = join(__dirname, "..");

export const FRAMES_JSON = join(frontendRoot, "src", "api", "generated", "terminal-frames.json");
export const FRAMES_DTS_OUT = join(
  frontendRoot,
  "src",
  "api",
  "generated",
  "terminal-frames.d.ts",
);

//: R-6 (generated-file header): names the exact regen command, states the
// file is not hand-edited. `.d.ts` files support `//` comments, unlike the
// JSON artifacts, so this lives as a real header comment rather than an
// `x-generated-by` field.
const GENERATED_HEADER = `/**
 * GENERATED FILE -- do not hand-edit.
 * Regenerate with: npm run generate:types (from frontend/).
 * Source: src/api/generated/terminal-frames.json.
 * See docs/maintaining-generated-types.md.
 */
`;

/** Builds the synthetic, in-memory OpenAPI 3.1 document openapi-typescript
 * needs, from the frame contract's raw JSON text. Never touches disk. */
export function buildSyntheticOpenApiDoc(framesJsonText) {
  const framesDoc = JSON.parse(framesJsonText);

  const schemas = {};
  for (const [name, def] of Object.entries(framesDoc.inbound?.$defs ?? {})) {
    schemas[name] = def;
  }
  for (const [name, def] of Object.entries(framesDoc.outbound?.$defs ?? {})) {
    schemas[name] = def;
  }
  schemas.InboundFrame = {
    oneOf: framesDoc.inbound?.oneOf,
    discriminator: framesDoc.inbound?.discriminator,
  };
  schemas.OutboundFrame = {
    oneOf: framesDoc.outbound?.oneOf,
    discriminator: framesDoc.outbound?.discriminator,
  };

  // Rewrite every "#/$defs/X" ref to "#/components/schemas/X" now that these
  // schemas are hoisted into an OpenAPI components.schemas object.
  const rewritten = JSON.parse(
    JSON.stringify(schemas).replaceAll("#/$defs/", "#/components/schemas/"),
  );

  return {
    openapi: "3.1.0",
    info: {
      title: framesDoc.protocol ?? "remo-terminal.v1",
      version: String(framesDoc.frame_version ?? 1),
    },
    paths: {},
    components: { schemas: rewritten },
  };
}

/** Reads terminal-frames.json, generates TypeScript, and writes it to
 * `outPath` (defaults to the checked-in path). Returns the generated text. */
export async function generateFrameTypes(outPath = FRAMES_DTS_OUT) {
  const framesJsonText = readFileSync(FRAMES_JSON, "utf8");
  const synthetic = buildSyntheticOpenApiDoc(framesJsonText);

  const ast = await openapiTS(synthetic);
  const body = astToString(ast);
  const text = GENERATED_HEADER + body;

  writeFileSync(outPath, text);
  return text;
}

const isCliEntryPoint = import.meta.url === `file://${process.argv[1]}`;
if (isCliEntryPoint) {
  generateFrameTypes()
    .then(() => {
      console.log(`wrote ${FRAMES_DTS_OUT}`);
    })
    .catch((err) => {
      console.error(err);
      process.exit(1);
    });
}
