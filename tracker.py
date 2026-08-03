#!/usr/bin/env python3
"""
Berlin Ordnungsamt Müll-Tracker
================================
Täglich ausführen via GitHub Actions oder Cron.
Legt alle Müll-Meldungen in einer SQLite-Datenbank ab und berechnet
Hotspot-Scores für wiederkehrende Ablagerungen.
"""

import re
import sqlite3
import json
import requests
import hashlib
import logging
from collections import Counter
from datetime import datetime
from pathlib import Path

import betreff_filter
import retention
import sperrliste

# ── Konfiguration ─────────────────────────────────────────────────────────────
DB_PATH = Path(__file__).parent / "ordnungsamt.db"

# Müll-Kategorien — nur explizite Abfall-Einträge, kein catch-all
MUELL_KEYWORDS = [
    # Direkte Abfall-Kategorien aus der App
    "abfall",
    "autowrack",
    "bauabfälle", "bauschutt",
    "bioabfälle",
    "elektroschrott",
    "müllablagerung", "müll",
    "papierkörbe",
    "schrottfahrräder",
    "sperrmüll",
    "tierkadaver", "tote tiere",
    "unrat",
    "weihnachtsbäume",
    # Ablagerungen
    "flaschen", "abgelagert",
    "fässer",
    "kfz-teile", "betriebsstoffe",
    "kanister",
    # Allgemeine Begriffe
    "entsorgung", "ablagerung", "deponie",
    "sondermüll", "grünschnitt",
    "schrottauto",
]

# Nicht-Müll-Kategorien — explizit ausschließen (DSGVO: Nicht-Müll enthält
# Beschwerden gegen identifizierbare Personen, darf nicht gespeichert werden)
NON_MUELL_KEYWORDS = [
    "lärm", "laerm",
    "ruhestörung", "ruhe störung", "ruhestoerung",
    "hund", "hundebesitzer", "hundekot",
    "falschpark", "falsch park", "falschparkend",
    "parkverstoß", "parkverstoss",
    "verkehrsdelikt", "verkehrsverstoß",
    "nachbar", "hausnachbar",
    "gaststätte",
]

# Radius in Grad (~150m) für Geo-Clustering
GEO_RADIUS = 0.0015

# k-Anonymitäts-Schwelle: Hotspots mit weniger als k Meldungen werden im
# öffentlichen Frontend nicht angezeigt (DSGVO Erwägungsgrund 26).
K_ANONYMITY_THRESHOLD = 3

# A-2 (DSFA 28.07.2026): Ortszellen mit nur einer Meldung werden gar nicht erst
# dauerhaft gespeichert. Sie sind für die Darstellung ohne Wert, weil sie
# ohnehin an der k-Anonymitäts-Schwelle hängenbleiben, tragen aber das höchste
# Re-Identifikationsrisiko (Risiko R-1 und R-7: bei genau einer Meldung ist die
# Zuordnung zu einem Grundstück trivial).
# Die Meldungen selbst bleiben erhalten — sie sind die Grundlage des
# Wiederkehr-Zählers und können später zu einer echten Zelle zusammenwachsen.
HOTSPOT_MIN_PERSIST = 2

# Tage bis zur "regulären" Entsorgung (Berliner Realität)
DISPOSAL_DAYS = 14

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(Path(__file__).parent / "tracker.log"),
        logging.StreamHandler()
    ]
)
log = logging.getLogger(__name__)


