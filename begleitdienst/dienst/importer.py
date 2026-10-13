"""Import in Paperless: gemeinsamer Weg für Windows-Client, E-Mail und Web-Upload.

Ablauf je Auftrag: hochladen → auf Paperless warten → (bei Mail) feste
Extraktion auf dem OCR-Text → Plausibilität → Kategorie, Tags, Korrespondent,
Dokumenttyp, Benutzerfelder setzen → Posteingang nur bei Unsicherem.
"""

from __future__ import annotations

import hashlib
import logging
import os
import threading
import time
import uuid
from email.message import EmailMessage
from email.utils import formataddr

from archiv_core import muster, plausibilitaet
from archiv_core.extraktion import feste_verfahren, titel_vorschlagen
from archiv_core.felder import (BENUTZERFELDER, DOKUMENTTYPEN_STANDARD, QUELLE_ERECHNUNG, QUELLE_VORGABE,
                                Ergebnis, Feldwert)

from .db import Datenbank
from .konfig import Konfig
from .paperless import Duplikat, Paperless, PaperlessFehler

log = logging.getLogger(__name__)

FREEMAIL = ("gmail.com", "gmx.de", "gmx.net", "web.de", "t-online.de", "outlook.com", "hotmail.com",
            "yahoo.com", "yahoo.de", "posteo.de", "icloud.com", "me.com", "mailbox.org", "freenet.de")


def sha256(daten: bytes) -> str:
    return hashlib.sha256(daten).hexdigest()


