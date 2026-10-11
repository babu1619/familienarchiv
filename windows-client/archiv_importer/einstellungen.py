"""Einstellungen des Clients. Das API-Token liegt – wenn verfügbar – in der Windows-Anmeldeinformationsverwaltung."""

from __future__ import annotations

import json
import logging
import os
import shutil
import sys
from dataclasses import asdict, dataclass, fields

log = logging.getLogger(__name__)

try:
    import keyring  # Windows: Anmeldeinformationsverwaltung
except Exception:  # pragma: no cover - optional
    keyring = None

KEYRING_DIENST = "ArchivImporter"


def datenverzeichnis() -> str:
    basis = os.environ.get("ARCHIV_IMPORTER_DATEN")
    if not basis:
        if sys.platform == "win32":
            basis = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "ArchivImporter")
        else:
            basis = os.path.join(os.path.expanduser("~"), ".archiv_importer")
    os.makedirs(basis, exist_ok=True)
    return basis


def tesseract_finden() -> str:
    for kandidat in (shutil.which("tesseract"),
                     r"C:\Program Files\Tesseract-OCR\tesseract.exe",
                     r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
                     os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "Tesseract-OCR", "tesseract.exe")):
        if kandidat and os.path.exists(kandidat):
            return kandidat
    return ""


@dataclass
class Einstellungen:
    server_url: str = ""
    api_token: str = ""
    ki_aktiv: bool = True
    ollama_url: str = "http://127.0.0.1:11434"
    modell: str = "qwen2.5:3b"
    ki_timeout: int = 600
    ki_max_zeichen: int = 6000
    tesseract_pfad: str = ""
    ocr_sprache: str = "deu"
    nachtmodus: bool = True
    sichere_automatisch_uebergeben: bool = False

    def vollstaendig(self) -> bool:
        return bool(self.server_url and self.api_token)

    def oeffentlich(self) -> dict:
        d = asdict(self)
        d["api_token"] = "••••••" if self.api_token else ""
        return d


class EinstellungsSpeicher:
    def __init__(self, verzeichnis: str | None = None):
        self.pfad = os.path.join(verzeichnis or datenverzeichnis(), "einstellungen.json")

    def laden(self) -> Einstellungen:
        e = Einstellungen()
        if os.path.exists(self.pfad):
            with open(self.pfad, encoding="utf-8") as f:
                daten = json.load(f)
            for feld in fields(Einstellungen):
                if feld.name in daten:
                    setattr(e, feld.name, daten[feld.name])
        if keyring and not e.api_token:
            try:
                e.api_token = keyring.get_password(KEYRING_DIENST, "api_token") or ""
            except Exception as exc:
                log.debug("Keyring nicht lesbar: %s", exc)
        if not e.tesseract_pfad:
            e.tesseract_pfad = tesseract_finden()
        return e

    def speichern(self, e: Einstellungen) -> None:
        daten = asdict(e)
        if keyring and e.api_token:
            try:
                keyring.set_password(KEYRING_DIENST, "api_token", e.api_token)
                daten["api_token"] = ""
            except Exception as exc:
                log.warning("Token nicht in der Anmeldeinformationsverwaltung gespeichert: %s", exc)
        with open(self.pfad, "w", encoding="utf-8") as f:
            json.dump(daten, f, indent=2, ensure_ascii=False)

    def aktualisieren(self, e: Einstellungen, neu: dict) -> Einstellungen:
        for feld in fields(Einstellungen):
            if feld.name not in neu:
                continue
            wert = neu[feld.name]
            if feld.name == "api_token" and (wert in ("", None) or set(str(wert)) == {"•"}):
                continue  # unverändert lassen
            typ = feld.type if isinstance(feld.type, str) else feld.type.__name__
            if typ == "bool":
                wert = bool(wert)
            elif typ == "int":
                wert = int(wert)
            else:
                wert = str(wert).strip()
            setattr(e, feld.name, wert)
        e.server_url = e.server_url.rstrip("/")
        e.ollama_url = e.ollama_url.rstrip("/")
        self.speichern(e)
        return e
