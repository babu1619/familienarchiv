"""Testhilfen: Beispielbelege als Text und PDF."""

from __future__ import annotations

import io
import os
import sys

WURZEL = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(WURZEL, "begleitdienst"))
sys.path.insert(0, os.path.join(WURZEL, "windows-client"))

RECHNUNG_STADTWERKE = """Stadtwerke Musterstadt GmbH
Postfach 100, 12345 Musterstadt
Familie Schmid
Musterweg 1
12345 Musterstadt
Kundennummer: 40 118 732
Rechnungsdatum: 03.10.2026
Rechnung Nr. RE-2026-1187
Abschlag Strom Oktober 70,76 €
Nettobetrag 70,76 €
Umsatzsteuer 19 % 13,44 €
Gesamtbetrag 84,20 €
Der Betrag wird innerhalb von 14 Tagen per Lastschrift von Ihrem Konto eingezogen.
Bankverbindung: IBAN DE89 3704 0044 0532 0130 00
USt-IdNr. DE123456789
"""

KASSENBON = """BAUMARKT MUSTERHAUS
Filiale 0815
Schrauben 4x40 3,49
Akkuschrauber 89,99
Kabelbinder 2,10
SUMME EUR 95,58
Gegeben BAR 100,00
Rückgeld 4,42
MwSt 19% Netto 80,32 MwSt 15,26
05.10.2026 14:31 Bon-Nr. 4711
TSE-Signatur xyz
"""

HANDWERKER = """Elektro Meier
Meisterbetrieb
Rechnung
Rechnungsnummer: 2025-0931
Datum: 29.09.2025
Montage Unterverteilung, Arbeitszeit 8 Std.
Arbeitskosten (Lohnanteil nach § 35a EStG) 640,00 €
Material 439,41 €
Nettobetrag 1.079,41 €
MwSt 19 % 205,09 €
Rechnungsbetrag 1.284,50 €
Zahlbar bis 13.10.2025 ohne Abzug.
"""

FACTURX_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<rsm:CrossIndustryInvoice xmlns:rsm="urn:un:unece:uncefact:data:standard:CrossIndustryInvoice:100"
 xmlns:ram="urn:un:unece:uncefact:data:standard:ReusableAggregateBusinessInformationEntity:100"
 xmlns:udt="urn:un:unece:uncefact:data:standard:UnqualifiedDataType:100">
 <rsm:ExchangedDocument>
  <ram:ID>RE-2026-1187</ram:ID>
  <ram:TypeCode>380</ram:TypeCode>
  <ram:IssueDateTime><udt:DateTimeString format="102">20261003</udt:DateTimeString></ram:IssueDateTime>
 </rsm:ExchangedDocument>
 <rsm:SupplyChainTradeTransaction>
  <ram:ApplicableHeaderTradeAgreement>
   <ram:SellerTradeParty><ram:Name>Stadtwerke Musterstadt</ram:Name></ram:SellerTradeParty>
   <ram:BuyerTradeParty><ram:ID>40118732</ram:ID><ram:Name>Schmid</ram:Name></ram:BuyerTradeParty>
  </ram:ApplicableHeaderTradeAgreement>
  <ram:ApplicableHeaderTradeSettlement>
   <ram:SpecifiedTradeSettlementPaymentMeans><ram:TypeCode>59</ram:TypeCode></ram:SpecifiedTradeSettlementPaymentMeans>
   <ram:SpecifiedTradePaymentTerms>
    <ram:DueDateDateTime><udt:DateTimeString format="102">20261017</udt:DateTimeString></ram:DueDateDateTime>
   </ram:SpecifiedTradePaymentTerms>
   <ram:SpecifiedTradeSettlementHeaderMonetarySummation>
    <ram:TaxBasisTotalAmount>70.76</ram:TaxBasisTotalAmount>
    <ram:TaxTotalAmount currencyID="EUR">13.44</ram:TaxTotalAmount>
    <ram:GrandTotalAmount>84.20</ram:GrandTotalAmount>
   </ram:SpecifiedTradeSettlementHeaderMonetarySummation>
  </ram:ApplicableHeaderTradeSettlement>
 </rsm:SupplyChainTradeTransaction>
</rsm:CrossIndustryInvoice>
"""


def pdf_aus_text(text: str, anhang: tuple[str, bytes] | None = None) -> bytes:
    """Erzeugt ein PDF mit Textlayer (und optional eingebetteter Datei)."""
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    puffer = io.BytesIO()
    c = canvas.Canvas(puffer, pagesize=A4)
    y = 800
    for zeile in text.splitlines():
        c.drawString(60, y, zeile)
        y -= 16
    c.save()
    daten = puffer.getvalue()
    if anhang:
        from pypdf import PdfReader, PdfWriter
        w = PdfWriter(clone_from=PdfReader(io.BytesIO(daten)))
        w.add_attachment(anhang[0], anhang[1])
        aus = io.BytesIO()
        w.write(aus)
        daten = aus.getvalue()
    return daten


def bild_pdf_aus_text(text: str) -> bytes:
    """PDF ohne Textlayer (wie ein Scan): Text als Bild."""
    from PIL import Image, ImageDraw, ImageFont

    bild = Image.new("RGB", (1240, 1754), "white")
    zeichnen = ImageDraw.Draw(bild)
    try:
        schrift = ImageFont.truetype("DejaVuSans.ttf", 30)
    except OSError:
        schrift = ImageFont.load_default()
    y = 120
    for zeile in text.splitlines():
        zeichnen.text((100, y), zeile, fill="black", font=schrift)
        y += 46
    puffer = io.BytesIO()
    bild.save(puffer, format="PDF", resolution=150)
    return puffer.getvalue()
