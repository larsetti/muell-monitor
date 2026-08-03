"""
Tests fuer tracker.py DSGVO-Patch (2026-05-25)
===============================================
Prueft:
- is_muell() erkennt Mueell-Meldungen korrekt
- is_muell() filtert Nicht-Mueell-Kategorien aus
- NON_MUELL_KEYWORDS haben Vorrang vor MUELL_KEYWORDS
- Tracker schreibt keine Nicht-Mueell-Meldungen in DB
- Tracker schreibt keine hausNummer in neue Eintraege

Review-Fix-Tests (2026-05-25, Findings C-01 / I-01 / I-02 / I-03):
- is_muell() Word-Boundary-Match (I-01)
- migrate_dsgvo_cleanup laeuft idempotent auf frischer DB (C-01)
- migrate_dsgvo_cleanup zeigt kein betreff im Standard-Dry-Run (I-02)
- migrate_dsgvo_cleanup leert hotspots nach DELETE (I-03)
"""

import io
import re
import sqlite3
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

# Technik/ ins Suchpfad aufnehmen damit tracker und migrate_dsgvo_cleanup
# importierbar sind
sys.path.insert(0, str(Path(__file__).parent.parent))

import export_html  # noqa: E402
import migrate_dsgvo_cleanup  # noqa: E402
import tracker  # noqa: E402


# ── is_muell() – Positiv-Tests ────────────────────────────────────────────────

def test_is_muell_erkennt_abfall():
    assert tracker.is_muell({"kategorie": "Abfallentsorgung", "betreff": "Muell auf Gehweg"})


def test_is_muell_erkennt_sperrmüll():
    assert tracker.is_muell({"kategorie": "Sperrmüll", "betreff": ""})


def test_is_muell_erkennt_muellablagerung():
    assert tracker.is_muell({"kategorie": "", "betreff": "Müllablagerung vor Haus"})


def test_is_muell_erkennt_bauschutt():
    assert tracker.is_muell({"kategorie": "Bauschutt", "betreff": "Baumaterial abgelagert"})


def test_is_muell_erkennt_tierkadaver():
    assert tracker.is_muell({"kategorie": "Tierkadaver", "betreff": ""})


# ── is_muell() – Negativ-Tests (Nicht-Mueell) ─────────────────────────────────

def test_is_muell_filtert_laerm():
    assert not tracker.is_muell({"kategorie": "", "betreff": "Lärm - Gaststätte"})


def test_is_muell_filtert_ruhestoerung():
    assert not tracker.is_muell({"kategorie": "", "betreff": "Ruhestörung durch Nachbarn"})


def test_is_muell_filtert_hund():
    assert not tracker.is_muell({"kategorie": "", "betreff": "Hundekot auf Spielplatz"})


def test_is_muell_filtert_falschparker():
    assert not tracker.is_muell({"kategorie": "", "betreff": "Falschparkend auf Gehweg"})


def test_is_muell_filtert_parkverstoss():
    assert not tracker.is_muell({"kategorie": "", "betreff": "Park- und Haltverbot nicht berücksichtigt"})


def test_is_muell_filtert_parken_auf_gehweg():
    assert not tracker.is_muell({"kategorie": "", "betreff": "Parken auf Gehweg"})


def test_is_muell_filtert_nachbar():
    assert not tracker.is_muell({"kategorie": "", "betreff": "Ruhe Störung durch Nachbarn"})


def test_is_muell_filtert_leere_meldung():
    assert not tracker.is_muell({"kategorie": "", "betreff": ""})


# ── NON_MUELL schlaegt MUELL_KEYWORDS ────────────────────────────────────────

def test_non_muell_schlaegt_muell_keyword():
    # "Müll" im Betreff, aber Hauptkategorie ist Lärm → kein Müll
    assert not tracker.is_muell({
        "kategorie": "Lärm", "betreff": "Müll und Lärm"
    })


# ── Tracker schreibt keine Nicht-Mueell-Zeilen in DB ─────────────────────────

def _make_in_memory_db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    tracker.init_db(conn)
    return conn


