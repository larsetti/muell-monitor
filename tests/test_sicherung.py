"""
Tests zu T-64: Sicherung und Protokoll der Loeschroutine
=========================================================
Befund S-03 der Abnahme vom 15.08.2026. Drei Umstaende trafen zusammen:
ordnungsamt.db ist gitignoriert und existiert genau einmal; `retention.py
--apply` schrieb weder nach tracker.log noch ins Abrufprotokoll; und genau
dieser Weg hat am 15.08.2026 rund 710 unwiederbringliche Meldungen entfernt.

Die Tests decken drei Zusagen ab:

  1. Die Sicherung liegt NICHT in einem synchronisierten Ordner und nicht
     neben dem Original — beides wird fail-closed abgewiesen.
  2. Der Sicherungsbestand wird nach Anzahl UND Alter begrenzt, damit keine
     zweite Altlast der Art T-41 / T-48 entsteht.
  3. Die Loeschroutine schreibt eine Protokollzeile mit Zahlen je Stadt und je
     Regel.
"""

import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

TECHNIK = Path(__file__).parent.parent
sys.path.insert(0, str(TECHNIK))

import retention  # noqa: E402
import sicherung  # noqa: E402
import tracker  # noqa: E402

JETZT = datetime(2026, 8, 15, 15, 0, 0)


def _quell_db(tmp_path: Path, zeilen: int = 5) -> Path:
    pfad = tmp_path / "quelle" / "ordnungsamt.db"
    pfad.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(pfad)
    tracker.init_db(conn)
    for i in range(zeilen):
        conn.execute(
            "INSERT INTO meldungen (id, fetched_at, datum, kategorie, betreff, "
            "bezirk, lat, lon, status, is_muell, stadt) "
            "VALUES (?,'2026-08-15','2026-08-01','Sperrmüll','','Mitte',52.5,"
            "13.4,'offen',1,'berlin')", (f"berlin:{i}",))
    conn.commit()
    conn.close()
    return pfad


# ── Zielpruefung: fail-closed ────────────────────────────────────────────────

@pytest.mark.parametrize("pfad", [
    r"C:\Users\larsw\OneDrive\Business\01_Aktiv\Muell-Monitor\sicherung",
    r"C:\Users\larsw\OneDrive - Oriatur\sicherung",
    r"C:\Users\larsw\Nextcloud3\Oriatur\sicherung",
    r"C:\Users\larsw\iCloudPhotos\sicherung",
    r"D:\Icloud\iCloudDrive\mm",
    r"C:\Users\larsw\Dropbox\mm",
])
def test_synchronisierte_ziele_werden_abgewiesen(pfad):
    """Aus einem Sync-Ordner wird nie verschoben oder geloescht — und eine
    43-MB-Datei, die sich taeglich vollstaendig aendert, gehoert erst recht
    nicht hinein. Der Bestand traegt ausserdem Ortsdaten, deren Uebermittlung
    an einen Auftragsverarbeiter nicht im Verarbeitungsverzeichnis steht."""
    with pytest.raises(sicherung.ZielUnzulaessig):
        sicherung.pruefe_ziel(Path(pfad))


def test_ziel_neben_der_datenbank_wird_abgewiesen(tmp_path):
    """Die Lehre aus T-41 und T-48: eine Sicherung im selben Ordner teilt das
    Schicksal des Originals und wird uebersehen, bis sie eine Altlast ist."""
    quelle = _quell_db(tmp_path)
    with pytest.raises(sicherung.ZielUnzulaessig):
        sicherung.pruefe_ziel(quelle.parent, quelle)


def test_gewoehnlicher_lokaler_ordner_ist_zulaessig(tmp_path):
    ziel = tmp_path / "Sicherungen" / "Muell-Monitor"
    assert sicherung.pruefe_ziel(ziel, _quell_db(tmp_path)) == ziel


