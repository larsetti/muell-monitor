#!/usr/bin/env python3
"""
Loeschroutine (Abhilfe A-4 der DSFA vom 28.07.2026)
====================================================
Setzt die Speicherbegrenzung nach Art. 5 Abs. 1 lit. e DSGVO um. Bis hierher
hat die Pipeline ausschliesslich hinzugefuegt; es gab kein DELETE im Code.

Zwei Fristen, beide aus der DSFA uebernommen (Abschnitt 3.5 und Massnahme A-4),
nicht selbst festgelegt:

  1. Wegfall aus der Quelle -> Loeschung nach 30 Tagen.
     Eine Meldung, die das Ordnungsamt aus seinem offenen Datenbestand
     entfernt hat, verschwindet 30 Tage spaeter auch hier.
  2. Alter ueber 24 Monate -> Reduktion auf nicht personenbezogene Aggregate.
     Die Einzelmeldung wird geloescht; erhalten bleibt nur, wie viele
     Meldungen einer Abfallgruppe in einem Monat auf eine Rasterzelle
     entfielen. Tagesdatum, Freitext und die genaue Koordinate entfallen.

Beide Fristen sind ueber Umgebungsvariablen konfigurierbar, damit ein
abweichender anwaltlicher Befund ohne Codeaenderung greift:
    MM_RETENTION_QUELLE_TAGE      (Standard 30)
    MM_RETENTION_AGGREGAT_MONATE  (Standard 24)

Aufruf:
    python retention.py --dry-run     zeigt die Wirkung ohne zu schreiben
    python retention.py --apply       fuehrt die Loeschung aus
Im Normalbetrieb laeuft die Routine automatisch am Ende jedes erfolgreichen
tracker.py-Laufs.

Schutz gegen Fehl-Loeschung: Schritt 1 wertet nur einen als vollstaendig
erkannten Abruf aus. Liefert die Schnittstelle nur einen Bruchteil des
ueblichen Umfangs, wird die Abwesenheit einer Meldung NICHT als Wegfall
gewertet. Sonst wuerde ein halber Abruf den halben Bestand zur Loeschung
vormerken.
"""

import argparse
import os
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

DB_PATH = Path(__file__).parent / "ordnungsamt.db"

# ── Fristen aus der DSFA ──────────────────────────────────────────────────────
QUELLE_FRIST_TAGE = int(os.environ.get("MM_RETENTION_QUELLE_TAGE", "30"))
AGGREGAT_FRIST_MONATE = int(os.environ.get("MM_RETENTION_AGGREGAT_MONATE", "24"))

# Finding M-01 (29.07.2026): Ohne Schwelle bestand das Aggregat zu 96 Prozent aus
# Zeilen mit dem Zaehler 1 — auf derselben Rasteraufloesung wie die Karte. Das war
# eine Vergroeberung, keine Aggregation, und trug weder fachlich noch als
# Anonymisierung. Behalten wird nur, was die k-Schwelle der Veroeffentlichung
# ohnehin verlangt (A-3): ab drei Meldungen. Alles darunter wird nach Ablauf der
# Frist ersatzlos geloescht.
# Nebenwirkung, bewusst in Kauf genommen: ein Eimer, der erst ueber mehrere
# Laeufe auf drei anwaechst, wird zwischendurch geloescht und faengt wieder bei
# eins an. Das trifft nur nachtraeglich eintreffende Altmeldungen und faellt
# zugunsten der Datensparsamkeit aus.
AGGREGAT_MIN_ANZAHL = int(os.environ.get("MM_RETENTION_AGGREGAT_MIN", "3"))

# Ein Abruf gilt als vollstaendig, wenn er mindestens diesen Anteil des Ankers
# erreicht. Darunter bleibt der Bestand unangetastet.
FEED_MINDESTANTEIL = 0.5
# Ohne Vergleichswert (frische Datenbank) diese absolute Untergrenze.
FEED_MINDESTZAHL = 1000

# Finding H-02 (29.07.2026): Der Anker ist das GROESSTE der letzten N
# erfolgreichen Abrufe, nicht der zuletzt akzeptierte. Sonst schaukelt sich die
# Schwelle nach unten: ein Abruf mit 51 Prozent des Vortages gilt als
# vollstaendig und wird selbst zum neuen Massstab. Nach acht solchen Laeufen
# waeren 99,5 Prozent des Bestands zur Loeschung vorgemerkt, ohne dass ein
# einziger Lauf auffaellig gewesen waere.
FEED_ANKER_LAEUFE = 14

