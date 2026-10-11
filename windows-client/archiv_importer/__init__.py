"""Archiv-Importer: Massenverarbeitung mit lokalem KI-Modell für das Familienarchiv."""

try:
    from ._version import __version__  # beim Build aus dem Git-Tag erzeugt
except ImportError:  # Entwicklung
    __version__ = "0.1.0-dev"
