"""Tests mit echten Daten von inberlinwohnen.de (Stand 20.10.2026)."""
import json
import re
import sys
import tomllib
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import tracker  # noqa: E402

K = tomllib.loads((HERE.parent / "config.toml").read_text(encoding="utf-8"))["suche"]


def test_parser_liest_echtes_html():
    html = (HERE / "beispiel_seite.html").read_text(encoding="utf-8")
    [w] = tracker.parse_seite(html)
    assert w.id == "23172"
    assert w.adresse == "Neuköllnische Allee 147, 12057, Neukölln"
    assert w.plz == "12057" and w.bezirk == "Neukölln"
    assert w.zimmer == 2.0 and w.flaeche == 66.11
    assert w.kaltmiete == 493.74 and w.warmmiete == 747.74
    assert w.wbs == "erforderlich" and w.etage == 0
    assert w.gesellschaft == "STADT UND LAND"
    assert w.link == "https://stadtundland.de/wohnungssuche/1001%2F0191%2F00044"
    assert (w.lat, w.lon) == (52.46070124, 13.47431298)
    assert "Balkon / Loggia / Terrasse" in w.merkmale


def test_ringbahn():
    assert tracker.ring_lage(52.5163, 13.3777)[0]          # Brandenburger Tor: innen
    assert tracker.ring_lage(52.5409, 13.4122)[0]          # Kollwitzkiez: innen
    assert tracker.ring_lage(52.4910, 13.4240)[0]          # Hermannplatz: innen
    assert not tracker.ring_lage(52.4569, 13.4471)[0]      # Britz: außen
    assert not tracker.ring_lage(52.5794, 13.4281)[0]      # Pankow-Heinersdorf: außen
    innen, abstand = tracker.ring_lage(52.4930, 13.2880)   # Karlsruher Str., Halensee: knapp außen
    assert abstand < 500


def test_befristung():
    assert tracker.befristung_bis("2-Zimmer-Wohnung in Mitte") is False
    assert tracker.befristung_bis("Befristete Vermietung bis 28.02.2028") == date(2028, 2, 28)
    assert tracker.befristung_bis("befristet bis 03/2027") == date(2027, 3, 28)
    assert tracker.befristung_bis("Zeitmietvertrag befristet bis Juli 2027") == date(2027, 7, 28)
    assert tracker.befristung_bis("befristet bis Ende 2026") == date(2026, 12, 31)
    assert tracker.befristung_bis("befristeter Mietvertrag") is None


def test_wbs_stufen():
    w = lambda t: tracker.Wohnung("x", t, "A 1, 10437, Pankow", "10437", "Pankow", 2, 50, None, 800,
                                  "erforderlich", 2, "", "", "", "", [], 52.5409, 13.4122)
    assert tracker.passt(w("WBS 160/180/220 für 2 Räume"), K)[0]
    assert tracker.passt(w("WBS 140/160 erforderlich"), K)[0]
    assert not tracker.passt(w("nur mit WBS 140 für 2-Zimmer über 50m²!"), K)[0]
    assert not tracker.passt(w("Nur mit WBS 100 - Barrierefrei"), K)[0]


def test_echte_angebote_vom_20_10_2026():
    treffer = []
    for t, a, zi, m2, warm, wbs, et, lat, lon in json.loads((HERE / "echte_angebote_2026-10-20.json").read_text("utf-8")):
        e = re.match(r"\s*(-?\d+)", et)
        w = tracker.Wohnung("x", t, a, re.search(r"\d{5}", a).group(), a.rsplit(", ", 1)[1], tracker.zahl(zi),
                            tracker.zahl(m2), None, tracker.zahl(warm), wbs, int(e.group(1)) if e else None,
                            "", "", "", "", [], lat, lon)
        if tracker.passt(w, K)[0]:
            treffer.append(a.split(",")[0])
    assert treffer == ["Dossestraße 15", "Dossestraße 23", "Dominicusstraße 39", "Heidelberger Straße 79B"]


def test_mail_baut_sich():
    w = tracker.Wohnung("1", "Befristete Vermietung bis 28.02.2028", "Dominicusstraße 39, 10827, Tempelhof-Schöneberg",
                        "10827", "Tempelhof-Schöneberg", 1, 44.93, 498, 676.41, "nicht erforderlich", 3, "", "",
                        "degewo", "https://www.degewo.de/", [], 52.48094855, 13.35093388)
    assert tracker.passt(w, K)[0]
    betreff, text, html = tracker.baue_mail([w])
    assert "Dominicusstraße 39" in betreff and "befristet bis 28.02.2028" in html and "⭐" in html
