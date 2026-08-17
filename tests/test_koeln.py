"""Koeln-Adapter (T-49, 15.08.2026).

Geprueft wird, was still schiefgehen koennte:

  * dass ein Koelner Lauf keine einzige Berliner Meldung anfasst
  * dass eine leere oder duenne Antwort einen FEHLER ausloest und nicht als
    "nichts passiert" durchgeht
  * dass eine Bildadresse nirgends in der Datenbank landet
  * dass die Seitenzahl IMMER mitgeht und nullbasiert gezaehlt wird
  * dass die Hausnummer aus der Adresse faellt, bevor sie gespeichert wird
  * dass die Kategorien-Zuordnung bis in die Anzeige durchhaelt
  * dass der Startzeitpunkt des Rueckimports aus der Frist kommt und nicht aus
    einer zweiten Konstante

Alle Beispieltexte hier sind erfunden oder aus der oeffentlichen
Kategorienliste der Stadt uebernommen; echte Buerger-Freitexte gehoeren nicht
in einen oeffentlichen Code-Speicher.
"""

import json
import sqlite3
from datetime import datetime

import pytest
import requests

import betreff_filter
import export_html
import open311
import quellen
import retention
import rueckimport
import tracker

KOELN = quellen.KOELN

BERLIN_ORT = (52.52, 13.405)


# ── Hilfsmittel ──────────────────────────────────────────────────────────────

class FalscheAntwort:
    def __init__(self, nutzlast, status=200):
        self._nutzlast = nutzlast
        self.status_code = status

    def json(self):
        if isinstance(self._nutzlast, str):
            raise ValueError("keine JSON-Antwort")
        return self._nutzlast


class FalscheSitzung:
    """Sammelt die abgesetzten Anfragen und liefert vorbereitete Seiten."""

    def __init__(self, seiten):
        # seiten: dict {seitenzahl: liste} oder liste von listen
        if isinstance(seiten, list):
            seiten = {i: s for i, s in enumerate(seiten)}
        self.seiten = seiten
        self.anfragen = []

    def get(self, url, params=None, headers=None, timeout=None):
        self.anfragen.append(dict(params or {}))
        seite = (params or {}).get("page")
        return FalscheAntwort(self.seiten.get(seite, []))


def koelner_meldung(nummer, code="1.1", name="Wilder Müll",
                    datum="2026-08-10T09:00:00+02:00",
                    adresse="50823 Köln - Ehrenfeld, Subbelrather Str. 167",
                    beschreibung="Freitext mit Herr Mustermann und Nr. 12",
                    bild="https://sags-uns.stadt-koeln.de/system/files/2026-07/IMG_1.jpeg",
                    lat=50.9520, lon=6.9248, status="closed"):
    return {
        "service_request_id": f"{nummer}-2026",
        "title": f"#{nummer}-2026 {name}",
        "description": beschreibung,
        "lat": lat,
        "long": lon,
        "address_string": adresse,
        "service_name": name,
        "requested_datetime": datum,
        "updated_datetime": datum,
        "status": status,
        "media_url": bild,
        "status_note": "",
        "service_code": code,
    }


def berliner_bestand(conn, anzahl=5):
    """Ein kleiner Berliner Bestand, der unangetastet bleiben muss."""
    for i in range(anzahl):
        conn.execute(
            "INSERT INTO meldungen (id, fetched_at, datum, kategorie, betreff, "
            "bezirk, lat, lon, status, is_muell, strasse, plz, stadt) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (f"berlin:{i}", "2026-04-14T00:00:00", "2026-04-14", "",
             "Abfall - Sperrmüll", "Mitte", BERLIN_ORT[0] + i * 0.0001,
             BERLIN_ORT[1], "Erledigt", 1, "Teststr", "10115", "berlin"))
    conn.commit()


@pytest.fixture
def db(tmp_path, monkeypatch):
    pfad = tmp_path / "koeln.db"
    monkeypatch.setattr(tracker, "DB_PATH", pfad)
    monkeypatch.setattr(rueckimport, "DB_PATH", pfad)
    monkeypatch.setattr(export_html, "DB_PATH", pfad)
    conn = sqlite3.connect(pfad)
    tracker.init_db(conn)
    conn.close()
    return pfad


# ── Auflage 3c: eine leere Antwort ist ein Fehler ────────────────────────────

def test_leere_antwort_gilt_nicht_als_keine_meldungen():
    """Der Kern der Auflage. Ein Zeitraum in der Vergangenheit, der nichts
    zurueckgibt, ist ein Ausfall — sonst importiert der erste Rueckimport-Lauf
    scheinbar erfolgreich null Meldungen."""
    zeitraum = open311.Zeitraum(von=datetime(2025, 5, 1), bis=datetime(2025, 6, 1))
    with pytest.raises(open311.AbrufUnplausibel) as fehler:
        open311.pruefe_plausibel([], zeitraum, mindest_je_tag=5.0,
                                 jetzt=datetime(2026, 8, 15))
    assert "0 Meldungen" in str(fehler.value)


def test_duenne_antwort_gilt_ebenfalls_als_ausfall():
    """Nicht nur die leere Antwort. Ein Monat mit drei Meldungen ist bei einer
    Quelle mit 93 Meldungen je Tag genauso wenig ein Befund."""
    zeitraum = open311.Zeitraum(von=datetime(2025, 5, 1), bis=datetime(2025, 6, 1))
    with pytest.raises(open311.AbrufUnplausibel):
        open311.pruefe_plausibel([{}, {}, {}], zeitraum, mindest_je_tag=5.0,
                                 jetzt=datetime(2026, 8, 15))


def test_angebrochener_tag_darf_duenn_sein():
    """Die Pruefung soll einen Ausfall erkennen, nicht einen ruhigen Vormittag
    beanstanden. Ein Zeitraum, der bis in die Zukunft reicht, wird milder
    bewertet."""
    zeitraum = open311.Zeitraum(von=datetime(2026, 8, 15, 6),
                                bis=datetime(2026, 8, 16))
    open311.pruefe_plausibel([{}], zeitraum, mindest_je_tag=5.0,
                             jetzt=datetime(2026, 8, 15, 12))


