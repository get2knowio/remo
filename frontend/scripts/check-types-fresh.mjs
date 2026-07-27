#!/usr/bin/env node
// Checks B + C-node (contracts/drift-checks.md §1): REST type freshness, and
// the Node half of the frame-contract freshness check.
//
// REST half (check B): regenerates `src/api/generated/schema.d.ts` from the
// checked-in `src/api/generated/openapi.json` into a TEMPORARY file (never a
// tracked path — R-1/FR-019) using the exact same CLI invocation as the
// `generate:types` npm script, then byte-compares (R-2) the result against
// the checked-in `schema.d.ts`.
//
// Frame half (check C-node, T054): regenerates
// `src/api/generated/terminal-frames.d.ts` from the checked-in
// `terminal-frames.json`, by calling `generateFrameTypes()` from
// `generate-frame-types.mjs` directly (in-process, not shelled out) with a
// temporary output path, then byte-compares against the checked-in file.
//
// Either half mismatching — including a version bump of `openapi-typescript`
// that changes output with no source change (R6/M-6) — is a failure, never a
// silent pass or a semantic diff.
//
// Run standalone: `node scripts/check-types-fresh.mjs` from `frontend/`.
// Wired as `npm run check:types-fresh`.

import { execFileSync } from "node:child_process";
import { mkdtempSync, readFileSync, rmSync, existsSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { generateFrameTypes, FRAMES_JSON } from "./generate-frame-types.mjs";

const __dirname = dirname(fileURLToPath(import.meta.url));
const frontendRoot = join(__dirname, "..");
const repoRoot = join(frontendRoot, "..");

const OPENAPI_JSON = join(frontendRoot, "src", "api", "generated", "openapi.json");
const CHECKED_IN_SCHEMA = join(frontendRoot, "src", "api", "generated", "schema.d.ts");
const CHECKED_IN_FRAMES_DTS = join(
  frontendRoot,
  "src",
  "api",
  "generated",
  "terminal-frames.d.ts",
);
const OPENAPI_TYPESCRIPT_BIN = join(
  frontendRoot,
  "node_modules",
  ".bin",
  process.platform === "win32" ? "openapi-typescript.cmd" : "openapi-typescript",
);

const DOC_LINK = "docs/maintaining-generated-types.md";

/** Thrown to unwind out of `main()` with a failure message, WITHOUT calling
 * `process.exit()` directly — that would skip any enclosing `finally` block
 * (e.g. temp-dir cleanup), silently violating R-1's "checks write only to
 * temporary paths" once you account for cleanup as part of "writing". */
class CheckFailure extends Error {}

function relative(absPath) {
  return absPath.startsWith(repoRoot + "/") ? absPath.slice(repoRoot.length + 1) : absPath;
}

function missingArtifactMessage(path, reason, sourceArtifact = OPENAPI_JSON) {
  return [
    `${relative(path)} is missing or unreadable — cannot check type freshness.`,
    "",
    `  ${reason}`,
    "",
    "To fix: generate the artifact:",
    "",
    "    npm run generate:types",
    "",
    `(run from ${relative(frontendRoot)}; requires a checked-in ` +
      `${relative(sourceArtifact)} — see \`uv run python scripts/export_openapi.py\` ` +
      "if that is also missing).",
    "",
    `See ${DOC_LINK}.`,
  ].join("\n");
}

function staleArtifactMessage() {
  return [
    `${relative(CHECKED_IN_SCHEMA)} is out of sync with ${relative(OPENAPI_JSON)}.`,
    "",
    "  Regenerating from the checked-in openapi.json produced different bytes " +
      "than the checked-in schema.d.ts.",
    "",
    `To fix: regenerate and commit the artifact from ${relative(frontendRoot)}:`,
    "",
    "    npm run generate:types",
    "",
    "If you did not change the API surface, an `openapi-typescript` version " +
      "bump can also cause this with no source change — regenerating and " +
      "committing is still the correct fix.",
    "",
    `See ${DOC_LINK}.`,
  ].join("\n");
}

function staleFramesArtifactMessage() {
  return [
    `${relative(CHECKED_IN_FRAMES_DTS)} is out of sync with ${relative(FRAMES_JSON)}.`,
    "",
    "  Regenerating from the checked-in terminal-frames.json produced different " +
      "bytes than the checked-in terminal-frames.d.ts.",
    "",
    `To fix: regenerate and commit the artifact from ${relative(frontendRoot)}:`,
    "",
    "    npm run generate:types",
    "",
    "If you did not change the frame contract, an `openapi-typescript` version " +
      "bump can also cause this with no source change — regenerating and " +
      "committing is still the correct fix.",
    "",
    `See ${DOC_LINK}.`,
  ].join("\n");
}

function fail(message) {
  throw new CheckFailure(message);
}

async function run() {
  // R-4: missing/unreadable checked-in artifacts fail with a distinct
  // "missing artifact" message, never a diff against empty.
  if (!existsSync(OPENAPI_JSON)) {
    fail(
      missingArtifactMessage(OPENAPI_JSON, "The checked-in OpenAPI source artifact does not exist."),
    );
  }
  if (!existsSync(CHECKED_IN_SCHEMA)) {
    fail(
      missingArtifactMessage(
        CHECKED_IN_SCHEMA,
        "The checked-in generated TypeScript artifact does not exist.",
      ),
    );
  }

  let openapiJsonBytes;
  try {
    openapiJsonBytes = readFileSync(OPENAPI_JSON);
  } catch (err) {
    fail(missingArtifactMessage(OPENAPI_JSON, `Could not read the file: ${err.message}`));
  }
  // Sanity-parse: an unparseable source artifact is a distinct failure, not a
  // downstream diff against whatever openapi-typescript happens to emit for
  // garbage input.
  try {
    JSON.parse(openapiJsonBytes.toString("utf8"));
  } catch (err) {
    fail(missingArtifactMessage(OPENAPI_JSON, `The file is not valid JSON: ${err.message}`));
  }

  let checkedInSchemaBytes;
  try {
    checkedInSchemaBytes = readFileSync(CHECKED_IN_SCHEMA);
  } catch (err) {
    fail(missingArtifactMessage(CHECKED_IN_SCHEMA, `Could not read the file: ${err.message}`));
  }

  // R-1: regenerate into a throwaway temp directory outside the repo tree.
  // Never write to any tracked path.
  const tempDir = mkdtempSync(join(tmpdir(), "remo-check-types-fresh-"));
  try {
    const tempSchema = join(tempDir, "schema.d.ts");

    try {
      execFileSync(OPENAPI_TYPESCRIPT_BIN, [OPENAPI_JSON, "-o", tempSchema], {
        cwd: frontendRoot,
        stdio: ["ignore", "pipe", "pipe"],
      });
    } catch (err) {
      // execFileSync throws on nonzero exit or spawn failure (e.g. missing
      // binary because `npm install` was never run).
      const detail = err.stderr ? err.stderr.toString("utf8") : err.message;
      fail(
        `Failed to run openapi-typescript to check type freshness.\n\n  ${detail}\n\n` +
          `Make sure dependencies are installed (\`npm install\` from ` +
          `${relative(frontendRoot)}), then re-run \`npm run check:types-fresh\`.`,
      );
    }

    let regeneratedBytes;
    try {
      regeneratedBytes = readFileSync(tempSchema);
    } catch (err) {
      fail(
        `openapi-typescript did not produce an output file at the expected temp ` +
          `path (${err.message}). This is a generator failure, not a type-freshness ` +
          `finding — see ${DOC_LINK}.`,
      );
    }

    // R-2: exact byte comparison, not a semantic/structural diff.
    if (!regeneratedBytes.equals(checkedInSchemaBytes)) {
      fail(staleArtifactMessage());
    }
  } finally {
    rmSync(tempDir, { recursive: true, force: true });
  }

  console.log(`${relative(CHECKED_IN_SCHEMA)} is up to date with ${relative(OPENAPI_JSON)}.`);

  // -------------------------------------------------------------------
  // Check C-node (frame contract freshness, T054): same R-1/R-2/R-4 rules,
  // applied to terminal-frames.d.ts vs. the checked-in terminal-frames.json.
  // -------------------------------------------------------------------

  if (!existsSync(FRAMES_JSON)) {
    fail(
      missingArtifactMessage(
        FRAMES_JSON,
        "The checked-in frame contract source artifact does not exist.",
        FRAMES_JSON,
      ),
    );
  }
  if (!existsSync(CHECKED_IN_FRAMES_DTS)) {
    fail(
      missingArtifactMessage(
        CHECKED_IN_FRAMES_DTS,
        "The checked-in generated frame TypeScript artifact does not exist.",
        FRAMES_JSON,
      ),
    );
  }

  let framesJsonBytes;
  try {
    framesJsonBytes = readFileSync(FRAMES_JSON);
  } catch (err) {
    fail(missingArtifactMessage(FRAMES_JSON, `Could not read the file: ${err.message}`, FRAMES_JSON));
  }
  try {
    JSON.parse(framesJsonBytes.toString("utf8"));
  } catch (err) {
    fail(
      missingArtifactMessage(FRAMES_JSON, `The file is not valid JSON: ${err.message}`, FRAMES_JSON),
    );
  }

  let checkedInFramesDtsBytes;
  try {
    checkedInFramesDtsBytes = readFileSync(CHECKED_IN_FRAMES_DTS);
  } catch (err) {
    fail(
      missingArtifactMessage(
        CHECKED_IN_FRAMES_DTS,
        `Could not read the file: ${err.message}`,
        FRAMES_JSON,
      ),
    );
  }

  // R-1: regenerate into a throwaway temp path outside the repo tree, using
  // the in-process generator function directly (not shelled out) — never
  // write to any tracked path.
  const framesTempDir = mkdtempSync(join(tmpdir(), "remo-check-frame-types-fresh-"));
  try {
    const tempFramesDts = join(framesTempDir, "terminal-frames.d.ts");

    let regeneratedFramesText;
    try {
      regeneratedFramesText = await generateFrameTypes(tempFramesDts);
    } catch (err) {
      fail(
        `Failed to run openapi-typescript (via generate-frame-types.mjs) to check ` +
          `frame type freshness.\n\n  ${err.message}\n\n` +
          `Make sure dependencies are installed (\`npm install\` from ` +
          `${relative(frontendRoot)}), then re-run \`npm run check:types-fresh\`.`,
      );
    }

    // R-2: exact byte comparison, not a semantic/structural diff.
    if (!Buffer.from(regeneratedFramesText).equals(checkedInFramesDtsBytes)) {
      fail(staleFramesArtifactMessage());
    }
  } finally {
    rmSync(framesTempDir, { recursive: true, force: true });
  }

  console.log(`${relative(CHECKED_IN_FRAMES_DTS)} is up to date with ${relative(FRAMES_JSON)}.`);
}

try {
  await run();
} catch (err) {
  if (err instanceof CheckFailure) {
    console.error("\n" + err.message + "\n");
    process.exit(1);
  }
  throw err;
}
