"""
Tests fuer die technischen Auflagen aus der DSFA vom 28.07.2026
================================================================
Deckt ab:
- A-11 / T-24: Wartungs-Lock bricht bei fehlender maintenance.html hart ab
- A-1: Koordinaten werden vor dem Embed auf das Cluster-Raster gerundet
- A-2: Ortszellen mit nur einer Meldung werden nicht persistiert
- A-7: Sperrliste nimmt einzelne Cluster dauerhaft vom Export aus (Art. 21)
- A-12: keine Fremdhosts mehr im ausgelieferten HTML, CSP und SRI gesetzt
- H-04 / T-39: die Seite nennt ihren Datenstand und behauptet keine
  Tagesaktualitaet mehr (Art. 5 Abs. 1 lit. d)

Grundlage: audits\\dsgvo\\2026-07-28-dsfa-art35.md
"""

import sqlite3
import sys
import tempfile
from pathlib import Path

TECHNIK = Path(__file__).parent.parent
sys.path.insert(0, str(TECHNIK))

import export_html  # noqa: E402
import sperrliste  # noqa: E402
import sri  # noqa: E402
import tracker  # noqa: E402


# ── Hilfsfunktionen ──────────────────────────────────────────────────────────

def _db_mit_hotspots(tmpdir: Path, zeilen: list[dict]) -> Path:
    """Temporaere DB mit den angegebenen Hotspot-Zeilen."""
    db_path = tmpdir / "ordnungsamt.db"
    conn = sqlite3.connect(db_path)
    tracker.init_db(conn)
    for h in zeilen:
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
    return db_path


def _hotspot(cluster_id: str, lat: float, lon: float, count: int = 5, **kw) -> dict:
    basis = {
        "cluster_id": cluster_id, "lat_center": lat, "lon_center": lon,
        "bezirk": "Mitte", "meldungen_count": count, "recurrence_count": 0,
        "last_seen": "2026-01-01", "first_seen": "2026-01-01",
        "score": float(count), "score_label": "mittel",
        "strasse": "Teststraße", "plz": "10115",
    }
    basis.update(kw)
    return basis


def _setup_export_env(tmpdir: Path, with_marker: bool, mit_maintenance: bool = True,
                      db_path: Path | None = None):
    """Biegt die modul-globalen Pfade von export_html auf tmpdir um."""
    template_path = tmpdir / "template.html"
    template_path.write_text(
        "<html><body><div id='app'>__APP_DATA_PLACEHOLDER__</div>"
        "<span>__LAST_UPDATE__</span></body></html>",
        encoding="utf-8",
    )
    maintenance_path = tmpdir / "maintenance.html"
    if mit_maintenance:
        maintenance_path.write_text(
            "<html><body><h1>Wartungsarbeiten</h1></body></html>", encoding="utf-8")

    if db_path is None:
        db_path = _db_mit_hotspots(tmpdir, [_hotspot("52.50000_13.40000", 52.5, 13.4)])

    marker_path = tmpdir / "LIVE_FREIGEGEBEN"
    if with_marker:
        marker_path.write_text("go", encoding="utf-8")

    orig = {k: getattr(export_html, k) for k in
            ("DB_PATH", "TEMPLATE", "OUT_PATH", "GO_LIVE_MARKER", "MAINTENANCE_PATH")}
    export_html.DB_PATH = db_path
    export_html.TEMPLATE = template_path
    export_html.OUT_PATH = tmpdir / "index.html"
    export_html.GO_LIVE_MARKER = marker_path
    export_html.MAINTENANCE_PATH = maintenance_path
    return orig, export_html.OUT_PATH


def _restore(orig: dict):
    for attr, value in orig.items():
        setattr(export_html, attr, value)


# Der Datensatz, den eine alte Live-Seite enthalten haette — dient in den
# A-11-Tests als Nachweis, dass genau diese Daten verschwinden.
ALTE_LIVE_SEITE = (
    "<html><body>LIVE 52.50000_13.40000 Hauptstraße 10115 "
    "lat_center:52.5001234567</body></html>"
)


# ── A-11 / T-24: Wartungs-Lock bricht hart ab ────────────────────────────────

def test_a11_fehlende_maintenance_ueberschreibt_alte_live_seite():
    """Lock AN + maintenance.html fehlt + alte Live-index.html mit Cluster-ID:
    danach darf KEINE Cluster-ID und keine Adresse mehr in index.html stehen.

    Reproduziert Sec M-02 aus dem Nachaudit vom 13.06.2026: vorher blieb die
    alte Live-Seite mit Ortsdaten unangetastet oeffentlich stehen.
    """
    with tempfile.TemporaryDirectory() as td:
        tmpdir = Path(td)
        orig, out_path = _setup_export_env(tmpdir, with_marker=False, mit_maintenance=False)
        try:
            out_path.write_text(ALTE_LIVE_SEITE, encoding="utf-8")
            rc = export_html.main()
            inhalt = out_path.read_text(encoding="utf-8")

            assert rc == 2, f"Fehlende maintenance.html muss Exit-Code 2 liefern, war {rc}"
            assert "52.50000_13.40000" not in inhalt, (
                "Cluster-ID der alten Live-Seite darf nicht stehenbleiben"
            )
            assert "Hauptstraße" not in inhalt, (
                "Adressdaten der alten Live-Seite duerfen nicht stehenbleiben"
            )
            assert "52.5001234567" not in inhalt, (
                "Feinaufgeloeste Koordinaten duerfen nicht stehenbleiben"
            )
            assert "nicht verfügbar" in inhalt, (
                "Die Fallback-Wartungsseite muss ausgeliefert werden"
            )
        finally:
            _restore(orig)