@pytest.mark.parametrize("pfad", [
    r"C:\Temp\C--Users-larsw-OneDrive-Business\scratchpad\s",
    r"C:\Arbeit\Kunde-Dropbox-Migration\s",
])
def test_namen_die_einen_sync_dienst_nur_erwaehnen_sind_zulaessig(pfad):
    """Verglichen wird am ANFANG eines Pfadbestandteils. Ein Arbeitsordner, der
    'OneDrive' mitten im Namen traegt, ist kein Sync-Ordner — dieser Fall hat
    am 15.08.2026 einen Testlauf faelschlich blockiert."""
    assert sicherung.pruefe_ziel(Path(pfad), Path(r"C:\x\ordnungsamt.db"))


def test_ordner_der_mit_einem_sync_dienst_beginnt_bleibt_gesperrt():
    """Die Gegenrichtung, und sie ist Absicht: 'onedrive-abgleich-alt' faellt
    unter die Regel, obwohl es vielleicht ein harmloser Arbeitsordner ist. Zu
    breit sperren kostet eine Umbenennung, zu eng sperren kostet den Bestand."""
    with pytest.raises(sicherung.ZielUnzulaessig):
        sicherung.pruefe_ziel(Path(r"C:\Sicherungen\onedrive-abgleich-alt"),
                              Path(r"C:\x\ordnungsamt.db"))


def test_sicherung_bricht_ab_statt_an_den_falschen_ort_zu_laufen(tmp_path):
    """Fail-closed: bei unzulaessigem Ziel entsteht KEINE Datei."""
    quelle = _quell_db(tmp_path)
    ziel = tmp_path / "OneDrive" / "sicherungen"
    with pytest.raises(sicherung.ZielUnzulaessig):
        sicherung.sichere(quelle, ziel, protokoll=False)
    assert not ziel.exists()


# ── Die Sicherung selbst ─────────────────────────────────────────────────────

def test_sicherung_ist_lesbar_und_vollstaendig(tmp_path):
    quelle = _quell_db(tmp_path, zeilen=7)
    ziel = tmp_path / "Sicherungen"
    e = sicherung.sichere(quelle, ziel, jetzt=JETZT, protokoll=False)

    assert e["integritaet"] == "ok"
    assert e["zahlen"]["meldungen"] == {"berlin": 7}

    # Die Pruefsumme gehoert zur SICHERUNG, nicht zum Vergleich mit der Quelle.
    # sqlite3.backup schreibt einen fachlich gleichen, aber nicht byte-gleichen
    # Stand — freie Seiten und Seitenreihenfolge koennen abweichen. Wer hier
    # Gleichheit erwartet, verwechselt die Backup-Schnittstelle mit shutil.copy.
    assert e["sha256"] == sicherung.sha256(e["pfad"])

    kopie = sqlite3.connect(e["pfad"])
    assert kopie.execute("SELECT COUNT(*) FROM meldungen").fetchone()[0] == 7
    assert {r[0] for r in kopie.execute("SELECT id FROM meldungen")} == {
        f"berlin:{i}" for i in range(7)}
    kopie.close()


def test_sicherung_zieht_einen_stimmigen_stand_waehrend_geschrieben_wird(tmp_path):
    """Der Grund fuer sqlite3.backup statt shutil.copy: am 15.08.2026 lief
    waehrend der Abnahme ein Rueckimport gegen dieselbe Datei."""
    quelle = _quell_db(tmp_path, zeilen=3)
    schreiber = sqlite3.connect(quelle)
    schreiber.execute("BEGIN")
    schreiber.execute(
        "INSERT INTO meldungen (id, fetched_at, datum, kategorie, betreff, "
        "bezirk, lat, lon, status, is_muell, stadt) "
        "VALUES ('berlin:offen','2026-08-15','2026-08-01','Sperrmüll','',"
        "'Mitte',52.5,13.4,'offen',1,'berlin')")
    try:
        e = sicherung.sichere(quelle, tmp_path / "S", jetzt=JETZT, protokoll=False)
    finally:
        schreiber.rollback()
        schreiber.close()

    assert e["integritaet"] == "ok"
    assert e["zahlen"]["meldungen"] == {"berlin": 3}, (
        "Die Sicherung darf einen noch nicht bestaetigten Schreibvorgang nicht "
        "halb mitnehmen."
    )


