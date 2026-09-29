# Deferred work (spec 027) — filed as GitHub issues at PR time

## 1. Route CLI warnings to stderr consistently (discovery warnings included)

`core/output.print_warning` prints to stdout; the plugin-discovery warnings added in 027 use it for consistency with every other CLI warning. The provider-plugins prompt asked for stderr. Moving *all* warnings to stderr at once (with a test that no warning reaches stdout) is a separate, behavior-visible change: scripts that parse `remo … --json`-style output today would otherwise see warnings interleaved.

## 2. `core/known_hosts.get_aws_region` still names `aws`

Pre-existing (018): `get_known_hosts(type_filter="aws")` inside a core helper used by the AWS proxy hook. It is outside 027's de-literalized set (`registry.py`, `reconcile.py`, `provider_plugins.py`, `provider_registry.py`, which `tests/unit/test_architecture_provider_literals.py` now gates) because moving it means changing the AWS proxy hook's call surface. Move it behind the descriptor (e.g. a `default_region` resolver on `ConnectionSpec`) and add `known_hosts.py` to the gate.

## 3. Tier 2 validation of entry-point discovery on a CI-built wheel

Entry points are package metadata (Constitution IX: a packaging surface). Before the release that ships `PROVIDER_API_VERSION` 1: run `gh workflow run dev-build.yml`, install the wheel plus the fixture distribution (`tests/fixtures/remo_fixture_provider`) into a fresh venv, and confirm `remo providers` lists `fixture` and `remo fixture --help` works.

## 4. A public connection extension point for non-SSH providers

The `Provider`/`ConnectionSpec` surface is SSH-shaped (`proxy_hook` returns an SSH `ProxyCommand` plan; `remo shell`, discovery and terminals all speak SSH). A plugin whose instances are not reachable over SSH would need a public seam for its transport. Out of 027's scope (the queue row's summary notes it); record the design question here.
