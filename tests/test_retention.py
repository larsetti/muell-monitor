"""
Tests fuer die Loeschroutine (Abhilfe A-4 der DSFA vom 28.07.2026)
==================================================================
Vor dieser Aenderung gab es UEBERHAUPT KEIN DELETE in der Pipeline; die
Speicherbegrenzung nach Art. 5 Abs. 1 lit. e DSGVO war nicht erfuellt.

Geprueft werden beide Fristen aus der DSFA:
- 30 Tage nach Wegfall aus der Quelle -> Meldung wird geloescht
- 24 Monate Alter -> Reduktion auf nicht personenbezogene Monats-Aggregate
plus die Schutzvorrichtung, die einen unvollstaendigen Abruf nicht als
Massenwegfall missdeutet.
"""

import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

TECHNIK = Path(__file__).parent.parent
sys.path.insert(0, str(TECHNIK))

import retention  # noqa: E402
import tracker  # noqa: E402

JETZT = datetime(2026, 7, 29, 12, 0, 0)


def _db(tmp_path: Path, name: str = "r.db") -> sqlite3.Connection:
    conn = sqlite3.connect(tmp_path / name)
    tracker.init_db(conn)
    return conn


def _meldung(conn, mid: str, datum: str, quelle_weg_seit: str = None,
             lat: float = 52.5, lon: float = 13.4, kategorie: str = "Sperrmüll",
             betreff: str = ""):
    conn.execute(
        "INSERT INTO meldungen (id, fetched_at, datum, kategorie, betreff, bezirk, "
        "lat, lon, status, is_muell, strasse, plz, quelle_weg_seit) "
        "VALUES (?,?,?,?,?,'Mitte',?,?,'offen',1,'Teststr','10115',?)",
        (mid, JETZT.isoformat(), datum, kategorie, betreff, lat, lon, quelle_weg_seit))
    conn.commit()


def _ids(conn) -> set:
    return {r[0] for r in conn.execute("SELECT id FROM meldungen")}


# ── Frist 1: Wegfall aus der Quelle (30 Tage) ────────────────────────────────

def test_a4_meldung_wird_30_tage_nach_quellabgang_geloescht(tmp_path):
    conn = _db(tmp_path)
    _meldung(conn, "alt", "2026-06-01", (JETZT - timedelta(days=31)).isoformat())
    _meldung(conn, "frisch_weg", "2026-06-01", (JETZT - timedelta(days=29)).isoformat())
    _meldung(conn, "in_quelle", "2026-06-01", None)

    anzahl = retention.loesche_quellabgang(conn, JETZT)

    assert anzahl == 1, f"Genau eine Meldung ist ueberfaellig, gezaehlt {anzahl}"
    assert _ids(conn) == {"frisch_weg", "in_quelle"}


def test_a4_frist_ist_konfigurierbar(tmp_path):
    """Eine abweichende Frist greift ohne Codeaenderung (Vorgabe des Anwalts)."""
    conn = _db(tmp_path)
    _meldung(conn, "x", "2026-06-01", (JETZT - timedelta(days=10)).isoformat())

    assert retention.loesche_quellabgang(conn, JETZT, frist_tage=30) == 0
    assert retention.loesche_quellabgang(conn, JETZT, frist_tage=7) == 1
    assert _ids(conn) == set()


def test_a4_dry_run_loescht_nichts(tmp_path):
    conn = _db(tmp_path)
    _meldung(conn, "alt", "2026-06-01", (JETZT - timedelta(days=99)).isoformat())

    assert retention.loesche_quellabgang(conn, JETZT, dry_run=True) == 1
    assert _ids(conn) == {"alt"}, "Dry-Run darf den Bestand nicht anfassen"