# ── K-05: der Abrufweg muss die Pruefung auch AUFRUFEN ───────────────────────
#
# Die drei Tests darueber pruefen pruefe_plausibel() unmittelbar. Sie bleiben
# alle gruen, wenn man den Aufruf am Ende von hole_zeitraum ersatzlos streicht —
# genau das hat die Mutationsprobe der Abnahme vom 15.08.2026 als M6 gefunden
# (Befund K-05). Eine Sicherung, deren Wegfall kein Test bemerkt, ist keine
# Sicherung. Es ist dieselbe Bauart wie der Fehler aus T-39: eine Ersetzung, die
# ins Leere lief, weil das Ergebnis wie ein geglueckter Lauf aussah.
#
# Deshalb wird hier nicht die Pruefung geprueft, sondern der WEG durch sie
# hindurch — mit einer kuenstlichen Quelle, ohne Netz.

def test_der_abrufweg_ruft_die_plausibilitaetspruefung_auch_auf():
    """Der gefaehrliche Fall ist der duenne, nicht der leere.

    Ein Rueckimport-Monat mit fuenf statt 2.800 Meldungen wuerde ohne diese
    Pruefung stumm als vollstaendiger Monat verbucht. Der voellig leere Fall
    fiele auch sonst noch auf, weil tracker.run bei 0 Meldungen abbricht.
    """
    zeitraum = open311.Zeitraum(von=datetime(2025, 5, 1), bis=datetime(2025, 5, 31))
    sitzung = FalscheSitzung([[koelner_meldung(i) for i in range(5)]])

    with pytest.raises(open311.AbrufUnplausibel) as fehler:
        open311.hole_zeitraum("https://beispiel.invalid/requests.json", zeitraum,
                              mindest_je_tag=5.0, sitzung=sitzung, pause=0,
                              jetzt=datetime(2026, 8, 15))

    assert "5 Meldungen erhalten" in str(fehler.value), (
        "hole_zeitraum hat 30 Tage mit fuenf Meldungen zurueckgegeben, statt "
        "sie als Ausfall zu behandeln. Der Aufruf von pruefe_plausibel am Ende "
        "der Funktion fehlt oder wirkt nicht (Befund K-05)."
    )


def test_der_abrufweg_faengt_auch_den_voellig_leeren_zeitraum():
    """Dieselbe Zeile, der andere Fall: eine Quelle, die gar nichts liefert."""
    zeitraum = open311.Zeitraum(von=datetime(2025, 5, 1), bis=datetime(2025, 5, 31))
    sitzung = FalscheSitzung([[]])

    with pytest.raises(open311.AbrufUnplausibel):
        open311.hole_zeitraum("https://beispiel.invalid/requests.json", zeitraum,
                              mindest_je_tag=5.0, sitzung=sitzung, pause=0,
                              jetzt=datetime(2026, 8, 15))


def test_ein_vollstaendiger_zeitraum_laeuft_weiterhin_durch():
    """Gegenprobe, damit der Test oben nicht nur 'wirft immer' beweist.

    Zwei volle Seiten und eine angebrochene: 250 Meldungen auf 30 Tage liegen
    ueber der Untergrenze von 150 und muessen ohne Beanstandung durchgehen.
    """
    zeitraum = open311.Zeitraum(von=datetime(2025, 5, 1), bis=datetime(2025, 5, 31))
    seiten = [[koelner_meldung(i) for i in range(0, 100)],
              [koelner_meldung(i) for i in range(100, 200)],
              [koelner_meldung(i) for i in range(200, 250)]]
    sitzung = FalscheSitzung(seiten)

    alle = open311.hole_zeitraum("https://beispiel.invalid/requests.json", zeitraum,
                                 mindest_je_tag=5.0, sitzung=sitzung, pause=0,
                                 jetzt=datetime(2026, 8, 15))
    assert len(alle) == 250


def test_die_koelner_quelle_reicht_ihre_untergrenze_an_den_abruf_durch():
    """Die Verdrahtung dahinter: der Wert steht in quellen.py, geprueft wird in
    open311.py. Faellt der Weg dazwischen auseinander, laeuft die Pruefung mit
    einer Untergrenze von 0 und beanstandet nie wieder etwas."""
    zeitraum = open311.Zeitraum(von=datetime(2025, 5, 1), bis=datetime(2025, 5, 31))
    sitzung = FalscheSitzung([[koelner_meldung(i) for i in range(5)]])

    assert KOELN.mindest_meldungen_je_tag > 0
    with pytest.raises(open311.AbrufUnplausibel):
        KOELN.hole_meldungen(zeitraum, sitzung=sitzung, pause=0,
                             jetzt=datetime(2026, 8, 15))


def test_unplausibler_abruf_beendet_den_lauf_wie_ein_ausfall(db, monkeypatch):
    """Im Tracker darf daraus kein halber Import werden: der Lauf endet mit
    Rueckgabewert 1 und einer Fehlermarke im Abrufprotokoll."""
    def wirft(*a, **k):
        raise open311.AbrufUnplausibel("Testfall")
    monkeypatch.setattr(KOELN, "hole_meldungen", wirft)

    assert tracker.run("koeln") == 1

    conn = sqlite3.connect(db)
    zeile = conn.execute(
        "SELECT count_total, stadt FROM fetch_log ORDER BY id DESC LIMIT 1").fetchone()
    conn.close()
    assert zeile == (-1, "koeln"), zeile


# ── Die Seitenzahl ───────────────────────────────────────────────────────────

def test_seitenzahl_geht_immer_mit_und_beginnt_bei_null():
    """Gemessen am 15.08.2026: page ist NULLBASIERT. Ein Abruf ohne page
    liefert stumm die erste Seite mit 100 Meldungen, nicht alles und auch keine
    Fehlermeldung. Wer nicht blaettert, importiert 100 von 531 und haelt das
    fuer den ganzen Zeitraum."""
    seite0 = [koelner_meldung(i) for i in range(open311.SEITENGROESSE)]
    seite1 = [koelner_meldung(1000 + i) for i in range(20)]
    sitzung = FalscheSitzung([seite0, seite1])

    zeitraum = open311.Zeitraum(von=datetime(2026, 8, 1), bis=datetime(2026, 8, 8))
    alle = open311.hole_zeitraum("http://test", zeitraum, mindest_je_tag=5.0,
                                 pause=0, sitzung=sitzung,
                                 jetzt=datetime(2026, 8, 15))

    assert len(alle) == 120
    assert [a["page"] for a in sitzung.anfragen] == [0, 1]
    assert all("page" in a for a in sitzung.anfragen)


def test_schnittstelle_die_nicht_blaettert_laeuft_nicht_endlos():
    """Liefert die Quelle auf jeder Seite dasselbe, wird abgebrochen statt
    Dubletten aufzuhaeufen."""
    immer_gleich = [koelner_meldung(i) for i in range(open311.SEITENGROESSE)]
    sitzung = FalscheSitzung({i: immer_gleich for i in range(50)})

    zeitraum = open311.Zeitraum(von=datetime(2026, 8, 1), bis=datetime(2026, 8, 8))
    alle = open311.hole_zeitraum("http://test", zeitraum, mindest_je_tag=5.0,
                                 pause=0, sitzung=sitzung,
                                 jetzt=datetime(2026, 8, 15))
    assert len(alle) == open311.SEITENGROESSE
    assert len(sitzung.anfragen) == 2


