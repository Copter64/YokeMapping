"""YokeMapping: map a real flight yoke onto the MSFS2024 virtual yoke via a Steam Frame headset."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("yokemapping")
except PackageNotFoundError:  # running from a source tree without an install
    __version__ = "0.0.0+unknown"

__all__ = ["__version__"]