def test_a4_quellpraesenz_markiert_fehlende_meldungen(tmp_path):
    # Bestand gross genug, dass der Massen-Abgangs-Schutz aus H-02 nicht greift:
    # eine fehlende Meldung von zehn sind 10 Prozent, erlaubt sind 20.
    conn = _db(tmp_path)
    for i in range(9):
        _meldung(conn, f"bleibt{i}", "2026-06-01")
    _meldung(conn, "verschwunden", "2026-06-01")

    ergebnis = retention.markiere_quellpraesenz(
        conn, [f"bleibt{i}" for i in range(9)], JETZT.isoformat())

    assert ergebnis["abgebrochen"] is False
    assert ergebnis["in_quelle"] == 9
    assert ergebnis["neu_als_weggefallen_markiert"] == 1
    zeilen = dict(conn.execute("SELECT id, quelle_weg_seit FROM meldungen").fetchall())
    assert zeilen["bleibt0"] is None
    assert zeilen["verschwunden"] == JETZT.isoformat()


def test_a4_rueckkehr_in_die_quelle_hebt_markierung_auf(tmp_path):
    """Taucht eine Meldung wieder auf, laeuft die Frist nicht weiter."""
    conn = _db(tmp_path)
    _meldung(conn, "zurueck", "2026-06-01", (JETZT - timedelta(days=20)).isoformat())

    retention.markiere_quellpraesenz(conn, ["zurueck"], JETZT.isoformat())

    weg = conn.execute("SELECT quelle_weg_seit FROM meldungen WHERE id='zurueck'").fetchone()[0]
    assert weg is None, "Wegfall-Markierung muss geloescht werden"
    assert retention.loesche_quellabgang(conn, JETZT + timedelta(days=15)) == 0


def test_a4_markierung_ist_idempotent(tmp_path):
    """Ein zweiter Abruf verschiebt den Beginn der Frist nicht nach hinten."""
    conn = _db(tmp_path)
    for i in range(9):
        _meldung(conn, f"bleibt{i}", "2026-06-01")
    _meldung(conn, "weg", "2026-06-01")
    im_abruf = [f"bleibt{i}" for i in range(9)]

    retention.markiere_quellpraesenz(conn, im_abruf, JETZT.isoformat())
    spaeter = (JETZT + timedelta(days=10)).isoformat()
    retention.markiere_quellpraesenz(conn, im_abruf, spaeter)

    weg = conn.execute("SELECT quelle_weg_seit FROM meldungen WHERE id='weg'").fetchone()[0]
    assert weg == JETZT.isoformat(), (
        "Der Fristbeginn muss der erste Abruf ohne die Meldung bleiben"
    )


# ── Schutz gegen Fehl-Loeschung bei unvollstaendigem Abruf ───────────────────

def test_a4_halber_abruf_gilt_nicht_als_vollstaendig(tmp_path):
    """Liefert die Schnittstelle nur einen Bruchteil, darf die Abwesenheit
    einer Meldung NICHT als Wegfall gewertet werden — sonst wuerde ein
    Teilausfall den halben Bestand zur Loeschung vormerken."""
    conn = _db(tmp_path)
    conn.execute("INSERT INTO fetch_log (fetched_at, count_total, count_new, count_muell) "
                 "VALUES (?, 82780, 0, 0)", (JETZT.isoformat(),))
    conn.commit()

    ok, _ = retention.feed_vollstaendig(conn, 100)
    assert not ok, "100 von zuletzt 82.780 darf nicht als vollstaendig gelten"
    ok, _ = retention.feed_vollstaendig(conn, 80000)
    assert ok, "80.000 von 82.780 ist ein normaler Abruf"


def test_a4_frische_datenbank_braucht_mindestumfang(tmp_path):
    conn = _db(tmp_path)
    assert not retention.feed_vollstaendig(conn, 5)[0]
    assert retention.feed_vollstaendig(conn, 50000)[0]


# ── Frist 2: Reduktion auf Aggregate nach 24 Monaten ─────────────────────────

