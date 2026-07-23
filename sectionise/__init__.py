from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("sectionise")
except PackageNotFoundError:  # running from a source tree with no installed dist
    __version__ = "0+unknown"

__all__ = ["__version__"]
