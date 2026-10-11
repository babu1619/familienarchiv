"""Ende-zu-Ende: Windows-Client → Begleitdienst → (nachgebautes) Paperless, mit nachgebautem Ollama."""

import json
import os
import shutil
import tempfile
import threading
import time
import unittest

import requests
import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

import hilfen  # noqa: F401
from fake_paperless import TOKEN, FakePaperless, freier_port
from hilfen import HANDWERKER, KASSENBON, RECHNUNG_STADTWERKE, bild_pdf_aus_text, pdf_aus_text

from dienst.db import Datenbank
from dienst.downloads import Downloads
from dienst.importer import Importer
from dienst.konfig import Konfig
from dienst.paperless import Paperless
from dienst.web import erstelle_app as dienst_app

from archiv_importer.einstellungen import Einstellungen, EinstellungsSpeicher
from archiv_importer.modelltest import ausfuehren as modelltest
from archiv_importer.oberflaeche import erstelle_app as client_app
from archiv_importer.speicher import Speicher
from archiv_importer.verarbeitung import Verarbeitung


def server_starten(app):
    port = freier_port()
    s = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    threading.Thread(target=s.run, daemon=True).start()
    while not s.started:
        time.sleep(0.05)
    return s, f"http://127.0.0.1:{port}"


class FakeOllama:
    """Antwortet wie Ollama /api/chat mit festgelegten Werten je Feld."""

    def __init__(self):
        self.antworten: dict = {}
        self.anfragen: list = []

        async def tags(request: Request):
            return JSONResponse({"models": [{"name": "qwen2.5:3b"}]})

        async def chat(request: Request):
            daten = await request.json()
            self.anfragen.append(daten)
            felder = daten["format"]["properties"]
            antwort = {k: self.antworten.get(k, "") for k in felder}
            return JSONResponse({"message": {"role": "assistant", "content": json.dumps(antwort)}})

        self.app = Starlette(routes=[Route("/api/tags", tags), Route("/api/chat", chat, methods=["POST"])])


class KetteTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.fake = FakePaperless().starten()
        k = Konfig(paperless_url=cls.fake.url, paperless_token=TOKEN, api_token="api-geheim", web_passwort="w",
                   daten_verzeichnis=os.path.join(cls.tmp, "dienst"), personen=["Familie", "Lisa"])
        os.makedirs(k.daten_verzeichnis)
        cls.db = Datenbank(os.path.join(k.daten_verzeichnis, "d.sqlite"))
        p = Paperless(k.paperless_url, TOKEN, cache_sekunden=0)
        p.benutzerfelder_sicherstellen(k.personen)
        for kat in ("Wohnen/Nebenkosten/Strom", "Wohnen/Instandhaltung", "Haushalt/Werkzeug"):
            p.tag_sicherstellen("kat:" + kat)
        p.korrespondent_sicherstellen("Stadtwerke Musterstadt")
        cls.importer = Importer(k, cls.db, p)
        cls.importer.starten()
        cls.dienst, cls.dienst_url = server_starten(dienst_app(k, cls.db, p, cls.importer, Downloads(k.daten_verzeichnis)))
        cls.ollama = FakeOllama()
        cls.ollama_server, cls.ollama_url = server_starten(cls.ollama.app)

    @classmethod
    def tearDownClass(cls):
        cls.importer.stoppen()
        for s in (cls.dienst, cls.ollama_server):
            s.should_exit = True
        cls.fake.stoppen()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def setUp(self):
        self.ordner = tempfile.mkdtemp(dir=self.tmp)
        self.e = Einstellungen(server_url=self.dienst_url, api_token="api-geheim", ollama_url=self.ollama_url,
                               tesseract_pfad=shutil.which("tesseract") or "", ocr_sprache="eng", nachtmodus=False)
        self.speicher = Speicher(os.path.join(self.ordner, "client.sqlite"))
        self.v = Verarbeitung(self.speicher, self.e)
        self.ollama.antworten = {}
        self.ollama.anfragen = []

    def datei(self, name, daten):
        pfad = os.path.join(self.ordner, name)
        with open(pfad, "wb") as f:
            f.write(daten)
        return pfad

    def alle_analysieren(self):
        while (d := self.speicher.naechstes("wartet")):
            self.v.analysieren(d)

    def uebergeben_und_warten(self):
        for d in self.speicher.mit_status("bereit"):
            self.v.uebergeben(d)
        for _ in range(100):
            self.v.status_abfragen()
            if not self.speicher.mit_status("uebertragen"):
                return
            time.sleep(0.1)
        self.fail("Übergabe nicht abgeschlossen")

    # ------------------------------------------------------------------

    def test_ki_nur_fuer_offene_felder_und_uebergabe(self):
        self.ollama.antworten = {"kategorie": "Wohnen/Nebenkosten/Strom", "kategorie_sicherheit": "hoch"}
        self.datei("strom.pdf", pdf_aus_text(RECHNUNG_STADTWERKE))
        self.v.stapel_anlegen([self.ordner], {"tags": ["steuer-2026"]})
        self.alle_analysieren()
        doc = self.speicher.mit_status("sicher", "pruefen")[0]
        self.assertEqual(doc["status"], "sicher", doc["ergebnis"])
        gefragt = set(self.ollama.anfragen[0]["format"]["properties"])
        self.assertEqual(gefragt, {"kategorie", "kategorie_sicherheit"})
        self.assertIn("Wohnen/Nebenkosten/Strom", self.ollama.anfragen[0]["format"]["properties"]["kategorie"]["enum"])
        self.assertEqual(doc["ergebnis"]["felder"]["kategorie"]["quelle"], "ki")
        self.assertEqual(self.v.alle_sicheren_freigeben(), 1)
        self.uebergeben_und_warten()
        doc = self.speicher.dokument(doc["id"])
        self.assertEqual(doc["status"], "archiviert", doc["fehler"])
        tags = self.fake.tagnamen(doc["paperless_id"])
        self.assertIn("kat:Wohnen/Nebenkosten/Strom", tags)
        self.assertIn("steuer-2026", tags)
        self.assertNotIn("Posteingang", tags)
        self.assertEqual(self.fake.feldwerte(doc["paperless_id"])["Betrag brutto"], "EUR84.20")
        # erneut hinzufügen → bereits im Archiv
        self.v.stapel_anlegen([self.ordner], {})
        self.alle_analysieren()
        self.assertEqual(self.speicher.mit_status("duplikat")[0]["fehler"], "Bereits im Archiv – übersprungen")

    def test_scan_mit_ocr_korrektur_und_regel(self):
        self.ollama.antworten = {"absender": "Baumarkt Musterhaus", "kategorie": "Haushalt/Werkzeug",
                                 "kategorie_sicherheit": "mittel"}
        self.datei("bon.pdf", bild_pdf_aus_text(KASSENBON))
        self.v.stapel_anlegen([self.ordner], {})
        self.alle_analysieren()
        doc = self.speicher.mit_status("sicher", "pruefen")[0]
        e = doc["ergebnis"]
        self.assertIn("OCR", " ".join(e["hinweise"]))
        self.assertEqual(e["felder"]["betrag_brutto"]["wert"], 95.58)
        self.assertEqual(e["felder"]["dokumenttyp"]["wert"], "Kassenbon")
        self.assertEqual(doc["status"], "pruefen")  # Kategorie nur mittel sicher
        doc = self.v.bearbeiten(doc["id"], {"felder": {"kategorie": "Wohnen/Instandhaltung"}, "als_regel": True})
        self.assertEqual(doc["status"], "sicher")
        self.assertEqual(doc["ergebnis"]["felder"]["kategorie"]["quelle"], "manuell")
        self.v.freigeben(doc["id"], geprueft=True)
        self.uebergeben_und_warten()
        doc = self.speicher.dokument(doc["id"])
        self.assertEqual(doc["status"], "archiviert", doc["fehler"])
        self.assertEqual(self.db.profil("Baumarkt Musterhaus")["kategorie"], "Wohnen/Instandhaltung")
        self.assertIn("kat:Wohnen/Instandhaltung", self.fake.tagnamen(doc["paperless_id"]))

    def test_ki_datum_nicht_im_text_wird_geprueft(self):
        text = HANDWERKER.replace("Datum: 29.09.2025", "Leistung am 22.09.2025").replace("Zahlbar bis 13.10.2025 ohne Abzug.", "")
        self.ollama.antworten = {"datum": "2025-09-30", "kategorie": "Wohnen/Instandhaltung",
                                 "kategorie_sicherheit": "hoch", "absender": "Elektro Meier"}
        self.datei("meier.pdf", pdf_aus_text(text))
        self.v.stapel_anlegen([self.ordner], {"person": "Lisa"})
        self.alle_analysieren()
        doc = self.speicher.mit_status("sicher", "pruefen")[0]
        e = doc["ergebnis"]
        self.assertEqual(doc["status"], "pruefen")
        self.assertFalse(e["felder"]["datum"]["sicher"])
        self.assertTrue(any(p["text"] == "Datum steht im Text" and not p["ok"] for p in e["pruefungen"]))
        self.assertEqual(e["felder"]["person"]["wert"], "Lisa")
        self.assertEqual(e["felder"]["arbeitskosten"]["wert"], 640.0)
        # ungeprüft übergeben → landet im Paperless-Posteingang
        self.v.freigeben(doc["id"], geprueft=False)
        self.uebergeben_und_warten()
        doc = self.speicher.dokument(doc["id"])
        self.assertEqual(doc["status"], "archiviert")
        self.assertIn("Posteingang", self.fake.tagnamen(doc["paperless_id"]))
        self.assertEqual(doc["fehler"], "Im Paperless-Posteingang zur Prüfung")

    def test_ohne_ki_und_doppelte_datei_im_stapel(self):
        self.e.ollama_url = "http://127.0.0.1:1"  # nicht erreichbar
        pdf = pdf_aus_text(HANDWERKER)
        self.datei("a.pdf", pdf)
        self.datei("b.pdf", pdf)
        self.v.stapel_anlegen([self.ordner], {})
        self.alle_analysieren()
        status = sorted(d["status"] for d in self.speicher.mit_status("sicher", "pruefen", "duplikat"))
        self.assertEqual(status, ["duplikat", "pruefen"])
        doc = self.speicher.mit_status("pruefen")[0]
        self.assertTrue(any("KI nicht verfügbar" in h for h in doc["ergebnis"]["hinweise"]))

    def test_passwortgeschuetzt(self):
        from io import BytesIO
        from pypdf import PdfReader, PdfWriter
        w = PdfWriter(clone_from=PdfReader(BytesIO(pdf_aus_text(KASSENBON))))
        w.encrypt("geheim")
        puffer = BytesIO()
        w.write(puffer)
        self.datei("gesperrt.pdf", puffer.getvalue())
        self.v.stapel_anlegen([self.ordner], {})
        self.alle_analysieren()
        self.assertIn("Passwortgeschützt", self.speicher.mit_status("fehler")[0]["fehler"])

    def test_modelltest(self):
        self.ollama.antworten = {"kategorie": "Wohnen/Nebenkosten/Strom", "kategorie_sicherheit": "hoch"}
        self.datei("strom.pdf", pdf_aus_text(RECHNUNG_STADTWERKE))
        with open(os.path.join(self.ordner, "strom.erwartet.json"), "w") as f:
            json.dump({"datum": "2026-10-03", "betrag_brutto": 84.2, "dokumenttyp": "Rechnung",
                       "kategorie": "Wohnen/Nebenkosten/Strom"}, f)
        zeilen = []
        self.assertEqual(modelltest(self.ordner, self.e, zeilen.append), 0)
        self.assertTrue(any("100.0 %" in z and "kategorie" in z for z in zeilen), zeilen)
        self.assertTrue(os.path.exists(os.path.join(self.ordner, "modelltest-qwen2.5_3b.csv")))
        self.assertTrue(os.path.exists(os.path.join(self.ordner, "modelltest-qwen2.5_3b.txt")))