def test_a4_altbestand_wird_auf_aggregate_reduziert(tmp_path):
    """Vier Sperrmuell-Meldungen im selben Monat und derselben Zelle ueberleben
    als eine Aggregat-Zeile; die einzelne Bauschutt-Meldung faellt unter die
    Schwelle aus M-01 und verschwindet ersatzlos."""
    conn = _db(tmp_path)
    for i in range(4):
        _meldung(conn, f"uralt{i}", f"2023-05-1{i}", kategorie="Sperrmüll")
    _meldung(conn, "einzeln", "2023-05-24", kategorie="Bauschutt")
    _meldung(conn, "jung", "2026-06-01")

    anzahl = retention.aggregiere_altbestand(conn, JETZT)

    assert anzahl == 5, "alle fuenf Altmeldungen werden verarbeitet"
    assert _ids(conn) == {"jung"}, "Meldungen ueber 24 Monate muessen weg sein"

    aggregat = conn.execute(
        "SELECT cluster_id, jahr_monat, kategorie_gruppe, anzahl "
        "FROM meldungen_aggregat ORDER BY kategorie_gruppe").fetchall()
    assert aggregat == [
        (tracker.cluster_id(52.5, 13.4), "2023-05", "sperrmüll", 4),
    ], f"Aggregat war {aggregat}"


def test_a4_aggregat_traegt_weder_freitext_noch_tagesdatum(tmp_path):
    """Das, was nach 24 Monaten uebrig bleibt, darf keinen Personenbezug mehr
    tragen: kein Betreff, kein Tagesdatum, keine gebaeudescharfe Koordinate."""
    conn = _db(tmp_path)
    # drei Meldungen, damit die Zeile die Schwelle aus M-01 ueberlebt
    for i in range(3):
        _meldung(conn, f"u{i}", f"2023-03-1{i+5}", lat=52.5012345, lon=13.4098765,
                 betreff="Sperrmüll vor dem Haus von Familie Meier")

    retention.aggregiere_altbestand(conn, JETZT)

    zeile = conn.execute("SELECT * FROM meldungen_aggregat").fetchone()
    text = " ".join(str(f) for f in zeile)
    assert "Meier" not in text
    assert "2023-03-17" not in text and "2023-03" in text, "nur Monatsgenauigkeit"
    assert "52.5012345" not in text and "13.4098765" not in text, "nur Rasterzelle"
    spalten = [r[1] for r in conn.execute("PRAGMA table_info(meldungen_aggregat)")]
    assert "betreff" not in spalten and "strasse" not in spalten


def test_a4_aggregat_summiert_ueber_mehrere_laeufe(tmp_path):
    """Ein zweiter Lauf addiert auf eine bestehende Zeile, statt sie zu
    ueberschreiben — sofern die Zeile die Schwelle aus M-01 erreicht hat."""
    conn = _db(tmp_path)
    for i in range(3):
        _meldung(conn, f"a{i}", f"2023-05-1{i}")
    retention.aggregiere_altbestand(conn, JETZT)
    _meldung(conn, "b", "2023-05-19")
    retention.aggregiere_altbestand(conn, JETZT)

    summe = conn.execute("SELECT SUM(anzahl) FROM meldungen_aggregat").fetchone()[0]
    assert summe == 4, f"Aggregat muss 4 zaehlen, war {summe}"


def test_m01_unterschwellige_eimer_wachsen_nicht_ueber_laeufe(tmp_path):
    """Dokumentierte Nebenwirkung der Schwelle aus M-01: ein Eimer, der erst
    ueber mehrere Laeufe auf drei anwachsen wuerde, wird zwischendurch
    verworfen und faengt wieder bei eins an. Das trifft nur nachtraeglich
    eintreffende Altmeldungen und faellt zugunsten der Datensparsamkeit aus.
    """
    conn = _db(tmp_path)
    for tag, kennung in enumerate(["x", "y", "z"], start=1):
        _meldung(conn, kennung, f"2023-05-0{tag}")
        retention.aggregiere_altbestand(conn, JETZT)

    zeilen = conn.execute("SELECT anzahl FROM meldungen_aggregat").fetchall()
    uebrig = _ids(conn)
    conn.close()
    assert zeilen == [], f"Kein Eimer erreicht die Schwelle, gefunden: {zeilen}"
    assert uebrig == set(), "Die Meldungen selbst sind trotzdem alle geloescht"


