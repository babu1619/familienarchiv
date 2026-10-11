import unittest
from datetime import date

import hilfen  # noqa: F401  (setzt den Suchpfad)
from hilfen import FACTURX_XML, HANDWERKER, KASSENBON, RECHNUNG_STADTWERKE, pdf_aus_text

from archiv_core import muster, plausibilitaet, syntax, zugferd
from archiv_core.extraktion import feste_verfahren, titel_vorschlagen
from archiv_core.felder import Ergebnis


class SyntaxTest(unittest.TestCase):
    def test_betreff(self):
        v = syntax.betreff_auswerten("WG: Zahnarzt Kontrolle @Gesundheit/Arzt #steuer-2026 #Erstattung +Lisa !pruefen")
        self.assertEqual(v.kategorie, "Gesundheit/Arzt")
        self.assertEqual(v.tags, ["steuer-2026", "erstattung"])
        self.assertEqual(v.person, "Lisa")
        self.assertTrue(v.pruefen)
        self.assertEqual(v.titel, "Zahnarzt Kontrolle")

    def test_kategorie_mit_leerzeichen(self):
        v = syntax.betreff_auswerten('Bon @"Haushalt/Lebensmittel & Drogerie" #bar')
        self.assertEqual(v.kategorie, "Haushalt/Lebensmittel & Drogerie")
        v = syntax.betreff_auswerten("Bon @Haushalt/Garten_und_Balkon")
        self.assertEqual(v.kategorie, "Haushalt/Garten und Balkon")

    def test_mailtext(self):
        v, rest = syntax.mailtext_auswerten(
            "\nKategorie: Wohnen/Nebenkosten/Strom\nTags: steuer-2026, #Jahresabrechnung\nPerson: Familie\n"
            "Notiz: Nachzahlung\n\nHallo, anbei die Abrechnung.")
        self.assertEqual(v.kategorie, "Wohnen/Nebenkosten/Strom")
        self.assertEqual(v.tags, ["steuer-2026", "jahresabrechnung"])
        self.assertEqual(v.person, "Familie")
        self.assertEqual(v.notiz, "Nachzahlung")
        self.assertTrue(rest.startswith("Hallo"))


class MusterTest(unittest.TestCase):
    def test_betrag_parsen(self):
        self.assertEqual(muster.betrag_parsen("1.284,50"), 1284.5)
        self.assertEqual(muster.betrag_parsen("1,284.50"), 1284.5)
        self.assertEqual(muster.betrag_parsen("84,20 €"), 84.2)
        self.assertEqual(muster.betrag_formatieren(1284.5), "1.284,50")

    def test_rechnung(self):
        e = Ergebnis()
        muster.auslesen(RECHNUNG_STADTWERKE, e)
        self.assertEqual(e.wert("dokumenttyp"), "Rechnung")
        self.assertEqual(e.wert("datum"), "2026-10-03")
        self.assertTrue(e.felder["datum"].sicher)
        self.assertEqual(e.wert("betrag_brutto"), 84.2)
        self.assertTrue(e.felder["betrag_brutto"].sicher)
        self.assertEqual(e.wert("betrag_netto"), 70.76)
        self.assertEqual(e.wert("mwst"), 13.44)
        self.assertEqual(e.wert("faelligkeit"), "2026-10-17")
        self.assertFalse(e.felder["faelligkeit"].sicher)
        self.assertEqual(e.wert("rechnungsnummer"), "RE-2026-1187")
        self.assertEqual(e.wert("kundennummer"), "40 118 732")
        self.assertEqual(e.wert("iban"), "DE89370400440532013000")
        self.assertEqual(e.wert("zahlungsart"), "Lastschrift")

    def test_kassenbon(self):
        e = Ergebnis()
        muster.auslesen(KASSENBON, e)
        self.assertEqual(e.wert("dokumenttyp"), "Kassenbon")
        self.assertEqual(e.wert("betrag_brutto"), 95.58)
        self.assertEqual(e.wert("datum"), "2026-10-05")
        self.assertEqual(e.wert("zahlungsart"), "bar")

    def test_handwerker(self):
        e = Ergebnis()
        muster.auslesen(HANDWERKER, e)
        self.assertEqual(e.wert("dokumenttyp"), "Handwerkerrechnung")
        self.assertEqual(e.wert("betrag_brutto"), 1284.5)
        self.assertEqual(e.wert("arbeitskosten"), 640.0)
        self.assertEqual(e.wert("faelligkeit"), "2025-10-13")
        self.assertTrue(e.felder["faelligkeit"].sicher)
        self.assertEqual(e.wert("rechnungsnummer"), "2025-0931")


class ZugferdTest(unittest.TestCase):
    def test_eingebettetes_xml(self):
        pdf = pdf_aus_text("Rechnung", ("factur-x.xml", FACTURX_XML))
        e = Ergebnis()
        self.assertTrue(zugferd.auslesen(pdf, e))
        self.assertEqual(e.wert("betrag_brutto"), 84.2)
        self.assertEqual(e.wert("datum"), "2026-10-03")
        self.assertEqual(e.wert("faelligkeit"), "2026-10-17")
        self.assertEqual(e.wert("absender"), "Stadtwerke Musterstadt")
        self.assertEqual(e.wert("zahlungsart"), "Lastschrift")
        self.assertTrue(all(f.sicher for f in e.felder.values()))

    def test_erechnung_verdraengt_muster(self):
        pdf = pdf_aus_text(RECHNUNG_STADTWERKE, ("factur-x.xml", FACTURX_XML))
        e = feste_verfahren(pdf, RECHNUNG_STADTWERKE)
        self.assertEqual(e.felder["faelligkeit"].quelle, "e-rechnung")
        self.assertTrue(e.felder["faelligkeit"].sicher)


class PlausibilitaetTest(unittest.TestCase):
    def test_sicheres_dokument(self):
        e = feste_verfahren(None, RECHNUNG_STADTWERKE, bekannte_absender=["Stadtwerke Musterstadt"])
        e.setze("kategorie", "wohnen/nebenkosten/strom", "profil", True)
        plausibilitaet.pruefen(e, RECHNUNG_STADTWERKE, kategorien=["Wohnen/Nebenkosten/Strom"],
                               dokumenttypen=["Rechnung"], korrespondenten=["Stadtwerke Musterstadt"],
                               heute=date(2026, 10, 10))
        self.assertEqual(e.wert("kategorie"), "Wohnen/Nebenkosten/Strom")
        self.assertTrue(e.ist_sicher(), e.pruefungen)
        self.assertEqual(titel_vorschlagen(e), "Stadtwerke Musterstadt Rechnung Oktober 2026")

    def test_falscher_betrag(self):
        e = feste_verfahren(None, RECHNUNG_STADTWERKE)
        e.setze("betrag_brutto", 99.99, "ki", True, ueberschreiben=True)
        e.setze("kategorie", "Wohnen/Strom", "ki", True)
        plausibilitaet.pruefen(e, RECHNUNG_STADTWERKE, kategorien=["Wohnen/Nebenkosten/Strom"])
        self.assertFalse(e.felder["betrag_brutto"].sicher)
        self.assertFalse(e.felder["kategorie"].sicher)
        self.assertIn("Vorschlag", e.felder["kategorie"].hinweis)
        self.assertFalse(e.ist_sicher())

    def test_iban(self):
        self.assertTrue(muster.iban_gueltig("DE89370400440532013000"))
        self.assertFalse(muster.iban_gueltig("DE89370400440532013001"))


if __name__ == "__main__":
    unittest.main()
