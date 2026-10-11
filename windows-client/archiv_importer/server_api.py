"""Verbindung zum Begleitdienst."""

from __future__ import annotations

import json
import time

import requests


class ServerFehler(Exception):
    pass


class ServerApi:
    def __init__(self, url: str, token: str, timeout: float = 30):
        self.url = url.rstrip("/")
        self.sitzung = requests.Session()
        self.sitzung.headers["Authorization"] = f"Bearer {token}"
        self.timeout = timeout
        self._stamm: tuple[float, dict] | None = None

    def _anfrage(self, methode: str, pfad: str, **kw):
        try:
            r = self.sitzung.request(methode, self.url + pfad, timeout=kw.pop("timeout", self.timeout), **kw)
        except requests.RequestException as exc:
            raise ServerFehler(f"Server nicht erreichbar: {exc.__class__.__name__}") from exc
        if r.status_code == 401:
            raise ServerFehler("API-Token wird vom Server abgelehnt")
        if r.status_code >= 400:
            try:
                meldung = r.json().get("fehler", r.text)
            except ValueError:
                meldung = r.text
            raise ServerFehler(f"Server meldet {r.status_code}: {meldung[:200]}")
        return r.json()

    def status(self) -> dict:
        return self._anfrage("GET", "/api/v1/status", timeout=8)

    def stammdaten(self, max_alter: float = 300) -> dict:
        if self._stamm and time.time() - self._stamm[0] < max_alter:
            return self._stamm[1]
        daten = self._anfrage("GET", "/api/v1/stammdaten")
        self._stamm = (time.time(), daten)
        return daten

    def stammdaten_verwerfen(self) -> None:
        self._stamm = None

    def hash_pruefung(self, hashes: list[str]) -> set[str]:
        if not hashes:
            return set()
        return set(self._anfrage("POST", "/api/v1/hash-pruefung", json={"sha256": hashes})["vorhanden"])

    def importieren(self, pfad: str, dateiname: str, daten: dict) -> dict:
        with open(pfad, "rb") as f:
            return self._anfrage("POST", "/api/v1/import", files={"datei": (dateiname, f)},
                                 data={"daten": json.dumps(daten, ensure_ascii=False)}, timeout=300)

    def auftrag(self, auftrag_id: str) -> dict:
        return self._anfrage("GET", f"/api/v1/import/{auftrag_id}")

    def korrekturen(self, eintraege: list[dict]) -> None:
        if eintraege:
            self._anfrage("POST", "/api/v1/korrekturen", json={"eintraege": eintraege})

    def client_version(self) -> dict:
        return self._anfrage("GET", "/api/v1/client/version", timeout=8)