def test_a4_zweiter_lauf_ohne_neue_daten_aendert_nichts(tmp_path):
    conn = _db(tmp_path)
    _meldung(conn, "a", "2023-05-10")
    retention.aggregiere_altbestand(conn, JETZT)
    vorher = conn.execute("SELECT * FROM meldungen_aggregat").fetchall()

    assert retention.aggregiere_altbestand(conn, JETZT) == 0
    assert conn.execute("SELECT * FROM meldungen_aggregat").fetchall() == vorher


def test_a4_monatsrechnung_ueber_jahresgrenze():
    assert retention._monate_zurueck(datetime(2026, 7, 29), 24) == datetime(2024, 7, 29)
    assert retention._monate_zurueck(datetime(2026, 1, 15), 2) == datetime(2025, 11, 15)
    # 31. August minus 6 Monate landet im Februar und darf nicht ueberlaufen
    assert retention._monate_zurueck(datetime(2026, 8, 31), 6) == datetime(2026, 2, 28)


# ── Zusammenspiel mit der Pipeline ───────────────────────────────────────────

def test_a4_gesamtlauf_meldet_beide_fristen(tmp_path):
    conn = _db(tmp_path)
    _meldung(conn, "quellweg", "2026-06-01", (JETZT - timedelta(days=40)).isoformat())
    _meldung(conn, "uralt", "2023-01-01")

    ergebnis = retention.anwenden(conn, JETZT)

    assert ergebnis["quellabgang_geloescht"] == 1
    assert ergebnis["altbestand_aggregiert"] == 1
    assert ergebnis["frist_quelle_tage"] == 30, "Frist aus der DSFA"
    assert ergebnis["frist_aggregat_monate"] == 24, "Frist aus der DSFA"
    assert _ids(conn) == set()


def test_a4_verwaiste_zelle_verschwindet_nach_der_loeschung(tmp_path, monkeypatch):
    """Loescht die Routine alle Meldungen einer Zelle, darf die Zelle mit
    Strasse und Koordinate nicht in der Datenbank zurueckbleiben."""
    db_pfad = tmp_path / "verwaist.db"
    conn = sqlite3.connect(db_pfad)
    tracker.init_db(conn)
    conn.execute(
        "INSERT INTO hotspots (cluster_id, lat_center, lon_center, bezirk, "
        "meldungen_count, recurrence_count, last_seen, first_seen, score, "
        "score_label, strasse, plz) VALUES (?,52.6,13.5,'Mitte',7,0,'2023-01-01',"
        "'2023-01-01',7.0,'mittel','Alte Straße','10115')",
        (tracker.cluster_id(52.6, 13.5),))
    conn.commit()
    conn.close()

    monkeypatch.setattr(tracker, "DB_PATH", db_pfad)
    monkeypatch.setattr(tracker, "fetch_meldungen", lambda: [
        {"id": "n1", "kategorie": "Sperrmüll", "betreff": "", "bezirk": "Mitte",
         "lat": 52.5, "lon": 13.4, "status": "offen", "erstellungsDatum": "01.01.2026",
         "strasse": "Neustr", "plz": "10000"},
        {"id": "n2", "kategorie": "Sperrmüll", "betreff": "", "bezirk": "Mitte",
         "lat": 52.5001, "lon": 13.4001, "status": "offen",
         "erstellungsDatum": "05.01.2026", "strasse": "Neustr", "plz": "10000"},
    ])
    assert tracker.run() == 0

    conn = sqlite3.connect(db_pfad)
    uebrig = [r[0] for r in conn.execute("SELECT cluster_id FROM hotspots")]
    conn.close()
    assert tracker.cluster_id(52.6, 13.5) not in uebrig, (
        f"Verwaiste Zelle blieb stehen: {uebrig}"
    )


