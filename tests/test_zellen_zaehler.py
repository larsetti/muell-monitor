"""
Tests zu T-75: der Zähler einer Zelle muss der Löschung folgen
===============================================================
`hotspots.meldungen_count` sagt, auf wie vielen Meldungen eine Kartenzelle
beruht. Geschrieben wurde der Wert ausschließlich in `berechne_hotspots`, und
dorthin kommt eine Stadt ohne antwortende Quelle nie (Leerpfad, Befund H-02b).
Die Löschroutine dagegen läuft bei JEDEM Lauf und über ALLE Städte (A-4, Befund
H-01). Der Bestand schrumpfte also, während der Zähler stehenblieb.

GEMESSEN am 17.08.2026 gegen eine Kopie der Betriebsdatenbank, mit
`tracker.cluster_id` und nicht mit nachgebauter Rundung: 555 der 5.839 Berliner
Zellen nannten mehr Meldungen, als es sie noch gab, drei von ihnen hatten gar
keine mehr. 43 davon standen nur noch über den zu hohen Zähler oberhalb der
k-Anonymitäts-Schwelle und wurden veröffentlicht, obwohl sie sie nicht mehr
erreichen. Köln, dessen Quelle antwortet, war zellgenau richtig — was den
Befund bestätigt statt ihn zu entkräften: dort wird eben neu gerechnet.

Es ist derselbe Fehlertyp wie T-55 und wie Befund H-01: eine Regel, die nur
beim Neuberechnen stimmt, ist bei einer toten Quelle dauerhaft falsch.

DIESE DATEI PRÜFT DREI DINGE ZUSAMMEN, weil jedes davon für sich zu haben wäre,
indem man eines der anderen kaputtmacht:
  1. der Zähler folgt der Löschung nach unten, samt Score und Daten,
  2. er steigt dabei NIE (H-02b: aus altem Bestand wird nichts Neues gerechnet),
  3. die Stadt-Trennung aus T-66 bleibt, und die Bremse gegen einen
     Massenabgang greift.
"""

import sqlite3
import sys
from pathlib import Path

TECHNIK = Path(__file__).parent.parent
sys.path.insert(0, str(TECHNIK))

import export_html  # noqa: E402
import quellen  # noqa: E402
import tracker  # noqa: E402

BERLIN = (52.5200, 13.4050)
BERLIN_2 = (52.4800, 13.3500)
KOELN = (50.9375, 6.9603)

TAGE = ["2026-01-05", "2026-02-05", "2026-03-05", "2026-04-05", "2026-05-05"]


def _zelle(conn, ort, stadt: str, count: int, first="2020-01-01",
           last="2020-01-02", score=99.0, label="kritisch"):
    """Eine Zelle mit frei gesetzten Werten — so, wie sie nach einem alten Lauf
    im Bestand steht und seitdem nicht mehr angefasst wurde."""
    conn.execute(
        "INSERT INTO hotspots (cluster_id, lat_center, lon_center, bezirk, "
        "meldungen_count, recurrence_count, last_seen, first_seen, score, "
        "score_label, strasse, plz, stadt) "
        "VALUES (?,?,?,'Mitte',?,7,?,?,?,?,'Teststr','10115',?)",
        (tracker.cluster_id(*ort), ort[0], ort[1], count, last, first,
         score, label, stadt))


def _meldung(conn, mid: str, datum: str, stadt: str, ort):
    conn.execute(
        "INSERT INTO meldungen (id, fetched_at, datum, kategorie, betreff, bezirk, "
        "lat, lon, status, is_muell, strasse, plz, stadt) "
        "VALUES (?,'2026-08-17',?,'Sperrmüll','','Mitte',?,?,'offen',1,"
        "'Teststr','10115',?)",
        (mid, datum, ort[0], ort[1], stadt))


def _leerer_berliner_lauf(tmp_path, monkeypatch) -> Path:
    """Der Betriebszustand seit dem 22.04.2026: Berlin ruft ab, es kommt
    nichts, run() geht den Leerpfad."""
    db_pfad = tmp_path / "zaehler.db"
    monkeypatch.setattr(tracker, "DB_PATH", db_pfad)
    monkeypatch.setattr(tracker, "fetch_meldungen", lambda: [])
    return db_pfad


def _zellwert(db_pfad: Path, ort, feld="meldungen_count"):
    conn = sqlite3.connect(db_pfad)
    zeile = conn.execute(
        f"SELECT {feld} FROM hotspots WHERE cluster_id = ?",
        (tracker.cluster_id(*ort),)).fetchone()
    conn.close()
    return zeile[0] if zeile else None


# ── Der eigentliche Befund T-75 ──────────────────────────────────────────────

