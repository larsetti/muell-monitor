"""
Tests fuer den Betreff-Filter (Abhilfe A-14 der DSFA vom 28.07.2026, Risiko R-10)
=================================================================================
Geprueft wird:
- die beiden Faelle, die die Folgenabschaetzung selbst benannt hat, liegen
  danach nicht mehr im Klartext
- harmlose Betreffs (Stichprobe aus den echten 538 Werten) gehen unveraendert durch
- die Abfallgruppe ueberlebt das Verwerfen des Freitexts
- der Lauf ist wiederholbar: ein entschaerfter Wert wird nicht erneut angefasst
- Wortgrenzen: 'zelt' steckt in 'Einzelteile', 'roma' in 'Aroma', 'freier' in
  'Rechtsfreier Raum' — keiner dieser Faelle darf ausloesen
- Hausnummern im Freitext fallen, ohne den Satz zu zerlegen
- der Schreibweg selbst filtert, nicht erst ein Nachlauf
- der Betreff verlaesst die Datenbank nicht (Grundannahme fuer die Einstufung
  von R-10 als Bestands- und nicht als Veroeffentlichungsrisiko)

WICHTIG: Alle Testdaten hier sind ERFUNDEN und den echten Faellen nur
nachgebaut. Dieser Code-Speicher ist oeffentlich; die tatsaechlichen Wortlaute
(darunter ein Personenname und mehrere vollstaendige Anschriften) stehen
ausschliesslich im Pruefbericht unter
audits\\dsgvo\\2026-08-03-a14-betreff-filter.md. Wer hier echte Betreffs
einsetzt, veroeffentlicht genau die Angaben, die der Filter entfernen soll.

Grundlage: audits\\dsgvo\\2026-07-28-dsfa-art35.md
"""

import json
import sqlite3
import sys
from pathlib import Path

TECHNIK = Path(__file__).parent.parent
sys.path.insert(0, str(TECHNIK))

import betreff_filter  # noqa: E402
import export_html  # noqa: E402
import tracker  # noqa: E402

# Nachbau der zwei Faelle aus Abschnitt 4 der DSFA ("538 verschiedene Werte,
# kategorieartig. Zwei Treffer beschreiben eine Lebenssituation ohne
# Namensnennung"). Gleiche Bauart wie die echten Werte: das tragende Wort
# steht mitten im Satz, einmal mit und einmal ohne ableitbare Abfallgruppe.
DSFA_FAELLE = [
    "Obdachloser verteilt Abfall im Gebüsch ",
    "Obdachlose mit Matratzen und Abfall",
]

# Stichprobe harmloser Werte, quer durch die Haeufigkeitsverteilung der echten
# 538: die vier haeufigsten, dazu Randfaelle mit Zahlen, Satzzeichen,
# Grossschreibung und Tippfehlern.
HARMLOS = [
    "Abfall - Sperrmüll",
    "Abfall - Müllablagerung",
    "Sperrmüll abgelagert",
    "Abfall - Bauabfälle, Bauschutt",
    "Abfall - Elektroschrott",
    "Grünanlage/Park - Müll, Verschmutzung",
    "Abfall - Tierkadaver/tote Tiere",
    "SPERRMÜLL UND DIVERSER MÜLL",
    "wilde Müllablagerung",
    "illegale Ablagerung",
    "Abfall - Weihnachtsbäume",
    "Abfall - Schrottfahrräder",
    "Müll+tote Ratte",
    "Abfall 4 Autoreifen",
    "13 Abfall - Sperrmüll",
    "eiAbfall - Schrottfahrräder Einkaufswagen 26stck",
    "MÜLL MÜLL MÜLL schon mehrere  Wochen!",
    "bsr steinplatte + müllsack ",
    "Abfall - Sperrmüll, Müll, Verschmutzung, Bauschuttreste, Möbel",
    "Müll am Gehweg zwischen Beispielring und 85",
]


