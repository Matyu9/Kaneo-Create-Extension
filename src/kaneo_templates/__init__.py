from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("kaneo-templates")
except PackageNotFoundError:
    __version__ = "0.0.0"