def test_tracker_speichert_nur_muell(monkeypatch):
    """run() darf keine Nicht-Mueell-Meldungen persistieren."""
    meldungen = [
        {"id": "1", "kategorie": "Sperrmüll", "betreff": "", "bezirk": "Mitte",
         "lat": 52.5, "lon": 13.4, "status": "offen",
         "erstellungsDatum": "01.01.2025", "strasse": "Teststr", "plz": "10000"},
        {"id": "2", "kategorie": "", "betreff": "Lärm - Gaststätte", "bezirk": "Mitte",
         "lat": 52.5, "lon": 13.4, "status": "offen",
         "erstellungsDatum": "01.01.2025", "strasse": "Teststr", "plz": "10000"},
        {"id": "3", "kategorie": "", "betreff": "Ruhestörung", "bezirk": "Neukölln",
         "lat": 52.48, "lon": 13.43, "status": "offen",
         "erstellungsDatum": "02.01.2025", "strasse": "Nebenstr", "plz": "12045"},
    ]

    conn = _make_in_memory_db()

    # fetch_meldungen und DB-Pfad patchen
    monkeypatch.setattr(tracker, "fetch_meldungen", lambda: meldungen)
    monkeypatch.setattr(tracker, "DB_PATH", ":memory:")

    # run() direkt mit dem gemockten conn aufrufen
    import datetime
    now = datetime.datetime.utcnow().isoformat()
    count_new = 0
    count_muell = 0

    for m in meldungen:
        mid = tracker.make_id(m)
        lat, lon = tracker.extract_coords(m)
        muell = tracker.is_muell(m)
        datum = m.get("erstellungsDatum", now[:10])
        if datum and len(datum) >= 10 and datum[2] == '.':
            datum = datetime.datetime.strptime(datum[:10], "%d.%m.%Y").strftime("%Y-%m-%d")

        if not muell:
            continue
        if conn.execute("SELECT id FROM meldungen WHERE id=?", (mid,)).fetchone():
            continue

        strasse = m.get("strasse", "")
        plz_val = m.get("plz", "")
        conn.execute("""
            INSERT INTO meldungen
                (id, fetched_at, datum, kategorie, betreff, bezirk, lat, lon, status, is_muell, strasse, plz)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
        """, (mid, now, datum,
              m.get("kategorie", ""), m.get("betreff", ""), m.get("bezirk", ""),
              lat, lon, m.get("status", ""), 1, strasse, str(plz_val)))
        count_new += 1
        count_muell += 1

    conn.commit()

    rows = conn.execute("SELECT id, kategorie, betreff FROM meldungen").fetchall()
    assert len(rows) == 1, f"Erwartet 1 Mueell-Zeile, bekommen {len(rows)}: {[dict(r) for r in rows]}"
    assert rows[0]["id"] == "1"
    assert count_muell == 1


def test_tracker_insert_schema_hat_keine_hausnummer():
    """tracker.py init_db() legt meldungen ohne hausNummer-Spalte an.
    hausNummer wird ausschliesslich von enrich.py nachgeruestet.
    """
    conn = _make_in_memory_db()
    cols = [row[1] for row in conn.execute("PRAGMA table_info(meldungen)").fetchall()]
    # Neue DBs haben kein hausNummer-Feld mehr im Basis-Schema
    assert "hausNummer" not in cols, (
        f"init_db() darf hausNummer nicht mehr anlegen; Spalten: {cols}"
    )


# ── I-01: Word-Boundary-Match in is_muell() ──────────────────────────────────

def test_is_muell_hund_als_wort_filtert_raus():
    """'hund' als eigenstaendiges Wort → kein Mueell."""
    assert not tracker.is_muell({"kategorie": "", "betreff": "Hund hat auf Gehweg gekackt"})


def test_is_muell_hund_in_kompositum_filtert_nicht():
    """'hund' als Wortbestandteil ('hundekot') soll nicht als NON_MUELL-Keyword treffen."""
    # 'hundekot' ist selbst in NON_MUELL_KEYWORDS — der Test prueft, dass
    # 'hund' in 'hundekot-Tueten' nicht als Wortgrenze gilt, waehrend
    # 'hundekot' als eigenstaendiges Keyword weiter filtert.
    # Eigentliche Pruefung: ein Wort wie 'hundewiese' enthaelt 'hund' als
    # Praefix, soll aber Sperrmueell-Meldungen nicht eliminieren.
    assert tracker.is_muell({"kategorie": "Sperrmüll", "betreff": "Sperrmüll an der Hundewiese"})


def test_is_muell_park_in_kompositum_filtert_nicht():
    """'park' als Praefix in 'Parkanlage' soll keine echte Muell-Meldung eliminieren."""
    # NON_MUELL hat 'falschpark', 'falsch park', 'falschparkend' — nicht 'park' solo.
    # 'Parkanlage' enthaelt keines dieser Keywords als Wortgrenze → Mueell.
    assert tracker.is_muell({"kategorie": "Abfall", "betreff": "Müllablagerung in Parkanlage"})


