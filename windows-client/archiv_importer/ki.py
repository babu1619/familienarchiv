"""Lokales Sprachmodell über Ollama: füllt nur die Felder, die die festen Verfahren offen lassen.

Modell und Adresse sind reine Einstellungen – ein stärkerer Rechner bekommt
einfach ein größeres Modell, am Programm ändert sich nichts.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import date

import requests

from archiv_core.muster import betrag_parsen, daten_in

log = logging.getLogger(__name__)

SYSTEM = (
    "Du liest deutsche Belege (Rechnungen, Kassenbons, Verträge, Bescheide, Briefe) und gibst nur die "
    "verlangten Felder als JSON zurück. Erfinde nichts: Steht ein Wert nicht im Text, bleibt er leer. "
    "Datum immer als JJJJ-MM-TT. Beträge so, wie sie im Beleg stehen (z. B. 84,20). "
    "Kategorie und Dokumenttyp nur aus den erlaubten Werten wählen."
)

_BESCHREIBUNG = {
    "titel": "kurzer Titel, z. B. 'Stadtwerke Stromabschlag Oktober'",
    "datum": "Belegdatum (Rechnungs- bzw. Kaufdatum) als JJJJ-MM-TT",
    "faelligkeit": "Fälligkeitsdatum als JJJJ-MM-TT",
    "absender": "Firma oder Behörde, die den Beleg ausgestellt hat",
    "betrag_brutto": "Gesamtbetrag inkl. MwSt",
    "betrag_netto": "Nettobetrag",
    "mwst": "Mehrwertsteuerbetrag",
    "arbeitskosten": "Arbeits- bzw. Lohnkosten (Handwerkerleistung)",
    "rechnungsnummer": "Rechnungs- oder Belegnummer",
    "kundennummer": "Kunden- oder Vertragsnummer",
}
DATUMSFELDER = ("datum", "faelligkeit", "garantie_bis", "vertragsende")
BETRAGSFELDER = ("betrag_brutto", "betrag_netto", "mwst", "arbeitskosten")


class KiFehler(Exception):
    pass


class Ki:
    def __init__(self, url: str, modell: str, timeout: int = 600, max_zeichen: int = 6000):
        self.url = url.rstrip("/")
        self.modell = modell
        self.timeout = timeout
        self.max_zeichen = max_zeichen

    def verbindung(self) -> dict:
        try:
            r = requests.get(f"{self.url}/api/tags", timeout=5)
            r.raise_for_status()
            modelle = [m.get("name", "") for m in r.json().get("models", [])]
        except requests.RequestException:
            return {"erreichbar": False, "modell_vorhanden": False, "modelle": []}
        vorhanden = any(m == self.modell or m.split(":")[0] == self.modell or m == f"{self.modell}:latest"
                        for m in modelle)
        return {"erreichbar": True, "modell_vorhanden": vorhanden, "modelle": modelle}

    def schema(self, felder: list[str], stamm: dict) -> dict:
        eigenschaften: dict = {}
        for f in felder:
            if f == "kategorie":
                kats = list(stamm.get("kategorien") or [])
                eigenschaften["kategorie"] = {"type": "string", "enum": kats + [""]} if kats else {"type": "string"}
                eigenschaften["kategorie_sicherheit"] = {"type": "string", "enum": ["hoch", "mittel", "niedrig"]}
            elif f == "dokumenttyp":
                typen = list(stamm.get("dokumenttypen") or [])
                eigenschaften[f] = {"type": "string", "enum": typen} if typen else {"type": "string"}
            elif f == "zahlungsart":
                eigenschaften[f] = {"type": "string",
                                    "enum": ["bar", "Karte", "Lastschrift", "Überweisung", "PayPal", ""]}
            else:
                eigenschaften[f] = {"type": "string", "description": _BESCHREIBUNG.get(f, f)}
        return {"type": "object", "properties": eigenschaften, "required": list(eigenschaften)}

    def anfrage_bauen(self, text: str, felder: list[str], stamm: dict) -> dict:
        absender = ", ".join((stamm.get("korrespondenten") or [])[:150])
        zeilen = [f"Gesuchte Felder: {', '.join(felder)}."]
        if absender and "absender" in felder:
            zeilen.append(f"Bekannte Absender (falls passend, genau so schreiben): {absender}")
        if "kategorie" in felder and stamm.get("kategorien"):
            zeilen.append("Erlaubte Kategorien: " + "; ".join(stamm["kategorien"]))
        zeilen.append(f"Heute ist {date.today().isoformat()}.")
        zeilen.append("Beleg-Text:\n<<<\n" + text[: self.max_zeichen] + "\n>>>")
        return {
            "model": self.modell,
            "stream": False,
            "format": self.schema(felder, stamm),
            "options": {"temperature": 0, "num_ctx": 4096},
            "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": "\n".join(zeilen)}],
        }

    def ergaenzen(self, text: str, felder: list[str], stamm: dict) -> tuple[dict, float]:
        """Fragt das Modell. Gibt ({feld: wert, 'kategorie_sicherheit': …}, Sekunden) zurück."""
        if not felder or not text.strip():
            return {}, 0.0
        start = time.time()
        try:
            r = requests.post(f"{self.url}/api/chat", json=self.anfrage_bauen(text, felder, stamm),
                              timeout=self.timeout)
            r.raise_for_status()
            inhalt = r.json()["message"]["content"]
            roh = json.loads(inhalt)
        except requests.RequestException as exc:
            raise KiFehler(f"Ollama nicht erreichbar oder Zeitüberschreitung ({exc.__class__.__name__})") from exc
        except (KeyError, ValueError) as exc:
            raise KiFehler("Antwort des Modells ist kein gültiges JSON") from exc
        return self.bereinigen(roh, felder), time.time() - start

    @staticmethod
    def bereinigen(roh: dict, felder: list[str]) -> dict:
        ergebnis: dict = {}
        for f in felder:
            wert = roh.get(f)
            if wert in (None, "", "null", "unbekannt"):
                continue
            if f in DATUMSFELDER:
                s = str(wert).strip()
                try:
                    wert = date.fromisoformat(s[:10]).isoformat()
                except ValueError:
                    gefunden = daten_in(s)
                    if not gefunden:
                        continue
                    wert = gefunden[0]
            elif f in BETRAGSFELDER:
                wert = betrag_parsen(str(wert)) if not isinstance(wert, (int, float)) else round(float(wert), 2)
                if wert is None:
                    continue
            else:
                wert = str(wert).strip()
            ergebnis[f] = wert
        if roh.get("kategorie_sicherheit"):
            ergebnis["kategorie_sicherheit"] = roh["kategorie_sicherheit"]
        return ergebnis
