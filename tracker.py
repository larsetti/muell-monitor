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
import open311
import quellen
import retention
import sperrliste

# ── Konfiguration ─────────────────────────────────────────────────────────────
DB_PATH = Path(__file__).parent / "ordnungsamt.db"

# ── Mehrstadt-Faehigkeit (Schema-Vorstufe zu T-49, 15.08.2026) ───────────────
# Der Bestand kannte bisher bezirk, aber keine Stadt. Sobald eine zweite Stadt
# in dieselbe Datenbank schreibt, rechnen alle Routinen, die "der gesamte
# Bestand" meinen, ueber Staedtegrenzen hinweg — die Loeschroutine, der
# Quellabgleich und der Datenstand-Streifen aus T-39 gleichermassen. Deshalb
# traegt jede Zeile ihre Stadt, bevor die erste fremde Meldung importiert wird.
#
# STADT ist seit T-49 (Adapter-Stufe, 15.08.2026) nur noch die VORGABE fuer
# einen Aufruf ohne Angabe. Welche Stadt ein Lauf bedient, entscheidet der
# Parameter von run() beziehungsweise --stadt auf der Befehlszeile; die
# Eigenheiten je Stadt stehen in quellen.py.
#
#     python tracker.py                 Berlin, unveraendert
#     python tracker.py --stadt koeln   Koeln ueber Open311
#
# Es bleibt bei EINEM Tracker: der Ablauf steht hier, der Unterschied zwischen
# den Staedten in quellen.py. Niemand muss mehr eine Konstante von Hand
# aendern, um eine zweite Quelle abzurufen.
STADT = "berlin"