# ── Datenbank-Setup ───────────────────────────────────────────────────────────
def init_db(conn: sqlite3.Connection):
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS meldungen (
            id          TEXT PRIMARY KEY,
            fetched_at  TEXT NOT NULL,
            datum       TEXT,
            kategorie   TEXT,
            betreff     TEXT,
            bezirk      TEXT,
            lat         REAL,
            lon         REAL,
            status      TEXT,
            is_muell    INTEGER DEFAULT 0,
            strasse     TEXT DEFAULT '',
            plz         TEXT DEFAULT ''
        );
        CREATE INDEX IF NOT EXISTS idx_latlon  ON meldungen(lat, lon);
        CREATE INDEX IF NOT EXISTS idx_datum   ON meldungen(datum);
        CREATE INDEX IF NOT EXISTS idx_bezirk  ON meldungen(bezirk);
        CREATE INDEX IF NOT EXISTS idx_ismuell ON meldungen(is_muell);

        CREATE TABLE IF NOT EXISTS hotspots (
            cluster_id       TEXT PRIMARY KEY,
            lat_center       REAL,
            lon_center       REAL,
            bezirk           TEXT,
            meldungen_count  INTEGER DEFAULT 0,
            recurrence_count INTEGER DEFAULT 0,
            last_seen        TEXT,
            first_seen       TEXT,
            score            REAL DEFAULT 0.0,
            score_label      TEXT DEFAULT 'niedrig',
            strasse          TEXT DEFAULT '',
            plz              TEXT DEFAULT ''
        );

        CREATE TABLE IF NOT EXISTS fetch_log (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            fetched_at  TEXT,
            count_total INTEGER,
            count_new   INTEGER,
            count_muell INTEGER
        );
    """)
    conn.commit()

    # Spalten nachrüsten falls DB bereits existiert
    for col, typedef in [("strasse", "TEXT DEFAULT ''"), ("plz", "TEXT DEFAULT ''")]:
        try:
            conn.execute(f"ALTER TABLE meldungen ADD COLUMN {col} {typedef}")
            conn.commit()
        except sqlite3.OperationalError:
            pass

    for col, typedef in [("strasse", "TEXT DEFAULT ''"), ("plz", "TEXT DEFAULT ''")]:
        try:
            conn.execute(f"ALTER TABLE hotspots ADD COLUMN {col} {typedef}")
            conn.commit()
        except sqlite3.OperationalError:
            pass

    # raw_json Spalte entfernen falls vorhanden (spart Speicherplatz)
    try:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(meldungen)").fetchall()]
        if "raw_json" in cols:
            conn.execute("UPDATE meldungen SET raw_json = NULL")
            conn.commit()
            log.info("raw_json geleert um Speicherplatz zu sparen")
    except Exception:
        pass

    # A-4: Felder für die Löschroutine (last_seen_at, quelle_weg_seit) und die
    # Aggregat-Tabelle. A-7: Sperrliste. Beide idempotent nach demselben Muster.
    retention.init_schema(conn)
    sperrliste.ensure_table(conn)

    log.info("Datenbank initialisiert: %s", DB_PATH)


# ── API-Abruf ─────────────────────────────────────────────────────────────────
API_URLS = [
    "https://ordnungsamt.berlin.de/frontend.webservice.opendata/api/meldungen",
]

def _parse_response(data) -> list[dict]:
    return (
        data if isinstance(data, list)
        else data.get("index",
             data.get("meldungen",
             data.get("data", [])))
    )


def fetch_meldungen() -> list[dict]:
    import time

    # Lokale Datei verwenden falls vorhanden
    local = Path(__file__).parent / "meldungen.json"
    if local.exists():
        log.info("Lese lokale Datei: %s (%.1f MB)", local, local.stat().st_size / 1024 / 1024)
        data = json.loads(local.read_text(encoding="utf-8"))
        meldungen = _parse_response(data)
        if meldungen:
            log.info("Erfolg: %d Meldungen aus lokaler Datei", len(meldungen))
            return meldungen

    headers = {
        "Accept": "application/json, text/plain, */*",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
    }
    for url in API_URLS:
        log.info("Versuche API-Endpunkt: %s", url)
        for attempt in range(3):
            try:
                log.info("Starte Download (~26MB, bitte warten)...")
                resp = requests.get(url, timeout=(30, 600), headers=headers, stream=True)
                if resp.status_code == 200:
                    chunks = []
                    total = 0
                    for chunk in resp.iter_content(chunk_size=65536):
                        if chunk:
                            chunks.append(chunk)
                            total += len(chunk)
                            if total % (1024 * 1024) < 65536:
                                log.info("  %.1f MB geladen...", total / 1024 / 1024)
                    raw = b"".join(chunks)
                    log.info("Download abgeschlossen: %.1f MB", len(raw) / 1024 / 1024)
                    data = json.loads(raw.decode("utf-8"))
                    meldungen = _parse_response(data)
                    if meldungen:
                        log.info("Erfolg: %d Meldungen erhalten", len(meldungen))
                        return meldungen
                else:
                    log.warning("HTTP %d von %s", resp.status_code, url)
                    break
            except requests.exceptions.Timeout:
                log.warning("Timeout bei %s (Versuch %d/3)", url, attempt + 1)
                if attempt < 2:
                    time.sleep(30)
            except Exception as e:
                log.warning("Fehler bei %s (Versuch %d/3): %s", url, attempt + 1, e)
                if attempt < 2:
                    time.sleep(30)
    log.warning("Alle API-Endpunkte nicht erreichbar")
    return []


# ── Hilfsfunktionen ───────────────────────────────────────────────────────────
def _word_boundary_match(keyword: str, text: str) -> bool:
    """Prüft ob keyword als eigenständiges Wort in text vorkommt.

    Nutzt \\b-Wortgrenzen statt reinem Substring-Match, um False-Positives
    zu vermeiden (z.B. 'hund' in 'hundekot' oder 'park' in 'parkraum').
    """
    return bool(re.search(r'\b' + re.escape(keyword) + r'\b', text))


def is_muell(m: dict) -> bool:
    text = " ".join([
        str(m.get("kategorie", "")),
        str(m.get("betreff",   "")),
        str(m.get("bereich",   "")),
        str(m.get("beschreibung", ""))
    ]).lower()
    # Nicht-Müll-Kategorien zuerst ausschließen (DSGVO-Datenminimierung).
    # Word-Boundary-Match verhindert False-Positives bei zusammengesetzten
    # Wörtern (z.B. 'hund' trifft nicht auf 'hundekot').
    if any(_word_boundary_match(kw, text) for kw in NON_MUELL_KEYWORDS):
        return False
    return any(kw in text for kw in MUELL_KEYWORDS)


def extract_coords(m: dict) -> tuple[float | None, float | None]:
    lat = m.get("lat") or m.get("latitude") or m.get("breitengrad")
    lon = m.get("lon") or m.get("lng") or m.get("longitude") or m.get("laengengrad")
    if not lat and "position" in m:
        lat = m["position"].get("lat") or m["position"].get("latitude")
        lon = m["position"].get("lon") or m["position"].get("lng")
    if not lat and "koordinaten" in m:
        lat = m["koordinaten"].get("lat")
        lon = m["koordinaten"].get("lon")
    if not lat and "geoPosition" in m:
        lat = m["geoPosition"].get("lat") or m["geoPosition"].get("latitude")
        lon = m["geoPosition"].get("lon") or m["geoPosition"].get("lng")
    try:
        return float(lat), float(lon)
    except (TypeError, ValueError):
        return None, None


def make_id(m: dict) -> str:
    raw_id = m.get("id") or m.get("meldungsId") or m.get("meldung_id")
    if raw_id:
        return str(raw_id)
    digest = hashlib.md5(json.dumps(m, sort_keys=True).encode()).hexdigest()
    return f"hash_{digest[:16]}"


def cluster_id(lat: float, lon: float) -> str:
    cell_lat = round(lat / GEO_RADIUS) * GEO_RADIUS
    cell_lon = round(lon / GEO_RADIUS) * GEO_RADIUS
    return f"{cell_lat:.5f}_{cell_lon:.5f}"


# ── Hotspot-Score-Berechnung ──────────────────────────────────────────────────
def compute_score(count: int, recurrence: int, days_since_first: int) -> tuple[float, str]:
    base = count + recurrence * 3
    time_factor = max(0.5, 1 - (days_since_first / 365) * 0.3)
    score = round(base * time_factor, 2)
    if score < 4:    label = "niedrig"
    elif score < 8:  label = "mittel"
    elif score < 13: label = "hoch"
    else:            label = "kritisch"
    return score, label


# ── Hauptlogik ────────────────────────────────────────────────────────────────
def _betreffe_nachziehen(conn) -> dict:
    """A-14: dieselben Regeln über den bereits gespeicherten Bestand ziehen.

    Läuft wie die Löschfristen bei JEDEM Lauf, auch wenn die Schnittstelle
    nichts liefert. Der Schreibweg allein genügt nicht: die Datenbank überlebt
    Rechnerwechsel und Wiedereinspielungen aus Sicherungen, und die Regelliste
    wächst mit jeder Formulierung, die jemand nachträgt. Geprüft werden die
    verschiedenen Werte, nicht die Zeilen — 538 statt 82.780.
    """
    ergebnis = betreff_filter.bestand_nachziehen(conn)
    if ergebnis["werte_geaendert"]:
        log.info("Betreff-Filter: %d von %d verschiedenen Werten entschärft "
                 "(%d Lebenssituation, %d Hausnummer), %d Zeilen betroffen",
                 ergebnis["werte_geaendert"], ergebnis["werte_geprueft"],
                 ergebnis["lebenssituation"], ergebnis["hausnummer"],
                 ergebnis["zeilen_geaendert"])
    return ergebnis


def _fristen_anwenden(conn) -> dict:
    """A-4: Löschfristen anwenden und das Ergebnis protokollieren.

    Bewusst unabhängig vom Ausgang des Abrufs — siehe Aufrufstellen.
    """
    fristen = retention.anwenden(conn, datetime.utcnow())
    log.info("Löschroutine: %d Meldungen wegen Quellabgang (Frist %d Tage) gelöscht, "
             "%d Meldungen auf Aggregate reduziert (Frist %d Monate)",
             fristen["quellabgang_geloescht"], fristen["frist_quelle_tage"],
             fristen["altbestand_aggregiert"], fristen["frist_aggregat_monate"])
    return fristen


def run():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    init_db(conn)

    now = datetime.utcnow().isoformat()
    meldungen = fetch_meldungen()
    log.info("%d Meldungen von API erhalten", len(meldungen))

    # H-02b: Leerer Abruf darf NICHT still als Erfolg durchgehen. Sonst läuft
    # die Pipeline über alte Daten weiter und das Frontend behauptet weiter
    # "tagesaktuell". Fehler-Marker in fetch_log (count_total=-1) schreiben und
    # mit Exit-Code != 0 abbrechen, damit der Launcher den Push überspringt.
    if not meldungen:
        log.error("Abruf lieferte 0 Meldungen — API-Ausfall oder leerer Feed. "
                  "Pipeline wird abgebrochen, kein Hotspot-Neuaufbau, kein Push.")
        conn.execute("""
            INSERT INTO fetch_log (fetched_at, count_total, count_new, count_muell)
            VALUES (?,?,?,?)
        """, (now, -1, 0, 0))
        conn.commit()
        # A-4 / Finding H-01 (29.07.2026): Die Fristen laufen AUCH hier.
        # Die Speicherbegrenzung nach Art. 5 Abs. 1 lit. e darf nicht daran
        # hängen, dass die Schnittstelle der Behörde erreichbar ist — sonst
        # steht die Löschung genau dann still, wenn der Bestand am längsten
        # unangetastet liegt. Der Ausfall seit April 2026 hätte die Routine
        # 98 Tage lang nie ausgeführt.
        # Was hier NICHT läuft, ist die Wegfall-Markierung: die braucht einen
        # vollständigen Abruf, sonst gälte der Ausfall als Massenwegfall.
        _betreffe_nachziehen(conn)
        _fristen_anwenden(conn)
        conn.close()
        return 1

    count_new = 0
    count_muell = 0

    for m in meldungen:
        mid  = make_id(m)
        lat, lon = extract_coords(m)
        muell = is_muell(m)
        datum = (m.get("erstellungsDatum") or m.get("datum") or
                 m.get("erstelltAm") or m.get("created_at") or now[:10])
        # erstellungsDatum ist das echte API-Feld; normalisieren auf YYYY-MM-DD
        datum_raw = datum
        if datum and len(datum) >= 10 and datum[2] == '.':
            # Format "DD.MM.YYYY ..." aus der API
            try:
                datum = datetime.strptime(datum[:10], "%d.%m.%Y").strftime("%Y-%m-%d")
            except ValueError:
                log.warning("Unbekanntes Datumsformat fuer Meldung %s: %r", mid, datum_raw)
                datum = now[:10]

        # DSGVO-Datenminimierung: Nicht-Müll-Meldungen nicht persistieren.
        # Sie können Beschwerden gegen identifizierbare Personen enthalten.
        if not muell:
            continue

        if conn.execute("SELECT id FROM meldungen WHERE id=?", (mid,)).fetchone():
            continue

        # H-02: strasse-Fallback strikt — kein Rückfall auf generische Adress-
        # felder (adresse, address, ort), die Hausnummern enthalten könnten.
        strasse = m.get("strasse") or m.get("street") or ""
        # Hausnummer-Suffix am String-Ende entfernen (z.B. "12a", "14-18b").
        strasse = re.sub(r'\s+\d+[a-zA-Z]?(\s*[-/]\s*\d+[a-zA-Z]?)?\s*$', '', strasse).strip()
        plz_val = m.get("plz") or m.get("postleitzahl") or m.get("zip") or ""

        # A-14: Betrefftexte, die eine Lebenssituation offenlegen, werden VOR
        # dem Schreiben entschärft — nicht erst beim Export. Einmal gespeichert
        # wäre die Angabe im Bestand, in jeder Sicherung und in jedem Klon.
        kategorie_roh = m.get("kategorie") or m.get("category", "")
        betreff_roh = m.get("betreff") or m.get("subject", "")
        betreff_sicher, regeln = betreff_filter.entschaerfe(betreff_roh, kategorie_roh)
        if regeln:
            # Bewusst ohne den Originaltext im Protokoll — sonst stünde die
            # Angabe in tracker.log, die der Filter gerade aus der Datenbank
            # heraushält.
            log.info("Betreff entschärft (Meldung %s, Regel %s)", mid, ",".join(regeln))

        conn.execute("""
            INSERT INTO meldungen
                (id, fetched_at, datum, kategorie, betreff, bezirk, lat, lon, status, is_muell, strasse, plz)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            mid, now, datum,
            kategorie_roh,
            betreff_sicher,
            m.get("bezirk")    or m.get("district",  ""),
            lat, lon,
            m.get("status", ""),
            1,
            strasse, str(plz_val)
        ))
        count_new += 1
        count_muell += 1

    conn.commit()

    # ── A-4: Löschroutine (Art. 5 Abs. 1 lit. e) ──────────────────────────────
    # Zuerst festhalten, was noch in der Quelle steht. Das wertet nur ein
    # vollständiger Abruf aus — ein halber Abruf würde sonst den halben
    # Bestand fälschlich zur Löschung vormerken.
    vollstaendig, begruendung = retention.feed_vollstaendig(conn, len(meldungen))
    if vollstaendig:
        praesenz = retention.markiere_quellpraesenz(conn, (make_id(m) for m in meldungen), now)
        if praesenz.get("abgebrochen"):
            log.warning("Quellabgleich abgebrochen: %s", praesenz["begruendung"])
        else:
            log.info("Quellabgleich: %d Meldungen weiter in der Quelle, %d neu als "
                     "weggefallen markiert (%s)",
                     praesenz["in_quelle"], praesenz["neu_als_weggefallen_markiert"],
                     begruendung)
    else:
        log.warning("Abruf nicht als vollständig gewertet (%s) — Wegfall-Markierung "
                    "übersprungen, es wird nichts neu vorgemerkt.", begruendung)

    _betreffe_nachziehen(conn)
    _fristen_anwenden(conn)

    # ── Hotspot-Berechnung ────────────────────────────────────────────────────
    muell_rows = conn.execute("""
        SELECT id, datum, lat, lon, bezirk, strasse, plz
        FROM meldungen
        WHERE is_muell=1 AND lat IS NOT NULL AND lon IS NOT NULL
        ORDER BY datum ASC
    """).fetchall()

    clusters: dict[str, dict] = {}
    for row in muell_rows:
        cid = cluster_id(row["lat"], row["lon"])
        if cid not in clusters:
            clusters[cid] = {
                "lats": [], "lons": [], "dates": [],
                "bezirk": row["bezirk"], "recurrence": 0,
                "adressen": []
            }
        c = clusters[cid]
        c["lats"].append(row["lat"])
        c["lons"].append(row["lon"])
        c["dates"].append(row["datum"])
        if row["strasse"]:
            c["adressen"].append((row["strasse"], row["plz"] or ""))

    for cid, c in clusters.items():
        dates_sorted = sorted(c["dates"])
        for i in range(1, len(dates_sorted)):
            try:
                d1 = datetime.fromisoformat(dates_sorted[i-1][:10])
                d2 = datetime.fromisoformat(dates_sorted[i][:10])
                gap = (d2 - d1).days
                if 0 < gap <= (DISPOSAL_DAYS + 7):
                    c["recurrence"] += 1
            except Exception:
                pass

    gesperrt = sperrliste.laden(conn)
    uebersprungen_einzelfall = 0
    for cid, c in clusters.items():
        # A-7: gesperrte Zellen werden gar nicht erst angelegt (Art. 21).
        if cid in gesperrt:
            continue
        # A-2: Einzelfall-Zellen nicht persistieren.
        if len(c["dates"]) < HOTSPOT_MIN_PERSIST:
            uebersprungen_einzelfall += 1
            continue
        lat_c = sum(c["lats"]) / len(c["lats"])
        lon_c = sum(c["lons"]) / len(c["lons"])
        dates_sorted = sorted(c["dates"])
        first = dates_sorted[0]
        last  = dates_sorted[-1]
        try:
            days_age = (datetime.utcnow() - datetime.fromisoformat(first[:10])).days
        except Exception:
            days_age = 0

        score, label = compute_score(len(c["dates"]), c["recurrence"], days_age)

        # Häufigste Adresse im Cluster ableiten
        if c["adressen"]:
            most_common = Counter(c["adressen"]).most_common(1)[0][0]
            best_strasse, best_plz = most_common
        else:
            best_strasse, best_plz = "", ""

        conn.execute("""
            INSERT INTO hotspots
                (cluster_id, lat_center, lon_center, bezirk, meldungen_count,
                 recurrence_count, last_seen, first_seen, score, score_label,
                 strasse, plz)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(cluster_id) DO UPDATE SET
                meldungen_count  = excluded.meldungen_count,
                recurrence_count = excluded.recurrence_count,
                last_seen        = excluded.last_seen,
                -- M-02 (Nachaudit 29.07.2026): first_seen muss MIT fortgeschrieben
                -- werden. Ohne diese Zeile blieb der Wert auf dem Stand der ersten
                -- Anlage stehen; loeschte die Aufbewahrungsroutine spaeter die
                -- aelteste Meldung einer Zelle, ueberlebte deren Meldedatum in der
                -- veroeffentlichten Zelle. Es wurde also ein Datum gezeigt, zu dem
                -- es keine Meldung mehr gab. excluded.first_seen ist bei jedem Lauf
                -- neu aus dem tatsaechlichen Bestand der Zelle berechnet.
                first_seen       = excluded.first_seen,
                score            = excluded.score,
                score_label      = excluded.score_label,
                strasse          = excluded.strasse,
                plz              = excluded.plz
        """, (cid, lat_c, lon_c, c["bezirk"], len(c["dates"]),
              c["recurrence"], last, first, score, label,
              best_strasse, best_plz))

    conn.commit()

    # ── Nachlauf: Altbestand an die neuen Regeln angleichen ───────────────────
    # Ohne diesen Schritt würden Zellen, die vor A-2/A-7 angelegt wurden oder
    # deren Meldungen die Löschroutine entfernt hat, unbegrenzt stehenbleiben.
    entfernt_einzelfall = conn.execute(
        "DELETE FROM hotspots WHERE meldungen_count < ?", (HOTSPOT_MIN_PERSIST,)
    ).rowcount
    entfernt_gesperrt = conn.execute(
        "DELETE FROM hotspots WHERE cluster_id IN (SELECT cluster_id FROM sperrliste)"
    ).rowcount
    # Verwaiste Zellen: keine Meldung mehr im Bestand (Folge der Löschroutine).
    bekannt = set(clusters.keys())
    verwaist = [r[0] for r in conn.execute("SELECT cluster_id FROM hotspots")
                if r[0] not in bekannt]
    if verwaist:
        conn.executemany("DELETE FROM hotspots WHERE cluster_id = ?",
                         [(c,) for c in verwaist])
    conn.commit()
    log.info("Zellen-Bereinigung: %d Einzelfall-Zellen nicht angelegt, %d aus dem "
             "Altbestand entfernt, %d gesperrt entfernt, %d verwaist entfernt",
             uebersprungen_einzelfall, entfernt_einzelfall, entfernt_gesperrt,
             len(verwaist))

    conn.execute("""
        INSERT INTO fetch_log (fetched_at, count_total, count_new, count_muell)
        VALUES (?,?,?,?)
    """, (now, len(meldungen), count_new, count_muell))
    conn.commit()

    hotspots_gesamt = conn.execute("SELECT COUNT(*) FROM hotspots").fetchone()[0]
    log.info("Fertig: %d neu, %d Müll-Meldungen, %d Hotspots gespeichert",
             count_new, count_muell, hotspots_gesamt)
    conn.close()
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(run())
