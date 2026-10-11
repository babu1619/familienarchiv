"""SQLite-Datenbank des Begleitdienstes: Aufträge, Feldquellen, Mails, Korrekturen, Profile."""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime

SCHEMA = """
CREATE TABLE IF NOT EXISTS auftraege (
    id TEXT PRIMARY KEY,
    quelle TEXT NOT NULL,
    dateiname TEXT,
    sha256 TEXT,
    status TEXT NOT NULL,             -- wartet | laeuft | fertig | duplikat | fehler
    daten TEXT,                       -- JSON: Vorgaben und Felder des Auftrags
    paperless_task TEXT,
    paperless_id INTEGER,
    sicher INTEGER,
    fehler TEXT,
    erstellt TEXT NOT NULL,
    geaendert TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS auftraege_status ON auftraege(status);
CREATE TABLE IF NOT EXISTS dateien (
    sha256 TEXT PRIMARY KEY,
    paperless_id INTEGER,
    erstellt TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS feldwerte (
    paperless_id INTEGER NOT NULL,
    feld TEXT NOT NULL,
    wert TEXT,
    quelle TEXT,
    sicher INTEGER,
    PRIMARY KEY (paperless_id, feld)
);
CREATE TABLE IF NOT EXISTS mails (
    message_id TEXT PRIMARY KEY,
    absender TEXT,
    betreff TEXT,
    ergebnis TEXT,
    details TEXT,
    zeit TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS korrekturen (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sha256 TEXT,
    paperless_id INTEGER,
    absender TEXT,
    feld TEXT,
    alt TEXT,
    neu TEXT,
    quelle_alt TEXT,
    zeit TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS profile (
    name TEXT PRIMARY KEY,
    ust_id TEXT,
    iban TEXT,
    domain TEXT,
    kategorie TEXT,
    kategorie_zaehler INTEGER DEFAULT 0,
    tags TEXT,
    stichworte TEXT,
    aktualisiert TEXT
);
CREATE TABLE IF NOT EXISTS zustand (
    schluessel TEXT PRIMARY KEY,
    wert TEXT
);
"""


def jetzt() -> str:
    return datetime.now().isoformat(timespec="seconds")


