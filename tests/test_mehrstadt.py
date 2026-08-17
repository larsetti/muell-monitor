"""
Tests zur Mehrstadt-Faehigkeit (Schema-Vorstufe zu T-49, 15.08.2026)
=====================================================================
Vor dieser Aenderung kannte der Bestand `bezirk`, aber keine Stadt. Vier
Routinen rechneten deshalb ueber den GESAMTEN Bestand, obwohl sie "eine Stadt"
meinten. Drei davon haetten stillen, nicht reparierbaren Schaden angerichtet,
sobald Koeln oder Bonn in dieselbe Datenbank schreiben:

  Befund 1  aggregiere_altbestand loeschte rein datumsbasiert ueber alles.
            Der Koelner Rueckimport ab dem 12.12.2023 waere beim ersten
            reguleren Lauf sofort zu Monats-Aggregaten reduziert und der
            duenne Rest ueber die k-Schwelle ersatzlos verworfen worden.
  Befund 2  markiere_quellpraesenz und loesche_quellabgang kannten keine Stadt.
            Ein Koelner Lauf haette den gesamten Berliner Bestand als aus der
            Quelle verschwunden vorgemerkt und 30 Tage spaeter geloescht.
  Befund 3  fetch_log hatte keine Stadt-Spalte. Der Datenstand-Streifen aus
            T-39 haette auf der Berliner Seite ein frisches Datum gezeigt,
            sobald Koeln abruft.
  Befund 4  meldungen.id war TEXT PRIMARY KEY ohne Stadtanteil. Berlin und
            Koeln vergeben beide numerische Kennungen; eine Kollision haette
            ueber ON CONFLICT ... DO UPDATE still eine fremde Meldung
            ueberschrieben.

Der Berliner Bestand ist unwiederbringlich: die Schnittstelle war ein rollendes
Fenster, erledigte Meldungen fielen nach 30 Tagen heraus. Ein Fehler hier ist
nicht reparierbar, deshalb diese Tests.
"""

import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

TECHNIK = Path(__file__).parent.parent
sys.path.insert(0, str(TECHNIK))

import migrate_mehrstadt  # noqa: E402
import retention  # noqa: E402
import sperrliste  # noqa: E402
import tracker  # noqa: E402

JETZT = datetime(2026, 8, 15, 12, 0, 0)

# Berlin-Mitte und Koeln-Innenstadt, weit genug auseinander fuer verschiedene
# Rasterzellen.
BERLIN = (52.5200, 13.4050)
KOELN = (50.9375, 6.9603)


def _db(tmp_path: Path, name: str = "m.db") -> sqlite3.Connection:
    conn = sqlite3.connect(tmp_path / name)
    tracker.init_db(conn)
    return conn


def _meldung(conn, mid: str, datum: str, stadt: str, ort=BERLIN,
             quelle_weg_seit: str = None, kategorie: str = "Sperrmüll",
             betreff: str = ""):
    conn.execute(
        "INSERT INTO meldungen (id, fetched_at, datum, kategorie, betreff, bezirk, "
        "lat, lon, status, is_muell, strasse, plz, stadt, quelle_weg_seit) "
        "VALUES (?,?,?,?,?,'Mitte',?,?,'offen',1,'Teststr','10115',?,?)",
        (mid, JETZT.isoformat(), datum, kategorie, betreff, ort[0], ort[1],
         stadt, quelle_weg_seit))
    conn.commit()


def _ids(conn, stadt: str = None) -> set:
    if stadt:
        return {r[0] for r in conn.execute(
            "SELECT id FROM meldungen WHERE stadt = ?", (stadt,))}
    return {r[0] for r in conn.execute("SELECT id FROM meldungen")}


# ── Schema ───────────────────────────────────────────────────────────────────

def test_stadt_spalte_in_allen_vier_tabellen(tmp_path):
    conn = _db(tmp_path)
    retention.init_schema(conn)
    for tabelle in ("meldungen", "hotspots", "fetch_log", "sperrliste",
                    "meldungen_aggregat"):
        spalten = [r[1] for r in conn.execute(f"PRAGMA table_info({tabelle})")]
        assert "stadt" in spalten, f"{tabelle} hat keine Stadt-Spalte: {spalten}"
    conn.close()


