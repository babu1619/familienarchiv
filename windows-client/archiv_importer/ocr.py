"""Text gewinnen: vorhandener Textlayer, sonst Texterkennung mit Tesseract."""

from __future__ import annotations

import io
import logging

from archiv_core.pdftext import ist_pdf, text_lesen

log = logging.getLogger(__name__)

BILDENDUNGEN = (".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp", ".webp")
MIN_TEXT = 40          # weniger Zeichen = kein brauchbarer Textlayer
MAX_OCR_SEITEN = 4


class OcrFehlt(Exception):
    pass


def _tesseract(bild, pfad: str, sprache: str) -> str:
    try:
        import pytesseract
    except ImportError as exc:
        raise OcrFehlt("pytesseract fehlt") from exc
    if not pfad:
        raise OcrFehlt("Tesseract ist nicht installiert")
    pytesseract.pytesseract.tesseract_cmd = pfad
    try:
        return pytesseract.image_to_string(bild, lang=sprache)
    except pytesseract.TesseractError as exc:
        if "Failed loading language" in str(exc) or "lang" in str(exc).lower():
            log.warning("Sprachpaket %s fehlt – erkenne ohne Sprachpaket", sprache)
            return pytesseract.image_to_string(bild)
        raise
    except FileNotFoundError as exc:
        raise OcrFehlt(f"Tesseract nicht gefunden: {pfad}") from exc


def seitenbilder(daten: bytes, max_seiten: int = MAX_OCR_SEITEN, dpi: int = 300):
    import pypdfium2 as pdfium
    pdf = pdfium.PdfDocument(daten)
    try:
        for i in range(min(len(pdf), max_seiten)):
            yield pdf[i].render(scale=dpi / 72).to_pil()
    finally:
        pdf.close()


def text_gewinnen(daten: bytes, dateiname: str, tesseract_pfad: str, sprache: str = "deu") -> tuple[str, str]:
    """Gibt (text, hinweis) zurück. Wirft PasswortGeschuetzt bei verschlüsselten PDFs."""
    name = dateiname.lower()
    if ist_pdf(daten):
        text = text_lesen(daten)
        if len(text.strip()) >= MIN_TEXT:
            return text, ""
        try:
            teile = [_tesseract(b, tesseract_pfad, sprache) for b in seitenbilder(daten)]
        except OcrFehlt as exc:
            return text, f"Kein Textlayer und keine Texterkennung möglich: {exc}"
        return "\n".join(teile).strip(), "Text per Texterkennung (OCR)"
    if name.endswith(BILDENDUNGEN):
        from PIL import Image, ImageOps
        try:
            bild = ImageOps.exif_transpose(Image.open(io.BytesIO(daten)))
        except Exception as exc:
            return "", f"Bild nicht lesbar: {exc}"
        try:
            return _tesseract(bild.convert("RGB"), tesseract_pfad, sprache).strip(), "Text per Texterkennung (OCR)"
        except OcrFehlt as exc:
            return "", f"Keine Texterkennung möglich: {exc}"
    return "", "Dateityp wird nicht unterstützt"