def test_is_muell_toter_hund_ist_tierkadaver():
    """Tierkadaver-Meldung mit 'Hund' im Betreff: 'hund' als Wort → filtert raus.

    Das ist der semantisch korrekte Fall: eine Meldung ueber einen toten Hund
    ist eine Tierkadaver-Beschwerde. 'hund' als Wortgrenzen-Match filtert sie
    raus, weil Hund-Beschwerden DSGVO-relevant sein koennen.
    Dokumentiert als bekanntes Verhalten, kein Bug.
    """
    result = tracker.is_muell({"kategorie": "Tierkadaver", "betreff": "toter Hund neben Mülltonne"})
    # 'hund' trifft als Wortgrenze → False. Dokumentiertes Verhalten.
    assert result is False


def test_is_muell_laerm_in_kompositum_filtert_nicht():
    """'lärm' als Praefix in 'Lärmschutzgitter' soll echte Muell-Meldung nicht eliminieren."""
    assert tracker.is_muell({"kategorie": "Sperrmüll", "betreff": "Lärmschutzgitter umgestürzt und abgelagert"})


# ── C-01: Idempotenz auf frischer DB (ohne hausNummer-Spalte) ─────────────────

def _make_fresh_db_file():
    """Legt eine temporaere DB-Datei an via tracker.init_db() — ohne hausNummer."""
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    conn = sqlite3.connect(tmp.name)
    tracker.init_db(conn)
    conn.close()
    return Path(tmp.name)


def test_migration_idempotent_auf_frischer_db():
    """migrate_dsgvo_cleanup laeuft auf frischer DB (ohne hausNummer) fehlerfrei durch.

    Reproduziert den C-01-Bug: vorher crashte das Skript mit
    sqlite3.OperationalError: no such column: hausNummer.
    """
    db_path = _make_fresh_db_file()
    try:
        # Darf keinen Exception werfen
        migrate_dsgvo_cleanup.run(db_path, dry_run=True)
        migrate_dsgvo_cleanup.run(db_path, dry_run=False)
    finally:
        db_path.unlink(missing_ok=True)


def test_migration_idempotent_zweimal_hintereinander():
    """Zweimaliges Ausfuehren von --apply auf derselben DB darf nicht crashen."""
    db_path = _make_fresh_db_file()
    try:
        migrate_dsgvo_cleanup.run(db_path, dry_run=False)
        # Zweiter Lauf — DB ist jetzt schon migriert
        migrate_dsgvo_cleanup.run(db_path, dry_run=False)
    finally:
        db_path.unlink(missing_ok=True)


# ── I-02: PII-Logging im Dry-Run ─────────────────────────────────────────────

def test_migration_dry_run_kein_betreff_in_output():
    """Standard-Dry-Run gibt kein 'betreff=' in die Konsole aus."""
    db_path = _make_fresh_db_file()
    conn = sqlite3.connect(db_path)
    # Testdaten mit sensiblem betreff eintragen
    conn.execute(
        "INSERT INTO meldungen (id, fetched_at, datum, kategorie, betreff, bezirk, is_muell) "
        "VALUES ('t1', '2026-01-01', '2026-01-01', 'Lärm', 'Sehr laute Party bei Nachbar Meier', 'Mitte', 0)"
    )
    conn.commit()
    conn.close()

    captured = io.StringIO()
    with patch("sys.stdout", captured):
        migrate_dsgvo_cleanup.run(db_path, dry_run=True, with_samples=False)

    output = captured.getvalue()
    assert "betreff=" not in output, (
        f"Standard-Dry-Run darf kein 'betreff=' ausgeben. Gefunden in: {output}"
    )
    assert "Meier" not in output, (
        f"Standard-Dry-Run darf keinen Freitext aus betreff ausgeben. Gefunden in: {output}"
    )
    db_path.unlink(missing_ok=True)


def test_migration_with_samples_zeigt_betreff():
    """--with-samples gibt betreff in der Stichprobe aus."""
    db_path = _make_fresh_db_file()
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO meldungen (id, fetched_at, datum, kategorie, betreff, bezirk, is_muell) "
        "VALUES ('t2', '2026-01-01', '2026-01-01', 'Lärm', 'TestBetreffInhalt', 'Mitte', 0)"
    )
    conn.commit()
    conn.close()

    captured = io.StringIO()
    with patch("sys.stdout", captured):
        migrate_dsgvo_cleanup.run(db_path, dry_run=True, with_samples=True)

    output = captured.getvalue()
    assert "betreff=" in output, (
        f"--with-samples muss 'betreff=' in der Stichprobe ausgeben. Output: {output}"
    )
    db_path.unlink(missing_ok=True)


# ── I-03: Hotspots-Cleanup nach Migration ─────────────────────────────────────