def test_stadt_hat_vorgabewert_berlin(tmp_path):
    """Der Vorgabewert ist Absicht: vorhandener Code, der die Spalte nicht
    kennt, schreibt weiter korrekte Daten statt an NOT NULL zu scheitern."""
    conn = _db(tmp_path)
    conn.execute(
        "INSERT INTO meldungen (id, fetched_at, datum, kategorie, betreff, "
        "bezirk, lat, lon, status, is_muell) "
        "VALUES ('alt', '2026-01-01', '2026-01-01', 'Sperrmüll', '', 'Mitte', "
        "52.5, 13.4, 'offen', 1)")
    conn.execute("INSERT INTO fetch_log (fetched_at, count_total, count_new, "
                 "count_muell) VALUES ('2026-01-01', 100, 0, 0)")
    conn.commit()
    assert conn.execute("SELECT stadt FROM meldungen").fetchone()[0] == "berlin"
    assert conn.execute("SELECT stadt FROM fetch_log").fetchone()[0] == "berlin"
    conn.close()


def test_indizes_auf_stadt_vorhanden(tmp_path):
    conn = _db(tmp_path)
    namen = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index'")}
    conn.close()
    assert "idx_stadt_datum" in namen
    assert "idx_stadt_bezirk" in namen


# ── Befund 4: ID-Praefix ─────────────────────────────────────────────────────

def test_make_id_traegt_die_stadt(tmp_path):
    assert tracker.make_id({"id": "1066349"}, "berlin") == "berlin:1066349"
    assert tracker.make_id({"id": "1066349"}, "koeln") == "koeln:1066349"


def test_make_id_ohne_stadt_scheitert():
    """Kein Vorgabewert. Eine vergessene Stadt soll auffallen, nicht
    stillschweigend als Berlin gelten."""
    import pytest
    with pytest.raises(TypeError):
        tracker.make_id({"id": "1"})
    with pytest.raises(ValueError):
        tracker.make_id({"id": "1"}, "")


def test_gleiche_quellkennung_in_zwei_staedten_kollidiert_nicht(tmp_path):
    """Der eigentliche Befund 4: Berlin und Koeln vergeben beide numerische
    Kennungen. Ohne Praefix haette der zweite Insert den ersten ueberschrieben,
    weil tracker.py mit ON CONFLICT ... DO UPDATE arbeitet."""
    conn = _db(tmp_path)
    _meldung(conn, tracker.make_id({"id": "1066349"}, "berlin"), "2026-01-01",
             "berlin", BERLIN)
    _meldung(conn, tracker.make_id({"id": "1066349"}, "koeln"), "2026-01-01",
             "koeln", KOELN)

    zeilen = conn.execute("SELECT id, stadt FROM meldungen ORDER BY id").fetchall()
    conn.close()
    assert zeilen == [("berlin:1066349", "berlin"), ("koeln:1066349", "koeln")], (
        f"Beide Meldungen muessen nebeneinander bestehen, gefunden: {zeilen}"
    )


def test_roh_id_und_stadt_aus_id():
    assert tracker.roh_id("berlin:1066349") == "1066349"
    assert tracker.stadt_aus_id("koeln:42") == "koeln"
    # Zeile aus der Zeit vor der Umstellung
    assert tracker.roh_id("1066349") == "1066349"
    assert tracker.stadt_aus_id("1066349") == "berlin"


# ── Befund 2: Quellabgleich trifft nur die eigene Stadt ──────────────────────

