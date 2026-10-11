"""Weboberfläche und REST-API des Begleitdienstes (Starlette)."""

from __future__ import annotations

import base64
import hmac
import json
import logging
import os
from datetime import date, timedelta
from functools import wraps
from urllib.parse import quote, urlparse

from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles
from starlette.templating import Jinja2Templates

from archiv_core.muster import betrag_formatieren

from . import __version__
from .db import Datenbank
from .downloads import Downloads
from .importer import Importer
from .konfig import Konfig
from .paperless import Paperless, PaperlessFehler

log = logging.getLogger(__name__)
HIER = os.path.dirname(os.path.abspath(__file__))


def _gleich(a: str, b: str) -> bool:
    return bool(a) and bool(b) and hmac.compare_digest(a.encode(), b.encode())


def erstelle_app(k: Konfig, db: Datenbank, p: Paperless, importer: Importer, downloads: Downloads,
                 mail=None) -> Starlette:
    vorlagen = Jinja2Templates(directory=os.path.join(HIER, "templates"))
    vorlagen.env.filters["euro"] = lambda w: (betrag_formatieren(float(w)) + " €") if w not in (None, "") else ""

    # ---------------------------------------------------------------- Zugriffsschutz

    def web(handler):
        @wraps(handler)
        async def geschuetzt(request: Request):
            if not k.web_passwort:
                return PlainTextResponse("Bitte in der Konfiguration ein web_passwort setzen.", status_code=503)
            kopf = request.headers.get("authorization", "")
            ok = False
            if kopf.lower().startswith("basic "):
                try:
                    benutzer, _, passwort = base64.b64decode(kopf[6:]).decode().partition(":")
                    ok = _gleich(benutzer, k.web_benutzer) and _gleich(passwort, k.web_passwort)
                except Exception:
                    ok = False
            if not ok:
                return Response("Anmeldung erforderlich", status_code=401,
                                headers={"WWW-Authenticate": 'Basic realm="Familienarchiv", charset="UTF-8"'})
            if request.method == "POST":
                herkunft = request.headers.get("origin") or request.headers.get("referer") or ""
                if herkunft and urlparse(herkunft).netloc != request.headers.get("host"):
                    return PlainTextResponse("Anfrage von fremder Seite abgelehnt.", status_code=403)
            return await handler(request)
        return geschuetzt

    def api(handler):
        @wraps(handler)
        async def geschuetzt(request: Request):
            if not k.api_token:
                return JSONResponse({"fehler": "api_token ist nicht konfiguriert"}, status_code=503)
            kopf = request.headers.get("authorization", "")
            token = kopf[7:] if kopf.lower().startswith("bearer ") else request.query_params.get("token", "")
            if not _gleich(token, k.api_token):
                return JSONResponse({"fehler": "Nicht angemeldet"}, status_code=401)
            try:
                return await handler(request)
            except PaperlessFehler as exc:
                return JSONResponse({"fehler": str(exc)}, status_code=502)
        return geschuetzt

    def seite(request: Request, name: str, **kontext) -> Response:
        kontext.setdefault("meldung", request.query_params.get("meldung", ""))
        kontext.update(version=__version__, paperless_link=k.paperless_link, aktiv=name)
        return vorlagen.TemplateResponse(request, name + ".html", kontext)

    def zurueck(pfad: str, meldung: str) -> RedirectResponse:
        return RedirectResponse(f"{pfad}?meldung={quote(meldung)}", status_code=303)

    # ---------------------------------------------------------------- Kennzahlen

    def kennzahlen() -> dict:
        heute = date.today().isoformat()
        z = {
            "wartend": db.zaehlen("status IN ('wartet', 'laeuft')"),
            "fehler": db.zaehlen("status = 'fehler'"),
            "heute": db.zaehlen("status = 'fertig' AND erstellt >= ?", (heute,)),
            "heute_pruefen": db.zaehlen("status = 'fertig' AND sicher = 0 AND erstellt >= ?", (heute,)),
            "fristen": db.faellige(heute, (date.today() + timedelta(days=7)).isoformat()),
            "paperless": p.erreichbar(),
            "posteingang": None,
        }
        if z["paperless"]:
            try:
                tag = next((t for t in p.tags() if t["name"].lower() == k.posteingang_tag.lower()), None)
                z["posteingang"] = p.anzahl_mit_tag(tag["id"]) if tag else 0
            except PaperlessFehler:
                pass
        return z

    # ---------------------------------------------------------------- Seiten

    @web
    async def startseite(request: Request):
        z = await run_in_threadpool(kennzahlen)
        return seite(request, "start", z=z, auftraege=db.auftraege(15),
                     mail_aktiv=bool(k.imap_server),
                     mail_abruf=db.zustand("mail_letzter_abruf"), mail_fehler=db.zustand("mail_letzter_fehler"),
                     client=downloads.info(), personen=k.personen)

    @web
    async def auftraege_seite(request: Request):
        status = request.query_params.get("status") or None
        return seite(request, "auftraege", auftraege=db.auftraege(200, status), filter=status or "")

    @web
    async def auftrag_wiederholen(request: Request):
        ok = importer.wiederholen(request.path_params["auftrag_id"])
        return zurueck("/auftraege", "Auftrag wird erneut verarbeitet." if ok else "Auftrag kann nicht wiederholt werden.")

    @web
    async def mails_seite(request: Request):
        return seite(request, "mails", mails=db.alle("SELECT * FROM mails ORDER BY zeit DESC LIMIT 100"),
                     mail_aktiv=bool(k.imap_server), adresse=k.imap_benutzer,
                     erlaubt=k.erlaubte_absender)

    @web
    async def download_seite(request: Request):
        server_url = f"{request.url.scheme}://{request.headers.get('host', '')}"
        return seite(request, "download", client=downloads.info(), server_url=server_url,
                     api_token=k.api_token, github_repo=k.github_repo)

    @web
    async def download_datei(request: Request):
        if not downloads.info():
            return zurueck("/download", "Noch kein Windows-Client hinterlegt.")
        return FileResponse(downloads.pfad, filename="ArchivImporter.exe",
                            media_type="application/vnd.microsoft.portable-executable")

    @web
    async def download_hochladen(request: Request):
        async with request.form() as form:
            datei = form.get("datei")
            if not datei or not hasattr(datei, "read"):
                return zurueck("/download", "Bitte eine .exe-Datei auswählen.")
            inhalt, version = await datei.read(), str(form.get("version") or "")
        try:
            info = downloads.speichern(inhalt, version, "Hochgeladen")
        except ValueError as exc:
            return zurueck("/download", str(exc))
        return zurueck("/download", f"Windows-Client {info['version']} bereitgestellt.")

    @web
    async def download_github(request: Request):
        try:
            info = await run_in_threadpool(downloads.von_github_holen)
        except Exception as exc:
            return zurueck("/download", f"GitHub: {exc}")
        return zurueck("/download", f"Windows-Client {info['version']} von GitHub geladen.")

    @web
    async def web_hochladen(request: Request):
        anzahl = 0
        async with request.form() as form:
            vorgaben = {"quelle": "web", "kategorie": str(form.get("kategorie") or ""),
                        "tags": [t.strip().lstrip("#").lower() for t in str(form.get("tags") or "").split(",")
                                 if t.strip()],
                        "person": str(form.get("person") or "")}
            for datei in form.getlist("dateien"):
                if hasattr(datei, "read"):
                    daten = await datei.read()
                    if daten:
                        importer.einreihen(daten, datei.filename or "dokument.pdf", dict(vorgaben))
                        anzahl += 1
        return zurueck("/auftraege", f"{anzahl} Datei(en) eingereiht.")

    # ---------------------------------------------------------------- API

    @api
    async def api_status(request: Request):
        return JSONResponse({"version": __version__, "paperless": await run_in_threadpool(p.erreichbar),
                             "posteingang_tag": k.posteingang_tag})

    @api
    async def api_stammdaten(request: Request):
        return JSONResponse(await run_in_threadpool(importer.stammdaten))

    @api
    async def api_hash_pruefung(request: Request):
        daten = await request.json()
        vorhanden = [h for h in daten.get("sha256", []) if db.datei_bekannt(str(h).lower())]
        return JSONResponse({"vorhanden": vorhanden})

    @api
    async def api_import(request: Request):
        async with request.form() as form:
            datei = form.get("datei")
            if not datei or not hasattr(datei, "read"):
                return JSONResponse({"fehler": "Feld 'datei' fehlt"}, status_code=400)
            try:
                auftrag = json.loads(str(form.get("daten") or "{}"))
            except json.JSONDecodeError:
                return JSONResponse({"fehler": "Feld 'daten' ist kein gültiges JSON"}, status_code=400)
            inhalt, dateiname = await datei.read(), datei.filename
        auftrag.setdefault("quelle", "windows-client")
        if not inhalt:
            return JSONResponse({"fehler": "Datei ist leer"}, status_code=400)
        ergebnis = importer.einreihen(inhalt, dateiname or auftrag.get("datei") or "dokument.pdf", auftrag)
        return JSONResponse(ergebnis, status_code=202)

    @api
    async def api_import_status(request: Request):
        a = db.auftrag(request.path_params["auftrag_id"])
        if not a:
            return JSONResponse({"fehler": "Unbekannter Auftrag"}, status_code=404)
        return JSONResponse({"id": a["id"], "status": a["status"], "paperless_id": a["paperless_id"],
                             "sicher": bool(a["sicher"]) if a["sicher"] is not None else None,
                             "fehler": a["fehler"],
                             "link": f"{k.paperless_link}/documents/{a['paperless_id']}/details"
                             if a["paperless_id"] else None})

    @api
    async def api_korrekturen(request: Request):
        daten = await request.json()
        anzahl = importer.korrekturen_lernen(list(daten.get("eintraege") or []))
        return JSONResponse({"gespeichert": anzahl})

    @api
    async def api_client_version(request: Request):
        return JSONResponse(downloads.info() or {})

    @api
    async def api_ha_sensoren(request: Request):
        z = await run_in_threadpool(kennzahlen)
        return JSONResponse({"posteingang": z["posteingang"], "fristen_7_tage": len(z["fristen"]),
                             "auftraege_wartend": z["wartend"], "auftraege_fehler": z["fehler"],
                             "importe_heute": z["heute"], "paperless_erreichbar": z["paperless"]})

    async def gesundheit(request: Request):
        return JSONResponse({"ok": True, "version": __version__})

    routen = [
        Route("/", startseite),
        Route("/auftraege", auftraege_seite),
        Route("/auftraege/{auftrag_id}/wiederholen", auftrag_wiederholen, methods=["POST"]),
        Route("/hochladen", web_hochladen, methods=["POST"]),
        Route("/mails", mails_seite),
        Route("/download", download_seite),
        Route("/download/ArchivImporter.exe", download_datei),
        Route("/download/hochladen", download_hochladen, methods=["POST"]),
        Route("/download/github", download_github, methods=["POST"]),
        Route("/gesundheit", gesundheit),
        Route("/api/v1/status", api_status),
        Route("/api/v1/stammdaten", api_stammdaten),
        Route("/api/v1/hash-pruefung", api_hash_pruefung, methods=["POST"]),
        Route("/api/v1/import", api_import, methods=["POST"]),
        Route("/api/v1/import/{auftrag_id}", api_import_status),
        Route("/api/v1/korrekturen", api_korrekturen, methods=["POST"]),
        Route("/api/v1/client/version", api_client_version),
        Route("/api/v1/ha/sensoren", api_ha_sensoren),
        Mount("/static", StaticFiles(directory=os.path.join(HIER, "static")), name="static"),
    ]
    return Starlette(routes=routen)
