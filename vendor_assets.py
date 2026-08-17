#!/usr/bin/env python3
"""
Fremde Ressourcen lokal ausliefern (Abhilfe A-12 der DSFA vom 28.07.2026)
=========================================================================
Holt Leaflet, Leaflet.markercluster und die Schriften einmalig nach
assets/vendor/ und schreibt eine lokale Schrift-Einbindung.

Hintergrund: bis hierher lud jede aufgerufene Seite Skripte und Stylesheets von
cdnjs.cloudflare.com und Schriften von fonts.googleapis.com/fonts.gstatic.com
nach. Beide Anbieter bekamen dadurch die IP-Adresse jedes Besuchers, ohne dass
das für den Zweck nötig gewesen wäre (Risiko R-9). Lokal ausgeliefert entfallen
sie als Empfänger, und die Datenschutzerklärung wird um zwei Einträge kürzer.

Nicht ersetzbar bleiben die Kartenkacheln von tile.openstreetmap.org. Sie
werden zur Laufzeit gebraucht und lassen sich nicht mitliefern; die
OpenStreetMap Foundation bleibt deshalb Empfänger.

Das Skript ist ein einmaliger Beschaffungsvorgang, kein Teil der täglichen
Pipeline. Erneut ausführen nur bei einem Versionswechsel — danach
`python sri.py --schreiben` und die Tests laufen lassen.
"""

import re
import shutil
from pathlib import Path

import requests

BASIS = Path(__file__).parent
VENDOR = BASIS / "assets" / "vendor"

LEAFLET = "1.9.4"
MARKERCLUSTER = "1.5.3"
CDN = "https://cdnjs.cloudflare.com/ajax/libs"

# Ein moderner Kennstring ist nötig, damit Google woff2 statt ttf ausliefert.
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

DATEIEN = [
    (f"{CDN}/leaflet/{LEAFLET}/leaflet.min.css", "leaflet/leaflet.min.css"),
    (f"{CDN}/leaflet/{LEAFLET}/leaflet.min.js", "leaflet/leaflet.min.js"),
    (f"{CDN}/leaflet/{LEAFLET}/images/layers.png", "leaflet/images/layers.png"),
    (f"{CDN}/leaflet/{LEAFLET}/images/layers-2x.png", "leaflet/images/layers-2x.png"),
    (f"{CDN}/leaflet/{LEAFLET}/images/marker-icon.png", "leaflet/images/marker-icon.png"),
    (f"{CDN}/leaflet/{LEAFLET}/images/marker-icon-2x.png", "leaflet/images/marker-icon-2x.png"),
    (f"{CDN}/leaflet/{LEAFLET}/images/marker-shadow.png", "leaflet/images/marker-shadow.png"),
    (f"{CDN}/leaflet.markercluster/{MARKERCLUSTER}/MarkerCluster.css",
     "markercluster/MarkerCluster.css"),
    (f"{CDN}/leaflet.markercluster/{MARKERCLUSTER}/MarkerCluster.Default.css",
     "markercluster/MarkerCluster.Default.css"),
    (f"{CDN}/leaflet.markercluster/{MARKERCLUSTER}/leaflet.markercluster.min.js",
     "markercluster/leaflet.markercluster.min.js"),
]

# Schriftfamilien, die die ausgelieferten Seiten verwenden.
SCHRIFTEN = {
    "source-sans-3": "https://fonts.googleapis.com/css2?family=Source+Sans+3:wght@300;400;600;700&display=swap",
    "montserrat": "https://fonts.googleapis.com/css2?family=Montserrat:wght@300;400;600&display=swap",
}
# Nur die für deutschsprachige Seiten nötigen Zeichensätze mitliefern.
SUBSETS = ("latin", "latin-ext")


def hole(url: str) -> bytes:
    antwort = requests.get(url, timeout=60, headers={"User-Agent": UA})
    antwort.raise_for_status()
    return antwort.content


def hole_bibliotheken():
    for url, ziel in DATEIEN:
        pfad = VENDOR / ziel
        pfad.parent.mkdir(parents=True, exist_ok=True)
        pfad.write_bytes(hole(url))
        print(f"  {ziel}  ({pfad.stat().st_size} Byte)")