def test_koelner_lauf_markiert_keine_berliner_meldung_als_weggefallen(tmp_path):
    """Der teuerste Fall aus Befund 2. Ein Koelner Abruf enthaelt naturgemaess
    keine Berliner Kennung. Stadtblind waere jede Berliner Meldung damit als
    aus der Quelle verschwunden vorgemerkt und 30 Tage spaeter geloescht
    worden — unwiederbringlich, weil die Berliner Schnittstelle ein rollendes
    Fenster war."""
    conn = _db(tmp_path)
    for i in range(50):
        _meldung(conn, f"berlin:{i}", "2026-06-01", "berlin", BERLIN)
    for i in range(10):
        _meldung(conn, f"koeln:{i}", "2026-06-01", "koeln", KOELN)

    ergebnis = retention.markiere_quellpraesenz(
        conn, [f"koeln:{i}" for i in range(10)], JETZT.isoformat(), stadt="koeln")

    berlin_vorgemerkt = conn.execute(
        "SELECT COUNT(*) FROM meldungen WHERE stadt='berlin' "
        "AND quelle_weg_seit IS NOT NULL").fetchone()[0]
    conn.close()

    assert ergebnis["abgebrochen"] is False
    assert ergebnis["in_quelle"] == 10
    assert ergebnis["neu_als_weggefallen_markiert"] == 0
    assert berlin_vorgemerkt == 0, (
        f"Ein Koelner Lauf darf keine Berliner Meldung vormerken, "
        f"{berlin_vorgemerkt} waren es"
    )


def test_abgangsgrenze_rechnet_je_stadt(tmp_path):
    """Der 20-Prozent-Riegel misst am Bestand DER STADT, nicht am Gesamtbestand.
    Sonst waere er in der kleinen Stadt wirkungslos: fielen alle 10 Koelner
    Meldungen weg, waeren das gegen 990 Berliner nur 1 Prozent und der Riegel
    haette geschwiegen."""
    conn = _db(tmp_path)
    for i in range(990):
        _meldung(conn, f"berlin:{i}", "2026-06-01", "berlin", BERLIN)
    for i in range(10):
        _meldung(conn, f"koeln:{i}", "2026-06-01", "koeln", KOELN)

    # Koelner Abruf liefert nur noch 1 von 10 Meldungen: 90 Prozent Abgang.
    ergebnis = retention.markiere_quellpraesenz(
        conn, ["koeln:0"], JETZT.isoformat(), stadt="koeln")
    vorgemerkt = conn.execute(
        "SELECT COUNT(*) FROM meldungen WHERE quelle_weg_seit IS NOT NULL").fetchone()[0]
    conn.close()

    assert ergebnis["abgebrochen"] is True, "Der Riegel muss je Stadt greifen"
    assert "9 von 10" in ergebnis["begruendung"]
    assert vorgemerkt == 0


def test_vollstaendigkeitsanker_rechnet_je_stadt(tmp_path):
    """Befund 2, dritter Teil. Der Anker ist der groesste Abruf DIESER STADT.
    Stadtblind haette der Berliner Anker von 105.456 dafuer gesorgt, dass ein
    normaler Bonner Abruf nie als vollstaendig gilt und die Wegfall-Markierung
    dort dauerhaft ausgesetzt bleibt — lautlos, weil das Aussetzen der
    vorgesehene Schutz ist."""
    conn = _db(tmp_path)
    conn.execute("INSERT INTO fetch_log (fetched_at, count_total, count_new, "
                 "count_muell, stadt) VALUES (?, 105456, 0, 0, 'berlin')",
                 (JETZT.isoformat(),))
    conn.execute("INSERT INTO fetch_log (fetched_at, count_total, count_new, "
                 "count_muell, stadt) VALUES (?, 5000, 0, 0, 'bonn')",
                 (JETZT.isoformat(),))
    conn.commit()

    ok_bonn, grund_bonn = retention.feed_vollstaendig(conn, 4800, stadt="bonn")
    ok_berlin, _ = retention.feed_vollstaendig(conn, 4800, stadt="berlin")
    conn.close()

    assert ok_bonn, f"4.800 von zuletzt 5.000 ist ein normaler Bonner Abruf: {grund_bonn}"
    assert not ok_berlin, "4.800 von zuletzt 105.456 ist kein vollstaendiger Berliner Abruf"