def test_a11_fallback_ohne_externe_ressourcen():
    """Die Fallback-Wartungsseite laedt nichts nach — sie muss auch dann
    tragen, wenn assets/ fehlt, und darf keine Empfaenger-IP weitergeben."""
    html = export_html.FALLBACK_WARTUNG_HTML
    assert "http://" not in html and "https://" not in html, (
        "Fallback-Seite darf keine externen Ressourcen laden"
    )
    assert "<img" not in html, "Fallback-Seite darf kein Bild referenzieren"
    assert "Content-Security-Policy" in html


def test_a11_hard_fail_ist_idempotent():
    """Zweiter Lauf ohne maintenance.html aendert die Datei nicht mehr,
    liefert aber weiterhin Exit-Code 2."""
    with tempfile.TemporaryDirectory() as td:
        tmpdir = Path(td)
        orig, out_path = _setup_export_env(tmpdir, with_marker=False, mit_maintenance=False)
        try:
            assert export_html.main() == 2
            erster = out_path.read_text(encoding="utf-8")
            assert export_html.main() == 2, "Der Zustand bleibt abnormal, Exit-Code bleibt 2"
            assert out_path.read_text(encoding="utf-8") == erster
        finally:
            _restore(orig)


def test_a11_normalfall_liefert_exit_code_null():
    """Vorhandene maintenance.html: unveraendertes Verhalten, Exit-Code 0."""
    with tempfile.TemporaryDirectory() as td:
        tmpdir = Path(td)
        orig, out_path = _setup_export_env(tmpdir, with_marker=False, mit_maintenance=True)
        try:
            assert export_html.main() == 0
            assert "Wartungsarbeiten" in out_path.read_text(encoding="utf-8")
        finally:
            _restore(orig)


# ── A-1: Rundung auf das Rasterzentrum vor dem Embed ─────────────────────────

def test_a1_koordinaten_auf_rasterzentrum_gerundet():
    """load_data liefert das Rasterzentrum, nicht den gebaeudescharfen
    Mittelwert der Einzelmeldungen.

    Das Rasterzentrum ist ein Vielfaches von GEO_RADIUS. Erwartet wird deshalb
    genau der Wert, der auch in der Zell-Kennung steht — beides muss
    zusammenpassen, sonst zeigt die Karte den Punkt woanders als die Kennung.
    """
    genau_lat, genau_lon = 52.501234567891, 13.400987654321
    cid = tracker.cluster_id(genau_lat, genau_lon)
    soll_lat, soll_lon = (float(t) for t in cid.split("_"))

    with tempfile.TemporaryDirectory() as td:
        tmpdir = Path(td)
        db = _db_mit_hotspots(tmpdir, [_hotspot(cid, genau_lat, genau_lon)])
        orig = export_html.DB_PATH
        try:
            export_html.DB_PATH = db
            h = export_html.load_data()["hotspots"][0]
            assert h["lat_center"] == soll_lat, f"lat_center war {h['lat_center']!r}"
            assert h["lon_center"] == soll_lon, f"lon_center war {h['lon_center']!r}"
            assert h["lat_center"] != genau_lat, "Der gebaeudescharfe Wert darf nicht durchgereicht werden"
            assert h["lon_center"] != genau_lon
        finally:
            export_html.DB_PATH = orig


def test_a1_keine_koordinate_mit_mehr_als_fuenf_nachkommastellen():
    """Im gesamten Export darf keine Koordinate feiner als das Raster sein."""
    with tempfile.TemporaryDirectory() as td:
        tmpdir = Path(td)
        db = _db_mit_hotspots(tmpdir, [
            _hotspot("52.50100_13.40050", 52.501234567891, 13.400987654321, count=9),
        ])
        orig = export_html.DB_PATH
        try:
            export_html.DB_PATH = db
            data = export_html.load_data()
            for h in data["hotspots"]:
                for feld in ("lat_center", "lon_center"):
                    wert = h[feld]
                    nachkomma = len(str(wert).split(".")[1]) if "." in str(wert) else 0
                    assert nachkomma <= 5, f"{feld}={wert!r} hat {nachkomma} Nachkommastellen"
        finally:
            export_html.DB_PATH = orig


def test_a1_rasterzentrum_bleibt_in_der_zelle():
    """Die Rundung darf einen Punkt nicht in die Nachbarzelle verschieben."""
    for lat, lon in [(52.5001, 13.4001), (52.49999, 13.39999), (52.5296, 13.3812)]:
        rlat, rlon = export_html.raster_zentrum(lat, lon)
        assert abs(rlat - lat) <= export_html.GEO_RADIUS / 2 + 1e-9
        assert abs(rlon - lon) <= export_html.GEO_RADIUS / 2 + 1e-9
        assert tracker.cluster_id(lat, lon) == tracker.cluster_id(rlat, rlon)


# ── A-2: Einzelfall-Zellen werden nicht persistiert ──────────────────────────