def hole_schriften():
    """Lädt die woff2-Dateien und schreibt je Familie ein lokales Stylesheet.

    Source Sans 3 und Montserrat liegen bei Google als Variable Font vor: alle
    angefragten Schnitte verweisen auf dieselbe Datei. Sie wird deshalb je
    Zeichensatz nur einmal abgelegt und über eine Gewichts-Spanne eingebunden
    (font-weight: 300 700), statt sie viermal gleich zu speichern.
    """
    ziel_dir = VENDOR / "fonts"
    ziel_dir.mkdir(parents=True, exist_ok=True)

    for familie, url in SCHRIFTEN.items():
        css = hole(url).decode("utf-8")
        # Google kommentiert jeden Block mit dem Zeichensatz: /* latin */
        bloecke = re.split(r'/\*\s*([\w\-\[\]]+)\s*\*/', css)
        # je Zeichensatz: {quell_url: [gewichte], ...} plus ein Beispielblock
        gesammelt: dict[str, dict] = {}
        for i in range(1, len(bloecke) - 1, 2):
            subset, block = bloecke[i], bloecke[i + 1]
            if subset not in SUBSETS:
                continue
            treffer = re.search(r'url\((https://[^)]+\.woff2)\)', block)
            gewicht = re.search(r'font-weight:\s*(\d+)', block)
            if not treffer or not gewicht:
                continue
            eintrag = gesammelt.setdefault(subset, {"urls": {}, "block": block})
            eintrag["urls"].setdefault(treffer.group(1), []).append(int(gewicht.group(1)))

        ergebnis = []
        for subset, eintrag in gesammelt.items():
            for nr, (quelle, gewichte) in enumerate(eintrag["urls"].items()):
                suffix = f"-{nr}" if len(eintrag["urls"]) > 1 else ""
                name = f"{familie}-{subset}{suffix}.woff2"
                (ziel_dir / name).write_bytes(hole(quelle))
                spanne = (str(gewichte[0]) if len(set(gewichte)) == 1
                          else f"{min(gewichte)} {max(gewichte)}")
                block = re.sub(r'font-weight:\s*\d+', f'font-weight: {spanne}',
                               eintrag["block"])
                ergebnis.append(re.sub(r'url\(https://[^)]+\.woff2\)',
                                       f'url({name})', block).strip())
                print(f"  fonts/{name}  ({(ziel_dir / name).stat().st_size} Byte, "
                      f"Gewicht {spanne})")

        kopf = (f"/* {familie}: lokal ausgeliefert statt von fonts.gstatic.com\n"
                f"   nachgezogen mit vendor_assets.py (Abhilfe A-12 der DSFA) */\n")
        # newline="\n" ist Pflicht, kein Schoenheitsfehler (T-71, 17.08.2026).
        # Ohne die Angabe übersetzt write_text jedes \n in die Zeilenende-Art
        # des Betriebssystems, auf Windows also in \r\n. Die Datei sieht dann je
        # nach Rechner anders aus, auf dem sie beschafft wurde — und auf ihr
        # steht eine sha384-Prüfsumme. Genau daran ist es hängengeblieben: die
        # Prüfsumme war gegen die Windows-Fassung gesetzt, ausgeliefert wurde
        # die Fassung aus dem Speicher. Die Bibliotheken darueber sind nicht
        # betroffen, die schreibt hole_bibliotheken byteweise.
        (ziel_dir / f"{familie}.css").write_text(kopf + "\n".join(ergebnis) + "\n",
                                                 encoding="utf-8", newline="\n")
        print(f"  fonts/{familie}.css")


def main():
    # ignore_errors, weil OneDrive einzelne Verzeichnisse kurzzeitig sperrt.
    # Übrig gebliebene Dateien werden ohnehin überschrieben; Reste eines
    # Versionswechsels räumt man einmalig von Hand weg.
    if VENDOR.exists():
        shutil.rmtree(VENDOR, ignore_errors=True)
    VENDOR.mkdir(parents=True, exist_ok=True)
    print(f"Ziel: {VENDOR}")
    print("Bibliotheken:")
    hole_bibliotheken()
    print("Schriften:")
    hole_schriften()
    print("\nFertig. Danach ausfuehren:")
    print("  python sri.py --schreiben")
    print("  python -m pytest tests/ -q")


if __name__ == "__main__":
    main()