def test_anlass_steht_im_dateinamen(tmp_path):
    e = sicherung.sichere(_quell_db(tmp_path), tmp_path / "S",
                          anlass="vor-loeschroutine", jetzt=JETZT,
                          protokoll=False)
    assert e["pfad"].name == "ordnungsamt.db.2026-08-15T150000-vor-loeschroutine"


# ── Keine zweite Altlast ─────────────────────────────────────────────────────

def test_bestand_wird_nach_anzahl_begrenzt(tmp_path):
    ziel = tmp_path / "S"
    quelle = _quell_db(tmp_path)
    for i in range(6):
        sicherung.sichere(quelle, ziel, behalten=3, jetzt=JETZT + timedelta(minutes=i),
                          protokoll=False)
    assert len(sicherung.vorhandene(ziel)) == 3


def test_bestand_wird_nach_alter_begrenzt(tmp_path):
    """Die datenschutzrechtlich wichtigere Grenze: eine Sicherung haelt
    geloeschte Meldungen weiter vor und verlaengert damit faktisch die
    Aufbewahrung nach Art. 5 Abs. 1 lit. e DSGVO."""
    ziel = tmp_path / "S"
    ziel.mkdir()
    alt = ziel / (sicherung.PRAEFIX + "2026-06-01T120000")
    mittel = ziel / (sicherung.PRAEFIX + "2026-08-10T120000")
    alt.write_bytes(b"x")
    mittel.write_bytes(b"x")
    import os
    os.utime(alt, (datetime(2026, 6, 1).timestamp(),) * 2)
    os.utime(mittel, (datetime(2026, 8, 10).timestamp(),) * 2)

    entfernt = sicherung.aufraeumen(ziel, behalten=99, max_alter_tage=30,
                                    jetzt=JETZT)

    assert alt in entfernt, "75 Tage alt und trotzdem stehengeblieben"
    assert mittel not in entfernt
    assert not alt.exists()


def test_juengste_sicherung_ueberlebt_jede_altersgrenze(tmp_path):
    """Sonst stuende nach einer laengeren Pause gar keine Sicherung mehr da."""
    ziel = tmp_path / "S"
    ziel.mkdir()
    einzige = ziel / (sicherung.PRAEFIX + "2020-01-01T120000")
    einzige.write_bytes(b"x")
    import os
    os.utime(einzige, (datetime(2020, 1, 1).timestamp(),) * 2)

    entfernt = sicherung.aufraeumen(ziel, behalten=1, max_alter_tage=1,
                                    jetzt=JETZT)
    assert entfernt == []
    assert einzige.exists()


def test_aufraeumen_fasst_fremde_dateien_nicht_an(tmp_path):
    ziel = tmp_path / "S"
    ziel.mkdir()
    fremd = ziel / "liesmich.txt"
    fremd.write_text("kein Sicherungsstand", encoding="utf-8")
    for i in range(4):
        (ziel / (sicherung.PRAEFIX + f"2026-08-1{i}T120000")).write_bytes(b"x")

    sicherung.aufraeumen(ziel, behalten=1, max_alter_tage=365, jetzt=JETZT)
    assert fremd.exists()


# ── Protokoll der Loeschroutine ──────────────────────────────────────────────

