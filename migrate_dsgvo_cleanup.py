#!/usr/bin/env python3
"""
DSGVO-Bereinigung der ordnungsamt.db (2026-05-25)
==================================================
Bereinigt die bestehende SQLite-DB rückwirkend:
  1. hausNummer in allen meldungen-Zeilen auf NULL setzen
  2. Nicht-Müll-Meldungen löschen (Lärm, Ruhestörung, Hund, Falschparker etc.)
  3. hotspots.meldungen_count nach dem Löschen konsistent halten
  4. raw_json-Spalte entfernen (verhindert versehentliche PII-Persistierung)

Flags:
  --dry-run       Zeigt SQL-Effekte ohne Schreiben (SELECT statt UPDATE/DELETE)
  --db PATH       Pfad zur DB (default: ./ordnungsamt.db)
  --with-samples  Zeigt bis zu 5 Beispiel-Zeilen aus Schritt 2 (nur für
                  Debug-Zwecke; enthält Freitext, Standard: aus)
  --no-backup     Kein automatisches Backup vor dem Schreiben (nicht empfohlen)

Idempotent: mehrfaches Ausführen ist sicher. Wenn die hausNummer-Spalte nicht
mehr existiert (frische DB nach Phase 2), wird Schritt 1 übersprungen.
Vor dem ersten Schreiben wird automatisch ein Backup unter
<db>.pre-migration-<timestamp>.bak angelegt (außer --no-backup ist gesetzt).
"""

import argparse
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path

# Ausschluss-Keywords — gespiegelt aus tracker.py NON_MUELL_KEYWORDS
NON_MUELL_KEYWORDS = [
    "lärm", "laerm",
    "ruhestörung", "ruhe störung", "ruhestoerung",
    "hund", "hundebesitzer", "hundekot",
    "falschpark", "falsch park", "falschparkend",
    "parkverstoß", "parkverstoss",
    "verkehrsdelikt", "verkehrsverstoß",
    "nachbar", "hausnachbar",
    "gaststätte",
]


def build_non_muell_where() -> tuple[str, list]:
    """LIKE-Bedingungen für Nicht-Müll-Kategorien über kategorie und betreff."""
    conditions = []
    params = []
    for kw in NON_MUELL_KEYWORDS:
        conditions.append("LOWER(kategorie) LIKE ?")
        params.append(f"%{kw}%")
        conditions.append("LOWER(betreff) LIKE ?")
        params.append(f"%{kw}%")
    return " OR ".join(conditions), params


