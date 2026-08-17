#!/usr/bin/env python3
"""
Sperrliste (Abhilfe A-7 der DSFA vom 28.07.2026)
=================================================
Technische Umsetzung des Widerspruchsrechts nach Art. 21 DSGVO. Bis hierher
war der Widerspruch nur organisatorisch vorgesehen: es gab keinen Weg, eine
einzelne Ortszelle dauerhaft von der Veroeffentlichung auszunehmen.

Eine gesperrte Zelle wird zweifach ausgeschlossen:
  - tracker.py legt sie gar nicht erst als Hotspot an und entfernt eine
    bereits vorhandene Zeile beim naechsten Lauf (die Sperre wirkt also
    rueckwirkend, nicht nur nach vorn),
  - export_html.py filtert sie zusaetzlich beim Rendern heraus.

Die doppelte Pruefung ist Absicht: eine Sperre darf nicht daran scheitern,
dass ein Schritt der Kette uebersprungen wurde.

Die Sperre wird doppelt gehalten: in der Datenbank und in der Datei
sperrliste.txt daneben. Grund: ordnungsamt.db ist gitignored und liegt seit
Mai 2026 nicht mehr im Repository. Auf einem frisch aufgesetzten Rechner
entsteht die Datenbank neu — eine nur dort gefuehrte Sperre waere damit
verloren, ohne dass es jemandem auffiele. Beim Laden wird die Datei in die
Datenbank zurueckgespielt, beim Eintragen werden beide geschrieben. Was von
beiden ueberlebt, stellt die Sperre wieder her.

sperrliste.txt gehoert ebenfalls NICHT ins Repository und ist gitignored: eine
oeffentliche Liste ausgenommener Zellen liesse sich gegen die Karte halten und
wuerde gerade das offenlegen, was die Sperre verbergen soll. Die Datei muss
deshalb bei einem Rechnerwechsel von Hand mitgenommen werden.

WICHTIG zum Feld `grund`: dort gehoert KEIN Name, keine Anschrift und kein
Mailtext hinein, sondern nur ein Aktenzeichen des Vorgangs. Sonst wandert
ueber die mitgelieferte Datenbank ein Personenbezug in den Bestand, den die
Sperre gerade verhindern soll.

Aufruf:
    python sperrliste.py --list
    python sperrliste.py --add 52.50000_13.40000 --quelle W-2026-001
    python sperrliste.py --add-koordinate 52.5013 13.4021 --quelle W-2026-002
    python sperrliste.py --remove 52.50000_13.40000
"""

import argparse
import re
import sqlite3
from datetime import datetime
from pathlib import Path

DB_PATH = Path(__file__).parent / "ordnungsamt.db"
SPERRLISTE_DATEI = Path(__file__).parent / "sperrliste.txt"

# Format wie in tracker.cluster_id erzeugt: fuenf Nachkommastellen, per _ getrennt
CLUSTER_ID_MUSTER = re.compile(r'^-?\d+\.\d{5}_-?\d+\.\d{5}$')

DATEI_KOPF = """# Sperrliste nach Art. 21 DSGVO (Abhilfe A-7 der DSFA vom 28.07.2026)
# Eine Zell-Kennung je Zeile, alles ab # ist Kommentar.
# Diese Datei ist die ausfallsichere Zweitschrift zur Tabelle in der Datenbank
# und wird bei jedem Lauf zurueckgespielt. Sie gehoert NICHT ins Repository
# und muss bei einem Rechnerwechsel von Hand mitgenommen werden.
# Kein Name, keine Anschrift, kein Mailtext — nur das Aktenzeichen des Vorgangs.
"""


