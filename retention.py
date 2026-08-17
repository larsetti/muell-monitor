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

Seit T-49 (15.08.2026) sind beide Fristen zusaetzlich STADTSCHARF ueberschreibbar:
    MM_RETENTION_QUELLE_TAGE_KOELN
    MM_RETENTION_AGGREGAT_MONATE_KOELN
Der Wert steht fuer JEDE Stadt unveraendert auf den 24 Monaten aus Abschnitt 3.5
der Folgenabschaetzung. Die stadtscharfe Einstellbarkeit ist bewusst nur die
Vorrichtung, nicht die Entscheidung: ob Koeln eine andere Frist bekommt, ist
eine Frage an Lars und die Kanzlei und wird nicht im Code beantwortet.

Aufruf:
    python retention.py --dry-run     zeigt die Wirkung ohne zu schreiben
    python retention.py --apply       fuehrt die Loeschung aus
    python retention.py --apply --stadt koeln    nur eine Stadt
Ohne --stadt laeuft die Routine NACHEINANDER je Stadt, nie stadtblind ueber den
Gesamtbestand. Im Normalbetrieb laeuft sie automatisch am Ende jedes
tracker.py-Laufs.

Schutz gegen Fehl-Loeschung: Schritt 1 wertet nur einen als vollstaendig
erkannten Abruf aus. Liefert die Schnittstelle nur einen Bruchteil des
ueblichen Umfangs, wird die Abwesenheit einer Meldung NICHT als Wegfall
gewertet. Sonst wuerde ein halber Abruf den halben Bestand zur Loeschung
vormerken.

Zweiter Schutz, seit T-49: JEDE Rechnung dieses Moduls ist auf eine Stadt
begrenzt. Vorher kannten weder die Praesenzmarkierung noch der
Vollstaendigkeitsanker noch die Abgangsgrenze eine Stadt — ein Koelner Lauf
haette den gesamten Berliner Bestand als aus der Quelle verschwunden vorgemerkt
und 30 Tage spaeter geloescht. Der Berliner Bestand ist unwiederbringlich: die
Schnittstelle war ein rollendes Fenster, erledigte Meldungen fielen nach 30
Tagen heraus. Die Jahre 2024 und 2025 hat heute niemand mehr, die Behoerde
eingeschlossen.
"""

import argparse
import logging
import os
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

import sicherung

DB_PATH = Path(__file__).parent / "ordnungsamt.db"
LOG_DATEI = Path(__file__).parent / "tracker.log"

# ── Fristen aus der DSFA ──────────────────────────────────────────────────────
QUELLE_FRIST_TAGE = int(os.environ.get("MM_RETENTION_QUELLE_TAGE", "30"))
AGGREGAT_FRIST_MONATE = int(os.environ.get("MM_RETENTION_AGGREGAT_MONATE", "24"))

# Standardstadt fuer Zeilen aus der Zeit vor T-49 und fuer Aufrufe, die keine
# Stadt nennen. Deckungsgleich mit dem Spalten-Vorgabewert im Schema.
STADT_STANDARD = "berlin"


def frist_quelle_tage(stadt: str = None) -> int:
    """Wegfall-Frist fuer eine Stadt. Ohne stadtscharfe Vorgabe gilt der Wert
    aus der Folgenabschaetzung fuer alle Staedte gleich."""
    if stadt:
        eigen = os.environ.get(f"MM_RETENTION_QUELLE_TAGE_{stadt.upper()}")
        if eigen:
            return int(eigen)
    return QUELLE_FRIST_TAGE


def frist_aggregat_monate(stadt: str = None) -> int:
    """Aggregat-Frist fuer eine Stadt.

    T-49: Der geplante Koelner Rueckimport beginnt am 12.12.2023. Beim ersten
    reguleren Lauf danach wuerde alles vor dem 15.08.2024 sofort zu
    Monats-Aggregaten reduziert und der duenne Rest ueber die k-Schwelle
    ersatzlos verworfen — man importiert also genau die Tiefe, fuer die der
    Rueckimport gemacht wird, und der naechste Lauf nimmt sie wieder weg, ohne
    Fehlermeldung, weil das die vorgesehene Funktion ist.
    Die Frist bleibt trotzdem bei 24 Monaten. Sie stammt WOERTLICH aus
    Abschnitt 3.5 der Folgenabschaetzung und wird nicht im Code angehoben; die
    Vorrichtung hier macht eine anwaltlich getragene Abweichung nur moeglich.
    """
    if stadt:
        eigen = os.environ.get(f"MM_RETENTION_AGGREGAT_MONATE_{stadt.upper()}")
        if eigen:
            return int(eigen)
    return AGGREGAT_FRIST_MONATE

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
            stadt            TEXT NOT NULL DEFAULT 'berlin',
            PRIMARY KEY (cluster_id, jahr_monat, kategorie_gruppe)
        );
        CREATE INDEX IF NOT EXISTS idx_quelle_weg ON meldungen(quelle_weg_seit);
    """)
    # T-49: stadt auch im Aggregat. Der Primaerschluessel bleibt unveraendert —
    # cluster_id ist eine Koordinatenzelle und damit weltweit eindeutig, zwei
    # Staedte koennen sich dort nicht begegnen. Die Spalte dient dazu, dass die
    # Schwellen-Loeschung aus M-01 nur die Zeilen ihrer eigenen Stadt anfasst.
    try:
        conn.execute("ALTER TABLE meldungen_aggregat ADD COLUMN stadt "
                     "TEXT NOT NULL DEFAULT 'berlin'")
    except sqlite3.OperationalError:
        pass
    conn.commit()


