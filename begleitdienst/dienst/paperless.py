"""Schlanker Client für die REST-API von Paperless-ngx."""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

import requests

from archiv_core.felder import BENUTZERFELDER, FeldDef

log = logging.getLogger(__name__)

_PAPERLESS_TYP = {"text": "string", "datum": "date", "betrag": "monetary", "auswahl": "select",
                  "ja_nein": "boolean"}


class PaperlessFehler(Exception):
    pass


class Duplikat(PaperlessFehler):
    pass


class Paperless:
    def __init__(self, url: str, token: str, timeout: float = 30, cache_sekunden: float = 60,
                 ssl_pruefen: bool = True):
        self.url = url.rstrip("/")
        self.timeout = timeout
        self.sitzung = requests.Session()
        self.sitzung.verify = ssl_pruefen
        if not ssl_pruefen:
            import urllib3
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        token = token.strip()
        if token.lower().startswith("token "):
            token = token[6:].strip()
        self.sitzung.headers.update({"Authorization": f"Token {token}", "Accept": "application/json"})
        self._cache: dict[str, tuple[float, list]] = {}
        self._cache_sekunden = cache_sekunden
        self._lock = threading.RLock()

    # ---------------------------------------------------------------- Grundlagen

    def _anfrage(self, methode: str, pfad: str, **kw) -> requests.Response:
        url = pfad if pfad.startswith("http") else f"{self.url}{pfad}"
        try:
            antwort = self.sitzung.request(methode, url, timeout=kw.pop("timeout", self.timeout), **kw)
        except requests.RequestException as exc:
            raise PaperlessFehler(f"Paperless nicht erreichbar: {exc}") from exc
        if antwort.status_code >= 400:
            raise PaperlessFehler(f"{methode} {pfad} → {antwort.status_code}: {antwort.text[:300]}")
        return antwort

    def _json(self, methode: str, pfad: str, **kw) -> Any:
        antwort = self._anfrage(methode, pfad, **kw)
        return antwort.json() if antwort.content else None

    def _alle(self, pfad: str, params: dict | None = None) -> list[dict]:
        ergebnis: list[dict] = []
        params = {"page_size": 100, **(params or {})}
        naechste: str | None = pfad
        while naechste:
            daten = self._json("GET", naechste, params=params if naechste == pfad else None)
            ergebnis.extend(daten.get("results", []))
            naechste = daten.get("next")
        return ergebnis

    def verbindung_pruefen(self) -> tuple[bool, str]:
        """Prüft Adresse und Token. Gibt (ok, Grund in Klartext) zurück."""
        try:
            r = self.sitzung.get(f"{self.url}/api/documents/", params={"page_size": 1}, timeout=15)
        except requests.exceptions.ConnectTimeout:
            return False, f"Zeitüberschreitung beim Verbinden mit {self.url}"
        except requests.exceptions.SSLError:
            return False, ("Das HTTPS-Zertifikat von Paperless wird nicht akzeptiert (selbst signiert?) – "
                           "Option paperless_ssl_pruefen ausschalten")
        except requests.exceptions.ConnectionError as exc:
            text = str(exc)
            if "Name or service not known" in text or "resolve" in text:
                grund = "Name nicht auflösbar"
            elif "reset by peer" in text or "aborted" in text.lower() or "RemoteDisconnected" in text:
                grund = ("Verbindung vom Server abgebrochen – meist sind http:// und https:// vertauscht; "
                         "die Adresse genau so eintragen, wie sie im Browser funktioniert")
            else:
                grund = "Verbindung abgelehnt oder kein Netz"
            return False, f"Keine Verbindung zu {self.url} ({grund})"
        except requests.RequestException as exc:
            return False, f"Keine Verbindung zu {self.url}: {exc.__class__.__name__}"
        if r.status_code in (401, 403):
            return False, (f"Paperless lehnt das API-Token ab (HTTP {r.status_code}). Token in den Optionen prüfen: "
                           "nur das Token selbst, ohne „Token “ davor")
        if r.status_code == 400:
            return False, ("Paperless lehnt die Anfrage ab (HTTP 400) – meist passt die Adresse nicht zu "
                           "PAPERLESS_URL bzw. PAPERLESS_ALLOWED_HOSTS in Paperless")
        if r.status_code == 404:
            return False, f"Unter {self.url}/api/ antwortet kein Paperless (HTTP 404) – Adresse oder Port prüfen"
        if r.status_code >= 400:
            return False, f"Paperless antwortet mit HTTP {r.status_code}"
        if "json" not in r.headers.get("content-type", ""):
            return False, (f"Unter {self.url} antwortet etwas, aber nicht die Paperless-API "
                           "(z. B. eine Anmeldeseite) – Adresse prüfen")
        return True, ""

    def erreichbar(self) -> bool:
        return self.verbindung_pruefen()[0]

    # ---------------------------------------------------------------- Stammdaten mit Cache

    def _liste(self, art: str) -> list[dict]:
        with self._lock:
            eintrag = self._cache.get(art)
            if eintrag and time.time() - eintrag[0] < self._cache_sekunden:
                return eintrag[1]
            daten = self._alle(f"/api/{art}/")
            self._cache[art] = (time.time(), daten)
            return daten

    def cache_leeren(self, art: str | None = None) -> None:
        with self._lock:
            if art:
                self._cache.pop(art, None)
            else:
                self._cache.clear()

    def tags(self) -> list[dict]:
        return self._liste("tags")

    def korrespondenten(self) -> list[dict]:
        return self._liste("correspondents")

    def dokumenttypen(self) -> list[dict]:
        return self._liste("document_types")

    def benutzerfelder(self) -> list[dict]:
        return self._liste("custom_fields")

    def _sicherstellen(self, art: str, name: str, extra: dict | None = None) -> int:
        name = name.strip()
        for eintrag in self._liste(art):
            if eintrag["name"].lower() == name.lower():
                return eintrag["id"]
        with self._lock:
            self.cache_leeren(art)
            for eintrag in self._liste(art):  # evtl. von anderem Thread angelegt
                if eintrag["name"].lower() == name.lower():
                    return eintrag["id"]
            neu = self._json("POST", f"/api/{art}/", json={"name": name, **(extra or {})})
            self.cache_leeren(art)
            log.info("In Paperless angelegt (%s): %s", art, name)
            return neu["id"]

    def tag_sicherstellen(self, name: str, posteingang: bool = False) -> int:
        extra = {"matching_algorithm": 0}
        if posteingang:
            extra["is_inbox_tag"] = True
        return self._sicherstellen("tags", name, extra)

    def korrespondent_sicherstellen(self, name: str) -> int:
        return self._sicherstellen("correspondents", name)

    def dokumenttyp_sicherstellen(self, name: str) -> int:
        return self._sicherstellen("document_types", name)

    # ---------------------------------------------------------------- Benutzerfelder

    @staticmethod
    def _optionen(feld: dict) -> list:
        return list((feld.get("extra_data") or {}).get("select_options") or [])

    @staticmethod
    def _optionsname(option) -> str:
        return option.get("label", "") if isinstance(option, dict) else str(option)

    def benutzerfelder_sicherstellen(self, personen: list[str]) -> None:
        vorhanden = {f["name"].lower(): f for f in self.benutzerfelder()}
        for fd in BENUTZERFELDER:
            optionen = list(personen) if fd.name == "person" else list(fd.optionen)
            feld = vorhanden.get(fd.label.lower())
            if feld is None:
                self._feld_anlegen(fd, optionen)
            elif fd.typ == "auswahl":
                fehlend = [o for o in optionen
                           if o.lower() not in {self._optionsname(x).lower() for x in self._optionen(feld)}]
                if fehlend:
                    self._optionen_ergaenzen(feld, fehlend)
        self.cache_leeren("custom_fields")

    def _feld_anlegen(self, fd: FeldDef, optionen: list[str]) -> None:
        daten: dict = {"name": fd.label, "data_type": _PAPERLESS_TYP[fd.typ]}
        if fd.typ == "betrag":
            daten["extra_data"] = {"default_currency": "EUR"}
        if fd.typ == "auswahl":
            daten["extra_data"] = {"select_options": [{"label": o} for o in optionen]}
        try:
            self._json("POST", "/api/custom_fields/", json=daten)
        except PaperlessFehler:
            if fd.typ != "auswahl":
                raise
            daten["extra_data"] = {"select_options": optionen}  # ältere Paperless-Versionen
            self._json("POST", "/api/custom_fields/", json=daten)
        log.info("Benutzerfeld angelegt: %s", fd.label)

    def _optionen_ergaenzen(self, feld: dict, fehlend: list[str]) -> None:
        alt = self._optionen(feld)
        if alt and isinstance(alt[0], dict):
            neu = alt + [{"label": o} for o in fehlend]
        else:
            neu = [self._optionsname(o) for o in alt] + fehlend
        extra = dict(feld.get("extra_data") or {})
        extra["select_options"] = neu
        self._json("PATCH", f"/api/custom_fields/{feld['id']}/", json={"extra_data": extra})

    def benutzerfeld_wert(self, fd: FeldDef, wert: Any) -> tuple[int, Any] | None:
        """Wandelt einen Wert in das Paperless-Format: (Feld-ID, Wert) oder None."""
        feld = next((f for f in self.benutzerfelder() if f["name"].lower() == fd.label.lower()), None)
        if feld is None or wert in (None, ""):
            return None
        if fd.typ == "betrag":
            try:
                return feld["id"], f"EUR{float(wert):.2f}"
            except (TypeError, ValueError):
                return None
        if fd.typ == "datum":
            return feld["id"], str(wert)[:10]
        if fd.typ == "ja_nein":
            return feld["id"], bool(wert)
        if fd.typ == "auswahl":
            for i, option in enumerate(self._optionen(feld)):
                if self._optionsname(option).lower() == str(wert).lower():
                    return feld["id"], option.get("id", i) if isinstance(option, dict) and option.get("id") else i
            return None
        return feld["id"], str(wert)

    def benutzerfeld_lesen(self, feld_id: int, wert: Any) -> tuple[str, Any] | None:
        """Umkehrung: Paperless-Wert → (interner Feldname, Wert)."""
        feld = next((f for f in self.benutzerfelder() if f["id"] == feld_id), None)
        if not feld:
            return None
        fd = next((d for d in BENUTZERFELDER if d.label.lower() == feld["name"].lower()), None)
        if not fd or wert in (None, ""):
            return None
        if fd.typ == "betrag":
            try:
                return fd.name, float(str(wert).lstrip("EURUSD€ "))
            except ValueError:
                return None
        if fd.typ == "auswahl":
            optionen = self._optionen(feld)
            for i, o in enumerate(optionen):
                if (isinstance(o, dict) and o.get("id") == wert) or i == wert:
                    return fd.name, self._optionsname(o)
            return None
        return fd.name, wert

    # ---------------------------------------------------------------- Dokumente

    def hochladen(self, daten: bytes, dateiname: str, titel: str | None = None) -> str:
        felder = {"title": titel} if titel else {}
        antwort = self._json("POST", "/api/documents/post_document/",
                             files={"document": (dateiname, daten)}, data=felder, timeout=120)
        return antwort if isinstance(antwort, str) else str(antwort.get("task_id", antwort))

    def dokument_per_pruefsumme(self, md5: str) -> int | None:
        """Sucht ein Dokument über die MD5-Prüfsumme des Originals. None, wenn nicht eindeutig gefunden."""
        try:
            daten = self._json("GET", "/api/documents/", params={"checksum__iexact": md5, "page_size": 2})
            treffer = daten.get("results", []) if isinstance(daten, dict) else []
            if len(treffer) != 1:
                return None
            doc_id = int(treffer[0]["id"])
            # absichern, falls eine Paperless-Version den Filter ignoriert
            meta = self._json("GET", f"/api/documents/{doc_id}/metadata/")
            if str(meta.get("original_checksum", "")).lower() == md5.lower():
                return doc_id
        except (PaperlessFehler, KeyError, ValueError, TypeError) as exc:
            log.debug("Suche über Prüfsumme nicht möglich: %s", exc)
        return None

    def auf_aufgabe_warten(self, task_id: str, timeout: float = 900, intervall: float = 2,
                           md5: str | None = None) -> int:
        """Wartet, bis Paperless das Dokument angelegt hat, und gibt dessen ID zurück.

        Primär über die Aufgabe (task), zusätzlich über die Prüfsumme – manche Paperless-Versionen
        liefern die Aufgabe nicht zuverlässig über /api/tasks/ aus."""
        ende = time.time() + timeout
        naechste_suche = time.time() + 10
        zuletzt = "keine Antwort"
        while time.time() < ende:
            try:
                aufgaben = self._json("GET", "/api/tasks/", params={"task_id": task_id})
            except PaperlessFehler as exc:
                aufgaben, zuletzt = [], str(exc)[:200]
            if isinstance(aufgaben, dict):
                aufgaben = aufgaben.get("results", [])
            a = next((x for x in aufgaben or [] if str(x.get("task_id")) == str(task_id)), None)
            if a is None and aufgaben:
                zuletzt = f"{len(aufgaben)} Aufgabe(n), aber nicht {task_id}"
            if a:
                status = a.get("status")
                zuletzt = f"Status {status}"
                if status == "SUCCESS" and a.get("related_document") not in (None, ""):
                    return int(a["related_document"])
                if status in ("FAILURE", "REVOKED"):
                    meldung = str(a.get("result") or "")
                    if "duplicate" in meldung.lower() or "duplikat" in meldung.lower():
                        raise Duplikat(meldung)
                    raise PaperlessFehler(f"Paperless konnte das Dokument nicht verarbeiten: {meldung}")
            if md5 and time.time() >= naechste_suche:
                doc_id = self.dokument_per_pruefsumme(md5)
                if doc_id:
                    log.info("Dokument %s über Prüfsumme gefunden (Aufgabe: %s)", doc_id, zuletzt)
                    return doc_id
                naechste_suche = time.time() + 10
            time.sleep(intervall)
        raise PaperlessFehler(f"Zeitüberschreitung beim Warten auf Paperless (zuletzt: {zuletzt})")

    def dokument(self, doc_id: int) -> dict:
        return self._json("GET", f"/api/documents/{doc_id}/")

    def dokument_aendern(self, doc_id: int, daten: dict) -> dict:
        try:
            return self._json("PATCH", f"/api/documents/{doc_id}/", json=daten)
        except PaperlessFehler as exc:
            # ältere Versionen erwarten beim Erstellungsdatum einen Zeitstempel
            if "created" in daten and "created" in str(exc):
                daten = dict(daten)
                daten["created"] = f"{daten['created']}T12:00:00"
                return self._json("PATCH", f"/api/documents/{doc_id}/", json=daten)
            raise

    def notiz_hinzufuegen(self, doc_id: int, text: str) -> None:
        try:
            self._json("POST", f"/api/documents/{doc_id}/notes/", json={"note": text})
        except PaperlessFehler as exc:
            log.warning("Notiz nicht gespeichert: %s", exc)

    def anzahl_mit_tag(self, tag_id: int) -> int:
        daten = self._json("GET", "/api/documents/", params={"tags__id__all": tag_id, "page_size": 1})
        return int(daten.get("count", 0))
