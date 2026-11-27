# Installing remo

One installer, for every release: stable and pre-release alike.

```bash
curl -fsSL https://raw.githubusercontent.com/get2knowio/remo/main/install.sh | bash
```

## How it works

Every GitHub release carries the same artifact set: the `remo-cli` wheel, one
wheel per first-party provider plugin, and a `manifest.json` listing each wheel
with its SHA-256. The installer:

1. picks the release (the latest stable one, `--version`, or a pre-release);
2. downloads that release's `manifest.json` and refuses anything whose URLs do
   not live under that release;
3. downloads the wheels you asked for and verifies each digest (`sha256sum`, or
   `shasum -a 256` on macOS; with neither it stops);
4. installs from those verified files in one `uv tool install` (or `pipx install`
   plus one `pipx inject` per addition);
5. records what it installed in `install.json` (below).

If a digest does not match, nothing is installed.

## Channels

| You want | Command (after `\| bash -s --`) |
|---|---|
| Latest stable | (nothing) |
| A specific stable release | `--version 5.0.0` |
| Newest pre-release | `--prerelease` |
| A specific pre-release | `--prerelease 5.0.0rc1` (or `--version 5.0.0rc1`) |

Stable releases are tagged `v<version>`; pre-releases `rc-<version>`. GitHub
Releases is the only channel: no remo release, stable or pre-release, plugin or
not, is published to PyPI.

A release appears on GitHub a few minutes before its wheels and `manifest.json`
are attached. If the newest stable release has no manifest yet, the installer
asks GitHub's API (`https://api.github.com/repos/get2knowio/remo/releases?per_page=20`)
for the newest stable release that has one, installs that, and says which release
is still being published; run it again later to get the newer one. `--prerelease`
picks the newest pre-release that has a manifest the same way. A version you name
with `--version` or `--prerelease <version>` is never swapped for another: if it
has no manifest, the installer stops. `remo self-update` and the update notice
follow the same rule, so neither offers a release that cannot be installed yet.

Both lookups use GitHub's API, which allows 60 unauthenticated requests per hour per IP; if that is exhausted the
installer says so and you can name the version instead.

## `--with`: web and plugins

```bash
curl -fsSL https://raw.githubusercontent.com/get2knowio/remo/main/install.sh \
  | bash -s -- --with ec2,web
```

- `web` is the browser console (the `remo-cli[web]` extra).
- Every other name is a provider plugin listed in the release's manifest today:
  `ec2` and `hetzner`. The accepted names come from the manifest, so a new
  first-party plugin needs no installer change. An unknown name stops the run
  before anything is downloaded and lists the accepted names.
- `--with` is repeatable and comma-separated; `REMO_WITH` is its environment twin
  (a flag wins).

## Additive, remembered installs (`install.json`)

After a successful install the installer writes `~/.config/remo/install.json`
(under `$REMO_HOME` if set, else `$XDG_CONFIG_HOME/remo`): the repo, channel,
version, installer and the sets of extras, plugins and third-party additions.

A later run installs the union of what is recorded and what you ask for, so
`--with hetzner` on a machine that already has `ec2,web` ends with all three.
`--without NAME` (or `REMO_WITHOUT`) removes an entry; removing something that is
not installed is a note, not an error. Re-running the same set changes nothing but
the timestamp. A dry run or a failed install never writes the record, and an
unreadable record is ignored with a warning.

## Third-party plugins

```bash
curl -fsSL https://raw.githubusercontent.com/get2knowio/remo/main/install.sh \
  | bash -s -- --with-pkg some-remo-plugin==1.2 \
      --with-url https://example.com/other_plugin-1.0-py3-none-any.whl
```

`--with-pkg` installs from an index; `--with-url` takes an https wheel URL. Both
are passed to uv/pipx as-is (they are not checksummed by remo), recorded, and kept
by later runs. Values may only contain letters, digits and `._:/@+=,~%-`.

## Keeping up to date

```bash
remo self-update            # newest release on your channel, same set
remo self-update --check    # only report
remo self-update --yes      # no confirmation prompt
```

For a recorded install, `self-update` fetches `install.sh` from the release it is
moving to (`https://raw.githubusercontent.com/get2knowio/remo/<tag>/install.sh`)
and runs it with your recorded `--with`, `--with-pkg` and `--with-url`, so the
installer always matches the manifest format of the release it installs. The
passive "update available" notice after a command follows the same channel. A
source checkout is told to `git pull && uv sync`.

## Verifying a download

Beyond the manifest's digests, each wheel is attested with GitHub build
provenance:

```bash
gh attestation verify remo_cli-5.0.0-py3-none-any.whl -R get2knowio/remo
```

## Compatibility

The manifest pins plugins to the build they were released with, so an installer
run gives a coherent set. The runtime guard is unchanged: each plugin declares a
`REMO_PROVIDER_API_VERSION`, and remo skips a plugin whose version it cannot host,
with a reason in `remo providers`.

## Switching from a PyPI install

Earlier versions of `remo-cli` were published on PyPI. PyPI no longer receives
remo releases: the last version there stays frozen, so `uv tool upgrade remo-cli`
or `pipx upgrade remo-cli` will never find anything newer, and
`remo self-update` cannot move a PyPI install. A PyPI install still tells you
after a command when a newer GitHub release exists, and points here.

Run the one-liner once; it replaces the PyPI copy (and any copy under the other
of uv/pipx) after the new one is in place, and from then on `remo self-update`
keeps you current:

```bash
curl -fsSL https://raw.githubusercontent.com/get2knowio/remo/main/install.sh \
  | bash -s -- --with ec2,web
```

Choose the `--with` list from what you had (`remo providers` shows the plugins).

## Notes

- Bash 3.2 (stock macOS) is enough; the installer needs no `jq` or `python3`.
- Parity with try-hola/hola's `cli-install.sh`: `--version`, `--prerelease`
  (alias `--pre-release`), `REMO_VERSION`, `REMO_PRERELEASE`, `REMO_REPO_SLUG`.
- Windows shells (Git Bash, MSYS2, Cygwin) are refused; use WSL2.
- `--dry-run` prints the exact command(s), with manifest URLs in place of local
  files, and downloads and writes nothing.