def staedte_im_bestand(conn: sqlite3.Connection) -> list[str]:
    """Alle Staedte, die in der Datenbank vorkommen — aus Meldungen UND
    Abrufprotokoll, damit eine Stadt auch dann bedient wird, wenn ihr Bestand
    gerade leer ist. Eine leere Datenbank liefert die Standardstadt, damit ein
    Lauf nie ins Nichts laeuft.
    """
    gefunden = set()
    for tabelle in ("meldungen", "fetch_log"):
        try:
            gefunden.update(
                r[0] for r in conn.execute(
                    f"SELECT DISTINCT stadt FROM {tabelle} WHERE stadt IS NOT NULL")
            )
        except sqlite3.OperationalError:
            pass
    return sorted(gefunden) or [STADT_STANDARD]


# ── Schritt 1: Wegfall aus der Quelle ────────────────────────────────────────

def feed_vollstaendig(conn: sqlite3.Connection, anzahl: int,
                      stadt: str = STADT_STANDARD) -> tuple[bool, str]:
    """Prueft, ob ein Abruf umfangreich genug ist, um Abwesenheit als Wegfall
    zu werten. Gibt (Ergebnis, Begruendung) zurueck.

    Der Massstab ist das groesste der letzten FEED_ANKER_LAEUFE erfolgreichen
    Abrufe DIESER STADT. Ein einzelner schrumpfender Lauf kann die Schwelle
    damit nicht absenken (Finding H-02).

    T-49: ohne den Stadt-Filter waere der Anker die groesste Abrufzahl
    IRGENDEINER Stadt. Berlin liefert rund 105.000 Meldungen je Abruf; ein
    Bonner Abruf mit wenigen tausend Meldungen haette die Schwelle nie erreicht
    und die Wegfall-Markierung waere dort dauerhaft ausgesetzt geblieben — ohne
    dass es auffaellt, weil das Aussetzen der vorgesehene Schutz ist.
    """
    anker = conn.execute(
        "SELECT MAX(count_total) FROM (SELECT count_total FROM fetch_log "
        "WHERE count_total > 0 AND stadt = ? ORDER BY id DESC LIMIT ?)",
        (stadt, FEED_ANKER_LAEUFE)
    ).fetchone()[0]
    if anker is None:
        ok = anzahl >= FEED_MINDESTZAHL
        return ok, (f"Stadt {stadt}: kein Vergleichswert, Untergrenze "
                    f"{FEED_MINDESTZAHL}, abgerufen {anzahl}")
    schwelle = int(anker * FEED_MINDESTANTEIL)
    return anzahl >= schwelle, (f"Stadt {stadt}: Anker {anker} (groesster der "
                                f"letzten {FEED_ANKER_LAEUFE} erfolgreichen "
                                f"Abrufe dieser Stadt), Schwelle {schwelle}, "
                                f"abgerufen {anzahl}")


