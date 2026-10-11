"""Nachgebautes Paperless-ngx (nur die vom Begleitdienst genutzten Endpunkte) für Tests."""

from __future__ import annotations

import hashlib
import io
import re
import socket
import threading
import time
import uuid

import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

TOKEN = "paperless-test-token"


def freier_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class FakePaperless:
    def __init__(self, neue_select_optionen: bool = True):
        self.neue_select_optionen = neue_select_optionen
        self.daten = {"tags": [], "correspondents": [], "document_types": [], "custom_fields": []}
        self.dokumente: dict[int, dict] = {}
        self.aufgaben: dict[str, dict] = {}
        self.notizen: dict[int, list[str]] = {}
        self.pruefsummen: dict[str, int] = {}
        self._naechste_id = 1
        self.port = freier_port()
        self.url = f"http://127.0.0.1:{self.port}"
        self.app = Starlette(routes=self._routen())
        self._server = None

    def _id(self) -> int:
        self._naechste_id += 1
        return self._naechste_id

    # ---------------------------------------------------------------- Server

    def starten(self):
        konfig = uvicorn.Config(self.app, host="127.0.0.1", port=self.port, log_level="error")
        self._server = uvicorn.Server(konfig)
        threading.Thread(target=self._server.run, daemon=True).start()
        for _ in range(100):
            if self._server.started:
                return self
            time.sleep(0.05)
        raise RuntimeError("Fake-Paperless startet nicht")

    def stoppen(self):
        if self._server:
            self._server.should_exit = True

    # ---------------------------------------------------------------- Hilfen

    @staticmethod
    def _auth(request: Request) -> bool:
        return request.headers.get("authorization") == f"Token {TOKEN}"

    def _liste(self, art):
        async def handler(request: Request):
            if not self._auth(request):
                return JSONResponse({"detail": "auth"}, 401)
            if request.method == "POST":
                daten = await request.json()
                if any(e["name"].lower() == daten["name"].lower() for e in self.daten[art]):
                    return JSONResponse({"name": ["exists"]}, 400)
                if art == "custom_fields" and daten.get("data_type") == "select":
                    optionen = daten.get("extra_data", {}).get("select_options", [])
                    if self.neue_select_optionen:
                        if optionen and isinstance(optionen[0], str):
                            return JSONResponse({"extra_data": ["format"]}, 400)
                        for o in optionen:
                            o["id"] = uuid.uuid4().hex[:16]
                    elif optionen and isinstance(optionen[0], dict):
                        return JSONResponse({"extra_data": ["format"]}, 400)
                eintrag = {"id": self._id(), **daten}
                self.daten[art].append(eintrag)
                return JSONResponse(eintrag, 201)
            return JSONResponse({"count": len(self.daten[art]), "next": None, "results": self.daten[art]})
        return handler

    def _feld_aendern(self):
        async def handler(request: Request):
            fid = int(request.path_params["id"])
            feld = next(f for f in self.daten["custom_fields"] if f["id"] == fid)
            daten = await request.json()
            for o in daten.get("extra_data", {}).get("select_options", []):
                if isinstance(o, dict) and not o.get("id"):
                    o["id"] = uuid.uuid4().hex[:16]
            feld.update(daten)
            return JSONResponse(feld)
        return handler

    # ---------------------------------------------------------------- Dokumente

    async def _post_document(self, request: Request):
        if not self._auth(request):
            return JSONResponse({"detail": "auth"}, 401)
        form = await request.form()
        datei = form["document"]
        inhalt = await datei.read()
        task = str(uuid.uuid4())
        md5 = hashlib.md5(inhalt).hexdigest()
        if md5 in self.pruefsummen:
            self.aufgaben[task] = {"task_id": task, "status": "FAILURE", "related_document": None,
                                   "result": f"{datei.filename}: Not consuming {datei.filename}: It is a duplicate of "
                                             f"x (#{self.pruefsummen[md5]})."}
            return JSONResponse(task)
        text = ""
        if inhalt[:5] == b"%PDF-":
            from pypdf import PdfReader
            text = "\n".join(p.extract_text() or "" for p in PdfReader(io.BytesIO(inhalt)).pages)
        elif datei.filename.endswith(".txt"):
            text = inhalt.decode("utf-8")
        doc_id = self._id()
        inbox = [t["id"] for t in self.daten["tags"] if t.get("is_inbox_tag")]
        self.dokumente[doc_id] = {"id": doc_id, "title": form.get("title") or datei.filename, "content": text,
                                  "tags": inbox, "correspondent": None, "document_type": None,
                                  "created": "2026-10-10", "custom_fields": []}
        self.pruefsummen[md5] = doc_id
        self.aufgaben[task] = {"task_id": task, "status": "STARTED", "related_document": None, "result": None,
                               "_doc": doc_id, "_fertig": time.time() + 0.3}
        return JSONResponse(task)

    async def _tasks(self, request: Request):
        a = self.aufgaben.get(request.query_params.get("task_id", ""))
        if not a:
            return JSONResponse([])
        if a["status"] == "STARTED" and time.time() >= a["_fertig"]:
            a["status"], a["related_document"], a["result"] = "SUCCESS", a["_doc"], "Success"
        return JSONResponse([{k: v for k, v in a.items() if not k.startswith("_")}])

    async def _documents(self, request: Request):
        if not self._auth(request):
            return JSONResponse({"detail": "auth"}, 401)
        docs = list(self.dokumente.values())
        tag = request.query_params.get("tags__id__all")
        if tag:
            docs = [d for d in docs if int(tag) in d["tags"]]
        return JSONResponse({"count": len(docs), "next": None, "results": docs[:int(request.query_params.get("page_size", 25))]})

    async def _document(self, request: Request):
        doc = self.dokumente[int(request.path_params["id"])]
        if request.method == "PATCH":
            daten = await request.json()
            if "created" in daten and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(daten["created"])):
                return JSONResponse({"created": ["format"]}, 400)
            for t in daten.get("tags", []):
                if not any(x["id"] == t for x in self.daten["tags"]):
                    return JSONResponse({"tags": [f"unknown {t}"]}, 400)
            for cf in daten.get("custom_fields", []):
                feld = next((f for f in self.daten["custom_fields"] if f["id"] == cf["field"]), None)
                if feld is None:
                    return JSONResponse({"custom_fields": ["unknown field"]}, 400)
                if feld["data_type"] == "monetary" and not re.fullmatch(r"[A-Z]{3}\d+\.\d{2}", str(cf["value"])):
                    return JSONResponse({"custom_fields": ["monetary format"]}, 400)
            doc.update(daten)
        return JSONResponse(doc)

    async def _notes(self, request: Request):
        doc_id = int(request.path_params["id"])
        self.notizen.setdefault(doc_id, []).append((await request.json())["note"])
        return JSONResponse([])

    def _routen(self):
        routen = [
            Route("/api/documents/", self._documents),
            Route("/api/documents/post_document/", self._post_document, methods=["POST"]),
            Route("/api/documents/{id:int}/", self._document, methods=["GET", "PATCH"]),
            Route("/api/documents/{id:int}/notes/", self._notes, methods=["POST"]),
            Route("/api/tasks/", self._tasks),
            Route("/api/custom_fields/{id:int}/", self._feld_aendern(), methods=["PATCH"]),
        ]
        for art in self.daten:
            routen.append(Route(f"/api/{art}/", self._liste(art), methods=["GET", "POST"]))
        return routen

    # ---------------------------------------------------------------- Auswertung für Tests

    def name(self, art: str, eid) -> str | None:
        return next((e["name"] for e in self.daten[art] if e["id"] == eid), None)

    def tagnamen(self, doc_id: int) -> set[str]:
        return {self.name("tags", t) for t in self.dokumente[doc_id]["tags"]}

    def feldwerte(self, doc_id: int) -> dict:
        return {self.name("custom_fields", cf["field"]): cf["value"] for cf in self.dokumente[doc_id]["custom_fields"]}