def test_zaehler_folgt_der_loeschung_auch_ohne_abruf(tmp_path, monkeypatch):
    """Der Kern. Fünf Meldungen standen in der Zelle, zwei sind noch da — dann
    muss die Zelle zwei sagen, nicht fünf."""
    db_pfad = _leerer_berliner_lauf(tmp_path, monkeypatch)
    conn = sqlite3.connect(db_pfad)
    tracker.init_db(conn)
    _zelle(conn, BERLIN, "berlin", count=5)
    # Was die Löschroutine übriggelassen hat.
    for i, tag in enumerate(TAGE[:2]):
        _meldung(conn, f"berlin:{i}", tag, "berlin", BERLIN)
    conn.commit()
    conn.close()

    assert tracker.run() == 1, "Ein leerer Abruf bleibt ein Ausfall (H-02b)."

    assert _zellwert(db_pfad, BERLIN) == 2, (
        f"Die Zelle nennt weiter {_zellwert(db_pfad, BERLIN)} Meldungen, im "
        f"Bestand liegen 2. meldungen_count wird nur in berechne_hotspots "
        f"geschrieben, und dorthin kommt eine Stadt ohne antwortende Quelle "
        f"nie — die Karte behauptet damit dauerhaft mehr, als der Bestand "
        f"hergibt. Der Leerpfad muss die Zähler nach unten angleichen."
    )


def test_score_und_daten_folgen_dem_gesenkten_zaehler(tmp_path, monkeypatch):
    """Nur den Zähler nachzuführen hieße, den Fehler zu verschieben: Score,
    Wiederkehr und die beiden Daten hängen an derselben Meldungsmenge. Bei
    first_seen ist genau das unter M-02 schon einmal aufgefallen — es stand ein
    Meldedatum in der Zelle, zu dem es keine Meldung mehr gab."""
    db_pfad = _leerer_berliner_lauf(tmp_path, monkeypatch)
    conn = sqlite3.connect(db_pfad)
    tracker.init_db(conn)
    _zelle(conn, BERLIN, "berlin", count=5, first="2020-01-01",
           last="2020-12-31", score=99.0, label="kritisch")
    for i, tag in enumerate(TAGE[2:]):        # 2026-03-05 bis 2026-05-05
        _meldung(conn, f"berlin:{i}", tag, "berlin", BERLIN)
    conn.commit()
    conn.close()

    assert tracker.run() == 1
    assert _zellwert(db_pfad, BERLIN) == 3
    assert _zellwert(db_pfad, BERLIN, "first_seen") == "2026-03-05", (
        "first_seen zeigt weiter auf eine Meldung, die es nicht mehr gibt — "
        "wortgleich der Fehler M-02, nur eine Schicht später."
    )
    assert _zellwert(db_pfad, BERLIN, "last_seen") == "2026-05-05"
    assert _zellwert(db_pfad, BERLIN, "recurrence_count") == 0, (
        "Der Wiederkehr-Zähler stammt noch aus dem alten Bestand. Die drei "
        "übrigen Meldungen liegen einen Monat auseinander, das Berliner "
        "Fenster sind 21 Tage — es kann keine Wiederkehr geben."
    )
    assert _zellwert(db_pfad, BERLIN, "score") < 99.0, (
        "Der Score steht noch auf dem Wert der gelöschten Meldungen. Damit "
        "wäre die Zelle weiter 'kritisch' und stünde in der Karte oben."
    )


def test_zelle_ohne_jede_meldung_verschwindet(tmp_path, monkeypatch):
    """Drei solcher Zellen lagen am 17.08.2026 im Berliner Bestand. Eine Zelle
    ohne Meldung behauptet einen Ort, für den es keine Grundlage mehr gibt."""
    db_pfad = _leerer_berliner_lauf(tmp_path, monkeypatch)
    conn = sqlite3.connect(db_pfad)
    tracker.init_db(conn)
    _zelle(conn, BERLIN, "berlin", count=9)
    _meldung(conn, "berlin:1", TAGE[0], "berlin", BERLIN_2)   # anderer Ort
    _meldung(conn, "berlin:2", TAGE[1], "berlin", BERLIN_2)
    conn.commit()
    conn.close()

    assert tracker.run() == 1
    assert _zellwert(db_pfad, BERLIN) is None, (
        "Eine Zelle ohne eine einzige zugehörige Meldung steht weiter im "
        "Bestand und wird veröffentlicht."
    )