def run(db_path: Path, dry_run: bool, with_samples: bool = False, no_backup: bool = False):
    print(f"Datenbank: {db_path}")
    print(f"Modus:     {'DRY-RUN (kein Schreiben)' if dry_run else 'LIVE'}")
    print()

    # M-03: Backup vor dem ersten Schreiben
    if not dry_run and not no_backup:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = db_path.parent / f"{db_path.name}.pre-migration-{ts}.bak"
        try:
            shutil.copy2(db_path, backup_path)
            print(f"Backup erstellt: {backup_path}")
        except Exception as e:
            print(f"WARNUNG: Backup fehlgeschlagen ({e}) — Abbruch. Mit --no-backup überspringen.")
            raise
        print()

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    # Einmalig Spalten-Inventar ermitteln — für idempotente Checks
    cols = [r[1] for r in conn.execute("PRAGMA table_info(meldungen)").fetchall()]
    has_hausnr = "hausNummer" in cols

    # ── Schritt 1: hausNummer leeren ─────────────────────────────────────────
    print(f"Schritt 1 — hausNummer leeren")
    if not has_hausnr:
        print(f"  übersprungen — hausNummer-Spalte nicht vorhanden (DB bereits migriert)")
    else:
        non_null_count = conn.execute(
            "SELECT COUNT(*) FROM meldungen WHERE hausNummer IS NOT NULL AND hausNummer != ''"
        ).fetchone()[0]
        print(f"  Betroffene Zeilen: {non_null_count}")

        if not dry_run:
            conn.execute("UPDATE meldungen SET hausNummer = NULL WHERE hausNummer IS NOT NULL AND hausNummer != ''")
            conn.commit()
            after = conn.execute(
                "SELECT COUNT(*) FROM meldungen WHERE hausNummer IS NOT NULL AND hausNummer != ''"
            ).fetchone()[0]
            print(f"  Ergebnis: {after} Zeilen mit nicht-leerem hausNummer (soll 0 sein)")
        else:
            print(f"  [DRY-RUN] UPDATE meldungen SET hausNummer = NULL WHERE hausNummer IS NOT NULL AND hausNummer != ''")
    print()

    # ── Schritt 2: Nicht-Müll-Meldungen löschen ──────────────────────────────
    where_clause, params = build_non_muell_where()

    non_muell_count = conn.execute(
        f"SELECT COUNT(*) FROM meldungen WHERE is_muell = 0 OR ({where_clause})",
        params
    ).fetchone()[0]

    print(f"Schritt 2 — Nicht-Müll-Meldungen löschen")
    print(f"  Betroffene Zeilen: {non_muell_count}")

    # Stichprobe: nur strukturelle Felder ohne Freitext (DSGVO-clean).
    # Freitext-Inhalte nur mit --with-samples anzeigen (Debug, nicht Standard).
    if non_muell_count > 0 and with_samples:
        samples = conn.execute(
            f"SELECT id, kategorie, betreff, bezirk FROM meldungen WHERE is_muell = 0 OR ({where_clause}) LIMIT 5",
            params
        ).fetchall()
        print("  Stichprobe (bis 5 Zeilen, --with-samples aktiv):")
        for row in samples:
            print(f"    id={row['id']} | kat={row['kategorie']!r} | betreff={row['betreff']!r} | bezirk={row['bezirk']!r}")
    elif non_muell_count > 0:
        samples = conn.execute(
            f"SELECT id, kategorie, bezirk FROM meldungen WHERE is_muell = 0 OR ({where_clause}) LIMIT 5",
            params
        ).fetchall()
        print("  Stichprobe (bis 5 Zeilen, kein Freitext — --with-samples für betreff):")
        for row in samples:
            print(f"    id={row['id']} | kat={row['kategorie']!r} | bezirk={row['bezirk']!r}")

    if not dry_run:
        conn.execute(
            f"DELETE FROM meldungen WHERE is_muell = 0 OR ({where_clause})",
            params
        )
        conn.commit()
        after = conn.execute(
            f"SELECT COUNT(*) FROM meldungen WHERE is_muell = 0 OR ({where_clause})",
            params
        ).fetchone()[0]
        print(f"  Ergebnis: {after} Nicht-Müll-Meldungen verblieben (soll 0 sein)")
    else:
        print(f"  [DRY-RUN] DELETE würde {non_muell_count} Zeilen entfernen")
    print()

    # ── Schritt 2b: hotspots.meldungen_count konsistent halten ───────────────
    # Nach dem Löschen von Meldungen können Hotspot-Zähler veraltet sein.
    # Neu aggregieren aus dem aktuellen meldungen-Stand.
    print(f"Schritt 2b — hotspots.meldungen_count aktualisieren")
    if not dry_run:
        # hotspots komplett leeren — tracker.run() baut sie beim nächsten Lauf
        # aus dem bereinigten meldungen-Stand neu auf. cluster_id ist eine
        # Python-Funktion und nicht als SQL-Aggregat nutzbar, daher kein
        # selektiver Abgleich möglich.
        conn.execute("DELETE FROM hotspots")
        conn.commit()
        hotspot_count = conn.execute("SELECT COUNT(*) FROM hotspots").fetchone()[0]
        print(f"  hotspots-Tabelle geleert ({hotspot_count} Einträge verbleiben, soll 0 sein)")
        print(f"  Hinweis: nächster tracker.run()-Lauf baut hotspots neu auf")
    else:
        stale_check = conn.execute("SELECT COUNT(*) FROM hotspots").fetchone()[0]
        print(f"  [DRY-RUN] hotspots-Tabelle hat aktuell {stale_check} Eintraege (wuerden bei --apply geleert)")
    print()

    # ── Schritt 3: VACUUM (Datei-Größe reduzieren) ────────────────────────────
    if not dry_run:
        size_before = db_path.stat().st_size
        print(f"Schritt 3 — VACUUM")
        print(f"  DB-Größe vor VACUUM: {size_before / 1024 / 1024:.1f} MB")
        conn.execute("VACUUM")
        conn.commit()
        size_after = db_path.stat().st_size
        print(f"  DB-Größe nach VACUUM: {size_after / 1024 / 1024:.1f} MB")
        print(f"  Ersparnis: {(size_before - size_after) / 1024 / 1024:.1f} MB")
    else:
        size_now = db_path.stat().st_size
        print(f"Schritt 3 — VACUUM (übersprungen im DRY-RUN)")
        print(f"  Aktuelle DB-Größe: {size_now / 1024 / 1024:.1f} MB")
    print()

    # ── Schritt 4: raw_json-Spalte entfernen ─────────────────────────────────
    # M-02: verhindert, dass künftiger Code versehentlich rohen API-JSON
    # (inkl. Hausnummern) in dieser Spalte persistiert.
    print(f"Schritt 4 — raw_json-Spalte entfernen")
    cols_now = [r[1] for r in conn.execute("PRAGMA table_info(meldungen)").fetchall()]
    if "raw_json" not in cols_now:
        print(f"  übersprungen — raw_json-Spalte nicht vorhanden")
    elif not dry_run:
        conn.execute("ALTER TABLE meldungen DROP COLUMN raw_json")
        conn.commit()
        cols_after = [r[1] for r in conn.execute("PRAGMA table_info(meldungen)").fetchall()]
        if "raw_json" not in cols_after:
            print(f"  raw_json-Spalte erfolgreich entfernt")
        else:
            print(f"  WARNUNG: raw_json-Spalte konnte nicht entfernt werden")
    else:
        print(f"  [DRY-RUN] ALTER TABLE meldungen DROP COLUMN raw_json")
    print()

    # ── Abschluss-Statistik ───────────────────────────────────────────────────
    total = conn.execute("SELECT COUNT(*) FROM meldungen").fetchone()[0]
    muell_count = conn.execute("SELECT COUNT(*) FROM meldungen WHERE is_muell = 1").fetchone()[0]
    print(f"Abschluss-Statistik:")
    print(f"  Gesamt-Meldungen in DB:   {total}")
    print(f"  is_muell=1:               {muell_count}")
    if has_hausnr:
        remaining_hausnr = conn.execute(
            "SELECT COUNT(*) FROM meldungen WHERE hausNummer IS NOT NULL AND hausNummer != ''"
        ).fetchone()[0]
        print(f"  Noch mit hausNummer != '': {remaining_hausnr}")
    else:
        print(f"  hausNummer-Spalte: nicht vorhanden (bereits migriert)")

    conn.close()
    print()
    if dry_run:
        print("DRY-RUN abgeschlossen. Keine Daten geändert.")
    else:
        print("Migration abgeschlossen.")


def main():
    parser = argparse.ArgumentParser(description="DSGVO-Bereinigung ordnungsamt.db")
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Zeigt Effekte ohne Schreiben"
    )
    parser.add_argument(
        "--db", default=str(Path(__file__).parent / "ordnungsamt.db"),
        help="Pfad zur DB (default: ./ordnungsamt.db)"
    )
    parser.add_argument(
        "--with-samples", action="store_true",
        help="Stichprobe mit Freitext (betreff) anzeigen — nur für Debug-Zwecke"
    )
    parser.add_argument(
        "--no-backup", action="store_true",
        help="Kein automatisches Backup vor dem Schreiben (nicht empfohlen)"
    )
    args = parser.parse_args()
    run(Path(args.db), dry_run=args.dry_run, with_samples=args.with_samples,
        no_backup=args.no_backup)


if __name__ == "__main__":
    main()
