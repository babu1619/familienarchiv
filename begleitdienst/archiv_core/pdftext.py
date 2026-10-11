"""Text und eingebettete Dateien aus PDFs lesen (ohne OCR)."""

from __future__ import annotations

import io
import logging

from pypdf import PdfReader

log = logging.getLogger(__name__)


def ist_pdf(daten: bytes) -> bool:
    return daten[:5] == b"%PDF-"


def text_lesen(daten: bytes, max_seiten: int = 6) -> str:
    """Liest den Textlayer der ersten Seiten. Leer, wenn keiner vorhanden ist."""
    try:
        reader = PdfReader(io.BytesIO(daten))
        if reader.is_encrypted:
            try:
                ok = reader.decrypt("")  # nur ein Besitzer-Passwort lässt sich so öffnen
            except Exception:
                ok = 0
            if not ok:
                raise PasswortGeschuetzt()
        teile = []
        for seite in reader.pages[:max_seiten]:
            teile.append(seite.extract_text() or "")
        return "\n".join(teile).strip()
    except PasswortGeschuetzt:
        raise
    except Exception as exc:  # defekte PDFs nicht durchreichen
        log.warning("PDF-Text nicht lesbar: %s", exc)
        return ""


def seitenzahl(daten: bytes) -> int:
    try:
        return len(PdfReader(io.BytesIO(daten)).pages)
    except Exception:
        return 0


def anhaenge_lesen(daten: bytes) -> dict[str, bytes]:
    """Eingebettete Dateien (z. B. factur-x.xml) als {name: inhalt}."""
    try:
        reader = PdfReader(io.BytesIO(daten))
        ergebnis = {}
        for name, inhalte in (reader.attachments or {}).items():
            if inhalte:
                ergebnis[name] = inhalte[0]
        return ergebnis
    except Exception as exc:
        log.debug("Keine Anhänge lesbar: %s", exc)
        return {}


class PasswortGeschuetzt(Exception):
    """Das PDF ist verschlüsselt und ohne Passwort nicht lesbar."""
