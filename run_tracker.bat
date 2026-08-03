@echo off
:: ============================================================
:: Müll-Hotspot Monitor — Täglicher Tracker
:: Dieses Script täglich via Windows Aufgabenplaner ausführen
:: ============================================================

:: Pfad zu deinem Repo-Ordner — HIER ANPASSEN:
set REPO=C:\Users\%USERNAME%\Documents\muell-monitor

:: Ins Repo-Verzeichnis wechseln
cd /d "%REPO%"

:: Python-Tracker ausführen
echo Starte Tracker...
python tracker.py

:: HTML exportieren
:: WICHTIG (A-11): export_html.py liefert Exit-Code 2, wenn maintenance.html
:: fehlte und die eingebaute Ersatz-Wartungsseite geschrieben wurde. Der Push
:: unten darf davon NICHT abhaengig gemacht werden - die Ersatzseite ist die
:: Abhilfe. Wer hier auf Exit-Code 0 prueft, laesst eine alte Live-Seite mit
:: Ortsdaten oeffentlich stehen.
echo Exportiere HTML...
python export_html.py

:: Git: Änderungen committen und pushen
echo Pushe auf GitHub...
git add index.html
git diff --staged --quiet || git commit -m "Auto-Update: %date:~6,4%-%date:~3,2%-%date:~0,2%"
git push

echo Fertig!
