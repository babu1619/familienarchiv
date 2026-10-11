"""Feste Extraktionsverfahren in Rangfolge: E-Rechnung, Absenderprofil, Muster."""

from __future__ import annotations

import re

from . import muster, zugferd
from .felder import QUELLE_PROFIL, Ergebnis
from .pdftext import ist_pdf


def absender_im_text(text: str, bekannte: list[str]) -> str | None:
    """Längster bekannter Absendername, der wörtlich im Text vorkommt."""
    t = (text or "").lower()
    treffer = [n for n in bekannte if len(n) >= 3 and re.search(r"(?<!\w)" + re.escape(n.lower()) + r"(?!\w)", t)]
    return max(treffer, key=len) if treffer else None


def profil_finden(text: str, profile: list[dict], mail_domain: str = "") -> dict | None:
    t = (text or "").replace(" ", "").lower()
    for p in profile:
        if p.get("ust_id") and p["ust_id"].replace(" ", "").lower() in t:
            return p
        if p.get("iban") and p["iban"].replace(" ", "").lower() in t:
            return p
        if mail_domain and p.get("domain") and mail_domain.lower().endswith(p["domain"].lower()):
            return p
    tl = (text or "").lower()
    for p in profile:
        for wort in p.get("stichworte") or []:
            if wort and wort.lower() in tl:
                return p
    return None


def profil_anwenden(text: str, e: Ergebnis, profile: list[dict], bekannte_absender: list[str],
                    mail_domain: str = "") -> dict | None:
    p = profil_finden(text, profile, mail_domain)
    name = p["name"] if p else absender_im_text(text, bekannte_absender)
    if name:
        e.setze("absender", name, QUELLE_PROFIL, True)
    if p:
        e.setze("kategorie", p.get("kategorie"), QUELLE_PROFIL, bool(p.get("kategorie_sicher")))
        for tag in p.get("tags") or []:
            if tag not in e.tags:
                e.tags.append(tag)
    return p


def feste_verfahren(daten: bytes | None, text: str, profile: list[dict] | None = None,
                    bekannte_absender: list[str] | None = None, mail_domain: str = "") -> Ergebnis:
    e = Ergebnis(text=text or "")
    if daten and ist_pdf(daten):
        zugferd.auslesen(daten, e)
    profil_anwenden(text, e, profile or [], bekannte_absender or [], mail_domain)
    muster.auslesen(text, e)
    return e


def titel_vorschlagen(e: Ergebnis) -> str:
    teile = [e.wert("absender") or "", e.wert("dokumenttyp") or "Dokument"]
    datum = e.wert("datum")
    if datum:
        monate = ["Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August",
                  "September", "Oktober", "November", "Dezember"]
        teile.append(f"{monate[int(str(datum)[5:7]) - 1]} {str(datum)[:4]}")
    return " ".join(t for t in teile if t).strip()