def test_loeschroutine_protokolliert_je_stadt_und_je_regel(tmp_path):
    """Der Kern von T-64. Die Summenzeile, die tracker._fristen_anwenden
    schreibt, reicht nicht: aus ihr geht nicht hervor, welcher Stadt die
    entfernten Meldungen gehoerten."""
    ergebnis = {
        "je_stadt": {
            "berlin": {"quellabgang_geloescht": 12, "altbestand_aggregiert": 710,
                       "frist_quelle_tage": 30, "frist_aggregat_monate": 24},
            "koeln": {"quellabgang_geloescht": 0, "altbestand_aggregiert": 0,
                      "frist_quelle_tage": 30, "frist_aggregat_monate": 24},
        }
    }
    zeile = retention.protokolliere(ergebnis, r"C:\pfad\ordnungsamt.db",
                                    dry_run=False, log=_stummer_logger())

    assert "LIVE" in zeile
    assert r"C:\pfad\ordnungsamt.db" in zeile
    assert "berlin" in zeile and "koeln" in zeile
    assert "710" in zeile and "12" in zeile
    assert "30 Tage" in zeile and "24 Monate" in zeile


def test_trockenlauf_ist_als_solcher_erkennbar():
    zeile = retention.protokolliere({"je_stadt": {}}, "x.db", dry_run=True,
                                    log=_stummer_logger())
    assert "TROCKENLAUF" in zeile


def test_protokollzeile_landet_in_tracker_log(tmp_path, monkeypatch):
    """Dieselbe Datei, in die auch tracker.py schreibt — sonst haengt die
    Nachvollziehbarkeit daran, welcher Weg zufaellig benutzt wurde."""
    ziel = tmp_path / "tracker.log"
    monkeypatch.setattr(retention, "LOG_DATEI", ziel)
    import logging
    logging.getLogger("retention").handlers.clear()
    try:
        retention.protokolliere(
            {"je_stadt": {"berlin": {"quellabgang_geloescht": 1,
                                     "altbestand_aggregiert": 2,
                                     "frist_quelle_tage": 30,
                                     "frist_aggregat_monate": 24}}},
            "x.db", dry_run=False)
    finally:
        for h in logging.getLogger("retention").handlers:
            h.close()
        logging.getLogger("retention").handlers.clear()

    inhalt = ziel.read_text(encoding="utf-8")
    assert "Loeschroutine" in inhalt and "berlin" in inhalt


# ── Der vollstaendige Kommandozeilenweg ─────────────────────────────────────
#
# Die Tests darueber pruefen protokolliere() und sichere() einzeln. Das reicht
# nicht: niemand pruefte damit, dass main() sie auch AUFRUFT. Genau diese
# Bauart hat der Reviewer am 15.08.2026 als K-05 gefunden — eine gut getestete
# Funktion, deren Aufruf im Weg fehlte, und alle Tests blieben gruen.

def _main_lauf(argv, tmp_path, monkeypatch, quelle):
    import logging
    log_datei = tmp_path / "tracker.log"
    sicherungen = tmp_path / "Sicherungen"
    monkeypatch.setattr(retention, "LOG_DATEI", log_datei)
    monkeypatch.setattr(sicherung, "LOG_DATEI", log_datei)
    monkeypatch.setattr(sicherung, "ZIEL_VORGABE", sicherungen)
    monkeypatch.setattr(sys, "argv", ["retention.py", "--db", str(quelle)] + argv)
    for name in ("retention", "sicherung"):
        logging.getLogger(name).handlers.clear()
    try:
        code = retention.main()
    finally:
        for name in ("retention", "sicherung"):
            for h in logging.getLogger(name).handlers:
                h.close()
            logging.getLogger(name).handlers.clear()
    return code, log_datei, sicherungen


