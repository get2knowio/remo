# Install fixtures

Nothing is checked in here on purpose. A fixture release is a folder `<tag>/`
(`v9.9.9`, `rc-9.9.9rc1`) holding `manifest.json` in the line-oriented layout of
`specs/031-install-path/contracts/release-manifest.md` plus tiny placeholder
"wheel" files whose sha256 matches the manifest.

`tests/unit/install_fixtures.py` builds these into `tmp_path` at test time
(computing every digest), so no test hard-codes one. It also lays out the
rest of what a fake GitHub needs: `latest/` (what
`releases/latest/download/` serves) and `api/releases.json` (the releases
list). `test_install_script.py` points its `curl` shim at that root through
`REMO_TEST_FIXTURE_ROOT`.

Variants the builder supports: stable, RC, a tampered wheel, a foreign URL,
a tag mismatch, and a missing manifest.