# ── Unlesbare Seiten in der Quelle ───────────────────────────────────────────

class SitzungMitKaputterSeite(FalscheSitzung):
    """Bildet die gemessene Eigenheit nach: eine Seite antwortet dauerhaft mit
    HTTP 500, die Nachbarseiten liefern normal."""

    def __init__(self, seiten, kaputt):
        super().__init__(seiten)
        self.kaputt = kaputt

    def get(self, url, params=None, headers=None, timeout=None):
        self.anfragen.append(dict(params or {}))
        seite = (params or {}).get("page")
        if seite == self.kaputt:
            return FalscheAntwort([], status=500)
        return FalscheAntwort(self.seiten.get(seite, []))


def _seiten(anzahl_voll, rest):
    seiten = {i: [koelner_meldung(i * 100 + j) for j in range(open311.SEITENGROESSE)]
              for i in range(anzahl_voll)}
    seiten[anzahl_voll] = [koelner_meldung(9000 + j) for j in range(rest)]
    return seiten


def test_unlesbare_seite_bricht_den_taeglichen_lauf_ab():
    """Vorgabe ohne ``luecken``: eine unlesbare Seite ist ein Grund, nichts zu
    tun — nicht ein Grund, den Rest fuer den ganzen Zeitraum zu halten."""
    sitzung = SitzungMitKaputterSeite(_seiten(4, 20), kaputt=2)
    zeitraum = open311.Zeitraum(von=datetime(2024, 9, 14), bis=datetime(2024, 10, 14))
    with pytest.raises(open311.AbrufFehler):
        open311.hole_zeitraum("http://test", zeitraum, mindest_je_tag=5.0,
                              pause=0, sitzung=sitzung, pause_bei_fehler=0,
                              jetzt=datetime(2026, 8, 15))


def test_ein_voruebergehender_fehler_wird_wiederholt_und_ist_keine_luecke():
    """Am 15.08.2026 aus Schaden gelernt: beim ersten echten Rueckimport lief
    parallel eine zweite Sitzung gegen dieselbe Schnittstelle, der Server liess
    eine Verbindung fallen — und der Adapter vermerkte das als DAUERHAFTE
    Luecke in der Quelle. Ein Schluckauf des Netzes darf nicht dasselbe
    bedeuten wie ein kaputter Datensatz."""
    class EinmalKaputt(FalscheSitzung):
        def __init__(self, seiten):
            super().__init__(seiten)
            self.schon_gescheitert = False

        def get(self, url, params=None, headers=None, timeout=None):
            self.anfragen.append(dict(params or {}))
            seite = (params or {}).get("page")
            if seite == 1 and not self.schon_gescheitert:
                self.schon_gescheitert = True
                raise requests.ConnectionError("Remote end closed connection")
            return FalscheAntwort(self.seiten.get(seite, []))

    sitzung = EinmalKaputt(_seiten(2, 20))
    zeitraum = open311.Zeitraum(von=datetime(2025, 8, 10), bis=datetime(2025, 9, 9))
    luecken = []
    alle = open311.hole_zeitraum("http://test", zeitraum, mindest_je_tag=5.0,
                                 pause=0, pause_bei_fehler=0, sitzung=sitzung,
                                 luecken=luecken, jetzt=datetime(2026, 8, 15))

    assert luecken == [], "Ein wiederholbarer Fehler darf keine Luecke sein"
    assert len(alle) == 2 * open311.SEITENGROESSE + 20
    assert [a["page"] for a in sitzung.anfragen] == [0, 1, 1, 2]


def test_eine_abgelehnte_anfrage_wird_nicht_wiederholt():
    """4xx wird durch Wiederholen nicht richtiger."""
    class Abgelehnt(FalscheSitzung):
        def get(self, url, params=None, headers=None, timeout=None):
            self.anfragen.append(dict(params or {}))
            return FalscheAntwort([], status=404)

    sitzung = Abgelehnt([])
    with pytest.raises(open311.AbrufFehler):
        open311.hole_seite("http://test", None, 0, sitzung=sitzung,
                           pause_bei_fehler=0)
    assert len(sitzung.anfragen) == 1


def test_unlesbare_seite_wird_im_rueckimport_zur_vermerkten_luecke():
    """Gemessen am 15.08.2026: Seite 11 von 14.09. bis 14.10.2024 antwortet bei
    jedem Versuch mit HTTP 500, Seite 10 und 12 nicht. Der Rueckimport soll
    deswegen nicht zwei Jahre Tiefe liegen lassen — aber die Luecke muss
    sichtbar bleiben."""
    sitzung = SitzungMitKaputterSeite(_seiten(4, 20), kaputt=2)
    zeitraum = open311.Zeitraum(von=datetime(2024, 9, 14), bis=datetime(2024, 10, 14))
    luecken = []
    alle = open311.hole_zeitraum("http://test", zeitraum, mindest_je_tag=5.0,
                                 pause=0, pause_bei_fehler=0, sitzung=sitzung,
                                 luecken=luecken, jetzt=datetime(2026, 8, 15))

    assert len(luecken) == 1
    assert luecken[0]["seite"] == 2
    # Seite 0, 1, 3 voll plus die angebrochene Seite 4 — Seite 2 fehlt.
    assert len(alle) == 3 * open311.SEITENGROESSE + 20
    # Seite 2 wird dreimal versucht, bevor sie als Luecke gilt.
    assert [a["page"] for a in sitzung.anfragen] == [0, 1, 2, 2, 2, 3, 4]


def test_viele_unlesbare_seiten_am_stueck_sind_ein_abbruch():
    """Eine kaputte Seite ist eine Luecke. Fuenf am Stueck heisst, dass die
    Quelle weg ist — dann wird abgebrochen, auch mit Luecken-Liste."""
    class ImmerKaputt(FalscheSitzung):
        def get(self, url, params=None, headers=None, timeout=None):
            self.anfragen.append(dict(params or {}))
            return FalscheAntwort([], status=500)

    zeitraum = open311.Zeitraum(von=datetime(2024, 9, 1), bis=datetime(2024, 10, 1))
    with pytest.raises(open311.AbrufFehler):
        open311.hole_zeitraum("http://test", zeitraum, mindest_je_tag=5.0,
                              pause=0, pause_bei_fehler=0,
                              sitzung=ImmerKaputt([]), luecken=[],
                              jetzt=datetime(2026, 8, 15))


