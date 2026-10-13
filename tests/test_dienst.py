import base64
import json
import os
import shutil
import tempfile
import threading
import time
import unittest
from email.message import EmailMessage

import requests
import uvicorn

import hilfen  # noqa: F401
from fake_paperless import TOKEN, FakePaperless, freier_port
from hilfen import FACTURX_XML, HANDWERKER, RECHNUNG_STADTWERKE, pdf_aus_text

from dienst.db import Datenbank
from dienst.downloads import Downloads
from dienst.importer import Importer
from dienst.konfig import Konfig, laden
from dienst.mail import nachricht_verarbeiten
from dienst.paperless import Paperless
from dienst.web import erstelle_app


class Umgebung(unittest.TestCase):
    neue_select_optionen = True

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.fake = FakePaperless(self.neue_select_optionen).starten()
        self.k = Konfig(paperless_url=self.fake.url, paperless_token=TOKEN, api_token="api-geheim",
                        web_passwort="web-geheim", daten_verzeichnis=self.tmp, personen=["Familie", "Lisa"],
                        erlaubte_absender=["benjamin@example.de", "@familie.example"])
        self.db = Datenbank(os.path.join(self.tmp, "t.sqlite"))
        self.p = Paperless(self.k.paperless_url, TOKEN, cache_sekunden=0)
        self.p.benutzerfelder_sicherstellen(self.k.personen)
        self.p.tag_sicherstellen("kat:Wohnen/Nebenkosten/Strom")
        self.p.tag_sicherstellen("kat:Wohnen/Instandhaltung")
        self.p.korrespondent_sicherstellen("Stadtwerke Musterstadt")
        self.imp = Importer(self.k, self.db, self.p)

    def tearDown(self):
        self.fake.stoppen()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def abarbeiten(self):
        while (a := self.db.naechster_auftrag()):
            self.imp.verarbeiten(a)