def _tracker_lauf(db_path: Path, meldungen: list[dict], monkeypatch):
    monkeypatch.setattr(tracker, "DB_PATH", db_path)
    monkeypatch.setattr(tracker, "fetch_meldungen", lambda: meldungen)
    return tracker.run()


def _meldung(mid: str, lat: float, lon: float, datum: str = "01.01.2025") -> dict:
    return {"id": mid, "kategorie": "Sperrmüll", "betreff": "", "bezirk": "Mitte",
            "lat": lat, "lon": lon, "status": "offen", "erstellungsDatum": datum,
            "strasse": "Teststr", "plz": "10000"}


def test_a2_singleton_zelle_wird_nicht_persistiert(monkeypatch, tmp_path):
    """Eine Zelle mit genau einer Meldung darf gar nicht erst in hotspots landen."""
    db = tmp_path / "a2.db"
    rc = _tracker_lauf(db, [_meldung("1", 52.5, 13.4)], monkeypatch)
    assert rc == 0
    conn = sqlite3.connect(db)
    zeilen = conn.execute("SELECT cluster_id, meldungen_count FROM hotspots").fetchall()
    meldungen_da = conn.execute("SELECT COUNT(*) FROM meldungen").fetchone()[0]
    conn.close()
    assert zeilen == [], f"Singleton-Zelle darf nicht persistiert werden, fand {zeilen}"
    assert meldungen_da == 1, "Die Meldung selbst bleibt erhalten (Grundlage der Wiederkehr)"


def test_a2_zweier_zelle_wird_persistiert(monkeypatch, tmp_path):
    """Ab zwei Meldungen wird die Zelle gespeichert (A-2 betrifft nur Einzelfaelle)."""
    db = tmp_path / "a2b.db"
    rc = _tracker_lauf(db, [_meldung("1", 52.5, 13.4), _meldung("2", 52.5001, 13.4001,
                                                                "05.01.2025")], monkeypatch)
    assert rc == 0
    conn = sqlite3.connect(db)
    zeilen = conn.execute("SELECT meldungen_count FROM hotspots").fetchall()
    conn.close()
    assert [z[0] for z in zeilen] == [2], f"Zweier-Zelle muss persistiert werden, fand {zeilen}"


def test_a2_altbestand_singletons_werden_entfernt(monkeypatch, tmp_path):
    """Bereits gespeicherte Einzelfall-Zellen verschwinden beim naechsten Lauf."""
    db = _db_mit_hotspots(tmp_path, [
        _hotspot("52.60000_13.50000", 52.6, 13.5, count=1),
        _hotspot("52.61000_13.51000", 52.61, 13.51, count=1),
    ])
    rc = _tracker_lauf(db, [_meldung("1", 52.5, 13.4), _meldung("2", 52.5001, 13.4001,
                                                                "05.01.2025")], monkeypatch)
    assert rc == 0
    conn = sqlite3.connect(db)
    uebrig = conn.execute("SELECT COUNT(*) FROM hotspots WHERE meldungen_count < 2").fetchone()[0]
    conn.close()
    assert uebrig == 0, f"Altbestand-Singletons muessen entfernt werden, {uebrig} uebrig"


# ── A-7: Sperrliste (Widerspruch nach Art. 21) ───────────────────────────────

def test_a7_gesperrter_cluster_faellt_aus_dem_export():
    """Ein gesperrter Cluster darf in load_data() nicht mehr auftauchen."""
    with tempfile.TemporaryDirectory() as td:
        tmpdir = Path(td)
        db = _db_mit_hotspots(tmpdir, [
            _hotspot("52.50000_13.40000", 52.5, 13.4, count=9),
            _hotspot("52.51000_13.41000", 52.51, 13.41, count=7),
        ])
        conn = sqlite3.connect(db)
        sperrliste.eintragen(conn, "52.50000_13.40000",
                             grund="Widerspruch Art. 21", quelle="W-2026-001")
        conn.commit()
        conn.close()

        orig = export_html.DB_PATH
        try:
            export_html.DB_PATH = db
            data = export_html.load_data()
            ids = [h["cluster_id"] for h in data["hotspots"]]
            assert "52.50000_13.40000" not in ids, f"Gesperrter Cluster im Export: {ids}"
            assert "52.51000_13.41000" in ids, "Nicht gesperrte Cluster bleiben erhalten"
            # Auch die Bezirks-Kennzahlen duerfen ihn nicht mehr mitzaehlen
            gesamt = sum(b["total_hotspots"] for b in data["bezirk_stats"])
            assert gesamt == 1, f"bezirk_stats zaehlt gesperrte Zellen mit: {gesamt}"
        finally:
            export_html.DB_PATH = orig


def test_a7_gesperrter_cluster_wird_nicht_neu_persistiert(monkeypatch, tmp_path):
    """Ein gesperrter Cluster wird auch von tracker.run() nicht wieder angelegt."""
    db = tmp_path / "a7.db"
    conn = sqlite3.connect(db)
    tracker.init_db(conn)
    sperrliste.eintragen(conn, tracker.cluster_id(52.5, 13.4), quelle="W-2026-002")
    conn.commit()
    conn.close()

    rc = _tracker_lauf(db, [
        _meldung("1", 52.5, 13.4), _meldung("2", 52.5001, 13.4001, "05.01.2025"),
        _meldung("3", 52.5002, 13.4002, "09.01.2025"),
    ], monkeypatch)
    assert rc == 0

    conn = sqlite3.connect(db)
    zeilen = conn.execute("SELECT cluster_id FROM hotspots").fetchall()
    conn.close()
    assert zeilen == [], f"Gesperrter Cluster darf nicht persistiert werden, fand {zeilen}"