def test_loeschung_wegen_quellabgang_trifft_nur_die_eigene_stadt(tmp_path):
    conn = _db(tmp_path)
    ueberfaellig = (JETZT - timedelta(days=40)).isoformat()
    _meldung(conn, "berlin:1", "2026-06-01", "berlin", BERLIN, ueberfaellig)
    _meldung(conn, "koeln:1", "2026-06-01", "koeln", KOELN, ueberfaellig)

    anzahl = retention.loesche_quellabgang(conn, JETZT, stadt="koeln")
    uebrig = _ids(conn)
    conn.close()

    assert anzahl == 1
    assert uebrig == {"berlin:1"}, (
        f"Nur die Koelner Meldung darf weg sein, uebrig: {uebrig}"
    )


# ── Befund 1: Aggregat-Frist trifft nur die eigene Stadt ─────────────────────

def test_aggregation_einer_stadt_fasst_die_andere_nicht_an(tmp_path):
    """Befund 1, der teuerste. Der Koelner Rueckimport beginnt am 12.12.2023.
    Ein Berliner Aggregat-Lauf haette ihn rein datumsbasiert mitreduziert — die
    Einzelmeldungen geloescht und den Rest unter drei ersatzlos verworfen. Man
    haette also genau die Tiefe importiert, fuer die der Rueckimport gemacht
    wird, und der naechste Lauf haette sie wieder weggenommen, ohne
    Fehlermeldung, weil das die vorgesehene Funktion ist."""
    conn = _db(tmp_path)
    retention.init_schema(conn)
    # Koelner Rueckimport-Tiefe: aelter als 24 Monate vor dem 15.08.2026
    for i in range(5):
        _meldung(conn, f"koeln:{i}", f"2023-12-1{i}", "koeln", KOELN)
    for i in range(5):
        _meldung(conn, f"berlin:{i}", f"2023-12-1{i}", "berlin", BERLIN)

    anzahl = retention.aggregiere_altbestand(conn, JETZT, stadt="berlin")

    koeln_uebrig = _ids(conn, "koeln")
    berlin_uebrig = _ids(conn, "berlin")
    aggregat_staedte = {r[0] for r in conn.execute(
        "SELECT DISTINCT stadt FROM meldungen_aggregat")}
    conn.close()

    assert anzahl == 5, f"Nur die fuenf Berliner Meldungen, gezaehlt {anzahl}"
    assert berlin_uebrig == set(), "Berlin muss reduziert sein"
    assert len(koeln_uebrig) == 5, (
        f"Der Koelner Rueckimport muss unangetastet bleiben, uebrig: "
        f"{len(koeln_uebrig)} von 5"
    )
    assert aggregat_staedte == {"berlin"}


def test_k_schwelle_verwirft_nur_die_eigene_stadt(tmp_path):
    """M-01 loescht Aggregat-Zeilen unter drei Meldungen. Stadtblind haette ein
    Koelner Lauf dabei auch Berliner Zeilen mitgenommen."""
    conn = _db(tmp_path)
    retention.init_schema(conn)
    conn.execute("INSERT INTO meldungen_aggregat (cluster_id, jahr_monat, "
                 "kategorie_gruppe, anzahl, stadt) VALUES ('52.52000_13.40500', "
                 "'2023-12', 'sperrmüll', 1, 'berlin')")
    conn.commit()
    # Koelner Lauf ohne eigene Altmeldungen
    retention.aggregiere_altbestand(conn, JETZT, stadt="koeln")
    _meldung(conn, "koeln:1", "2023-12-01", "koeln", KOELN)
    retention.aggregiere_altbestand(conn, JETZT, stadt="koeln")

    berlin_zeilen = conn.execute(
        "SELECT COUNT(*) FROM meldungen_aggregat WHERE stadt='berlin'").fetchone()[0]
    conn.close()
    assert berlin_zeilen == 1, (
        "Die unterschwellige Berliner Zeile darf ein Koelner Lauf nicht loeschen"
    )