# Trennzeichen zwischen Stadt und der Kennung der Quelle. Berlin vergibt
# numerische Kennungen (1066349), Koelns Open311 vergibt service_request_id,
# ebenfalls numerisch. Ohne Praefix ueberschreibt eine Kollision still eine
# fremde Meldung, weil der Insert bei doppelter Kennung ueberschreibt statt zu
# scheitern. Der Doppelpunkt kommt in keiner der bekannten Quellkennungen vor.
STADT_TRENNER = ":"

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
            plz         TEXT DEFAULT '',
            stadt       TEXT NOT NULL DEFAULT 'berlin'
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
            plz              TEXT DEFAULT '',
            stadt            TEXT NOT NULL DEFAULT 'berlin'
        );

        CREATE TABLE IF NOT EXISTS fetch_log (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            fetched_at  TEXT,
            count_total INTEGER,
            count_new   INTEGER,
            count_muell INTEGER,
            stadt       TEXT NOT NULL DEFAULT 'berlin'
        );
    """)
    conn.commit()

    # Spalten nachrüsten falls DB bereits existiert.
    # T-49: stadt kommt mit DEFAULT 'berlin' dazu. Der Vorgabewert ist Absicht —
    # jede Zeile, die vor der Umstellung geschrieben wurde, ist eine Berliner
    # Zeile, und vorhandener Code, der die Spalte nicht kennt, schreibt weiter
    # korrekte Daten statt an einer NOT-NULL-Bedingung zu scheitern.
    stadt_spalte = "TEXT NOT NULL DEFAULT 'berlin'"
    for col, typedef in [("strasse", "TEXT DEFAULT ''"), ("plz", "TEXT DEFAULT ''"),
                         ("stadt", stadt_spalte)]:
        try:
            conn.execute(f"ALTER TABLE meldungen ADD COLUMN {col} {typedef}")
            conn.commit()
        except sqlite3.OperationalError:
            pass

    for col, typedef in [("strasse", "TEXT DEFAULT ''"), ("plz", "TEXT DEFAULT ''"),
                         ("stadt", stadt_spalte)]:
        try:
            conn.execute(f"ALTER TABLE hotspots ADD COLUMN {col} {typedef}")
            conn.commit()
        except sqlite3.OperationalError:
            pass

    # T-49 / Befund 3: ohne Stadt im fetch_log liest der Datenstand-Streifen aus
    # T-39 den letzten erfolgreichen Abruf IRGENDEINER Stadt. Sobald Koeln
    # abruft, stuende auf der Berliner Seite ein frisches Datum, obwohl von dort
    # seit dem 22.04.2026 nichts mehr kommt — genau der Fehlertyp, den T-39
    # geschlossen hat.
    try:
        conn.execute(f"ALTER TABLE fetch_log ADD COLUMN stadt {stadt_spalte}")
        conn.commit()
    except sqlite3.OperationalError:
        pass

    conn.executescript("""
        CREATE INDEX IF NOT EXISTS idx_stadt_datum  ON meldungen(stadt, datum);
        CREATE INDEX IF NOT EXISTS idx_stadt_bezirk ON meldungen(stadt, bezirk);
        CREATE INDEX IF NOT EXISTS idx_hotspot_stadt ON hotspots(stadt);
        CREATE INDEX IF NOT EXISTS idx_fetchlog_stadt ON fetch_log(stadt, id);
    """)
    conn.commit()

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

    # Den Pfad aus der Verbindung nehmen, nicht aus dem Modul: rueckimport.py
    # und die Tests arbeiten auf einer anderen Datenbank, und ein Protokoll,
    # das dann den Betriebspfad nennt, fuehrt beim Nachlesen in die Irre.
    try:
        pfad = next((r[2] for r in conn.execute("PRAGMA database_list")
                     if r[1] == "main"), None) or DB_PATH
    except sqlite3.Error:
        pfad = DB_PATH
    log.info("Datenbank initialisiert: %s", pfad)


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


def make_id(m: dict, stadt: str) -> str:
    """Kennung einer Meldung, immer mit der Stadt davor.

    T-49 / Befund 4: meldungen.id war TEXT PRIMARY KEY ohne Stadtanteil. Berlin
    und Koeln vergeben beide rein numerische Kennungen; eine Kollision haette
    still eine fremde Meldung ueberschrieben, weil der Insert bei doppelter
    Kennung aktualisiert statt zu scheitern. Der Parameter hat bewusst KEINEN
    Vorgabewert — eine Stadt, die vergessen wird, soll auffallen und nicht
    stillschweigend als Berlin gelten.
    """
    if not stadt:
        raise ValueError("make_id braucht eine Stadt — ein Vorgabewert waere "
                         "genau die stille Verwechslung, die T-49 verhindert.")
    raw_id = m.get("id") or m.get("meldungsId") or m.get("meldung_id")
    if not raw_id:
        digest = hashlib.md5(json.dumps(m, sort_keys=True).encode()).hexdigest()
        raw_id = f"hash_{digest[:16]}"
    return f"{stadt}{STADT_TRENNER}{raw_id}"


def stadt_aus_id(meldung_id: str) -> str:
    """Stadt aus einer praefixierten Kennung. Ohne Praefix gilt Berlin, weil
    jede Zeile ohne Praefix aus der Zeit vor der Umstellung stammt."""
    stadt, trenner, _ = (meldung_id or "").partition(STADT_TRENNER)
    return stadt if trenner else "berlin"


def roh_id(meldung_id: str) -> str:
    """Kennung ohne Stadt-Praefix — fuer Abfragen gegen die Quelle, die ihre
    eigene Nummer erwartet (enrich.py, fix_datum.py)."""
    _, trenner, rest = (meldung_id or "").partition(STADT_TRENNER)
    return rest if trenner else meldung_id


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

    T-49: läuft bewusst über ALLE Städte, nicht nur über die dieses Laufs. Die
    Routine rechnet dabei nicht stadtblind, sondern nacheinander je Stadt
    (retention.anwenden ohne Stadt-Angabe). Der Grund ist derselbe wie bei
    Finding H-01: die Speicherbegrenzung nach Art. 5 Abs. 1 lit. e darf nicht
    daran hängen, dass die Schnittstelle EINER Stadt gerade erreichbar ist.
    Berlin liefert seit dem 22.04.2026 nichts mehr; seine Fristen müssen
    trotzdem weiterlaufen, wenn Köln abruft.
    """
    fristen = retention.anwenden(conn, datetime.utcnow())
    log.info("Löschroutine: %d Meldungen wegen Quellabgang (Frist %d Tage) gelöscht, "
             "%d Meldungen auf Aggregate reduziert (Frist %d Monate)",
             fristen["quellabgang_geloescht"], fristen["frist_quelle_tage"],
             fristen["altbestand_aggregiert"], fristen["frist_aggregat_monate"])
    return fristen


