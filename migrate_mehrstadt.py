#!/usr/bin/env python3
"""
Mehrstadt-Migration (Schema-Vorstufe zu T-49, 15.08.2026)
==========================================================
Bereitet den Bestand darauf vor, dass neben Berlin noch Koeln und Bonn in
dieselbe Datenbank schreiben. Fuehrt KEINEN Import durch und stellt keine
Netzverbindung her — sie raeumt nur das Schema.

Zwei Schritte:

  1. Stadt-Spalten und Indizes anlegen (uebernimmt tracker.init_db,
     retention.init_schema und sperrliste.ensure_table; alles nach dem
     bestehenden Muster CREATE TABLE IF NOT EXISTS plus ALTER TABLE in
     try/except, ohne Migrationsverwaltung).
  2. Jede vorhandene meldungen.id auf `berlin:<alte-id>` umstellen.

Warum Schritt 2 sein muss: meldungen.id war TEXT PRIMARY KEY ohne Stadtanteil.
Berlin vergibt numerische Kennungen (1066349), Koelns Open311 vergibt
service_request_id, ebenfalls numerisch. Der Insert in tracker.py arbeitet mit
ON CONFLICT ... DO UPDATE — eine Kollision haette also nicht gescheitert,
sondern still eine fremde Meldung ueberschrieben.

Der Lauf ist idempotent: bereits praefixierte Kennungen werden uebersprungen.
Erkannt wird das am Trennzeichen, das in keiner der bekannten Quellkennungen
vorkommt (alle 82.780 Berliner Kennungen sind rein numerisch, geprueft am
15.08.2026).

Aufruf:
    python migrate_mehrstadt.py               Trockenlauf, schreibt nichts
    python migrate_mehrstadt.py --apply       fuehrt die Umstellung aus
    python migrate_mehrstadt.py --apply --db pfad/zur/andere.db

ACHTUNG HEIM-SERVER: Auf dem Raspberry Pi laeuft eine EIGENE Datenbank. Diese
Migration muss dort ausgefuehrt werden, BEVOR ein Stand mit den Aenderungen vom
15.08.2026 deployed wird. Ohne sie schreibt der neue tracker.py praefixierte
Kennungen in einen Bestand mit unpraefixierten — jede Berliner Meldung waere
danach doppelt vorhanden, und der Quellabgleich wuerde die alte Haelfte als aus
der Quelle verschwunden vormerken und 30 Tage spaeter loeschen.
"""

import argparse
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import retention  # noqa: E402
import sperrliste  # noqa: E402
import tracker  # noqa: E402

DB_PATH = Path(__file__).parent / "ordnungsamt.db"

STADT_ALTBESTAND = "berlin"


def spalten(conn: sqlite3.Connection, tabelle: str) -> list[str]:
    return [r[1] for r in conn.execute(f"PRAGMA table_info({tabelle})")]


def zaehle(conn: sqlite3.Connection) -> dict:
    """Ist-Zahlen, die vor und nach der Migration gleich sein muessen."""
    q = lambda s: conn.execute(s).fetchone()
    werte = {
        "meldungen": q("SELECT COUNT(*) FROM meldungen")[0],
        "hotspots": q("SELECT COUNT(*) FROM hotspots")[0],
        "sperrliste": q("SELECT COUNT(*) FROM sperrliste")[0],
        "fetch_log": q("SELECT COUNT(*) FROM fetch_log")[0],
    }
    werte["datum_min"], werte["datum_max"] = q("SELECT MIN(datum), MAX(datum) FROM meldungen")
    werte["id_verschieden"] = q("SELECT COUNT(DISTINCT id) FROM meldungen")[0]
    return werte


def schema_anlegen(conn: sqlite3.Connection):
    """Alle Stadt-Spalten und Indizes. Idempotent."""
    tracker.init_db(conn)
    retention.init_schema(conn)
    sperrliste.ensure_table(conn)


def offene_ids(conn: sqlite3.Connection) -> int:
    """Wieviele Kennungen tragen noch kein Stadt-Praefix."""
    return conn.execute(
        "SELECT COUNT(*) FROM meldungen WHERE instr(id, ?) = 0",
        (tracker.STADT_TRENNER,)
    ).fetchone()[0]


def ids_praefixieren(conn: sqlite3.Connection, stadt: str = STADT_ALTBESTAND,
                     dry_run: bool = True) -> int:
    """Setzt `<stadt>:` vor jede noch unpraefixierte Kennung.

    Der Filter `instr(id, ':') = 0` macht den Lauf idempotent: ein zweiter
    Durchgang findet nichts mehr und schreibt nichts.
    """
    betroffen = offene_ids(conn)
    if dry_run or not betroffen:
        return betroffen

    # Kollisionsprobe vor dem Schreiben. Sie kann heute nicht anschlagen (die
    # Zieltabelle hat keine praefixierten Kennungen), aber ein zweiter Lauf mit
    # abweichender Stadt wuerde sonst am PRIMARY KEY scheitern und die
    # Datenbank halb umgestellt zuruecklassen.
    kollision = conn.execute(
        "SELECT COUNT(*) FROM meldungen a WHERE instr(a.id, ?) = 0 AND EXISTS ("
        "  SELECT 1 FROM meldungen b WHERE b.id = ? || a.id)",
        (tracker.STADT_TRENNER, stadt + tracker.STADT_TRENNER)
    ).fetchone()[0]
    if kollision:
        raise RuntimeError(
            f"{kollision} Kennung(en) wuerden auf eine bereits vorhandene "
            f"praefixierte Kennung treffen. Nichts geschrieben. Ursache pruefen, "
            f"bevor erneut migriert wird."
        )

    conn.execute(
        "UPDATE meldungen SET id = ? || id WHERE instr(id, ?) = 0",
        (stadt + tracker.STADT_TRENNER, tracker.STADT_TRENNER)
    )
    conn.commit()
    return betroffen