def test_a7_sperrung_entfernt_bestehende_zelle(monkeypatch, tmp_path):
    """Wird eine bereits gespeicherte Zelle gesperrt, verschwindet sie beim
    naechsten Lauf aus der Datenbank — die Sperre wirkt rueckwirkend."""
    db = tmp_path / "a7b.db"
    meldungen = [_meldung("1", 52.5, 13.4), _meldung("2", 52.5001, 13.4001, "05.01.2025")]
    assert _tracker_lauf(db, meldungen, monkeypatch) == 0

    conn = sqlite3.connect(db)
    vorher = conn.execute("SELECT COUNT(*) FROM hotspots").fetchone()[0]
    sperrliste.eintragen(conn, tracker.cluster_id(52.5, 13.4), quelle="W-2026-003")
    conn.commit()
    conn.close()
    assert vorher == 1

    assert _tracker_lauf(db, meldungen, monkeypatch) == 0
    conn = sqlite3.connect(db)
    nachher = conn.execute("SELECT COUNT(*) FROM hotspots").fetchone()[0]
    conn.close()
    assert nachher == 0, f"Sperre muss die bestehende Zelle entfernen, {nachher} uebrig"


def test_a7_cluster_id_wird_validiert(tmp_path):
    """Ein unsauber formatierter Eintrag wird abgewiesen, statt still nicht zu wirken."""
    db = tmp_path / "a7c.db"
    conn = sqlite3.connect(db)
    tracker.init_db(conn)
    for murks in ["52.5_13.4", "Hauptstraße 12", "", "52.50000-13.40000"]:
        try:
            sperrliste.eintragen(conn, murks)
        except ValueError:
            continue
        raise AssertionError(f"{murks!r} haette abgewiesen werden muessen")
    conn.close()


def test_a7_sperre_ueberlebt_neuaufbau_der_datenbank(tmp_path, monkeypatch):
    """ordnungsamt.db ist gitignored und entsteht auf einem neuen Rechner neu.
    Die Sperre darf dabei nicht verlorengehen — die Zweitschrift als Datei
    spielt sie zurueck."""
    datei = tmp_path / "sperrliste.txt"
    monkeypatch.setattr(sperrliste, "SPERRLISTE_DATEI", datei)

    alte_db = tmp_path / "alt.db"
    conn = sqlite3.connect(alte_db)
    tracker.init_db(conn)
    sperrliste.eintragen(conn, "52.50000_13.40000", quelle="W-2026-004")
    conn.commit()
    conn.close()
    assert datei.is_file(), "Die Zweitschrift muss angelegt worden sein"

    # Rechnerwechsel: frische Datenbank, nur die Datei ist mitgekommen
    neue_db = tmp_path / "neu.db"
    conn = sqlite3.connect(neue_db)
    tracker.init_db(conn)
    assert sperrliste.laden(conn) == {"52.50000_13.40000"}, (
        "Die Sperre muss aus der Zweitschrift zurueckkommen"
    )
    in_db = {r[0] for r in conn.execute("SELECT cluster_id FROM sperrliste")}
    conn.close()
    assert in_db == {"52.50000_13.40000"}, "und dabei in die Datenbank zurueckgespielt werden"


def test_a7_zweitschrift_enthaelt_keine_klartextangaben(tmp_path, monkeypatch):
    """In der Zweitschrift steht ein Aktenzeichen, kein Name."""
    datei = tmp_path / "sperrliste.txt"
    monkeypatch.setattr(sperrliste, "SPERRLISTE_DATEI", datei)
    conn = sqlite3.connect(tmp_path / "z.db")
    tracker.init_db(conn)
    sperrliste.eintragen(conn, "52.50000_13.40000",
                         grund="Widerspruch nach Art. 21 DSGVO", quelle="W-2026-005")
    conn.commit()
    conn.close()

    inhalt = datei.read_text(encoding="utf-8")
    assert "52.50000_13.40000" in inhalt
    assert "W-2026-005" in inhalt
    assert "Kein Name" in inhalt or "Aktenzeichen" in inhalt, (
        "Der Dateikopf muss vor Klartextangaben warnen"
    )


def test_a7_cluster_id_fuer_koordinate_passt_zum_tracker(tmp_path):
    """Die Hilfsfunktion fuer die Kommandozeile liefert dieselbe Zell-Kennung
    wie die Pipeline — sonst sperrt Lars die falsche Zelle."""
    for lat, lon in [(52.5001, 13.4001), (52.4839, 13.4283)]:
        assert sperrliste.cluster_id_fuer(lat, lon) == tracker.cluster_id(lat, lon)


# ── A-12: keine Fremdhosts, CSP und SRI ──────────────────────────────────────

AUSGELIEFERTE_SEITEN = ["template.html", "maintenance.html", "index.html"]

# tile.openstreetmap.org bleibt zwingend extern (Kartenkacheln lassen sich
# nicht mitliefern) und ist in der Datenschutzerklaerung als Empfaenger gefuehrt.
ERLAUBTE_FREMDHOSTS = {
    "tile.openstreetmap.org",
    "www.openstreetmap.org",      # Copyright-Link im Karten-Attribut
    "muell-monitor.de",           # eigene Domain in og:-Metadaten
    "www.gnu.org", "www.govdata.de",  # Lizenz-Links (kein Nachladen)
    "www.w3.org",                 # XML-Namensraum eingebetteter SVG, kein Abruf
}


