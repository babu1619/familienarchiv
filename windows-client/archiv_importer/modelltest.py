"""Modelltest: misst Trefferquote und Zeit eines Modells an bestätigten eigenen Belegen.

Im Testordner liegt zu jedem Beleg eine Datei mit den richtigen Werten:

    stadtwerke.pdf
    stadtwerke.erwartet.json   {"datum": "2026-10-03", "betrag_brutto": 84.20,
                                "dokumenttyp": "Rechnung", "absender": "Stadtwerke Musterstadt",
                                "kategorie": "Wohnen/Nebenkosten/Strom"}

Aufruf:  ArchivImporter.exe --modelltest C:\\Testsatz
Ergebnis: modelltest-<modell>.txt (Zusammenfassung) und .csv (je Beleg) im Testordner.
"""

from __future__ import annotations

import csv
import json
import os
import tempfile
import time

from .einstellungen import Einstellungen
from .speicher import Speicher
from .verarbeitung import Verarbeitung, dateien_sammeln

VERGLEICH = ("datum", "betrag_brutto", "dokumenttyp", "absender", "kategorie")


def _gleich(feld: str, a, b) -> bool:
    if a is None or b is None:
        return a == b
    if feld == "betrag_brutto":
        return abs(float(a) - float(b)) < 0.005
    return str(a).strip().lower() == str(b).strip().lower()


def ausfuehren(ordner: str, e: Einstellungen, ausgabe=None) -> int:
    protokoll: list[str] = []

    def schreiben(text: str = "") -> None:
        protokoll.append(text)
        (ausgabe or print)(text)

    try:
        return _ausfuehren(ordner, e, schreiben)
    finally:
        if protokoll and os.path.isdir(ordner):
            name = (e.modell if e.ki_aktiv else "ohne-ki").replace(":", "_").replace("/", "_")
            with open(os.path.join(ordner, f"modelltest-{name}.txt"), "w", encoding="utf-8") as f:
                f.write("\n".join(protokoll) + "\n")


def _ausfuehren(ordner: str, e: Einstellungen, ausgabe) -> int:
    paare = []
    for pfad in dateien_sammeln([ordner]):
        erwartet = os.path.splitext(pfad)[0] + ".erwartet.json"
        if os.path.exists(erwartet):
            with open(erwartet, encoding="utf-8") as f:
                paare.append((pfad, json.load(f)))
    if not paare:
        ausgabe("Keine Belege mit .erwartet.json gefunden.")
        return 1

    tmp = tempfile.mkdtemp()
    speicher = Speicher(os.path.join(tmp, "test.sqlite"))
    v = Verarbeitung(speicher, e)
    speicher.stapel_anlegen("Modelltest", {}, [p for p, _ in paare])
    zeilen, treffer, gesamt, zeiten = [], {f: 0 for f in VERGLEICH}, {f: 0 for f in VERGLEICH}, []
    ausgabe(f"Modell {e.modell if e.ki_aktiv else '(KI aus)'} · {len(paare)} Belege\n")
    for doc, (pfad, soll) in zip(speicher.mit_status("wartet"), paare):
        start = time.time()
        doc = v.analysieren(doc)
        zeiten.append(time.time() - start)
        felder = (doc.get("ergebnis") or {}).get("felder") or {}
        zeile = {"datei": os.path.basename(pfad), "sekunden": round(zeiten[-1], 1), "status": doc["status"]}
        for f in VERGLEICH:
            if f not in soll:
                continue
            ist = (felder.get(f) or {}).get("wert")
            ok = _gleich(f, ist, soll[f])
            gesamt[f] += 1
            treffer[f] += ok
            zeile[f] = "ok" if ok else f"{ist!r} statt {soll[f]!r}"
        zeilen.append(zeile)
        ausgabe(f"{zeile['datei'][:40]:40} {zeile['sekunden']:6.1f} s  " +
                "  ".join(f"{f}:{'✓' if zeile.get(f) == 'ok' else '✗'}" for f in VERGLEICH if f in zeile))
    ausgabe("\nTrefferquote je Feld:")
    for f in VERGLEICH:
        if gesamt[f]:
            ausgabe(f"  {f:15} {100 * treffer[f] / gesamt[f]:5.1f} %  ({treffer[f]}/{gesamt[f]})")
    ausgabe(f"Mittlere Zeit je Beleg: {sum(zeiten) / len(zeiten):.1f} s")
    name = (e.modell if e.ki_aktiv else "ohne-ki").replace(":", "_").replace("/", "_")
    datei = os.path.join(ordner, f"modelltest-{name}.csv")
    with open(datei, "w", newline="", encoding="utf-8-sig") as f:
        schreiber = csv.DictWriter(f, fieldnames=["datei", "sekunden", "status", *VERGLEICH], delimiter=";")
        schreiber.writeheader()
        schreiber.writerows(zeilen)
    ausgabe(f"Bericht: {datei}")
    return 0
