"""
Tests zu T-66: die Zellen-Bereinigung darf nicht stadtblind sein
=================================================================
Befund K-03 der Abnahme vom 15.08.2026. `tracker.berechne_hotspots` enthaelt
drei Loeschungen im Nachlauf. Die Verwaisten-Bereinigung war korrekt auf die
eigene Stadt begrenzt, die beiden davor nicht:

    DELETE FROM hotspots WHERE meldungen_count < ?
    DELETE FROM hotspots WHERE cluster_id IN (SELECT cluster_id FROM sperrliste)

Gemessen gegen eine Kopie der Betriebsdatenbank hat ein einziger Koelner Lauf
die Berliner Zellen von 8.748 auf 5.839 gesenkt, waehrend die Berliner
Meldungen byte-genau unveraendert blieben.

WARUM DAS EIN RISIKO IST und nicht nur unsauber: Die Berliner Quelle ist seit
dem 22.04.2026 tot. Es wird nie wieder einen Berliner Lauf geben, der eine zu
Unrecht entfernte Zelle aus dem Meldungsbestand neu aufbaut. Was ein fremder
Lauf hier wegnimmt, ist weg.

DIE ZWEITE LOESCHUNG BLEIBT ABSICHTLICH STADTBLIND. Eine Sperre nach Art. 21
DSGVO muss ueberall greifen, sonst ist sie keine. Ein Stadt-Filter waere hier
der schwerere Fehler — die zweite Haelfte dieser Datei bewacht genau das und
wird rot, wenn jemand ihn nachtraeglich einbaut.
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


def _db(tmp_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(tmp_path / "z.db")
    conn.row_factory = sqlite3.Row
    tracker.init_db(conn)
    return conn


def _meldung(conn, mid: str, datum: str, stadt: str, ort):
    conn.execute(
        "INSERT INTO meldungen (id, fetched_at, datum, kategorie, betreff, bezirk, "
        "lat, lon, status, is_muell, strasse, plz, stadt) "
        "VALUES (?,'2026-08-15',?,'Sperrmüll','','Mitte',?,?,'offen',1,"
        "'Teststr','10115',?)",
        (mid, datum, ort[0], ort[1], stadt))
    conn.commit()


def _zelle(conn, ort, stadt: str, count: int):
    """Legt eine Zelle unmittelbar an, ohne den Weg ueber die Berechnung.

    So entsteht der Altbestand aus der Zeit vor Abhilfe A-2 — Zellen mit
    weniger als HOTSPOT_MIN_PERSIST Meldungen, die es heute nicht mehr geben
    duerfte und die genau deshalb der Bereinigung zum Opfer fallen.
    """
    conn.execute(
        "INSERT INTO hotspots (cluster_id, lat_center, lon_center, bezirk, "
        "meldungen_count, recurrence_count, last_seen, first_seen, score, "
        "score_label, strasse, plz, stadt) "
        "VALUES (?,?,?,'Mitte',?,0,'2026-01-01','2026-01-01',1.0,'niedrig','','',?)",
        (tracker.cluster_id(*ort), ort[0], ort[1], count, stadt))
    conn.commit()


def _zellen(conn, stadt: str) -> int:
    return conn.execute("SELECT COUNT(*) FROM hotspots WHERE stadt = ?",
                        (stadt,)).fetchone()[0]


def _koelner_lauf(conn, tmp_path: Path):
    """Nur die Zellenrechnung, ohne Netz. Genau der Weg, den der taegliche
    Koelner Lauf und der Rueckimport gemeinsam benutzen."""
    sperrliste.SPERRLISTE_DATEI = tmp_path / "sperr.txt"
    return tracker.berechne_hotspots(conn, quellen.hole("koeln"))


# ── Der eigentliche Befund K-03 ──────────────────────────────────────────────

def test_koelner_lauf_laesst_berliner_einzelfall_zellen_stehen(tmp_path):
    """Der Kern von T-66. Ohne den Stadt-Filter faellt hier der Berliner
    Altbestand, sobald Koeln rechnet — und Berlin baut ihn nie wieder auf."""
    conn = _db(tmp_path)
    # Berliner Altbestand aus der Zeit vor A-2: zwei Zellen mit je einer Meldung
    _zelle(conn, BERLIN, "berlin", count=1)
    _zelle(conn, BERLIN_2, "berlin", count=1)
    # Koeln hat echte Meldungen und eine eigene Einzelfall-Altlast
    _meldung(conn, "koeln:1", "2026-08-01", "koeln", KOELN)
    _meldung(conn, "koeln:2", "2026-08-05", "koeln", KOELN)
    _zelle(conn, KOELN_2, "koeln", count=1)

    vorher_berlin = _zellen(conn, "berlin")
    ergebnis = _koelner_lauf(conn, tmp_path)
    nachher_berlin = _zellen(conn, "berlin")
    koelner_zellen = {r[0] for r in conn.execute(
        "SELECT cluster_id FROM hotspots WHERE stadt='koeln'")}
    conn.close()

    assert vorher_berlin == 2
    assert nachher_berlin == 2, (
        f"Ein Koelner Lauf hat {vorher_berlin - nachher_berlin} Berliner Zellen "
        f"entfernt. Das ist Befund K-03: die Einzelfall-Bereinigung im Nachlauf "
        f"von berechne_hotspots muss auf die eigene Stadt begrenzt bleiben "
        f"(AND stadt = ?). Berlin kann geloeschte Zellen nicht neu aufbauen, "
        f"die Quelle ist seit dem 22.04.2026 tot."
    )
    # Die eigene Stadt raeumt sehr wohl auf — sonst waere der Filter zu breit
    assert ergebnis["entfernt_einzelfall"] == 1, (
        "Die eigene Koelner Einzelfall-Zelle muss weiterhin fallen (A-2)."
    )
    assert tracker.cluster_id(*KOELN_2) not in koelner_zellen


def test_koelner_lauf_laesst_berliner_zellen_und_meldungen_unangetastet(tmp_path):
    """Breitere Fassung: nach einem fremden Lauf muss der gesamte Berliner
    Bestand Zeile fuer Zeile derselbe sein — Zellen wie Meldungen."""
    conn = _db(tmp_path)
    _meldung(conn, "berlin:1", "2026-01-01", "berlin", BERLIN)
    _meldung(conn, "berlin:2", "2026-01-10", "berlin", BERLIN)
    _zelle(conn, BERLIN, "berlin", count=2)
    _zelle(conn, BERLIN_2, "berlin", count=1)
    _meldung(conn, "koeln:1", "2026-08-01", "koeln", KOELN)
    _meldung(conn, "koeln:2", "2026-08-05", "koeln", KOELN)

    def stand():
        return (
            tuple(conn.execute("SELECT COUNT(*), SUM(LENGTH(id)) FROM meldungen "
                               "WHERE stadt='berlin'").fetchone()),
            sorted(tuple(r) for r in conn.execute(
                "SELECT cluster_id, meldungen_count, stadt "
                "FROM hotspots WHERE stadt='berlin'")),
        )

    vorher = stand()
    _koelner_lauf(conn, tmp_path)
    nachher = stand()
    conn.close()

    assert vorher == nachher, (
        f"Ein Koelner Lauf hat den Berliner Bestand veraendert.\n"
        f"vorher:  {vorher}\nnachher: {nachher}"
    )


# ── K-04: der Stadt-Filter im SELECT, nicht nur in den Loeschungen ───────────
#
# Die beiden Tests darueber bewachen die Loeschungen im Nachlauf. Der Filter im
# SELECT am Anfang von berechne_hotspots war davon nicht gedeckt: die
# Mutationsprobe der Abnahme vom 15.08.2026 hat ihn als M5 entfernt, und nur ein
# einziger Test wurde rot — der Vergleich "Zeile fuer Zeile derselbe Bestand",
# der zwar anschlaegt, aber nicht sagt, WAS passiert ist. Der Test hier benennt
# den Schaden: die Berliner Zelle wird nicht geloescht, sie wechselt die Stadt.
#
# Der Weg dahin fuehrt ueber die ON-CONFLICT-Klausel mit stadt = excluded.stadt.
# Rechnet ein Koelner Lauf auch die Berliner Zellen, schreibt er sie als KOELNER
# Zellen zurueck. Danach liefert die Berliner Karte nichts mehr und die Koelner
# ganz Berlin — ohne dass irgendwo eine Zeile fehlt.

def test_ein_koelner_lauf_faerbt_keine_berliner_zelle_um(tmp_path):
    """Der Kern von K-04. Nach einem Koelner Lauf darf keine Zelle noerdlich des
    52. Breitengrades als koeln gefuehrt werden — dort liegt Berlin, Koeln liegt
    bei 50,9 Grad."""
    conn = _db(tmp_path)
    # Berlin: zwei Meldungen in einer Zelle, also eine Zelle, die nach A-2
    # bestehen bleiben DARF und deshalb umgefaerbt werden koennte.
    _meldung(conn, "berlin:1", "2026-01-01", "berlin", BERLIN)
    _meldung(conn, "berlin:2", "2026-01-10", "berlin", BERLIN)
    _zelle(conn, BERLIN, "berlin", count=2)
    # Koeln hat eigene Meldungen, der Lauf hat also etwas zu rechnen.
    _meldung(conn, "koeln:1", "2026-08-01", "koeln", KOELN)
    _meldung(conn, "koeln:2", "2026-08-05", "koeln", KOELN)

    vorher_berlin = _zellen(conn, "berlin")
    _koelner_lauf(conn, tmp_path)

    umgefaerbt = conn.execute(
        "SELECT cluster_id, lat_center, stadt FROM hotspots "
        "WHERE lat_center > 52 AND stadt <> 'berlin'").fetchall()
    nachher_berlin = _zellen(conn, "berlin")
    conn.close()

    assert not umgefaerbt, (
        f"Ein Koelner Lauf hat Berliner Zellen als koelnische zurueckgeschrieben: "
        f"{[tuple(r) for r in umgefaerbt]}. Damit liefert die Berliner Karte eine "
        f"leere Flaeche und die Koelner ganz Berlin. Ursache ist der fehlende "
        f"Stadt-Filter im SELECT von berechne_hotspots (Befund K-04) — die "
        f"ON-CONFLICT-Klausel setzt stadt = excluded.stadt."
    )
    assert nachher_berlin == vorher_berlin == 1


def test_ein_koelner_lauf_rechnet_nur_koelner_meldungen(tmp_path):
    """Dieselbe Ursache, eine Ebene frueher gemessen: der Lauf darf die
    Berliner Meldungen gar nicht erst in seine Rechnung nehmen. Die Zahl der
    berechneten Zellen ist der Beleg."""
    conn = _db(tmp_path)
    for i, tag in enumerate(("2026-01-01", "2026-01-10", "2026-01-20")):
        _meldung(conn, f"berlin:{i}", tag, "berlin", BERLIN)
    _meldung(conn, "koeln:1", "2026-08-01", "koeln", KOELN)
    _meldung(conn, "koeln:2", "2026-08-05", "koeln", KOELN)

    ergebnis = _koelner_lauf(conn, tmp_path)
    conn.close()

    assert ergebnis["zellen"] == 1, (
        f"Der Koelner Lauf hat {ergebnis['zellen']} Zellen berechnet, erwartet "
        f"war die eine Koelner. Er hat also auch den Berliner Bestand gerechnet "
        f"(Befund K-04)."
    )


def test_eigene_stadt_raeumt_ihren_altbestand_weiter_auf(tmp_path):
    """Gegenprobe zum Stadt-Filter: er darf A-2 nicht aushebeln. Laeuft Berlin
    selbst, muss der Berliner Altbestand fallen."""
    conn = _db(tmp_path)
    _zelle(conn, BERLIN_2, "berlin", count=1)
    _meldung(conn, "berlin:1", "2026-01-01", "berlin", BERLIN)
    _meldung(conn, "berlin:2", "2026-01-10", "berlin", BERLIN)

    sperrliste.SPERRLISTE_DATEI = tmp_path / "sperr.txt"
    ergebnis = tracker.berechne_hotspots(conn, quellen.hole("berlin"))
    uebrig = {r[0] for r in conn.execute(
        "SELECT cluster_id FROM hotspots WHERE stadt='berlin'")}
    conn.close()

    assert ergebnis["entfernt_einzelfall"] == 1
    assert tracker.cluster_id(*BERLIN_2) not in uebrig
    assert tracker.cluster_id(*BERLIN) in uebrig


def test_verwaiste_bereinigung_bleibt_stadtscharf(tmp_path):
    """Die dritte Loeschung war schon vor T-66 richtig. Der Test haelt sie
    fest, damit sie beim Umbau nicht mitgerissen wird."""
    conn = _db(tmp_path)
    _zelle(conn, BERLIN, "berlin", count=5)   # ohne zugehoerige Meldungen
    _meldung(conn, "koeln:1", "2026-08-01", "koeln", KOELN)
    _meldung(conn, "koeln:2", "2026-08-05", "koeln", KOELN)

    ergebnis = _koelner_lauf(conn, tmp_path)
    conn.close()

    assert ergebnis["entfernt_verwaist"] == 0, (
        "Eine Berliner Zelle ohne Meldungen gilt aus Koelner Sicht nicht als "
        "verwaist — der Koelner Lauf kennt den Berliner Bestand nicht."
    )


# ── Die Gegenrichtung: die Sperre muss stadtblind bleiben ────────────────────

def test_sperre_greift_auch_bei_fremdem_stadtlauf(tmp_path):
    """A-7 / Art. 21 DSGVO. Der Gegenpol zu T-66, und der wichtigere Test.

    Eine gesperrte Zelle muss auch dann aus dem Bestand fallen, wenn gerade
    eine ANDERE Stadt rechnet. Drei Gruende, jeder traegt fuer sich:

      1. Eine Zell-Kennung ist eine gerundete Koordinate und weltweit
         eindeutig. Es gibt nichts zu trennen.
      2. Die Zweitschrift sperrliste.txt fuehrt keine Stadt. Ein von dort
         zurueckgespielter Widerspruch traegt den Vorgabewert 'berlin' und
         fiele mit einem Stadt-Filter bei jedem Koelner Lauf still heraus.
      3. Berlin laeuft nicht mehr. Mit Stadt-Filter wuerde ein Widerspruch
         gegen eine Berliner Zelle von KEINEM Lauf mehr vollzogen.

    Wird dieser Test rot, ist die technische Umsetzung des Widerspruchsrechts
    unwirksam geworden — ein schwererer Fehler als der, den T-66 behebt.
    """
    conn = _db(tmp_path)
    datei = tmp_path / "sperr.txt"
    # Eine Berliner Zelle mit genug Meldungen, damit sie NUR ueber die Sperre
    # fallen kann und nicht schon ueber die Einzelfall-Regel.
    _zelle(conn, BERLIN, "berlin", count=9)
    _meldung(conn, "koeln:1", "2026-08-01", "koeln", KOELN)
    _meldung(conn, "koeln:2", "2026-08-05", "koeln", KOELN)

    sperrliste.eintragen(conn, tracker.cluster_id(*BERLIN),
                         quelle="W-2026-099", stadt="berlin", datei=datei)
    conn.commit()

    assert _zellen(conn, "berlin") == 1, "Ausgangslage: die Zelle steht noch"

    sperrliste.SPERRLISTE_DATEI = datei
    ergebnis = tracker.berechne_hotspots(conn, quellen.hole("koeln"))
    uebrig = _zellen(conn, "berlin")
    conn.close()

    assert uebrig == 0, (
        "Die gesperrte Berliner Zelle steht nach einem Koelner Lauf noch im "
        "Bestand. Damit ist ein Widerspruch nach Art. 21 DSGVO unwirksam. Die "
        "Sperr-Loeschung in berechne_hotspots muss stadtblind bleiben — sie "
        "darf KEIN 'AND stadt = ?' bekommen, anders als die Einzelfall-Regel "
        "daneben (T-66)."
    )
    assert ergebnis["entfernt_gesperrt"] == 1


def test_sperre_aus_der_zweitschrift_greift_bei_fremdem_stadtlauf(tmp_path):
    """Der Fall, den ein Stadt-Filter am unauffaelligsten kaputtmachen wuerde.

    Nach einem Rechnerwechsel entsteht die Datenbank neu; die Sperre existiert
    dann nur noch in sperrliste.txt. Diese Datei fuehrt KEINE Stadt, der
    zurueckgespielte Eintrag traegt also den Vorgabewert 'berlin'. Betrifft der
    Widerspruch in Wahrheit eine Koelner Zelle, wuerde ein Stadt-Filter ihn bei
    jedem Koelner Lauf uebergehen — ohne dass irgendwo ein Fehler erschiene.
    """
    conn = _db(tmp_path)
    datei = tmp_path / "sperr.txt"
    datei.write_text(
        sperrliste.DATEI_KOPF + f"{tracker.cluster_id(*KOELN_2)}  # W-2026-100 | \n",
        encoding="utf-8")

    _zelle(conn, KOELN_2, "koeln", count=9)
    _meldung(conn, "koeln:1", "2026-08-01", "koeln", KOELN)
    _meldung(conn, "koeln:2", "2026-08-05", "koeln", KOELN)

    sperrliste.SPERRLISTE_DATEI = datei
    tracker.berechne_hotspots(conn, quellen.hole("koeln"))
    uebrig = {r[0] for r in conn.execute("SELECT cluster_id FROM hotspots")}
    zurueckgespielt = conn.execute(
        "SELECT stadt FROM sperrliste WHERE cluster_id = ?",
        (tracker.cluster_id(*KOELN_2),)).fetchone()[0]
    conn.close()

    assert zurueckgespielt == "berlin", (
        "Die Zweitschrift fuehrt keine Stadt — der Eintrag kommt mit dem "
        "Vorgabewert zurueck. Genau das ist der Grund fuer die stadtblinde "
        "Pruefung."
    )
    assert tracker.cluster_id(*KOELN_2) not in uebrig, (
        "Die aus der Zweitschrift wiederhergestellte Sperre hat nicht "
        "gegriffen."
    )


def test_berliner_und_koelner_zellkennungen_koennen_nicht_kollidieren():
    """Belegt die Annahme hinter der stadtblinden Sperre: eine Zell-Kennung ist
    eine gerundete Koordinate, zwei Staedte begegnen sich darin nie.

    Geprueft an den Eckpunkten der beiden Stadtgebiete, grosszuegig gefasst.
    """
    berliner = {tracker.cluster_id(lat / 1000, lon / 1000)
                for lat in range(52300, 52700, 7)
                for lon in range(13050, 13800, 7)}
    koelner = {tracker.cluster_id(lat / 1000, lon / 1000)
               for lat in range(50800, 51100, 7)
               for lon in range(6750, 7200, 7)}
    assert not (berliner & koelner)
