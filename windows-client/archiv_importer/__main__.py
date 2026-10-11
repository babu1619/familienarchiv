"""Start des Archiv-Importers.

    ArchivImporter.exe                     Oberfläche öffnen
    ArchivImporter.exe --browser           Oberfläche im Standardbrowser statt im eigenen Fenster
    ArchivImporter.exe --modelltest ORDNER Modell an bestätigten Belegen messen (siehe modelltest.py)
"""

from __future__ import annotations

import argparse
import logging
import os
import secrets
import socket
import sys
import threading
import time
import webbrowser

import uvicorn

from . import __version__
from .einstellungen import EinstellungsSpeicher, datenverzeichnis
from .oberflaeche import erstelle_app
from .speicher import Speicher
from .verarbeitung import Verarbeitung


def _freier_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _logging_einrichten() -> None:
    pfad = os.path.join(datenverzeichnis(), "importer.log")
    handler: list[logging.Handler] = [logging.FileHandler(pfad, encoding="utf-8")]
    if sys.stderr:
        handler.append(logging.StreamHandler())
    logging.basicConfig(level=logging.INFO, handlers=handler,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")


class FensterApi:
    """Wird im eigenen Fenster (pywebview) für native Auswahldialoge genutzt."""

    def __init__(self):
        self.fenster = None

    def _dialog(self, art: str, mehrere: bool = False):
        import webview
        konstanten = getattr(webview, "FileDialog", None)
        typ = getattr(konstanten, art, None) if konstanten else getattr(webview, f"{art}_DIALOG")
        kwargs = {"allow_multiple": mehrere}
        if art == "OPEN":
            kwargs["file_types"] = ("Belege (*.pdf;*.jpg;*.jpeg;*.png;*.tif;*.tiff)", "Alle Dateien (*.*)")
        ergebnis = self.fenster.create_file_dialog(typ, **kwargs)
        return list(ergebnis or [])

    def ordner_waehlen(self):
        return self._dialog("FOLDER")

    def dateien_waehlen(self):
        return self._dialog("OPEN", mehrere=True)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="ArchivImporter")
    parser.add_argument("--browser", action="store_true", help="im Standardbrowser öffnen")
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--modelltest", metavar="ORDNER")
    parser.add_argument("--version", action="version", version=__version__)
    args = parser.parse_args(argv)
    _logging_einrichten()

    einst = EinstellungsSpeicher()
    e = einst.laden()
    if args.modelltest:
        from .modelltest import ausfuehren
        sys.exit(ausfuehren(args.modelltest, e))

    speicher = Speicher(os.path.join(datenverzeichnis(), "arbeitsstand.sqlite"))
    verarbeitung = Verarbeitung(speicher, e)
    verarbeitung.starten()

    token = secrets.token_urlsafe(24)
    port = args.port or _freier_port()
    app = erstelle_app(verarbeitung, speicher, einst, token)
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_config=None, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    while not server.started:
        time.sleep(0.05)
    url = f"http://127.0.0.1:{port}/?t={token}"
    logging.info("Archiv-Importer %s läuft auf Port %s", __version__, port)

    try:
        if args.browser:
            raise ImportError
        import webview
    except ImportError:
        webbrowser.open(url)
        print(f"Oberfläche: {url}\nBeenden mit Strg+C")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            pass
    else:
        fenster_api = FensterApi()
        fenster = webview.create_window(f"Archiv-Importer {__version__}", url, js_api=fenster_api,
                                        width=1360, height=860, min_size=(900, 600))
        fenster_api.fenster = fenster
        webview.start()
    verarbeitung.stoppen()
    server.should_exit = True


if __name__ == "__main__":
    main()
