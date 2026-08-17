"""
Tests zu K-12: der Bezirk einer Zelle ist ihr Mehrheitswert
===========================================================
Befund K-12 der Abnahme vom 15.08.2026, umgesetzt unter T-68 am 17.08.2026.

`tracker.berechne_hotspots` bestimmt die Strasse einer Zelle ueber
`Counter.most_common`, den Bezirk uebernahm es dagegen aus der ERSTEN Meldung.
War das eine ohne Stadtteil, blieb die ganze Zelle ohne — obwohl der Wert in
ihren uebrigen Meldungen steht.

GEMESSEN am 15.08.2026 gegen eine Kopie der Betriebsdatenbank: von den 281
Koelner Zellen ab drei Meldungen ohne Bezirk fuehren 185 den Stadtteil in einer
anderen ihrer eigenen Meldungen, also 66 Prozent. Mit der Mehrheitsregel faellt
die Zahl leerer Zellen von 281 auf 96 (8,1 auf 2,8 Prozent). Berlin ist nicht
betroffen, dort ist keine Zelle leer.

WARUM GENAU SO UND NICHT ANDERS: Es entsteht kein neues Datum, ein bereits
gespeichertes wird nur richtig ausgewaehlt — die Risikobewertung der
Folgenabschaetzung bleibt unberuehrt. Der naheliegende zweite Weg, den
Stadtteil aus der Postleitzahl abzuleiten, waere neue Ortsinformation erzeugen
statt vorhandene lesen und muesste durch die Bewertung. Deshalb bleibt auch
`open311.zerlege_adresse` unangetastet: die Bauart "50739 Koeln,
Wilensteinweg 13" fuehrt schlicht keinen Stadtteil, der Parser uebersieht
nichts.
"""

import sqlite3
import sys
from pathlib import Path

TECHNIK = Path(__file__).parent.parent
sys.path.insert(0, str(TECHNIK))

import quellen  # noqa: E402
import tracker  # noqa: E402

KOELN = (50.9375, 6.9603)


