"""Allgemeine Textmuster für deutsche Belege: Datum, Beträge, Nummern, Typ.

Ein Wert gilt als sicher, wenn er über ein eindeutiges Schlüsselwort gefunden
wurde. Alles andere wird als Vorschlag mit "prüfen" eingetragen.
"""

from __future__ import annotations

import re
from datetime import date, timedelta

from .felder import QUELLE_BERECHNET, QUELLE_MUSTER, Ergebnis

MONATE = {
    "januar": 1, "jan": 1, "februar": 2, "feb": 2, "märz": 3, "maerz": 3, "mär": 3, "mrz": 3,
    "april": 4, "apr": 4, "mai": 5, "juni": 6, "jun": 6, "juli": 7, "jul": 7, "august": 8,
    "aug": 8, "september": 9, "sep": 9, "sept": 9, "oktober": 10, "okt": 10, "november": 11,
    "nov": 11, "dezember": 12, "dez": 12,
}

BETRAG_RE = re.compile(
    r"(?<![\d.,])(-?\s?(?:\d{1,3}(?:\.\d{3})+,\d{2}|\d+,\d{2}|\d{1,3}(?:,\d{3})+\.\d{2}|\d+\.\d{2}))(?![\d])"
)
DATUM_RE = re.compile(
    r"(?<!\d)(\d{1,2})\.\s?(\d{1,2})\.\s?(\d{4}|\d{2})(?!\d)"
    r"|(?<!\d)(\d{4})-(\d{2})-(\d{2})(?!\d)"
    r"|(?<!\d)(\d{1,2})\.?\s+(" + "|".join(sorted(MONATE, key=len, reverse=True)) + r")\.?\s+(\d{4})",
    re.IGNORECASE,
)


# ---------------------------------------------------------------- Grundbausteine

def betrag_parsen(text: str) -> float | None:
    s = text.replace("€", "").replace("EUR", "").replace(" ", "").strip()
    if not s:
        return None
    neg = s.startswith("-")
    s = s.lstrip("-")
    if "," in s and "." in s:
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    elif "," in s:
        s = s.replace(",", ".")
    try:
        wert = round(float(s), 2)
    except ValueError:
        return None
    return -wert if neg else wert


def betrag_formatieren(wert: float) -> str:
    """84.2 -> '84,20', 1284.5 -> '1.284,50'"""
    s = f"{abs(wert):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return ("-" if wert < 0 else "") + s


def betraege_in(zeile: str) -> list[float]:
    return [b for b in (betrag_parsen(m.group(1)) for m in BETRAG_RE.finditer(zeile)) if b is not None]


def daten_in(zeile: str) -> list[str]:
    ergebnis = []
    for m in DATUM_RE.finditer(zeile):
        try:
            if m.group(1):
                t, mo, j = int(m.group(1)), int(m.group(2)), int(m.group(3))
                if j < 100:
                    j += 2000 if j < 70 else 1900
            elif m.group(4):
                j, mo, t = int(m.group(4)), int(m.group(5)), int(m.group(6))
            else:
                t, mo, j = int(m.group(7)), MONATE[m.group(8).lower()], int(m.group(9))
            ergebnis.append(date(j, mo, t).isoformat())
        except (ValueError, KeyError):
            continue
    return ergebnis


def _zeilen(text: str) -> list[str]:
    return [z.strip() for z in text.splitlines() if z.strip()]


def _hat(zeile: str, muster: str) -> bool:
    return re.search(muster, zeile, re.IGNORECASE) is not None


# ---------------------------------------------------------------- Felder

_BRUTTO = [
    r"zu\s+zahlen", r"zahlbetrag", r"rechnungsbetrag", r"gesamtbetrag", r"endbetrag",
    r"bruttobetrag", r"gesamtsumme", r"summe\s+brutto", r"brutto", r"gesamt\b", r"\bsumme\b",
    r"\btotal\b", r"\bbetrag\b",
]
_STARKE_BRUTTO = 9  # die ersten Schlüsselwörter gelten als eindeutig


def brutto_finden(zeilen: list[str]) -> tuple[float | None, bool]:
    kandidaten: list[tuple[int, float]] = []
    for i, z in enumerate(zeilen):
        if _hat(z, r"zwischensumme|netto(?!.*brutto)|mwst|ust\b|umsatzsteuer|rabatt|gegeben|rückgeld"):
            continue
        for rang, m in enumerate(_BRUTTO):
            if _hat(z, m):
                werte = betraege_in(z) or (betraege_in(zeilen[i + 1]) if i + 1 < len(zeilen) else [])
                if werte:
                    kandidaten.append((rang, werte[-1]))
                break
    if not kandidaten:
        return None, False
    bester = min(r for r, _ in kandidaten)
    werte = {w for r, w in kandidaten if r == bester}
    wert = max(werte)
    return wert, bester < _STARKE_BRUTTO and len(werte) == 1


_BETRAG_NACH = r"[\s:=]*(?:EUR|€)?\s*(-?\s?(?:\d{1,3}(?:\.\d{3})+,\d{2}|\d+,\d{2}|\d{1,3}(?:,\d{3})+\.\d{2}|\d+\.\d{2}))(?!\d)"