def berechne_hotspots(conn, quelle) -> dict:
    """Zellen einer Stadt aus ihrem Meldungsbestand neu berechnen.

    Aus run() herausgeloest, damit der Rueckimport (rueckimport.py) dieselbe
    Rechnung benutzt statt einer zweiten Fassung davon. Fachlich unveraendert.
    """
    # ── Hotspot-Berechnung ────────────────────────────────────────────────────
    # T-49: nur der Bestand dieser Stadt. Die Zellen der anderen Staedte werden
    # von deren eigenem Lauf gepflegt und duerfen hier weder neu berechnet noch
    # als verwaist eingestuft werden.
    muell_rows = conn.execute("""
        SELECT id, datum, lat, lon, bezirk, strasse, plz
        FROM meldungen
        WHERE is_muell=1 AND lat IS NOT NULL AND lon IS NOT NULL AND stadt=?
        ORDER BY datum ASC
    """, (quelle.stadt,)).fetchall()

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

    # T-49: das Wiederkehr-Fenster gehoert der Stadt, nicht dem Modul. Der
    # Berliner Wert (14 Tage Abfuhr plus 7 Tage Puffer) beschreibt den Berliner
    # Reinigungsrhythmus und ist auf eine andere Stadt nicht uebertragbar —
    # siehe quellen.py, wo je Stadt steht, worauf der Wert beruht.
    fenster = quelle.wiederkehr_fenster_tage
    for cid, c in clusters.items():
        dates_sorted = sorted(c["dates"])
        for i in range(1, len(dates_sorted)):
            try:
                d1 = datetime.fromisoformat(dates_sorted[i-1][:10])
                d2 = datetime.fromisoformat(dates_sorted[i][:10])
                gap = (d2 - d1).days
                if 0 < gap <= fenster:
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
                 strasse, plz, stadt)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(cluster_id) DO UPDATE SET
                stadt            = excluded.stadt,
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
              best_strasse, best_plz, quelle.stadt))

    conn.commit()

    # ── Nachlauf: Altbestand an die neuen Regeln angleichen ───────────────────
    # Ohne diesen Schritt würden Zellen, die vor A-2/A-7 angelegt wurden oder
    # deren Meldungen die Löschroutine entfernt hat, unbegrenzt stehenbleiben.
    #
    # T-66 (15.08.2026, Befund K-03): Diese Bereinigung lief bis hierher über
    # ALLE Städte. Gemessen hat ein einziger Kölner Lauf die Berliner Zellen von
    # 8.748 auf 5.839 gesenkt, während die Berliner Meldungen unangetastet
    # blieben. Datenschutzrechtlich war jede einzelne dieser Löschungen richtig
    # — die Zellen trugen weniger als HOTSPOT_MIN_PERSIST Meldungen und hätten
    # nach A-2 nie liegen dürfen. Falsch war, WER sie gelöscht hat: eine Stadt
    # räumt hier im Bestand einer anderen auf. Berlin ist dabei der Sonderfall,
    # der aus dem Schönheitsfehler ein Risiko macht: die Berliner Quelle ist
    # seit dem 22.04.2026 tot, es wird nie wieder einen Berliner Lauf geben, der
    # eine zu Unrecht entfernte Zelle neu aufbaut. Würde HOTSPOT_MIN_PERSIST je
    # angehoben, nähme ein Kölner Lauf Berlin unwiederbringlich Zellen weg.
    entfernt_einzelfall = conn.execute(
        "DELETE FROM hotspots WHERE meldungen_count < ? AND stadt = ?",
        (HOTSPOT_MIN_PERSIST, quelle.stadt)
    ).rowcount
    # A-7 dagegen bleibt BEWUSST stadtblind, und das ist keine vergessene Hälfte
    # von T-66, sondern die Bedingung dafür, dass ein Widerspruch nach Art. 21
    # DSGVO überhaupt wirkt. Dieselbe Begründung trägt schon sperrliste.laden():
    #
    #   1. Eine Zell-Kennung ist eine auf ~150 m gerundete Koordinate und damit
    #      weltweit eindeutig. Zwei Städte können sich in derselben Zelle nicht
    #      begegnen — ein Stadt-Filter hätte hier nichts zu trennen, er könnte
    #      nur etwas übersehen.
    #   2. Die ausfallsichere Zweitschrift sperrliste.txt führt KEINE Stadt.
    #      Jeder von dort zurückgespielte Eintrag trägt den Vorgabewert
    #      'berlin'. Mit einem Stadt-Filter fiele ein so wiederhergestellter
    #      Widerspruch bei jedem Kölner Lauf still aus der Sperre heraus.
    #   3. Berlin läuft nicht mehr. Ein Widerspruch gegen eine Berliner Zelle
    #      würde mit Stadt-Filter von keinem Lauf mehr vollzogen — die Zeile
    #      bliebe stehen, obwohl ihr ausdrücklich widersprochen wurde.
    #
    # Zu breit sperren kann keine Daten offenlegen, zu eng sperren schon. Die
    # Richtung des Fehlers entscheidet. Bewacht von
    # test_staedte.py::test_sperre_greift_auch_bei_fremdem_stadtlauf.
    entfernt_gesperrt = conn.execute(
        "DELETE FROM hotspots WHERE cluster_id IN (SELECT cluster_id FROM sperrliste)"
    ).rowcount
    # Verwaiste Zellen: keine Meldung mehr im Bestand (Folge der Löschroutine).
    # T-49: NUR die Zellen dieser Stadt prüfen. clusters enthält ausschließlich
    # Zellen aus dem Bestand dieser Stadt — ohne den Filter gälte jede Zelle einer
    # anderen Stadt als verwaist und würde bei jedem Berliner Lauf gelöscht.
    bekannt = set(clusters.keys())
    verwaist = [r[0] for r in conn.execute(
        "SELECT cluster_id FROM hotspots WHERE stadt=?", (quelle.stadt,))
        if r[0] not in bekannt]
    if verwaist:
        conn.executemany("DELETE FROM hotspots WHERE cluster_id = ?",
                         [(c,) for c in verwaist])
    conn.commit()
    log.info("Zellen-Bereinigung [%s]: %d Einzelfall-Zellen nicht angelegt, %d aus dem "
             "Altbestand entfernt, %d verwaist entfernt (alle drei nur %s); "
             "%d gesperrt entfernt (stadtuebergreifend, A-7)",
             quelle.stadt, uebersprungen_einzelfall, entfernt_einzelfall,
             len(verwaist), quelle.stadt, entfernt_gesperrt)

    return {
        "zellen": len(clusters),
        "uebersprungen_einzelfall": uebersprungen_einzelfall,
        "entfernt_einzelfall": entfernt_einzelfall,
        "entfernt_gesperrt": entfernt_gesperrt,
        "entfernt_verwaist": len(verwaist),
    }


