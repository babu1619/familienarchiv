"use strict";

const TOKEN = new URLSearchParams(location.search).get("t") || "";
const $ = (s) => document.querySelector(s);
const QUELLEN = { vorgabe: "Vorgabe", manuell: "geprüft", "e-rechnung": "E-Rechnung", profil: "Absenderprofil",
  muster: "Muster", ki: "KI", berechnet: "berechnet" };
const STATUS = { wartet: "○ wartet", analyse: "◐ läuft", sicher: "● sicher", pruefen: "● prüfen",
  duplikat: "● Duplikat", fehler: "● Fehler", bereit: "◐ wird übergeben", uebertragen: "◐ übertragen",
  archiviert: "✓ archiviert", uebertragungsfehler: "● Übergabe fehlgeschlagen" };
const FILTER = [["alle", "Alle", null], ["pruefen", "Prüfen", ["pruefen"]], ["sicher", "Sicher", ["sicher"]],
  ["offen", "In Arbeit", ["wartet", "analyse", "bereit", "uebertragen"]],
  ["fehler", "Fehler", ["fehler", "uebertragungsfehler"]], ["archiviert", "Archiviert", ["archiviert", "duplikat"]]];
const HAUPTFELDER = [
  ["titel", "Titel", "text", "breit"], ["dokumenttyp", "Dokumenttyp", "liste:dokumenttypen"],
  ["datum", "Datum", "date"], ["absender", "Absender", "text:liste-absender", "breit"],
  ["betrag_brutto", "Betrag brutto", "betrag"], ["faelligkeit", "Fälligkeit", "date"],
  ["kategorie", "Kategorie", "text:liste-kategorien", "breit"], ["person", "Person", "liste:personen"],
  ["tags", "Tags (kommagetrennt)", "tags"],
];
const WEITERE = [
  ["betrag_netto", "Betrag netto", "betrag"], ["mwst", "MwSt", "betrag"], ["arbeitskosten", "Arbeitskosten", "betrag"],
  ["rechnungsnummer", "Rechnungsnummer", "text"], ["kundennummer", "Kundennummer", "text"],
  ["zahlungsart", "Zahlungsart", "liste:zahlungsarten"], ["zahlungsstatus", "Zahlungsstatus", "liste:zahlungsstati"],
  ["iban", "IBAN", "text"], ["garantie_bis", "Garantie bis", "date"], ["vertragsende", "Vertragsende", "date"],
  ["papier_ablageort", "Papier-Ablageort", "text"],
];
const LISTEN = { zahlungsarten: ["bar", "Karte", "Lastschrift", "Überweisung", "PayPal", "sonstige"],
  zahlungsstati: ["offen", "bezahlt"] };

let zustand = null, stamm = { kategorien: [], korrespondenten: [], dokumenttypen: [], personen: [] };
let gewaehlt = null, geladen = null, filter = "alle", modus = "ordner";

// ------------------------------------------------------------------ Hilfen