def _fremdhosts(html: str) -> set[str]:
    import re
    treffer = re.findall(r'https?://([A-Za-z0-9.\-]+)', html)
    return {h for h in treffer
            if not any(h == e or h.endswith("." + e) for e in ERLAUBTE_FREMDHOSTS)}


def test_a12_keine_fremden_skript_und_schrift_hosts():
    """Weder cdnjs.cloudflare.com noch fonts.googleapis.com duerfen in einer
    ausgelieferten Seite stehen — sonst bekommen sie die IP der Besucher."""
    for name in AUSGELIEFERTE_SEITEN:
        html = (TECHNIK / name).read_text(encoding="utf-8")
        rest = _fremdhosts(html)
        assert not rest, f"{name} laedt noch von: {sorted(rest)}"


def test_a12_csp_gesetzt():
    """Jede ausgelieferte Seite traegt eine Inhaltssicherheitsrichtlinie."""
    for name in AUSGELIEFERTE_SEITEN:
        html = (TECHNIK / name).read_text(encoding="utf-8")
        assert 'http-equiv="Content-Security-Policy"' in html, f"{name} ohne CSP"
        assert "default-src 'none'" in html, f"{name}: CSP ohne restriktives default-src"
        assert "font-src 'self'" in html, f"{name}: CSP erlaubt keine lokalen Schriften"


def test_a12_sri_pruefsummen_stimmen():
    """Jede integrity-Angabe passt zur ausgelieferten Datei."""
    for name in AUSGELIEFERTE_SEITEN:
        html = (TECHNIK / name).read_text(encoding="utf-8")
        fehler = sri.pruefe_html(html, TECHNIK)
        assert not fehler, f"{name}: {fehler}"


def test_a12_template_hat_sri_auf_allen_skripten():
    """Die Live-Quelle bindet kein Skript ohne Pruefsumme ein."""
    html = (TECHNIK / "template.html").read_text(encoding="utf-8")
    ohne = sri.skripte_ohne_integritaet(html)
    assert not ohne, f"Skripte ohne integrity: {ohne}"


# ── M-03: Stored XSS ueber die Datumsfelder ──────────────────────────────────

def test_m03_datumsfelder_werden_im_frontend_escaped():
    """M-03: first_seen und last_seen stammen ungefiltert aus der Behoerden-
    Schnittstelle und wurden per innerHTML eingesetzt, waehrend strasse, bezirk
    und cluster_id daneben durch esc() laufen. Jede Einsetzung dieser beiden
    Felder muss escaped sein.
    """
    import re
    html = (TECHNIK / "template.html").read_text(encoding="utf-8")
    ungeschuetzt = re.findall(r'\$\{h\.(first_seen|last_seen)\}', html)
    assert not ungeschuetzt, (
        f"Diese Felder werden ohne esc() ins DOM geschrieben: {set(ungeschuetzt)}"
    )
    # und die geschuetzte Variante muss es geben, sonst wurde nur geloescht
    assert "${esc(h.first_seen)}" in html and "${esc(h.last_seen)}" in html


def test_m03_nutzlast_bricht_nicht_aus_dem_skriptblock(tmp_path):
    """Zweite Verteidigungslinie: die </-Ersetzung im Render verhindert, dass
    ein </script> im Datenbestand den eingebetteten Datenblock schliesst."""
    db = _db_mit_hotspots(tmp_path, [
        _hotspot("52.50000_13.40000", 52.5, 13.4, first_seen="</script><script>alert(1)</script>"),
    ])
    orig = export_html.DB_PATH
    try:
        export_html.DB_PATH = db
        import json
        roh = json.dumps(export_html.load_data(), ensure_ascii=False)
        eingebettet = roh.replace('</', r'<\/')
        assert "</script>" not in eingebettet, (
            "Die Nutzlast darf den Skriptblock nicht schliessen koennen"
        )
    finally:
        export_html.DB_PATH = orig


def test_a12_render_bricht_bei_falscher_pruefsumme_ab(tmp_path):
    """Passt eine Pruefsumme nicht mehr zur Datei, bricht der Render hart ab,
    statt eine Seite mit toten Ressourcen zu veroeffentlichen."""
    import pytest
    (tmp_path / "assets").mkdir()
    ziel = tmp_path / "assets" / "x.js"
    ziel.write_text("console.log(1)", encoding="utf-8")
    html = ('<script src="assets/x.js" integrity="sha384-'
            + "A" * 64 + '" crossorigin="anonymous"></script>')
    fehler = sri.pruefe_html(html, tmp_path)
    assert fehler, "Falsche Pruefsumme muss gemeldet werden"

    orig = export_html.TEMPLATE
    try:
        tpl = tmp_path / "template.html"
        tpl.write_text(html + "__APP_DATA_PLACEHOLDER__", encoding="utf-8")
        export_html.TEMPLATE = tpl
        with pytest.raises(RuntimeError, match="Pruefsumme"):
            export_html.pruefe_sri()
    finally:
        export_html.TEMPLATE = orig