class Importer:
    def __init__(self, konfig: Konfig, db: Datenbank, paperless: Paperless):
        self.k = konfig
        self.db = db
        self.p = paperless
        self.eingang = os.path.join(konfig.daten_verzeichnis, "eingang")
        os.makedirs(self.eingang, exist_ok=True)
        self._stopp = threading.Event()
        self._signal = threading.Event()
        self._thread: threading.Thread | None = None
        self.versender = None  # optional: Funktion(EmailMessage) für Rückmeldungen

    # ---------------------------------------------------------------- Stammdaten

    def stammdaten(self) -> dict:
        praefix = self.k.kategorie_praefix
        tags = [t["name"] for t in self.p.tags()]
        return {
            "kategorien": sorted(t[len(praefix):] for t in tags if t.lower().startswith(praefix.lower())),
            "tags": sorted(t for t in tags if not t.lower().startswith(praefix.lower())
                           and t.lower() != self.k.posteingang_tag.lower()),
            "korrespondenten": sorted(k["name"] for k in self.p.korrespondenten()),
            "dokumenttypen": sorted({d["name"] for d in self.p.dokumenttypen()} | set(DOKUMENTTYPEN_STANDARD)),
            "personen": list(self.k.personen),
            "felder": [{"name": f.name, "label": f.label, "typ": f.typ,
                        "optionen": list(self.k.personen if f.name == "person" else f.optionen)}
                       for f in BENUTZERFELDER],
            "profile": [{k: v for k, v in p.items() if k != "aktualisiert"} for p in self.db.profile()],
        }

    # ---------------------------------------------------------------- Einreihen

    def einreihen(self, daten: bytes, dateiname: str, auftrag: dict) -> dict:
        """Legt einen Auftrag an und gibt {id, status} zurück. Bekannte Dateien werden nicht erneut importiert."""
        pruefsumme = sha256(daten)
        auftrag_id = uuid.uuid4().hex
        quelle = auftrag.get("quelle", "web")
        bekannt = self.db.datei_bekannt(pruefsumme)
        if bekannt:
            self.db.auftrag_anlegen(auftrag_id, quelle, dateiname, pruefsumme, auftrag, status="duplikat")
            self.db.auftrag_setzen(auftrag_id, paperless_id=bekannt["paperless_id"],
                                   fehler="Datei ist bereits im Archiv")
            return {"id": auftrag_id, "status": "duplikat", "paperless_id": bekannt["paperless_id"]}
        with open(os.path.join(self.eingang, auftrag_id), "wb") as f:
            f.write(daten)
        self.db.auftrag_anlegen(auftrag_id, quelle, dateiname, pruefsumme, auftrag)
        self._signal.set()
        return {"id": auftrag_id, "status": "wartet"}

    def wiederholen(self, auftrag_id: str) -> bool:
        a = self.db.auftrag(auftrag_id)
        if not a or a["status"] != "fehler" or not os.path.exists(os.path.join(self.eingang, auftrag_id)):
            return False
        self.db.auftrag_setzen(auftrag_id, status="wartet", fehler=None)
        self._signal.set()
        return True

    # ---------------------------------------------------------------- Arbeiter

    def starten(self) -> None:
        # nach einem Neustart unterbrochene Aufträge erneut einreihen
        self.db.ausfuehren("UPDATE auftraege SET status = 'wartet' WHERE status = 'laeuft' AND paperless_id IS NULL")
        self._thread = threading.Thread(target=self._schleife, name="importer", daemon=True)
        self._thread.start()

    def stoppen(self) -> None:
        self._stopp.set()
        self._signal.set()

    def _schleife(self) -> None:
        while not self._stopp.is_set():
            auftrag = self.db.naechster_auftrag()
            if not auftrag:
                self._signal.wait(5)
                self._signal.clear()
                continue
            try:
                self.verarbeiten(auftrag)
            except Exception as exc:  # Arbeiter darf nie sterben
                log.exception("Auftrag %s fehlgeschlagen", auftrag["id"])
                self.db.auftrag_setzen(auftrag["id"], status="fehler", fehler=str(exc)[:500])
                time.sleep(2)

    # ---------------------------------------------------------------- Verarbeitung

    def verarbeiten(self, a: dict) -> None:
        auftrag_id, d = a["id"], a["daten"] or {}
        pfad = os.path.join(self.eingang, auftrag_id)
        with open(pfad, "rb") as f:
            daten = f.read()
        self.db.auftrag_setzen(auftrag_id, status="laeuft")

        md5 = hashlib.md5(daten).hexdigest()
        try:
            # Wiederholung: Paperless hat die Datei evtl. schon beim letzten Versuch übernommen
            doc_id = self.p.dokument_per_pruefsumme(a["sha256"], md5) if a.get("paperless_task") else None
            if not doc_id:
                task = self.p.hochladen(daten, a["dateiname"] or "dokument.pdf", d.get("titel") or None)
                self.db.auftrag_setzen(auftrag_id, paperless_task=task)
                doc_id = self.p.auf_aufgabe_warten(task, md5=md5, sha256=a["sha256"])
        except Duplikat as exc:
            self.db.datei_merken(a["sha256"], None)
            self.db.auftrag_setzen(auftrag_id, status="duplikat", fehler=f"Paperless: {exc}"[:500])
            os.remove(pfad)
            return

        dok = self.p.dokument(doc_id)
        stamm = self.stammdaten()
        e = self._ergebnis_bilden(d, daten, dok.get("content") or "", stamm, a["quelle"])
        neue_kategorie = self._kategorie_klaeren(e, d, stamm)
        aenderung, sicher = self._aenderung_bauen(dok, d, e, neue_kategorie)
        self.p.dokument_aendern(doc_id, aenderung)

        notizen = [d["notiz"]] if d.get("notiz") else []
        if not sicher:
            offen = [p["text"] for p in e.pruefungen if not p["ok"]]
            namen = {"datum": "Datum", "betrag_brutto": "Betrag", "kategorie": "Kategorie",
                     "dokumenttyp": "Dokumenttyp"}
            for n, label in namen.items():
                if n not in e.felder:
                    if n != "betrag_brutto":
                        offen.append(f"{label} fehlt")
                elif not e.felder[n].sicher:
                    offen.append(f"{label} unsicher")
            if offen:
                notizen.append("Bitte prüfen: " + "; ".join(dict.fromkeys(offen)))
        for n in notizen:
            self.p.notiz_hinzufuegen(doc_id, n)

        self.db.feldwerte_speichern(doc_id, {k: v.als_dict() for k, v in e.felder.items()})
        self.db.datei_merken(a["sha256"], doc_id)
        self._profil_lernen(e, dok.get("content") or "", d, sicher)
        self.db.auftrag_setzen(auftrag_id, status="fertig", paperless_id=doc_id, sicher=1 if sicher else 0,
                               fehler=None)
        os.remove(pfad)
        log.info("Auftrag %s → Dokument %s (%s)", auftrag_id, doc_id, "sicher" if sicher else "Posteingang")
        if d.get("antwort_an"):
            self._rueckmelden(d, doc_id, e, sicher)

    def _ergebnis_bilden(self, d: dict, daten: bytes, text: str, stamm: dict, quelle: str) -> Ergebnis:
        e = Ergebnis(text=text)
        for name, fw in (d.get("felder") or {}).items():
            e.felder[name] = Feldwert.aus_dict(fw)
        e.tags = [t for t in d.get("tags") or []]
        if quelle != "windows-client":
            # Der Client hat die festen Verfahren schon ausgeführt; Mail und Web-Upload noch nicht
            fest = feste_verfahren(daten, text, stamm["profile"], stamm["korrespondenten"], d.get("mail_domain", ""))
            for name, fw in fest.felder.items():
                e.setze(name, fw.wert, fw.quelle, fw.sicher, fw.hinweis)
            for t in fest.tags:
                if t not in e.tags:
                    e.tags.append(t)
            e.pruefungen = fest.pruefungen
        for name in ("kategorie", "dokumenttyp", "absender", "person"):
            # Vom Client geschickte Felder behalten ihre Quelle und Sicherheit
            if d.get(name) and not (quelle == "windows-client" and name in e.felder):
                e.setze(name, d[name], QUELLE_VORGABE, True, ueberschreiben=True)
        plausibilitaet.pruefen(e, text, kategorien=stamm["kategorien"],
                               dokumenttypen=stamm["dokumenttypen"], korrespondenten=stamm["korrespondenten"])
        return e

    def _kategorie_klaeren(self, e: Ergebnis, d: dict, stamm: dict) -> bool:
        """True, wenn eine neue Kategorie angelegt werden darf (nur nach menschlicher Prüfung)."""
        kat = e.wert("kategorie")
        if not kat or plausibilitaet.in_liste(kat, stamm["kategorien"]):
            return False
        if d.get("geprueft"):
            e.felder["kategorie"].sicher = True
            e.pruefungen = [p for p in e.pruefungen if p.get("feld") != "kategorie"]
            return True
        e.felder["kategorie"].sicher = False
        return False

    def _aenderung_bauen(self, dok: dict, d: dict, e: Ergebnis, neue_kategorie: bool) -> tuple[dict, bool]:
        aenderung: dict = {"title": d.get("titel") or titel_vorschlagen(e) or dok.get("title")}
        if e.wert("datum"):
            aenderung["created"] = str(e.wert("datum"))[:10]
        if e.wert("absender"):
            aenderung["correspondent"] = self.p.korrespondent_sicherstellen(e.wert("absender"))
        if e.wert("dokumenttyp"):
            aenderung["document_type"] = self.p.dokumenttyp_sicherstellen(e.wert("dokumenttyp"))

        tags = set(dok.get("tags") or [])
        kat = e.wert("kategorie")
        if kat and (e.felder["kategorie"].sicher or neue_kategorie or
                    plausibilitaet.in_liste(kat, self.stammdaten()["kategorien"])):
            tags.add(self.p.tag_sicherstellen(self.k.kategorie_praefix + kat))
        for t in e.tags:
            if t and t.lower() != self.k.posteingang_tag.lower():
                tags.add(self.p.tag_sicherstellen(t))

        sicher = bool(d.get("geprueft")) or (e.ist_sicher() and not d.get("pruefen_erzwingen"))
        posteingang = self.p.tag_sicherstellen(self.k.posteingang_tag, posteingang=True)
        if sicher:
            tags.discard(posteingang)
        else:
            tags.add(posteingang)
        aenderung["tags"] = sorted(tags)

        werte = {cf["field"]: cf["value"] for cf in dok.get("custom_fields") or []}
        for fd in BENUTZERFELDER:
            wert = d.get("person") if fd.name == "person" and d.get("person") else e.wert(fd.name)
            if fd.name == "zahlungsstatus" and wert is None and e.wert("dokumenttyp") in ("Rechnung",
                                                                                         "Handwerkerrechnung"):
                wert = "offen"
            umgewandelt = self.p.benutzerfeld_wert(fd, wert)
            if umgewandelt:
                werte[umgewandelt[0]] = umgewandelt[1]
        aenderung["custom_fields"] = [{"field": k, "value": v} for k, v in werte.items()]
        return aenderung, sicher

    # ---------------------------------------------------------------- Lernen

    def _profil_lernen(self, e: Ergebnis, text: str, d: dict, sicher: bool) -> None:
        name = e.wert("absender")
        if not name:
            return
        p = self.db.profil(name) or {"name": name, "tags": [], "stichworte": [], "kategorie_zaehler": 0}
        p["ust_id"] = p.get("ust_id") or muster.ustid_finden(text)
        if e.felder.get("iban") and e.felder["iban"].quelle == QUELLE_ERECHNUNG:
            p["iban"] = e.wert("iban")
        domain = (d.get("mail_domain") or "").lower()
        if domain and domain not in FREEMAIL:
            p["domain"] = domain
        kat = e.wert("kategorie")
        if kat and (sicher or d.get("geprueft")):
            if (p.get("kategorie") or "").lower() == kat.lower():
                p["kategorie_zaehler"] = (p.get("kategorie_zaehler") or 0) + 1
            else:
                p["kategorie"], p["kategorie_zaehler"] = kat, 1
        self.db.profil_speichern(p)

    def korrekturen_lernen(self, eintraege: list[dict]) -> int:
        """Korrekturen aus dem Client: speichern und Kategorien ins Profil übernehmen."""
        for eintrag in eintraege:
            self.db.korrektur_merken(eintrag)
            if eintrag.get("feld") == "kategorie" and eintrag.get("absender") and eintrag.get("als_regel"):
                p = self.db.profil(eintrag["absender"]) or {"name": eintrag["absender"], "tags": [],
                                                             "stichworte": []}
                p["kategorie"], p["kategorie_zaehler"] = eintrag.get("neu"), 2  # Regel gilt sofort als sicher
                self.db.profil_speichern(p)
        return len(eintraege)

    # ---------------------------------------------------------------- Rückmeldung

    def _rueckmelden(self, d: dict, doc_id: int, e: Ergebnis, sicher: bool) -> None:
        if not self.versender:
            return
        zeilen = [f"Ihr Dokument wurde archiviert{'.' if sicher else ' und liegt zur Prüfung im Posteingang.'}", ""]
        for name, label in (("absender", "Absender"), ("dokumenttyp", "Typ"), ("datum", "Datum"),
                            ("betrag_brutto", "Betrag"), ("kategorie", "Kategorie")):
            if e.wert(name) is not None:
                wert = e.wert(name)
                if name == "betrag_brutto":
                    wert = muster.betrag_formatieren(float(wert)) + " €"
                zeilen.append(f"{label}: {wert}{'' if e.felder[name].sicher else '  (bitte prüfen)'}")
        if e.tags:
            zeilen.append("Tags: " + ", ".join(e.tags))
        zeilen += ["", f"{self.k.paperless_link}/documents/{doc_id}/details"]
        nachricht = EmailMessage()
        nachricht["Subject"] = f"Archiviert: {d.get('titel') or titel_vorschlagen(e)}"
        nachricht["From"] = formataddr(("Familienarchiv", self.k.smtp_absender or self.k.imap_benutzer))
        nachricht["To"] = d["antwort_an"]
        nachricht.set_content("\n".join(zeilen))
        try:
            self.versender(nachricht)
        except Exception as exc:
            log.warning("Rückmeldung nicht versendet: %s", exc)
