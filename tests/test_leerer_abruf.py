"""
Tests zu T-55: ein leerer Abruf darf keine Auflage aussetzen
=============================================================
Die Folgenabschaetzung vom 28.07.2026 sagt in Abhilfe A-2 (Risiken R-1 und
R-7), Zellen mit einer einzigen Meldung werden "gar nicht erst dauerhaft
gespeichert". Umgesetzt war das an zwei Stellen: beim Anlegen in
`tracker.berechne_hotspots` und als Nachlauf ueber den Altbestand am Ende
derselben Funktion.

DER FEHLER: `tracker.run` kehrt bei einem leeren Abruf zurueck, BEVOR es
berechne_hotspots erreicht (Befund H-02b, richtig so — aus einem leeren Abruf
darf nichts neu aufgebaut werden). Damit lief auch der reine Loeschteil nicht
mehr. Eine Datenschutz-Auflage hing also daran, dass eine fremde Schnittstelle
antwortet.

Das ist wortgleich die Lehre aus Befund H-01 vom 29.07.2026, wo genau das fuer
die Loeschfristen behoben wurde: `_fristen_anwenden` laeuft seitdem auch im
Leerpfad. `_zellen_auflagen_nachziehen` tut das seit dem 17.08.2026 ebenso.

WIE ES AUFFIEL: 2.909 Berliner Einzelfall-Zellen lagen im Bestand. Berlins
Quelle liefert seit dem 22.04.2026 nichts, jeder Berliner Lauf endete also im
Leerpfad, und die Bereinigung kam dort nie an. Weggeraeumt hat sie am
15.08.2026 ein Koelner Lauf, der stadtblind mitbereinigte — was seinerseits
der Fehler war, den T-66 behoben hat. Nach T-66 haette Berlin sie behalten,
ohne dass irgendwo etwas auffaellt.

DESHALB PRUEFT DIESE DATEI ZWEI DINGE ZUSAMMEN, nicht nacheinander: dass die
Bereinigung ohne Abruf laeuft, UND dass sie dabei die unter T-66 eingezogene
Stadt-Trennung nicht wieder aufweicht. Jede Haelfte allein waere zu haben,
indem man die andere kaputtmacht.
"""

import sqlite3
import sys
from pathlib import Path

TECHNIK = Path(__file__).parent.parent
sys.path.insert(0, str(TECHNIK))

import quellen  # noqa: E402
import sperrliste  # noqa: E402
import tracker  # noqa: E402

BERLIN = (52.5200, 13.4050)
BERLIN_2 = (52.4800, 13.3500)
KOELN = (50.9375, 6.9603)
KOELN_2 = (50.9200, 6.9400)


def _zelle(conn, ort, stadt: str, count: int):
    """Altbestand aus der Zeit vor A-2, unmittelbar angelegt."""
    conn.execute(
        "INSERT INTO hotspots (cluster_id, lat_center, lon_center, bezirk, "
        "meldungen_count, recurrence_count, last_seen, first_seen, score, "
        "score_label, strasse, plz, stadt) "
        "VALUES (?,?,?,'Mitte',?,0,'2026-01-01','2026-01-01',1.0,'niedrig','','',?)",
        (tracker.cluster_id(*ort), ort[0], ort[1], count, stadt))


def _meldung(conn, mid: str, datum: str, stadt: str, ort):
    conn.execute(
        "INSERT INTO meldungen (id, fetched_at, datum, kategorie, betreff, bezirk, "
        "lat, lon, status, is_muell, strasse, plz, stadt) "
        "VALUES (?,'2026-08-17',?,'Sperrmüll','','Mitte',?,?,'offen',1,"
        "'Teststr','10115',?)",
        (mid, datum, ort[0], ort[1], stadt))


def _stand(db_pfad: Path, stadt: str) -> tuple[int, int, int]:
    """(Zellen gesamt, davon mit genau einer Meldung, davon ab drei).

    Dieselben drei Messpunkte, mit denen der Bestand am 15.08.2026 gegen eine
    Kopie der Betriebsdatenbank vermessen wurde.
    """
    conn = sqlite3.connect(db_pfad)
    werte = tuple(conn.execute(
        "SELECT COUNT(*), "
        "       SUM(CASE WHEN meldungen_count = 1 THEN 1 ELSE 0 END), "
        "       SUM(CASE WHEN meldungen_count >= 3 THEN 1 ELSE 0 END) "
        "FROM hotspots WHERE stadt = ?", (stadt,)).fetchone())
    conn.close()
    return tuple(w or 0 for w in werte)