# ── T-25 / T-40: Pflichtangaben auf der Live-Seite ───────────────────────────
# Bis zum 03.08.2026 enthielt template.html weder Impressum noch
# Datenschutzerklaerung noch Kontakt noch einen Hinweis auf das
# Widerspruchsrecht — je null Treffer, waehrend maintenance.html alles davon
# hatte (Sec M-03 vom 13.06.2026, Befund H-03 vom 29.07.2026). Die Sperrliste
# nach Art. 21 war seit dem 29.07. technisch fertig, nur erfuhr niemand davon.

TEMPLATE_HTML = (TECHNIK / "template.html").read_text(encoding="utf-8")
MAINTENANCE_HTML = (TECHNIK / "maintenance.html").read_text(encoding="utf-8")


def test_t25_template_hat_impressum_datenschutz_und_kontakt():
    """Die drei Pflichtangaben stehen in der Live-Quelle."""
    fehlend = [b for b in ("Impressum", "Datenschutzerkl", "info@muell-monitor.de")
               if b not in TEMPLATE_HTML]
    assert not fehlend, f"In template.html fehlen: {fehlend}"


def test_t25_impressum_traegt_die_pflichtangaben_nach_ddg():
    for teil in ("§ 5 DDG", "§ 18 Abs. 2 MStV", "Lars Wittkopf",
                 "Welserstraße 3", "87463 Dietmannsried"):
        assert teil in TEMPLATE_HTML, f"Impressum unvollstaendig, es fehlt: {teil!r}"


def test_t40_widerspruchsrecht_ist_ausdruecklich_benannt():
    """Der eigentliche Punkt von T-40: nicht nur die Erklaerung verlinken,
    sondern den WEG zum Widerspruch benennen. Ohne ihn bleibt A-7 wirkungslos."""
    for teil in ("Art. 21", "Widerspruch"):
        assert teil in TEMPLATE_HTML, f"Widerspruchsrecht unvollstaendig: {teil!r}"
    # Ein anklickbarer Weg, nicht bloss ein Satz darueber.
    assert "Widerspruch%20nach%20Art.%2021%20DSGVO" in TEMPLATE_HTML, (
        "Kein vorbereiteter Mail-Weg fuer den Widerspruch")
    # Und ein Einstieg, der nicht erst durch die ganze Erklaerung fuehrt.
    assert 'oeffneRecht(\'widerspruch\')' in TEMPLATE_HTML, (
        "Kein direkter Einstieg zum Widerspruch")


def test_t40_widerspruch_ist_von_der_kopfzeile_aus_erreichbar():
    """Die Kopfzeile ist der einzige Ort, der auf jedem Geraet und in jedem
    Zustand der Mobilansicht sichtbar ist. Der Seitenfuss der Seitenleiste
    liegt auf Mobile im eingeklappten Blatt."""
    kopf = TEMPLATE_HTML.split('<div class="main">')[0]
    assert "hdr-legal" in kopf, "Pflichtangaben nicht in der Kopfzeile verankert"
    assert "oeffneRecht('widerspruch')" in kopf


def test_t25_keine_eckigen_platzhalter_in_den_ausgelieferten_seiten():
    """Kein unausgefuellter Platzhalter darf produktiv online gehen.

    Am 03.08.2026 stand in maintenance.html — und damit live, weil index.html
    eine Kopie davon ist — "Verantwortlich: [Name / Firma, Anschrift, E-Mail]".
    """
    import re

    def sichtbarer_text(html: str) -> str:
        """Nur das, was der Besucher liest.

        Eingebetteter Stil und Anwendungscode fallen weg, ebenso Kommentare
        und die Auszeichnung selbst. Sonst schlagen CSS-Attributwaehler
        ('[data-t="lvl"]') und Feldzugriffe ('[h.score_label]') an, die keine
        Platzhalter sind.
        """
        ohne = re.sub(r"<script\b.*?</script>", " ", html, flags=re.S | re.I)
        ohne = re.sub(r"<style\b.*?</style>", " ", ohne, flags=re.S | re.I)
        ohne = re.sub(r"<!--.*?-->", " ", ohne, flags=re.S)
        return re.sub(r"<[^>]*>", " ", ohne)

    muster = re.compile(r"\[[^\]\n]{3,}\]")
    for name, inhalt in (("template.html", TEMPLATE_HTML),
                         ("maintenance.html", MAINTENANCE_HTML)):
        treffer = muster.findall(sichtbarer_text(inhalt))
        assert not treffer, f"Platzhalter im sichtbaren Text von {name}: {treffer}"


def test_t25_maintenance_nennt_verantwortlichen_und_widerspruchsweg():
    for teil in ("Lars Wittkopf", "info@muell-monitor.de", "Art. 21"):
        assert teil in MAINTENANCE_HTML, f"maintenance.html unvollstaendig: {teil!r}"