def markiere_quellpraesenz(conn: sqlite3.Connection, quell_ids, jetzt: str,
                           stadt: str = STADT_STANDARD) -> dict:
    """Haelt fest, welche Meldungen DIESER STADT noch in der Quelle stehen.

    Wer im Abruf vorkommt, bekommt last_seen_at gesetzt und eine etwaige
    Wegfall-Markierung geloescht (die Meldung ist zurueck). Wer fehlt und noch
    nicht markiert ist, bekommt quelle_weg_seit auf jetzt.

    Der Abgleich laeuft ueber eine temporaere Tabelle statt ueber ein grosses
    IN (...), weil der Abruf sechsstellig viele Kennungen enthaelt.

    T-49 / Befund 2: JEDE der drei Rechnungen hier ist auf die Stadt begrenzt —
    Bestandsgroesse, Abgangsgrenze und beide UPDATE-Anweisungen. Stadtblind
    haette ein Koelner Lauf jede Berliner Meldung als aus der Quelle
    verschwunden vorgemerkt. Der 20-Prozent-Riegel haette das gebremst, aber
    nicht gesperrt: er meldet nur und haette ab dem ersten Koelner Lauf bei
    JEDEM Durchgang angeschlagen und damit die Loeschroutine dauerhaft
    blockiert. Aus einem stillen Datenverlust waere ein dauerhafter
    Rechtsverstoss geworden — beides falsch.
    """
    conn.execute("CREATE TEMP TABLE IF NOT EXISTS _quell_ids (id TEXT PRIMARY KEY)")
    conn.execute("DELETE FROM _quell_ids")
    conn.executemany("INSERT OR IGNORE INTO _quell_ids (id) VALUES (?)",
                     ((str(i),) for i in quell_ids))

    # Zweite Sicherung gegen Massen-Vormerkung (H-02): erst zaehlen, dann
    # entscheiden. Ein Lauf, der mehr als FEED_MAX_ABGANG_ANTEIL des Bestands
    # DIESER STADT auf einmal vormerken wuerde, wird abgebrochen und gemeldet.
    # Lieber ein Lauf, der auffaellt, als eine Loeschung, die 30 Tage spaeter
    # auffaellt.
    bestand = conn.execute(
        "SELECT COUNT(*) FROM meldungen WHERE stadt = ?", (stadt,)).fetchone()[0]
    wuerde_weg = conn.execute(
        "SELECT COUNT(*) FROM meldungen WHERE stadt = ? "
        "AND quelle_weg_seit IS NULL AND id NOT IN (SELECT id FROM _quell_ids)",
        (stadt,)
    ).fetchone()[0]
    grenze = int(bestand * FEED_MAX_ABGANG_ANTEIL)
    if bestand and wuerde_weg > grenze:
        conn.commit()
        return {
            "in_quelle": 0, "neu_als_weggefallen_markiert": 0, "abgebrochen": True,
            "stadt": stadt,
            "begruendung": (
                f"Stadt {stadt}: {wuerde_weg} von {bestand} Meldungen waeren auf "
                f"einmal als weggefallen vorgemerkt worden, erlaubt sind {grenze} "
                f"({int(FEED_MAX_ABGANG_ANTEIL * 100)} Prozent). Der Bestand bleibt "
                f"unangetastet. Ursache pruefen: liefert die Quelle wirklich einen "
                f"neu aufgebauten Bestand, oder ist der Abruf unvollstaendig?"
            ),
        }

    zurueck = conn.execute(
        "UPDATE meldungen SET last_seen_at = ?, quelle_weg_seit = NULL "
        "WHERE stadt = ? AND id IN (SELECT id FROM _quell_ids)", (jetzt, stadt)
    ).rowcount
    neu_weg = conn.execute(
        "UPDATE meldungen SET quelle_weg_seit = ? WHERE stadt = ? "
        "AND quelle_weg_seit IS NULL AND id NOT IN (SELECT id FROM _quell_ids)",
        (jetzt, stadt)
    ).rowcount
    conn.commit()
    return {"in_quelle": zurueck, "neu_als_weggefallen_markiert": neu_weg,
            "abgebrochen": False, "begruendung": "", "stadt": stadt}