# ── A-17: keine Bildadressen ─────────────────────────────────────────────────

def test_media_url_landet_in_keiner_spalte(db, monkeypatch):
    """A-17. In der Stichprobe trugen 73,6 Prozent der Meldungen eine
    Bildadresse. Sie wird nicht uebernommen — und auch nicht abgerufen."""
    meldungen = [koelner_meldung(100 + i) for i in range(4)]
    monkeypatch.setattr(KOELN, "hole_meldungen", lambda *a, **k: meldungen)

    assert tracker.run("koeln") == 0

    conn = sqlite3.connect(db)
    zeilen = conn.execute("SELECT * FROM meldungen WHERE stadt='koeln'").fetchall()
    conn.close()
    assert zeilen
    roh = json.dumps(zeilen, ensure_ascii=False, default=str)
    for verraeter in ("media_url", "sags-uns", ".jpeg", ".jpg", "http"):
        assert verraeter not in roh, f"{verraeter!r} steht in der Datenbank: {roh[:400]}"


def test_aufbereiten_liest_das_bildfeld_gar_nicht():
    """Nicht nur 'wird nicht gespeichert', sondern 'wird nicht angefasst'. Eine
    Meldung ohne das Feld muss genauso durchlaufen wie eine mit."""
    ohne_feld = koelner_meldung(1)
    del ohne_feld["media_url"]
    satz = KOELN.aufbereiten(ohne_feld, "2026-08-15T00:00:00")
    assert satz is not None and satz["id"] == "koeln:1-2026"


# T-69 (Befund K-13, 15.08.2026): Die beiden Tests darueber laufen ausschliesslich
# mit Meldungen, die eine Adresse tragen — koelner_meldung() liefert immer eine.
# Damit blieb eine schmalere Bauart des Fehlers ungesehen: eine Ersetzung wie
# 'strasse': strasse or media_url schreibt die Bildadresse NUR dann ins
# Strassenfeld, wenn die Stadt keine Adresse mitliefert, und liess alle 292
# Tests gruen. Der Fall ist selten und real: in der Stichprobe vom 15.08.2026
# trugen 3.998 von 4.000 Koelner Meldungen eine Hausnummer, zwei also keine
# verwertbare Adresse. Genau die zwei Zeilen wuerde ein solcher Fehler treffen.

@pytest.mark.parametrize("adresse", ["", None, "   "])
def test_media_url_bleibt_auch_ohne_adresse_draussen(db, monkeypatch, adresse):
    """A-17 fuer die Meldung ohne Adresse. Ein leeres Strassenfeld ist die
    einzige Stelle, an der eine Bildadresse noch Platz haette."""
    meldungen = [koelner_meldung(300 + i, adresse=adresse) for i in range(3)]
    monkeypatch.setattr(KOELN, "hole_meldungen", lambda *a, **k: meldungen)

    assert tracker.run("koeln") == 0

    conn = sqlite3.connect(db)
    zeilen = conn.execute("SELECT * FROM meldungen WHERE stadt='koeln'").fetchall()
    conn.close()
    assert zeilen, "Ohne Adresse wurde gar nichts geschrieben"
    roh = json.dumps(zeilen, ensure_ascii=False, default=str)
    for verraeter in ("media_url", "sags-uns", ".jpeg", ".jpg", "http"):
        assert verraeter not in roh, (
            f"{verraeter!r} steht in der Datenbank, sobald die Meldung keine "
            f"Adresse traegt (Befund K-13): {roh[:400]}")


def test_ohne_adresse_bleiben_die_ortsfelder_leer():
    """Die Gegenprobe zum Test darueber, eine Ebene frueher und schaerfer:
    nicht nur 'keine Bildadresse', sondern ueberhaupt nichts Erfundenes. Ein
    Ersatzwert an dieser Stelle waere genauso falsch wie die Bildadresse."""
    satz = KOELN.aufbereiten(koelner_meldung(1, adresse=""),
                             "2026-08-15T00:00:00")
    assert satz is not None
    assert satz["strasse"] == "", f"strasse traegt {satz['strasse']!r}"
    assert satz["plz"] == "", f"plz traegt {satz['plz']!r}"
    assert satz["bezirk"] == "", f"bezirk traegt {satz['bezirk']!r}"


# ── K-06: die zweite Haelfte von A-17 — Bilder werden nicht ABGERUFEN ────────
#
# Auflage A-17 hat zwei Haelften: Bildadressen nicht uebernehmen UND Bilder
# nicht abrufen. Die erste ist durch die beiden Tests darueber gedeckt. Die
# zweite war es nicht: die Mutationsprobe der Abnahme vom 15.08.2026 hat als M9d
# eine Zeile eingebaut, die das Bild tatsaechlich herunterlaedt — alle Tests
# blieben gruen, der Lauf wurde lediglich von 12 auf 250 Sekunden langsamer.
#
# Der Unterschied ist kein formaler. Eine nicht gespeicherte Bildadresse ist ein
# Datum, das wir nicht haben; ein abgerufenes Bild ist eine Uebermittlung an
# unseren Rechner und damit eine Verarbeitung, die in keinem Verzeichnis steht.
# Bilder aus dem oeffentlichen Raum zeigen Gesichter, Kennzeichen und Fassaden.
#
# Geprueft wird deshalb nicht der Quelltext, sondern das Verhalten: waehrend der
# Aufbereitung darf ueberhaupt keine Verbindung nach draussen aufgebaut werden.

class NetzverbotVerletzt(BaseException):
    """Absichtlich von BaseException abgeleitet.

    Ein 'except Exception' im Produktivcode soll die Falle nicht verschlucken
    koennen — sonst waere ein Bild-Abruf in einem try-Block wieder unsichtbar.
    """