def run(stadt: str = None, zeitraum=None):
    """Ein Lauf fuer genau eine Stadt.

    ``stadt`` waehlt den Eintrag aus quellen.py; ohne Angabe laeuft Berlin wie
    bisher. ``zeitraum`` reicht ein Abrufintervall an Quellen durch, die eines
    kennen (Open311); der Berliner Feed kennt keines und ignoriert es.
    """
    quelle = quellen.hole(stadt or STADT)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    init_db(conn)

    now = datetime.utcnow().isoformat()
    log.info("Lauf fuer %s (%s)", quelle.name, quelle.stadt)
    try:
        meldungen = (quelle.hole_meldungen(zeitraum) if zeitraum is not None
                     else quelle.hole_meldungen())
    except open311.AbrufFehler as fehler:
        # Auflage 3c aus T-49: ein unplausibles Ergebnis ist ein Ausfall und
        # KEIN Befund. Der Lauf endet wie ein leerer Abruf — mit Fehlermarke im
        # Abrufprotokoll und ohne Neuaufbau der Zellen.
        log.error("Abruf fuer %s gescheitert: %s", quelle.stadt, fehler)
        meldungen = []
    log.info("%d Meldungen von der Quelle %s erhalten", len(meldungen), quelle.stadt)

    # H-02b: Leerer Abruf darf NICHT still als Erfolg durchgehen. Sonst läuft
    # die Pipeline über alte Daten weiter und das Frontend behauptet weiter
    # "tagesaktuell". Fehler-Marker in fetch_log (count_total=-1) schreiben und
    # mit Exit-Code != 0 abbrechen, damit der Launcher den Push überspringt.
    if not meldungen:
        log.error("Abruf fuer %s lieferte 0 Meldungen — Ausfall oder leerer "
                  "Feed. Pipeline wird abgebrochen, kein Hotspot-Neuaufbau, "
                  "kein Push.", quelle.stadt)
        conn.execute("""
            INSERT INTO fetch_log (fetched_at, count_total, count_new, count_muell, stadt)
            VALUES (?,?,?,?,?)
        """, (now, -1, 0, 0, quelle.stadt))
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
        # Die Zuordnung auf unsere Spalten macht die Quelle. Sie gibt None
        # zurueck, wenn die Meldung nicht muellnah ist — dann wird sie gar
        # nicht erst gespeichert (DSGVO-Datenminimierung: Nicht-Muell enthaelt
        # Beschwerden gegen identifizierbare Personen).
        satz = quelle.aufbereiten(m, now)
        if satz is None:
            continue

        if conn.execute("SELECT id FROM meldungen WHERE id=?",
                        (satz["id"],)).fetchone():
            continue

        conn.execute("""
            INSERT INTO meldungen
                (id, fetched_at, datum, kategorie, betreff, bezirk, lat, lon, status,
                 is_muell, strasse, plz, stadt)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            satz["id"], now, satz["datum"],
            satz["kategorie"],
            satz["betreff"],
            satz["bezirk"],
            satz["lat"], satz["lon"],
            satz["status"],
            1,
            satz["strasse"], str(satz["plz"]),
            quelle.stadt
        ))
        count_new += 1
        count_muell += 1

    conn.commit()

    # ── A-4: Löschroutine (Art. 5 Abs. 1 lit. e) ──────────────────────────────
    # Zuerst festhalten, was noch in der Quelle steht. Das wertet nur ein
    # vollständiger Abruf aus — ein halber Abruf würde sonst den halben
    # Bestand fälschlich zur Löschung vormerken.
    # T-49 / Befund 2: beides rechnet ausschliesslich innerhalb dieser Stadt.
    # Stadtblind wuerde ein Koelner Lauf den gesamten Berliner Bestand als aus
    # der Quelle verschwunden vormerken und 30 Tage spaeter loeschen.
    # T-49: Quellen, deren Abruf ein ZEITRAUM und nicht der Bestand ist, nehmen
    # an diesem Schritt gar nicht teil. Sonst gaelte jede Meldung ausserhalb des
    # Abrufzeitraums als aus der Quelle verschwunden. Begruendung ausfuehrlich
    # in quellen.Quelle.quellabgleich_moeglich.
    if not quelle.quellabgleich_moeglich:
        log.info("Quellabgleich fuer %s entfaellt bewusst: der Abruf liefert "
                 "einen Zeitraum und nicht den Bestand. Abwesenheit ist hier "
                 "kein Wegfall.", quelle.stadt)
    else:
        vollstaendig, begruendung = retention.feed_vollstaendig(
            conn, len(meldungen), quelle.stadt)
        if vollstaendig:
            praesenz = retention.markiere_quellpraesenz(
                conn, (quelle.kennung(m) for m in meldungen), now, quelle.stadt)
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
    berechne_hotspots(conn, quelle)

    conn.execute("""
        INSERT INTO fetch_log (fetched_at, count_total, count_new, count_muell, stadt)
        VALUES (?,?,?,?,?)
    """, (now, len(meldungen), count_new, count_muell, quelle.stadt))
    conn.commit()

    hotspots_gesamt = conn.execute("SELECT COUNT(*) FROM hotspots").fetchone()[0]
    log.info("Fertig: %d neu, %d Müll-Meldungen, %d Hotspots gespeichert",
             count_new, count_muell, hotspots_gesamt)
    conn.close()
    return 0


if __name__ == "__main__":
    import argparse
    import sys

    p = argparse.ArgumentParser(description="Muell-Tracker, ein Lauf je Stadt")
    p.add_argument("--stadt", default=STADT,
                   choices=sorted(quellen.ALLE),
                   help=f"Stadt dieses Laufs (Vorgabe: {STADT})")
    p.add_argument("--tage", type=int, default=None,
                   help="nur bei Open311-Quellen: Zeitraum der letzten N Tage. "
                        "Ohne Angabe liefert die Quelle ihren Standardzeitraum.")
    args = p.parse_args()

    abrufzeitraum = None
    if args.tage is not None:
        abrufzeitraum = open311.zeitraum_tage(datetime.utcnow(), args.tage)

    sys.exit(run(args.stadt, abrufzeitraum))
