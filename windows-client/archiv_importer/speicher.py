"""Lokaler Arbeitsstand in SQLite: Stapel und Dokumente. Ein Stapel läuft nach Neustart weiter."""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime

SCHEMA = """
CREATE TABLE IF NOT EXISTS stapel (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT,
    vorgaben TEXT,
    erstellt TEXT
);
CREATE TABLE IF NOT EXISTS dokumente (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    stapel_id INTEGER,
    pfad TEXT NOT NULL,
    dateiname TEXT,
    sha256 TEXT,
    status TEXT NOT NULL,
    ergebnis TEXT,
    text TEXT,
    fehler TEXT,
    geprueft INTEGER DEFAULT 0,
    korrekturen TEXT,
    auftrag_id TEXT,
    paperless_id INTEGER,
    link TEXT,
    dauer REAL,
    ki_genutzt INTEGER DEFAULT 0,
    erstellt TEXT,
    geaendert TEXT
);
CREATE INDEX IF NOT EXISTS dokumente_status ON dokumente(status);
"""

# Status eines Dokuments
WARTET, ANALYSE, SICHER, PRUEFEN, DUPLIKAT, FEHLER = "wartet", "analyse", "sicher", "pruefen", "duplikat", "fehler"
BEREIT, UEBERTRAGEN, ARCHIVIERT, UE_FEHLER = "bereit", "uebertragen", "archiviert", "uebertragungsfehler"


def jetzt() -> str:
    return datetime.now().isoformat(timespec="seconds")


class Speicher:
    def __init__(self, pfad: str):
        self._con = sqlite3.connect(pfad, check_same_thread=False)
        self._con.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        with self._lock:
            self._con.executescript(SCHEMA)
            # nach Absturz: angefangene Analysen neu starten
            self._con.execute("UPDATE dokumente SET status = 'wartet' WHERE status = 'analyse'")
            self._con.commit()

    def _sql(self, sql: str, p=()) -> sqlite3.Cursor:
        with self._lock:
            cur = self._con.execute(sql, p)
            self._con.commit()
            return cur

    def _alle(self, sql: str, p=()) -> list[dict]:
        with self._lock:
            return [self._dict(r) for r in self._con.execute(sql, p).fetchall()]

    @staticmethod
    def _dict(r: sqlite3.Row) -> dict:
        d = dict(r)
        for feld in ("ergebnis", "korrekturen", "vorgaben"):
            if feld in d:
                d[feld] = json.loads(d[feld]) if d[feld] else ({} if feld != "korrekturen" else [])
        return d

    # ---------------------------------------------------------------- Stapel

    def stapel_anlegen(self, name: str, vorgaben: dict, pfade: list[str]) -> int:
        import os
        with self._lock:
            cur = self._con.execute("INSERT INTO stapel (name, vorgaben, erstellt) VALUES (?, ?, ?)",
                                    (name, json.dumps(vorgaben, ensure_ascii=False), jetzt()))
            sid = cur.lastrowid
            self._con.executemany(
                "INSERT INTO dokumente (stapel_id, pfad, dateiname, status, erstellt, geaendert) VALUES (?, ?, ?, ?, ?, ?)",
                [(sid, p, os.path.basename(p), WARTET, jetzt(), jetzt()) for p in pfade])
            self._con.commit()
            return sid

    def stapel(self, sid: int) -> dict | None:
        r = self._alle("SELECT * FROM stapel WHERE id = ?", (sid,))
        return r[0] if r else None

    def letzter_stapel(self) -> dict | None:
        r = self._alle("SELECT * FROM stapel ORDER BY id DESC LIMIT 1")
        return r[0] if r else None

    # ---------------------------------------------------------------- Dokumente

    def dokument(self, did: int) -> dict | None:
        r = self._alle("SELECT * FROM dokumente WHERE id = ?", (did,))
        return r[0] if r else None

    def setzen(self, did: int, **werte) -> None:
        for feld in ("ergebnis", "korrekturen"):
            if feld in werte and not isinstance(werte[feld], str):
                werte[feld] = json.dumps(werte[feld], ensure_ascii=False)
        werte["geaendert"] = jetzt()
        spalten = ", ".join(f"{k} = ?" for k in werte)
        self._sql(f"UPDATE dokumente SET {spalten} WHERE id = ?", (*werte.values(), did))

    def naechstes(self, status: str) -> dict | None:
        r = self._alle("SELECT * FROM dokumente WHERE status = ? ORDER BY id LIMIT 1", (status,))
        return r[0] if r else None

    def mit_status(self, *status: str, limit: int = 1000) -> list[dict]:
        platz = ",".join("?" * len(status))
        return self._alle(f"SELECT * FROM dokumente WHERE status IN ({platz}) ORDER BY id LIMIT ?", (*status, limit))

    def liste(self, limit: int = 500) -> list[dict]:
        # offene zuerst, dann die zuletzt archivierten
        return self._alle(
            "SELECT id, stapel_id, dateiname, status, ergebnis, fehler, geprueft, paperless_id, link, dauer "
            "FROM dokumente ORDER BY CASE status WHEN 'archiviert' THEN 1 WHEN 'duplikat' THEN 1 ELSE 0 END, "
            "stapel_id DESC, id LIMIT ?", (limit,))

    def zaehlen(self) -> dict:
        with self._lock:
            return {r["status"]: r["n"] for r in
                    self._con.execute("SELECT status, COUNT(*) AS n FROM dokumente GROUP BY status")}

    def sha_bekannt(self, sha: str, ausser: int) -> bool:
        r = self._alle("SELECT id FROM dokumente WHERE sha256 = ? AND id != ? AND status NOT IN ('fehler', 'wartet')",
                       (sha, ausser))
        return bool(r)

    def mittlere_dauer(self) -> float | None:
        with self._lock:
            r = self._con.execute("SELECT AVG(dauer) AS d FROM (SELECT dauer FROM dokumente WHERE dauer IS NOT NULL "
                                  "ORDER BY geaendert DESC LIMIT 20)").fetchone()
            return r["d"] if r and r["d"] else None

    def entfernen(self, did: int) -> None:
        self._sql("DELETE FROM dokumente WHERE id = ?", (did,))

    def aufraeumen(self) -> int:
        """Entfernt archivierte und doppelte Einträge aus der Liste."""
        return self._sql("DELETE FROM dokumente WHERE status IN ('archiviert', 'duplikat')").rowcount