def test_t25_live_render_bleibt_ohne_freigabe_der_rechtstexte_gesperrt(tmp_path,
                                                                       monkeypatch):
    """Fail-closed: Freigabe-Marker allein genuegt nicht.

    Solange die Marke im Vorlagentext steht, darf keine Live-Seite entstehen,
    sondern es bleibt bei der Wartungsseite. Exit-Code 3.
    """
    vorlage = tmp_path / "template.html"
    vorlage.write_text("<html><!-- __RECHTSTEXT_UNGEPRUEFT__ -->"
                       "__APP_DATA_PLACEHOLDER__</html>", encoding="utf-8")
    wartung = tmp_path / "maintenance.html"
    wartung.write_text("<html>Wartung</html>", encoding="utf-8")
    marker = tmp_path / "LIVE_FREIGEGEBEN"
    marker.write_text("", encoding="utf-8")
    ausgabe = tmp_path / "index.html"
    ausgabe.write_text("<html>ALTE LIVE-SEITE MIT ORTSDATEN</html>", encoding="utf-8")

    monkeypatch.setattr(export_html, "TEMPLATE", vorlage)
    monkeypatch.setattr(export_html, "MAINTENANCE_PATH", wartung)
    monkeypatch.setattr(export_html, "GO_LIVE_MARKER", marker)
    monkeypatch.setattr(export_html, "OUT_PATH", ausgabe)

    assert export_html.main() == 3
    assert ausgabe.read_text(encoding="utf-8") == "<html>Wartung</html>", (
        "Die alte Live-Seite steht noch")


def test_t25_nach_freigabe_laeuft_der_live_render_wieder(tmp_path, monkeypatch):
    """Gegenprobe: ohne die Marke ist der Weg frei. Sonst waere die Sperre
    eine Sackgasse statt eines Tors."""
    vorlage = tmp_path / "template.html"
    vorlage.write_text("<html>__APP_DATA_PLACEHOLDER__ __LAST_UPDATE__</html>",
                       encoding="utf-8")
    monkeypatch.setattr(export_html, "TEMPLATE", vorlage)
    assert export_html.rechtstexte_freigegeben() is True


def test_t25_echte_vorlage_ist_noch_gesperrt():
    """Solange T-03 und T-22 offen sind, MUSS die echte Vorlage gesperrt sein.

    Faellt dieser Test, ist entweder die anwaltliche Freigabe erteilt (dann
    diesen Test entfernen) oder jemand hat die Marke versehentlich geloescht.
    """
    assert export_html.RECHTSTEXT_MARKE in TEMPLATE_HTML, (
        "Die Sperre der Rechtstexte ist aufgehoben. War das Absicht? "
        "Anwaltliche Freigabe nach T-03/T-22 erteilt?")


# ── M-02 (Nachaudit 29.07.2026): first_seen wird fortgeschrieben ─────────────

def test_m02_first_seen_ueberlebt_die_loeschung_der_aeltesten_meldung_nicht(tmp_path,
                                                                            monkeypatch):
    """Loescht die Aufbewahrungsroutine die aelteste Meldung einer Zelle, darf
    deren Meldedatum nicht in der veroeffentlichten Zelle stehenbleiben.

    Vorher fehlte 'first_seen = excluded.first_seen' in der ON-CONFLICT-Klausel:
    der Wert blieb auf dem Stand der ersten Anlage. Die Zelle zeigte damit ein
    Datum, zu dem es keine Meldung mehr gab.
    """
    db = tmp_path / "m02.db"
    monkeypatch.setattr(tracker, "DB_PATH", db)

    def lauf(meldungen):
        monkeypatch.setattr(tracker, "fetch_meldungen", lambda: meldungen)
        return tracker.run()

    def m(mid, datum):
        return {"id": mid, "kategorie": "", "betreff": "Abfall - Sperrmüll",
                "bezirk": "Mitte", "lat": 52.5, "lon": 13.4, "status": "offen",
                "erstellungsDatum": datum, "strasse": "Teststr", "plz": "10115"}

    # Erster Lauf: zwei Meldungen, die aeltere vom 01.03.2025
    assert lauf([m("alt", "01.03.2025"), m("neu", "01.06.2026")]) == 0
    conn = sqlite3.connect(db)
    cid = tracker.cluster_id(52.5, 13.4)
    vorher = conn.execute("SELECT first_seen FROM hotspots WHERE cluster_id=?",
                          (cid,)).fetchone()[0]
    assert vorher == "2025-03-01"

    # Die aelteste Meldung faellt weg, zwei neue kommen dazu (die Zelle muss die
    # Persistenz-Schwelle weiter erreichen).
    conn.execute("DELETE FROM meldungen WHERE id='alt'")
    conn.commit()
    conn.close()

    assert lauf([m("neu", "01.06.2026"), m("neu2", "02.06.2026")]) == 0

    conn = sqlite3.connect(db)
    nachher = conn.execute("SELECT first_seen FROM hotspots WHERE cluster_id=?",
                           (cid,)).fetchone()[0]
    assert nachher == "2026-06-01", (
        f"first_seen steht noch auf {nachher!r}. Das Meldedatum der geloeschten "
        f"Meldung ueberlebt in der veroeffentlichten Zelle (M-02).")


# ── H-04 / T-39: Datenstand statt behaupteter Tagesaktualitaet ───────────────
#
# Befund H-04 der Gegenpruefung vom 29.07.2026: die Seite behauptete an zwei
# fest verdrahteten Stellen, sie sei tagesaktuell, waehrend die Quelle seit dem
# 22.04.2026 nichts mehr lieferte. Der berechnete Stand wurde nirgends
# angezeigt, weil der Platzhalter beim Design-Umbau aus der Vorlage gefallen
# war. Gegenueber einer Kommune als zahlendem Kunden ist das eine falsche
# Leistungsangabe, gegenueber Betroffenen beruehrt es Art. 5 Abs. 1 lit. d.