def netto_finden(zeilen: list[str]) -> float | None:
    for z in zeilen:
        if _hat(z, r"netto") and not _hat(z, r"brutto|tage\s+netto|netto\s+kasse"):
            m = re.search(r"netto(?:betrag|summe)?" + _BETRAG_NACH, z, re.IGNORECASE)
            if m:
                return betrag_parsen(m.group(1))
            werte = betraege_in(z)
            if werte and re.match(r"\s*(summe\s+)?netto", z, re.IGNORECASE):
                return werte[-1]
    return None


_MWST = r"(?:mwst|mw\.-?st\.?|ust\b|umsatzsteuer|mehrwertsteuer)"


def mwst_finden(zeilen: list[str]) -> float | None:
    einzel = []
    for z in zeilen:
        if not _hat(z, _MWST):
            continue
        if _hat(z, r"ust-?id|steuer-?nr|ust\.?-?idnr"):
            continue
        treffer = re.findall(_MWST + r"(?:\s*\d{1,2}(?:[.,]\d{1,2})?\s*%)?" + _BETRAG_NACH, z, re.IGNORECASE)
        werte = [betrag_parsen(t) for t in treffer]
        if not werte:
            # z. B. "Umsatzsteuer 19 % auf 70,76 = 13,44" – dann der letzte Betrag der Zeile
            werte = betraege_in(z)[-1:] if not _hat(z, r"netto") else []
        if not werte:
            continue
        if _hat(z, r"gesamt|summe"):
            return werte[-1]
        einzel.append(werte[-1])
    return round(sum(einzel), 2) if einzel else None


def datum_finden(zeilen: list[str]) -> tuple[str | None, bool]:
    for z in zeilen:
        if _hat(z, r"rechnungsdatum|belegdatum|ausstellungsdatum|datum\s+der\s+rechnung|\bdatum\b|\bvom\b"):
            if _hat(z, r"liefer|leistung|zeitraum|geburt|gültig|fällig|zahlbar"):
                continue
            d = daten_in(z)
            if d:
                return d[0], True
    alle = {d for z in zeilen for d in daten_in(z)}
    for z in zeilen:
        if _hat(z, r"liefer|leistung|zeitraum|geburt|gültig|fällig|zahlbar"):
            continue
        d = daten_in(z)
        if d:
            # eindeutig, wenn es das einzige Datum ist oder mit Uhrzeit steht (Kassenbon)
            return d[0], len(alle) == 1 or bool(re.search(r"\b\d{1,2}:\d{2}\b", z))
    return None, False


def faelligkeit_finden(zeilen: list[str], datum: str | None) -> tuple[str | None, str, bool]:
    for z in zeilen:
        if _hat(z, r"fällig|faellig|zahlbar\s+bis|zahlungsziel|spätestens|abbuchung\s+am|eingezogen\s+am"):
            d = daten_in(z)
            if d:
                return d[-1], QUELLE_MUSTER, True
    if datum:
        for z in zeilen:
            m = re.search(r"innerhalb\s+(?:von\s+)?(\d{1,3})\s+tag|(\d{1,3})\s+tage\s+netto|zahlungsziel\s*:?\s*(\d{1,3})",
                          z, re.IGNORECASE)
            if m:
                tage = int(next(g for g in m.groups() if g))
                return (date.fromisoformat(datum) + timedelta(days=tage)).isoformat(), QUELLE_BERECHNET, False
    return None, QUELLE_MUSTER, False


_NUMMER = r"([A-Z0-9][A-Z0-9\-/_.]{2,30})"


def rechnungsnummer_finden(text: str) -> str | None:
    m = re.search(r"(?:rechnungs?[- ]?(?:nr|nummer|no)\.?|rechnung\s+nr\.?|beleg[- ]?nr\.?|bon[- ]?nr\.?"
                  r"|invoice\s*(?:no|number)?\.?)\s*[:#]?\s*" + _NUMMER, text, re.IGNORECASE)
    if m and re.search(r"\d", m.group(1)):
        return m.group(1).rstrip(".")
    return None


def kundennummer_finden(text: str) -> str | None:
    m = re.search(r"(?:kunden[- ]?(?:nr|nummer)\.?|kd\.?[- ]?nr\.?|vertragskonto|vertragsnummer)\s*[:#]?\s*"
                  r"([A-Z0-9][A-Z0-9\-/]*(?: \d{2,})*)", text, re.IGNORECASE)
    if m and re.search(r"\d", m.group(1)):
        return m.group(1).strip()
    return None


def iban_gueltig(iban: str) -> bool:
    iban = iban.replace(" ", "").upper()
    if not re.fullmatch(r"[A-Z]{2}\d{2}[A-Z0-9]{11,30}", iban):
        return False
    umgestellt = iban[4:] + iban[:4]
    zahl = "".join(str(int(c, 36)) for c in umgestellt)
    return int(zahl) % 97 == 1