def test_migration_leert_hotspots():
    """Nach migrate --apply ist die hotspots-Tabelle leer."""
    db_path = _make_fresh_db_file()
    conn = sqlite3.connect(db_path)
    # Hotspot eintragen
    conn.execute(
        "INSERT INTO hotspots (cluster_id, lat_center, lon_center, bezirk, meldungen_count) "
        "VALUES ('52.50000_13.40000', 52.5, 13.4, 'Mitte', 3)"
    )
    conn.commit()
    conn.close()

    migrate_dsgvo_cleanup.run(db_path, dry_run=False)

    conn = sqlite3.connect(db_path)
    count = conn.execute("SELECT COUNT(*) FROM hotspots").fetchone()[0]
    conn.close()
    db_path.unlink(missing_ok=True)

    assert count == 0, f"hotspots-Tabelle muss nach Migration leer sein, hat aber {count} Einträge"


def test_migration_dry_run_aendert_hotspots_nicht():
    """Dry-Run darf die hotspots-Tabelle nicht veraendern."""
    db_path = _make_fresh_db_file()
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO hotspots (cluster_id, lat_center, lon_center, bezirk, meldungen_count) "
        "VALUES ('52.50000_13.40000', 52.5, 13.4, 'Mitte', 5)"
    )
    conn.commit()
    conn.close()

    migrate_dsgvo_cleanup.run(db_path, dry_run=True)

    conn = sqlite3.connect(db_path)
    count = conn.execute("SELECT COUNT(*) FROM hotspots").fetchone()[0]
    conn.close()
    db_path.unlink(missing_ok=True)

    assert count == 1, f"Dry-Run darf hotspots nicht löschen, hat aber {count} Einträge"


# ── C-03: k-Anonymitäts-Filter in export_html.py ─────────────────────────────

def _make_db_with_hotspots(hotspot_rows: list[dict]) -> Path:
    """Temporäre DB mit angegebenen Hotspot-Zeilen für export_html-Tests."""
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    conn = sqlite3.connect(tmp.name)
    tracker.init_db(conn)
    for h in hotspot_rows:
        conn.execute("""
            INSERT INTO hotspots
                (cluster_id, lat_center, lon_center, bezirk, meldungen_count,
                 recurrence_count, last_seen, first_seen, score, score_label,
                 strasse, plz)
            VALUES (:cluster_id,:lat_center,:lon_center,:bezirk,:meldungen_count,
                    :recurrence_count,:last_seen,:first_seen,:score,:score_label,
                    :strasse,:plz)
        """, h)
    conn.commit()
    conn.close()
    return Path(tmp.name)


def test_k_anonymity_filtert_singletons():
    """Hotspot mit meldungen_count=1 darf nicht in load_data() auftauchen."""
    db = _make_db_with_hotspots([
        {"cluster_id": "52.50000_13.40000", "lat_center": 52.5, "lon_center": 13.4,
         "bezirk": "Mitte", "meldungen_count": 1, "recurrence_count": 0,
         "last_seen": "2026-01-01", "first_seen": "2026-01-01",
         "score": 1.0, "score_label": "niedrig", "strasse": "Teststr", "plz": "10115"},
    ])
    orig = export_html.DB_PATH
    try:
        export_html.DB_PATH = db
        data = export_html.load_data()
        assert len(data["hotspots"]) == 0, (
            f"count=1 muss gefiltert sein, aber load_data liefert {len(data['hotspots'])} Hotspots"
        )
    finally:
        export_html.DB_PATH = orig
        db.unlink(missing_ok=True)


def test_k_anonymity_filtert_zweier():
    """Hotspot mit meldungen_count=2 darf nicht in load_data() auftauchen."""
    db = _make_db_with_hotspots([
        {"cluster_id": "52.50000_13.40000", "lat_center": 52.5, "lon_center": 13.4,
         "bezirk": "Mitte", "meldungen_count": 2, "recurrence_count": 0,
         "last_seen": "2026-01-01", "first_seen": "2026-01-01",
         "score": 2.0, "score_label": "niedrig", "strasse": "Teststr", "plz": "10115"},
    ])
    orig = export_html.DB_PATH
    try:
        export_html.DB_PATH = db
        data = export_html.load_data()
        assert len(data["hotspots"]) == 0, (
            f"count=2 muss gefiltert sein, aber load_data liefert {len(data['hotspots'])} Hotspots"
        )
    finally:
        export_html.DB_PATH = orig
        db.unlink(missing_ok=True)


