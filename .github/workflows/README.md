# 🏠 Wohnungs-Tracker für inberlinwohnen.de

Prüft regelmäßig die neuesten Angebote von degewo, Gewobag, HOWOGE, WBM, STADT UND LAND,
GESOBAU und berlinovo – und schickt dir **sofort eine Push-Nachricht aufs Handy**, wenn eine
Wohnung zu deinen Kriterien passt. Ein Tipp auf die Nachricht öffnet direkt das Exposé.
Läuft kostenlos bei GitHub, dein Laptop muss dafür nicht an sein.

## Wann wird geprüft?
Alle 10 Minuten, täglich von ca. 6 bis 24 Uhr. Das Repository ist öffentlich, damit GitHub das
unbegrenzt kostenlos ausführt. Sichtbar sind Code und `config.toml` – deine Secrets (ntfy-Name,
Passwörter) sieht niemand.

## Einrichtung
1. **ntfy-App** (iPhone/Android): **+** → geheimen, schwer zu ratenden Namen ausdenken
   (z. B. `filja-wohnung-` + ein paar zufällige Zeichen) → **Subscribe**.
   Den Namen niemandem verraten – wer ihn kennt, kann mitlesen.
2. Im Repository: **Settings → Secrets and variables → Actions → New repository secret**
   - Name: `NTFY_THEMA` · Wert: dein geheimer Name aus Schritt 1
3. **Actions → Wohnungs-Tracker → Run workflow** → Haken bei *„Nur eine Test-Nachricht schicken“*
   → **Run workflow**. Nach ca. 30 Sekunden klingelt dein Handy.

Danach läuft alles automatisch. Beim ersten echten Lauf bekommst du alle *aktuell* passenden
Wohnungen auf einmal (höchstens 5 Nachrichten), danach nur noch neue.

## Wie funktioniert das?
- **Wer prüft?** Ein Rechner von GitHub startet nach Zeitplan dieses Programm, liest die
  50 neuesten Angebote und vergleicht sie mit deinen Kriterien. Bereits gemeldete Wohnungen
  merkt es sich in `gesehen.json`.
- **Wer schickt die Nachricht?** Der kostenlose Dienst ntfy.sh an deine App.
- **Wie schnell?** Die Nachricht ist Sekunden nach dem Lauf da. Wartezeit entsteht nur durch den
  Takt oben – und GitHub startet geplante Läufe manchmal 5–20 Minuten zu spät.
- **Lage:** Jede Wohnung hat auf inberlinwohnen.de genaue Koordinaten. Geprüft wird, ob sie
  innerhalb der Ringbahn liegt (Linie durch alle 27 Ringbahnhöfe) oder höchstens 500 m außerhalb.
  Friedenau wird zusätzlich gemeldet, Wedding/Gesundbrunnen nicht. Lieblingsbezirke bekommen ⭐.
- **Befristete Wohnungen** kommen, wenn sie laut Titel mindestens bis Juli 2027 laufen oder kein
  Enddatum dabeisteht (dann mit ⚠️). Steht die Befristung nur im Exposé, sieht der Tracker sie nicht.

## Kriterien ändern
Alles steht in **`config.toml`** (auf GitHub: Datei anklicken → ✏️ → ändern → *Commit changes*):
- `min_flaeche_m2`, `min_zimmer`, `max_warmmiete`, `erdgeschoss_ok`
- `habe_wbs`, `wbs_stufe` – Wohnungen „nur mit WBS 140“ werden bei WBS 160 aussortiert
- `befristet_mindestens_bis`
- `ring_puffer_m`, `wunsch_bezirke` (⭐), `extra_plz` (immer melden), `ausschluss_plz` (nie melden)
- `ausschluss_woerter` – z. B. Senioren-, Rollstuhl- oder Studentenwohnungen

## Pausieren
**Actions → Wohnungs-Tracker → ⋯ → Disable workflow.** Wieder einschalten genauso.

## Optional: zusätzlich E-Mail
Gmail-App-Passwort unter <https://myaccount.google.com/apppasswords> erstellen und drei weitere
Secrets anlegen: `GMAIL_USER` (deine Gmail-Adresse), `GMAIL_APP_PASSWORD`, `EMAIL_AN`.

## Gut zu wissen
- Pro Lauf werden nur 5 Seiten abgerufen, mit Pausen dazwischen – kaum mehr, als wenn du selbst
  nachschaust. Bitte den Takt nicht weiter erhöhen.
- Wenn inberlinwohnen.de umgebaut wird, findet der Tracker keine Wohnungen mehr. Dann schlägt der
  Lauf fehl und GitHub schickt dir automatisch eine Mail.
