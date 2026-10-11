"""Verarbeitung: Analyse im Hintergrund, Prüfung durch den Menschen, Übergabe an den Server."""

from __future__ import annotations

import hashlib
import logging
import os
import threading
import time

from archiv_core import plausibilitaet
from archiv_core.extraktion import feste_verfahren, titel_vorschlagen
from archiv_core.felder import (DOKUMENTTYPEN_STANDARD, QUELLE_ERECHNUNG, QUELLE_KI, QUELLE_MANUELL,
                                QUELLE_PROFIL, QUELLE_VORGABE, TYPEN_MIT_BETRAG, Ergebnis, Feldwert)
from archiv_core.pdftext import PasswortGeschuetzt

from . import energie
from .einstellungen import Einstellungen
from .ki import Ki, KiFehler
from .ocr import BILDENDUNGEN, text_gewinnen
from .server_api import ServerApi, ServerFehler
from .speicher import (ANALYSE, ARCHIVIERT, BEREIT, DUPLIKAT, FEHLER, PRUEFEN, SICHER, UE_FEHLER, UEBERTRAGEN,
                       WARTET, Speicher)

log = logging.getLogger(__name__)

ENDUNGEN = (".pdf",) + BILDENDUNGEN
FELDNAMEN = {"datum": "Datum", "dokumenttyp": "Dokumenttyp", "absender": "Absender", "kategorie": "Kategorie",
             "betrag_brutto": "Betrag", "arbeitskosten": "Arbeitskosten", "faelligkeit": "Fälligkeit"}
_FESTE_QUELLEN = (QUELLE_VORGABE, QUELLE_MANUELL, QUELLE_ERECHNUNG, QUELLE_PROFIL)


def dateien_sammeln(pfade: list[str]) -> list[str]:
    ergebnis = []
    for pfad in pfade:
        if os.path.isdir(pfad):
            for wurzel, _, dateien in os.walk(pfad):
                for d in sorted(dateien):
                    if d.lower().endswith(ENDUNGEN) and not d.startswith("~$"):
                        ergebnis.append(os.path.join(wurzel, d))
        elif os.path.isfile(pfad) and pfad.lower().endswith(ENDUNGEN):
            ergebnis.append(pfad)
    return ergebnis


def gewuenschte_felder(e: Ergebnis) -> list[str]:
    felder = ["datum", "dokumenttyp", "absender", "kategorie"]
    typ = e.wert("dokumenttyp")
    if typ is None or typ in TYPEN_MIT_BETRAG:
        felder.append("betrag_brutto")
    if typ == "Handwerkerrechnung":
        felder.append("arbeitskosten")
    return felder


