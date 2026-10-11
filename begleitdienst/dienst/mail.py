"""E-Mail-Eingang: Archiv-Postfach per IMAP abrufen und Anhänge mit Kurzsyntax importieren."""

from __future__ import annotations

import email
import imaplib
import logging
import re
import smtplib
import ssl
import threading
from email import policy
from email.message import EmailMessage
from email.utils import parseaddr

from archiv_core.syntax import betreff_auswerten, mailtext_auswerten, zusammenfuehren

from .db import Datenbank, jetzt
from .importer import Importer
from .konfig import Konfig

log = logging.getLogger(__name__)

# flache Ordnernamen – funktionieren unabhängig vom Trennzeichen des IMAP-Servers
ORDNER_OK = "Archiv-Verarbeitet"
ORDNER_FEHLER = "Archiv-Fehler"
ORDNER_QUARANTAENE = "Archiv-Quarantaene"

ANHANG_TYPEN = ("application/pdf", "image/jpeg", "image/png", "image/tiff", "image/webp", "image/heic")
MIN_BILDGROESSE = 20_000  # kleinere Bilder sind meist Logos oder Signaturen


def absender_erlaubt(adresse: str, erlaubt: list[str]) -> bool:
    adresse = adresse.lower()
    for eintrag in erlaubt:
        if eintrag.startswith("@") and adresse.endswith(eintrag):
            return True
        if adresse == eintrag:
            return True
    return False


def _textkoerper(msg: EmailMessage) -> str:
    teil = msg.get_body(preferencelist=("plain", "html"))
    if teil is None:
        return ""
    inhalt = teil.get_content()
    if teil.get_content_type() == "text/html":
        inhalt = re.sub(r"<br\s*/?>|</p>", "\n", inhalt, flags=re.IGNORECASE)
        inhalt = re.sub(r"<[^>]+>", "", inhalt)
    return inhalt


def _weitergeleitete_domain(text: str) -> str:
    m = re.search(r"^\s*(?:von|from)\s*:.*?[\w.+-]+@([\w.-]+\.\w+)", text, re.IGNORECASE | re.MULTILINE)
    return m.group(1).lower() if m else ""


def anhaenge(msg: EmailMessage) -> list[tuple[str, bytes]]:
    ergebnis = []
    for teil in msg.walk():
        if teil.is_multipart():
            continue
        typ = teil.get_content_type()
        name = teil.get_filename() or ""
        if typ not in ANHANG_TYPEN and not name.lower().endswith(".pdf"):
            continue
        daten = teil.get_payload(decode=True) or b""
        if typ.startswith("image/") and len(daten) < MIN_BILDGROESSE:
            continue
        if not name:
            name = f"anhang.{typ.split('/')[-1]}"
        ergebnis.append((name, daten))
    return ergebnis


def nachricht_verarbeiten(roh: bytes, k: Konfig, db: Datenbank, importer: Importer) -> tuple[str, str]:
    """Wertet eine Mail aus. Gibt (ergebnis, details) zurück: verarbeitet | quarantaene | fehler | bekannt."""
    msg: EmailMessage = email.message_from_bytes(roh, policy=policy.default)  # type: ignore[assignment]
    absender = parseaddr(str(msg.get("From", "")))[1].lower()
    betreff = str(msg.get("Subject", ""))
    message_id = str(msg.get("Message-ID", "")) or f"{absender}|{betreff}|{msg.get('Date', '')}"
    if db.mail_bekannt(message_id):
        return "bekannt", "bereits verarbeitet"
    if not absender_erlaubt(absender, k.erlaubte_absender):
        db.mail_merken(message_id, absender, betreff, "quarantaene", "Absender nicht freigegeben")
        return "quarantaene", "Absender nicht freigegeben"

    text = _textkoerper(msg)
    v_text, rest = mailtext_auswerten(text)
    v = zusammenfuehren(betreff_auswerten(betreff), v_text)
    domain = _weitergeleitete_domain(rest) or absender.split("@")[-1]

    dateien = anhaenge(msg)
    if not dateien:
        if not rest.strip():
            db.mail_merken(message_id, absender, betreff, "fehler", "Kein Anhang und kein Text")
            return "fehler", "Kein Anhang und kein Text"
        kopf = f"Betreff: {v.titel or betreff}\nVon: {absender}\nDatum: {msg.get('Date', '')}\n\n"
        dateien = [((v.titel or "E-Mail")[:60].replace("/", "-") + ".txt", (kopf + rest).encode("utf-8"))]

    auftraege = []
    for i, (name, daten) in enumerate(dateien):
        titel = v.titel
        if titel and len(dateien) > 1:
            titel = f"{titel} ({i + 1})"
        auftrag = {
            "quelle": "mail", "titel": titel, "kategorie": v.kategorie, "tags": v.tags, "person": v.person,
            "dokumenttyp": v.dokumenttyp, "notiz": v.notiz, "pruefen_erzwingen": v.pruefen,
            "mail_domain": domain, "absender_mail": absender,
        }
        if k.rueckmeldung and k.smtp_server:
            auftrag["antwort_an"] = absender
        auftraege.append(importer.einreihen(daten, name, auftrag))
    details = f"{len(auftraege)} Dokument(e)" + (f", Kategorie {v.kategorie}" if v.kategorie else "")
    db.mail_merken(message_id, absender, betreff, "verarbeitet", details)
    return "verarbeitet", details