def test_24_monate_gelten_unveraendert_fuer_jede_stadt():
    """Die Frist stammt woertlich aus Abschnitt 3.5 der Folgenabschaetzung und
    wird durch diese Aenderung NICHT angehoben — nur stadtscharf einstellbar.
    Ob Koeln eine andere Frist bekommt, ist eine Lars- und Anwaltsfrage."""
    assert retention.AGGREGAT_FRIST_MONATE == 24
    for stadt in ("berlin", "koeln", "bonn"):
        assert retention.frist_aggregat_monate(stadt) == 24, (
            f"{stadt} muss unveraendert auf 24 Monaten stehen"
        )
        assert retention.frist_quelle_tage(stadt) == 30


def test_stadtscharfe_frist_ist_einstellbar(monkeypatch):
    """Die Vorrichtung fuer eine anwaltlich getragene Abweichung existiert,
    ohne dass heute jemand sie benutzt."""
    monkeypatch.setenv("MM_RETENTION_AGGREGAT_MONATE_KOELN", "36")
    assert retention.frist_aggregat_monate("koeln") == 36
    assert retention.frist_aggregat_monate("berlin") == 24


# ── Kein stadtblinder Gesamtlauf ─────────────────────────────────────────────

def test_lauf_ohne_stadt_arbeitet_staedte_nacheinander_ab(tmp_path):
    """Ein Lauf ohne Stadt-Angabe darf nicht stillschweigend alles auf einmal
    treffen. Er bedient jede Stadt, rechnet aber je Stadt."""
    conn = _db(tmp_path)
    _meldung(conn, "berlin:1", "2023-01-01", "berlin", BERLIN)
    _meldung(conn, "koeln:1", "2023-01-01", "koeln", KOELN)

    ergebnis = retention.anwenden(conn, JETZT)
    conn.close()

    assert set(ergebnis["staedte"]) == {"berlin", "koeln"}
    assert ergebnis["je_stadt"]["berlin"]["altbestand_aggregiert"] == 1
    assert ergebnis["je_stadt"]["koeln"]["altbestand_aggregiert"] == 1
    assert ergebnis["altbestand_aggregiert"] == 2, "die Summe bleibt lesbar"


def test_leere_datenbank_liefert_die_standardstadt(tmp_path):
    conn = _db(tmp_path)
    assert retention.staedte_im_bestand(conn) == ["berlin"]
    conn.close()


# ── Befund 3: fetch_log je Stadt ─────────────────────────────────────────────

def test_datenstand_je_stadt_getrennt_lesbar(tmp_path):
    """Der Datenstand-Streifen aus T-39 liest den letzten erfolgreichen Abruf.
    Ohne Stadt im fetch_log stuende auf der Berliner Seite ein frisches Datum,
    sobald Koeln abruft — obwohl aus Berlin seit dem 22.04.2026 nichts kommt."""
    conn = _db(tmp_path)
    conn.executemany(
        "INSERT INTO fetch_log (fetched_at, count_total, count_new, count_muell, stadt) "
        "VALUES (?,?,?,?,?)",
        [("2026-04-22T06:00:00", 105456, 0, 0, "berlin"),
         ("2026-08-15T06:00:00", 8600, 12, 12, "koeln")])
    conn.commit()

    letzter = dict(conn.execute(
        "SELECT stadt, MAX(fetched_at) FROM fetch_log WHERE count_total > 0 "
        "GROUP BY stadt").fetchall())
    conn.close()

    assert letzter["berlin"].startswith("2026-04-22"), (
        f"Berlin muss seinen alten Stand behalten, war {letzter['berlin']}"
    )
    assert letzter["koeln"].startswith("2026-08-15")


# ── tracker.run() im Mehrstadt-Bestand ───────────────────────────────────────