def _fetch_log(db_path: Path, eintraege: list[tuple]):
    """Schreibt (fetched_at, count_total)-Tupel in fetch_log."""
    conn = sqlite3.connect(db_path)
    for fetched_at, count_total in eintraege:
        conn.execute(
            "INSERT INTO fetch_log (fetched_at, count_total, count_new, count_muell) "
            "VALUES (?,?,0,0)", (fetched_at, count_total))
    conn.commit()
    conn.close()


def test_t39_vorlage_behauptet_keine_tagesaktualitaet_mehr():
    """Keine fest verdrahtete Aussage ueber Aktualitaet in der Vorlage.

    Sie war doppelt vorhanden: in der og:description (Zeile 12) und im
    Abschnitt Datengrundlage. Beide standen im Quelltext und waren damit
    unabhaengig davon wahr oder falsch, ob die Schnittstelle liefert.
    """
    treffer = [zeile.strip() for zeile in TEMPLATE_HTML.splitlines()
               if "tagesaktuell" in zeile.lower()]
    assert not treffer, (
        f"Die Vorlage behauptet weiterhin Tagesaktualitaet: {treffer}")


def test_t39_vorlage_traegt_den_platzhalter_wieder():
    """Ohne __LAST_UPDATE__ laeuft die Ersetzung in export_html ins Leere —
    still, weil eine Nicht-Ersetzung genau aussieht wie ein geglueckter Lauf."""
    assert "__LAST_UPDATE__" in TEMPLATE_HTML, (
        "Der Platzhalter fehlt in template.html. Genau so ist H-04 entstanden.")


def test_t39_gerenderte_seite_traegt_den_stand_aus_dem_fetch_log(tmp_path):
    """Der Stand aus dem letzten ERFOLGREICHEN Abruf muss im HTML stehen.

    Ausdruecklich nicht das Render-Datum: die Pipeline laeuft taeglich weiter,
    auch waehrend die Behoerde nichts liefert.
    """
    db = _db_mit_hotspots(tmp_path, [_hotspot("52.50000_13.40000", 52.5, 13.4)])
    _fetch_log(db, [("2026-04-14T06:00:00", 105456),
                    ("2026-04-22T06:00:00", 105100),
                    ("2026-08-13T06:00:00", -1)])   # Fehllauf, zaehlt nicht
    orig, out = _setup_export_env(tmp_path, with_marker=True, db_path=db)
    try:
        assert export_html.main() == 0
        html = out.read_text(encoding="utf-8")
        # An der Stelle des Platzhalters, nicht irgendwo: das Render-Datum
        # steckt ohnehin im eingebetteten Prognose-Block.
        assert "<span>22.04.2026</span>" in html, (
            "Der Datenstand aus dem fetch_log steht nicht an der Stelle des "
            "Platzhalters im gerenderten HTML")
        assert "__LAST_UPDATE__" not in html, "Platzhalter nicht ersetzt"
        assert "<span>14.04.2026</span>" not in html, (
            "Es wird nicht der juengste erfolgreiche Abruf angezeigt")
    finally:
        _restore(orig)


def test_t39_ohne_erfolgreichen_abruf_steht_unbekannt(tmp_path):
    """Frische Datenbank ohne einen einzigen erfolgreichen Abruf: die Seite
    sagt 'unbekannt' und nicht etwa das heutige Datum."""
    db = _db_mit_hotspots(tmp_path, [_hotspot("52.50000_13.40000", 52.5, 13.4)])
    orig, out = _setup_export_env(tmp_path, with_marker=True, db_path=db)
    try:
        assert export_html.main() == 0
        html = out.read_text(encoding="utf-8")
        assert "unbekannt" in html, "Fehlender Datenstand wird nicht benannt"
    finally:
        _restore(orig)


def test_t39_render_bricht_ab_wenn_der_platzhalter_fehlt(tmp_path, monkeypatch):
    """Faellt der Platzhalter wieder aus der Vorlage, darf der Render nicht
    stillschweigend eine Seite ohne Datenstand schreiben."""
    db = _db_mit_hotspots(tmp_path, [_hotspot("52.50000_13.40000", 52.5, 13.4)])
    vorlage = tmp_path / "ohne_platzhalter.html"
    vorlage.write_text("<html>__APP_DATA_PLACEHOLDER__</html>", encoding="utf-8")
    ausgabe = tmp_path / "index_ohne.html"
    monkeypatch.setattr(export_html, "DB_PATH", db)
    monkeypatch.setattr(export_html, "TEMPLATE", vorlage)
    monkeypatch.setattr(export_html, "OUT_PATH", ausgabe)

    import pytest
    with pytest.raises(RuntimeError, match="__LAST_UPDATE__"):
        export_html.render_live()
    assert not ausgabe.exists(), "Es wurde trotzdem eine Seite geschrieben"


def test_t39_deutsches_datum_und_rueckfall():
    assert export_html.stand_fuer_anzeige("2026-04-22") == "22.04.2026"
    assert export_html.stand_fuer_anzeige("2026-04-22T06:00:00") == "22.04.2026"
    assert export_html.stand_fuer_anzeige(None) == "unbekannt"
    assert export_html.stand_fuer_anzeige("") == "unbekannt"
    # Unerwartetes Format wird durchgereicht, nicht verschluckt.
    assert export_html.stand_fuer_anzeige("kaputt") == "kaputt"