def loesche_quellabgang(conn: sqlite3.Connection, jetzt: datetime,
                        frist_tage: int = None, dry_run: bool = False,
                        stadt: str = STADT_STANDARD) -> int:
    """Loescht Meldungen DIESER STADT, die seit frist_tage nicht mehr in der
    Quelle stehen."""
    frist_tage = frist_quelle_tage(stadt) if frist_tage is None else frist_tage
    grenze = (jetzt - timedelta(days=frist_tage)).isoformat()
    betroffen = conn.execute(
        "SELECT COUNT(*) FROM meldungen WHERE stadt = ? "
        "AND quelle_weg_seit IS NOT NULL AND quelle_weg_seit < ?", (stadt, grenze)
    ).fetchone()[0]
    if not dry_run and betroffen:
        conn.execute("DELETE FROM meldungen WHERE stadt = ? "
                     "AND quelle_weg_seit IS NOT NULL AND quelle_weg_seit < ?",
                     (stadt, grenze))
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


def aggregat_grenze(jetzt: datetime, stadt: str = None) -> datetime:
    """Der Zeitpunkt, ab dem Einzelmeldungen dieser Stadt noch bestehen duerfen.

    Alles davor wird beim naechsten Lauf zu Monats-Aggregaten reduziert und
    unterhalb der k-Schwelle ersatzlos verworfen.

    Oeffentlich, weil der Rueckimport (T-49) seinen Startzeitpunkt HIER
    ableitet statt ihn zu verdrahten. Lars-Entscheidung vom 15.08.2026: der
    Rueckimport wird auf die Aufbewahrungsfrist gekuerzt, weil alles Aeltere
    beim ersten regulaeren Lauf ohnehin wieder verschwinden wuerde. Ein festes
    Datum im Quelltext liefe in einem Jahr gegen dieselbe Grenze; diese
    Funktion nicht.
    """
    return _monate_zurueck(jetzt, frist_aggregat_monate(stadt))


def _gruppe(kategorie: str, betreff: str) -> str:
    """Abfallgruppe statt Freitext — der Aggregat-Bestand traegt keinen
    Originaltext mehr (wirkt zugleich auf Risiko R-10)."""
    import export_html
    return export_html.kategorisiere(f"{kategorie or ''} {betreff or ''}") or "sonstige"