class Netzsperre:
    """Vermerkt jeden Versuch, waehrend des Tests ins Netz zu gehen.

    Drei Ebenen, damit die Sperre nicht an der Bauart des Abrufs vorbeigeht:
    die requests-Ebene faengt requests.get und jede Sitzung, urlopen faengt den
    Weg an requests vorbei, und socket.connect ist der Engpass, durch den beide
    am Ende ohnehin muessen. Die Aufbereitung braucht von sich aus keine einzige
    Verbindung — jeder Eintrag hier ist also ein Befund.
    """

    def __init__(self):
        self.versuche: list[str] = []

    def scharfschalten(self, monkeypatch):
        import socket
        import urllib.request
        import requests.adapters

        def falle(ebene, ziel_aus):
            def _falle(*args, **kwargs):
                ziel = ziel_aus(*args, **kwargs)
                self.versuche.append(f"{ebene}: {ziel}")
                raise NetzverbotVerletzt(f"{ebene}: {ziel}")
            return _falle

        monkeypatch.setattr(
            requests.adapters.HTTPAdapter, "send",
            falle("requests", lambda selbst, anfrage, *a, **k: anfrage.url))
        monkeypatch.setattr(
            urllib.request, "urlopen",
            falle("urllib", lambda ziel, *a, **k: getattr(ziel, "full_url", ziel)))
        monkeypatch.setattr(
            socket.socket, "connect",
            falle("socket", lambda selbst, adresse, *a, **k: adresse))
        return self


def test_die_aufbereitung_ruft_kein_bild_ab(monkeypatch):
    """A-17, zweite Haelfte. Vier Meldungen mit Bildadresse durch die
    Aufbereitung — dabei darf keine einzige Verbindung entstehen."""
    sperre = Netzsperre().scharfschalten(monkeypatch)

    try:
        for i in range(4):
            satz = KOELN.aufbereiten(koelner_meldung(100 + i),
                                     "2026-08-15T00:00:00")
            assert satz is not None
    except NetzverbotVerletzt:
        pass  # die Meldung steht in sperre.versuche und kommt unten heraus

    assert sperre.versuche == [], (
        f"Die Aufbereitung einer Koelner Meldung hat eine Verbindung nach "
        f"draussen aufgebaut: {sperre.versuche}. Auflage A-17 verbietet nicht "
        f"nur, die Bildadresse zu speichern, sondern auch, das Bild abzurufen "
        f"(Befund K-06)."
    )


def test_ein_ganzer_koelner_lauf_ruft_kein_bild_ab(db, monkeypatch):
    """Breitere Fassung: der vollstaendige Weg vom Abruf bis in die Datenbank.

    Der Abruf selbst ist ersetzt, es bleibt also kein zulaessiger Grund fuer
    irgendeine Verbindung uebrig. Der Lauf muss trotzdem sauber durchgehen und
    die Meldungen schreiben — sonst beweist der Test nur, dass er abgebrochen
    ist, bevor er zum Bild kam.
    """
    meldungen = [koelner_meldung(200 + i) for i in range(4)]
    monkeypatch.setattr(KOELN, "hole_meldungen", lambda *a, **k: meldungen)
    sperre = Netzsperre().scharfschalten(monkeypatch)

    ergebnis = None
    try:
        ergebnis = tracker.run("koeln")
    except NetzverbotVerletzt:
        pass

    assert sperre.versuche == [], (
        f"Ein Koelner Lauf hat trotz ersetztem Abruf eine Verbindung nach "
        f"draussen aufgebaut: {sperre.versuche}. Erwartet wird keine einzige "
        f"(Befund K-06, Auflage A-17)."
    )
    assert ergebnis == 0

    conn = sqlite3.connect(db)
    anzahl = conn.execute(
        "SELECT COUNT(*) FROM meldungen WHERE stadt='koeln'").fetchone()[0]
    conn.close()
    assert anzahl == 4, (
        "Der Lauf hat nichts geschrieben — dann sagt der Test oben nichts "
        "darueber aus, ob ein Bild abgerufen worden waere."
    )


def test_die_netzsperre_wuerde_einen_bild_abruf_auch_merken(monkeypatch):
    """Gegenprobe zur Sperre selbst. Ein Werkzeug, das nie anschlaegt, ist von
    einem kaputten Werkzeug nicht zu unterscheiden — hier wird der verbotene
    Abruf absichtlich ausgeloest und muss vermerkt werden."""
    sperre = Netzsperre().scharfschalten(monkeypatch)
    bild = koelner_meldung(1)["media_url"]

    for abruf in (lambda: requests.get(bild, timeout=5),
                  lambda: requests.Session().get(bild, timeout=5)):
        sperre.versuche.clear()
        try:
            abruf()
        except BaseException:
            pass
        assert sperre.versuche, "Die Netzsperre hat den Abruf nicht bemerkt."
        assert bild in sperre.versuche[0]


# ── A-18: Freitext ───────────────────────────────────────────────────────────

def test_freitext_wird_fuer_koeln_nicht_uebernommen():
    """Gemessen: 3.592 verschiedene Freitexte in 3.705 Meldungen aus sechs
    Wochen. Der Berliner Weg (jeden verschiedenen Wert einmal durchsehen) traegt
    das nicht. Da die Stadt fuer 100 Prozent der Meldungen eine Kategorie
    mitliefert, ist der Freitext entbehrlich — genau die Ausnahme, die A-18
    vorsieht."""
    m = koelner_meldung(1, beschreibung="Sehr geehrte Damen und Herren, Herr "
                                        "Mustermann in der Beispielstr. 12 hat "
                                        "wieder Sperrmüll abgestellt")
    satz = KOELN.aufbereiten(m, "2026-08-15T00:00:00")
    assert satz["betreff"] == "Wilder Müll"
    assert "Mustermann" not in satz["betreff"]
    assert "Beispielstr" not in satz["betreff"]


def test_koelner_wortliste_greift_wenn_der_freitext_doch_kaeme():
    """Der Filter ist die zweite Reihe, nicht die erste — aber er ist da.
    Wuerde der Freitext je uebernommen, faellt er bei einem Treffer ganz."""
    text = "Wohnmobil mit Kennzeichen K-HS 1011 steht seit Monaten"
    neu, regeln = betreff_filter.entschaerfe(text, "", stadt="koeln")
    assert "kennzeichen" in regeln
    assert "K-HS" not in neu


def test_koelner_zusatzregeln_gelten_nicht_fuer_berlin():
    """Der Berliner Satz bleibt woertlich, wie er ist. Jede Aenderung daran
    wuerde beim naechsten Lauf 82.780 Zeilen erneut anfassen."""
    text = "Wohnmobil mit Kennzeichen K-HS 1011 steht seit Monaten"
    _, regeln = betreff_filter.entschaerfe(text, "", stadt="berlin")
    assert "kennzeichen" not in regeln


def test_hausnummer_in_worten_faellt_nur_in_koeln():
    """'Hs Nr. 36' kommt in Koelner Texten vor (112 von 3.705), in Berliner
    nicht. Die Regel gilt deshalb nur dort."""
    text = "Gehweg vor der Hs Nr. 36 ist defekt"
    koeln, _ = betreff_filter.entschaerfe(text, "", stadt="koeln")
    berlin, _ = betreff_filter.entschaerfe(text, "", stadt="berlin")
    assert "36" not in koeln
    assert "36" in berlin