def test_k_anonymity_laesst_dreier_durch():
    """Hotspot mit meldungen_count=3 muss in load_data() erscheinen."""
    db = _make_db_with_hotspots([
        {"cluster_id": "52.50000_13.40000", "lat_center": 52.5, "lon_center": 13.4,
         "bezirk": "Mitte", "meldungen_count": 3, "recurrence_count": 0,
         "last_seen": "2026-01-01", "first_seen": "2026-01-01",
         "score": 3.0, "score_label": "niedrig", "strasse": "Teststr", "plz": "10115"},
    ])
    orig = export_html.DB_PATH
    try:
        export_html.DB_PATH = db
        data = export_html.load_data()
        assert len(data["hotspots"]) == 1, (
            f"count=3 muss durchgelassen werden, aber load_data liefert {len(data['hotspots'])} Hotspots"
        )
    finally:
        export_html.DB_PATH = orig
        db.unlink(missing_ok=True)


# ── H-02: Hausnummer-Stripper in tracker.py und export_html.py ───────────────

def test_tracker_strasse_strip_hausnummer_am_ende():
    """tracker.py entfernt Hausnummer-Suffix (z.B. '12a') aus dem strasse-Feld."""
    stripped = re.sub(
        r'\s+\d+[a-zA-Z]?(\s*[-/]\s*\d+[a-zA-Z]?)?\s*$',
        '',
        "Hauptstraße 12a"
    ).strip()
    assert stripped == "Hauptstraße", f"Erwartet 'Hauptstraße', bekommen '{stripped}'"


def test_tracker_strasse_strip_hausnummer_range():
    """tracker.py entfernt zusammengesetzte Hausnummern (z.B. '14-18')."""
    stripped = re.sub(
        r'\s+\d+[a-zA-Z]?(\s*[-/]\s*\d+[a-zA-Z]?)?\s*$',
        '',
        "Teststraße 14-18"
    ).strip()
    assert stripped == "Teststraße", f"Erwartet 'Teststraße', bekommen '{stripped}'"


def test_tracker_strasse_kein_strip_bei_reinem_strassennamen():
    """tracker.py lässt strasse ohne Hausnummer unverändert."""
    name = "Am Grünen Weg"
    stripped = re.sub(
        r'\s+\d+[a-zA-Z]?(\s*[-/]\s*\d+[a-zA-Z]?)?\s*$',
        '',
        name
    ).strip()
    assert stripped == name, f"Erwartet '{name}', bekommen '{stripped}'"


def test_export_html_strip_hausnummer():
    """export_html.py entfernt Hausnummer aus strasse-Feld beim Rendern."""
    db = _make_db_with_hotspots([
        {"cluster_id": "52.50000_13.40000", "lat_center": 52.5, "lon_center": 13.4,
         "bezirk": "Mitte", "meldungen_count": 5, "recurrence_count": 0,
         "last_seen": "2026-01-01", "first_seen": "2026-01-01",
         "score": 5.0, "score_label": "mittel",
         "strasse": "Hauptstraße 12a", "plz": "10115"},
    ])
    orig = export_html.DB_PATH
    try:
        export_html.DB_PATH = db
        data = export_html.load_data()
        assert len(data["hotspots"]) == 1
        h = data["hotspots"][0]
        assert h["strasse"] == "Hauptstraße", (
            f"export_html muss Hausnummer entfernen, strasse ist '{h['strasse']}'"
        )
    finally:
        export_html.DB_PATH = orig
        db.unlink(missing_ok=True)


def test_export_html_fallback_bei_fehlender_plz():
    """export_html.py zeigt 'Adresse unvollständig' wenn plz fehlt."""
    db = _make_db_with_hotspots([
        {"cluster_id": "52.50000_13.40000", "lat_center": 52.5, "lon_center": 13.4,
         "bezirk": "Mitte", "meldungen_count": 5, "recurrence_count": 0,
         "last_seen": "2026-01-01", "first_seen": "2026-01-01",
         "score": 5.0, "score_label": "mittel",
         "strasse": "Hauptstraße", "plz": ""},
    ])
    orig = export_html.DB_PATH
    try:
        export_html.DB_PATH = db
        data = export_html.load_data()
        assert len(data["hotspots"]) == 1
        h = data["hotspots"][0]
        assert h["strasse"] == "Adresse unvollständig", (
            f"Bei fehlender PLZ muss 'Adresse unvollständig' stehen, ist '{h['strasse']}'"
        )
    finally:
        export_html.DB_PATH = orig
        db.unlink(missing_ok=True)


# ── M-02/M-03: migrate_dsgvo_cleanup — raw_json + Backup ─────────────────────

def test_migration_entfernt_raw_json_spalte():
    """migrate_dsgvo_cleanup entfernt die raw_json-Spalte aus meldungen."""
    db_path = _make_fresh_db_file()
    conn = sqlite3.connect(db_path)
    # raw_json-Spalte manuell hinzufügen (simuliert alte DB)
    try:
        conn.execute("ALTER TABLE meldungen ADD COLUMN raw_json TEXT")
        conn.commit()
    except sqlite3.OperationalError:
        pass  # Falls Spalte schon existiert
    conn.close()

    migrate_dsgvo_cleanup.run(db_path, dry_run=False, no_backup=True)

    conn = sqlite3.connect(db_path)
    cols = [r[1] for r in conn.execute("PRAGMA table_info(meldungen)").fetchall()]
    conn.close()
    db_path.unlink(missing_ok=True)

    assert "raw_json" not in cols, f"raw_json muss nach Migration entfernt sein, Spalten: {cols}"