class ImporterTest(Umgebung):
    def test_mail_ohne_kategorie_geht_in_den_posteingang(self):
        r = self.imp.einreihen(pdf_aus_text(RECHNUNG_STADTWERKE), "strom.pdf", {"quelle": "mail"})
        self.abarbeiten()
        a = self.db.auftrag(r["id"])
        self.assertEqual(a["status"], "fertig", a["fehler"])
        doc = self.fake.dokumente[a["paperless_id"]]
        self.assertEqual(self.fake.name("correspondents", doc["correspondent"]), "Stadtwerke Musterstadt")
        self.assertEqual(self.fake.name("document_types", doc["document_type"]), "Rechnung")
        self.assertEqual(doc["created"], "2026-10-03")
        werte = self.fake.feldwerte(a["paperless_id"])
        self.assertEqual(werte["Betrag brutto"], "EUR84.20")
        self.assertEqual(werte["Rechnungsnummer"], "RE-2026-1187")
        self.assertEqual(werte["Fälligkeit"], "2026-10-17")
        self.assertIn("Posteingang", self.fake.tagnamen(a["paperless_id"]))
        self.assertTrue(any("Bitte prüfen" in n for n in self.fake.notizen[a["paperless_id"]]))
        self.assertEqual(a["sicher"], 0)

    def test_erechnung_mit_kategorie_ist_sicher(self):
        pdf = pdf_aus_text(RECHNUNG_STADTWERKE, ("factur-x.xml", FACTURX_XML))
        r = self.imp.einreihen(pdf, "strom.pdf", {"quelle": "mail", "kategorie": "wohnen/nebenkosten/strom",
                                                  "tags": ["steuer-2026"], "person": "Lisa"})
        self.abarbeiten()
        a = self.db.auftrag(r["id"])
        self.assertEqual(a["sicher"], 1)
        tags = self.fake.tagnamen(a["paperless_id"])
        self.assertIn("kat:Wohnen/Nebenkosten/Strom", tags)
        self.assertIn("steuer-2026", tags)
        self.assertNotIn("Posteingang", tags)
        werte = self.fake.feldwerte(a["paperless_id"])
        feld = next(f for f in self.fake.daten["custom_fields"] if f["name"] == "Person")
        lisa = next(o["id"] for o in feld["extra_data"]["select_options"] if o["label"] == "Lisa")
        self.assertEqual(werte["Person"], lisa)
        self.assertEqual(werte["Zahlungsart"], next(o["id"] for o in next(
            f for f in self.fake.daten["custom_fields"] if f["name"] == "Zahlungsart")["extra_data"]["select_options"]
            if o["label"] == "Lastschrift"))
        # Profil wurde gelernt
        self.assertEqual(self.db.profil("Stadtwerke Musterstadt")["kategorie"], "Wohnen/Nebenkosten/Strom")

    def test_unbekannte_kategorie_aus_mail_wird_nicht_angelegt(self):
        r = self.imp.einreihen(pdf_aus_text(HANDWERKER), "meier.pdf", {"quelle": "mail", "kategorie": "Wohnen/Instandhaltng"})
        self.abarbeiten()
        a = self.db.auftrag(r["id"])
        tags = self.fake.tagnamen(a["paperless_id"])
        self.assertNotIn("kat:Wohnen/Instandhaltng", tags)
        self.assertIn("Posteingang", tags)
        self.assertTrue(any("Wohnen/Instandhaltung" in n for n in self.fake.notizen[a["paperless_id"]]))

    def test_client_auftrag_geprueft_legt_neue_kategorie_an(self):
        felder = {
            "datum": {"wert": "2025-09-29", "quelle": "manuell", "sicher": True},
            "dokumenttyp": {"wert": "Handwerkerrechnung", "quelle": "muster", "sicher": True},
            "absender": {"wert": "Elektro Meier", "quelle": "ki", "sicher": True},
            "betrag_brutto": {"wert": 1284.5, "quelle": "muster", "sicher": True},
            "arbeitskosten": {"wert": 640.0, "quelle": "manuell", "sicher": True},
        }
        r = self.imp.einreihen(pdf_aus_text(HANDWERKER), "meier.pdf",
                               {"quelle": "windows-client", "kategorie": "Wohnen/Elektrik", "felder": felder,
                                "tags": ["akte:renovierung-bad"], "geprueft": True, "titel": "Elektro Meier Bad"})
        self.abarbeiten()
        a = self.db.auftrag(r["id"])
        self.assertEqual(a["status"], "fertig", a["fehler"])
        doc = self.fake.dokumente[a["paperless_id"]]
        self.assertEqual(doc["title"], "Elektro Meier Bad")
        tags = self.fake.tagnamen(a["paperless_id"])
        self.assertIn("kat:Wohnen/Elektrik", tags)
        self.assertIn("akte:renovierung-bad", tags)
        self.assertNotIn("Posteingang", tags)
        self.assertEqual(self.fake.feldwerte(a["paperless_id"])["Arbeitskosten"], "EUR640.00")
        self.assertEqual(self.fake.name("correspondents", doc["correspondent"]), "Elektro Meier")

    def test_duplikate(self):
        pdf = pdf_aus_text(RECHNUNG_STADTWERKE)
        self.imp.einreihen(pdf, "a.pdf", {"quelle": "web"})
        self.abarbeiten()
        r = self.imp.einreihen(pdf, "a-kopie.pdf", {"quelle": "web"})
        self.assertEqual(r["status"], "duplikat")
        # Duplikat, das nur Paperless kennt
        andere = pdf_aus_text(HANDWERKER)
        self.fake.pruefsummen[__import__("hashlib").md5(andere).hexdigest()] = 99
        r = self.imp.einreihen(andere, "b.pdf", {"quelle": "web"})
        self.abarbeiten()
        self.assertEqual(self.db.auftrag(r["id"])["status"], "duplikat")

    def test_ohne_aufgabenliste_ueber_pruefsumme(self):
        self.fake.tasks_kaputt = True
        r = self.imp.einreihen(pdf_aus_text(RECHNUNG_STADTWERKE), "s.pdf", {"quelle": "web"})
        self.abarbeiten()
        a = self.db.auftrag(r["id"])
        self.assertEqual(a["status"], "fertig", a["fehler"])
        self.assertEqual(self.fake.feldwerte(a["paperless_id"])["Betrag brutto"], "EUR84.20")

    def test_paperless3_format(self):
        self.fake.v3 = True
        pdf = pdf_aus_text(RECHNUNG_STADTWERKE)
        r = self.imp.einreihen(pdf, "s.pdf", {"quelle": "web"})
        self.abarbeiten()
        a = self.db.auftrag(r["id"])
        self.assertEqual(a["status"], "fertig", a["fehler"])
        # Duplikat in Paperless 3
        self.db.ausfuehren("DELETE FROM dateien")
        r = self.imp.einreihen(pdf, "s2.pdf", {"quelle": "web"})
        self.abarbeiten()
        self.assertEqual(self.db.auftrag(r["id"])["status"], "duplikat")
        # Prüfsumme SHA256
        import hashlib
        self.assertEqual(self.p.dokument_per_pruefsumme(hashlib.sha256(pdf).hexdigest(), "x"), a["paperless_id"])

    def test_wiederholung_nutzt_vorhandenes_dokument(self):
        pdf = pdf_aus_text(HANDWERKER)
        r = self.imp.einreihen(pdf, "m.pdf", {"quelle": "web"})
        a = self.db.auftrag(r["id"])
        # erster Versuch: hochgeladen, dann abgebrochen
        task = self.p.hochladen(pdf, "m.pdf")
        self.p.auf_aufgabe_warten(task)
        self.db.auftrag_setzen(r["id"], status="fehler", paperless_task=task, fehler="Zeitüberschreitung")
        self.assertTrue(self.imp.wiederholen(r["id"]))
        self.abarbeiten()
        a = self.db.auftrag(r["id"])
        self.assertEqual(a["status"], "fertig", a["fehler"])
        self.assertEqual(len(self.fake.dokumente), 1)

    def test_korrektur_als_regel(self):
        self.imp.korrekturen_lernen([{"absender": "Elektro Meier", "feld": "kategorie", "alt": "Wohnen",
                                      "neu": "Wohnen/Instandhaltung", "als_regel": True}])
        p = self.db.profil("Elektro Meier")
        self.assertEqual(p["kategorie"], "Wohnen/Instandhaltung")
        self.assertTrue(p["kategorie_sicher"])


