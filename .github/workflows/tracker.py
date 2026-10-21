#!/usr/bin/env python3
"""
Wohnungs-Tracker für inberlinwohnen.de

Prüft die neuesten Angebote der landeseigenen Wohnungsbaugesellschaften,
filtert nach deinen Kriterien (config.toml) und schickt dir neue Treffer
per E-Mail und/oder Push-Nachricht (ntfy).

Aufruf:
    python tracker.py              # normaler Lauf
    python tracker.py --test-mail  # schickt eine Test-Benachrichtigung
    python tracker.py --dry-run    # zeigt Treffer an, ohne zu senden oder zu speichern
"""
from __future__ import annotations

import argparse
import json
import os
import re
import smtplib
import sys
import time
import tomllib
from dataclasses import dataclass, field, asdict
from datetime import date, datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from html import escape
from pathlib import Path

import requests
from bs4 import BeautifulSoup

BASE_URL = "https://www.inberlinwohnen.de/wohnungsfinder"
HERE = Path(__file__).resolve().parent
CONFIG_FILE = HERE / "config.toml"
STATE_FILE = HERE / "gesehen.json"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (privater Wohnungs-Tracker; eine Anfrage alle 10 Min.)",
    "Accept-Language": "de-DE,de;q=0.9",
}
GESELLSCHAFTEN = {
    "berlinovo": "berlinovo",
    "degewo": "degewo",
    "gesobau": "GESOBAU",
    "gewobag": "Gewobag",
    "howoge": "HOWOGE",
    "stadtundland": "STADT UND LAND",
    "wbm": "WBM",
}


# ───────────────────────────── Datenmodell ─────────────────────────────

@dataclass
class Wohnung:
    id: str
    titel: str
    adresse: str
    plz: str
    bezirk: str
    zimmer: float | None
    flaeche: float | None
    kaltmiete: float | None
    warmmiete: float | None
    wbs: str
    etage: int | None
    bezugsfertig: str
    eingestellt: str
    gesellschaft: str
    link: str
    merkmale: list[str] = field(default_factory=list)
    lat: float | None = None
    lon: float | None = None
    hinweise: list[str] = field(default_factory=list)  # z. B. "⭐ Wunschkiez", "befristet bis …"


def zahl(text: str | None) -> float | None:
    """'1.149,97 €' -> 1149.97"""
    if not text:
        return None
    m = re.search(r"\d[\d.]*(?:,\d+)?", text)
    if not m:
        return None
    wert = float(m.group(0).replace(".", "").replace(",", "."))
    return wert or None  # "0,00 €" heißt auf der Seite: unbekannt