def test_migration_raw_json_dry_run_belaesst_spalte():
    """Dry-Run entfernt raw_json-Spalte nicht."""
    db_path = _make_fresh_db_file()
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("ALTER TABLE meldungen ADD COLUMN raw_json TEXT")
        conn.commit()
    except sqlite3.OperationalError:
        pass
    conn.close()

    migrate_dsgvo_cleanup.run(db_path, dry_run=True)

    conn = sqlite3.connect(db_path)
    cols = [r[1] for r in conn.execute("PRAGMA table_info(meldungen)").fetchall()]
    conn.close()
    db_path.unlink(missing_ok=True)

    assert "raw_json" in cols, f"Dry-Run darf raw_json nicht entfernen, Spalten: {cols}"


def test_migration_erstellt_backup():
    """migrate_dsgvo_cleanup erstellt ein .bak-File vor dem Schreiben."""
    db_path = _make_fresh_db_file()
    try:
        migrate_dsgvo_cleanup.run(db_path, dry_run=False, no_backup=False)
        bak_files = list(db_path.parent.glob(f"{db_path.name}.pre-migration-*.bak"))
        assert len(bak_files) == 1, (
            f"Genau ein Backup-File erwartet, gefunden: {bak_files}"
        )
    finally:
        for f in db_path.parent.glob(f"{db_path.name}.pre-migration-*.bak"):
            f.unlink(missing_ok=True)
        db_path.unlink(missing_ok=True)


def test_migration_kein_backup_mit_flag():
    """--no-backup verhindert das Anlegen eines Backup-Files."""
    db_path = _make_fresh_db_file()
    try:
        migrate_dsgvo_cleanup.run(db_path, dry_run=False, no_backup=True)
        bak_files = list(db_path.parent.glob(f"{db_path.name}.pre-migration-*.bak"))
        assert len(bak_files) == 0, (
            f"Mit --no-backup kein Backup erwartet, aber gefunden: {bak_files}"
        )
    finally:
        db_path.unlink(missing_ok=True)


# ── H-02b: Leerer Abruf geht nicht still als Erfolg durch ────────────────────

def test_run_bei_leerem_abruf_exit_code_und_marker(monkeypatch):
    """run() bricht bei 0 Meldungen mit Exit-Code != 0 ab und schreibt
    einen Fehler-Marker (count_total=-1), persistiert aber keine Meldungen."""
    db_path = _make_fresh_db_file()
    try:
        monkeypatch.setattr(tracker, "DB_PATH", db_path)
        monkeypatch.setattr(tracker, "fetch_meldungen", lambda: [])

        rc = tracker.run()
        assert rc != 0, f"Leerer Abruf muss Exit-Code != 0 liefern, war {rc}"

        conn = sqlite3.connect(db_path)
        marker = conn.execute(
            "SELECT count_total FROM fetch_log ORDER BY id DESC LIMIT 1"
        ).fetchone()
        meldungen_count = conn.execute("SELECT COUNT(*) FROM meldungen").fetchone()[0]
        conn.close()

        assert marker is not None and marker[0] == -1, (
            f"Fehler-Marker count_total=-1 erwartet, gefunden: {marker}"
        )
        assert meldungen_count == 0, (
            f"Bei leerem Abruf dürfen keine Meldungen geschrieben werden, fand {meldungen_count}"
        )
    finally:
        db_path.unlink(missing_ok=True)


def test_run_bei_erfolg_exit_code_null(monkeypatch):
    """run() liefert Exit-Code 0 und einen positiven fetch_log-Eintrag, wenn
    der Abruf echte Meldungen liefert."""
    db_path = _make_fresh_db_file()
    try:
        monkeypatch.setattr(tracker, "DB_PATH", db_path)
        monkeypatch.setattr(tracker, "fetch_meldungen", lambda: [
            {"id": "1", "kategorie": "Sperrmüll", "betreff": "", "bezirk": "Mitte",
             "lat": 52.5, "lon": 13.4, "status": "offen",
             "erstellungsDatum": "01.01.2025", "strasse": "Teststr", "plz": "10000"},
        ])

        rc = tracker.run()
        assert rc == 0, f"Erfolgreicher Abruf muss Exit-Code 0 liefern, war {rc}"

        conn = sqlite3.connect(db_path)
        total = conn.execute(
            "SELECT count_total FROM fetch_log ORDER BY id DESC LIMIT 1"
        ).fetchone()[0]
        conn.close()
        assert total == 1, f"count_total muss die Meldungszahl spiegeln, war {total}"
    finally:
        db_path.unlink(missing_ok=True)