# ── Adresse und Hausnummer ───────────────────────────────────────────────────

@pytest.mark.parametrize("eingabe,erwartet", [
    ("50823 Köln - Ehrenfeld, Subbelrather Str. 167",
     ("Subbelrather Str.", "50823", "Ehrenfeld")),
    ("50825 Köln - Ehrenfeld, Venloer Str. 354e",
     ("Venloer Str.", "50825", "Ehrenfeld")),
    ("50739 Köln, Wilensteinweg 13", ("Wilensteinweg", "50739", "")),
    ("51061 Köln - Stammheim, Am Oberhof 20", ("Am Oberhof", "51061", "Stammheim")),
    ("", ("", "", "")),
    # Im echten Bestand aufgefallen: sechs Koelner Adressen tragen gar keinen
    # Strassennamen, sondern nur die Hausnummer. Uebrig bliebe sonst eine
    # "Strasse" namens "4" — also genau eine Hausnummer in dem Feld, aus dem
    # sie im Mai 2026 muehsam entfernt worden sind.
    ("50969 Köln - Zollstock, 4", ("", "50969", "Zollstock")),
    ("50969 Köln - Zollstock, 1f", ("", "50969", "Zollstock")),
    # Eine Ziffer IM Strassennamen bleibt selbstverstaendlich stehen.
    ("50969 Köln - Zollstock, Straße des 17. Juni 4",
     ("Straße des 17. Juni", "50969", "Zollstock")),
])
def test_adresse_wird_zerlegt_und_die_hausnummer_faellt(eingabe, erwartet):
    """Im Mai 2026 sind 105.100 Hausnummern aus dem Berliner Bestand entfernt
    worden, weil sie einmal drin waren. Hier fallen sie vor dem Schreiben."""
    assert open311.zerlege_adresse(eingabe) == erwartet


def test_keine_ziffer_im_strassenfeld(db, monkeypatch):
    adressen = [
        "50823 Köln - Ehrenfeld, Subbelrather Str. 167",
        "50825 Köln - Ehrenfeld, Venloer Str. 354e",
        "51061 Köln - Stammheim, Am Oberhof 20",
    ]
    meldungen = [koelner_meldung(200 + i, adresse=a, lat=50.95 + i * 0.01)
                 for i, a in enumerate(adressen)]
    monkeypatch.setattr(KOELN, "hole_meldungen", lambda *a, **k: meldungen)
    tracker.run("koeln")

    conn = sqlite3.connect(db)
    strassen = [r[0] for r in conn.execute(
        "SELECT strasse FROM meldungen WHERE stadt='koeln'")]
    conn.close()
    assert strassen
    assert not any(any(z.isdigit() for z in s) for s in strassen), strassen


# ── Kategorien ───────────────────────────────────────────────────────────────

def test_jede_muellnahe_kategorie_kommt_in_der_anzeige_wieder_heraus():
    """Die Gruppe wird beim Schreiben aus dem Kategorie-Code bestimmt, die
    Anzeige leitet sie zur Renderzeit erneut aus kategorie+betreff ab. Beide
    muessen dasselbe sagen, sonst zeigt die Karte eine andere Gruppe als die
    Datenbank kennt."""
    for code, (name, gruppe) in quellen.KOELN_KATEGORIEN.items():
        m = koelner_meldung(1, code=code, name=name)
        satz = KOELN.aufbereiten(m, "2026-08-15T00:00:00")
        assert satz is not None, f"{code} muss muellnah sein"
        erneut = export_html.kategorisiere(
            f"{satz['kategorie']} {satz['betreff']}")
        assert erneut == gruppe, (
            f"{code} ({name}): geschrieben {gruppe!r}, Anzeige leitet "
            f"{erneut!r} ab")


def test_schrottfahrraeder_haben_eine_eigene_gruppe():
    """Lars-Entscheidung 15.08.2026: statt unzugeordnet oder falsch einsortiert
    eine siebte Gruppe. Schrott-KFZ meint Fahrzeuge mit Kennzeichen, Sperrmuell
    den umgangenen Abholtermin — beides passt nicht."""
    m = koelner_meldung(1, code="1.5", name="Schrottfahrräder")
    satz = KOELN.aufbereiten(m, "2026-08-15T00:00:00")
    assert satz is not None
    assert satz["kategorie"] == "schrottfahrrad"
    assert export_html.kategorisiere(
        f"{satz['kategorie']} {satz['betreff']}") == "schrottfahrrad"


def test_die_neue_gruppe_verschiebt_keine_bestehende_zuordnung():
    """Sie steht am Ende der Liste. Texte, die schon eine Gruppe hatten,
    behalten sie — sonst waeren mit einem Schlag Berliner Zellen anders
    eingefaerbt, ohne dass jemand die Wirkung gemessen haette."""
    assert export_html.kategorisiere("Abfall - Sperrmüll und Schrottfahrrad") == "sperrmüll"
    assert export_html.kategorisiere("Abfall - Müllablagerung und Schrottfahrrad") == "illegal"
    # Was vorher gar keine Gruppe hatte, bekommt jetzt eine.
    assert export_html.kategorisiere("Abfall - Schrottfahrräder") == "schrottfahrrad"
    assert export_html.kategorisiere("Abfall - Fahrradskelett") == "schrottfahrrad"


def test_jeder_gruppenschluessel_ist_sein_eigenes_schluesselwort():
    """Der Adapter schreibt den Gruppennamen in die Spalte kategorie und die
    Anzeige leitet ihn daraus wieder ab. Das haelt nur, solange jeder
    Gruppenname unter seinen eigenen Schluesselwoertern steht."""
    for schluessel in export_html.KATEGORIE_GRUPPEN:
        assert export_html.kategorisiere(schluessel) == schluessel, schluessel


def test_volle_container_sind_kein_ablagerungsort():
    """Ein voller, aber regulaerer Container ist ortsfest. Er wuerde als
    dauerhafter Scheinschwerpunkt in der Karte stehen."""
    for code in ("1.3.1", "1.4.1", "1.2", "1.6", "3.2"):
        m = koelner_meldung(1, code=code, name="egal")
        assert KOELN.aufbereiten(m, "2026-08-15T00:00:00") is None, code
        assert code in quellen.KOELN_BEWUSST_AUSSEN, (
            f"{code} wird ausgeschlossen, aber die Begruendung fehlt")


# ── Trennung der Staedte ─────────────────────────────────────────────────────