async function api(pfad, optionen = {}) {
  const kopf = { "X-Token": TOKEN, ...(optionen.headers || {}) };
  if (optionen.json !== undefined) { kopf["Content-Type"] = "application/json"; optionen.body = JSON.stringify(optionen.json); }
  const r = await fetch(pfad, { ...optionen, headers: kopf });
  const daten = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(daten.fehler || `Fehler ${r.status}`);
  return daten;
}
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const euro = (w) => (w === null || w === undefined || w === "") ? "" :
  Number(w).toLocaleString("de-DE", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const datumDe = (d) => d ? `${d.slice(8, 10)}.${d.slice(5, 7)}.${d.slice(0, 4)}` : "";
function dauer(s) {
  if (s === null || s === undefined) return "";
  if (s < 90) return `ca. ${Math.max(1, Math.round(s))} s`;
  if (s < 5400) return `ca. ${Math.round(s / 60)} Min.`;
  return `ca. ${(s / 3600).toLocaleString("de-DE", { maximumFractionDigits: 1 })} Std.`;
}

// ------------------------------------------------------------------ Zustand und Liste

async function aktualisieren() {
  try { zustand = await api("/api/zustand"); } catch (e) { $("#server-status").textContent = e.message; return; }
  const z = zustand.zaehler, gesamt = zustand.gesamt || 0;
  const fertig = gesamt - zustand.offen;
  $("#einrichtung").hidden = !zustand.einstellungen_fehlen;
  $("#server-status").textContent = zustand.server.erreichbar
    ? `Server verbunden${zustand.server.paperless === false ? " · Paperless nicht erreichbar" : ""}`
    : `Server: ${zustand.server.fehler}`;
  const upd = $("#update");
  if (zustand.server.update) { upd.hidden = false; upd.href = zustand.server.update.link; upd.textContent = `Version ${zustand.server.update.version} verfügbar`; }
  else upd.hidden = true;
  $("#stapel-name").textContent = zustand.stapel ? `Stapel „${zustand.stapel.name}“` : "Noch kein Stapel";
  const rest = zustand.offen ? (zustand.restzeit ? ` · Rest ${dauer(zustand.restzeit)}` : " · Restzeit wird ermittelt") : "";
  $("#stapel-zahl").textContent = gesamt ? `${fertig} von ${gesamt} analysiert${rest}` : "";
  const prozent = gesamt ? Math.round(100 * fertig / gesamt) : 0;
  $("#balken-wert").style.width = prozent + "%";
  $("#balken").setAttribute("aria-valuenow", prozent);
  const zahl = (k) => z[k] || 0;
  $("#zaehler").innerHTML = [
    ["var(--ok)", `${zahl("sicher")} sicher`], ["var(--warn)", `${zahl("pruefen")} prüfen`],
    ["var(--leise)", `${zahl("duplikat")} Duplikate`], ["var(--fehler)", `${zahl("fehler") + zahl("uebertragungsfehler")} Fehler`],
    ["var(--akzent)", `${zahl("archiviert")} archiviert`],
  ].map(([farbe, text]) => `<span><span class="punkt" style="color:${farbe}">●</span>${esc(text)}</span>`).join("");
  const ki = zustand.ki;
  $("#ki-modell").textContent = ki.aktiv ? ki.modell : "aus";
  $("#ki-status").textContent = !ki.aktiv ? "nur feste Verfahren" : !ki.erreichbar ? "Ollama nicht erreichbar"
    : !ki.modell_vorhanden ? "Modell nicht geladen (ollama pull)" : zustand.mittlere_dauer ? `ca. ${Math.round(zustand.mittlere_dauer)} s je Dokument` : "bereit";
  $("#nachtmodus").checked = zustand.nachtmodus;
  $("#knopf-pause").textContent = zustand.pausiert ? "Fortsetzen" : "Pausieren";
  $("#knopf-alle").textContent = `Alle sicheren übergeben (${zahl("sicher")})`;
  $("#knopf-alle").disabled = !zahl("sicher");
  const unterwegs = zahl("bereit") + zahl("uebertragen");
  $("#uebergabe-status").textContent = zustand.server.uebergabe_fehler
    ? `Übergabe wartet: ${zustand.server.uebergabe_fehler}`
    : `Übergabe: ${zahl("archiviert")} archiviert · ${unterwegs} unterwegs`;
  filterZeichnen();
  tabelleZeichnen();
  if (gewaehlt && geladen) {
    const kurz = zustand.dokumente.find((d) => d.id === gewaehlt);
    if (kurz && kurz.status !== geladen.status && !formularGeaendert()) detailLaden(gewaehlt);
  }
}

function filterZeichnen() {
  const z = zustand.zaehler;
  $("#filter").innerHTML = FILTER.map(([id, name, status]) => {
    const n = status ? status.reduce((s, k) => s + (z[k] || 0), 0) : zustand.gesamt;
    return `<button type="button" data-filter="${id}" class="${filter === id ? "aktiv" : ""}" aria-pressed="${filter === id}">${name} ${n}</button>`;
  }).join("");
}

function sichtbareDokumente() {
  const f = FILTER.find(([id]) => id === filter);
  return zustand.dokumente.filter((d) => !f[2] || f[2].includes(d.status));
}

function tabelleZeichnen() {
  const docs = sichtbareDokumente();
  $("#leer").hidden = docs.length > 0;
  $("#tabelle").innerHTML = docs.map((d) => {
    const nebeninfo = ["duplikat", "fehler", "uebertragungsfehler", "wartet", "analyse"].includes(d.status);
    const zellen = nebeninfo
      ? `<td colspan="4" class="leise">${esc(d.status === "analyse" ? "Text und KI laufen …" : d.fehler || (d.status === "wartet" ? "in der Warteschlange" : ""))}</td>`
      : `<td>${esc(d.absender)}</td><td>${datumDe(d.datum)}</td><td class="r mono">${d.betrag != null ? euro(d.betrag) + " €" : ""}</td><td class="leise">${esc(d.kategorie)}</td>`;
    return `<tr class="zeile-dok${d.id === gewaehlt ? " gewaehlt" : ""}" data-id="${d.id}" tabindex="0">
      <td class="st st-${d.status}">${STATUS[d.status] || d.status}</td><td class="mono" title="${esc(d.dateiname)}">${esc(d.dateiname)}</td>${zellen}</tr>`;
  }).join("");
}

// ------------------------------------------------------------------ Detail

async function detailLaden(id) {
  gewaehlt = id;
  tabelleZeichnen();
  try { geladen = await api(`/api/dokument/${id}`); } catch (e) { $("#detail").innerHTML = `<p class="meldung">${esc(e.message)}</p>`; return; }
  detailZeichnen();
}

function feldHtml([name, label, typ, breit], e) {
  const felder = e.felder || {};
  const fw = felder[name];
  let wert = name === "titel" ? e.titel : name === "tags" ? (e.tags || []).join(", ") : fw ? fw.wert : "";
  if (typ === "betrag" && wert !== "" && wert != null) wert = euro(wert);
  const unsicher = fw && !fw.sicher;
  const quelle = fw ? ` · ${QUELLEN[fw.quelle] || fw.quelle}${unsicher ? " · prüfen" : ""}` : "";
  const id = `f-${name}`;
  let eingabe;
  if (typ.startsWith("liste:")) {
    const optionen = LISTEN[typ.slice(6)] || stamm[typ.slice(6)] || [];
    const alle = wert && !optionen.includes(wert) ? [wert, ...optionen] : optionen;
    eingabe = `<select id="${id}" data-feld="${name}"><option value="">–</option>${alle.map((o) => `<option${o === wert ? " selected" : ""}>${esc(o)}</option>`).join("")}</select>`;
  } else {
    const liste = typ.startsWith("text:") ? ` list="${typ.slice(5)}"` : "";
    const art = typ === "date" ? "date" : "text";
    eingabe = `<input id="${id}" type="${art}" data-feld="${name}" value="${esc(wert ?? "")}"${liste}${typ === "betrag" ? ' inputmode="decimal" class="mono"' : ""}>`;
  }
  const hinweis = fw && fw.hinweis ? `<span class="hinweis">${esc(fw.hinweis)}</span>` : "";
  return `<div class="feld${unsicher ? " unsicher" : ""}${breit ? " breit" : ""}"><label for="${id}"><span>${label}<span class="quelle">${esc(quelle)}</span></span></label>${eingabe}${hinweis}</div>`;
}

function detailZeichnen() {
  const d = geladen, e = d.ergebnis || {};
  const bearbeitbar = ["sicher", "pruefen", "uebertragungsfehler"].includes(d.status);
  let html = `<div class="zeile zwischen"><h2>${esc(d.dateiname)}</h2>
    <button type="button" class="link" id="knopf-vorschau">Vorschau</button></div>
    <iframe id="vorschau" class="vorschau" title="Vorschau" hidden></iframe>`;
  if (d.fehler) html += `<p class="meldung${d.status === "archiviert" ? " ok-meldung" : ""}">${esc(d.fehler)}</p>`;
  if (d.status === "archiviert" && d.link) html += `<p><a href="${esc(d.link)}" target="_blank" rel="noopener">In Paperless öffnen ↗</a></p>`;
  if (e.felder) {
    html += `<form id="detail-form"><div class="felder">${HAUPTFELDER.map((f) => feldHtml(f, e)).join("")}</div>
      <details${WEITERE.some(([n]) => e.felder[n] && !e.felder[n].sicher) ? " open" : ""}><summary>Weitere Felder</summary>
      <div class="felder">${WEITERE.map((f) => feldHtml(f, e)).join("")}</div></details>`;
    const pr = e.pruefungen || [];
    if (pr.length) html += `<div class="pruefungen"><strong>Plausibilitätsprüfung</strong>${pr.map((p) =>
      `<span><span class="${p.ok ? "ok" : "nok"}">${p.ok ? "✓" : "✗"}</span> ${esc(p.text)}</span>`).join("")}</div>`;
    if ((e.hinweise || []).length) html += `<p class="leise klein">${e.hinweise.map(esc).join(" · ")}</p>`;
    if (bearbeitbar) {
      html += `<label class="haken"><input type="checkbox" id="als-regel"> Kategorie als Regel für diesen Absender speichern</label>
      <div class="aktionen"><button type="submit" class="primaer" id="knopf-freigeben">Freigeben · Enter</button>
      <button type="button" id="knopf-ungeprueft">Ungeprüft übergeben</button>
      <button type="button" id="knopf-speichern">Nur speichern</button></div>`;
    }
    html += `</form>`;
  }
  html += `<div class="aktionen">${["fehler", "sicher", "pruefen", "duplikat", "uebertragungsfehler"].includes(d.status)
    ? '<button type="button" id="knopf-erneut">Erneut analysieren</button>' : ""}
    ${d.status !== "uebertragen" && d.status !== "bereit" ? '<button type="button" id="knopf-entfernen">Aus Liste entfernen</button>' : ""}</div>`;
  $("#detail").innerHTML = html;
  const form = $("#detail-form");
  if (form) {
    form.querySelectorAll("input, select").forEach((el) => { el.dataset.start = el.value; });
    form.addEventListener("submit", (ev) => { ev.preventDefault(); freigeben(true); });
  }
}

function formularGeaendert() {
  const form = $("#detail-form");
  return !!form && [...form.querySelectorAll("[data-feld]")].some((el) => el.value !== el.dataset.start);
}

async function speichern() {
  const form = $("#detail-form");
  if (!form) return;
  const felder = {}; let titel, tags;
  form.querySelectorAll("[data-feld]").forEach((el) => {
    if (el.value === el.dataset.start) return;
    if (el.dataset.feld === "titel") titel = el.value;
    else if (el.dataset.feld === "tags") tags = el.value.split(",").map((t) => t.trim()).filter(Boolean);
    else felder[el.dataset.feld] = el.value;
  });
  const daten = { felder, als_regel: $("#als-regel")?.checked || false };
  if (titel !== undefined) daten.titel = titel;
  if (tags !== undefined) daten.tags = tags;
  if (Object.keys(felder).length || titel !== undefined || tags !== undefined) {
    geladen = await api(`/api/dokument/${gewaehlt}`, { method: "POST", json: daten });
  }
}

async function freigeben(geprueft) {
  try {
    await speichern();
    await api(`/api/dokument/${gewaehlt}/freigeben`, { method: "POST", json: { geprueft } });
    const naechstes = sichtbareDokumente().find((d) => d.id !== gewaehlt && ["pruefen", "sicher"].includes(d.status));
    await aktualisieren();
    if (naechstes) detailLaden(naechstes.id); else { gewaehlt = null; $("#detail").innerHTML = '<p class="leise">Alles erledigt.</p>'; }
  } catch (e) { alarm(e.message); }
}

function alarm(text) {
  const p = document.createElement("p");
  p.className = "meldung"; p.setAttribute("role", "alert"); p.textContent = text;
  $("#detail").prepend(p);
  setTimeout(() => p.remove(), 6000);
}

// ------------------------------------------------------------------ Hinzufügen

function hinzufuegenOeffnen(art) {
  modus = art;
  const nativ = !!(window.pywebview && window.pywebview.api);
  $("#hinzufuegen-titel").textContent = art === "ordner" ? "Ordner hinzufügen" : "Dateien hinzufügen";
  $("#bereich-pfad").hidden = art !== "ordner" && !nativ;
  $("#bereich-dateien").hidden = art === "ordner" || nativ;
  $("#knopf-durchsuchen").hidden = !nativ;
  $("#v-person").innerHTML = `<option value="">–</option>` + (stamm.personen || []).map((p) => `<option>${esc(p)}</option>`).join("");
  $("#dlg-hinzufuegen").showModal();
  if (nativ && art === "dateien") durchsuchen();
}

async function durchsuchen() {
  const api_ = window.pywebview.api;
  const pfade = modus === "ordner" ? await api_.ordner_waehlen() : await api_.dateien_waehlen();
  if (pfade && pfade.length) $("#pfad").value = pfade.join(" | ");
}

async function stapelStarten() {
  const vorgaben = { kategorie: $("#v-kategorie").value.trim(), person: $("#v-person").value,
    tags: $("#v-tags").value.split(",").map((t) => t.trim().replace(/^#/, "").toLowerCase()).filter(Boolean) };
  try {
    if (!$("#bereich-dateien").hidden) {
      const fd = new FormData();
      [...$("#dateien").files].forEach((f) => fd.append("dateien", f));
      fd.append("vorgaben", JSON.stringify(vorgaben));
      await api("/api/hochladen", { method: "POST", body: fd });
    } else {
      const pfade = $("#pfad").value.split("|").map((p) => p.trim().replace(/^"|"$/g, "")).filter(Boolean);
      await api("/api/stapel", { method: "POST", json: { pfade, vorgaben } });
    }
    $("#dlg-hinzufuegen").close();
    $("#pfad").value = ""; $("#dateien").value = "";
    filter = "alle";
    aktualisieren();
  } catch (e) { alert(e.message); }
}

// ------------------------------------------------------------------ Einstellungen

async function einstellungenOeffnen() {
  const e = await api("/api/einstellungen");
  const form = $("#form-einstellungen");
  for (const [k, v] of Object.entries(e)) {
    const el = form.elements[k];
    if (!el) continue;
    if (el.type === "checkbox") el.checked = !!v; else el.value = v ?? "";
  }
  $("#test-ergebnis").textContent = "";
  $("#dlg-einstellungen").showModal();
}

function einstellungenDaten() {
  const form = $("#form-einstellungen"), daten = {};
  [...form.elements].forEach((el) => { if (el.name) daten[el.name] = el.type === "checkbox" ? el.checked : el.value; });
  return daten;
}

async function verbindungTesten() {
  $("#test-ergebnis").textContent = "Teste …";
  await api("/api/einstellungen", { method: "POST", json: einstellungenDaten() });
  const t = await api("/api/verbindung-testen", { method: "POST" });
  const zeilen = [
    t.server.erreichbar ? `✓ Server erreichbar${t.server.paperless === false ? ", aber Paperless nicht" : ", Paperless verbunden"}` : `✗ Server: ${t.server.fehler}`,
    t.ki.erreichbar ? (t.ki.modell_vorhanden ? "✓ Ollama und Modell bereit" : `✗ Modell fehlt – in der Eingabeaufforderung: ollama pull ${einstellungenDaten().modell}`) : "✗ Ollama nicht erreichbar – läuft Ollama?",
    t.tesseract ? "✓ Tesseract gefunden" : "✗ Tesseract nicht gefunden – Scans ohne Textlayer können nicht gelesen werden",
  ];
  $("#liste-modelle").innerHTML = (t.ki.modelle || []).map((m) => `<option value="${esc(m)}">`).join("");
  $("#test-ergebnis").textContent = zeilen.join("\n");
}

// ------------------------------------------------------------------ Stammdaten

async function stammdatenLaden() {
  try { stamm = await api("/api/stammdaten"); } catch { return; }
  $("#liste-kategorien").innerHTML = (stamm.kategorien || []).map((k) => `<option value="${esc(k)}">`).join("");
  $("#liste-absender").innerHTML = (stamm.korrespondenten || []).map((k) => `<option value="${esc(k)}">`).join("");
}

// ------------------------------------------------------------------ Ereignisse

document.addEventListener("click", async (ev) => {
  const ziel = ev.target.closest("button, tr.zeile-dok");
  if (!ziel) return;
  if (ziel.matches("tr.zeile-dok")) { detailLaden(Number(ziel.dataset.id)); return; }
  if (ziel.dataset.filter) { filter = ziel.dataset.filter; filterZeichnen(); tabelleZeichnen(); return; }
  if (ziel.hasAttribute("data-schliessen")) { ziel.closest("dialog").close(); return; }
  try {
    switch (ziel.id) {
      case "knopf-ordner": hinzufuegenOeffnen("ordner"); break;
      case "knopf-dateien": hinzufuegenOeffnen("dateien"); break;
      case "knopf-durchsuchen": durchsuchen(); break;
      case "knopf-einstellungen": case "link-einstellungen": einstellungenOeffnen(); break;
      case "knopf-testen": verbindungTesten(); break;
      case "knopf-pause": await api("/api/pause", { method: "POST", json: { an: !zustand.pausiert } }); aktualisieren(); break;
      case "knopf-alle": await api("/api/alle-sicheren", { method: "POST" }); aktualisieren(); break;
      case "knopf-aufraeumen": await api("/api/aufraeumen", { method: "POST" }); aktualisieren(); break;
      case "knopf-ungeprueft": freigeben(false); break;
      case "knopf-speichern": await speichern(); detailZeichnen(); aktualisieren(); break;
      case "knopf-erneut": await api(`/api/dokument/${gewaehlt}/erneut`, { method: "POST" }); aktualisieren(); detailLaden(gewaehlt); break;
      case "knopf-entfernen": await api(`/api/dokument/${gewaehlt}/entfernen`, { method: "POST" }); gewaehlt = null;
        $("#detail").innerHTML = '<p class="leise">Dokument in der Liste auswählen.</p>'; aktualisieren(); break;
      case "knopf-vorschau": {
        const f = $("#vorschau");
        f.hidden = !f.hidden;
        if (!f.hidden && !f.src) f.src = `/datei/${gewaehlt}?t=${encodeURIComponent(TOKEN)}`;
        break;
      }
    }
  } catch (e) { alarm(e.message); }
});

document.addEventListener("keydown", (ev) => {
  if (ev.key === "Enter" && ev.target.matches("tr.zeile-dok")) detailLaden(Number(ev.target.dataset.id));
});
$("#form-hinzufuegen").addEventListener("submit", (ev) => { ev.preventDefault(); stapelStarten(); });
$("#form-einstellungen").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  await api("/api/einstellungen", { method: "POST", json: einstellungenDaten() });
  $("#dlg-einstellungen").close();
  stammdatenLaden(); aktualisieren();
});
$("#nachtmodus").addEventListener("change", (ev) => api("/api/einstellungen", { method: "POST", json: { nachtmodus: ev.target.checked } }));

stammdatenLaden();
aktualisieren();
setInterval(aktualisieren, 2500);
setInterval(stammdatenLaden, 120000);