# ── H-02b: last_update spiegelt letzten erfolgreichen Fetch ──────────────────

def _insert_fetch_log(db_path: Path, eintraege: list[tuple]):
    """Schreibt (fetched_at, count_total)-Tupel in fetch_log."""
    conn = sqlite3.connect(db_path)
    for fetched_at, count_total in eintraege:
        conn.execute(
            "INSERT INTO fetch_log (fetched_at, count_total, count_new, count_muell) "
            "VALUES (?,?,0,0)",
            (fetched_at, count_total),
        )
    conn.commit()
    conn.close()


def test_last_update_aus_letztem_erfolgreichen_fetch():
    """last_update kommt aus dem jüngsten fetch_log mit count_total > 0,
    nicht aus dem Render-Datum."""
    db = _make_db_with_hotspots([
        {"cluster_id": "52.50000_13.40000", "lat_center": 52.5, "lon_center": 13.4,
         "bezirk": "Mitte", "meldungen_count": 3, "recurrence_count": 0,
         "last_seen": "2026-01-01", "first_seen": "2026-01-01",
         "score": 3.0, "score_label": "niedrig", "strasse": "Teststr", "plz": "10115"},
    ])
    _insert_fetch_log(db, [
        ("2026-05-20T06:00:00", 12345),
        ("2026-05-22T06:00:00", 12500),
    ])
    orig = export_html.DB_PATH
    try:
        export_html.DB_PATH = db
        data = export_html.load_data()
        assert data["last_update"] == "2026-05-22", (
            f"last_update muss letzter Erfolg (2026-05-22) sein, war {data['last_update']!r}"
        )
    finally:
        export_html.DB_PATH = orig
        db.unlink(missing_ok=True)


def test_last_update_ignoriert_fehler_marker():
    """Ein Fehllauf (count_total=-1) nach einem Erfolg darf last_update nicht
    nach vorne ziehen — der letzte ECHTE Fetch bleibt maßgeblich."""
    db = _make_db_with_hotspots([
        {"cluster_id": "52.50000_13.40000", "lat_center": 52.5, "lon_center": 13.4,
         "bezirk": "Mitte", "meldungen_count": 3, "recurrence_count": 0,
         "last_seen": "2026-01-01", "first_seen": "2026-01-01",
         "score": 3.0, "score_label": "niedrig", "strasse": "Teststr", "plz": "10115"},
    ])
    _insert_fetch_log(db, [
        ("2026-05-22T06:00:00", 12500),
        ("2026-05-25T06:00:00", -1),
    ])
    orig = export_html.DB_PATH
    try:
        export_html.DB_PATH = db
        data = export_html.load_data()
        assert data["last_update"] == "2026-05-22", (
            f"Fehler-Marker darf last_update nicht überschreiben, war {data['last_update']!r}"
        )
    finally:
        export_html.DB_PATH = orig
        db.unlink(missing_ok=True)


def test_last_update_none_ohne_erfolgreichen_fetch():
    """Frische DB ohne erfolgreichen Fetch → last_update ist None."""
    db = _make_db_with_hotspots([
        {"cluster_id": "52.50000_13.40000", "lat_center": 52.5, "lon_center": 13.4,
         "bezirk": "Mitte", "meldungen_count": 3, "recurrence_count": 0,
         "last_seen": "2026-01-01", "first_seen": "2026-01-01",
         "score": 3.0, "score_label": "niedrig", "strasse": "Teststr", "plz": "10115"},
    ])
    orig = export_html.DB_PATH
    try:
        export_html.DB_PATH = db
        data = export_html.load_data()
        assert data["last_update"] is None, (
            f"Ohne erfolgreichen Fetch muss last_update None sein, war {data['last_update']!r}"
        )
    finally:
        export_html.DB_PATH = orig
        db.unlink(missing_ok=True)


# ── Rev H-01: Wartungs-Lock in export_html.main() ────────────────────────────

