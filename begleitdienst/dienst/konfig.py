"""Konfiguration: aus /data/options.json (Home-Assistant-Add-on) und Umgebungsvariablen.

Jede Einstellung kann per Umgebungsvariable ARCHIV_<NAME> überschrieben werden,
z. B. ARCHIV_PAPERLESS_URL. Listen werden kommagetrennt angegeben.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, fields


@dataclass
class Konfig:
    paperless_url: str = "http://localhost:8000"
    paperless_token: str = ""
    paperless_oeffentliche_url: str = ""       # für Links in Mails und Oberfläche
    paperless_ssl_pruefen: bool = True         # aus bei selbst signiertem Zertifikat
    api_token: str = ""                        # Zugang für Windows-Client und Home Assistant
    web_benutzer: str = "admin"
    web_passwort: str = ""
    daten_verzeichnis: str = "/data"
    port: int = 8099
    kategorie_praefix: str = "kat:"
    posteingang_tag: str = "Posteingang"
    personen: list[str] = field(default_factory=lambda: ["Familie"])
    # E-Mail-Eingang
    imap_server: str = ""
    imap_port: int = 993
    imap_benutzer: str = ""
    imap_passwort: str = ""
    imap_ordner: str = "INBOX"
    imap_intervall: int = 120
    erlaubte_absender: list[str] = field(default_factory=list)
    # Rückmeldung per Mail (optional)
    smtp_server: str = ""
    smtp_port: int = 587
    smtp_benutzer: str = ""
    smtp_passwort: str = ""
    smtp_absender: str = ""
    rueckmeldung: bool = False
    # Windows-Client-Download
    github_repo: str = ""                      # z. B. "benutzer/familienarchiv"
    github_token: str = ""                     # nur für private Repositories

    @property
    def paperless_link(self) -> str:
        return (self.paperless_oeffentliche_url or self.paperless_url).rstrip("/")


def _umwandeln(typ, wert):
    if typ in (list[str], "list[str]"):
        if isinstance(wert, list):
            return [str(x).strip() for x in wert if str(x).strip()]
        return [x.strip() for x in str(wert).split(",") if x.strip()]
    if typ in (int, "int"):
        return int(wert)
    if typ in (bool, "bool"):
        return wert if isinstance(wert, bool) else str(wert).lower() in ("1", "true", "ja", "yes", "on")
    return str(wert).strip()


def laden(pfad: str = "/data/options.json") -> Konfig:
    k = Konfig()
    werte: dict = {}
    if os.path.exists(pfad):
        with open(pfad, encoding="utf-8") as f:
            werte.update(json.load(f))
    for f in fields(Konfig):
        umgebung = os.environ.get("ARCHIV_" + f.name.upper())
        if umgebung is not None:
            werte[f.name] = umgebung
    for f in fields(Konfig):
        if f.name in werte and werte[f.name] is not None:
            setattr(k, f.name, _umwandeln(f.type, werte[f.name]))
    k.erlaubte_absender = [a.lower() for a in k.erlaubte_absender]
    return k