class AlteSelectOptionenTest(Umgebung):
    neue_select_optionen = False

    def test_alte_paperless_version(self):
        feld = next(f for f in self.fake.daten["custom_fields"] if f["name"] == "Person")
        self.assertEqual(feld["extra_data"]["select_options"], ["Familie", "Lisa"])
        r = self.imp.einreihen(pdf_aus_text(RECHNUNG_STADTWERKE), "s.pdf", {"quelle": "mail", "person": "Lisa"})
        self.abarbeiten()
        a = self.db.auftrag(r["id"])
        self.assertEqual(self.fake.feldwerte(a["paperless_id"])["Person"], 1)


class MailTest(Umgebung):
    def _mail(self, absender, betreff, anhang=True, text="Hallo"):
        m = EmailMessage()
        m["From"] = absender
        m["To"] = "archiv@example.de"
        m["Subject"] = betreff
        m["Message-ID"] = f"<{time.time()}@test>"
        m.set_content(text)
        if anhang:
            m.add_attachment(pdf_aus_text(RECHNUNG_STADTWERKE), maintype="application", subtype="pdf",
                             filename="rechnung.pdf")
        return m.as_bytes()

    def test_mail_mit_kurzsyntax(self):
        ergebnis, _ = nachricht_verarbeiten(
            self._mail("Benjamin <Benjamin@example.de>", "Strom Oktober @Wohnen/Nebenkosten/Strom #steuer-2026 +Lisa"),
            self.k, self.db, self.imp)
        self.assertEqual(ergebnis, "verarbeitet")
        self.abarbeiten()
        a = self.db.auftraege(1)[0]
        self.assertEqual(a["status"], "fertig", a["fehler"])
        tags = self.fake.tagnamen(a["paperless_id"])
        self.assertIn("kat:Wohnen/Nebenkosten/Strom", tags)
        self.assertIn("steuer-2026", tags)
        self.assertEqual(self.fake.dokumente[a["paperless_id"]]["title"], "Strom Oktober")

    def test_fremder_absender_quarantaene(self):
        ergebnis, _ = nachricht_verarbeiten(self._mail("spam@boese.example", "Rechnung"), self.k, self.db, self.imp)
        self.assertEqual(ergebnis, "quarantaene")
        self.assertEqual(self.db.auftraege(), [])

    def test_domain_freigabe_und_mail_ohne_anhang(self):
        ergebnis, _ = nachricht_verarbeiten(
            self._mail("lisa@familie.example", "Bestellbestätigung #online", anhang=False,
                       text="Kategorie: Wohnen/Instandhaltung\n\nIhre Bestellung über 49,90 EUR"),
            self.k, self.db, self.imp)
        self.assertEqual(ergebnis, "verarbeitet")
        self.abarbeiten()
        a = self.db.auftraege(1)[0]
        self.assertTrue(a["dateiname"].endswith(".txt"))
        self.assertIn("kat:Wohnen/Instandhaltung", self.fake.tagnamen(a["paperless_id"]))


