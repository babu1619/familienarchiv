"""E-Mail-Kurzsyntax: Kategorie, Tags und Person im Betreff oder im Mailtext.

Betreff:  Zahnarzt Kontrolle @Gesundheit/Arzt #steuer-2026 #erstattung +Lisa !pruefen
Mailtext (Zeilen am Anfang):
    Kategorie: Wohnen/Nebenkosten/Strom
    Tags: steuer-2026, jahresabrechnung
    Person: Familie
    Typ: Rechnung
    Notiz: Abrechnung 2025
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_PRAEFIXE = re.compile(r"^\s*((re|aw|fw|fwd|wg|wtr)\s*:\s*)+", re.IGNORECASE)
_TOKEN = re.compile(r"(?<!\S)([@#+!])(\"[^\"]+\"|\S+)")
_ZEILE = re.compile(r"^\s*(kategorie|tags?|person|typ|dokumenttyp|notiz|titel)\s*:\s*(.*)$",
                    re.IGNORECASE)


@dataclass
class Vorgaben:
    titel: str = ""
    kategorie: str = ""
    tags: list[str] = field(default_factory=list)
    person: str = ""
    dokumenttyp: str = ""
    notiz: str = ""
    pruefen: bool = False

    def leer(self) -> bool:
        return not (self.kategorie or self.tags or self.person or self.dokumenttyp or self.pruefen)


def _kategorie_normalisieren(text: str) -> str:
    teile = [t.strip().replace("_", " ") for t in re.split(r"\s*/\s*", text.strip().strip('"'))]
    return "/".join(t for t in teile if t)


def betreff_auswerten(betreff: str) -> Vorgaben:
    v = Vorgaben()
    betreff = _PRAEFIXE.sub("", betreff or "")
    rest = betreff
    for m in _TOKEN.finditer(betreff):
        zeichen, wert = m.group(1), m.group(2).strip('"')
        if zeichen == "@":
            v.kategorie = _kategorie_normalisieren(wert)
        elif zeichen == "#":
            tag = wert.strip(",;").lower()
            if tag and tag not in v.tags:
                v.tags.append(tag)
        elif zeichen == "+":
            v.person = wert.strip(",;")
        elif zeichen == "!":
            if wert.lower() in ("pruefen", "prüfen", "check"):
                v.pruefen = True
            else:
                continue  # unbekanntes Steuerwort bleibt im Titel
        rest = rest.replace(m.group(0), " ", 1)
    v.titel = re.sub(r"\s+", " ", rest).strip(" -–:")
    return v


def mailtext_auswerten(text: str) -> tuple[Vorgaben, str]:
    """Liest Steuerzeilen am Anfang des Mailtexts. Gibt Vorgaben und Resttext zurück."""
    v = Vorgaben()
    zeilen = (text or "").splitlines()
    i = 0
    while i < len(zeilen) and not zeilen[i].strip():
        i += 1
    while i < len(zeilen):
        m = _ZEILE.match(zeilen[i])
        if not m:
            break
        schluessel, wert = m.group(1).lower(), m.group(2).strip()
        if schluessel == "kategorie":
            v.kategorie = _kategorie_normalisieren(wert)
        elif schluessel in ("tag", "tags"):
            for t in re.split(r"[,;]", wert):
                t = t.strip().lstrip("#").lower()
                if t and t not in v.tags:
                    v.tags.append(t)
        elif schluessel == "person":
            v.person = wert
        elif schluessel in ("typ", "dokumenttyp"):
            v.dokumenttyp = wert
        elif schluessel == "notiz":
            v.notiz = wert
        elif schluessel == "titel":
            v.titel = wert
        i += 1
    return v, "\n".join(zeilen[i:]).strip()


def zusammenfuehren(betreff: Vorgaben, text: Vorgaben) -> Vorgaben:
    """Betreff hat Vorrang, Mailtext ergänzt."""
    v = Vorgaben(
        titel=betreff.titel or text.titel,
        kategorie=betreff.kategorie or text.kategorie,
        person=betreff.person or text.person,
        dokumenttyp=betreff.dokumenttyp or text.dokumenttyp,
        notiz=text.notiz,
        pruefen=betreff.pruefen or text.pruefen,
    )
    for t in betreff.tags + text.tags:
        if t not in v.tags:
            v.tags.append(t)
    return v
