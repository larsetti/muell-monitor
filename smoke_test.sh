#!/usr/bin/env bash
# Smoke-Test: Prüft die wichtigsten Invarianten nach einem Build
# Aufruf: bash smoke_test.sh
set -euo pipefail
cd "$(dirname "$0")"

ERRORS=0

# ── 1. export_html.py erzeugt index.html ohne Fehler ────────────────────
echo "[1] export_html.py ..."
python export_html.py 2>&1 | tail -2
if [ ! -f index.html ]; then
  echo "  FEHLER: index.html nicht erzeugt"
  ERRORS=$((ERRORS+1))
else
  SIZE=$(wc -c < index.html)
  echo "  OK: index.html erzeugt, ${SIZE} Bytes"
fi

# ── 2. Bekannte Bezirke in index.html vorhanden ──────────────────────────
echo "[2] Bezirks-Stichprobe ..."
for BEZIRK in "Neukölln" "Mitte" "Pankow"; do
  if grep -q "$BEZIRK" index.html; then
    echo "  OK: $BEZIRK gefunden"
  else
    echo "  FEHLER: $BEZIRK nicht in index.html"
    ERRORS=$((ERRORS+1))
  fi
done

# ── 3. C-01: Adressen in index.html vorhanden ───────────────────────────
echo "[3] C-01 Adressen ..."
ADDR_COUNT=$(grep -c "hc-addr" index.html || true)
if [ "$ADDR_COUNT" -ge 1 ]; then
  echo "  OK: hc-addr-Klasse gefunden (${ADDR_COUNT}x)"
else
  echo "  FEHLER: Keine Adress-Einträge in index.html"
  ERRORS=$((ERRORS+1))
fi

# ── 4. C-02: kategorie_gruppen im JSON vorhanden, kat_keys nicht ────────
echo "[4] C-02 Feldname kategorie_gruppen ..."
if grep -q '"kategorie_gruppen"' index.html; then
  echo "  OK: kategorie_gruppen vorhanden"
else
  echo "  FEHLER: kategorie_gruppen fehlt in index.html"
  ERRORS=$((ERRORS+1))
fi
if grep -q 'D\.kat_keys' index.html; then
  echo "  FEHLER: kat_keys noch im JS"
  ERRORS=$((ERRORS+1))
else
  echo "  OK: kat_keys nicht mehr im JS"
fi

# ── 5. C-04: XSS-Escape greift ──────────────────────────────────────────
echo "[5] C-04 Script-Injection-Schutz ..."
# esc()-Funktion vorhanden
if grep -q "function esc(" index.html; then
  echo "  OK: esc()-Funktion vorhanden"
else
  echo "  FEHLER: esc()-Funktion fehlt"
  ERRORS=$((ERRORS+1))
fi
# </script> darf im JSON-Block nur escaped vorkommen
# Prüfen: keine rohe </script> ausser dem echten schliessenden Tag
RAW_CLOSE=$(python -c "
import re
c = open('index.html', encoding='utf-8').read()
json_start = c.find('const D = ') + len('const D = ')
json_end = c.find('\n</script>', json_start)
block = c[json_start:json_end]
print(block.count('</script>'))
")
if [ "$RAW_CLOSE" -eq 0 ]; then
  echo "  OK: Kein rohes </script> im JSON-Block"
else
  echo "  WARNUNG: ${RAW_CLOSE} rohe </script>-Vorkommen im JSON-Block (sollten escaped sein)"
  ERRORS=$((ERRORS+1))
fi

# ── Ergebnis ─────────────────────────────────────────────────────────────
echo ""
if [ "$ERRORS" -eq 0 ]; then
  echo "Smoke-Test BESTANDEN (0 Fehler)"
  exit 0
else
  echo "Smoke-Test FEHLGESCHLAGEN: ${ERRORS} Fehler"
  exit 1
fi
