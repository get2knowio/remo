# public/

The `ghostty-web` WASM asset (from the pinned `ghostty-web@0.4.0` npm
package's `dist/wasm` output) must be copied here at build time so it is
served same-origin — never from a CDN — per FR-038.

This copy step will be wired into the Docker build stage (see
`docker/Dockerfile`) and/or the `prebuild` npm script
(`scripts/copy-ghostty-wasm.mjs`) once `npm install` is actually run in a
networked environment. Until then this directory only contains this README.