def iban_finden(text: str) -> str | None:
    for m in re.finditer(r"\b([A-Z]{2}\d{2}(?: ?[A-Z0-9]{4}){3,7}(?: ?[A-Z0-9]{1,3})?)\b", text):
        kandidat = m.group(1).replace(" ", "")
        if iban_gueltig(kandidat):
            return kandidat
    return None


def ustid_finden(text: str) -> str | None:
    m = re.search(r"\b(DE ?\d{3} ?\d{3} ?\d{3})\b", text)
    return m.group(1).replace(" ", "") if m else None


_ZAHLUNGSARTEN = [
    ("Lastschrift", r"lastschrift|abgebucht|eingezogen|sepa-?mandat"),
    ("Karte", r"ec-?karte|girocard|kartenzahlung|\bvisa\b|mastercard|maestro|kreditkarte|debitkarte|contactless|kontaktlos"),
    ("bar", r"\bbar\b|barzahlung|bargeld|rückgeld|gegeben\s+bar"),
    ("PayPal", r"paypal"),
    ("Überweisung", r"überweis|ueberweis"),
]


def zahlungsart_finden(text: str) -> tuple[str | None, bool]:
    treffer = [name for name, m in _ZAHLUNGSARTEN if _hat(text, m)]
    if not treffer:
        return None, False
    return treffer[0], len(treffer) == 1


def arbeitskosten_finden(zeilen: list[str]) -> tuple[float | None, bool]:
    for z in zeilen:
        if _hat(z, r"arbeitskosten|lohnkosten|arbeitslohn|lohnanteil|arbeitsleistung|lohn\s+und\s+fahrt"):
            werte = betraege_in(z)
            if werte:
                return werte[-1], _hat(z, r"arbeitskosten|lohnanteil") and "35a" in " ".join(zeilen).lower()
    return None, False


def dokumenttyp_finden(text: str) -> tuple[str | None, bool]:
    kopf = "\n".join(_zeilen(text)[:25]).lower()
    t = text.lower()
    regeln = [
        ("Kreditkartenabrechnung", r"kreditkarten-?abrechnung|kartenabrechnung|umsatzaufstellung"),
        ("Kontoauszug", r"kontoauszug"),
        ("Mahnung", r"\bmahnung\b|zahlungserinnerung"),
        ("Gutschrift", r"\bgutschrift\b"),
        ("Bescheid", r"bescheid\b|steuerbescheid|bescheid\s+(?:über|für)"),
        ("Versicherung", r"versicherungsschein|beitragsrechnung|police\b"),
        ("Vertrag", r"\bvertrag\b|vertragsbestätigung|auftragsbestätigung"),
    ]
    for typ, m in regeln:
        if re.search(m, kopf):
            return typ, True
    if re.search(r"\brechnung\b|rechnungsnummer|rechnungsdatum", t):
        if re.search(r"arbeitskosten|arbeitszeit|monteur|montage|lohnkosten|35a", t):
            return "Handwerkerrechnung", True
        return "Rechnung", bool(re.search(r"\brechnung\b", kopf))
    if re.search(r"\bsumme\b|\btotal\b", t) and re.search(
            r"\bbar\b|rückgeld|gegeben|girocard|kartenzahlung|kasse|\btse\b|bon[- ]?nr|beleg[- ]?nr", t):
        return "Kassenbon", True
    for typ, m in regeln:
        if re.search(m, t):
            return typ, False
    return None, False


# ---------------------------------------------------------------- Gesamt

def auslesen(text: str, ergebnis: Ergebnis) -> None:
    """Trägt alle über Muster erkennbaren Werte ein (verdrängt keine besseren Quellen)."""
    if not text:
        return
    zeilen = _zeilen(text)
    q = QUELLE_MUSTER

    typ, sicher = dokumenttyp_finden(text)
    ergebnis.setze("dokumenttyp", typ, q, sicher)

    datum, sicher = datum_finden(zeilen)
    ergebnis.setze("datum", datum, q, sicher)

    brutto, sicher = brutto_finden(zeilen)
    ergebnis.setze("betrag_brutto", brutto, q, sicher)
    ergebnis.setze("betrag_netto", netto_finden(zeilen), q, False)
    ergebnis.setze("mwst", mwst_finden(zeilen), q, False)

    faellig, quelle, sicher = faelligkeit_finden(zeilen, ergebnis.wert("datum"))
    ergebnis.setze("faelligkeit", faellig, quelle, sicher,
                   "aus Zahlungsziel berechnet" if quelle == QUELLE_BERECHNET else "")

    ergebnis.setze("rechnungsnummer", rechnungsnummer_finden(text), q, True)
    ergebnis.setze("kundennummer", kundennummer_finden(text), q, True)
    ergebnis.setze("iban", iban_finden(text), q, True)

    art, sicher = zahlungsart_finden(text)
    ergebnis.setze("zahlungsart", art, q, sicher)

    if ergebnis.wert("dokumenttyp") == "Handwerkerrechnung":
        ak, sicher = arbeitskosten_finden(zeilen)
        ergebnis.setze("arbeitskosten", ak, q, sicher)
