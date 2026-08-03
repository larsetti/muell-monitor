# Setup-Anleitung: Täglicher Tracker auf Windows

## Schritt 1 — Python installieren (falls noch nicht vorhanden)
1. https://www.python.org/downloads/ → "Download Python 3.x"
2. Bei der Installation: **"Add Python to PATH"** anhaken!
3. Danach im Startmenü "cmd" öffnen und tippen: `python --version`
   → sollte "Python 3.x.x" ausgeben

## Schritt 2 — Git installieren (falls noch nicht vorhanden)
1. https://git-scm.com/download/win → herunterladen und installieren
2. Alles auf Standard lassen
3. Nach Installation: `git --version` in cmd testen

## Schritt 3 — Repo lokal klonen
Im Startmenü "cmd" öffnen:
```
cd C:\Users\DEINNAME\Documents
git clone https://github.com/larsetti/OA-Plus muell-monitor
cd muell-monitor
pip install requests
```

## Schritt 4 — Git für automatischen Push einrichten
Damit git pushen kann ohne jedes Mal nach Passwort zu fragen:
```
git config --global user.email "deine@email.de"
git config --global user.name "Lars"
```
Dann einmal manuell pushen um Credentials zu speichern:
```
git push
```
→ GitHub fragt nach Benutzername + Token (nicht Passwort!)
→ Token erstellen: github.com → Settings → Developer settings → Personal access tokens → Tokens (classic) → Generate new token → Haken bei "repo" → Generate → Token kopieren

## Schritt 5 — run_tracker.bat einrichten
1. Die Datei `run_tracker.bat` ins Repo-Verzeichnis kopieren
2. `C:\Users\DEINNAME\Documents\muell-monitor\run_tracker.bat` öffnen
3. Die Zeile `set REPO=C:\Users\%USERNAME%\Documents\muell-monitor` prüfen
   → Pfad ggf. anpassen falls Repo woanders liegt
4. Doppelklick auf run_tracker.bat → sollte einmal manuell funktionieren

## Schritt 6 — Windows Aufgabenplaner einrichten
1. Startmenü → "Aufgabenplanung" suchen und öffnen
2. Rechts: "Einfache Aufgabe erstellen..."
3. Name: "Müll-Hotspot Tracker"
4. Trigger: "Täglich"
5. Uhrzeit: 08:00:00 (oder wann der Rechner läuft)
6. Aktion: "Programm starten"
7. Programm/Skript: `C:\Users\DEINNAME\Documents\muell-monitor\run_tracker.bat`
8. "Fertig stellen"

## Schritt 7 — GitHub Actions deaktivieren (nicht mehr nötig)
Da der Tracker jetzt lokal läuft:
GitHub → Repo → Actions → daily_update.yml → rechts "..." → "Disable workflow"

## Testen
Doppelklick auf run_tracker.bat — ein schwarzes Fenster öffnet sich,
läuft durch und schließt sich. Danach auf der Website prüfen ob
neue Daten erscheinen (Strg+Shift+R).

## Falls etwas nicht klappt
- Schwarzes Fenster öffnet sich kurz und schließt sich sofort:
  → run_tracker.bat mit Rechtsklick → "Bearbeiten" → am Ende vor der letzten Zeile `pause` hinzufügen
  → So bleibt die Fehlermeldung sichtbar
