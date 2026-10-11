"""Einstiegspunkt für PyInstaller und für den Start aus dem Quellcode:  python start.py"""

import os
import sys

if not getattr(sys, "frozen", False):
    # Entwicklung: gemeinsame Bibliothek aus dem Begleitdienst-Ordner verwenden
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "begleitdienst"))

from archiv_importer.__main__ import main  # noqa: E402

if __name__ == "__main__":
    main()