def test_koelner_lauf_fasst_keine_berliner_meldung_an(db, monkeypatch):
    """Die zentrale Zusicherung des Auftrags."""
    conn = sqlite3.connect(db)
    berliner_bestand(conn, anzahl=5)
    vorher = conn.execute(
        "SELECT id, datum, betreff, quelle_weg_seit FROM meldungen "
        "WHERE stadt='berlin' ORDER BY id").fetchall()
    conn.close()

    meldungen = [koelner_meldung(300 + i, lat=50.95 + i * 0.01) for i in range(3)]
    monkeypatch.setattr(KOELN, "hole_meldungen", lambda *a, **k: meldungen)
    assert tracker.run("koeln") == 0

    conn = sqlite3.connect(db)
    nachher = conn.execute(
        "SELECT id, datum, betreff, quelle_weg_seit FROM meldungen "
        "WHERE stadt='berlin' ORDER BY id").fetchall()
    koelner = conn.execute(
        "SELECT COUNT(*) FROM meldungen WHERE stadt='koeln'").fetchone()[0]
    conn.close()

    assert nachher == vorher, "Der Berliner Bestand hat sich veraendert"
    assert koelner == 3


def test_koelner_lauf_merkt_keine_berliner_meldung_als_weggefallen_vor(db, monkeypatch):
    """Der Abruf einer Open311-Quelle ist ein ZEITRAUM, nicht der Bestand. Wer
    darin fehlt, ist nicht geloescht, sondern aelter. Liefe der Abgleich, waere
    nach dem ersten Lauf alles Uebrige vorgemerkt."""
    conn = sqlite3.connect(db)
    berliner_bestand(conn, anzahl=5)
    conn.close()

    # Genug Meldungen, dass die Vollstaendigkeitsschwelle kein Zufallsschutz ist
    meldungen = [koelner_meldung(400 + i, lat=50.95 + i * 0.0001)
                 for i in range(1500)]
    monkeypatch.setattr(KOELN, "hole_meldungen", lambda *a, **k: meldungen)
    tracker.run("koeln")

    conn = sqlite3.connect(db)
    vorgemerkt = conn.execute(
        "SELECT COUNT(*) FROM meldungen WHERE quelle_weg_seit IS NOT NULL"
    ).fetchone()[0]
    conn.close()
    assert vorgemerkt == 0, (
        f"{vorgemerkt} Meldungen sind als weggefallen vorgemerkt. Bei einer "
        f"Quelle, die einen Zeitraum liefert, darf das nie passieren.")


def test_open311_quelle_nimmt_am_quellabgleich_nicht_teil():
    assert quellen.BERLIN.quellabgleich_moeglich is True
    assert quellen.KOELN.quellabgleich_moeglich is False


def test_kennung_traegt_die_stadt():
    m = koelner_meldung(19369)
    assert KOELN.kennung(m) == "koeln:19369-2026"


def test_meldung_ohne_kennung_ist_ein_fehler():
    """Ohne stabile Kennung waere jeder Lauf ein Neuimport."""
    m = koelner_meldung(1)
    m["service_request_id"] = ""
    with pytest.raises(ValueError):
        KOELN.kennung(m)


def test_anzeige_zeigt_nur_den_bestand_einer_stadt(db, monkeypatch):
    """A-19: getrennte Karte je Stadt. Ohne den Filter stuenden Koelner Zellen
    auf der Berliner Seite."""
    conn = sqlite3.connect(db)
    for stadt, ort in (("berlin", BERLIN_ORT), ("koeln", (50.95, 6.92))):
        conn.execute(
            "INSERT INTO hotspots (cluster_id, lat_center, lon_center, bezirk, "
            "meldungen_count, recurrence_count, last_seen, first_seen, score, "
            "score_label, strasse, plz, stadt) VALUES (?,?,?,?,5,1,'2026-08-01',"
            "'2026-01-01',9.0,'hoch','Teststr','12345',?)",
            (tracker.cluster_id(*ort), ort[0], ort[1], "Bezirk", stadt))
    conn.commit()
    conn.close()

    nur_berlin = export_html.load_data("berlin")["hotspots"]
    nur_koeln = export_html.load_data("koeln")["hotspots"]
    assert len(nur_berlin) == 1 and len(nur_koeln) == 1
    assert nur_berlin[0]["cluster_id"] != nur_koeln[0]["cluster_id"]


def test_datenstand_gilt_je_stadt(db):
    """Befund 3 aus T-51, hier auf der Leseseite: der Berliner Streifen darf
    nicht das Datum des letzten Koelner Abrufs zeigen."""
    conn = sqlite3.connect(db)
    conn.execute("INSERT INTO fetch_log (fetched_at, count_total, count_new, "
                 "count_muell, stadt) VALUES ('2026-04-14T06:00:00',100,1,1,'berlin')")
    conn.execute("INSERT INTO fetch_log (fetched_at, count_total, count_new, "
                 "count_muell, stadt) VALUES ('2026-08-15T06:00:00',100,1,1,'koeln')")
    conn.commit()
    conn.close()

    assert export_html.load_data("berlin")["last_update"] == "2026-04-14"
    assert export_html.load_data("koeln")["last_update"] == "2026-08-15"


# ── Wiederkehr-Fenster ───────────────────────────────────────────────────────

def test_wiederkehr_fenster_kommt_von_der_stadt(db, monkeypatch):
    """Das Fenster ist auf den Berliner Reinigungsrhythmus geeicht und nicht
    uebertragbar. Es muss je Stadt einstellbar sein — hier nachgewiesen, indem
    ein enges Fenster die Wiederkehr verschwinden laesst."""
    tage = ("2026-08-01T09:00:00+02:00", "2026-08-12T09:00:00+02:00")
    meldungen = [koelner_meldung(500 + i, datum=d) for i, d in enumerate(tage)]
    monkeypatch.setattr(KOELN, "hole_meldungen", lambda *a, **k: meldungen)

    monkeypatch.setattr(KOELN, "wiederkehr_fenster_tage", 21)
    tracker.run("koeln")
    conn = sqlite3.connect(db)
    weit = conn.execute("SELECT recurrence_count FROM hotspots "
                        "WHERE stadt='koeln'").fetchone()[0]
    conn.execute("DELETE FROM hotspots")
    conn.commit()
    conn.close()

    monkeypatch.setattr(KOELN, "wiederkehr_fenster_tage", 5)
    tracker.run("koeln")
    conn = sqlite3.connect(db)
    eng = conn.execute("SELECT recurrence_count FROM hotspots "
                       "WHERE stadt='koeln'").fetchone()[0]
    conn.close()

    assert weit == 1, weit
    assert eng == 0, eng


