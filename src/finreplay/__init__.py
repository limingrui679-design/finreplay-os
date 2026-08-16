"""FinReplay OS: point-in-time financial-system replay infrastructure."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("finreplay-os")
except PackageNotFoundError:  # pragma: no cover - source checkout before installation
    __version__ = "0.1.0a1"

__all__ = ["__version__"]