class Datenbank:
    def __init__(self, pfad: str):
        self._con = sqlite3.connect(pfad, check_same_thread=False)
        self._con.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        with self._lock:
            self._con.executescript(SCHEMA)
            self._con.commit()

    def ausfuehren(self, sql: str, parameter=()) -> sqlite3.Cursor:
        with self._lock:
            cur = self._con.execute(sql, parameter)
            self._con.commit()
            return cur

    def alle(self, sql: str, parameter=()) -> list[dict]:
        with self._lock:
            return [dict(r) for r in self._con.execute(sql, parameter).fetchall()]

    def eins(self, sql: str, parameter=()) -> dict | None:
        with self._lock:
            r = self._con.execute(sql, parameter).fetchone()
            return dict(r) if r else None

    # ---------------------------------------------------------------- Aufträge

    def auftrag_anlegen(self, auftrag_id: str, quelle: str, dateiname: str, sha256: str,
                        daten: dict, status: str = "wartet") -> None:
        self.ausfuehren(
            "INSERT INTO auftraege (id, quelle, dateiname, sha256, status, daten, erstellt, geaendert) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (auftrag_id, quelle, dateiname, sha256, status, json.dumps(daten, ensure_ascii=False), jetzt(), jetzt()))

    def auftrag_setzen(self, auftrag_id: str, **werte) -> None:
        werte["geaendert"] = jetzt()
        spalten = ", ".join(f"{k} = ?" for k in werte)
        self.ausfuehren(f"UPDATE auftraege SET {spalten} WHERE id = ?", (*werte.values(), auftrag_id))

    def auftrag(self, auftrag_id: str) -> dict | None:
        a = self.eins("SELECT * FROM auftraege WHERE id = ?", (auftrag_id,))
        if a and a.get("daten"):
            a["daten"] = json.loads(a["daten"])
        return a

    def naechster_auftrag(self) -> dict | None:
        a = self.eins("SELECT id FROM auftraege WHERE status = 'wartet' ORDER BY erstellt LIMIT 1")
        return self.auftrag(a["id"]) if a else None

    def auftraege(self, limit: int = 50, status: str | None = None) -> list[dict]:
        if status:
            return self.alle("SELECT * FROM auftraege WHERE status = ? ORDER BY erstellt DESC LIMIT ?", (status, limit))
        return self.alle("SELECT * FROM auftraege ORDER BY erstellt DESC LIMIT ?", (limit,))

    def zaehlen(self, sql_bedingung: str = "1=1", parameter=()) -> int:
        r = self.eins(f"SELECT COUNT(*) AS n FROM auftraege WHERE {sql_bedingung}", parameter)
        return r["n"] if r else 0

    # ---------------------------------------------------------------- Dateien und Felder

    def datei_bekannt(self, sha256: str) -> dict | None:
        return self.eins("SELECT * FROM dateien WHERE sha256 = ?", (sha256,))

    def datei_merken(self, sha256: str, paperless_id: int | None) -> None:
        self.ausfuehren("INSERT OR REPLACE INTO dateien (sha256, paperless_id, erstellt) VALUES (?, ?, ?)",
                        (sha256, paperless_id, jetzt()))

    def feldwerte_speichern(self, paperless_id: int, felder: dict) -> None:
        for name, fw in felder.items():
            self.ausfuehren(
                "INSERT OR REPLACE INTO feldwerte (paperless_id, feld, wert, quelle, sicher) VALUES (?, ?, ?, ?, ?)",
                (paperless_id, name, json.dumps(fw["wert"], ensure_ascii=False), fw.get("quelle"),
                 1 if fw.get("sicher") else 0))

    def faellige(self, von: str, bis: str) -> list[dict]:
        return self.alle(
            "SELECT paperless_id, json_extract(wert, '$') AS datum FROM feldwerte "
            "WHERE feld = 'faelligkeit' AND json_extract(wert, '$') BETWEEN ? AND ? ORDER BY datum", (von, bis))

    # ---------------------------------------------------------------- Mails, Korrekturen

    def mail_bekannt(self, message_id: str) -> bool:
        return self.eins("SELECT 1 AS x FROM mails WHERE message_id = ?", (message_id,)) is not None

    def mail_merken(self, message_id: str, absender: str, betreff: str, ergebnis: str, details: str) -> None:
        self.ausfuehren("INSERT OR REPLACE INTO mails VALUES (?, ?, ?, ?, ?, ?)",
                        (message_id, absender, betreff, ergebnis, details, jetzt()))

    def korrektur_merken(self, eintrag: dict) -> None:
        self.ausfuehren(
            "INSERT INTO korrekturen (sha256, paperless_id, absender, feld, alt, neu, quelle_alt, zeit) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (eintrag.get("sha256"), eintrag.get("paperless_id"), eintrag.get("absender"), eintrag.get("feld"),
             json.dumps(eintrag.get("alt"), ensure_ascii=False), json.dumps(eintrag.get("neu"), ensure_ascii=False),
             eintrag.get("quelle_alt"), jetzt()))

    # ---------------------------------------------------------------- Profile

    def profile(self) -> list[dict]:
        ergebnis = []
        for p in self.alle("SELECT * FROM profile"):
            p["tags"] = json.loads(p["tags"] or "[]")
            p["stichworte"] = json.loads(p["stichworte"] or "[]")
            p["kategorie_sicher"] = (p.get("kategorie_zaehler") or 0) >= 2
            ergebnis.append(p)
        return ergebnis

    def profil(self, name: str) -> dict | None:
        return next((p for p in self.profile() if p["name"].lower() == name.lower()), None)

    def profil_speichern(self, p: dict) -> None:
        self.ausfuehren(
            "INSERT OR REPLACE INTO profile (name, ust_id, iban, domain, kategorie, kategorie_zaehler, tags, "
            "stichworte, aktualisiert) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (p["name"], p.get("ust_id"), p.get("iban"), p.get("domain"), p.get("kategorie"),
             p.get("kategorie_zaehler", 0), json.dumps(p.get("tags") or [], ensure_ascii=False),
             json.dumps(p.get("stichworte") or [], ensure_ascii=False), jetzt()))

    # ---------------------------------------------------------------- Zustand

    def zustand(self, schluessel: str, standard: str = "") -> str:
        r = self.eins("SELECT wert FROM zustand WHERE schluessel = ?", (schluessel,))
        return r["wert"] if r else standard

    def zustand_setzen(self, schluessel: str, wert: str) -> None:
        self.ausfuehren("INSERT OR REPLACE INTO zustand VALUES (?, ?)", (schluessel, wert))