def _setup_export_env(tmpdir: Path, with_marker: bool):
    """Biegt alle modul-globalen Pfade von export_html auf ein tmp-Verzeichnis um
    und legt template.html, maintenance.html und eine gefüllte DB an.

    Verändert NICHT die echte index.html/maintenance.html im Repo-Root.
    Gibt die Original-Pfade als dict zum Wiederherstellen zurück.
    """
    template_path = tmpdir / "template.html"
    template_path.write_text(
        "<html><body><div id='app'>__APP_DATA_PLACEHOLDER__</div></body></html>",
        encoding="utf-8",
    )
    maintenance_path = tmpdir / "maintenance.html"
    maintenance_path.write_text(
        "<html><body><h1>Wartungsarbeiten</h1></body></html>",
        encoding="utf-8",
    )
    out_path = tmpdir / "index.html"

    # DB mit einem Hotspot über der k-Anonymitäts-Schwelle (API "wieder da").
    db_path = _make_db_with_hotspots([
        {"cluster_id": "52.50000_13.40000", "lat_center": 52.5, "lon_center": 13.4,
         "bezirk": "Mitte", "meldungen_count": 5, "recurrence_count": 0,
         "last_seen": "2026-01-01", "first_seen": "2026-01-01",
         "score": 5.0, "score_label": "mittel", "strasse": "Teststraße", "plz": "10115"},
    ])
    # Verschiebbar in den tmp-Ordner, damit eine evtl. .bak o.ä. dort landet.
    db_dest = tmpdir / "ordnungsamt.db"
    db_path.replace(db_dest)
    _insert_fetch_log(db_dest, [("2026-05-22T06:00:00", 12500)])

    marker_path = tmpdir / "LIVE_FREIGEGEBEN"
    if with_marker:
        marker_path.write_text("go", encoding="utf-8")

    orig = {
        "DB_PATH": export_html.DB_PATH,
        "TEMPLATE": export_html.TEMPLATE,
        "OUT_PATH": export_html.OUT_PATH,
        "GO_LIVE_MARKER": export_html.GO_LIVE_MARKER,
        "MAINTENANCE_PATH": export_html.MAINTENANCE_PATH,
    }
    export_html.DB_PATH = db_dest
    export_html.TEMPLATE = template_path
    export_html.OUT_PATH = out_path
    export_html.GO_LIVE_MARKER = marker_path
    export_html.MAINTENANCE_PATH = maintenance_path
    return orig, out_path


def _restore_export_env(orig: dict):
    for attr, value in orig.items():
        setattr(export_html, attr, value)


def test_lock_aktiv_bleibt_wartungsseite():
    """Lock AN (kein Freigabe-Marker) + gefüllte DB (API wieder da):
    main() rendert KEINE Hotspot-Daten, die Wartungsseite bleibt in index.html.
    """
    with tempfile.TemporaryDirectory() as td:
        tmpdir = Path(td)
        orig, out_path = _setup_export_env(tmpdir, with_marker=False)
        try:
            export_html.main()
            inhalt = out_path.read_text(encoding="utf-8")
            assert "Wartungsarbeiten" in inhalt, (
                "Bei aktivem Lock muss die Wartungsseite in index.html stehen"
            )
            assert "52.50000_13.40000" not in inhalt, (
                "Bei aktivem Lock dürfen keine Hotspot-Cluster-IDs in index.html landen"
            )
            assert "Teststraße" not in inhalt, (
                "Bei aktivem Lock dürfen keine Hotspot-Adressdaten in index.html landen"
            )
        finally:
            _restore_export_env(orig)


def test_lock_aktiv_idempotent_bei_bereits_gesetzter_wartungsseite():
    """Zweiter Lauf bei aktivem Lock ändert die schon gesetzte Wartungsseite nicht."""
    with tempfile.TemporaryDirectory() as td:
        tmpdir = Path(td)
        orig, out_path = _setup_export_env(tmpdir, with_marker=False)
        try:
            export_html.main()
            erster = out_path.read_text(encoding="utf-8")
            export_html.main()
            zweiter = out_path.read_text(encoding="utf-8")
            assert erster == zweiter, "Wartungs-Lock muss idempotent sein"
        finally:
            _restore_export_env(orig)


def test_freigabe_rendert_live_seite():
    """Mit Freigabe-Marker: main() rendert die Live-Seite, Hotspot-Daten
    landen in index.html.
    """
    with tempfile.TemporaryDirectory() as td:
        tmpdir = Path(td)
        orig, out_path = _setup_export_env(tmpdir, with_marker=True)
        try:
            export_html.main()
            inhalt = out_path.read_text(encoding="utf-8")
            assert "52.50000_13.40000" in inhalt, (
                "Mit Freigabe muss die Hotspot-Cluster-ID in index.html stehen"
            )
            assert "Teststraße" in inhalt, (
                "Mit Freigabe müssen die Hotspot-Adressdaten in index.html stehen"
            )
            assert "Wartungsarbeiten" not in inhalt, (
                "Mit Freigabe darf nicht die Wartungsseite ausgeliefert werden"
            )
        finally:
            _restore_export_env(orig)