def aggregiere_altbestand(conn: sqlite3.Connection, jetzt: datetime,
                          frist_monate: int = None, dry_run: bool = False,
                          stadt: str = STADT_STANDARD) -> int:
    """Reduziert Meldungen DIESER STADT, die aelter als frist_monate sind, auf
    Monats-Aggregate je Rasterzelle und Abfallgruppe und loescht danach die
    Einzelmeldungen.

    T-49 / Befund 1: vorher lief das DELETE ueber den GESAMTEN Bestand, rein
    datumsbasiert. Ein Lauf, der fuer Berlin gedacht war, haette den Koelner
    Rueckimport ab dem 12.12.2023 im selben Zug mitreduziert.
    """
    frist_monate = frist_aggregat_monate(stadt) if frist_monate is None else frist_monate
    grenze = _monate_zurueck(jetzt, frist_monate).strftime("%Y-%m-%d")

    rows = conn.execute(
        "SELECT lat, lon, datum, kategorie, betreff FROM meldungen "
        "WHERE stadt = ? AND datum IS NOT NULL AND datum < ?", (stadt, grenze)
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
        INSERT INTO meldungen_aggregat (cluster_id, jahr_monat, kategorie_gruppe, anzahl, stadt)
        VALUES (?,?,?,?,?)
        ON CONFLICT(cluster_id, jahr_monat, kategorie_gruppe)
        DO UPDATE SET anzahl = anzahl + excluded.anzahl
    """, [(c, jm, g, n, stadt) for (c, jm, g), n in eimer.items()])
    conn.execute("DELETE FROM meldungen WHERE stadt = ? AND datum IS NOT NULL "
                 "AND datum < ?", (stadt, grenze))
    # M-01: alles unterhalb der k-Schwelle faellt ersatzlos weg — aber nur in
    # der Stadt, die gerade verarbeitet wird.
    verworfen = conn.execute(
        "DELETE FROM meldungen_aggregat WHERE stadt = ? AND anzahl < ?",
        (stadt, AGGREGAT_MIN_ANZAHL)).rowcount
    conn.commit()
    if verworfen:
        print(f"  {verworfen} Aggregat-Zeile(n) der Stadt {stadt} unterhalb der "
              f"Schwelle {AGGREGAT_MIN_ANZAHL} verworfen")
    return len(rows)


# ── Gesamtlauf ───────────────────────────────────────────────────────────────

def anwenden(conn: sqlite3.Connection, jetzt: datetime = None,
             dry_run: bool = False, stadt: str = None) -> dict:
    """Fuehrt beide Fristen aus und gibt die Zahlen zum Protokollieren zurueck.

    Mit `stadt` laeuft genau diese eine Stadt. OHNE `stadt` laeuft die Routine
    NACHEINANDER je Stadt aus dem Bestand — sie fasst nie stadtblind den
    Gesamtbestand an. Das ist der Unterschied, auf den es ankommt: ein Lauf
    ohne Angabe soll alle Staedte bedienen, aber jede fuer sich rechnen, damit
    eine stadtscharfe Frist auch dann greift, wenn niemand sie ausdruecklich
    anfordert.
    """
    jetzt = jetzt or datetime.utcnow()
    init_schema(conn)

    staedte = [stadt] if stadt else staedte_im_bestand(conn)
    je_stadt = {}
    for s in staedte:
        je_stadt[s] = {
            "quellabgang_geloescht": loesche_quellabgang(
                conn, jetzt, dry_run=dry_run, stadt=s),
            "altbestand_aggregiert": aggregiere_altbestand(
                conn, jetzt, dry_run=dry_run, stadt=s),
            "frist_quelle_tage": frist_quelle_tage(s),
            "frist_aggregat_monate": frist_aggregat_monate(s),
        }

    return {
        # Summen ueber alle bearbeiteten Staedte — was die Aufrufer bisher
        # protokolliert haben, bleibt damit unveraendert lesbar.
        "quellabgang_geloescht": sum(e["quellabgang_geloescht"] for e in je_stadt.values()),
        "altbestand_aggregiert": sum(e["altbestand_aggregiert"] for e in je_stadt.values()),
        "frist_quelle_tage": QUELLE_FRIST_TAGE,
        "frist_aggregat_monate": AGGREGAT_FRIST_MONATE,
        "staedte": list(je_stadt.keys()),
        "je_stadt": je_stadt,
    }


def _log() -> logging.Logger:
    """Eigener Logger auf dieselbe Datei wie tracker.py.

    Bewusst kein `import tracker` — tracker importiert dieses Modul, ein Import
    zurueck waere ein Zirkel. Der Handler wird nur einmal gehaengt.

    T-64 (15.08.2026, Befund S-03): Bis hierher schrieb der Kommandozeilenweg
    ausschliesslich auf die Konsole. Als am 15.08.2026 rund 710 Meldungen
    unwiederbringlich entfernt wurden, war der Vorgang aus der Datenbank und aus
    tracker.log allein nicht mehr rekonstruierbar — die Loeschung war gewollt,
    aber sie hinterliess keine Spur. Die drei Wege, die aggregieren, muessen
    dieselbe Zeile schreiben, sonst haengt die Nachvollziehbarkeit daran,
    welcher davon zufaellig benutzt wurde.
    """
    log = logging.getLogger("retention")
    if not log.handlers:
        log.setLevel(logging.INFO)
        formatierer = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
        datei = logging.FileHandler(LOG_DATEI, encoding="utf-8")
        datei.setFormatter(formatierer)
        log.addHandler(datei)
        strom = logging.StreamHandler()
        strom.setFormatter(formatierer)
        log.addHandler(strom)
    return log


def protokolliere(ergebnis: dict, db_pfad, dry_run: bool,
                  log: logging.Logger = None) -> str:
    """Schreibt die Protokollzeile zum Ergebnis eines Laufs (T-64).

    Genannt werden Pfad, Modus und die Zahlen JE STADT UND JE REGEL — die
    Summenzeile, die tracker._fristen_anwenden schreibt, reicht dafuer nicht:
    aus ihr geht nicht hervor, welcher Stadt die entfernten Meldungen gehoerten.
    """
    log = log or _log()
    je_stadt = " | ".join(
        f"{s}: {e['quellabgang_geloescht']} wegen Quellabgang (Frist "
        f"{e['frist_quelle_tage']} Tage), {e['altbestand_aggregiert']} auf "
        f"Aggregate reduziert (Frist {e['frist_aggregat_monate']} Monate)"
        for s, e in ergebnis["je_stadt"].items()
    ) or "keine Stadt im Bestand"
    zeile = (f"Loeschroutine [{'TROCKENLAUF' if dry_run else 'LIVE'}] "
             f"{db_pfad} — {je_stadt}")
    log.info(zeile)
    return zeile


def main():
    p = argparse.ArgumentParser(description="Loeschroutine nach Art. 5 Abs. 1 lit. e DSGVO")
    p.add_argument("--dry-run", action="store_true", help="nur anzeigen, nichts loeschen")
    p.add_argument("--apply", action="store_true", help="Loeschung ausfuehren")
    p.add_argument("--db", default=str(DB_PATH), help="Pfad zur Datenbank")
    p.add_argument("--stadt", default=None,
                   help="nur diese Stadt bearbeiten; ohne Angabe nacheinander alle")
    p.add_argument("--keine-sicherung", action="store_true",
                   help="ohne vorherige Sicherung loeschen (nur mit --apply "
                        "wirksam; die Loeschung ist danach nicht mehr "
                        "rueckgaengig zu machen)")
    args = p.parse_args()
    if args.apply == args.dry_run:
        p.error("genau eines von --dry-run oder --apply angeben")

    # T-64: vor der einzigen unwiederbringlichen Operation dieses Projekts wird
    # gesichert. Der Trockenlauf braucht das nicht, er schreibt nichts.
    # Schlaegt die Sicherung fehl, wird NICHT geloescht — fail-closed. Das ist
    # die Lehre aus dem 15.08.2026: an dem Tag hat dieser Weg 710
    # unwiederbringliche Meldungen entfernt, und es gab keine Sicherung.
    if args.apply and not args.keine_sicherung:
        try:
            gesichert = sicherung.sichere(Path(args.db),
                                          anlass="vor-loeschroutine")
            print(f"Sicherung: {gesichert['pfad']}")
            print(f"           {gesichert['groesse']:,} Byte, "
                  f"integrity_check {gesichert['integritaet']}")
        except Exception as fehler:
            print(f"ABBRUCH: Die Sicherung ist gescheitert, deshalb wird NICHT "
                  f"geloescht.\n  {fehler}\n"
                  f"  Ohne Sicherung loeschen: --keine-sicherung. Der Bestand "
                  f"ist danach nicht wiederherstellbar.", file=sys.stderr)
            return 2

    conn = sqlite3.connect(args.db)
    ergebnis = anwenden(conn, dry_run=args.dry_run, stadt=args.stadt)
    rest = conn.execute("SELECT COUNT(*) FROM meldungen").fetchone()[0]
    rest_je_stadt = conn.execute(
        "SELECT stadt, COUNT(*) FROM meldungen GROUP BY stadt ORDER BY stadt").fetchall()
    conn.close()

    protokolliere(ergebnis, args.db, args.dry_run)

    print(f"Datenbank: {args.db}")
    print(f"Modus:     {'DRY-RUN (kein Schreiben)' if args.dry_run else 'LIVE'}")
    print(f"Staedte:   {', '.join(ergebnis['staedte'])}")
    for s, e in ergebnis["je_stadt"].items():
        print(f"\n  [{s}]")
        print(f"    Frist Wegfall aus der Quelle: {e['frist_quelle_tage']} Tage")
        print(f"    Frist Aggregat-Reduktion:     {e['frist_aggregat_monate']} Monate")
        print(f"    wegen Quellabgang geloescht:  {e['quellabgang_geloescht']}")
        print(f"    auf Aggregate reduziert:      {e['altbestand_aggregiert']}")
    print()
    for s, n in rest_je_stadt:
        print(f"  Meldungen verbleibend ({s}): {n}")
    print(f"  Meldungen verbleibend gesamt: {rest}")
    print(f"\nProtokolliert nach {LOG_DATEI}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