def _leerer_berliner_lauf(tmp_path, monkeypatch) -> Path:
    """Ein Berliner Lauf, dessen Quelle nichts liefert — der Betriebszustand
    seit dem 22.04.2026."""
    db_pfad = tmp_path / "leer.db"
    monkeypatch.setattr(tracker, "DB_PATH", db_pfad)
    monkeypatch.setattr(tracker, "fetch_meldungen", lambda: [])
    return db_pfad


# ── Der eigentliche Befund T-55 ──────────────────────────────────────────────

def test_leerer_abruf_raeumt_die_eigenen_einzelfall_zellen_trotzdem_auf(
        tmp_path, monkeypatch):
    """Der Kern. Ohne Abruf, ohne Neuaufbau — aber A-2 wird vollzogen."""
    db_pfad = _leerer_berliner_lauf(tmp_path, monkeypatch)
    conn = sqlite3.connect(db_pfad)
    tracker.init_db(conn)
    # Zwei Berliner Einzelfall-Zellen, die es nach A-2 nicht geben duerfte,
    # und eine, die bleiben muss.
    _zelle(conn, BERLIN, "berlin", count=1)
    _zelle(conn, BERLIN_2, "berlin", count=1)
    _meldung(conn, "berlin:1", "2026-01-01", "berlin", KOELN_2)  # Ort egal
    conn.commit()
    conn.close()

    vorher = _stand(db_pfad, "berlin")
    assert tracker.run() == 1, "Ein leerer Abruf bleibt ein Ausfall (H-02b)."
    nachher = _stand(db_pfad, "berlin")

    assert vorher == (2, 2, 0)
    assert nachher == (0, 0, 0), (
        f"Nach einem leeren Berliner Abruf stehen noch {nachher[0]} Berliner "
        f"Zellen im Bestand, davon {nachher[1]} mit einer einzigen Meldung. "
        f"Abhilfe A-2 der Folgenabschaetzung wird also nur vollzogen, solange "
        f"die Schnittstelle antwortet — genau der Fehler aus Befund H-01. "
        f"tracker.run muss _zellen_auflagen_nachziehen auch im Leerpfad rufen."
    )


def test_leerer_abruf_baut_keine_zellen_neu_auf(tmp_path, monkeypatch):
    """Die Gegenprobe, und die Grenze der Abhilfe. Geloescht wird, gerechnet
    nicht — ein Neuaufbau aus einem leeren Abruf ist genau das, was Befund
    H-02b verbietet."""
    db_pfad = _leerer_berliner_lauf(tmp_path, monkeypatch)
    conn = sqlite3.connect(db_pfad)
    tracker.init_db(conn)
    # Drei Meldungen in einer Zelle: eine Neuberechnung WUERDE hier eine Zelle
    # anlegen. Sie darf es nicht.
    for i, tag in enumerate(("2026-01-01", "2026-01-10", "2026-01-20")):
        _meldung(conn, f"berlin:{i}", tag, "berlin", BERLIN)
    conn.commit()
    conn.close()

    assert tracker.run() == 1
    conn = sqlite3.connect(db_pfad)
    zellen = conn.execute("SELECT COUNT(*) FROM hotspots").fetchone()[0]
    conn.close()

    assert zellen == 0, (
        "Der Leerpfad hat Zellen angelegt. Er darf ausschliesslich loeschen — "
        "aus einem leeren Abruf neu zu rechnen hiesse, alten Bestand als "
        "frisch auszugeben (Befund H-02b)."
    )


# ── Die Stadt-Trennung aus T-66 darf dabei nicht aufweichen ──────────────────