def stadt_nachtragen(conn: sqlite3.Connection, stadt: str = STADT_ALTBESTAND,
                     dry_run: bool = True) -> dict:
    """Setzt die Stadt auf allen Altzeilen, bei denen sie leer geblieben ist.

    Der Spalten-Vorgabewert erledigt das eigentlich schon. Diese Funktion faengt
    den Fall ab, dass jemand die Spalte von Hand oder mit einem aelteren Skript
    ohne Wert gefuellt hat.
    """
    ergebnis = {}
    for tabelle in ("meldungen", "hotspots", "fetch_log", "sperrliste"):
        anzahl = conn.execute(
            f"SELECT COUNT(*) FROM {tabelle} WHERE stadt IS NULL OR stadt = ''"
        ).fetchone()[0]
        ergebnis[tabelle] = anzahl
        if not dry_run and anzahl:
            conn.execute(
                f"UPDATE {tabelle} SET stadt = ? WHERE stadt IS NULL OR stadt = ''",
                (stadt,))
    if not dry_run:
        conn.commit()
    return ergebnis


def main():
    p = argparse.ArgumentParser(
        description="Mehrstadt-Migration: Stadt-Spalten und ID-Praefix (T-49)")
    p.add_argument("--apply", action="store_true",
                   help="Umstellung ausfuehren; ohne diesen Schalter Trockenlauf")
    p.add_argument("--db", default=str(DB_PATH), help="Pfad zur Datenbank")
    p.add_argument("--stadt", default=STADT_ALTBESTAND,
                   help="Stadt, der der vorhandene Bestand zugeordnet wird")
    args = p.parse_args()

    dry_run = not args.apply
    conn = sqlite3.connect(args.db)

    print(f"Datenbank: {args.db}")
    print(f"Modus:     {'TROCKENLAUF (kein Schreiben)' if dry_run else 'ANWENDEN'}")
    print()

    vorher = zaehle(conn)
    print("Ist-Zahlen vorher:")
    for k, v in vorher.items():
        print(f"  {k:16} {v}")
    print()

    print("Schritt 1: Schema")
    fehlend_vorher = {t: ("stadt" not in spalten(conn, t))
                      for t in ("meldungen", "hotspots", "fetch_log", "sperrliste")}
    if dry_run:
        for t, fehlt in fehlend_vorher.items():
            print(f"  {t:16} {'stadt fehlt, wuerde angelegt' if fehlt else 'stadt vorhanden'}")
    else:
        schema_anlegen(conn)
        for t in fehlend_vorher:
            print(f"  {t:16} stadt {'angelegt' if fehlend_vorher[t] else 'bereits vorhanden'}")
    print()

    print("Schritt 2: Stadt auf Altzeilen nachtragen")
    if dry_run and any(fehlend_vorher.values()):
        print("  uebersprungen im Trockenlauf, solange die Spalten noch fehlen")
    else:
        leer = stadt_nachtragen(conn, args.stadt, dry_run=dry_run)
        for t, n in leer.items():
            print(f"  {t:16} {n} Zeile(n) ohne Stadt"
                  f"{'' if dry_run else ' -> ' + args.stadt}")
    print()

    print("Schritt 3: ID-Praefix")
    offen = offene_ids(conn)
    print(f"  {offen} von {vorher['meldungen']} Kennung(en) ohne Praefix")
    if offen == 0:
        print("  nichts zu tun, die Datenbank ist bereits umgestellt")
    elif dry_run:
        beispiel = conn.execute(
            "SELECT id FROM meldungen WHERE instr(id, ?) = 0 LIMIT 3",
            (tracker.STADT_TRENNER,)).fetchall()
        for (alt,) in beispiel:
            print(f"    {alt}  ->  {args.stadt}{tracker.STADT_TRENNER}{alt}")
    else:
        geaendert = ids_praefixieren(conn, args.stadt, dry_run=False)
        print(f"  {geaendert} Kennung(en) umgestellt")
    print()

    nachher = zaehle(conn)
    print("Ist-Zahlen nachher:")
    abweichung = []
    for k, v in nachher.items():
        gleich = vorher[k] == v
        print(f"  {k:16} {v}  {'' if gleich else f'ABWEICHUNG, vorher {vorher[k]}'}")
        if not gleich:
            abweichung.append(k)
    conn.close()

    if abweichung:
        print()
        print(f"FEHLER: {', '.join(abweichung)} weicht ab. Die Migration darf "
              f"nichts verlieren. Sicherung zurueckspielen und Ursache pruefen.")
        return 1
    print()
    print("Alle Ist-Zahlen unveraendert.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