def test_kommandozeile_apply_sichert_und_protokolliert(tmp_path, monkeypatch):
    quelle = _quell_db(tmp_path, zeilen=4)
    code, log_datei, sicherungen = _main_lauf(["--apply"], tmp_path,
                                              monkeypatch, quelle)

    assert code == 0
    staende = sicherung.vorhandene(sicherungen)
    assert len(staende) == 1, (
        "main() hat vor der Loeschung nicht gesichert. Genau dieser Weg hat am "
        "15.08.2026 rund 710 unwiederbringliche Meldungen entfernt."
    )
    assert "vor-loeschroutine" in staende[0].name

    inhalt = log_datei.read_text(encoding="utf-8")
    assert "Loeschroutine [LIVE]" in inhalt, (
        "main() ruft protokolliere() nicht auf — der Vorgang bleibt wieder "
        "spurlos."
    )
    assert "Sicherung:" in inhalt


def test_kommandozeile_trockenlauf_sichert_nicht(tmp_path, monkeypatch):
    """Der Trockenlauf schreibt nichts, er braucht auch keine Sicherung — sonst
    saehe der Sicherungsordner nach Betrieb aus, wo nur geschaut wurde."""
    quelle = _quell_db(tmp_path)
    code, log_datei, sicherungen = _main_lauf(["--dry-run"], tmp_path,
                                              monkeypatch, quelle)

    assert code == 0
    assert sicherung.vorhandene(sicherungen) == []
    assert "Loeschroutine [TROCKENLAUF]" in log_datei.read_text(encoding="utf-8")


def test_kommandozeile_bricht_ab_wenn_die_sicherung_scheitert(tmp_path, monkeypatch):
    """Fail-closed. Ohne Sicherung wird NICHT geloescht — der Bestand ist
    nicht nachbeschaffbar, die Behoerde hat ihn selbst nicht mehr."""
    quelle = _quell_db(tmp_path, zeilen=4)

    def scheitert(*a, **k):
        raise RuntimeError("Ziel nicht beschreibbar")

    monkeypatch.setattr(sicherung, "sichere", scheitert)
    code, log_datei, _ = _main_lauf(["--apply"], tmp_path, monkeypatch, quelle)

    assert code == 2
    conn = sqlite3.connect(quelle)
    uebrig = conn.execute("SELECT COUNT(*) FROM meldungen").fetchone()[0]
    conn.close()
    assert uebrig == 4, "Trotz gescheiterter Sicherung wurde geloescht"
    assert not log_datei.exists() or "Loeschroutine [LIVE]" not in \
        log_datei.read_text(encoding="utf-8")


def test_keine_sicherung_ist_ein_bewusster_ausweg(tmp_path, monkeypatch):
    """Der Ausweg muss es geben — etwa auf dem Heim-Server, wo der Zielordner
    ein anderer ist. Er darf nur nicht der Normalfall sein."""
    quelle = _quell_db(tmp_path)
    code, log_datei, sicherungen = _main_lauf(["--apply", "--keine-sicherung"],
                                              tmp_path, monkeypatch, quelle)

    assert code == 0
    assert sicherung.vorhandene(sicherungen) == []
    assert "Loeschroutine [LIVE]" in log_datei.read_text(encoding="utf-8"), (
        "Auch ohne Sicherung muss der Vorgang protokolliert werden — das ist "
        "die zweite, unabhaengige Haelfte von T-64."
    )


# ── T-64, letzter Handgriff: der taegliche Lauf sichert ──────────────────────
#
# Die drei Launcher rufen sicherung.py jetzt fail-closed VOR dem Tracker auf.
# Damit haengt der ganze taegliche Betrieb am Rueckgabewert dieses Skripts, und
# der muss genau zwei Faelle unterscheiden koennen: "es gibt noch nichts zu
# sichern" ist kein Fehler, "die Sicherung ist gescheitert" sehr wohl.

