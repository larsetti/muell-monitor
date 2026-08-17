@echo off
:: ============================================================
:: Müll-Hotspot Monitor — Täglicher Tracker
:: Dieses Script täglich via Windows Aufgabenplaner ausführen
:: ============================================================

:: Pfad zu deinem Repo-Ordner — HIER ANPASSEN:
set REPO=C:\Users\%USERNAME%\Documents\muell-monitor

:: Ins Repo-Verzeichnis wechseln
cd /d "%REPO%"

:: Python-Tracker ausfuehren, EIN LAUF JE STADT (T-62, 15.08.2026).
:: Vorher stand hier nur "python tracker.py". Das laeuft Berlin, und Berlin
:: liefert seit dem 22.04.2026 nichts. Der Koeln-Adapter war damit vollstaendig
:: gebaut, getestet und im Betrieb nie aktiv. Jede Stadt wird jetzt ausdruecklich
:: genannt, auch Berlin - eine neue Stadt in quellen.py faellt sonst hier durch.
:: tests\test_launcher.py wird rot, wenn eine Stadt fehlt.
:: Sicherung VOR der Erfassung (T-64, 15.08.2026). Der taegliche Lauf loescht:
:: tracker.run ruft am Ende retention.anwenden auf, und die laeuft ueber JEDE
:: Stadt. Sobald Koeln taeglich laeuft, tickt damit auch Berlins Frist weiter --
:: und der Berliner Bestand ist nicht nachbeschaffbar, die Quelle liefert seit
:: dem 22.04.2026 nichts. Deshalb fail-closed: schlaegt die Sicherung fehl, wird
:: nicht erfasst. Der Export und der Push laufen trotzdem (A-11), sonst bliebe
:: eine alte Live-Seite mit Ortsdaten stehen.
:: --wenn-vorhanden: beim allerersten Lauf gibt es noch keine Datenbank, das ist
:: kein Fehler. Ohne den Zusatz koennte ein frischer Aufbau nie anfangen.
echo Sichere Datenbank...
python sicherung.py --wenn-vorhanden
if errorlevel 1 goto keine_erfassung

echo Starte Tracker Berlin...
python tracker.py --stadt berlin
echo Starte Tracker Koeln...
python tracker.py --stadt koeln
goto nach_erfassung

:keine_erfassung
echo.
echo ABBRUCH DER ERFASSUNG: Die Sicherung ist fehlgeschlagen.
echo Es wurde NICHT erfasst, damit die Loeschroutine nicht ohne Sicherung laeuft.
echo Grund steht oben und in tracker.log. Export und Push laufen weiter.
echo.

:nach_erfassung

:: HTML exportieren
:: WICHTIG (A-11): export_html.py liefert Exit-Code 2, wenn maintenance.html
:: fehlte und die eingebaute Ersatz-Wartungsseite geschrieben wurde. Der Push
:: unten darf davon NICHT abhaengig gemacht werden - die Ersatzseite ist die
:: Abhilfe. Wer hier auf Exit-Code 0 prueft, laesst eine alte Live-Seite mit
:: Ortsdaten oeffentlich stehen.
:: T-62: Ohne Zusatz baut export_html.py seit dem 15.08.2026 die Staedte-
:: Struktur (Startseite plus je eine Seite fuer berlin, koeln und die
:: Umlaut-Weiterleitung). Wer hier --eine-seite ergaenzt, baut wieder eine
:: einzelne Seite - genau davor warnt der Test.
echo Exportiere HTML...
python export_html.py

:: Git: Änderungen committen und pushen
:: Der Pfadfilter "*/index.html" nimmt berlin\, koeln\ und die Umlaut-
:: Weiterleitung koeln mit Umlaut mit, ohne dass ein Umlaut in dieser
:: Batch-Datei stehen muss - der waere je nach Codepage der Konsole nicht mehr
:: derselbe Ordnername. Git wertet das Muster selbst aus, cmd.exe fasst es
:: nicht an. _vorschau\ ist gitignoriert und faellt dabei weg.
echo Pushe auf GitHub...
git add index.html "*/index.html"
git diff --staged --quiet || git commit -m "Auto-Update: %date:~6,4%-%date:~3,2%-%date:~0,2%"
git push

echo Fertig!