# Zweite Sicherung: soviel Anteil des Bestands darf ein einzelner Lauf hoechstens
# neu als weggefallen vormerken. Greift auch dann, wenn der Anker taeuscht — etwa
# wenn die Behoerde nach dem Ausfall mit einem neu aufgebauten, kleineren Bestand
# zurueckkommt. Ueberschreitungen werden gemeldet, nicht still ausgefuehrt.
FEED_MAX_ABGANG_ANTEIL = 0.20


def init_schema(conn: sqlite3.Connection):
    """Legt die Felder und die Aggregat-Tabelle an. Idempotent.

    Folgt dem Muster aus tracker.init_db: CREATE TABLE IF NOT EXISTS plus
    ALTER TABLE ADD COLUMN in try/except, keine Migrationsverwaltung.
    """
    for col, typedef in [("last_seen_at", "TEXT"), ("quelle_weg_seit", "TEXT")]:
        try:
            conn.execute(f"ALTER TABLE meldungen ADD COLUMN {col} {typedef}")
        except sqlite3.OperationalError:
            pass
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS meldungen_aggregat (
            cluster_id       TEXT NOT NULL,
            jahr_monat       TEXT NOT NULL,
            kategorie_gruppe TEXT NOT NULL,
            anzahl           INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (cluster_id, jahr_monat, kategorie_gruppe)
        );
        CREATE INDEX IF NOT EXISTS idx_quelle_weg ON meldungen(quelle_weg_seit);
    """)
    conn.commit()


# ── Schritt 1: Wegfall aus der Quelle ────────────────────────────────────────

def feed_vollstaendig(conn: sqlite3.Connection, anzahl: int) -> tuple[bool, str]:
    """Prueft, ob ein Abruf umfangreich genug ist, um Abwesenheit als Wegfall
    zu werten. Gibt (Ergebnis, Begruendung) zurueck.

    Der Massstab ist das groesste der letzten FEED_ANKER_LAEUFE erfolgreichen
    Abrufe. Ein einzelner schrumpfender Lauf kann die Schwelle damit nicht
    absenken (Finding H-02).
    """
    anker = conn.execute(
        "SELECT MAX(count_total) FROM (SELECT count_total FROM fetch_log "
        "WHERE count_total > 0 ORDER BY id DESC LIMIT ?)", (FEED_ANKER_LAEUFE,)
    ).fetchone()[0]
    if anker is None:
        ok = anzahl >= FEED_MINDESTZAHL
        return ok, (f"kein Vergleichswert, Untergrenze {FEED_MINDESTZAHL}, "
                    f"abgerufen {anzahl}")
    schwelle = int(anker * FEED_MINDESTANTEIL)
    return anzahl >= schwelle, (f"Anker {anker} (groesster der letzten "
                                f"{FEED_ANKER_LAEUFE} erfolgreichen Abrufe), "
                                f"Schwelle {schwelle}, abgerufen {anzahl}")


def markiere_quellpraesenz(conn: sqlite3.Connection, quell_ids, jetzt: str) -> dict:
    """Haelt fest, welche Meldungen noch in der Quelle stehen.

    Wer im Abruf vorkommt, bekommt last_seen_at gesetzt und eine etwaige
    Wegfall-Markierung geloescht (die Meldung ist zurueck). Wer fehlt und noch
    nicht markiert ist, bekommt quelle_weg_seit auf jetzt.

    Der Abgleich laeuft ueber eine temporaere Tabelle statt ueber ein grosses
    IN (...), weil der Abruf sechsstellig viele Kennungen enthaelt.
    """
    conn.execute("CREATE TEMP TABLE IF NOT EXISTS _quell_ids (id TEXT PRIMARY KEY)")
    conn.execute("DELETE FROM _quell_ids")
    conn.executemany("INSERT OR IGNORE INTO _quell_ids (id) VALUES (?)",
                     ((str(i),) for i in quell_ids))

    # Zweite Sicherung gegen Massen-Vormerkung (H-02): erst zaehlen, dann
    # entscheiden. Ein Lauf, der mehr als FEED_MAX_ABGANG_ANTEIL des Bestands
    # auf einmal vormerken wuerde, wird abgebrochen und gemeldet. Lieber ein
    # Lauf, der auffaellt, als eine Loeschung, die 30 Tage spaeter auffaellt.
    bestand = conn.execute("SELECT COUNT(*) FROM meldungen").fetchone()[0]
    wuerde_weg = conn.execute(
        "SELECT COUNT(*) FROM meldungen "
        "WHERE quelle_weg_seit IS NULL AND id NOT IN (SELECT id FROM _quell_ids)"
    ).fetchone()[0]
    grenze = int(bestand * FEED_MAX_ABGANG_ANTEIL)
    if bestand and wuerde_weg > grenze:
        conn.commit()
        return {
            "in_quelle": 0, "neu_als_weggefallen_markiert": 0, "abgebrochen": True,
            "begruendung": (
                f"{wuerde_weg} von {bestand} Meldungen waeren auf einmal als "
                f"weggefallen vorgemerkt worden, erlaubt sind {grenze} "
                f"({int(FEED_MAX_ABGANG_ANTEIL * 100)} Prozent). Der Bestand bleibt "
                f"unangetastet. Ursache pruefen: liefert die Quelle wirklich einen "
                f"neu aufgebauten Bestand, oder ist der Abruf unvollstaendig?"
            ),
        }

    zurueck = conn.execute(
        "UPDATE meldungen SET last_seen_at = ?, quelle_weg_seit = NULL "
        "WHERE id IN (SELECT id FROM _quell_ids)", (jetzt,)
    ).rowcount
    neu_weg = conn.execute(
        "UPDATE meldungen SET quelle_weg_seit = ? "
        "WHERE quelle_weg_seit IS NULL AND id NOT IN (SELECT id FROM _quell_ids)",
        (jetzt,)
    ).rowcount
    conn.commit()
    return {"in_quelle": zurueck, "neu_als_weggefallen_markiert": neu_weg,
            "abgebrochen": False, "begruendung": ""}


def loesche_quellabgang(conn: sqlite3.Connection, jetzt: datetime,
                        frist_tage: int = None, dry_run: bool = False) -> int:
    """Loescht Meldungen, die seit frist_tage nicht mehr in der Quelle stehen."""
    frist_tage = QUELLE_FRIST_TAGE if frist_tage is None else frist_tage
    grenze = (jetzt - timedelta(days=frist_tage)).isoformat()
    betroffen = conn.execute(
        "SELECT COUNT(*) FROM meldungen "
        "WHERE quelle_weg_seit IS NOT NULL AND quelle_weg_seit < ?", (grenze,)
    ).fetchone()[0]
    if not dry_run and betroffen:
        conn.execute("DELETE FROM meldungen "
                     "WHERE quelle_weg_seit IS NOT NULL AND quelle_weg_seit < ?",
                     (grenze,))
        conn.commit()
    return betroffen


# ── Schritt 2: Reduktion auf Aggregate nach 24 Monaten ───────────────────────

def _monate_zurueck(zeitpunkt: datetime, monate: int) -> datetime:
    """Datum um Monate zurueckrechnen, ohne Zusatzbibliothek."""
    jahr = zeitpunkt.year
    monat = zeitpunkt.month - monate
    while monat <= 0:
        monat += 12
        jahr -= 1
    tag = min(zeitpunkt.day, [31, 29 if (jahr % 4 == 0 and (jahr % 100 != 0 or jahr % 400 == 0))
                              else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][monat - 1])
    return zeitpunkt.replace(year=jahr, month=monat, day=tag)


def _gruppe(kategorie: str, betreff: str) -> str:
    """Abfallgruppe statt Freitext — der Aggregat-Bestand traegt keinen
    Originaltext mehr (wirkt zugleich auf Risiko R-10)."""
    import export_html
    return export_html.kategorisiere(f"{kategorie or ''} {betreff or ''}") or "sonstige"


def aggregiere_altbestand(conn: sqlite3.Connection, jetzt: datetime,
                          frist_monate: int = None, dry_run: bool = False) -> int:
    """Reduziert Meldungen aelter als frist_monate auf Monats-Aggregate je
    Rasterzelle und Abfallgruppe und loescht danach die Einzelmeldungen."""
    frist_monate = AGGREGAT_FRIST_MONATE if frist_monate is None else frist_monate
    grenze = _monate_zurueck(jetzt, frist_monate).strftime("%Y-%m-%d")

    rows = conn.execute(
        "SELECT lat, lon, datum, kategorie, betreff FROM meldungen "
        "WHERE datum IS NOT NULL AND datum < ?", (grenze,)
    ).fetchall()
    if not rows:
        return 0
    if dry_run:
        return len(rows)

    import tracker
    eimer: dict[tuple, int] = {}
    for lat, lon, datum, kategorie, betreff in rows:
        cid = tracker.cluster_id(lat, lon) if lat is not None and lon is not None else "ohne_ort"
        schluessel = (cid, str(datum)[:7], _gruppe(kategorie, betreff))
        eimer[schluessel] = eimer.get(schluessel, 0) + 1

    conn.executemany("""
        INSERT INTO meldungen_aggregat (cluster_id, jahr_monat, kategorie_gruppe, anzahl)
        VALUES (?,?,?,?)
        ON CONFLICT(cluster_id, jahr_monat, kategorie_gruppe)
        DO UPDATE SET anzahl = anzahl + excluded.anzahl
    """, [(c, jm, g, n) for (c, jm, g), n in eimer.items()])
    conn.execute("DELETE FROM meldungen WHERE datum IS NOT NULL AND datum < ?", (grenze,))
    # M-01: alles unterhalb der k-Schwelle faellt ersatzlos weg.
    verworfen = conn.execute("DELETE FROM meldungen_aggregat WHERE anzahl < ?",
                             (AGGREGAT_MIN_ANZAHL,)).rowcount
    conn.commit()
    if verworfen:
        print(f"  {verworfen} Aggregat-Zeile(n) unterhalb der Schwelle "
              f"{AGGREGAT_MIN_ANZAHL} verworfen")
    return len(rows)


# ── Gesamtlauf ───────────────────────────────────────────────────────────────

def anwenden(conn: sqlite3.Connection, jetzt: datetime = None,
             dry_run: bool = False) -> dict:
    """Fuehrt beide Fristen aus und gibt die Zahlen zum Protokollieren zurueck."""
    jetzt = jetzt or datetime.utcnow()
    init_schema(conn)
    geloescht = loesche_quellabgang(conn, jetzt, dry_run=dry_run)
    aggregiert = aggregiere_altbestand(conn, jetzt, dry_run=dry_run)
    return {
        "quellabgang_geloescht": geloescht,
        "altbestand_aggregiert": aggregiert,
        "frist_quelle_tage": QUELLE_FRIST_TAGE,
        "frist_aggregat_monate": AGGREGAT_FRIST_MONATE,
    }


def main():
    p = argparse.ArgumentParser(description="Loeschroutine nach Art. 5 Abs. 1 lit. e DSGVO")
    p.add_argument("--dry-run", action="store_true", help="nur anzeigen, nichts loeschen")
    p.add_argument("--apply", action="store_true", help="Loeschung ausfuehren")
    p.add_argument("--db", default=str(DB_PATH), help="Pfad zur Datenbank")
    args = p.parse_args()
    if args.apply == args.dry_run:
        p.error("genau eines von --dry-run oder --apply angeben")

    conn = sqlite3.connect(args.db)
    ergebnis = anwenden(conn, dry_run=args.dry_run)
    rest = conn.execute("SELECT COUNT(*) FROM meldungen").fetchone()[0]
    conn.close()

    print(f"Datenbank: {args.db}")
    print(f"Modus:     {'DRY-RUN (kein Schreiben)' if args.dry_run else 'LIVE'}")
    print(f"Frist Wegfall aus der Quelle: {ergebnis['frist_quelle_tage']} Tage")
    print(f"Frist Aggregat-Reduktion:     {ergebnis['frist_aggregat_monate']} Monate")
    print(f"  wegen Quellabgang geloescht: {ergebnis['quellabgang_geloescht']}")
    print(f"  auf Aggregate reduziert:     {ergebnis['altbestand_aggregiert']}")
    print(f"  Meldungen verbleibend:       {rest}")


if __name__ == "__main__":
    main()