class MailEingang:
    def __init__(self, k: Konfig, db: Datenbank, importer: Importer):
        self.k, self.db, self.importer = k, db, importer
        self._stopp = threading.Event()
        self.letzter_abruf = ""
        self.letzter_fehler = ""

    def starten(self) -> None:
        threading.Thread(target=self._schleife, name="mail", daemon=True).start()

    def stoppen(self) -> None:
        self._stopp.set()

    def _schleife(self) -> None:
        while not self._stopp.is_set():
            try:
                self.abrufen()
                self.letzter_fehler = ""
            except Exception as exc:
                self.letzter_fehler = str(exc)[:300]
                log.warning("Mailabruf fehlgeschlagen: %s", exc)
            self.letzter_abruf = jetzt()
            self.db.zustand_setzen("mail_letzter_abruf", self.letzter_abruf)
            self.db.zustand_setzen("mail_letzter_fehler", self.letzter_fehler)
            self._stopp.wait(max(30, self.k.imap_intervall))

    def abrufen(self) -> int:
        verbindung = imaplib.IMAP4_SSL(self.k.imap_server, self.k.imap_port,
                                       ssl_context=ssl.create_default_context())
        try:
            verbindung.login(self.k.imap_benutzer, self.k.imap_passwort)
            for ordner in (ORDNER_OK, ORDNER_FEHLER, ORDNER_QUARANTAENE):
                verbindung.create(f'"{ordner}"')  # Fehler, wenn vorhanden – wird ignoriert
            verbindung.select(f'"{self.k.imap_ordner}"')
            _, daten = verbindung.uid("search", None, "ALL")
            uids = daten[0].split() if daten and daten[0] else []
            for uid in uids:
                _, teile = verbindung.uid("fetch", uid, "(BODY.PEEK[])")
                roh = next((t[1] for t in teile if isinstance(t, tuple)), None)
                if roh is None:
                    continue
                try:
                    ergebnis, details = nachricht_verarbeiten(roh, self.k, self.db, self.importer)
                except Exception as exc:
                    log.exception("Mail %s nicht verarbeitet", uid)
                    ergebnis, details = "fehler", str(exc)
                ziel = {"verarbeitet": ORDNER_OK, "bekannt": ORDNER_OK,
                        "quarantaene": ORDNER_QUARANTAENE}.get(ergebnis, ORDNER_FEHLER)
                self._verschieben(verbindung, uid, ziel)
                log.info("Mail %s: %s (%s)", uid.decode(), ergebnis, details)
            verbindung.expunge()
            return len(uids)
        finally:
            try:
                verbindung.logout()
            except Exception:
                pass

    @staticmethod
    def _verschieben(verbindung: imaplib.IMAP4, uid: bytes, ziel: str) -> None:
        typ, _ = verbindung.uid("copy", uid, f'"{ziel}"')
        if typ == "OK":
            verbindung.uid("store", uid, "+FLAGS", r"(\Deleted)")


def smtp_versender(k: Konfig):
    """Gibt eine Funktion zurück, die Rückmeldungen per SMTP versendet."""
    def senden(nachricht: EmailMessage) -> None:
        with smtplib.SMTP(k.smtp_server, k.smtp_port, timeout=30) as s:
            s.starttls(context=ssl.create_default_context())
            s.login(k.smtp_benutzer or k.imap_benutzer, k.smtp_passwort or k.imap_passwort)
            s.send_message(nachricht)
    return senden