def test_berliner_lauf_laesst_koelner_zellen_stehen(tmp_path, monkeypatch):
    """Die Verwaisten-Bereinigung in tracker.run() vergleicht die Zellen in der
    Datenbank gegen die Zellen des aktuellen Laufs. Ohne Stadt-Filter gaelte
    JEDE Koelner Zelle als verwaist und waere bei jedem Berliner Lauf
    geloescht worden."""
    db_pfad = tmp_path / "lauf.db"
    conn = sqlite3.connect(db_pfad)
    tracker.init_db(conn)
    conn.execute(
        "INSERT INTO hotspots (cluster_id, lat_center, lon_center, bezirk, "
        "meldungen_count, recurrence_count, last_seen, first_seen, score, "
        "score_label, strasse, plz, stadt) VALUES (?,?,?,'Innenstadt',7,2,"
        "'2026-08-01','2026-01-01',9.0,'hoch','Domstr','50667','koeln')",
        (tracker.cluster_id(*KOELN), KOELN[0], KOELN[1]))
    conn.commit()
    conn.close()

    monkeypatch.setattr(tracker, "DB_PATH", db_pfad)
    monkeypatch.setattr(tracker, "fetch_meldungen", lambda: [
        {"id": "n1", "kategorie": "Sperrmüll", "betreff": "", "bezirk": "Mitte",
         "lat": BERLIN[0], "lon": BERLIN[1], "status": "offen",
         "erstellungsDatum": "01.08.2026", "strasse": "Neustr", "plz": "10000"},
        {"id": "n2", "kategorie": "Sperrmüll", "betreff": "", "bezirk": "Mitte",
         "lat": BERLIN[0] + 0.0001, "lon": BERLIN[1] + 0.0001, "status": "offen",
         "erstellungsDatum": "05.08.2026", "strasse": "Neustr", "plz": "10000"},
    ])
    assert tracker.run() == 0

    conn = sqlite3.connect(db_pfad)
    zellen = dict(conn.execute("SELECT cluster_id, stadt FROM hotspots").fetchall())
    kennungen = {r[0] for r in conn.execute("SELECT id FROM meldungen")}
    log_stadt = conn.execute(
        "SELECT stadt FROM fetch_log ORDER BY id DESC LIMIT 1").fetchone()[0]
    conn.close()

    assert zellen.get(tracker.cluster_id(*KOELN)) == "koeln", (
        f"Die Koelner Zelle darf ein Berliner Lauf nicht entfernen: {zellen}"
    )
    assert zellen.get(tracker.cluster_id(*BERLIN)) == "berlin"
    assert kennungen == {"berlin:n1", "berlin:n2"}, f"gefunden: {kennungen}"
    assert log_stadt == "berlin"


# ── Migration ────────────────────────────────────────────────────────────────

def _altbestand(pfad: Path, anzahl: int = 20):
    """Datenbank im Stand VOR der Umstellung: keine Stadt-Spalten, rohe IDs."""
    conn = sqlite3.connect(pfad)
    conn.executescript("""
        CREATE TABLE meldungen (
            id TEXT PRIMARY KEY, fetched_at TEXT NOT NULL, datum TEXT,
            kategorie TEXT, betreff TEXT, bezirk TEXT, lat REAL, lon REAL,
            status TEXT, is_muell INTEGER DEFAULT 0, strasse TEXT DEFAULT '',
            plz TEXT DEFAULT ''
        );
        CREATE TABLE hotspots (
            cluster_id TEXT PRIMARY KEY, lat_center REAL, lon_center REAL,
            bezirk TEXT, meldungen_count INTEGER DEFAULT 0,
            recurrence_count INTEGER DEFAULT 0, last_seen TEXT, first_seen TEXT,
            score REAL DEFAULT 0.0, score_label TEXT DEFAULT 'niedrig',
            strasse TEXT DEFAULT '', plz TEXT DEFAULT ''
        );
        CREATE TABLE fetch_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT, fetched_at TEXT,
            count_total INTEGER, count_new INTEGER, count_muell INTEGER
        );
    """)
    conn.executemany(
        "INSERT INTO meldungen (id, fetched_at, datum, kategorie, betreff, bezirk, "
        "lat, lon, status, is_muell, strasse, plz) "
        "VALUES (?,'2026-01-01',?,'Sperrmüll','','Mitte',52.52,13.405,'offen',1,'S','1')",
        [(str(1000000 + i), f"2024-01-{i % 28 + 1:02d}") for i in range(anzahl)])
    conn.execute("INSERT INTO hotspots (cluster_id, lat_center, lon_center, bezirk, "
                 "meldungen_count) VALUES ('52.52000_13.40500',52.52,13.405,'Mitte',5)")
    conn.execute("INSERT INTO fetch_log (fetched_at, count_total, count_new, "
                 "count_muell) VALUES ('2026-04-22', 105456, 0, 0)")
    conn.commit()
    conn.close()