def _sicherung_lauf(argv, tmp_path, monkeypatch, db_pfad):
    import logging
    log_datei = tmp_path / "tracker.log"
    ziel = tmp_path / "Sicherungen"
    monkeypatch.setattr(sicherung, "LOG_DATEI", log_datei)
    monkeypatch.setattr(sicherung, "ZIEL_VORGABE", ziel)
    monkeypatch.setattr(sicherung, "DB_PATH", db_pfad)
    monkeypatch.setattr(sys, "argv",
                        ["sicherung.py", "--db", str(db_pfad),
                         "--ziel", str(ziel)] + argv)
    logging.getLogger("sicherung").handlers.clear()
    try:
        code = sicherung.main()
    finally:
        for h in logging.getLogger("sicherung").handlers:
            h.close()
        logging.getLogger("sicherung").handlers.clear()
    return code, ziel


def test_fehlende_datenbank_ist_beim_ersten_lauf_kein_fehler(tmp_path, monkeypatch):
    """Sonst blockiert sich die fail-closed-Kette selbst.

    Bei einem frischen Aufbau gibt es noch keine Datenbank. Ohne diesen Ausweg
    scheitert die Sicherung, der Tracker laeuft deshalb nie, und weil er nie
    laeuft, entsteht auch nie eine Datenbank.
    """
    fehlt = tmp_path / "gibt-es-nicht.db"
    code, ziel = _sicherung_lauf(["--wenn-vorhanden"], tmp_path, monkeypatch, fehlt)

    assert code == 0, (
        "Eine fehlende Datenbank muss mit --wenn-vorhanden als Rueckgabewert 0 "
        "durchgehen, sonst kommt ein frischer Aufbau nie in Gang.")
    assert sicherung.vorhandene(ziel) == [], "Es wurde etwas geschrieben"


def test_ohne_den_zusatz_bleibt_die_fehlende_datenbank_ein_fehler(tmp_path, monkeypatch):
    """Gegenprobe. Der Ausweg gilt nur, wenn er ausdruecklich verlangt wird —
    von Hand aufgerufen ist eine fehlende Datenbank sehr wohl ein Befund."""
    fehlt = tmp_path / "gibt-es-nicht.db"
    code, _ = _sicherung_lauf([], tmp_path, monkeypatch, fehlt)
    assert code == 2


def test_der_zusatz_verzeiht_keine_gescheiterte_sicherung(tmp_path, monkeypatch):
    """Der wichtigste der drei. --wenn-vorhanden darf ausschliesslich den Fall
    'noch nichts da' entschaerfen. Ein FEHLGESCHLAGENER Versuch bleibt ein
    Fehler, sonst haette der Zusatz die fail-closed-Kette stillgelegt."""
    quelle = _quell_db(tmp_path, zeilen=4)

    def scheitert(*a, **k):
        raise RuntimeError("Ziel nicht beschreibbar")

    monkeypatch.setattr(sicherung, "sichere", scheitert)
    code, _ = _sicherung_lauf(["--wenn-vorhanden"], tmp_path, monkeypatch, quelle)

    assert code == 2, (
        "Mit --wenn-vorhanden wurde eine gescheiterte Sicherung als Erfolg "
        "gewertet. Damit liefe der Tracker trotz fehlender Sicherung, und "
        "genau das soll die Kette verhindern.")


def test_mit_datenbank_sichert_der_zusatz_ganz_normal(tmp_path, monkeypatch):
    """Und der Normalfall: liegt eine Datenbank da, wird auch gesichert. Ohne
    diesen Test koennte --wenn-vorhanden alles ueberspringen und niemand saehe
    es an den drei Tests darueber."""
    quelle = _quell_db(tmp_path, zeilen=4)
    code, ziel = _sicherung_lauf(["--wenn-vorhanden"], tmp_path, monkeypatch, quelle)

    assert code == 0
    staende = sicherung.vorhandene(ziel)
    assert len(staende) == 1, f"Erwartet war genau eine Sicherung, da sind {staende}"


def _stummer_logger():
    import logging
    log = logging.getLogger("retention.test")
    log.handlers.clear()
    log.addHandler(logging.NullHandler())
    log.propagate = False
    return log
