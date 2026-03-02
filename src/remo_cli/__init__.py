"""remo - Remote development environment CLI."""

from importlib.metadata import version, PackageNotFoundError

try:
    __version__ = version("remo-cli")
except PackageNotFoundError:
    __version__ = "0.0.0-dev"
