"""Plausibilitätsprüfung: entscheidet je Feld zwischen "sicher" und "prüfen".

Die Selbsteinschätzung eines Sprachmodells reicht dafür nicht. Deshalb wird
gegen den Text und gegen einfache Rechenregeln geprüft.
"""

from __future__ import annotations

import difflib
import re
from datetime import date, timedelta

from .felder import QUELLE_ERECHNUNG, QUELLE_KI, QUELLE_MANUELL, QUELLE_VORGABE, Ergebnis
from .muster import betrag_formatieren, daten_in, iban_gueltig

_VERTRAUT = (QUELLE_ERECHNUNG, QUELLE_MANUELL, QUELLE_VORGABE)


def _norm(s: str) -> str:
    return re.sub(r"\s+", "", s or "")


def betrag_im_text(wert: float, text: str) -> bool:
    t = _norm(text)
    varianten = {
        betrag_formatieren(abs(wert)),                       # 1.284,50
        betrag_formatieren(abs(wert)).replace(".", ""),      # 1284,50
        f"{abs(wert):.2f}",                                  # 1284.50
        f"{abs(wert):,.2f}",                                 # 1,284.50
    }
    return any(v in t for v in varianten)


def aehnlichster(name: str, liste: list[str]) -> str | None:
    if not name or not liste:
        return None
    treffer = difflib.get_close_matches(name.lower(), [x.lower() for x in liste], n=1, cutoff=0.6)
    if not treffer:
        return None
    return next(x for x in liste if x.lower() == treffer[0])


def in_liste(name: str, liste: list[str]) -> str | None:
    for x in liste:
        if x.lower() == (name or "").strip().lower():
            return x
    return None


def pruefen(e: Ergebnis, text: str = "", kategorien: list[str] | None = None,
            dokumenttypen: list[str] | None = None, korrespondenten: list[str] | None = None,
            heute: date | None = None) -> Ergebnis:
    heute = heute or date.today()
    text = text or e.text
    pr: list[dict] = [p for p in e.pruefungen if p.get("feld") is None and p.get("ok")]  # z. B. E-Rechnung-Hinweis

    def merke(feld: str, ok: bool, satz: str) -> None:
        pr.append({"feld": feld, "ok": ok, "text": satz})
        if not ok and feld in e.felder and e.felder[feld].quelle not in (QUELLE_MANUELL, QUELLE_VORGABE):
            e.felder[feld].sicher = False

    brutto = e.wert("betrag_brutto")
    netto = e.wert("betrag_netto")
    mwst = e.wert("mwst")

    if isinstance(brutto, (int, float)):
        im_text = True
        if text.strip() and e.felder["betrag_brutto"].quelle not in _VERTRAUT:
            im_text = betrag_im_text(brutto, text)
            merke("betrag_brutto", im_text, "Betrag steht wörtlich im Text")
        if isinstance(netto, (int, float)) and isinstance(mwst, (int, float)):
            ok = abs(round(netto + mwst, 2) - round(brutto, 2)) <= 0.02
            merke("betrag_brutto", ok, "Netto + MwSt = Brutto")
            if ok:
                for f in ("betrag_netto", "mwst"):
                    e.felder[f].sicher = True
                if im_text:  # zwei unabhängige Belege für denselben Betrag
                    e.felder["betrag_brutto"].sicher = True
            else:
                for f in ("betrag_netto", "mwst"):
                    e.felder[f].sicher = False
        if isinstance(brutto, (int, float)) and brutto == 0:
            merke("betrag_brutto", False, "Betrag ist 0")

    ak = e.wert("arbeitskosten")
    if isinstance(ak, (int, float)) and isinstance(brutto, (int, float)):
        merke("arbeitskosten", 0 < ak <= abs(brutto), "Arbeitskosten nicht höher als der Gesamtbetrag")

    datum = e.wert("datum")
    if datum:
        try:
            d = date.fromisoformat(str(datum))
            merke("datum", date(1990, 1, 1) <= d <= heute + timedelta(days=30), "Datum liegt im plausiblen Bereich")
            if e.felder["datum"].quelle == QUELLE_KI and text.strip():
                merke("datum", d.isoformat() in daten_in(text), "Datum steht im Text")
        except ValueError:
            merke("datum", False, "Datum ist lesbar")
    faellig = e.wert("faelligkeit")
    if faellig and datum:
        try:
            merke("faelligkeit", date.fromisoformat(str(faellig)) >= date.fromisoformat(str(datum)),
                  "Fälligkeit liegt nicht vor dem Belegdatum")
        except ValueError:
            merke("faelligkeit", False, "Fälligkeit ist lesbar")

    iban = e.wert("iban")
    if iban:
        merke("iban", iban_gueltig(str(iban)), "IBAN-Prüfsumme stimmt")

    kat = e.wert("kategorie")
    if kat and kategorien is not None:
        treffer = in_liste(kat, kategorien)
        if treffer:
            e.felder["kategorie"].wert = treffer
            merke("kategorie", True, "Kategorie existiert auf dem Server")
        else:
            vorschlag = aehnlichster(kat, kategorien)
            merke("kategorie", False, f"Kategorie „{kat}“ unbekannt" + (f" – meinten Sie „{vorschlag}“?" if vorschlag else ""))
            if vorschlag:
                e.felder["kategorie"].hinweis = f"Vorschlag: {vorschlag}"

    typ = e.wert("dokumenttyp")
    if typ and dokumenttypen is not None:
        treffer = in_liste(typ, dokumenttypen)
        if treffer:
            e.felder["dokumenttyp"].wert = treffer
        else:
            merke("dokumenttyp", False, f"Dokumenttyp „{typ}“ unbekannt")

    absender = e.wert("absender")
    if absender and korrespondenten is not None:
        treffer = in_liste(absender, korrespondenten)
        if treffer:
            e.felder["absender"].wert = treffer
        elif e.felder["absender"].quelle not in _VERTRAUT:
            im_text = absender.lower() in (text or "").lower()
            pr.append({"feld": "absender", "ok": True,
                       "text": "Absender ist neu und wird angelegt" + ("" if im_text else " (nicht wörtlich im Text)")})
            if not im_text:
                e.felder["absender"].sicher = False

    e.pruefungen = pr
    return e
