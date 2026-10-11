"""Lokale Oberfläche des Clients (nur auf 127.0.0.1, mit Sitzungs-Token gegen fremde Webseiten)."""

from __future__ import annotations

import hmac
import os
import re
import time
from datetime import datetime
from functools import wraps

from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from . import __version__
from .einstellungen import EinstellungsSpeicher, datenverzeichnis
from .ki import Ki
from .server_api import ServerApi, ServerFehler
from .speicher import WARTET, Speicher
from .verarbeitung import Verarbeitung

HIER = os.path.dirname(os.path.abspath(__file__))


class Pruefcache:
    """Merkt sich Verbindungstests für einige Sekunden, damit die Oberfläche flott bleibt."""

    def __init__(self, sekunden: float = 30):
        self.sekunden = sekunden
        self._werte: dict[str, tuple[float, dict]] = {}

    def holen(self, schluessel: str, funktion) -> dict:
        eintrag = self._werte.get(schluessel)
        if eintrag and time.time() - eintrag[0] < self.sekunden:
            return eintrag[1]
        wert = funktion()
        self._werte[schluessel] = (time.time(), wert)
        return wert

    def leeren(self) -> None:
        self._werte.clear()


def _server_pruefen(v: Verarbeitung) -> dict:
    s = v.server
    if not s:
        return {"erreichbar": False, "fehler": "Server-Adresse und API-Token fehlen", "update": None}
    try:
        status = s.status()
        client = s.client_version()
    except ServerFehler as exc:
        return {"erreichbar": False, "fehler": str(exc), "update": None}
    update = None
    if client.get("version") and client["version"] not in ("unbekannt", __version__) and \
            not __version__.endswith("-dev"):
        update = {"version": client["version"], "link": f"{v.e.server_url}/download"}
    return {"erreichbar": True, "fehler": "", "paperless": status.get("paperless"), "update": update}