def _db(tmp_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(tmp_path / "b.db")
    conn.row_factory = sqlite3.Row
    tracker.init_db(conn)
    return conn


def _meldung(conn, mid: str, betreff: str, lat=52.5, lon=13.4,
             datum="2026-06-01"):
    conn.execute(
        "INSERT INTO meldungen (id, fetched_at, datum, kategorie, betreff, "
        "bezirk, lat, lon, status, is_muell, strasse, plz) "
        "VALUES (?,'2026-08-03T00:00:00',?,'',?,'Mitte',?,?,'offen',1,'Teststr','10115')",
        (mid, datum, betreff, lat, lon))
    conn.commit()


# ── Die beiden Faelle der Folgenabschaetzung ─────────────────────────────────

def test_a14_dsfa_faelle_werden_erkannt():
    """Beide von der DSFA benannten Betreffs loesen eine Regel aus."""
    for fall in DSFA_FAELLE:
        assert betreff_filter.treffer(fall), (
            f"DSFA-Fall wird nicht erkannt: {fall!r}")


def test_a14_dsfa_faelle_liegen_nach_dem_nachziehen_nicht_mehr_im_klartext(tmp_path):
    """Der Kernbeleg: nach dem Bestandsabgleich steht keiner der beiden
    Wortlaute mehr in der Datenbank — weder ganz noch in Teilen."""
    conn = _db(tmp_path)
    for i, fall in enumerate(DSFA_FAELLE):
        _meldung(conn, f"dsfa{i}", fall)
    _meldung(conn, "harmlos", "Abfall - Sperrmüll")

    betreff_filter.bestand_nachziehen(conn)

    gespeichert = [r[0] for r in conn.execute("SELECT betreff FROM meldungen")]
    for fall in DSFA_FAELLE:
        assert fall not in gespeichert
    # Auch das tragende Wort selbst darf nirgends mehr stehen.
    assert not any("obdachlos" in b.lower() for b in gespeichert), gespeichert
    assert "Abfall - Sperrmüll" in gespeichert, "Harmlose Zeile wurde mitgerissen"


# ── Harmlose Meldungen bleiben unangetastet ──────────────────────────────────

def test_a14_harmlose_betreffs_gehen_unveraendert_durch():
    for text in HARMLOS:
        neu, regeln = betreff_filter.entschaerfe(text)
        assert regeln == [], f"Fehltreffer {regeln} bei {text!r}"
        assert neu == text, f"{text!r} wurde zu {neu!r} veraendert"


def test_a14_wortgrenzen_verhindern_fehltreffer():
    """Die Regelwoerter stecken als Zeichenfolge in unverdaechtigen Woertern.

    'Einzelteile' enthaelt 'zelt', 'Aroma' enthaelt 'roma', 'Rechtsfreier'
    enthaelt 'freier' (deshalb steht 'freier' gar nicht erst in der Liste),
    'Herrenlose' beginnt mit 'Herr'. Der letzte Fall stammt aus den echten
    Daten: der Bauart 'Herrenlose Muelltonne'.
    """
    unverdaechtig = [
        "Abfall - Einzelteile eines Regals",
        "Abfall - Aroma-Behälter aus Gewerbebetrieb",
        "Müllhalde und Rechtsfreier Raum Beispielstraße/Beispielbrücke",
        "Herrenlose überquellende Mülltonne",
        "Müll behindert den Gehweg",
        "Romantikweg vermüllt",
        "Sperrmüll wurde dort gelagert",
    ]
    for text in unverdaechtig:
        assert betreff_filter.treffer(text) == [], (
            f"Fehltreffer bei {text!r}: {betreff_filter.treffer(text)}")


# ── Die Abfallgruppe ueberlebt ───────────────────────────────────────────────

def test_a14_abfallgruppe_bleibt_nach_dem_verwerfen_erhalten():
    """Der Freitext faellt, die Kategorisierung nicht.

    'Obdachlose mit Matratzen und Abfall' traegt die Gruppe 'sperrmüll'
    (ueber 'matratze'). Nach dem Ersetzen muss dieselbe Gruppe herauskommen,
    sonst verliert die Karte die Kategorien-Auswertung dieser Meldung.
    """
    original = "Obdachlose mit Matratzen und Abfall"
    vorher = export_html.kategorisiere(original)
    assert vorher == "sperrmüll"

    neu, _ = betreff_filter.entschaerfe(original)
    assert export_html.kategorisiere(neu) == vorher, (
        f"Gruppe verloren: {original!r} -> {neu!r}")


def test_a14_alle_abfallgruppen_ueberstehen_den_ersatztext():
    """Der Ersatztext muss fuer JEDE Gruppe wieder dieselbe Gruppe liefern.

    Sonst haengt die Erhaltung an der Reihenfolge in KATEGORIE_GRUPPEN.
    """
    for gruppe in export_html.KATEGORIE_GRUPPEN:
        ersatz = f"{betreff_filter.MARKER} - {gruppe}"
        assert export_html.kategorisiere(ersatz) == gruppe, (
            f"Ersatztext {ersatz!r} liefert "
            f"{export_html.kategorisiere(ersatz)!r} statt {gruppe!r}")


def test_a14_ersatztext_ohne_gruppe_loest_keine_kategorie_aus():
    neu, _ = betreff_filter.entschaerfe("Obdachloser verteilt Abfall im Gebüsch ")
    assert neu == betreff_filter.MARKER
    assert export_html.kategorisiere(neu) is None


# ── Wiederholbarkeit ─────────────────────────────────────────────────────────

def test_a14_lauf_ist_wiederholbar(tmp_path):
    """Der Ersatztext darf keine Regel ausloesen — sonst fasst jeder Lauf
    denselben Datensatz erneut an und der Vermerk waechst endlos."""
    conn = _db(tmp_path)
    for i, fall in enumerate(DSFA_FAELLE):
        _meldung(conn, f"f{i}", fall)

    erster = betreff_filter.bestand_nachziehen(conn)
    assert erster["werte_geaendert"] == 2
    nach_erstem = sorted(r[0] for r in conn.execute("SELECT betreff FROM meldungen"))

    zweiter = betreff_filter.bestand_nachziehen(conn)
    assert zweiter["werte_geaendert"] == 0, zweiter["aenderungen"]
    assert sorted(r[0] for r in conn.execute("SELECT betreff FROM meldungen")) == nach_erstem


def test_a14_dry_run_schreibt_nicht(tmp_path):
    conn = _db(tmp_path)
    _meldung(conn, "f", DSFA_FAELLE[0])
    ergebnis = betreff_filter.bestand_nachziehen(conn, dry_run=True)
    assert ergebnis["werte_geaendert"] == 1
    assert conn.execute("SELECT betreff FROM meldungen").fetchone()[0] == DSFA_FAELLE[0]


# ── Hausnummern im Freitext ──────────────────────────────────────────────────

def test_a14_hausnummer_faellt_satz_bleibt():
    faelle = {
        "Wilde Müllablagerungen, Musterstraße 81, 13158 Berlin. (ungefähre Angabe)":
            "Wilde Müllablagerungen, Musterstraße, 13158 Berlin. (ungefähre Angabe)",
        "Stete Sperrmüllablagerungen Muster-Beispiel-Straße 4-10, 12681 Berlin 28.04.2025":
            "Stete Sperrmüllablagerungen Muster-Beispiel-Straße, 12681 Berlin 28.04.2025",
        "Müll Beispielstr 39, 12099 Berlin": "Müll Beispielstr, 12099 Berlin",
    }
    for vorher, erwartet in faelle.items():
        neu, regeln = betreff_filter.entschaerfe(vorher)
        assert regeln == ["hausnummer"], f"{vorher!r} -> {regeln}"
        assert neu == erwartet, f"{vorher!r} wurde zu {neu!r}, erwartet {erwartet!r}"


def test_a14_hausnummer_regel_verschluckt_kein_folgewort():
    """Rueckfall-Absicherung fuer zwei Fehler aus der Entwicklung.

    Ein '\\s*' vor dem Buchstaben-Zusatz ('12a') liess die Regel das naechste
    Wort mitnehmen: 'Str. 17 in 10405' wurde zu 'Str.n 10405' und
    'Beispielring 73 und 85' zu 'Beispielringnd 85'.
    """
    neu, _ = betreff_filter.entschaerfe(
        "Musterhofer Str. 17 in 10405 Berlin: Anzeige - Sperrmüll vor dem Haus")
    assert neu == "Musterhofer Str. in 10405 Berlin: Anzeige - Sperrmüll vor dem Haus"

    neu, _ = betreff_filter.entschaerfe(
        "Müll am Gehweg zwischen Beispielring 73 und 85!!!")
    assert neu == "Müll am Gehweg zwischen Beispielring!!!"


def test_a14_hausnummer_ohne_strassenwort_bleibt_stehen():
    """Zahlen ohne Strassenbezug sind Mengenangaben, keine Hausnummern."""
    for text in ["Abfall 4 Autoreifen", "13 Abfall - Sperrmüll",
                 "eiAbfall - Schrottfahrräder Einkaufswagen 26stck"]:
        neu, regeln = betreff_filter.entschaerfe(text)
        assert regeln == [] and neu == text, f"{text!r} -> {neu!r} {regeln}"


# ── Namensnennung ────────────────────────────────────────────────────────────

def test_a14_namensnennung_wird_verworfen():
    """Aus den echten Daten: ein Betreff nennt Inhaberin samt Namen und
    Insolvenz. Der ganze Freitext faellt."""
    original = ("Eckkiosk am Platz (Inhaberin Erika Musterfrau), Sondernutzung, "
                "Insolvenz, Vemüllung")
    neu, regeln = betreff_filter.entschaerfe(original)
    assert regeln == ["person"]
    assert "Musterfrau" not in neu and "Eckkiosk" not in neu


# ── Schreibweg ───────────────────────────────────────────────────────────────

def test_a14_filter_greift_beim_schreiben_nicht_erst_beim_export(tmp_path, monkeypatch):
    """tracker.run() darf den Rohtext gar nicht erst speichern."""
    meldungen = [
        {"id": "1", "kategorie": "", "betreff": "Obdachlose mit Matratzen und Abfall",
         "bezirk": "Mitte", "lat": 52.5, "lon": 13.4, "status": "offen",
         "erstellungsDatum": "01.06.2026", "strasse": "Teststr", "plz": "10115"},
        {"id": "2", "kategorie": "", "betreff": "Abfall - Sperrmüll",
         "bezirk": "Mitte", "lat": 52.5, "lon": 13.4, "status": "offen",
         "erstellungsDatum": "02.06.2026", "strasse": "Teststr", "plz": "10115"},
    ]
    monkeypatch.setattr(tracker, "DB_PATH", tmp_path / "run.db")
    monkeypatch.setattr(tracker, "fetch_meldungen", lambda: meldungen)

    assert tracker.run() == 0

    conn = sqlite3.connect(tmp_path / "run.db")
    gespeichert = dict(conn.execute("SELECT id, betreff FROM meldungen").fetchall())
    assert gespeichert["1"] == f"{betreff_filter.MARKER} - sperrmüll", gespeichert
    assert gespeichert["2"] == "Abfall - Sperrmüll", gespeichert


def test_a14_bestandsabgleich_laeuft_auch_bei_leerem_abruf(tmp_path, monkeypatch):
    """Wie die Loeschfristen (Befund H-01) darf der Abgleich nicht daran
    haengen, dass die Schnittstelle der Behoerde erreichbar ist. Sie liefert
    seit April 2026 nichts."""
    monkeypatch.setattr(tracker, "DB_PATH", tmp_path / "leer.db")
    conn = sqlite3.connect(tmp_path / "leer.db")
    tracker.init_db(conn)
    _meldung(conn, "alt", DSFA_FAELLE[1])
    conn.close()

    monkeypatch.setattr(tracker, "fetch_meldungen", lambda: [])
    assert tracker.run() == 1  # Ausfall wird weiterhin als Fehler gemeldet

    conn = sqlite3.connect(tmp_path / "leer.db")
    assert conn.execute("SELECT betreff FROM meldungen").fetchone()[0] != DSFA_FAELLE[1]


# ── Grundannahme: der Betreff verlaesst die Datenbank nicht ──────────────────

def test_a14_betreff_erscheint_nicht_in_den_exportierten_daten(tmp_path, monkeypatch):
    """R-10 ist als Bestandsrisiko eingestuft, weil load_data() den Betreff nur
    zum Kategorisieren liest und nirgends einbettet. Sollte das jemand aendern,
    faellt es hier auf, bevor der Freitext auf der Karte landet.
    """
    db = tmp_path / "export.db"
    monkeypatch.setattr(export_html, "DB_PATH", db)
    conn = sqlite3.connect(db)
    tracker.init_db(conn)
    kennung = "EINDEUTIGER-FREITEXT-MARKER"
    for i in range(3):
        _meldung(conn, f"m{i}", f"Abfall - Sperrmüll {kennung}",
                 datum=f"2026-06-0{i + 1}")
    conn.execute(
        "INSERT INTO hotspots (cluster_id, lat_center, lon_center, bezirk, "
        "meldungen_count, recurrence_count, last_seen, first_seen, score, "
        "score_label, strasse, plz) VALUES "
        "(?,52.5,13.4,'Mitte',3,1,'2026-06-03','2026-06-01',9.0,'hoch','Teststr','10115')",
        (tracker.cluster_id(52.5, 13.4),))
    conn.commit()
    conn.close()

    daten = export_html.load_data()
    assert daten["hotspots"], "Testaufbau falsch: kein Hotspot uebernommen"
    assert kennung not in json.dumps(daten, ensure_ascii=False), (
        "Der Betreff-Freitext landet in den exportierten Daten. Damit waere "
        "R-10 kein Bestands-, sondern ein Veroeffentlichungsrisiko.")
