"""Feldbeschreibungen und Datenstrukturen für extrahierte Werte."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

# Quellen eines Werts, vom sichersten zum unsichersten
QUELLE_VORGABE = "vorgabe"        # E-Mail-Kurzsyntax oder Stapel-Vorgabe
QUELLE_MANUELL = "manuell"        # vom Menschen geprüft oder korrigiert
QUELLE_ERECHNUNG = "e-rechnung"   # ZUGFeRD / XRechnung
QUELLE_PROFIL = "profil"          # Absenderprofil / Regel
QUELLE_MUSTER = "muster"          # allgemeine Textmuster
QUELLE_KI = "ki"                  # lokales Sprachmodell
QUELLE_BERECHNET = "berechnet"    # abgeleitet, z. B. Fälligkeit aus Zahlungsziel

RANG = {
    QUELLE_VORGABE: 0,
    QUELLE_MANUELL: 0,
    QUELLE_ERECHNUNG: 1,
    QUELLE_PROFIL: 2,
    QUELLE_MUSTER: 3,
    QUELLE_KI: 4,
    QUELLE_BERECHNET: 5,
}


@dataclass(frozen=True)
class FeldDef:
    name: str            # interner Schlüssel
    label: str           # Anzeige und Name des Paperless-Benutzerfelds
    typ: str             # text | datum | betrag | auswahl | ja_nein
    optionen: tuple[str, ...] = ()


# Benutzerfelder, die in Paperless angelegt und befüllt werden
BENUTZERFELDER: tuple[FeldDef, ...] = (
    FeldDef("betrag_brutto", "Betrag brutto", "betrag"),
    FeldDef("betrag_netto", "Betrag netto", "betrag"),
    FeldDef("mwst", "MwSt", "betrag"),
    FeldDef("arbeitskosten", "Arbeitskosten", "betrag"),
    FeldDef("rechnungsnummer", "Rechnungsnummer", "text"),
    FeldDef("kundennummer", "Kundennummer", "text"),
    FeldDef("faelligkeit", "Fälligkeit", "datum"),
    FeldDef("iban", "IBAN", "text"),
    FeldDef("zahlungsart", "Zahlungsart", "auswahl",
            ("bar", "Karte", "Lastschrift", "Überweisung", "PayPal", "sonstige")),
    FeldDef("zahlungsstatus", "Zahlungsstatus", "auswahl", ("offen", "bezahlt")),
    FeldDef("erstattungsstatus", "Erstattungsstatus", "auswahl",
            ("nicht relevant", "eingereicht", "erstattet")),
    FeldDef("person", "Person", "auswahl"),  # Optionen kommen aus der Konfiguration
    FeldDef("garantie_bis", "Garantie bis", "datum"),
    FeldDef("vertragsende", "Vertragsende", "datum"),
    FeldDef("papier_ablageort", "Papier-Ablageort", "text"),
    FeldDef("papier_aufbewahren", "Papier aufbewahren", "ja_nein"),
)

FELD = {f.name: f for f in BENUTZERFELDER}

# Kernfelder, die direkt am Paperless-Dokument hängen
KERNFELDER = ("titel", "datum", "dokumenttyp", "absender", "kategorie", "tags")

DOKUMENTTYPEN_STANDARD = (
    "Rechnung", "Kassenbon", "Handwerkerrechnung", "Gutschrift", "Mahnung",
    "Vertrag", "Bescheid", "Kontoauszug", "Kreditkartenabrechnung", "Versicherung",
    "Garantie", "Handbuch", "Brief", "Sonstiges",
)

# Für diese Typen ist ein Betrag Pflicht, damit ein Dokument "sicher" ist
TYPEN_MIT_BETRAG = {"Rechnung", "Kassenbon", "Handwerkerrechnung", "Gutschrift", "Mahnung"}


@dataclass
class Feldwert:
    wert: Any
    quelle: str
    sicher: bool = False
    hinweis: str = ""

    def als_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def aus_dict(d: dict) -> "Feldwert":
        return Feldwert(d.get("wert"), d.get("quelle", QUELLE_MANUELL),
                        bool(d.get("sicher")), d.get("hinweis", ""))


@dataclass
class Ergebnis:
    """Alle erkannten Werte eines Dokuments, je Feld mit Quelle und Sicherheit."""

    felder: dict[str, Feldwert] = field(default_factory=dict)
    tags: list[str] = field(default_factory=list)
    pruefungen: list[dict] = field(default_factory=list)  # {text, ok}
    text: str = ""

    def setze(self, name: str, wert: Any, quelle: str, sicher: bool = False,
              hinweis: str = "", ueberschreiben: bool = False) -> None:
        """Setzt ein Feld, wenn es leer ist oder die neue Quelle besser ist."""
        if wert in (None, "", []):
            return
        alt = self.felder.get(name)
        if alt is None or ueberschreiben or RANG.get(quelle, 9) < RANG.get(alt.quelle, 9):
            self.felder[name] = Feldwert(wert, quelle, sicher, hinweis)

    def wert(self, name: str, standard: Any = None) -> Any:
        f = self.felder.get(name)
        return f.wert if f else standard

    def offene_felder(self, gewuenscht: tuple[str, ...]) -> list[str]:
        return [n for n in gewuenscht if n not in self.felder or not self.felder[n].sicher]

    def ist_sicher(self) -> bool:
        pflicht = ["datum", "dokumenttyp", "kategorie"]
        if self.wert("dokumenttyp") in TYPEN_MIT_BETRAG:
            pflicht.append("betrag_brutto")
        return all(n in self.felder and self.felder[n].sicher for n in pflicht) and \
            all(p["ok"] for p in self.pruefungen)

    def als_dict(self) -> dict:
        return {
            "felder": {k: v.als_dict() for k, v in self.felder.items()},
            "tags": list(self.tags),
            "pruefungen": list(self.pruefungen),
            "sicher": self.ist_sicher(),
        }

    @staticmethod
    def aus_dict(d: dict) -> "Ergebnis":
        e = Ergebnis()
        e.felder = {k: Feldwert.aus_dict(v) for k, v in (d.get("felder") or {}).items()}
        e.tags = list(d.get("tags") or [])
        e.pruefungen = list(d.get("pruefungen") or [])
        return e