class Verarbeitung:
    def __init__(self, speicher: Speicher, einstellungen: Einstellungen):
        self.speicher = speicher
        self.e = einstellungen
        self.pausiert = threading.Event()
        self._stopp = threading.Event()
        self._signal = threading.Event()
        self.aktuell: int | None = None
        self.server_fehler = ""
        self._stamm_fallback: dict = {}

    # ---------------------------------------------------------------- Abhängigkeiten

    @property
    def server(self) -> ServerApi | None:
        if not self.e.vollstaendig():
            return None
        if getattr(self, "_server", None) is None or self._server.url != self.e.server_url.rstrip("/"):
            self._server = ServerApi(self.e.server_url, self.e.api_token)
        return self._server

    def einstellungen_geaendert(self, e: Einstellungen) -> None:
        self.e = e
        self._server = None
        self._signal.set()

    @property
    def ki(self) -> Ki:
        return Ki(self.e.ollama_url, self.e.modell, self.e.ki_timeout, self.e.ki_max_zeichen)

    def stammdaten(self) -> dict:
        try:
            if self.server:
                self._stamm_fallback = self.server.stammdaten()
                self.server_fehler = ""
        except ServerFehler as exc:
            self.server_fehler = str(exc)
        return self._stamm_fallback or {"kategorien": [], "tags": [], "korrespondenten": [],
                                        "dokumenttypen": list(DOKUMENTTYPEN_STANDARD), "personen": [], "profile": []}

    # ---------------------------------------------------------------- Threads

    def starten(self) -> None:
        threading.Thread(target=self._analyse_schleife, name="analyse", daemon=True).start()
        threading.Thread(target=self._uebergabe_schleife, name="uebergabe", daemon=True).start()

    def stoppen(self) -> None:
        self._stopp.set()
        self._signal.set()
        energie.wach_halten(False)

    def anstossen(self) -> None:
        self._signal.set()

    def _analyse_schleife(self) -> None:
        while not self._stopp.is_set():
            if self.pausiert.is_set():
                energie.wach_halten(False)
                self._signal.wait(1)
                self._signal.clear()
                continue
            doc = self.speicher.naechstes(WARTET)
            if not doc:
                energie.wach_halten(False)
                self._signal.wait(3)
                self._signal.clear()
                continue
            energie.wach_halten(self.e.nachtmodus)
            try:
                self.analysieren(doc)
            except Exception as exc:
                log.exception("Analyse von %s fehlgeschlagen", doc["dateiname"])
                self.speicher.setzen(doc["id"], status=FEHLER, fehler=f"Unerwarteter Fehler: {exc}"[:300])
            finally:
                self.aktuell = None

    def _uebergabe_schleife(self) -> None:
        while not self._stopp.is_set():
            try:
                for doc in self.speicher.mit_status(BEREIT, limit=20):
                    if self._stopp.is_set():
                        break
                    self.uebergeben(doc)
                self.status_abfragen()
            except ServerFehler as exc:
                self.server_fehler = str(exc)
                self._stopp.wait(20)
            except Exception:
                log.exception("Übergabe-Schleife")
            self._stopp.wait(3)

    # ---------------------------------------------------------------- Stapel

    def stapel_anlegen(self, pfade: list[str], vorgaben: dict, name: str = "") -> dict:
        dateien = dateien_sammeln(pfade)
        if not dateien:
            return {"anzahl": 0}
        name = name or (os.path.basename(os.path.normpath(pfade[0])) if len(pfade) == 1 else f"{len(dateien)} Dateien")
        sid = self.speicher.stapel_anlegen(name, vorgaben, dateien)
        self._signal.set()
        return {"stapel_id": sid, "anzahl": len(dateien), "name": name}

    # ---------------------------------------------------------------- Analyse

    def analysieren(self, doc: dict) -> dict:
        did = doc["id"]
        self.aktuell = did
        self.speicher.setzen(did, status=ANALYSE, fehler=None)
        start = time.time()
        try:
            with open(doc["pfad"], "rb") as f:
                daten = f.read()
        except OSError as exc:
            self.speicher.setzen(did, status=FEHLER, fehler=f"Datei nicht lesbar: {exc.strerror or exc}")
            return self.speicher.dokument(did)
        sha = hashlib.sha256(daten).hexdigest()
        self.speicher.setzen(did, sha256=sha)

        try:
            if self.server and sha in self.server.hash_pruefung([sha]):
                self.speicher.setzen(did, status=DUPLIKAT, fehler="Bereits im Archiv – übersprungen")
                return self.speicher.dokument(did)
        except ServerFehler as exc:
            self.server_fehler = str(exc)
        if self.speicher.sha_bekannt(sha, did):
            self.speicher.setzen(did, status=DUPLIKAT, fehler="Gleiche Datei ist bereits in der Liste")
            return self.speicher.dokument(did)

        hinweise: list[str] = []
        try:
            text, hinweis = text_gewinnen(daten, doc["dateiname"], self.e.tesseract_pfad, self.e.ocr_sprache)
        except PasswortGeschuetzt:
            self.speicher.setzen(did, status=FEHLER,
                                 fehler="Passwortgeschützt – bitte entsperrt speichern und erneut hinzufügen")
            return self.speicher.dokument(did)
        if hinweis:
            hinweise.append(hinweis)

        stamm = self.stammdaten()
        e = feste_verfahren(daten, text, stamm.get("profile"), stamm.get("korrespondenten"))
        stapel = self.speicher.stapel(doc["stapel_id"]) if doc.get("stapel_id") else None
        vorgaben = (stapel or {}).get("vorgaben") or {}
        for feld in ("kategorie", "person", "dokumenttyp"):
            if vorgaben.get(feld):
                e.setze(feld, vorgaben[feld], QUELLE_VORGABE, True, ueberschreiben=True)
        for t in vorgaben.get("tags") or []:
            if t not in e.tags:
                e.tags.append(t)

        ki_genutzt = False
        offen = e.offene_felder(tuple(gewuenschte_felder(e)))
        if self.e.ki_aktiv and offen and text.strip():
            try:
                antwort, sekunden = self.ki.ergaenzen(text, offen, stamm)
                self._ki_uebernehmen(e, antwort)
                ki_genutzt = True
                namen = [FELDNAMEN.get(k, k) for k in antwort if k != "kategorie_sicherheit"]
                hinweise.append(f"KI ergänzte {', '.join(namen) or 'nichts'} in {sekunden:.0f} s")
            except KiFehler as exc:
                hinweise.append(f"KI nicht verfügbar: {exc}")

        plausibilitaet.pruefen(e, text, kategorien=stamm.get("kategorien"),
                               dokumenttypen=stamm.get("dokumenttypen"),
                               korrespondenten=stamm.get("korrespondenten"))
        ergebnis = e.als_dict()
        ergebnis["titel"] = titel_vorschlagen(e) or os.path.splitext(doc["dateiname"])[0]
        ergebnis["hinweise"] = hinweise
        status = SICHER if e.ist_sicher() else PRUEFEN
        if status == SICHER and self.e.sichere_automatisch_uebergeben:
            status = BEREIT
        self.speicher.setzen(did, status=status, ergebnis=ergebnis, text=text[:20000],
                             dauer=round(time.time() - start, 1), ki_genutzt=1 if ki_genutzt else 0)
        return self.speicher.dokument(did)

    @staticmethod
    def _ki_uebernehmen(e: Ergebnis, antwort: dict) -> None:
        for feld, wert in antwort.items():
            if feld == "kategorie_sicherheit":
                continue
            alt = e.felder.get(feld)
            if alt and alt.quelle in _FESTE_QUELLEN:
                if alt.wert == wert:
                    alt.sicher = True
                continue
            if alt and alt.wert == wert:
                alt.sicher, alt.hinweis = True, "Muster und KI stimmen überein"
                continue
            sicher = True  # die Plausibilitätsprüfung kann das noch kippen
            if feld == "kategorie":
                sicher = antwort.get("kategorie_sicherheit") == "hoch"
            hinweis = f"Muster fand {alt.wert}" if alt else ""
            e.felder[feld] = Feldwert(wert, QUELLE_KI, sicher, hinweis)

    # ---------------------------------------------------------------- Bearbeitung durch den Menschen

    def bearbeiten(self, did: int, aenderungen: dict) -> dict:
        doc = self.speicher.dokument(did)
        if not doc or doc["status"] not in (SICHER, PRUEFEN, UE_FEHLER):
            raise ValueError("Dokument kann in diesem Zustand nicht bearbeitet werden")
        ergebnis = doc["ergebnis"] or {}
        e = Ergebnis.aus_dict(ergebnis)
        korrekturen = list(doc["korrekturen"] or [])
        absender = aenderungen.get("felder", {}).get("absender") or e.wert("absender")
        for feld, neu in (aenderungen.get("felder") or {}).items():
            if isinstance(neu, str):
                neu = neu.strip()
            if feld in ("betrag_brutto", "betrag_netto", "mwst", "arbeitskosten") and neu not in ("", None):
                from archiv_core.muster import betrag_parsen
                neu = betrag_parsen(str(neu)) if not isinstance(neu, (int, float)) else neu
            alt = e.felder.get(feld)
            if neu in ("", None):
                if alt:
                    korrekturen.append({"feld": feld, "alt": alt.wert, "neu": None, "quelle_alt": alt.quelle,
                                        "absender": absender, "sha256": doc["sha256"]})
                    del e.felder[feld]
                continue
            if alt is None or alt.wert != neu:
                korrekturen.append({"feld": feld, "alt": alt.wert if alt else None, "neu": neu,
                                    "quelle_alt": alt.quelle if alt else None, "absender": absender,
                                    "sha256": doc["sha256"],
                                    "als_regel": bool(aenderungen.get("als_regel")) and feld == "kategorie"})
            e.felder[feld] = Feldwert(neu, QUELLE_MANUELL if (alt is None or alt.wert != neu) else alt.quelle,
                                      True if (alt is None or alt.wert != neu) else alt.sicher)
        if "tags" in aenderungen:
            e.tags = [t.strip().lstrip("#").lower() for t in aenderungen["tags"] if t.strip()]
        stamm = self.stammdaten()
        plausibilitaet.pruefen(e, doc["text"] or "", kategorien=stamm.get("kategorien"),
                               dokumenttypen=stamm.get("dokumenttypen"),
                               korrespondenten=stamm.get("korrespondenten"))
        neu_ergebnis = e.als_dict()
        neu_ergebnis["titel"] = (aenderungen.get("titel") or ergebnis.get("titel") or "").strip()
        neu_ergebnis["hinweise"] = ergebnis.get("hinweise", [])
        neu_ergebnis["neue_kategorie"] = bool(aenderungen.get("neue_kategorie"))
        self.speicher.setzen(did, ergebnis=neu_ergebnis, korrekturen=korrekturen,
                             status=SICHER if e.ist_sicher() else PRUEFEN)
        return self.speicher.dokument(did)

    def freigeben(self, did: int, geprueft: bool) -> None:
        doc = self.speicher.dokument(did)
        if doc and doc["status"] in (SICHER, PRUEFEN, UE_FEHLER):
            self.speicher.setzen(did, status=BEREIT, geprueft=1 if geprueft else 0, fehler=None)

    def alle_sicheren_freigeben(self) -> int:
        docs = self.speicher.mit_status(SICHER)
        for d in docs:
            self.speicher.setzen(d["id"], status=BEREIT, geprueft=0)
        return len(docs)

    def erneut(self, did: int) -> None:
        self.speicher.setzen(did, status=WARTET, ergebnis={}, fehler=None, korrekturen=[])
        self._signal.set()

    # ---------------------------------------------------------------- Übergabe

    def auftrag_bauen(self, doc: dict) -> dict:
        ergebnis = doc["ergebnis"] or {}
        e = Ergebnis.aus_dict(ergebnis)
        return {
            "quelle": "windows-client",
            "titel": ergebnis.get("titel") or "",
            "sha256": doc["sha256"],
            "kategorie": e.wert("kategorie") or "",
            "dokumenttyp": e.wert("dokumenttyp") or "",
            "absender": e.wert("absender") or "",
            "person": e.wert("person") or "",
            "tags": e.tags,
            "felder": {k: v.als_dict() for k, v in e.felder.items()},
            "geprueft": bool(doc["geprueft"]),
        }

    def uebergeben(self, doc: dict) -> None:
        server = self.server
        if not server:
            return
        try:
            antwort = server.importieren(doc["pfad"], doc["dateiname"], self.auftrag_bauen(doc))
        except OSError as exc:
            self.speicher.setzen(doc["id"], status=UE_FEHLER, fehler=f"Datei nicht lesbar: {exc}")
            return
        except ServerFehler as exc:
            self.speicher.setzen(doc["id"], fehler=f"Übergabe wartet: {exc}")
            raise
        if antwort.get("status") == "duplikat":
            self.speicher.setzen(doc["id"], status=DUPLIKAT, fehler="Bereits im Archiv",
                                 paperless_id=antwort.get("paperless_id"))
        else:
            self.speicher.setzen(doc["id"], status=UEBERTRAGEN, auftrag_id=antwort["id"], fehler=None)
        try:
            server.korrekturen([k for k in doc["korrekturen"] or []])
        except ServerFehler as exc:
            log.warning("Korrekturen nicht übertragen: %s", exc)

    def status_abfragen(self) -> None:
        server = self.server
        if not server:
            return
        for doc in self.speicher.mit_status(UEBERTRAGEN, limit=50):
            s = server.auftrag(doc["auftrag_id"])
            if s["status"] == "fertig":
                self.speicher.setzen(doc["id"], status=ARCHIVIERT, paperless_id=s.get("paperless_id"),
                                     link=s.get("link"), fehler=None if s.get("sicher") else
                                     "Im Paperless-Posteingang zur Prüfung")
            elif s["status"] == "duplikat":
                self.speicher.setzen(doc["id"], status=DUPLIKAT, fehler=s.get("fehler") or "Bereits im Archiv")
            elif s["status"] == "fehler":
                self.speicher.setzen(doc["id"], status=UE_FEHLER, fehler=s.get("fehler"))
        self.server_fehler = ""

    # ---------------------------------------------------------------- Übersicht

    def restzeit(self) -> int | None:
        n = self.speicher.zaehlen().get(WARTET, 0)
        dauer = self.speicher.mittlere_dauer()
        if not n:
            return 0
        return int(n * dauer) if dauer else None