class WebTest(Umgebung):
    def setUp(self):
        super().setUp()
        self.imp.starten()
        self.downloads = Downloads(self.tmp)
        app = erstelle_app(self.k, self.db, self.p, self.imp, self.downloads)
        port = freier_port()
        self.url = f"http://127.0.0.1:{port}"
        self.server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
        threading.Thread(target=self.server.run, daemon=True).start()
        while not self.server.started:
            time.sleep(0.05)
        self.api = {"Authorization": "Bearer api-geheim"}
        self.web = ("admin", "web-geheim")

    def tearDown(self):
        self.imp.stoppen()
        self.server.should_exit = True
        super().tearDown()

    def test_zugriffsschutz(self):
        self.assertEqual(requests.get(self.url + "/api/v1/stammdaten").status_code, 401)
        self.assertEqual(requests.get(self.url + "/api/v1/stammdaten",
                                      headers={"Authorization": "Bearer falsch"}).status_code, 401)
        self.assertEqual(requests.get(self.url + "/").status_code, 401)
        self.assertEqual(requests.get(self.url + "/", auth=("admin", "falsch")).status_code, 401)
        self.assertEqual(requests.get(self.url + "/", auth=self.web).status_code, 200)
        fremd = requests.post(self.url + "/download/github", auth=self.web, headers={"Origin": "http://boese.example"})
        self.assertEqual(fremd.status_code, 403)

    def test_import_ueber_api(self):
        stamm = requests.get(self.url + "/api/v1/stammdaten", headers=self.api).json()
        self.assertIn("Wohnen/Nebenkosten/Strom", stamm["kategorien"])
        self.assertIn("Stadtwerke Musterstadt", stamm["korrespondenten"])
        self.assertIn("Lisa", stamm["personen"])
        pdf = pdf_aus_text(RECHNUNG_STADTWERKE)
        daten = {"kategorie": "Wohnen/Nebenkosten/Strom", "felder": {
            "datum": {"wert": "2026-10-03", "quelle": "muster", "sicher": True},
            "dokumenttyp": {"wert": "Rechnung", "quelle": "muster", "sicher": True},
            "betrag_brutto": {"wert": 84.2, "quelle": "muster", "sicher": True}}}
        r = requests.post(self.url + "/api/v1/import", headers=self.api,
                          files={"datei": ("s.pdf", pdf)}, data={"daten": json.dumps(daten)})
        self.assertEqual(r.status_code, 202, r.text)
        auftrag = r.json()["id"]
        for _ in range(100):
            s = requests.get(f"{self.url}/api/v1/import/{auftrag}", headers=self.api).json()
            if s["status"] in ("fertig", "fehler"):
                break
            time.sleep(0.1)
        self.assertEqual(s["status"], "fertig", s)
        self.assertTrue(s["sicher"])
        import hashlib
        h = requests.post(self.url + "/api/v1/hash-pruefung", headers=self.api,
                          json={"sha256": [hashlib.sha256(pdf).hexdigest(), "abc"]}).json()
        self.assertEqual(h["vorhanden"], [hashlib.sha256(pdf).hexdigest()])
        sensoren = requests.get(self.url + "/api/v1/ha/sensoren", headers=self.api).json()
        self.assertEqual(sensoren["importe_heute"], 1)
        self.assertEqual(sensoren["posteingang"], 0)
        seite = requests.get(self.url + "/", auth=self.web).text
        self.assertIn("archiviert", seite)

    def test_download(self):
        self.assertIn("Noch kein Windows-Client", requests.get(self.url + "/download", auth=self.web).text)
        falsch = requests.post(self.url + "/download/hochladen", auth=self.web,
                               files={"datei": ("x.exe", b"kein programm")}, data={"version": "0.1.0"})
        self.assertIn("kein Windows-Programm", requests.utils.unquote(falsch.url))
        requests.post(self.url + "/download/hochladen", auth=self.web,
                      files={"datei": ("ArchivImporter.exe", b"MZ" + b"\0" * 100)}, data={"version": "0.1.0"})
        seite = requests.get(self.url + "/download", auth=self.web).text
        self.assertIn("Version <strong>0.1.0</strong>", seite)
        datei = requests.get(self.url + "/download/ArchivImporter.exe", auth=self.web)
        self.assertTrue(datei.content.startswith(b"MZ"))
        self.assertEqual(requests.get(self.url + "/api/v1/client/version", headers=self.api).json()["version"], "0.1.0")


