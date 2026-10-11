"""Startpunkt: python -m dienst"""

from __future__ import annotations

import logging
import os
import threading
import time

import uvicorn

from .db import Datenbank
from .downloads import Downloads
from .importer import Importer
from .konfig import laden
from .mail import MailEingang, smtp_versender
from .paperless import Paperless, PaperlessFehler
from .web import erstelle_app


def _benutzerfelder_einrichten(p: Paperless, personen: list[str]) -> None:
    """Paperless startet evtl. später – so lange versuchen, bis es klappt."""
    log = logging.getLogger("dienst")
    for versuch in range(60):
        try:
            p.benutzerfelder_sicherstellen(personen)
            log.info("Benutzerfelder in Paperless geprüft")
            return
        except PaperlessFehler as exc:
            log.warning("Paperless noch nicht bereit (%s) – neuer Versuch in 30 s", exc)
            time.sleep(30)


def main() -> None:
    logging.basicConfig(level=os.environ.get("ARCHIV_LOGLEVEL", "INFO"),
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    k = laden(os.environ.get("ARCHIV_OPTIONEN", "/data/options.json"))
    os.makedirs(k.daten_verzeichnis, exist_ok=True)
    db = Datenbank(os.path.join(k.daten_verzeichnis, "begleitdienst.sqlite"))
    p = Paperless(k.paperless_url, k.paperless_token)
    importer = Importer(k, db, p)
    if k.rueckmeldung and k.smtp_server:
        importer.versender = smtp_versender(k)
    importer.starten()
    threading.Thread(target=_benutzerfelder_einrichten, args=(p, k.personen), daemon=True).start()
    mail = None
    if k.imap_server:
        mail = MailEingang(k, db, importer)
        mail.starten()
    downloads = Downloads(k.daten_verzeichnis, k.github_repo, k.github_token)
    app = erstelle_app(k, db, p, importer, downloads, mail)
    uvicorn.run(app, host="0.0.0.0", port=k.port, log_level="warning", proxy_headers=True)


if __name__ == "__main__":
    main()
