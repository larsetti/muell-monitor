#!/bin/bash
# ==============================================
# Müll-Monitor: Raspberry Pi Setup
# ==============================================
# Dieses Script auf dem Pi ausführen:
#   curl -sL <raw-url> | bash
# oder manuell: bash pi_setup.sh
# ==============================================

set -e

REPO_URL="https://github.com/larsetti/OA-Plus.git"
INSTALL_DIR="$HOME/muell-monitor"
VENV_DIR="$INSTALL_DIR/.venv"

echo "=== Müll-Monitor Pi Setup ==="

# 1. System-Pakete
echo "[1/5] System-Pakete installieren..."
sudo apt-get update -qq
sudo apt-get install -y -qq python3 python3-venv git

# 2. Repo klonen
if [ -d "$INSTALL_DIR" ]; then
    echo "[2/5] Repo existiert bereits, aktualisiere..."
    cd "$INSTALL_DIR" && git pull
else
    echo "[2/5] Repo klonen..."
    git clone "$REPO_URL" "$INSTALL_DIR"
fi
cd "$INSTALL_DIR"

# 3. Python venv + requests
echo "[3/5] Python-Umgebung einrichten..."
python3 -m venv "$VENV_DIR"
"$VENV_DIR/bin/pip" install -q requests

# 4. Git-Config fuer automatische Commits + Hook-Pfad
echo "[4/5] Git konfigurieren..."
cd "$INSTALL_DIR"
git config user.email "pi@muell-monitor.de"
git config user.name "Müll-Monitor Pi"
# pre-commit hook: verhindert, dass *.db-Dateien commitet werden
chmod +x "$INSTALL_DIR/scripts/pre-commit-no-db.sh"
git config core.hooksPath scripts

# 5. Cronjob einrichten (taeglich 06:00 Uhr)
echo "[5/5] Cronjob einrichten..."
# Die Schritte sind bewusst mit ; statt && verkettet (A-11, 29.07.2026).
# Mit && brach die Kette ab, sobald ein Schritt einen Fehlercode lieferte -
# und genau dann muss gepusht werden: export_html.py meldet Code 2, wenn
# maintenance.html fehlte und die eingebaute Ersatz-Wartungsseite geschrieben
# wurde. Waere der Push davon abhaengig, bliebe eine alte Live-Seite mit
# Ortsdaten oeffentlich stehen, obwohl sie lokal schon ersetzt ist.
# Ebenso soll ein fehlgeschlagener Abruf (tracker.py Code 1 bei leerem Feed)
# den Export nicht verhindern: der Wartungs-Lock haengt nicht am Abruf, und
# das Frontend zeigt ohnehin nur den letzten ERFOLGREICHEN Abruf als Stand.
CRON_CMD="0 6 * * * cd $INSTALL_DIR && { $VENV_DIR/bin/python tracker.py; $VENV_DIR/bin/python export_html.py; git add index.html; git diff --staged --quiet || git commit -m \"Auto-Update: \$(date '+\%Y-\%m-\%d')\"; git push; } >> $INSTALL_DIR/cron.log 2>&1"

# Bestehenden Cronjob ersetzen falls vorhanden
(crontab -l 2>/dev/null | grep -v "muell-monitor\|tracker.py"; echo "$CRON_CMD") | crontab -

echo ""
echo "=== Setup fertig! ==="
echo "Repo:     $INSTALL_DIR"
echo "Cronjob:  Taeglich 06:00 Uhr"
echo ""
echo "WICHTIG: Git Push braucht Authentifizierung."
echo "Entweder SSH-Key oder Token einrichten:"
echo "  git remote set-url origin git@github.com:larsetti/OA-Plus.git"
echo "  oder: gh auth login"
echo ""
echo "Testen mit: cd $INSTALL_DIR && $VENV_DIR/bin/python tracker.py"
