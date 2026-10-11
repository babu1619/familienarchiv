"""E-Rechnungen auslesen: ZUGFeRD / Factur-X (CII) und XRechnung (CII oder UBL).

Die Daten stammen aus dem eingebetteten XML und gelten deshalb als sicher.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

from .felder import QUELLE_ERECHNUNG, Ergebnis
from .pdftext import anhaenge_lesen

XML_NAMEN = ("factur-x.xml", "zugferd-invoice.xml", "zugferd_invoice.xml",
             "xrechnung.xml", "zugferd.xml")

_TYPCODES = {"380": "Rechnung", "381": "Gutschrift", "384": "Rechnung", "389": "Rechnung"}


def _lokal(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _finde(knoten, *pfad):
    """Sucht entlang lokaler Elementnamen, ignoriert Namensräume."""
    aktuell = [knoten]
    for name in pfad:
        naechste = []
        for k in aktuell:
            for kind in k.iter() if name.startswith("//") else list(k):
                if _lokal(kind.tag) == name.lstrip("/"):
                    naechste.append(kind)
        if not naechste:
            return None
        aktuell = naechste
    return aktuell[0]


def _text(knoten, *pfad) -> str:
    k = _finde(knoten, *pfad)
    return (k.text or "").strip() if k is not None and k.text else ""


def _betrag(s: str) -> float | None:
    try:
        return round(float(s), 2)
    except (TypeError, ValueError):
        return None


def _datum_102(s: str) -> str:
    s = s.strip()
    if len(s) == 8 and s.isdigit():
        return f"{s[0:4]}-{s[4:6]}-{s[6:8]}"
    if len(s) >= 10 and s[4] == "-":
        return s[:10]
    return ""


def xml_finden(pdf: bytes) -> bytes | None:
    anhaenge = anhaenge_lesen(pdf)
    for name, inhalt in anhaenge.items():
        if name.lower() in XML_NAMEN:
            return inhalt
    for name, inhalt in anhaenge.items():
        if name.lower().endswith(".xml") and (b"CrossIndustryInvoice" in inhalt or b"<Invoice" in inhalt
                                               or b":Invoice" in inhalt):
            return inhalt
    return None


def auslesen(pdf: bytes, ergebnis: Ergebnis) -> bool:
    """Trägt die Daten einer eingebetteten E-Rechnung ein. True, wenn eine gefunden wurde."""
    xml = xml_finden(pdf)
    if not xml:
        return False
    return xml_auslesen(xml, ergebnis)


def xml_auslesen(xml: bytes, ergebnis: Ergebnis) -> bool:
    try:
        wurzel = ET.fromstring(xml)
    except ET.ParseError:
        return False
    name = _lokal(wurzel.tag)
    if name == "CrossIndustryInvoice":
        _cii(wurzel, ergebnis)
    elif name in ("Invoice", "CreditNote"):
        _ubl(wurzel, ergebnis, gutschrift=name == "CreditNote")
    else:
        return False
    ergebnis.pruefungen.append({"text": "E-Rechnung (strukturierte Daten) gefunden", "ok": True})
    return True


def _setze(e: Ergebnis, feld: str, wert) -> None:
    e.setze(feld, wert, QUELLE_ERECHNUNG, sicher=True)


def _cii(w, e: Ergebnis) -> None:
    doc = _finde(w, "ExchangedDocument")
    if doc is not None:
        _setze(e, "rechnungsnummer", _text(doc, "ID"))
        _setze(e, "datum", _datum_102(_text(doc, "IssueDateTime", "DateTimeString")))
        _setze(e, "dokumenttyp", _TYPCODES.get(_text(doc, "TypeCode"), "Rechnung"))
    verkaeufer = _finde(w, "//SellerTradeParty")
    if verkaeufer is not None:
        _setze(e, "absender", _text(verkaeufer, "Name"))
    summen = _finde(w, "//SpecifiedTradeSettlementHeaderMonetarySummation")
    if summen is not None:
        _setze(e, "betrag_brutto", _betrag(_text(summen, "GrandTotalAmount")))
        _setze(e, "betrag_netto", _betrag(_text(summen, "TaxBasisTotalAmount")))
        _setze(e, "mwst", _betrag(_text(summen, "TaxTotalAmount")))
    faellig = _finde(w, "//SpecifiedTradePaymentTerms")
    if faellig is not None:
        _setze(e, "faelligkeit", _datum_102(_text(faellig, "DueDateDateTime", "DateTimeString")))
    iban = _finde(w, "//PayeePartyCreditorFinancialAccount")
    if iban is not None:
        _setze(e, "iban", _text(iban, "IBANID").replace(" ", ""))
    kaeufer = _finde(w, "//BuyerTradeParty")
    if kaeufer is not None:
        _setze(e, "kundennummer", _text(kaeufer, "ID"))
    mittel = _text(w, "//SpecifiedTradeSettlementPaymentMeans", "TypeCode")
    _setze(e, "zahlungsart", {"58": "Überweisung", "59": "Lastschrift", "30": "Überweisung",
                               "10": "bar", "48": "Karte", "54": "Karte"}.get(mittel, ""))


def _ubl(w, e: Ergebnis, gutschrift: bool) -> None:
    _setze(e, "rechnungsnummer", _text(w, "ID"))
    _setze(e, "datum", _datum_102(_text(w, "IssueDate")))
    _setze(e, "faelligkeit", _datum_102(_text(w, "DueDate")))
    _setze(e, "dokumenttyp", "Gutschrift" if gutschrift else "Rechnung")
    lieferant = _finde(w, "AccountingSupplierParty", "Party")
    if lieferant is not None:
        _setze(e, "absender", _text(lieferant, "PartyName", "Name")
               or _text(lieferant, "PartyLegalEntity", "RegistrationName"))
    summen = _finde(w, "LegalMonetaryTotal")
    if summen is not None:
        _setze(e, "betrag_brutto", _betrag(_text(summen, "PayableAmount")
                                           or _text(summen, "TaxInclusiveAmount")))
        _setze(e, "betrag_netto", _betrag(_text(summen, "TaxExclusiveAmount")))
    _setze(e, "mwst", _betrag(_text(w, "TaxTotal", "TaxAmount")))
    konto = _finde(w, "//PayeeFinancialAccount")
    if konto is not None:
        _setze(e, "iban", _text(konto, "ID").replace(" ", ""))