# ── Findings aus dem Nachaudit vom 29.07.2026 ────────────────────────────────

def test_h01_fristen_laufen_auch_bei_totem_abruf(tmp_path, monkeypatch):
    """H-01: Die Loeschung darf nicht daran haengen, dass die Schnittstelle
    erreichbar ist. Vorher stand retention.anwenden() hinter dem Abbruch bei
    leerem Abruf — waehrend des Ausfalls seit April 2026 lief sie 98 Tage nie.
    """
    db_pfad = tmp_path / "h01.db"
    conn = sqlite3.connect(db_pfad)
    tracker.init_db(conn)
    _meldung(conn, "uralt", "2023-01-01")
    _meldung(conn, "quellweg", "2026-06-01", (JETZT - timedelta(days=40)).isoformat())
    conn.close()

    monkeypatch.setattr(tracker, "DB_PATH", db_pfad)
    monkeypatch.setattr(tracker, "fetch_meldungen", lambda: [])  # Schnittstelle tot

    rc = tracker.run()
    assert rc == 1, "Leerer Abruf meldet weiterhin einen Fehler"

    conn = sqlite3.connect(db_pfad)
    uebrig = _ids(conn)
    marker = conn.execute("SELECT count_total FROM fetch_log ORDER BY id DESC LIMIT 1").fetchone()[0]
    conn.close()
    assert marker == -1, "Der Fehler-Marker muss weiterhin geschrieben werden"
    assert uebrig == set(), (
        f"Beide Fristen muessen trotz totem Abruf gegriffen haben, uebrig: {uebrig}"
    )


def test_h01_wegfall_wird_bei_totem_abruf_NICHT_markiert(tmp_path, monkeypatch):
    """Gegenprobe zu H-01: die Loeschung laeuft, die Wegfall-Markierung nicht.
    Sonst wuerde der Ausfall selbst als Massenwegfall gewertet."""
    db_pfad = tmp_path / "h01b.db"
    conn = sqlite3.connect(db_pfad)
    tracker.init_db(conn)
    _meldung(conn, "aktuell", "2026-06-01")
    conn.close()

    monkeypatch.setattr(tracker, "DB_PATH", db_pfad)
    monkeypatch.setattr(tracker, "fetch_meldungen", lambda: [])
    tracker.run()

    conn = sqlite3.connect(db_pfad)
    weg = conn.execute("SELECT quelle_weg_seit FROM meldungen WHERE id='aktuell'").fetchone()[0]
    conn.close()
    assert weg is None, "Ein toter Abruf darf keine Meldung als weggefallen vormerken"


def test_h02_anker_laesst_sich_nicht_herunterschaukeln(tmp_path):
    """H-02: Die Schwelle stammt aus dem GROESSTEN der letzten Laeufe, nicht aus
    dem zuletzt akzeptierten. Sonst wird jeder Schrumpf-Lauf zum neuen Massstab.
    """
    conn = _db(tmp_path)
    conn.execute("INSERT INTO fetch_log (fetched_at, count_total, count_new, count_muell) "
                 "VALUES (?, 105456, 0, 0)", (JETZT.isoformat(),))
    conn.commit()

    # Acht Laeufe mit je 51 Prozent des Vortages
    anzahl = 105456
    akzeptiert = 0
    for lauf in range(8):
        anzahl = int(anzahl * 0.51)
        ok, _ = retention.feed_vollstaendig(conn, anzahl)
        if ok:
            akzeptiert += 1
            conn.execute("INSERT INTO fetch_log (fetched_at, count_total, count_new, count_muell) "
                         "VALUES (?,?,0,0)", (JETZT.isoformat(), anzahl))
            conn.commit()
    conn.close()
    assert akzeptiert == 1, (
        f"Nur der erste Schrumpf-Lauf darf durchgehen, akzeptiert wurden {akzeptiert}"
    )