class KonfigTest(unittest.TestCase):
    def test_optionen_und_umgebung(self):
        tmp = tempfile.mkdtemp()
        pfad = os.path.join(tmp, "options.json")
        with open(pfad, "w") as f:
            json.dump({"paperless_url": "http://p:8000", "personen": ["A", "B"], "imap_port": 993,
                       "erlaubte_absender": ["X@Y.DE"]}, f)
        os.environ["ARCHIV_IMAP_INTERVALL"] = "300"
        os.environ["ARCHIV_RUECKMELDUNG"] = "true"
        try:
            k = laden(pfad)
        finally:
            del os.environ["ARCHIV_IMAP_INTERVALL"], os.environ["ARCHIV_RUECKMELDUNG"]
        self.assertEqual(k.paperless_url, "http://p:8000")
        self.assertEqual(k.personen, ["A", "B"])
        self.assertEqual(k.imap_intervall, 300)
        self.assertTrue(k.rueckmeldung)
        self.assertEqual(k.erlaubte_absender, ["x@y.de"])


if __name__ == "__main__":
    unittest.main()


class VerbindungTest(unittest.TestCase):
    def test_gruende(self):
        fake = FakePaperless().starten()
        try:
            self.assertEqual(Paperless(fake.url, TOKEN).verbindung_pruefen(), (True, ""))
            self.assertTrue(Paperless(fake.url, f"Token {TOKEN} ").verbindung_pruefen()[0])
            ok, grund = Paperless(fake.url, "falsch").verbindung_pruefen()
            self.assertFalse(ok)
            self.assertIn("API-Token", grund)
            ok, grund = Paperless(fake.url + "/falsch", TOKEN).verbindung_pruefen()
            self.assertIn("404", grund)
        finally:
            fake.stoppen()
        ok, grund = Paperless(f"http://127.0.0.1:{freier_port()}", TOKEN).verbindung_pruefen()
        self.assertFalse(ok)
        self.assertIn("Keine Verbindung", grund)


class DiagnoseTest(WebTest):
    def test_diagnose_seite(self):
        self.imp.einreihen(pdf_aus_text(RECHNUNG_STADTWERKE), "s.pdf", {"quelle": "web"})
        time.sleep(1.5)
        seite = requests.get(self.url + "/diagnose", auth=self.web).text
        self.assertIn("== Verbindung ==", seite)
        self.assertIn("/api/tasks/", seite)
        self.assertNotIn(TOKEN, seite)
