# Familienarchiv – Version 1 (Phase 1)

Archiv für Rechnungen, Kassenbons, Verträge und Bescheide auf Basis von **Paperless-ngx**.
Dazu kommen zwei eigene Bausteine:

| Baustein | Ordner | Aufgabe |
| --- | --- | --- |
| **Begleitdienst** | `begleitdienst/` | Home-Assistant-Add-on: Import-API, E-Mail-Eingang mit Kurzsyntax, Extraktion (E-Rechnung, Muster, Absenderprofile), Plausibilitätsprüfung, Weboberfläche, Download des Windows-Clients, Sensoren für Home Assistant |
| **Windows-Client** | `windows-client/` | `ArchivImporter.exe`: Massenverarbeitung auf dem PC mit lokalem KI-Modell (Ollama), Texterkennung (Tesseract), Prüfansicht, Übergabe von Datei und Daten an den Begleitdienst |
| Gemeinsame Bibliothek | `begleitdienst/archiv_core/` | Feldbeschreibungen, Kurzsyntax, ZUGFeRD/XRechnung, Textmuster, Plausibilität – von beiden genutzt |

Bewusst **noch nicht** enthalten: Weitergabe der Kreditkarten-Abrechnungen an den Finanz-Tracker
und die übrige Finanz-Tracker-Kopplung, Steuer-Jahresmappe, automatisches Nachanreichern des
Paperless-Posteingangs durch den Client.

```
Scan-Ordner ─┐
Browser ─────┤                        ┌──────────────┐
E-Mail ──────┼──► Begleitdienst ─────►│ Paperless-ngx│  OCR, PDF/A, Suche, Tags, Benutzerfelder
Windows-     │    (Extraktion,        └──────────────┘
Client ──────┘     Zuordnung)              ▲
(lokale KI)                                │ Posteingang = Prüfkorb
```

---

## 1. Paperless-ngx auf dem Home Assistant Green

1. Paperless-ngx als Add-on installieren (z. B. aus einem Community-Add-on-Repository für Paperless-ngx)
   und die Dateiablage auf die USB-SSD legen.
2. In Paperless einen Benutzer anlegen und unter *Profil › API-Auth-Token* ein Token erzeugen.
3. Empfohlen in den Paperless-Einstellungen: OCR-Sprache `deu`, Barcode-Trennung an, Unterordner des
   Scan-Ordners als Tags.

Der Begleitdienst legt beim ersten Start selbst an, was er braucht: die Benutzerfelder
(Betrag brutto, Fälligkeit, Person, Zahlungsstatus …) und den Tag **Posteingang** (als Posteingangs-Tag).
Kategorien sind Tags mit dem Präfix `kat:`, z. B. `kat:Wohnen/Nebenkosten/Strom` – die gewünschten
Kategorien einmal so in Paperless anlegen.

> Paperless versieht jedes neue Dokument mit allen Posteingangs-Tags. Am besten nur **Posteingang**
> als Posteingangs-Tag markieren. Der Begleitdienst nimmt ihn bei sicheren Dokumenten wieder weg –
> was danach im Posteingang steht, ist der Prüfkorb.

## 2. Begleitdienst als Add-on

1. Home Assistant › Einstellungen › Add-ons › Add-on-Store › ⋮ › *Repositories* →
   `https://github.com/babu1619/familienarchiv` eintragen.
2. Add-on **Familienarchiv Begleitdienst** installieren und konfigurieren:

| Option | Bedeutung |
| --- | --- |
| `paperless_url` | Adresse von Paperless aus Sicht des Add-ons, z. B. `http://192.168.1.20:8000` |
| `paperless_token` | API-Token aus Schritt 1 |
| `paperless_oeffentliche_url` | optional: Adresse für Links im Browser, falls abweichend |
| `paperless_ssl_pruefen` | bei `https://` mit selbst signiertem Zertifikat ausschalten |
| `api_token` | frei gewähltes, langes Geheimnis für Windows-Client und Home Assistant |
| `web_benutzer` / `web_passwort` | Anmeldung an der Weboberfläche (Pflicht) |
| `personen` | Auswahl für das Feld *Person*, z. B. `Familie`, `Benjamin`, `Lisa` |
| `imap_*` | Archiv-Postfach (leer lassen = E-Mail-Eingang aus) |
| `erlaubte_absender` | Adressen (`name@example.de`) oder ganze Domains (`@example.de`); alles andere geht in Quarantäne |
| `rueckmeldung`, `smtp_*` | optional: Antwortmail mit den erkannten Daten |
| `github_repo` | `babu1619/familienarchiv` – für „Von GitHub holen“ auf der Download-Seite |
| `github_token` | nur bei privatem Repository (Token mit Leserecht auf Inhalte) |

Weboberfläche: `http://<home-assistant>:8099` – Übersicht, Importe, E-Mail-Eingang und **Windows-Client**
(Download, Verbindungsdaten, Einrichtungsschritte).

## 3. Windows-Client bauen und bereitstellen

