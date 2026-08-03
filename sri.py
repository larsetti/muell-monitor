#!/usr/bin/env python3
"""
Prüfsummen für eingebundene Ressourcen (Teil von Abhilfe A-12)
===============================================================
Subresource Integrity: jedes eingebundene Skript und Stylesheet trägt eine
sha384-Prüfsumme. Weicht die Datei davon ab, lädt der Browser sie nicht.

Seit dem 29.07.2026 liegen alle diese Dateien lokal unter assets/vendor/ —
Cloudflare und Google sind als Empfänger entfallen. Die Prüfsummen bleiben
trotzdem sinnvoll: sie machen eine Veränderung an den mitgelieferten Dateien
sichtbar, statt sie stillschweigend zu veröffentlichen.

Damit die Prüfsummen nicht veralten, ruft export_html.render_live() vor jedem
Live-Render pruefe_html() auf und bricht bei Abweichung hart ab.

Aufruf:
    python sri.py                 prüft template.html und maintenance.html
    python sri.py --schreiben     trägt fehlende/veraltete Prüfsummen ein
"""

import argparse
import base64
import hashlib
import re
from pathlib import Path

BASIS = Path(__file__).parent
SEITEN = ["template.html", "maintenance.html", "index.html"]

# <script src="..."> und <link rel="stylesheet" href="...">, jeweils mit
# optionalem integrity-Attribut in beliebiger Reihenfolge.
_TAG = re.compile(r'<(script|link)\b([^>]*)>', re.IGNORECASE)
_ATTR = re.compile(r'(\w[\w-]*)\s*=\s*"([^"]*)"')


def berechne(pfad: Path) -> str:
    """sha384-Prüfsumme im SRI-Format."""
    digest = hashlib.sha384(Path(pfad).read_bytes()).digest()
    return "sha384-" + base64.b64encode(digest).decode("ascii")


def _referenzen(html: str):
    """Liefert (tagname, attribut-dict, quelle) für alle eingebundenen Dateien."""
    for treffer in _TAG.finditer(html):
        tag = treffer.group(1).lower()
        attrs = dict(_ATTR.findall(treffer.group(2)))
        quelle = attrs.get("src") if tag == "script" else attrs.get("href")
        if not quelle:
            continue
        if tag == "link" and attrs.get("rel", "").lower() != "stylesheet":
            continue
        yield tag, attrs, quelle


def _lokal(quelle: str, basis: Path) -> Path | None:
    if quelle.startswith(("http://", "https://", "//", "data:", "mailto:")):
        return None
    pfad = basis / quelle.split("?")[0].split("#")[0]
    return pfad if pfad.is_file() else None


def pruefe_html(html: str, basis: Path) -> list[str]:
    """Meldet jede integrity-Angabe, die nicht zur Datei passt."""
    fehler = []
    for tag, attrs, quelle in _referenzen(html):
        angegeben = attrs.get("integrity")
        if not angegeben:
            continue
        pfad = _lokal(quelle, basis)
        if pfad is None:
            fehler.append(f"{quelle}: Datei nicht auffindbar, Pruefsumme nicht pruefbar")
            continue
        tatsaechlich = berechne(pfad)
        if angegeben.strip() != tatsaechlich:
            fehler.append(f"{quelle}: Pruefsumme passt nicht "
                          f"(angegeben {angegeben[:24]}..., berechnet {tatsaechlich[:24]}...)")
    return fehler


def skripte_ohne_integritaet(html: str) -> list[str]:
    """Eingebundene Skripte und Stylesheets ohne Prüfsumme."""
    return [quelle for tag, attrs, quelle in _referenzen(html)
            if not attrs.get("integrity") and not quelle.startswith("data:")]


def schreibe(html: str, basis: Path) -> tuple[str, int]:
    """Trägt für jede lokale Einbindung die aktuelle Prüfsumme ein."""
    geaendert = 0

    def ersetze(treffer):
        nonlocal geaendert
        tag, roh = treffer.group(1), treffer.group(2)
        attrs = dict(_ATTR.findall(roh))
        quelle = attrs.get("src") if tag.lower() == "script" else attrs.get("href")
        if not quelle or (tag.lower() == "link" and attrs.get("rel", "").lower() != "stylesheet"):
            return treffer.group(0)
        pfad = _lokal(quelle, basis)
        if pfad is None:
            return treffer.group(0)
        summe = berechne(pfad)
        if attrs.get("integrity") == summe and "crossorigin" in attrs:
            return treffer.group(0)
        geaendert += 1
        neu = re.sub(r'\s+integrity\s*=\s*"[^"]*"', '', roh)
        neu = re.sub(r'\s+crossorigin\s*=\s*"[^"]*"', '', neu)
        neu = neu.rstrip().rstrip("/")
        schluss = "/>" if tag.lower() == "link" else ">"
        return f'<{tag}{neu} integrity="{summe}" crossorigin="anonymous"{schluss}'

    return _TAG.sub(ersetze, html), geaendert


def main():
    p = argparse.ArgumentParser(description="SRI-Pruefsummen pruefen oder setzen")
    p.add_argument("--schreiben", action="store_true",
                   help="fehlende oder veraltete Pruefsummen eintragen")
    args = p.parse_args()

    problematisch = 0
    for name in SEITEN:
        pfad = BASIS / name
        if not pfad.is_file():
            continue
        html = pfad.read_text(encoding="utf-8")
        if args.schreiben:
            neu, n = schreibe(html, BASIS)
            if n:
                pfad.write_text(neu, encoding="utf-8")
            print(f"{name}: {n} Pruefsumme(n) gesetzt")
        else:
            fehler = pruefe_html(html, BASIS)
            ohne = skripte_ohne_integritaet(html)
            problematisch += len(fehler) + len(ohne)
            print(f"{name}: {len(fehler)} Abweichung(en), {len(ohne)} ohne Pruefsumme")
            for f in fehler:
                print(f"   FEHLER  {f}")
            for o in ohne:
                print(f"   OFFEN   {o}")
    if not args.schreiben:
        print("\nAlles in Ordnung." if not problematisch else
              f"\n{problematisch} Punkt(e) zu klaeren.")


if __name__ == "__main__":
    main()