def ensure_table(conn: sqlite3.Connection):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS sperrliste (
            cluster_id TEXT PRIMARY KEY,
            grund      TEXT NOT NULL DEFAULT '',
            quelle     TEXT NOT NULL DEFAULT '',
            erfasst_am TEXT NOT NULL,
            stadt      TEXT NOT NULL DEFAULT 'berlin'
        )
    """)
    try:
        conn.execute("ALTER TABLE sperrliste ADD COLUMN stadt "
                     "TEXT NOT NULL DEFAULT 'berlin'")
    except sqlite3.OperationalError:
        pass
    conn.commit()


def cluster_id_fuer(lat: float, lon: float) -> str:
    """Rasterzelle zu einer Koordinate — fuer die Kommandozeile, damit aus einem
    Widerspruch mit Adressangabe die richtige Zelle wird."""
    import tracker
    return tracker.cluster_id(float(lat), float(lon))


def pruefe_cluster_id(cluster_id: str) -> str:
    cid = (cluster_id or "").strip()
    if not CLUSTER_ID_MUSTER.match(cid):
        raise ValueError(
            f"'{cluster_id}' ist keine gueltige Zell-Kennung. Erwartet wird das "
            f"Format 52.50000_13.40000 (fuenf Nachkommastellen). Aus einer "
            f"Koordinate erzeugt --add-koordinate die passende Kennung."
        )
    return cid


def _datei_lesen(pfad: Path = None) -> dict[str, tuple[str, str]]:
    """Liest die Zweitschrift. Format je Zeile: <cluster_id> [# <quelle> | <grund>]"""
    pfad = pfad or SPERRLISTE_DATEI
    if not pfad.is_file():
        return {}
    eintraege = {}
    for zeile in pfad.read_text(encoding="utf-8").splitlines():
        kennung, _, rest = zeile.partition("#")
        kennung = kennung.strip()
        if not kennung:
            continue
        try:
            kennung = pruefe_cluster_id(kennung)
        except ValueError:
            continue
        quelle, _, grund = rest.partition("|")
        eintraege[kennung] = (grund.strip(), quelle.strip())
    return eintraege


def _datei_schreiben(conn: sqlite3.Connection, pfad: Path = None):
    pfad = pfad or SPERRLISTE_DATEI
    # Das Dateiformat bleibt bewusst unveraendert ohne Stadt (siehe laden()).
    zeilen = [f"{cid}  # {quelle or '-'} | {grund}"
              for cid, grund, quelle, _, _ in auflisten(conn)]
    pfad.write_text(DATEI_KOPF + "\n".join(zeilen) + "\n", encoding="utf-8")


def eintragen(conn: sqlite3.Connection, cluster_id: str, grund: str = "",
              quelle: str = "", erfasst_am: str = None, datei: Path = None,
              stadt: str = "berlin"):
    """Sperrt eine Zelle dauerhaft in Datenbank und Zweitschrift. Idempotent.

    `stadt` wird mitgefuehrt, damit sich ein Widerspruch der richtigen Stadt
    zuordnen laesst. Fuer die WIRKUNG der Sperre ist sie ohne Belang — siehe
    laden().
    """
    cid = pruefe_cluster_id(cluster_id)
    ensure_table(conn)
    conn.execute(
        "INSERT INTO sperrliste (cluster_id, grund, quelle, erfasst_am, stadt) "
        "VALUES (?,?,?,?,?) "
        "ON CONFLICT(cluster_id) DO UPDATE SET grund=excluded.grund, "
        "quelle=excluded.quelle, stadt=excluded.stadt",
        (cid, grund, quelle, erfasst_am or datetime.utcnow().isoformat(), stadt)
    )
    _datei_schreiben(conn, datei)


def entfernen(conn: sqlite3.Connection, cluster_id: str, datei: Path = None) -> int:
    ensure_table(conn)
    n = conn.execute("DELETE FROM sperrliste WHERE cluster_id = ?",
                     (pruefe_cluster_id(cluster_id),)).rowcount
    _datei_schreiben(conn, datei)
    return n


def laden(conn: sqlite3.Connection, datei: Path = None) -> set[str]:
    """Alle gesperrten Zell-Kennungen aus Datenbank und Zweitschrift.

    Einträge, die nur in der Datei stehen (etwa nach einem Neuaufbau der
    Datenbank), werden dabei in die Datenbank zurückgespielt. Die Sperre wirkt
    also sofort und nicht erst nach einem manuellen Abgleich.

    BEWUSST OHNE STADT-FILTER, und das bleibt auch so (T-49, 15.08.2026). Zwei
    Gründe, beide tragen für sich:

    1. Eine Zell-Kennung ist eine gerundete Koordinate und damit weltweit
       eindeutig. Zwei Städte können sich in derselben Zelle nicht begegnen,
       ein Filter hätte also nichts zu trennen.
    2. Die Zweitschrift `sperrliste.txt` führt keine Stadt. Würde `laden()`
       nach Stadt filtern, bekäme jeder aus der Datei zurückgespielte Eintrag
       den Vorgabewert `berlin` und fiele bei einem Kölner Lauf still aus der
       Sperre heraus. Ein Widerspruch nach Art. 21 DSGVO wäre damit unwirksam,
       ohne dass irgendwo ein Fehler erschiene.

    Zu breit sperren kann keine Daten offenlegen, zu eng sperren schon. Die
    Richtung des Fehlers entscheidet, und deshalb bleibt die Prüfung stadtblind.
    """
    ensure_table(conn)
    aus_db = {r[0] for r in conn.execute("SELECT cluster_id FROM sperrliste")}
    aus_datei = _datei_lesen(datei)
    fehlend = set(aus_datei) - aus_db
    if fehlend:
        jetzt = datetime.utcnow().isoformat()
        conn.executemany(
            "INSERT OR IGNORE INTO sperrliste (cluster_id, grund, quelle, erfasst_am) "
            "VALUES (?,?,?,?)",
            [(cid, aus_datei[cid][0], aus_datei[cid][1], jetzt) for cid in fehlend])
        conn.commit()
    return aus_db | set(aus_datei)


def auflisten(conn: sqlite3.Connection) -> list[tuple]:
    ensure_table(conn)
    return conn.execute(
        "SELECT cluster_id, grund, quelle, erfasst_am, stadt FROM sperrliste "
        "ORDER BY erfasst_am"
    ).fetchall()


def main():
    p = argparse.ArgumentParser(description="Sperrliste nach Art. 21 DSGVO verwalten")
    p.add_argument("--db", default=str(DB_PATH))
    p.add_argument("--list", action="store_true", help="gesperrte Zellen anzeigen")
    p.add_argument("--add", metavar="CLUSTER_ID", help="Zelle sperren")
    p.add_argument("--add-koordinate", nargs=2, metavar=("LAT", "LON"),
                   help="Zelle zu einer Koordinate sperren")
    p.add_argument("--remove", metavar="CLUSTER_ID", help="Sperre aufheben")
    p.add_argument("--grund", default="Widerspruch nach Art. 21 DSGVO")
    p.add_argument("--quelle", default="", help="Aktenzeichen des Vorgangs, KEIN Name")
    p.add_argument("--stadt", default="berlin",
                   help="Stadt des Vorgangs (nur zur Zuordnung, die Sperre wirkt "
                        "unabhaengig davon)")
    args = p.parse_args()

    conn = sqlite3.connect(args.db)
    try:
        if args.add or args.add_koordinate:
            cid = args.add or cluster_id_fuer(*args.add_koordinate)
            eintragen(conn, cid, grund=args.grund, quelle=args.quelle,
                      stadt=args.stadt)
            conn.commit()
            print(f"Gesperrt: {cid}")
            print("Wirksam nach dem naechsten tracker.py- bzw. export_html.py-Lauf.")
        elif args.remove:
            n = entfernen(conn, args.remove)
            conn.commit()
            print(f"Sperre aufgehoben: {args.remove}" if n else
                  f"Nicht in der Sperrliste: {args.remove}")
        else:
            zeilen = auflisten(conn)
            if not zeilen:
                print("Sperrliste ist leer.")
            for cid, grund, quelle, erfasst, stadt in zeilen:
                print(f"{cid}  {erfasst[:10]}  {stadt}  {quelle or '-'}  {grund}")
            print(f"\n{len(zeilen)} gesperrte Zelle(n).")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