def _db(tmp_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(tmp_path / "b.db")
    conn.row_factory = sqlite3.Row
    tracker.init_db(conn)
    return conn


def _meldung(conn, mid: str, datum: str, bezirk: str, ort=KOELN, versatz=0.0):
    conn.execute(
        "INSERT INTO meldungen (id, fetched_at, datum, kategorie, betreff, bezirk, "
        "lat, lon, status, is_muell, strasse, plz, stadt) "
        "VALUES (?,'2026-08-17',?,'Sperrmüll','',?,?,?,'offen',1,"
        "'Domstr','50667','koeln')",
        (mid, datum, bezirk, ort[0] + versatz, ort[1] + versatz))
    conn.commit()


def _bezirk(conn) -> str:
    return conn.execute(
        "SELECT bezirk FROM hotspots WHERE cluster_id = ?",
        (tracker.cluster_id(*KOELN),)).fetchone()[0]


def _lauf(conn):
    return tracker.berechne_hotspots(conn, quellen.hole("koeln"))


# ── Der eigentliche Befund K-12 ──────────────────────────────────────────────

def test_erste_meldung_ohne_stadtteil_macht_die_zelle_nicht_leer(tmp_path):
    """Der Kern. Die aelteste Meldung fuehrt keinen Stadtteil, die beiden
    juengeren schon — die Zelle muss ihn trotzdem tragen.

    Die Reihenfolge ist nicht zufaellig: berechne_hotspots liest mit
    ORDER BY datum ASC, die aelteste Meldung war also die erste.
    """
    conn = _db(tmp_path)
    _meldung(conn, "koeln:1", "2026-01-01", "")           # aelteste, ohne
    _meldung(conn, "koeln:2", "2026-02-01", "Ehrenfeld", versatz=0.0001)
    _meldung(conn, "koeln:3", "2026-03-01", "Ehrenfeld", versatz=0.0002)
    _lauf(conn)
    ergebnis = _bezirk(conn)
    conn.close()

    assert ergebnis == "Ehrenfeld", (
        f"Die Zelle traegt '{ergebnis}' statt 'Ehrenfeld'. Der Bezirk wird "
        f"also weiterhin aus der ersten Meldung uebernommen statt als "
        f"Mehrheitswert bestimmt (Befund K-12). Auf der Karte steht dann "
        f"'Adresse unvollstaendig', obwohl der Stadtteil in der Datenbank liegt."
    )


def test_mehrheit_entscheidet_nicht_das_alter(tmp_path):
    """Gegenprobe zur naheliegenden Verwechslung: nicht 'der erste nicht-leere
    Wert' gewinnt, sondern der haeufigste — genau wie bei der Strasse."""
    conn = _db(tmp_path)
    _meldung(conn, "koeln:1", "2026-01-01", "Nippes")      # aelteste
    _meldung(conn, "koeln:2", "2026-02-01", "Ehrenfeld", versatz=0.0001)
    _meldung(conn, "koeln:3", "2026-03-01", "Ehrenfeld", versatz=0.0002)
    _lauf(conn)
    ergebnis = _bezirk(conn)
    conn.close()

    assert ergebnis == "Ehrenfeld", (
        f"Die Zelle traegt '{ergebnis}'. Erwartet war der Mehrheitswert "
        f"'Ehrenfeld' (zweimal) statt des aeltesten Werts 'Nippes' (einmal)."
    )


def test_zelle_ohne_jeden_stadtteil_bleibt_leer(tmp_path):
    """Die Grenze der Abhilfe, und sie ist Absicht. Fuehrt KEINE Meldung der
    Zelle einen Stadtteil, wird auch keiner erfunden — 'Adresse
    unvollstaendig' ist dort die ehrliche Anzeige (96 Koelner Zellen)."""
    conn = _db(tmp_path)
    _meldung(conn, "koeln:1", "2026-01-01", "")
    _meldung(conn, "koeln:2", "2026-02-01", "", versatz=0.0001)
    _lauf(conn)
    ergebnis = _bezirk(conn)
    conn.close()

    assert not ergebnis, (
        f"Die Zelle traegt '{ergebnis}', obwohl keine ihrer Meldungen einen "
        f"Stadtteil fuehrt. Es darf keiner abgeleitet werden — das waere neue "
        f"Ortsinformation und muesste durch die Risikobewertung."
    )


def test_bestehende_zelle_bekommt_den_bezirk_nachtraeglich(tmp_path):
    """Die zweite Haelfte von K-12, und die leicht zu uebersehende.

    Die 185 Koelner Zellen, um die es geht, gibt es laengst. Stuende `bezirk`
    nicht in der ON-CONFLICT-Klausel, gaelte die Mehrheitsregel nur fuer neu
    angelegte Zellen und die Aenderung waere folgenlos — dieselbe Falle wie bei
    `first_seen` unter Befund M-02.
    """
    conn = _db(tmp_path)
    # Altbestand: die Zelle steht schon, ohne Bezirk.
    conn.execute(
        "INSERT INTO hotspots (cluster_id, lat_center, lon_center, bezirk, "
        "meldungen_count, recurrence_count, last_seen, first_seen, score, "
        "score_label, strasse, plz, stadt) "
        "VALUES (?,?,?,'',3,0,'2026-03-01','2026-01-01',5.0,'mittel',"
        "'Domstr','50667','koeln')",
        (tracker.cluster_id(*KOELN), KOELN[0], KOELN[1]))
    conn.commit()

    _meldung(conn, "koeln:1", "2026-01-01", "")
    _meldung(conn, "koeln:2", "2026-02-01", "Ehrenfeld", versatz=0.0001)
    _meldung(conn, "koeln:3", "2026-03-01", "Ehrenfeld", versatz=0.0002)
    _lauf(conn)
    ergebnis = _bezirk(conn)
    conn.close()

    assert ergebnis == "Ehrenfeld", (
        f"Die bereits vorhandene Zelle traegt nach dem Lauf '{ergebnis}'. "
        f"`bezirk` fehlt also in der ON-CONFLICT-Klausel von "
        f"berechne_hotspots — die Mehrheitsregel greift dann nur fuer neu "
        f"angelegte Zellen, und genau die alten sind der Anlass gewesen."
    )