def sauber(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


# ───────────────────────────── Auslesen ─────────────────────────────

def parse_seite(html: str) -> list[Wohnung]:
    soup = BeautifulSoup(html, "html.parser")
    wohnungen = []
    for box in soup.select('[x-ref^="apartment-"]'):
        wid = box["x-ref"].removeprefix("apartment-")

        felder = {}
        for dt in box.find_all("dt"):
            dd = dt.find_next_sibling("dd")
            if dd is not None:
                felder[sauber(dt.get_text()).rstrip(":")] = sauber(dd.get_text(" "))

        titel_el = box.select_one("span.text-xl")
        link_el = box.select_one('a[title^="Zum Expose"]') or box.find("a", string=re.compile("Alle Details"))

        gesellschaft = "?"
        for img in box.find_all("img"):
            src = (img.get("src") or "").lower()
            for key, name in GESELLSCHAFTEN.items():
                if key in src.rsplit("/", 1)[-1]:
                    gesellschaft = name

        adresse = felder.get("Adresse", "")
        plz_m = re.search(r"\b(\d{5})\b", adresse)
        bezirk = adresse.rsplit(",", 1)[-1].strip() if "," in adresse else ""

        etage_m = re.match(r"\s*(-?\d+)", felder.get("Etage", ""))

        lat = lon = None
        karte = box.find("button", attrs={"aria-label": "Auf der Karte anzeigen"})
        if karte is not None:
            m = re.search(r'"lat"\s*:\s*"?([\d.]+)"?\s*,\s*"lon"\s*:\s*"?([\d.]+)', karte.get("wire:click", ""))
            if m:
                lat, lon = float(m.group(1)), float(m.group(2))

        merkmale_box = box.select_one("div.grid.text-sm")
        merkmale = [sauber(s.get_text()) for s in merkmale_box.find_all("span")] if merkmale_box else []

        wohnungen.append(Wohnung(
            id=wid,
            titel=sauber(titel_el.get_text()) if titel_el else "",
            adresse=adresse,
            plz=plz_m.group(1) if plz_m else "",
            bezirk=bezirk,
            zimmer=zahl(felder.get("Zimmeranzahl")),
            flaeche=zahl(felder.get("Wohnfläche")),
            kaltmiete=zahl(felder.get("Kaltmiete")),
            warmmiete=zahl(felder.get("Gesamtmiete")),
            wbs=felder.get("WBS", ""),
            etage=int(etage_m.group(1)) if etage_m else None,
            bezugsfertig=felder.get("Bezugsfertig ab", ""),
            eingestellt=felder.get("Eingestellt am", ""),
            gesellschaft=gesellschaft,
            link=link_el["href"] if link_el and link_el.get("href") else BASE_URL,
            merkmale=merkmale,
            lat=lat,
            lon=lon,
        ))
    return wohnungen


class SeiteNichtErreichbar(Exception):
    pass


def _hole_seite(s: requests.Session, seite: int) -> str:
    """Eine Seite abrufen, bei Netzwerk- oder Serverfehlern bis zu 3 Versuche."""
    letzter_fehler = None
    for versuch in range(3):
        try:
            r = s.get(BASE_URL, params={"page": seite}, timeout=30)
            r.raise_for_status()
            return r.text
        except requests.RequestException as e:
            letzter_fehler = e
            print(f"⚠️  Seite {seite}, Versuch {versuch + 1}: {e}")
            time.sleep(5 * (versuch + 1))
    raise SeiteNichtErreichbar(str(letzter_fehler))


def hole_wohnungen(seiten: int) -> list[Wohnung]:
    alle: list[Wohnung] = []
    with requests.Session() as s:
        s.headers.update(HEADERS)
        for seite in range(1, seiten + 1):
            try:
                html = _hole_seite(s, seite)
            except SeiteNichtErreichbar:
                if seite == 1:
                    raise
                break  # mit dem weitermachen, was wir schon haben
            treffer = parse_seite(html)
            if not treffer:
                break
            alle.extend(treffer)
            time.sleep(1)  # freundlich zur Seite sein
    return alle


# ───────────────────────────── Filtern ─────────────────────────────

# Die 27 Stationen der Ringbahn (S41/S42) im Uhrzeigersinn, Koordinaten aus Wikidata.
RINGBAHN = [
    ("Gesundbrunnen", 52.54861, 13.38944), ("Schönhauser Allee", 52.54933, 13.41371),
    ("Prenzlauer Allee", 52.54478, 13.42612), ("Greifswalder Straße", 52.53972, 13.43944),
    ("Landsberger Allee", 52.5295, 13.4548), ("Storkower Straße", 52.5238, 13.4644),
    ("Frankfurter Allee", 52.51417, 13.47472), ("Ostkreuz", 52.5031, 13.4689),
    ("Treptower Park", 52.4938, 13.4618), ("Sonnenallee", 52.473, 13.4558),
    ("Neukölln", 52.46917, 13.44167), ("Hermannstraße", 52.4675, 13.43111),
    ("Tempelhof", 52.4708, 13.3854), ("Südkreuz", 52.4756, 13.3644),
    ("Schöneberg", 52.4794, 13.3517), ("Innsbrucker Platz", 52.4786, 13.3439),
    ("Bundesplatz", 52.47766, 13.32853), ("Heidelberger Platz", 52.47967, 13.31244),
    ("Hohenzollerndamm", 52.4885, 13.3004), ("Halensee", 52.4961, 13.2906),
    ("Westkreuz", 52.5011, 13.2839), ("Messe Nord/ICC", 52.5078, 13.2836),
    ("Westend", 52.5181, 13.2844), ("Jungfernheide", 52.53033, 13.30083),
    ("Beusselstraße", 52.53444, 13.32917), ("Westhafen", 52.536, 13.344),
    ("Wedding", 52.5425, 13.3661),
]
_M_PRO_GRAD_LAT = 111_320
_M_PRO_GRAD_LON = 111_320 * 0.6088  # cos(52,5°)


def _xy(lat: float, lon: float) -> tuple[float, float]:
    return lon * _M_PRO_GRAD_LON, lat * _M_PRO_GRAD_LAT


def ring_lage(lat: float, lon: float) -> tuple[bool, float]:
    """(innerhalb?, Abstand zur Ringbahn-Linie in Metern)"""
    px, py = _xy(lat, lon)
    pts = [_xy(a, b) for _, a, b in RINGBAHN]
    innen, abstand = False, float("inf")
    for (x1, y1), (x2, y2) in zip(pts, pts[1:] + pts[:1]):
        if (y1 > py) != (y2 > py) and px < (x2 - x1) * (py - y1) / (y2 - y1) + x1:
            innen = not innen
        dx, dy = x2 - x1, y2 - y1
        t = max(0.0, min(1.0, ((px - x1) * dx + (py - y1) * dy) / (dx * dx + dy * dy)))
        abstand = min(abstand, ((px - x1 - t * dx) ** 2 + (py - y1 - t * dy) ** 2) ** 0.5)
    return innen, abstand


def naechste_station(lat: float, lon: float) -> str:
    px, py = _xy(lat, lon)
    return min(RINGBAHN, key=lambda s: (lambda x, y: (x - px) ** 2 + (y - py) ** 2)(*_xy(s[1], s[2])))[0]


MONATE = {"jan": 1, "feb": 2, "mär": 3, "maer": 3, "mar": 3, "apr": 4, "mai": 5, "jun": 6,
          "jul": 7, "aug": 8, "sep": 9, "okt": 10, "nov": 11, "dez": 12}


def befristung_bis(titel: str) -> date | None | bool:
    """Enddatum einer Befristung aus dem Titel. False = nicht befristet, None = befristet, Datum unbekannt."""
    t = titel.lower()
    if not re.search(r"befrist|zeitlich begrenzt|zwischenmiete|auf zeit", t):
        return False
    if m := re.search(r"bis\s*(?:zum\s*)?(\d{1,2})\.(\d{1,2})\.(\d{2,4})", t):
        j = int(m.group(3)); j += 2000 if j < 100 else 0
        return date(j, int(m.group(2)), int(m.group(1)))
    if m := re.search(r"bis\s*(?:ende\s*)?(\d{1,2})[./](\d{4})", t):
        return date(int(m.group(2)), int(m.group(1)), 28)
    if m := re.search(r"bis\s*(?:ende\s*)?([a-zä]{3})[a-zä]*\.?\s*(\d{4})", t):
        if m.group(1) in MONATE:
            return date(int(m.group(2)), MONATE[m.group(1)], 28)
    if m := re.search(r"bis\s*(?:ende\s*)?(20\d\d)\b", t):
        return date(int(m.group(1)), 12, 31)
    return None


def passt(w: Wohnung, k: dict) -> tuple[bool, str]:
    """Gibt (passt?, Grund) zurück. Fehlende Angaben gelten als 'ok', damit nichts verloren geht.
    Nebenbei werden Hinweise für die Benachrichtigung in w.hinweise gesammelt."""
    titel = w.titel.lower()
    w.hinweise = []

    # ── Lage ──
    if w.plz in k.get("ausschluss_plz", []):
        return False, f"PLZ {w.plz} ausgeschlossen"
    if w.plz in k.get("extra_plz", []):
        w.hinweise.append("⭐ Wunschkiez")
    elif w.lat is not None and w.lon is not None:
        innen, abstand = ring_lage(w.lat, w.lon)
        puffer = k.get("ring_puffer_m", 500)
        if not innen and abstand > puffer:
            return False, f"{abstand / 1000:.1f} km außerhalb des Rings"
        # Bis ~250 m ist innen/außen wegen der geraden Linien zwischen den Stationen nicht exakt.
        if abstand <= 250:
            lage = f"direkt am Ring (S {naechste_station(w.lat, w.lon)})"
        elif innen:
            lage = "im Ring"
        else:
            lage = f"{abstand:.0f} m außerhalb, nahe S {naechste_station(w.lat, w.lon)}"
        w.hinweise.append(("⭐ " if w.bezirk in k.get("wunsch_bezirke", []) else "") + lage)
    elif k.get("ohne_koordinaten_melden", True) is False:
        return False, "keine Koordinaten"
    else:
        w.hinweise.append("Lage unbekannt – bitte prüfen")

    # ── Befristung ──
    bis = befristung_bis(w.titel)
    if bis is None:
        w.hinweise.append("⚠️ befristet (Datum unklar)")
    elif bis:
        grenze = date.fromisoformat(k.get("befristet_mindestens_bis", "2000-01-01"))
        if bis < grenze:
            return False, f"befristet nur bis {bis:%m/%Y}"
        w.hinweise.append(f"⚠️ befristet bis {bis:%d.%m.%Y}")

    # ── Wohnung ──
    if w.flaeche is not None and w.flaeche < k.get("min_flaeche_m2", 0):
        return False, f"nur {w.flaeche:g} m²"
    if w.zimmer is not None and w.zimmer < k.get("min_zimmer", 0):
        return False, f"nur {w.zimmer:g} Zimmer"
    if w.warmmiete is not None and w.warmmiete > k.get("max_warmmiete", 10**9):
        return False, f"{w.warmmiete:.0f} € warm zu teuer"
    if not k.get("erdgeschoss_ok", True) and w.etage == 0:
        return False, "Erdgeschoss"
    if "erforderlich" in w.wbs.lower() and "nicht" not in w.wbs.lower() and not k.get("habe_wbs"):
        return False, "WBS erforderlich"
    # "WBS 100/140" → [100, 140]; passt, wenn irgendeine Stufe ≥ deiner ist (z. B. "WBS 160/180/220")
    stufen = [int(x) for x in re.findall(r"\b(\d{3})\b", " ".join(re.findall(r"wbs[\s\d/,und-]*", titel)))]
    if stufen and max(stufen) < k.get("wbs_stufe", 0):
        return False, f"nur für WBS {'/'.join(map(str, stufen))}"
    for wort in k.get("ausschluss_woerter", []):
        if wort.lower() in titel:
            return False, f"Ausschlusswort „{wort}“"
    return True, "passt"


# ───────────────────────────── Merken ─────────────────────────────

def lade_gesehen() -> dict[str, str]:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    return {}


def speichere_gesehen(gesehen: dict[str, str]) -> None:
    grenze = (date.today() - timedelta(days=90)).isoformat()
    gesehen = {k: v for k, v in gesehen.items() if v >= grenze}
    STATE_FILE.write_text(json.dumps(gesehen, indent=1, sort_keys=True), encoding="utf-8")


# ───────────────────────────── Benachrichtigen ─────────────────────────────

def euro(x: float | None) -> str:
    return "?" if x is None else f"{x:,.0f} €".replace(",", ".")


def kurzinfo(w: Wohnung) -> str:
    zi = f"{w.zimmer:g}" if w.zimmer else "?"
    m2 = f"{w.flaeche:g}" if w.flaeche else "?"
    etage = "EG" if w.etage == 0 else (f"{w.etage}. OG" if w.etage is not None else "Etage ?")
    return f"{zi} Zi · {m2} m² · {euro(w.warmmiete)} warm · {etage} · {w.gesellschaft}"


def baue_mail(neue: list[Wohnung]) -> tuple[str, str, str]:
    if len(neue) == 1:
        betreff = f"🏠 Neue Wohnung: {neue[0].adresse.split(',')[0]} – {euro(neue[0].warmmiete)} warm"
    else:
        betreff = f"🏠 {len(neue)} neue Wohnungen bei inberlinwohnen"

    text = "\n\n".join(f"{w.titel}\n{w.adresse}\n{kurzinfo(w)}\n{' · '.join(w.hinweise)}\nWBS: {w.wbs}\n{w.link}"
                       for w in neue)

    karten = []
    for w in neue:
        merkmale = ", ".join(w.merkmale[:6])
        karten.append(f"""
        <div style="border:1px solid #ddd;border-radius:10px;padding:14px 16px;margin:0 0 14px">
          <div style="font-size:16px;font-weight:600;margin-bottom:4px">{escape(w.adresse)}</div>
          <div style="color:#555;margin-bottom:8px">{escape(w.titel)}</div>
          {f'<div style="font-size:13px;margin-bottom:6px">{escape(" · ".join(w.hinweise))}</div>' if w.hinweise else ''}
          <div style="font-size:15px;margin-bottom:6px"><b>{escape(kurzinfo(w))}</b></div>
          <div style="color:#555;font-size:13px">WBS: {escape(w.wbs)} · Bezugsfertig ab {escape(w.bezugsfertig)} · Kalt {euro(w.kaltmiete)}</div>
          {f'<div style="color:#555;font-size:13px">{escape(merkmale)}</div>' if merkmale else ''}
          <a href="{escape(w.link)}" style="display:inline-block;margin-top:10px;background:#1d4ed8;color:#fff;
             text-decoration:none;padding:9px 16px;border-radius:7px;font-weight:600">Exposé öffnen &amp; bewerben →</a>
        </div>""")
    html = f"""<div style="font-family:-apple-system,Segoe UI,Roboto,sans-serif;max-width:560px">
      <p style="margin:0 0 14px">Neue passende Wohnung{'en' if len(neue) > 1 else ''} – schnell sein lohnt sich:</p>
      {''.join(karten)}
      <p style="color:#888;font-size:12px">Gefunden am {datetime.now():%d.%m.%Y um %H:%M}. Kriterien änderst du in config.toml.</p>
    </div>"""
    return betreff, text, html


def sende_mail(an: str, betreff: str, text: str, html: str) -> None:
    user = os.environ.get("GMAIL_USER")
    pw = os.environ.get("GMAIL_APP_PASSWORD")
    if not user or not pw:
        print("⚠️  GMAIL_USER / GMAIL_APP_PASSWORD fehlen – keine E-Mail gesendet.")
        return
    msg = MIMEMultipart("alternative")
    msg["Subject"], msg["From"], msg["To"] = betreff, f"Wohnungs-Tracker <{user}>", an
    msg.attach(MIMEText(text, "plain", "utf-8"))
    msg.attach(MIMEText(html, "html", "utf-8"))
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as smtp:
        smtp.login(user, pw.replace(" ", ""))
        smtp.send_message(msg)
    print(f"✉️  E-Mail an {an} gesendet.")


def sende_push(thema: str, neue: list[Wohnung]) -> set[str]:
    """Schickt bis zu 8 Nachrichten; gibt die IDs zurück, die erfolgreich gemeldet wurden."""
    ok: set[str] = set()
    for w in neue[:8]:
        for versuch in range(3):
            try:
                requests.post("https://ntfy.sh/", timeout=15, json={
                    "topic": thema,
                    "title": f"🏠 {w.adresse.split(',')[0]} – {euro(w.warmmiete)}",
                    "message": kurzinfo(w) + ("\n" + " · ".join(w.hinweise) if w.hinweise else ""),
                    "click": w.link,
                    "priority": 4,
                    "tags": ["house"],
                }).raise_for_status()
                ok.add(w.id)
                break
            except requests.RequestException as e:
                print(f"⚠️  Push für {w.adresse} fehlgeschlagen (Versuch {versuch + 1}): {e}")
                time.sleep(5 * (versuch + 1))
        time.sleep(1)
    print(f"📱 {len(ok)} Push-Nachricht(en) gesendet.")
    return ok


def benachrichtige(neue: list[Wohnung], b: dict) -> set[str]:
    """Gibt die IDs zurück, die über mindestens einen Weg gemeldet wurden."""
    an = os.environ.get("EMAIL_AN") or os.environ.get("GMAIL_USER")
    thema = os.environ.get("NTFY_THEMA") or b.get("ntfy_thema")
    if not an and not thema:
        raise SystemExit("❌ Kein Benachrichtigungsweg eingerichtet – Secret NTFY_THEMA fehlt.")
    gemeldet: set[str] = set()
    if an:
        try:
            sende_mail(an, *baue_mail(neue))
            if os.environ.get("GMAIL_APP_PASSWORD"):
                gemeldet |= {w.id for w in neue}
        except Exception as e:  # noqa: BLE001
            print(f"⚠️  E-Mail fehlgeschlagen: {e}")
    if thema:
        gemeldet |= sende_push(thema, neue)
    return gemeldet


# ───────────────────────────── Hauptprogramm ─────────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--test-mail", action="store_true")
    args = ap.parse_args()

    cfg = tomllib.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    k, b = cfg["suche"], cfg["benachrichtigung"]

    if args.test_mail:
        beispiel = Wohnung("test", "Testnachricht – so sehen Treffer aus", "Musterstraße 1, 10437, Pankow",
                           "10437", "Pankow", 2, 52.5, 690, 910, "nicht erforderlich", 3,
                           "01.12.2026", date.today().strftime("%d.%m.%Y"), "WBM", BASE_URL,
                           ["Balkon / Loggia / Terrasse", "Einbauküche"])
        benachrichtige([beispiel], b)
        return 0

    try:
        wohnungen = hole_wohnungen(k.get("seiten", 5))
    except SeiteNichtErreichbar as e:
        # Kurze Störung bei inberlinwohnen.de – kein Fehler, der nächste Lauf versucht es wieder.
        print(f"⚠️  inberlinwohnen.de gerade nicht erreichbar ({e}). Nächster Lauf versucht es erneut.")
        return 0
    if not wohnungen:
        print("❌ Keine Wohnungen ausgelesen – hat sich die Seite geändert?")
        return 1

    gesehen = lade_gesehen()
    neue = []
    for w in wohnungen:
        ok, grund = passt(w, k)
        status = "NEU ✓" if ok and w.id not in gesehen else ("✓" if ok else "✗")
        print(f"{status:6} {w.adresse[:45]:45} {kurzinfo(w):55} {'' if ok else grund}")
        if ok and w.id not in gesehen:
            neue.append(w)

    print(f"\n{len(wohnungen)} Wohnungen geprüft, {len(neue)} neue Treffer.")
    if args.dry_run:
        return 0

    gemeldet = benachrichtige(neue, b) if neue else set()
    nicht_gemeldet = {w.id for w in neue} - gemeldet
    if nicht_gemeldet:
        print(f"⚠️  {len(nicht_gemeldet)} Treffer nicht gemeldet – werden beim nächsten Lauf erneut versucht.")
    heute = date.today().isoformat()
    for w in wohnungen:  # alle merken, damit spätere Kriterien-Änderungen nicht alte Wohnungen nachmelden
        if w.id not in nicht_gemeldet:
            gesehen.setdefault(w.id, heute)
    speichere_gesehen(gesehen)
    return 0


if __name__ == "__main__":
    sys.exit(main())