def test_h02_massen_vormerkung_wird_abgebrochen(tmp_path):
    """H-02, zweite Sicherung: ein Lauf, der mehr als ein Fuenftel des Bestands
    auf einmal vormerken wuerde, laesst den Bestand unangetastet."""
    conn = _db(tmp_path)
    for i in range(100):
        _meldung(conn, f"m{i}", "2026-06-01")

    # Abruf enthaelt nur noch 10 der 100 Meldungen
    ergebnis = retention.markiere_quellpraesenz(conn, [f"m{i}" for i in range(10)],
                                                JETZT.isoformat())
    vorgemerkt = conn.execute(
        "SELECT COUNT(*) FROM meldungen WHERE quelle_weg_seit IS NOT NULL").fetchone()[0]
    conn.close()

    assert ergebnis["abgebrochen"] is True
    assert vorgemerkt == 0, f"Der Bestand muss unangetastet bleiben, {vorgemerkt} vorgemerkt"
    assert "90 von 100" in ergebnis["begruendung"]


def test_h02_normaler_abgang_geht_weiterhin_durch(tmp_path):
    """Gegenprobe: ein gewoehnlicher Abgang unterhalb der Grenze wirkt normal."""
    conn = _db(tmp_path)
    for i in range(100):
        _meldung(conn, f"m{i}", "2026-06-01")

    ergebnis = retention.markiere_quellpraesenz(conn, [f"m{i}" for i in range(95)],
                                                JETZT.isoformat())
    vorgemerkt = conn.execute(
        "SELECT COUNT(*) FROM meldungen WHERE quelle_weg_seit IS NOT NULL").fetchone()[0]
    conn.close()

    assert ergebnis["abgebrochen"] is False
    assert vorgemerkt == 5, f"Fuenf Meldungen sollten vorgemerkt sein, waren {vorgemerkt}"


def test_m01_aggregat_nur_ab_drei_meldungen(tmp_path):
    """M-01: Zeilen unterhalb der k-Schwelle fallen ersatzlos weg. Vorher
    bestand das Aggregat zu 96 Prozent aus Einzelfaellen auf Kartenaufloesung.
    """
    conn = _db(tmp_path)
    # eine Zelle mit drei Meldungen im selben Monat, eine mit nur einer
    for i in range(3):
        _meldung(conn, f"drei{i}", f"2023-05-1{i}", lat=52.5, lon=13.4)
    _meldung(conn, "einzel", "2023-05-20", lat=52.6, lon=13.5)

    retention.aggregiere_altbestand(conn, JETZT)

    zeilen = conn.execute("SELECT cluster_id, anzahl FROM meldungen_aggregat").fetchall()
    conn.close()
    assert zeilen == [(tracker.cluster_id(52.5, 13.4), 3)], (
        f"Nur die Zelle ab drei Meldungen darf bleiben, gefunden: {zeilen}"
    )


def test_m01_alle_meldungen_verschwinden_trotzdem(tmp_path):
    """Auch die Meldungen der verworfenen Zellen sind geloescht — verworfen wird
    das Aggregat, nicht die Loeschung."""
    conn = _db(tmp_path)
    _meldung(conn, "einzel", "2023-05-20", lat=52.6, lon=13.5)

    retention.aggregiere_altbestand(conn, JETZT)

    uebrig = _ids(conn)
    aggregat = conn.execute("SELECT COUNT(*) FROM meldungen_aggregat").fetchone()[0]
    conn.close()
    assert uebrig == set(), "Die Einzelmeldung muss geloescht sein"
    assert aggregat == 0, "und darf auch kein Aggregat hinterlassen"


def test_a4_schema_ist_idempotent(tmp_path):
    """Mehrfaches init_schema auf derselben Datenbank darf nicht scheitern."""
    conn = _db(tmp_path)
    retention.init_schema(conn)
    retention.init_schema(conn)
    spalten = [r[1] for r in conn.execute("PRAGMA table_info(meldungen)")]
    assert "last_seen_at" in spalten and "quelle_weg_seit" in spalten