def erstelle_app(v: Verarbeitung, speicher: Speicher, einst: EinstellungsSpeicher, token: str) -> Starlette:
    cache = Pruefcache()

    def geschuetzt(handler):
        @wraps(handler)
        async def pruefen(request: Request):
            host = request.headers.get("host", "")
            if not re.fullmatch(r"(127\.0\.0\.1|localhost)(:\d+)?", host):
                return PlainTextResponse("Nur lokal erreichbar", status_code=403)
            mitgegeben = request.headers.get("x-token") or request.query_params.get("t", "")
            if not hmac.compare_digest(mitgegeben.encode(), token.encode()):
                return PlainTextResponse("Ungültiges Sitzungs-Token", status_code=403)
            try:
                return await handler(request)
            except ValueError as exc:
                return JSONResponse({"fehler": str(exc)}, status_code=400)
        return pruefen

    def kurz(doc: dict) -> dict:
        e = doc.get("ergebnis") or {}
        f = e.get("felder") or {}
        wert = lambda n: (f.get(n) or {}).get("wert")  # noqa: E731
        return {"id": doc["id"], "status": doc["status"], "dateiname": doc["dateiname"],
                "absender": wert("absender"), "datum": wert("datum"), "betrag": wert("betrag_brutto"),
                "kategorie": wert("kategorie"), "fehler": doc.get("fehler"), "link": doc.get("link"),
                "geprueft": bool(doc.get("geprueft"))}

    @geschuetzt
    async def startseite(request: Request):
        with open(os.path.join(HIER, "static", "index.html"), encoding="utf-8") as f:
            return HTMLResponse(f.read())

    @geschuetzt
    async def zustand(request: Request):
        zaehler = speicher.zaehlen()
        stapel = speicher.letzter_stapel()
        ki = await run_in_threadpool(cache.holen, "ki", lambda: Ki(v.e.ollama_url, v.e.modell).verbindung()) \
            if v.e.ki_aktiv else {"erreichbar": False, "modell_vorhanden": False, "aus": True}
        server = await run_in_threadpool(cache.holen, "server", lambda: _server_pruefen(v))
        return JSONResponse({
            "version": __version__,
            "zaehler": zaehler,
            "gesamt": sum(zaehler.values()),
            "offen": zaehler.get(WARTET, 0) + zaehler.get("analyse", 0),
            "aktuell": v.aktuell,
            "pausiert": v.pausiert.is_set(),
            "restzeit": v.restzeit(),
            "mittlere_dauer": speicher.mittlere_dauer(),
            "stapel": {"name": stapel["name"], "vorgaben": stapel["vorgaben"]} if stapel else None,
            "ki": {**ki, "modell": v.e.modell, "aktiv": v.e.ki_aktiv},
            "server": {**server, "uebergabe_fehler": v.server_fehler},
            "einstellungen_fehlen": not v.e.vollstaendig(),
            "nachtmodus": v.e.nachtmodus,
            "dokumente": [kurz(d) for d in speicher.liste()],
        })

    @geschuetzt
    async def stammdaten(request: Request):
        return JSONResponse(await run_in_threadpool(v.stammdaten))

    @geschuetzt
    async def dokument(request: Request):
        did = int(request.path_params["id"])
        if request.method == "POST":
            daten = await request.json()
            doc = await run_in_threadpool(v.bearbeiten, did, daten)
        else:
            doc = speicher.dokument(did)
        if not doc:
            return JSONResponse({"fehler": "Unbekanntes Dokument"}, status_code=404)
        doc = dict(doc)
        doc["text"] = (doc.get("text") or "")[:3000]
        return JSONResponse(doc)

    @geschuetzt
    async def aktion(request: Request):
        did = int(request.path_params["id"])
        was = request.path_params["aktion"]
        daten = await request.json() if request.headers.get("content-length", "0") != "0" else {}
        if was == "freigeben":
            v.freigeben(did, bool(daten.get("geprueft", True)))
        elif was == "erneut":
            v.erneut(did)
        elif was == "entfernen":
            speicher.entfernen(did)
        else:
            return JSONResponse({"fehler": "Unbekannte Aktion"}, status_code=400)
        return JSONResponse({"ok": True})

    @geschuetzt
    async def alle_sicheren(request: Request):
        return JSONResponse({"anzahl": v.alle_sicheren_freigeben()})

    @geschuetzt
    async def stapel(request: Request):
        daten = await request.json()
        pfade = [p for p in daten.get("pfade") or [] if p]
        ergebnis = await run_in_threadpool(v.stapel_anlegen, pfade, daten.get("vorgaben") or {})
        if not ergebnis["anzahl"]:
            return JSONResponse({"fehler": "Keine PDFs oder Bilder gefunden"}, status_code=400)
        return JSONResponse(ergebnis)

    @geschuetzt
    async def hochladen(request: Request):
        ziel = os.path.join(datenverzeichnis(), "hochgeladen", datetime.now().strftime("%Y%m%d-%H%M%S"))
        os.makedirs(ziel, exist_ok=True)
        pfade = []
        async with request.form() as form:
            import json
            vorgaben = json.loads(str(form.get("vorgaben") or "{}"))
            for datei in form.getlist("dateien"):
                if not hasattr(datei, "read"):
                    continue
                name = os.path.basename(datei.filename or "datei.pdf")
                pfad = os.path.join(ziel, name)
                with open(pfad, "wb") as f:
                    f.write(await datei.read())
                pfade.append(pfad)
        ergebnis = await run_in_threadpool(v.stapel_anlegen, pfade, vorgaben,
                                           f"{len(pfade)} hochgeladene Datei(en)")
        return JSONResponse(ergebnis)

    @geschuetzt
    async def pause(request: Request):
        daten = await request.json()
        if daten.get("an"):
            v.pausiert.set()
        else:
            v.pausiert.clear()
        v.anstossen()
        return JSONResponse({"pausiert": v.pausiert.is_set()})

    @geschuetzt
    async def aufraeumen(request: Request):
        return JSONResponse({"entfernt": speicher.aufraeumen()})

    @geschuetzt
    async def einstellungen(request: Request):
        if request.method == "POST":
            neu = await request.json()
            e = einst.aktualisieren(v.e, neu)
            v.einstellungen_geaendert(e)
            cache.leeren()
        return JSONResponse(v.e.oeffentlich())

    @geschuetzt
    async def verbindung_testen(request: Request):
        cache.leeren()
        server = await run_in_threadpool(_server_pruefen, v)
        ki = await run_in_threadpool(Ki(v.e.ollama_url, v.e.modell).verbindung)
        return JSONResponse({"server": server, "ki": ki, "tesseract": bool(v.e.tesseract_pfad and
                                                                            os.path.exists(v.e.tesseract_pfad))})

    @geschuetzt
    async def datei(request: Request):
        doc = speicher.dokument(int(request.path_params["id"]))
        if not doc or not os.path.exists(doc["pfad"]):
            return PlainTextResponse("Datei nicht gefunden", status_code=404)
        return FileResponse(doc["pfad"], content_disposition_type="inline", filename=doc["dateiname"])

    return Starlette(routes=[
        Route("/", startseite),
        Route("/api/zustand", zustand),
        Route("/api/stammdaten", stammdaten),
        Route("/api/dokument/{id:int}", dokument, methods=["GET", "POST"]),
        Route("/api/dokument/{id:int}/{aktion}", aktion, methods=["POST"]),
        Route("/api/alle-sicheren", alle_sicheren, methods=["POST"]),
        Route("/api/stapel", stapel, methods=["POST"]),
        Route("/api/hochladen", hochladen, methods=["POST"]),
        Route("/api/pause", pause, methods=["POST"]),
        Route("/api/aufraeumen", aufraeumen, methods=["POST"]),
        Route("/api/einstellungen", einstellungen, methods=["GET", "POST"]),
        Route("/api/verbindung-testen", verbindung_testen, methods=["POST"]),
        Route("/datei/{id:int}", datei),
        Mount("/static", StaticFiles(directory=os.path.join(HIER, "static")), name="static"),
    ])