class OberflaecheTest(unittest.TestCase):
    def test_lokaler_zugriffsschutz(self):
        tmp = tempfile.mkdtemp()
        speicher = Speicher(os.path.join(tmp, "c.sqlite"))
        einst = EinstellungsSpeicher(tmp)
        v = Verarbeitung(speicher, Einstellungen())
        server, url = server_starten(client_app(v, speicher, einst, "sitzung123"))
        try:
            self.assertEqual(requests.get(url + "/").status_code, 403)
            self.assertEqual(requests.get(url + "/?t=sitzung123").status_code, 200)
            self.assertEqual(requests.get(url + "/api/zustand", headers={"X-Token": "sitzung123"}).status_code, 200)
            self.assertEqual(requests.get(url + "/api/zustand", headers={"X-Token": "sitzung123",
                                                                        "Host": "boese.example"}).status_code, 403)
            r = requests.post(url + "/api/einstellungen", headers={"X-Token": "sitzung123"},
                              json={"server_url": "http://server:8099/", "api_token": "abc", "ki_timeout": "300"})
            self.assertEqual(r.json()["api_token"], "••••••")
            self.assertEqual(r.json()["server_url"], "http://server:8099")
            # unverändertes Token bleibt erhalten
            requests.post(url + "/api/einstellungen", headers={"X-Token": "sitzung123"}, json={"api_token": "••••••"})
            self.assertEqual(einst.laden().api_token, "abc")
            self.assertEqual(einst.laden().ki_timeout, 300)
        finally:
            server.should_exit = True


if __name__ == "__main__":
    unittest.main()