def test_migration_verliert_nichts(tmp_path):
    pfad = tmp_path / "alt.db"
    _altbestand(pfad, 20)

    conn = sqlite3.connect(pfad)
    migrate_mehrstadt.schema_anlegen(conn)
    vorher = migrate_mehrstadt.zaehle(conn)
    migrate_mehrstadt.ids_praefixieren(conn, "berlin", dry_run=False)
    nachher = migrate_mehrstadt.zaehle(conn)
    kennungen = {r[0] for r in conn.execute("SELECT id FROM meldungen")}
    staedte = {r[0] for r in conn.execute("SELECT DISTINCT stadt FROM meldungen")}
    conn.close()

    assert vorher == nachher, f"vorher {vorher}, nachher {nachher}"
    assert all(k.startswith("berlin:") for k in kennungen)
    assert "berlin:1000000" in kennungen
    assert staedte == {"berlin"}


def test_migration_ist_idempotent(tmp_path):
    pfad = tmp_path / "idem.db"
    _altbestand(pfad, 10)

    conn = sqlite3.connect(pfad)
    migrate_mehrstadt.schema_anlegen(conn)
    erster = migrate_mehrstadt.ids_praefixieren(conn, "berlin", dry_run=False)
    zweiter = migrate_mehrstadt.ids_praefixieren(conn, "berlin", dry_run=False)
    kennungen = {r[0] for r in conn.execute("SELECT id FROM meldungen")}
    conn.close()

    assert erster == 10
    assert zweiter == 0, "Ein zweiter Lauf darf nichts mehr finden"
    assert not any(k.startswith("berlin:berlin:") for k in kennungen), (
        "Doppeltes Praefix — der Lauf ist nicht idempotent"
    )


def test_migration_trockenlauf_schreibt_nichts(tmp_path):
    pfad = tmp_path / "trocken.db"
    _altbestand(pfad, 10)

    conn = sqlite3.connect(pfad)
    migrate_mehrstadt.schema_anlegen(conn)
    offen = migrate_mehrstadt.ids_praefixieren(conn, "berlin", dry_run=True)
    kennungen = {r[0] for r in conn.execute("SELECT id FROM meldungen")}
    conn.close()

    assert offen == 10, "Der Trockenlauf meldet die Zahl"
    assert not any(":" in k for k in kennungen), "und schreibt nichts"


# ── Sperrliste ───────────────────────────────────────────────────────────────

def test_sperre_wirkt_stadtuebergreifend(tmp_path):
    """Die Sperre nach Art. 21 bleibt bewusst stadtblind: eine Zell-Kennung ist
    eine gerundete Koordinate und damit weltweit eindeutig, und die Zweitschrift
    fuehrt keine Stadt. Ein Stadt-Filter wuerde einen aus der Datei
    zurueckgespielten Widerspruch still unwirksam machen."""
    conn = _db(tmp_path)
    sperrliste.eintragen(conn, tracker.cluster_id(*KOELN), quelle="W-2026-010",
                         stadt="koeln", datei=tmp_path / "sperr.txt")
    conn.commit()

    geladen = sperrliste.laden(conn, datei=tmp_path / "sperr.txt")
    gespeicherte_stadt = conn.execute(
        "SELECT stadt FROM sperrliste").fetchone()[0]
    conn.close()

    assert tracker.cluster_id(*KOELN) in geladen
    assert gespeicherte_stadt == "koeln"
