"""Bereitstellung des Windows-Clients zum Herunterladen über die Weboberfläche."""

from __future__ import annotations

import json
import logging
import os
import re

import requests

from .db import jetzt

log = logging.getLogger(__name__)

DATEINAME = "ArchivImporter.exe"


class Downloads:
    def __init__(self, verzeichnis: str, github_repo: str = "", github_token: str = ""):
        self.verzeichnis = os.path.join(verzeichnis, "downloads")
        os.makedirs(self.verzeichnis, exist_ok=True)
        self.github_repo = github_repo
        self.github_token = github_token

    @property
    def pfad(self) -> str:
        return os.path.join(self.verzeichnis, DATEINAME)

    def info(self) -> dict | None:
        if not os.path.exists(self.pfad):
            return None
        meta = {}
        meta_pfad = os.path.join(self.verzeichnis, "version.json")
        if os.path.exists(meta_pfad):
            with open(meta_pfad, encoding="utf-8") as f:
                meta = json.load(f)
        return {"version": meta.get("version", "unbekannt"), "datum": meta.get("datum", ""),
                "groesse_mb": round(os.path.getsize(self.pfad) / 1_048_576, 1), "datei": DATEINAME,
                "quelle": meta.get("quelle", "")}

    def speichern(self, daten: bytes, version: str, quelle: str) -> dict:
        if not daten.startswith(b"MZ"):
            raise ValueError("Die Datei ist kein Windows-Programm (.exe).")
        version = re.sub(r"[^\w.\-]", "", version or "") or "unbekannt"
        tmp = self.pfad + ".neu"
        with open(tmp, "wb") as f:
            f.write(daten)
        os.replace(tmp, self.pfad)
        with open(os.path.join(self.verzeichnis, "version.json"), "w", encoding="utf-8") as f:
            json.dump({"version": version, "datum": jetzt(), "quelle": quelle}, f)
        return self.info() or {}

    def von_github_holen(self) -> dict:
        if not self.github_repo:
            raise ValueError("Kein GitHub-Repository konfiguriert (Option github_repo).")
        kopf = {"Accept": "application/vnd.github+json"}
        if self.github_token:
            kopf["Authorization"] = f"Bearer {self.github_token}"
        r = requests.get(f"https://api.github.com/repos/{self.github_repo}/releases", headers=kopf, timeout=30)
        r.raise_for_status()
        for release in r.json():
            if release.get("draft") or not str(release.get("tag_name", "")).startswith("client-v"):
                continue
            asset = next((a for a in release.get("assets", []) if a["name"].lower().endswith(".exe")), None)
            if not asset:
                continue
            kopf_datei = dict(kopf, Accept="application/octet-stream")
            datei = requests.get(asset["url"], headers=kopf_datei, timeout=300)
            datei.raise_for_status()
            version = release["tag_name"].removeprefix("client-v")
            log.info("Windows-Client %s von GitHub geladen", version)
            return self.speichern(datei.content, version, f"GitHub {self.github_repo}")
        raise ValueError("Kein Release mit Windows-Client (Tag client-v…) gefunden.")