def test_leerer_berliner_abruf_laesst_koelner_zellen_stehen(tmp_path, monkeypatch):
    """Die zweite Haelfte. Der Nachlauf im Leerpfad ist eine Loeschung im
    Zellenbestand — genau die Sorte Loeschung, die T-66 stadtscharf gemacht
    hat. Ohne 'AND stadt = ?' raeumte ein leerer Berliner Lauf im Koelner
    Bestand auf, und der Umbau vom 15.08.2026 waere still wieder zurueck."""
    db_pfad = _leerer_berliner_lauf(tmp_path, monkeypatch)
    conn = sqlite3.connect(db_pfad)
    tracker.init_db(conn)
    _zelle(conn, BERLIN, "berlin", count=1)     # faellt: eigene Stadt
    _zelle(conn, KOELN, "koeln", count=1)       # bleibt: fremde Stadt
    _zelle(conn, KOELN_2, "koeln", count=5)     # bleibt ohnehin
    conn.commit()
    conn.close()

    assert tracker.run() == 1
    conn = sqlite3.connect(db_pfad)
    uebrig = dict(conn.execute("SELECT cluster_id, stadt FROM hotspots").fetchall())
    conn.close()

    assert tracker.cluster_id(*BERLIN) not in uebrig, (
        "Die eigene Einzelfall-Zelle muss fallen, sonst ist A-2 folgenlos."
    )
    assert uebrig.get(tracker.cluster_id(*KOELN)) == "koeln", (
        f"Ein leerer BERLINER Lauf hat eine Koelner Zelle entfernt: {uebrig}. "
        f"Die Bereinigung im Leerpfad muss auf die eigene Stadt begrenzt "
        f"bleiben (AND stadt = ?), sonst ist die Trennung aus T-66 im neuen "
        f"Pfad wieder aufgehoben."
    )
    assert uebrig.get(tracker.cluster_id(*KOELN_2)) == "koeln"


def test_leerer_abruf_laesst_fremde_meldungen_und_zellen_zeile_fuer_zeile_gleich(
        tmp_path, monkeypatch):
    """Breitere Fassung derselben Sorge: nach einem leeren Berliner Lauf muss
    der gesamte Koelner Bestand unveraendert sein, Zellen wie Meldungen."""
    db_pfad = _leerer_berliner_lauf(tmp_path, monkeypatch)
    conn = sqlite3.connect(db_pfad)
    tracker.init_db(conn)
    _zelle(conn, KOELN, "koeln", count=1)
    _zelle(conn, KOELN_2, "koeln", count=4)
    _meldung(conn, "koeln:1", "2026-08-01", "koeln", KOELN)
    _meldung(conn, "koeln:2", "2026-08-05", "koeln", KOELN)
    conn.commit()

    def stand():
        return (
            sorted(tuple(r) for r in conn.execute(
                "SELECT cluster_id, meldungen_count, stadt FROM hotspots "
                "WHERE stadt='koeln'")),
            sorted(r[0] for r in conn.execute(
                "SELECT id FROM meldungen WHERE stadt='koeln'")),
        )

    vorher = stand()
    conn.close()

    assert tracker.run() == 1

    conn = sqlite3.connect(db_pfad)
    nachher = stand()
    conn.close()

    assert vorher == nachher, (
        f"Ein leerer Berliner Lauf hat den Koelner Bestand veraendert.\n"
        f"vorher:  {vorher}\nnachher: {nachher}"
    )


# ── A-7 bleibt auch hier stadtblind ──────────────────────────────────────────

def test_leerer_abruf_vollzieht_den_widerspruch_auch_fuer_eine_fremde_stadt(
        tmp_path, monkeypatch):
    """Der Gegenpol, aus demselben Grund wie in test_zellen_stadtscharf.py.

    A-2 ist stadtscharf, A-7 nicht. Eine Zell-Kennung ist eine gerundete
    Koordinate und weltweit eindeutig, die Zweitschrift sperrliste.txt fuehrt
    keine Stadt, und Berlin laeuft nur noch im Leerpfad. Bekaeme die Sperre
    hier einen Stadt-Filter, wuerde ein Widerspruch gegen eine Koelner Zelle
    von diesem Pfad nie vollzogen.

    Zu breit sperren kann nichts offenlegen, zu eng sperren schon.
    """
    db_pfad = _leerer_berliner_lauf(tmp_path, monkeypatch)
    conn = sqlite3.connect(db_pfad)
    tracker.init_db(conn)
    # Genug Meldungen, damit die Zelle NUR ueber die Sperre fallen kann.
    _zelle(conn, KOELN, "koeln", count=9)
    conn.commit()
    sperrliste.eintragen(conn, tracker.cluster_id(*KOELN),
                         quelle="W-2026-101", stadt="koeln")
    conn.commit()
    conn.close()

    assert tracker.run() == 1
    conn = sqlite3.connect(db_pfad)
    uebrig = {r[0] for r in conn.execute("SELECT cluster_id FROM hotspots")}
    conn.close()

    assert tracker.cluster_id(*KOELN) not in uebrig, (
        "Die gesperrte Koelner Zelle steht nach einem leeren Berliner Lauf "
        "noch im Bestand. Damit ist ein Widerspruch nach Art. 21 DSGVO in "
        "diesem Pfad unwirksam. Die Sperr-Loeschung muss stadtblind bleiben — "
        "anders als die Einzelfall-Regel daneben."
    )