# ── Rueckimport ──────────────────────────────────────────────────────────────

def test_startzeitpunkt_kommt_aus_der_frist_nicht_aus_einer_konstante(monkeypatch):
    """Lars-Entscheidung 15.08.2026: der Rueckimport wird auf die
    Aufbewahrungsfrist gekuerzt. Ein festes Datum im Quelltext liefe in einem
    Jahr wieder gegen dieselbe Grenze."""
    jetzt = datetime(2026, 8, 15)
    von, warum = rueckimport.startzeitpunkt(KOELN, jetzt)
    assert von == datetime(2024, 8, 15), von
    assert "24" in warum

    # Wird die Frist angehoben, reicht der Rueckimport ohne Codeaenderung
    # weiter zurueck — bis an den Anfang der Quelle.
    monkeypatch.setenv("MM_RETENTION_AGGREGAT_MONATE_KOELN", "48")
    von2, warum2 = rueckimport.startzeitpunkt(KOELN, jetzt)
    assert von2 == datetime(2023, 12, 12), von2
    assert "erst ab 2023-12-12" in warum2


def test_rueckimport_schreibt_ohne_ausfuehren_nichts(db, monkeypatch):
    """Der Trockenlauf ist die Vorgabe."""
    meldungen = [koelner_meldung(600 + i) for i in range(5)]
    monkeypatch.setattr(KOELN, "hole_meldungen", lambda *a, **k: meldungen)

    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    # Zwei Scheiben mit demselben Rueckgabewert: die Meldung darf trotzdem nur
    # einmal als neu gezaehlt werden, sonst waere die Zahl, an der Lars den
    # Umfang abschaetzt, zu hoch.
    ergebnis = rueckimport.lauf(KOELN, conn, datetime(2026, 8, 15),
                                ausfuehren=False, scheibe_tage=400, still=True)
    anzahl = conn.execute("SELECT COUNT(*) FROM meldungen").fetchone()[0]
    conn.close()

    assert ergebnis["neu"] == 5, ergebnis["neu"]
    assert ergebnis["abgerufen"] == 10
    assert ergebnis["mit_bild"] == 10
    assert anzahl == 0, "Der Trockenlauf hat geschrieben"


def test_rueckimport_bricht_bei_unplausiblem_abschnitt_ab(db, monkeypatch):
    """Eine Luecke wird nicht als 'in diesem Monat war nichts' verbucht."""
    def wirft(*a, **k):
        raise open311.AbrufUnplausibel("Testfall")
    monkeypatch.setattr(KOELN, "hole_meldungen", wirft)

    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    ergebnis = rueckimport.lauf(KOELN, conn, datetime(2026, 8, 15),
                                ausfuehren=True, still=True)
    anzahl = conn.execute("SELECT COUNT(*) FROM meldungen").fetchone()[0]
    conn.close()

    assert "abgebrochen" in ergebnis
    assert anzahl == 0


def test_rueckimport_laesst_andere_staedte_in_ruhe(db, monkeypatch):
    """Die Loeschfristen laufen mit (A-20) — aber nur fuer die eigene Stadt.
    Ein Berliner Bestand mit alten Meldungen darf davon nicht angefasst
    werden."""
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO meldungen (id, fetched_at, datum, kategorie, betreff, "
        "bezirk, lat, lon, status, is_muell, strasse, plz, stadt) "
        "VALUES ('berlin:alt','2020-01-01','2019-01-01','','Abfall - Sperrmüll',"
        "'Mitte',52.52,13.405,'Erledigt',1,'Teststr','10115','berlin')")
    conn.commit()
    conn.close()

    meldungen = [koelner_meldung(700 + i, lat=50.95 + i * 0.01) for i in range(3)]
    monkeypatch.setattr(KOELN, "hole_meldungen", lambda *a, **k: meldungen)

    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    rueckimport.lauf(KOELN, conn, datetime(2026, 8, 15), ausfuehren=True,
                     scheibe_tage=400, still=True)
    berlin = conn.execute(
        "SELECT COUNT(*) FROM meldungen WHERE stadt='berlin'").fetchone()[0]
    koeln = conn.execute(
        "SELECT COUNT(*) FROM meldungen WHERE stadt='koeln'").fetchone()[0]
    conn.close()

    assert berlin == 1, ("Die Berliner Meldung von 2019 wurde vom Koelner "
                         "Rueckimport aggregiert — genau das darf nicht sein")
    assert koeln == 3


def test_rueckimport_zerlegt_den_zeitraum_in_scheiben():
    von = datetime(2024, 8, 15)
    bis = datetime(2024, 11, 15)
    scheiben = list(rueckimport.monatsscheiben(von, bis, tage=30))
    assert scheiben[0].von == von
    assert scheiben[-1].bis == bis
    for a, b in zip(scheiben, scheiben[1:]):
        assert a.bis == b.von, "Zwischen zwei Scheiben darf keine Luecke sein"


# ── Berlin bleibt Berlin ─────────────────────────────────────────────────────

def test_berlin_laeuft_ohne_angabe_und_unveraendert(db, monkeypatch):
    """Der Auftrag verlangt ausdruecklich, dass Berlin unveraendert
    weiterlaeuft, falls die Schnittstelle zurueckkommt."""
    monkeypatch.setattr(tracker, "fetch_meldungen", lambda: [
        {"id": "77", "kategorie": "", "betreff": "Abfall - Sperrmüll",
         "bezirk": "Mitte", "lat": BERLIN_ORT[0], "lon": BERLIN_ORT[1],
         "status": "Erledigt", "erstellungsDatum": "01.08.2026",
         "strasse": "Teststr 12", "plz": "10115"},
    ])
    assert tracker.run() == 0

    conn = sqlite3.connect(db)
    zeile = conn.execute(
        "SELECT id, betreff, strasse, stadt FROM meldungen").fetchone()
    conn.close()
    assert zeile == ("berlin:77", "Abfall - Sperrmüll", "Teststr", "berlin")


def test_unbekannte_stadt_wird_nicht_stillschweigend_zu_berlin():
    with pytest.raises(KeyError):
        quellen.hole("hamburg")


def test_retention_grenze_ist_die_quelle_der_wahrheit():
    """Es gibt genau eine Stelle, an der die 24 Monate stehen."""
    jetzt = datetime(2026, 8, 15)
    assert retention.aggregat_grenze(jetzt, "koeln") == datetime(2024, 8, 15)
    assert retention.aggregat_grenze(jetzt, "berlin") == datetime(2024, 8, 15)