def test_a2_greift_erst_nach_der_angleichung(tmp_path, monkeypatch):
    """Die Reihenfolge im Nachlauf ist tragend. Die A-2-Löschung prüft
    'meldungen_count < 2' — sie liest also den gespeicherten Zähler. Steht der
    zu hoch, greift die Auflage genau bei den Zellen nicht, bei denen sie
    greifen müsste. 34 Berliner Zellen waren am 17.08.2026 in diesem Zustand."""
    db_pfad = _leerer_berliner_lauf(tmp_path, monkeypatch)
    conn = sqlite3.connect(db_pfad)
    tracker.init_db(conn)
    _zelle(conn, BERLIN, "berlin", count=8)
    _meldung(conn, "berlin:1", TAGE[0], "berlin", BERLIN)     # nur noch eine
    conn.commit()
    conn.close()

    assert tracker.run() == 1
    assert _zellwert(db_pfad, BERLIN) is None, (
        "Die Zelle beruht auf einer einzigen Meldung und steht trotzdem noch "
        "im Bestand. A-2 der Folgenabschätzung ist damit folgenlos, solange "
        "der alte Zähler sie über der Schwelle hält."
    )


def test_zelle_unter_der_schwelle_faellt_aus_der_karte(tmp_path, monkeypatch):
    """Was der Befund für die Veröffentlichung heißt. export_html filtert mit
    'meldungen_count >= 3' — auf demselben Zähler. 43 Berliner Zellen standen
    am 17.08.2026 nur noch über ihren zu hohen Zähler auf der Karte."""
    db_pfad = _leerer_berliner_lauf(tmp_path, monkeypatch)
    conn = sqlite3.connect(db_pfad)
    tracker.init_db(conn)
    _zelle(conn, BERLIN, "berlin", count=6)
    for i, tag in enumerate(TAGE[:2]):       # tatsächlich nur noch zwei
        _meldung(conn, f"berlin:{i}", tag, "berlin", BERLIN)
    conn.commit()
    conn.close()

    assert tracker.run() == 1

    monkeypatch.setattr(export_html, "DB_PATH", db_pfad)
    veroeffentlicht = [h["cluster_id"]
                       for h in export_html.load_data("berlin")["hotspots"]]
    assert tracker.cluster_id(*BERLIN) not in veroeffentlicht, (
        f"Die Zelle beruht auf zwei Meldungen und steht trotzdem auf der "
        f"Karte: {veroeffentlicht}. Die k-Anonymitäts-Schwelle hängt am selben "
        f"Zähler wie A-2 — ein zu hoher Zähler hebt beide auf."
    )


# ── Die Gegenrichtung: der Zähler darf nie steigen (H-02b) ───────────────────

def test_zaehler_steigt_nie_ohne_abruf(tmp_path, monkeypatch):
    """Die Grenze der Abhilfe, und der Grund für das 'AND meldungen_count > ?'
    in jedem UPDATE. Nach unten anzugleichen ist eine Löschung im weiteren
    Sinn und kann nichts offenlegen. Nach oben zu rechnen hieße, aus altem
    Bestand etwas Neues zu machen und als frisch auszugeben — genau das
    verbietet H-02b."""
    db_pfad = _leerer_berliner_lauf(tmp_path, monkeypatch)
    conn = sqlite3.connect(db_pfad)
    tracker.init_db(conn)
    _zelle(conn, BERLIN, "berlin", count=3, first="2026-01-05",
           last="2026-01-05", score=5.0, label="mittel")
    for i, tag in enumerate(TAGE):           # fünf im Bestand, drei in der Zelle
        _meldung(conn, f"berlin:{i}", tag, "berlin", BERLIN)
    conn.commit()
    conn.close()

    assert tracker.run() == 1
    assert _zellwert(db_pfad, BERLIN) == 3, (
        "Der Leerpfad hat den Zähler angehoben. Er darf ausschließlich senken."
    )
    assert _zellwert(db_pfad, BERLIN, "score") == 5.0, (
        "Auch die abgeleiteten Werte dürfen ohne Abruf nicht nach oben "
        "gerechnet werden — sonst steht ein frisch berechneter Score auf "
        "einer Zelle, die seit Monaten kein neues Datum gesehen hat."
    )


def test_leerpfad_legt_weiter_keine_zelle_an(tmp_path, monkeypatch):
    """Die zweite Hälfte derselben Grenze, hier gegen die Angleichung selbst
    gerichtet: sie schreibt nur vorhandene Zellen fort. Eine Meldungsmenge
    ohne Zelle bleibt ohne Zelle (siehe test_leerer_abruf.py)."""
    db_pfad = _leerer_berliner_lauf(tmp_path, monkeypatch)
    conn = sqlite3.connect(db_pfad)
    tracker.init_db(conn)
    for i, tag in enumerate(TAGE):
        _meldung(conn, f"berlin:{i}", tag, "berlin", BERLIN)
    conn.commit()
    conn.close()

    assert tracker.run() == 1
    conn = sqlite3.connect(db_pfad)
    zellen = conn.execute("SELECT COUNT(*) FROM hotspots").fetchone()[0]
    conn.close()
    assert zellen == 0, (
        "Die Angleichung hat eine Zelle angelegt. Sie darf ausschließlich "
        "vorhandene fortschreiben (H-02b)."
    )


