#!/usr/bin/env node
// Copies the ghostty-web WASM asset(s) into frontend/public/ so they are
// served same-origin (never from a CDN) per FR-038 (see research.md R6).
//
// This is a placeholder: `ghostty-web` is not yet installed in this
// sandbox (no npm network access). Once `npm install` has actually been
// run in a networked environment, this script locates the WASM/asset
// output shipped by the `ghostty-web` package and copies it here as a
// prebuild step.

import { existsSync, mkdirSync, readdirSync, statSync, copyFileSync } from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = dirname(fileURLToPath(import.meta.url));
const frontendRoot = join(__dirname, "..");
const packageDir = join(frontendRoot, "node_modules", "ghostty-web");
const publicDir = join(frontendRoot, "public");

/**
 * Recursively find files matching a predicate under a directory.
 * @param {string} dir
 * @param {(name: string) => boolean} predicate
 * @returns {string[]}
 */
function findFiles(dir, predicate) {
  const matches = [];
  let entries;
  try {
    entries = readdirSync(dir);
  } catch {
    return matches;
  }
  for (const entry of entries) {
    const fullPath = join(dir, entry);
    let stats;
    try {
      stats = statSync(fullPath);
    } catch {
      continue;
    }
    if (stats.isDirectory()) {
      matches.push(...findFiles(fullPath, predicate));
    } else if (predicate(entry)) {
      matches.push(fullPath);
    }
  }
  return matches;
}

function main() {
  if (!existsSync(packageDir)) {
    console.error(
      "[copy-ghostty-wasm] ghostty-web is not installed (expected at " +
        `${packageDir}). Run "npm install" first. Skipping WASM copy — ` +
        "this is expected in environments without npm network access.",
    );
    process.exit(1);
  }

  const wasmFiles = findFiles(packageDir, (name) => name.endsWith(".wasm"));

  if (wasmFiles.length === 0) {
    console.error(
      "[copy-ghostty-wasm] No .wasm assets found under " +
        `${packageDir}. The ghostty-web package layout may have changed — ` +
        "update this script's search logic.",
    );
    process.exit(1);
  }

  mkdirSync(publicDir, { recursive: true });

  for (const src of wasmFiles) {
    const destName = src.slice(packageDir.length + 1).replace(/[\\/]/g, "__");
    const dest = join(publicDir, destName);
    copyFileSync(src, dest);
    console.log(`[copy-ghostty-wasm] copied ${src} -> ${dest}`);
  }

  console.log(
    `[copy-ghostty-wasm] done: ${wasmFiles.length} asset(s) copied to ${publicDir}`,
  );
}

main();