Jede Änderung am Client auf `main` startet den GitHub-Workflow *Windows-Client bauen*. Er baut
`ArchivImporter.exe` und legt das Release `client-v<Version>` an; die Version steht in
`windows-client/VERSION`. Für eine neue Version diese Datei erhöhen. Manuell starten geht unter
*Actions › Windows-Client bauen › Run workflow*.

Danach in der Weboberfläche unter *Windows-Client* auf **Von GitHub holen** klicken. Alternativ die .exe
dort hochladen oder lokal mit `windows-client\build.ps1` bauen. Der Client meldet sich selbst, wenn der
Begleitdienst eine neuere Version anbietet.

## 4. Windows-Client einrichten (Lenovo T490, 8 GB)

1. **Ollama** installieren und ein kleines Modell laden: `ollama pull qwen2.5:3b`
   (mit 8 GB nur Modelle bis etwa 3–4 Mrd. Parameter; auf einem stärkeren Rechner einfach ein größeres
   Modell laden und in den Einstellungen eintragen).
2. **Tesseract-OCR** für Windows mit Sprachpaket Deutsch installieren (für Scans und Fotos).
3. `ArchivImporter.exe` von der Download-Seite laden und starten, unter *Einstellungen* Server-Adresse
   und API-Token eintragen, *Verbindung testen*.

Ablauf: *Ordner hinzufügen* → Analyse läuft im Hintergrund (Nachtmodus hält den Laptop wach) →
sichere Dokumente gesammelt übergeben, unsichere prüfen und mit **Enter** freigeben oder ungeprüft in
den Paperless-Posteingang schicken. Korrekturen an der Kategorie lassen sich als Regel für den Absender
speichern.

**Modelltest:** Testordner mit Belegen und je einer Datei `beleg.erwartet.json` anlegen, dann
`ArchivImporter.exe --modelltest C:\Testsatz`. Ergebnis: `modelltest-<modell>.txt` und `.csv` im Ordner.

## E-Mail-Kurzsyntax

```
Betreff:  Zahnarzt Kontrolle @Gesundheit/Arzt #steuer-2026 #erstattung +Lisa !pruefen
```

`@` Kategorie · `#` Tag · `+` Person · `!pruefen` landet immer im Posteingang · Rest = Titel.
Leerzeichen in Kategorien als `_` oder in Anführungszeichen. Alternativ Zeilen am Anfang des Mailtexts:
`Kategorie:`, `Tags:`, `Person:`, `Typ:`, `Notiz:`. Unbekannte Kategorien werden nicht angelegt,
sondern mit Vorschlag in den Posteingang gelegt. Verarbeitete Mails wandern in die Ordner
`Archiv-Verarbeitet`, `Archiv-Fehler` bzw. `Archiv-Quarantaene`.

## Sensoren in Home Assistant

```yaml
# configuration.yaml
rest:
  - resource: http://localhost:8099/api/v1/ha/sensoren
    headers:
      Authorization: !secret archiv_api_bearer   # "Bearer <api_token>"
    scan_interval: 300
    sensor:
      - name: Archiv Posteingang
        value_template: "{{ value_json.posteingang }}"
      - name: Archiv Fristen 7 Tage
        value_template: "{{ value_json.fristen_7_tage }}"
      - name: Archiv Importfehler
        value_template: "{{ value_json.auftraege_fehler }}"
```

Damit lassen sich Erinnerungen als Push über die Home-Assistant-App automatisieren.

## API des Begleitdienstes (Bearer-Token)

| Methode | Pfad | Zweck |
| --- | --- | --- |
| GET | `/api/v1/status` | Version, Paperless erreichbar |
| GET | `/api/v1/stammdaten` | Kategorien, Tags, Korrespondenten, Dokumenttypen, Personen, Absenderprofile |
| POST | `/api/v1/hash-pruefung` | `{"sha256": [...]}` → bereits archivierte Dateien |
| POST | `/api/v1/import` | multipart: `datei` + `daten` (JSON mit Feldern, Quelle, Sicherheit) → Auftrag |
| GET | `/api/v1/import/{id}` | Status eines Auftrags, Paperless-ID, Link |
| POST | `/api/v1/korrekturen` | Korrekturen aus der Prüfansicht (lernt Kategorie-Regeln) |
| GET | `/api/v1/client/version` | bereitgestellte Version des Windows-Clients |
| GET | `/api/v1/ha/sensoren` | Kennzahlen für Home Assistant |

## Entwicklung und Tests

```
pip install -r begleitdienst/requirements.txt pypdfium2 pytesseract pillow reportlab
cd tests && python -m unittest discover -v
```

Die Tests laufen gegen ein nachgebautes Paperless und ein nachgebautes Ollama (`tests/fake_paperless.py`,
`tests/test_client.py`) – vom Mailempfang bis zum fertig verschlagworteten Paperless-Dokument.
Start aus dem Quellcode: `python -m dienst` (im Ordner `begleitdienst`, Umgebungsvariablen `ARCHIV_…`)
bzw. `python windows-client/start.py --browser`.