# ── Stadt-Trennung (T-66) und Bremse ─────────────────────────────────────────

def test_fremde_stadt_bleibt_bei_der_angleichung_unangetastet(tmp_path, monkeypatch):
    """Die Angleichung kann eine Zelle unter die A-2-Schwelle drücken und damit
    löschen lassen. Genau deshalb ist sie stadtscharf wie die Löschung daneben:
    Berlins Zellen sind nicht wieder aufbaubar (T-66)."""
    db_pfad = _leerer_berliner_lauf(tmp_path, monkeypatch)
    conn = sqlite3.connect(db_pfad)
    tracker.init_db(conn)
    _zelle(conn, KOELN, "koeln", count=7)        # Zähler zu hoch, fremde Stadt
    _meldung(conn, "koeln:1", TAGE[0], "koeln", KOELN)
    _meldung(conn, "koeln:2", TAGE[1], "koeln", KOELN)
    _zelle(conn, BERLIN, "berlin", count=7)      # eigene Stadt, wird angeglichen
    for i, tag in enumerate(TAGE[:2]):
        _meldung(conn, f"berlin:{i}", tag, "berlin", BERLIN)
    conn.commit()
    conn.close()

    assert tracker.run() == 1
    assert _zellwert(db_pfad, KOELN) == 7, (
        "Ein leerer BERLINER Lauf hat den Kölner Zähler angefasst. Jede Stadt "
        "gleicht bei ihrem eigenen Lauf an — die Trennung aus T-66 gilt auch "
        "für den neuen Schreibweg."
    )
    assert _zellwert(db_pfad, BERLIN) == 2


def test_bremse_haelt_einen_massenabgang_auf(tmp_path, monkeypatch, caplog):
    """Ein Bestand, in dem plötzlich fast nichts mehr steht, ist eher eine halb
    eingespielte Sicherung als eine echte Löschung. Dann wird nichts
    angeglichen und der Lauf sagt es laut — sonst wären die Zellen weg, und
    Berliner Zellen kommen nicht wieder (T-66)."""
    db_pfad = _leerer_berliner_lauf(tmp_path, monkeypatch)
    conn = sqlite3.connect(db_pfad)
    tracker.init_db(conn)
    for i in range(200):
        ort = (52.50 + i * 0.01, 13.40)
        _zelle(conn, ort, "berlin", count=5)
    # Kein einziger Meldungsbestand dazu: alle 200 fielen auf null.
    conn.commit()
    conn.close()

    with caplog.at_level("ERROR"):
        assert tracker.run() == 1
    conn = sqlite3.connect(db_pfad)
    uebrig = conn.execute(
        "SELECT COUNT(*) FROM hotspots WHERE meldungen_count = 5").fetchone()[0]
    conn.close()

    assert uebrig == 200, (
        f"Nur noch {uebrig} von 200 Zellen tragen ihren alten Zähler. Die "
        f"Bremse aus ZELLEN_MAX_ABGANG_ANTEIL hat den Massenabgang nicht "
        f"aufgehalten."
    )
    assert any("Angleichung" in s and "abgebrochen" in s
               for s in caplog.messages), (
        f"Der Abbruch steht nicht im Protokoll: {caplog.messages}. Eine "
        f"Bremse, die still greift, ist keine."
    )


# ── Beide Pfade rechnen dasselbe ─────────────────────────────────────────────

def test_angleichung_ist_nach_einem_vollen_lauf_folgenlos(tmp_path, monkeypatch):
    """Die Probe darauf, dass es die Rechnung nur in einer Fassung gibt. Nach
    berechne_hotspots muss die Angleichung nichts mehr zu tun finden — sonst
    leiten die beiden Wege verschiedene Werte aus demselben Bestand ab."""
    db_pfad = tmp_path / "voll.db"
    monkeypatch.setattr(tracker, "DB_PATH", db_pfad)
    conn = sqlite3.connect(db_pfad)
    conn.row_factory = sqlite3.Row
    tracker.init_db(conn)
    for i, tag in enumerate(TAGE):
        _meldung(conn, f"berlin:{i}", tag, "berlin", BERLIN)
    for i, tag in enumerate(TAGE[:3]):
        _meldung(conn, f"berlin:1{i}", tag, "berlin", BERLIN_2)
    conn.commit()

    tracker.berechne_hotspots(conn, quellen.hole("berlin"))
    ergebnis = tracker._zaehler_angleichen(conn, "berlin")
    conn.close()

    assert ergebnis["angeglichen"] == 0, (
        f"Die Angleichung hat nach einem vollen Lauf noch "
        f"{ergebnis['angeglichen']} Zellen gesenkt. Beide Wege müssen aus "
        f"demselben Bestand dieselben Werte ableiten — sonst gibt es die "
        f"Rechnung doch in zwei Fassungen."
    )