def test_leerer_abruf_holt_die_sperre_aus_der_zweitschrift(tmp_path, monkeypatch):
    """Nach einem Rechnerwechsel steht die Sperre nur noch in sperrliste.txt.
    Der Leerpfad muss sie von dort holen, sonst wirkt sie erst wieder, wenn
    irgendeine Quelle antwortet."""
    db_pfad = _leerer_berliner_lauf(tmp_path, monkeypatch)
    datei = tmp_path / "sperrliste.txt"   # von conftest bereits umgebogen
    datei.write_text(
        sperrliste.DATEI_KOPF + f"{tracker.cluster_id(*BERLIN)}  # W-2026-102 | \n",
        encoding="utf-8")

    conn = sqlite3.connect(db_pfad)
    tracker.init_db(conn)
    _zelle(conn, BERLIN, "berlin", count=9)
    conn.commit()
    conn.close()

    assert tracker.run() == 1
    conn = sqlite3.connect(db_pfad)
    uebrig = {r[0] for r in conn.execute("SELECT cluster_id FROM hotspots")}
    conn.close()

    assert tracker.cluster_id(*BERLIN) not in uebrig, (
        "Die aus der Zweitschrift wiederhergestellte Sperre hat im Leerpfad "
        "nicht gegriffen. _zellen_auflagen_nachziehen muss sperrliste.laden() "
        "selbst rufen — es gibt hier keinen vorherigen Aufruf, der das tut."
    )


# ── Der Nachlauf steht in genau einer Fassung ────────────────────────────────

def test_beide_pfade_benutzen_dieselbe_bereinigung(tmp_path, monkeypatch):
    """Sicherung gegen die naheliegende Verschlimmbesserung: den DELETE im
    Leerpfad noch einmal hinzuschreiben statt die Funktion zu rufen. Dann
    laufen die beiden Wege beim naechsten Eingriff auseinander.

    Geprueft wird verhaltensgleich, nicht ueber den Quelltext: wer
    _zellen_auflagen_nachziehen ausser Kraft setzt, muss BEIDE Pfade
    stilllegen. Bleibt einer aktiv, hat er eine eigene Kopie der Regel.
    """
    db_pfad = tmp_path / "doppelt.db"
    monkeypatch.setattr(tracker, "DB_PATH", db_pfad)
    monkeypatch.setattr(tracker, "_zellen_auflagen_nachziehen",
                        lambda conn, stadt: {"einzelfall": 0, "gesperrt": 0})

    conn = sqlite3.connect(db_pfad)
    tracker.init_db(conn)
    _zelle(conn, BERLIN_2, "berlin", count=1)
    _meldung(conn, "berlin:1", "2026-01-01", "berlin", BERLIN)
    _meldung(conn, "berlin:2", "2026-01-10", "berlin", BERLIN)
    # Die Einzelfall-Zelle braucht ihre eine Meldung, sonst faellt sie schon
    # ueber die Verwaisten-Regel und der Test misst die falsche Loeschung.
    _meldung(conn, "berlin:3", "2026-02-01", "berlin", BERLIN_2)
    conn.commit()

    # Pfad 1: der volle Lauf ueber berechne_hotspots
    conn.row_factory = sqlite3.Row
    tracker.berechne_hotspots(conn, quellen.hole("berlin"))
    nach_vollem_lauf = conn.execute(
        "SELECT COUNT(*) FROM hotspots WHERE meldungen_count = 1").fetchone()[0]
    conn.close()

    # Pfad 2: der leere Abruf
    monkeypatch.setattr(tracker, "fetch_meldungen", lambda: [])
    assert tracker.run() == 1
    conn = sqlite3.connect(db_pfad)
    nach_leerem_lauf = conn.execute(
        "SELECT COUNT(*) FROM hotspots WHERE meldungen_count = 1").fetchone()[0]
    conn.close()

    assert nach_vollem_lauf == 1 and nach_leerem_lauf == 1, (
        f"Mit stillgelegter _zellen_auflagen_nachziehen raeumt noch immer "
        f"jemand auf (voller Lauf: {nach_vollem_lauf}, leerer Lauf: "
        f"{nach_leerem_lauf}, erwartet je 1 stehengebliebene Zelle). Einer der "
        f"beiden Pfade hat also eine eigene Kopie der A-2-Loeschung statt die "
        f"gemeinsame Funktion zu rufen."
    )
