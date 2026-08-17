"""
Eine DRITTE Stadt kommt nicht an den Sperren vorbei (T-49, 17.08.2026)
======================================================================
Bonn sollte in diesem Auftrag die dritte Stadt werden. Die Schnittstelle der
Stadt war dabei nicht erreichbar (siehe unten), der Import fand also nicht
statt. Was sich ohne die Quelle beantworten laesst, steht hier.

Die Auflage lautete woertlich: "Keine Stadt darf an den Sperren vorbei
importiert werden — mit einem Test belegen, nicht mit einer Zusicherung."
Genau das ist der Zweck dieser Datei. Sie prueft die Auflagen NICHT an Berlin
und NICHT an Koeln, sondern an einer Stadt, die es im Quelltext nicht gibt:

    ``probstadt`` — eine erfundene Open311-Quelle mit eigener Kategorienliste,
    an Bonns Koordinaten gesetzt, in KEINER Liste des Projekts eingetragen.

Der Unterschied zu ``test_koeln.py`` und ``test_zellen_stadtscharf.py`` ist
wesentlich. Die pruefen, dass die Auflagen fuer die beiden EINGETRAGENEN Staedte
greifen. Ein Schutz, der an einer Namensliste haengt, wuerde dort gruen bleiben
und bei Bonn stumm ausfallen. Hier faellt genau das auf: jeder Test unten laeuft
gegen eine Stadt, deren Namen kein Modul kennt.

Warum das die teuerste Fehlerklasse dieses Projekts ist
--------------------------------------------------------
Zweimal ist derselbe Fehler schon eingetreten, beide Male still:

  * T-55 — die A-2-Bereinigung sass ausschliesslich in ``berechne_hotspots``,
    und dorthin kommt eine Stadt ohne antwortende Quelle nie. Eine
    Datenschutzpflicht stand still, weil eine Behoerde nicht antwortete.
  * T-66 — dieselben Loeschungen waren stadtblind. Ein einziger Koelner Lauf
    senkte die Berliner Zellen von 8.707 auf 5.805, waehrend die Berliner
    Meldungen byte-genau unveraendert blieben.

Beide Male war der Bestand danach gueltig und die Zahlen plausibel. Nur die
Auflage war weg. Eine dritte Stadt ist der naechste Anlass fuer genau diese
Klasse, deshalb steht die Probe hier vor dem Import und nicht danach.

Belegt mit einer Mutationsprobe, nicht mit gruenen Tests
--------------------------------------------------------
Gruen allein sagt nichts. Zwoelf Rueckbauten der geprueften Sperren sind
einzeln eingebaut und gefahren worden, **zehn** meldet diese Datei rot. Die
ersten drei Fassungen des Freitext-, des Sperr- und des Rueckhalte-Tests waren
dabei WERTLOS und sind daran aufgefallen — sie konnten gar nicht rot werden.
Was daran falsch war, steht jeweils im Test.

Zwei Rueckbauten bleiben gruen, beide bewusst:

  * Das Durchreichen der Stadt an den Freitextfilter
    (``entschaerfe(..., stadt=self.stadt)``) laesst sich HIER nicht pruefen und
    das ist richtig so: ``probstadt`` hat keinen eigenen Regelsatz, also liefern
    der ausdrueckliche Aufruf und die Vorgabe dasselbe Regelwerk. Unterscheidbar
    wird der Rueckbau erst an einer Stadt MIT eigenem Satz — die Gesamtsuite
    meldet ihn mit 34 Fehlschlaegen.
  * Die Plausibilitaetsschwelle traegt bei Bonner Dichte nicht mehr. Das ist
    keine Luecke im Test, sondern eine gemessene Luecke im Bau; sie steht als
    eigener Test am Ende dieser Datei.

Alle Beispieltexte sind erfunden. Echte Buerger-Freitexte gehoeren nicht in
einen oeffentlichen Code-Speicher.
"""

import sqlite3
from datetime import datetime, timedelta

import pytest

import betreff_filter
import export_html
import open311
import quellen
import sperrliste
import tracker

# Bonns Koordinaten. Absichtlich die echten: sie liegen weit von Berlin
# (52,5 / 13,4) und von Koeln (50,94 / 6,96) entfernt, eine Verwechslung der
# Zellen ist damit ausgeschlossen und die Probe bleibt anschaulich.
PROB_ORT = (50.7339, 7.0997)
PROB_ORT_2 = (50.7250, 7.0850)
PROB_ORT_3 = (50.7180, 7.0700)
BERLIN_ORT = (52.5200, 13.4050)
KOELN_ORT = (50.9375, 6.9603)

# Die Kategorienliste der erfundenen Stadt. Bewusst andere Codes als Koeln —
# ein Schutz, der versehentlich auf Koelner Codes prueft, faellt hier auf.
PROB_KATEGORIEN = {
    "77.1": ("Wilde Ablagerung", "illegal"),
    "77.2": ("Schrottrad", "schrottfahrrad"),
}


# ── Die erfundene Stadt ──────────────────────────────────────────────────────

def probquelle(freitext_uebernehmen=True, mindest_je_tag=5.0):
    """Eine Open311-Quelle, die in keiner Liste des Projekts steht.

    ``freitext_uebernehmen`` steht hier auf True, anders als bei Koeln. Das ist
    Absicht: nur so laeuft ein Freitext ueberhaupt in die Zuordnung, und nur
    dann sagt die A-14-Probe etwas aus. Fuer eine echte Stadt ist der Verzicht
    der schaerfere Weg — aber ein Test, der den Filter prueft, muss ihm etwas
    zu tun geben.
    """
    return quellen.Open311Quelle(
        stadt="probstadt",
        name="Probstadt",
        url="https://example.invalid/georeport/v2/requests.json",
        kategorien=PROB_KATEGORIEN,
        wiederkehr_fenster_tage=21,
        mindest_meldungen_je_tag=mindest_je_tag,
        freitext_uebernehmen=freitext_uebernehmen,
        erste_meldung="2021-11-23",
    )


def prob_meldung(nummer, code="77.1", name="Wilde Ablagerung",
                 datum="2026-08-10T09:00:00+02:00",
                 adresse="53111 Probstadt - Nordviertel, Beispielweg 42",
                 beschreibung="Wilde Ablagerung am Gehweg",
                 bild="https://example.invalid/files/2026-08/IMG_9.jpeg",
                 ort=PROB_ORT, status="closed"):
    return {
        "service_request_id": f"{nummer}-2026",
        "title": f"#{nummer}-2026 {name}",
        "description": beschreibung,
        "lat": ort[0],
        "long": ort[1],
        "address_string": adresse,
        "service_name": name,
        "requested_datetime": datum,
        "updated_datetime": datum,
        "status": status,
        "media_url": bild,
        "status_note": "",
        "service_code": code,
    }


@pytest.fixture
def db(tmp_path, monkeypatch):
    pfad = tmp_path / "dritte.db"
    monkeypatch.setattr(tracker, "DB_PATH", pfad)
    monkeypatch.setattr(export_html, "DB_PATH", pfad)
    monkeypatch.setattr(sperrliste, "SPERRLISTE_DATEI", tmp_path / "sperr.txt")
    conn = sqlite3.connect(pfad)
    tracker.init_db(conn)
    conn.close()
    return pfad


@pytest.fixture
def lauf(db, monkeypatch):
    """Traegt die erfundene Stadt ein und laesst sie einen Lauf fahren.

    Die Eintragung in ``quellen.ALLE`` geschieht ueber monkeypatch und gilt nur
    fuer den einzelnen Test. Sonst wuerde ``test_launcher.py`` rot, das zu Recht
    verlangt, dass jede Stadt in ``ALLE`` von allen drei Launchern abgerufen
    wird.
    """
    def fahren(meldungen, quelle=None, vorbestand=None):
        quelle = quelle or probquelle()
        monkeypatch.setitem(quellen.ALLE, quelle.stadt, quelle)
        monkeypatch.setattr(quelle, "hole_meldungen",
                            lambda zeitraum=None, **kw: list(meldungen))
        if vorbestand:
            conn = sqlite3.connect(db)
            vorbestand(conn)
            conn.close()
        code = tracker.run(quelle.stadt)
        conn = sqlite3.connect(db)
        conn.row_factory = sqlite3.Row
        return code, conn

    return fahren


def _zellen(conn, stadt):
    return conn.execute("SELECT COUNT(*) FROM hotspots WHERE stadt = ?",
                        (stadt,)).fetchone()[0]


def _fremdbestand(conn):
    """Berliner und Koelner Zellen, die ein fremder Lauf nicht anfassen darf.

    Beide mit einer einzigen Meldung — also genau die Bauart, die die
    A-2-Bereinigung entfernt, wenn sie stadtblind laeuft (Befund K-03).
    """
    for stadt, ort in (("berlin", BERLIN_ORT), ("koeln", KOELN_ORT)):
        conn.execute(
            "INSERT INTO hotspots (cluster_id, lat_center, lon_center, bezirk, "
            "meldungen_count, recurrence_count, last_seen, first_seen, score, "
            "score_label, strasse, plz, stadt) "
            "VALUES (?,?,?,'Mitte',1,0,'2026-01-01','2026-01-01',1.0,"
            "'niedrig','Teststr','10115',?)",
            (tracker.cluster_id(*ort), ort[0], ort[1], stadt))
    conn.commit()


# ── A-14: der Freitextfilter gilt auch fuer eine unbekannte Stadt ────────────

def test_eine_unbekannte_stadt_bekommt_den_freitextfilter_und_nicht_keinen():
    """Die Vorgabe in ``betreff_filter`` ist der Berliner Satz, nicht ein leerer.

    Der Unterschied entscheidet alles. Waere die Vorgabe ein leeres Regelwerk,
    liefe eine neu angebundene Stadt ohne A-14 — und zwar ohne jede Meldung,
    weil ein leeres Regelwerk technisch einwandfrei arbeitet: es findet nichts.
    """
    regeln = betreff_filter.regeln_fuer("probstadt")

    assert regeln, (
        "Eine Stadt ohne eigenen Eintrag bekommt ein LEERES Regelwerk. Damit "
        "laeuft A-14 fuer jede neu angebundene Stadt ins Nichts, ohne "
        "Fehlermeldung. Die Vorgabe muss ein Regelsatz sein "
        "(betreff_filter.STADT_STANDARD)."
    )
    assert regeln == betreff_filter.regeln_fuer("berlin"), (
        "Die Vorgabe fuer eine unbekannte Stadt soll der geprueften Berliner "
        "Satz sein — schwaecher als noetig ist besser als offen."
    )


def test_a14_entschaerft_den_freitext_einer_dritten_stadt(lauf):
    """A-14 am ERGEBNIS gemessen, an einer Stadt, die kein Modul kennt.

    Dieser Test prueft den Endzustand des Bestands und ausdruecklich NICHT den
    Zeitpunkt. Der Grund ist gemessen, nicht vermutet: A-14 hat zwei Ebenen,
    den Schreibweg in ``quellen.Open311Quelle.aufbereiten`` und den Nachlauf
    ``tracker._betreffe_nachziehen``, der bei jedem Lauf ueber den bestehenden
    Bestand geht. Nimmt man den Schreibweg heraus, bleibt dieser Test gruen,
    weil der Nachlauf denselben Text wieder entschaerft (Rueckbau M-B3 vom
    17.08.2026). Erst wenn BEIDE Ebenen fehlen, wird er rot.

    Den Zeitpunkt pinnt der Test darunter. Beide zusammen sagen: der Bestand
    ist am Ende sauber, und er ist es schon beim Schreiben.
    """
    code, conn = lauf([
        prob_meldung(1, beschreibung="Obdachloser Herr Mueller lagert Muell"),
        prob_meldung(2, ort=PROB_ORT_2),
        prob_meldung(3, ort=PROB_ORT_2),
    ])
    betreffe = [r[0] for r in conn.execute(
        "SELECT betreff FROM meldungen WHERE stadt='probstadt'")]
    conn.close()

    assert code == 0
    assert not any("Mueller" in b for b in betreffe), (
        f"Der Name steht im Bestand der dritten Stadt: {betreffe}. A-14 greift "
        f"fuer sie nicht. Der Filter laeuft in quellen.Open311Quelle."
        f"aufbereiten und muss die Stadt durchreichen (stadt=self.stadt)."
    )
    assert not any("Obdachlos" in b for b in betreffe)
    assert any(betreff_filter.MARKER in b for b in betreffe), (
        "Der entschaerfte Text muss den Ersatztext samt Abfallgruppe tragen, "
        "sonst verliert die Meldung ihre Kategorie."
    )


def test_a14_greift_bei_der_dritten_stadt_schon_vor_dem_schreiben(lauf, monkeypatch):
    """Das WANN, und deshalb der wichtigere der beiden A-14-Tests.

    Der Nachlauf ist hier abgeschaltet. Uebrig bleibt allein der Schreibweg —
    genau die Ebene, die entscheidet, ob die Angabe je im Bestand steht. Ein
    Text, der erst nachtraeglich entschaerft wird, war vorher da: in der Datei,
    in jeder Sicherung, die in dem Moment lief, und in jedem Klon. So sind im
    Mai 2026 die 105.100 Hausnummern entstanden, die nachtraeglich entfernt
    werden mussten.

    Abgeschaltet wird der Nachlauf und nicht die Regelliste. Eine leere
    Regelliste wuerde beide Ebenen treffen und der Test saehe nur noch, dass
    irgendwo gefiltert wird.
    """
    monkeypatch.setattr(tracker, "_betreffe_nachziehen",
                        lambda conn: {"werte_geaendert": 0, "werte_geprueft": 0,
                                      "lebenssituation": 0, "hausnummer": 0,
                                      "zeilen_geaendert": 0})

    code, conn = lauf([
        prob_meldung(1, beschreibung="Obdachloser Herr Mueller lagert Muell"),
        prob_meldung(2, ort=PROB_ORT_2),
        prob_meldung(3, ort=PROB_ORT_2),
    ])
    betreffe = [r[0] for r in conn.execute(
        "SELECT betreff FROM meldungen WHERE stadt='probstadt'")]
    conn.close()

    assert code == 0
    assert not any("Mueller" in b for b in betreffe), (
        f"Ohne den Nachlauf steht der Name im Bestand: {betreffe}. Der "
        f"Schreibweg filtert also nicht selbst — die Angabe war einmal "
        f"gespeichert. Der Filter gehoert in "
        f"quellen.Open311Quelle.aufbereiten VOR das return, mit "
        f"stadt=self.stadt."
    )
    assert not any("Obdachlos" in b for b in betreffe)
    assert any(betreff_filter.MARKER in b for b in betreffe)


# ── A-17: Bildadressen ───────────────────────────────────────────────────────

def test_a17_keine_bildadresse_einer_dritten_stadt_im_bestand(lauf):
    """Bilder aus dem oeffentlichen Raum zeigen Gesichter, Kennzeichen und
    Hausfassaden. Die Adresse wird nicht uebernommen und das Bild nicht
    abgerufen — auch nicht fuer eine Stadt, die niemand eingetragen hat.

    Gefahren wird hier mit ``freitext_uebernehmen=False``, also so, wie eine
    echte neue Open311-Stadt steht. Der Grund ist in der Mutationsprobe
    aufgefallen und nicht vorher bedacht: mit uebernommenem Freitext wird
    ``bezeichnung`` eine Zeile spaeter durch ``description`` ERSETZT. Ein
    Rueckbau, der die Bildadresse an ``service_name`` haengt, waere damit
    ueberschrieben worden und der Test waere gruen geblieben — er haette die
    Auflage gar nicht geprueft.
    """
    code, conn = lauf([prob_meldung(i, ort=PROB_ORT) for i in range(1, 4)],
                      quelle=probquelle(freitext_uebernehmen=False))
    zeilen = conn.execute(
        "SELECT * FROM meldungen WHERE stadt='probstadt'").fetchall()
    conn.close()

    assert zeilen
    for zeile in zeilen:
        werte = " ".join(str(w) for w in tuple(zeile))
        assert "example.invalid/files" not in werte, (
            f"Eine Bildadresse ist in den Bestand gelangt: {werte[:200]}. "
            f"A-17 verlangt, dass media_url gar nicht gelesen wird."
        )
        assert ".jpeg" not in werte and "http" not in werte


def test_hausnummer_einer_dritten_stadt_faellt_vor_dem_schreiben(lauf):
    """"Beispielweg 42" darf als "Beispielweg" ankommen, nicht mit der 42."""
    code, conn = lauf([prob_meldung(i, ort=PROB_ORT) for i in range(1, 4)])
    strassen = {r[0] for r in conn.execute(
        "SELECT strasse FROM meldungen WHERE stadt='probstadt'")}
    conn.close()

    assert strassen == {"Beispielweg"}, (
        f"Das Strassenfeld der dritten Stadt lautet {strassen}. Die Hausnummer "
        f"muss in open311.zerlege_adresse fallen, bevor geschrieben wird."
    )


# ── A-2: Einzelfall-Zellen ───────────────────────────────────────────────────

def test_a2_einzelfall_zelle_einer_dritten_stadt_wird_nicht_gespeichert(lauf):
    """Eine Zelle mit einer einzigen Meldung entsteht gar nicht erst.

    Die Meldung selbst bleibt — sie fuettert den Wiederkehr-Zaehler. Nur die
    Zelle faellt, weil Bezirk, PLZ und Strasse bei einer einzigen Meldung eine
    Zuordnung auf eine Person zulassen.
    """
    code, conn = lauf([
        # zwei Meldungen in einer Zelle: die bleibt
        prob_meldung(1, ort=PROB_ORT),
        prob_meldung(2, ort=PROB_ORT),
        # eine einzige Meldung in einer eigenen Zelle: die faellt
        prob_meldung(3, ort=PROB_ORT_3),
    ])
    zellen = {r[0] for r in conn.execute(
        "SELECT cluster_id FROM hotspots WHERE stadt='probstadt'")}
    meldungen = conn.execute(
        "SELECT COUNT(*) FROM meldungen WHERE stadt='probstadt'").fetchone()[0]
    conn.close()

    assert meldungen == 3, "Die Meldungen selbst bleiben, nur die Zelle faellt."
    assert tracker.cluster_id(*PROB_ORT) in zellen
    assert tracker.cluster_id(*PROB_ORT_3) not in zellen, (
        "Die Einzelfall-Zelle der dritten Stadt steht im Bestand. A-2 greift "
        "fuer sie nicht (tracker.HOTSPOT_MIN_PERSIST)."
    )


def test_ein_lauf_der_dritten_stadt_laesst_berlin_und_koeln_unangetastet(lauf):
    """Befund K-03 / T-66, eine Stadt weiter.

    Der gefaehrliche Fall ist nicht, dass die neue Stadt zu wenig aufraeumt,
    sondern dass sie bei den anderen aufraeumt. Berlin kann eine zu Unrecht
    entfernte Zelle nicht neu aufbauen: die Quelle ist seit dem 22.04.2026 tot.
    """
    code, conn = lauf(
        [prob_meldung(1, ort=PROB_ORT), prob_meldung(2, ort=PROB_ORT)],
        vorbestand=_fremdbestand,
    )
    berlin, koeln = _zellen(conn, "berlin"), _zellen(conn, "koeln")
    umgefaerbt = conn.execute(
        "SELECT cluster_id, stadt FROM hotspots "
        "WHERE lat_center > 52 AND stadt <> 'berlin'").fetchall()
    conn.close()

    assert berlin == 1 and koeln == 1, (
        f"Ein Lauf der dritten Stadt hat fremde Zellen entfernt "
        f"(berlin={berlin}, koeln={koeln}, erwartet je 1). Die "
        f"Einzelfall-Bereinigung in tracker.berechne_hotspots muss auf die "
        f"eigene Stadt begrenzt bleiben (AND stadt = ?)."
    )
    assert not umgefaerbt, (
        f"Eine Berliner Zelle wurde umgefaerbt: {[tuple(r) for r in umgefaerbt]}. "
        f"Ursache ist ein fehlender Stadt-Filter im SELECT von "
        f"berechne_hotspots (Befund K-04)."
    )


# ── A-7: Widerspruch nach Art. 21 ────────────────────────────────────────────

def test_a7_sperre_greift_auch_fuer_eine_zelle_der_dritten_stadt(lauf, tmp_path):
    """Der Gegenpol zu A-2: die Sperre muss stadtblind bleiben.

    Gesperrt wird eine Zelle der EIGENEN und eine der FREMDEN Stadt, und
    gefahren wird ein Lauf der dritten. Nur die fremde Zelle beweist etwas:
    sperrt man ausschliesslich eine Zelle von ``probstadt`` und laesst
    ``probstadt`` laufen, faellt sie auch mit einem Stadt-Filter — der Test
    waere dann gruen, ohne die Auflage zu pruefen. In der Mutationsprobe ist
    genau das aufgefallen (Rueckbau M4 blieb unbemerkt).

    Beide Zellen bekommen genug Meldungen bzw. einen hohen Zaehler, damit sie
    nur ueber die Sperre fallen koennen und nicht schon ueber die
    Einzelfall-Regel.
    """
    def gesperrt(conn):
        conn.row_factory = sqlite3.Row
        # Eine FREMDE Zelle mit hohem Zaehler, damit A-2 sie nicht ohnehin holt
        conn.execute(
            "INSERT INTO hotspots (cluster_id, lat_center, lon_center, bezirk, "
            "meldungen_count, recurrence_count, last_seen, first_seen, score, "
            "score_label, strasse, plz, stadt) "
            "VALUES (?,?,?,'Mitte',9,0,'2026-01-01','2026-01-01',1.0,"
            "'niedrig','Teststr','10115','berlin')",
            (tracker.cluster_id(*BERLIN_ORT), BERLIN_ORT[0], BERLIN_ORT[1]))
        for cid, wer in ((tracker.cluster_id(*PROB_ORT_2), "probstadt"),
                         (tracker.cluster_id(*BERLIN_ORT), "berlin")):
            sperrliste.eintragen(conn, cid, quelle="W-2026-500", stadt=wer,
                                 datei=tmp_path / "sperr.txt")
        conn.commit()

    code, conn = lauf(
        [prob_meldung(1, ort=PROB_ORT), prob_meldung(2, ort=PROB_ORT)]
        + [prob_meldung(i, ort=PROB_ORT_2) for i in range(10, 14)],
        vorbestand=gesperrt,
    )
    eigene = {r[0] for r in conn.execute(
        "SELECT cluster_id FROM hotspots WHERE stadt='probstadt'")}
    fremde = {r[0] for r in conn.execute(
        "SELECT cluster_id FROM hotspots WHERE stadt='berlin'")}
    conn.close()

    assert tracker.cluster_id(*PROB_ORT) in eigene, "Ausgangslage"
    assert tracker.cluster_id(*PROB_ORT_2) not in eigene, (
        "Die gesperrte Zelle der dritten Stadt steht im Bestand. Damit ist ein "
        "Widerspruch nach Art. 21 DSGVO fuer sie unwirksam."
    )
    assert tracker.cluster_id(*BERLIN_ORT) not in fremde, (
        "Die gesperrte BERLINER Zelle steht nach einem Lauf der dritten Stadt "
        "noch im Bestand. Die Sperr-Loeschung in tracker."
        "_zellen_auflagen_nachziehen muss stadtblind bleiben — sie darf KEIN "
        "'AND stadt = ?' bekommen, anders als die Einzelfall-Regel daneben "
        "(T-66). Berlin laeuft nicht mehr: mit Stadt-Filter wuerde ein "
        "Widerspruch gegen eine Berliner Zelle von KEINEM Lauf mehr vollzogen."
    )


# ── C-03 und A-1: was die Anzeige verlaesst ──────────────────────────────────

def test_c03_k_anonymitaet_gilt_fuer_die_dritte_stadt(lauf):
    """Veroeffentlicht wird erst ab drei Meldungen je Zelle.

    Gemessen wird an ``load_data``, also an der Stelle, die die Karte fuellt —
    nicht an der Datenbank. Zwischen beiden liegt der Filter.
    """
    code, conn = lauf(
        [prob_meldung(1, ort=PROB_ORT), prob_meldung(2, ort=PROB_ORT)]
        + [prob_meldung(i, ort=PROB_ORT_2) for i in range(10, 14)])
    conn.close()

    daten = export_html.load_data("probstadt")
    counts = {h["cluster_id"]: h["meldungen_count"] for h in daten["hotspots"]}

    assert tracker.cluster_id(*PROB_ORT_2) in counts, "Ausgangslage: 4 Meldungen"
    assert tracker.cluster_id(*PROB_ORT) not in counts, (
        f"Eine Zelle der dritten Stadt mit 2 Meldungen geht in die Anzeige. "
        f"Die k-Anonymitaets-Schwelle ist "
        f"{export_html.K_ANONYMITY_THRESHOLD} (C-03, Erwaegungsgrund 26). "
        f"Gefunden: {counts}"
    )
    assert all(c >= export_html.K_ANONYMITY_THRESHOLD for c in counts.values())


def test_a1_koordinaten_der_dritten_stadt_verlassen_das_haus_gerundet(lauf):
    """A-1: was die Anzeige erreicht, ist das Rasterzentrum und nicht der Ort
    der Meldung. Ungerundet waeren es rund 150 Meter Genauigkeit weniger — und
    genau daran hing der Befund, wegen dem im August 2026 das
    Vorgaenger-Repository geloescht wurde (Massnahme A-15)."""
    code, conn = lauf([prob_meldung(i, ort=PROB_ORT_2) for i in range(10, 14)])
    conn.close()

    daten = export_html.load_data("probstadt")
    assert daten["hotspots"], "Ausgangslage: eine veroeffentlichungsfaehige Zelle"

    for h in daten["hotspots"]:
        lat, lon = h["lat_center"], h["lon_center"]
        assert (lat, lon) == export_html.raster_zentrum(lat, lon), (
            f"Die Koordinate {lat}/{lon} der dritten Stadt ist nicht auf das "
            f"Raster gerundet. A-1 muss vor allem stehen, was lat_center "
            f"weiterreicht."
        )


# ── A-21: eine duenne Antwort ist ein Ausfall ────────────────────────────────

def test_a21_leere_antwort_der_dritten_stadt_gilt_als_ausfall():
    """Die Mechanik der Auflage haengt nicht an der Stadt.

    ``pruefe_plausibel`` bekommt die Schwelle aus dem Quellen-Eintrag, prueft
    also fuer jede Stadt. Ein Zeitraum, der vollstaendig in der Vergangenheit
    liegt und nichts liefert, ist ein Fehler und kein Befund.
    """
    jetzt = datetime(2026, 8, 17)
    zeitraum = open311.Zeitraum(von=jetzt - timedelta(days=31),
                                bis=jetzt - timedelta(days=1))

    with pytest.raises(open311.AbrufUnplausibel):
        open311.pruefe_plausibel([], zeitraum, 5.0, jetzt=jetzt)


def test_a21_wird_von_hole_zeitraum_auch_wirklich_gerufen():
    """Nicht die Pruefung, sondern ihr AUFRUF.

    Der Test darueber ruft ``pruefe_plausibel`` selbst. Er bleibt deshalb gruen,
    wenn jemand den Aufruf am Ende von ``hole_zeitraum`` streicht — und genau
    das war Befund K-05 der Abnahme vom 15.08.2026: der Rueckbau liess damals
    alle 216 Tests gruen. Hier laeuft die Probe ueber den echten Weg, den eine
    dritte Stadt nimmt, mit einer Antwort, die technisch einwandfrei und
    inhaltlich unglaubwuerdig ist.
    """
    class Duenn:
        def get(self, url, params=None, headers=None, timeout=None):
            class Antwort:
                status_code = 200

                def json(self_inner):
                    # Eine einzige Meldung fuer einen ganzen Monat.
                    return [prob_meldung(1)] if (params or {}).get("page") == 0 else []

            return Antwort()

    jetzt = datetime(2026, 8, 17)
    zeitraum = open311.Zeitraum(von=jetzt - timedelta(days=31),
                                bis=jetzt - timedelta(days=1))

    with pytest.raises(open311.AbrufUnplausibel):
        open311.hole_zeitraum("https://example.invalid/x", zeitraum,
                              mindest_je_tag=5.0, pause=0, jetzt=jetzt,
                              sitzung=Duenn())


def test_a21_ein_ausfall_der_dritten_stadt_baut_die_zellen_nicht_neu(lauf):
    """Und die Stufe darueber: was ``tracker.run`` aus dem Ausfall macht.

    Ein gescheiterter Abruf muss wie ein leerer enden — Fehlermarke im
    Abrufprotokoll (count_total = -1), Rueckgabewert 1, damit der Launcher den
    Push ueberspringt, und KEIN Neuaufbau der Zellen (H-02b). Die Loeschungen
    laufen dagegen sehr wohl, das ist die Lehre aus T-55.
    """
    code, conn = lauf([], quelle=probquelle())
    marke = conn.execute(
        "SELECT count_total FROM fetch_log WHERE stadt='probstadt' "
        "ORDER BY rowid DESC LIMIT 1").fetchone()[0]
    zellen = _zellen(conn, "probstadt")
    conn.close()

    assert code == 1, (
        "Ein Ausfall der dritten Stadt muss einen Rueckgabewert ungleich 0 "
        "liefern, sonst pusht der Launcher einen Stand ohne Daten."
    )
    assert marke == -1, (
        f"Das Abrufprotokoll traegt {marke} statt der Fehlermarke -1. Ohne sie "
        f"sieht ein Ausfall aus wie ein Lauf ohne Meldungen."
    )
    assert zellen == 0, "Aus einem leeren Abruf entstehen keine Zellen (H-02b)."


def test_a21_seitenzahl_geht_auch_fuer_die_dritte_stadt_immer_mit():
    """Die Falle, an der die erste Koelner Messung gescheitert ist.

    ``page`` ist nullbasiert, und ein Abruf ohne ``page`` liefert stumm die
    erste Seite. Der Riegel sitzt im Adapter und nicht in einem
    Stadt-Eintrag — dieser Test haelt fest, dass das so bleibt.
    """
    class Sitzung:
        def __init__(self):
            self.seiten = []

        def get(self, url, params=None, headers=None, timeout=None):
            self.seiten.append((params or {}).get("page"))
            nummer = (params or {}).get("page")
            inhalt = ([prob_meldung(i + nummer * 100) for i in range(100)]
                      if nummer == 0 else
                      [prob_meldung(200 + i) for i in range(20)]
                      if nummer == 1 else [])

            class Antwort:
                status_code = 200

                def json(self_inner):
                    return inhalt

            return Antwort()

    sitzung = Sitzung()
    jetzt = datetime(2026, 8, 17)
    zeitraum = open311.Zeitraum(von=jetzt - timedelta(days=31),
                                bis=jetzt - timedelta(days=1))
    meldungen = open311.hole_zeitraum(
        "https://example.invalid/x", zeitraum, mindest_je_tag=1.0,
        pause=0, jetzt=jetzt, sitzung=sitzung)

    assert sitzung.seiten[0] == 0, (
        "Die erste Anfrage muss page=0 ausdruecklich mitgeben. Ohne den "
        "Parameter liefert die Schnittstelle stumm die erste Seite."
    )
    assert None not in sitzung.seiten, (
        f"Eine Anfrage ohne Seitenzahl ist abgesetzt worden: {sitzung.seiten}"
    )
    assert len(meldungen) == 120, (
        f"Es wurde nicht ueber die erste Seite hinaus geblaettert: "
        f"{len(meldungen)} statt 120 Meldungen."
    )


# ── A-4: der Quellabgleich darf fuer eine Open311-Stadt nicht laufen ─────────

def test_a4_quellabgleich_entfaellt_fuer_eine_dritte_open311_stadt(lauf, capsys):
    """Ein Open311-Abruf liefert einen ZEITRAUM, nicht den Bestand.

    Liefe der Abgleich, waere nach dem ersten Lauf der gesamte uebrige Bestand
    der Stadt als weggefallen vorgemerkt und 30 Tage spaeter geloescht — beim
    Rueckimport also genau die Tiefe, fuer die er gemacht wird. Der Schalter
    steht an der Quelle und nicht an einer Namensliste.
    """
    quelle = probquelle()
    assert quelle.quellabgleich_moeglich is False, (
        "Eine neue Open311-Quelle darf den Quellabgleich nicht erben. Die "
        "Vorgabe steht in quellen.Open311Quelle, nicht je Stadt."
    )

    # Vorbestand, der bei laufendem Abgleich als weggefallen gelten wuerde
    def altbestand(conn):
        conn.execute(
            "INSERT INTO meldungen (id, fetched_at, datum, kategorie, betreff, "
            "bezirk, lat, lon, status, is_muell, strasse, plz, stadt) "
            "VALUES ('probstadt:alt','2026-01-01','2026-01-01','illegal','x',"
            "'Nord',?,?,'Erledigt',1,'Beispielweg','53111','probstadt')",
            PROB_ORT_3)
        conn.commit()

    code, conn = lauf([prob_meldung(1, ort=PROB_ORT),
                       prob_meldung(2, ort=PROB_ORT)],
                      quelle=quelle, vorbestand=altbestand)
    weggefallen = conn.execute(
        "SELECT COUNT(*) FROM meldungen WHERE stadt='probstadt' "
        "AND quelle_weg_seit IS NOT NULL").fetchone()[0]
    conn.close()

    assert weggefallen == 0, (
        f"{weggefallen} Meldungen der dritten Stadt sind als aus der Quelle "
        f"weggefallen vorgemerkt worden, obwohl der Abruf nur einen Zeitraum "
        f"abdeckt. Das loescht 30 Tage spaeter den halben Bestand."
    )


# ── Der Ruecklauf: eine zurueckgehaltene Stadt bekommt keine Adresse ─────────

def _probstadt(veroeffentlicht: bool) -> "export_html.Stadt":
    return export_html.Stadt(
        slug="probstadt", name="Probstadt", quelle_satz="", status_text="",
        status_farbe="ruht", haftung_satz="", lizenz_url="", lizenz_text="",
        kachel_status="", kachel_marke="", kachel_marke_klasse="",
        karte_moeglich=True, veroeffentlicht=veroeffentlicht)


def test_eine_zurueckgehaltene_dritte_stadt_bekommt_keine_seite(monkeypatch):
    """T-74, auf die dritte Stadt angewandt.

    ``veroeffentlicht=False`` haelt die Stadt aus der ausgelieferten Struktur
    heraus — auch die Wartungsseite. Bei GitHub Pages ist der Speicher die
    Seite; eine gebaute Seite waere sofort oeffentlich.

    Die Probe laeuft gegen eine ``STAEDTE``-Fassung, die die erfundene Stadt
    ENTHAELT. Nur so sagt sie etwas aus: ohne die Eintragung wuerde
    ``ausgelieferte_staedte()`` den Namen ohnehin nie nennen, und der Test
    bliebe auch dann gruen, wenn die Auswahl gar nicht mehr filtert.
    """
    zurueckgehalten = _probstadt(veroeffentlicht=False)
    monkeypatch.setattr(export_html, "STAEDTE",
                        export_html.STAEDTE + (zurueckgehalten,))
    assert "probstadt" not in {s.slug for s in export_html.ausgelieferte_staedte()}, (
        "Eine Stadt mit veroeffentlicht=False steht in der ausgelieferten "
        "Auswahl. Damit bekaeme sie eine oeffentlich erreichbare Adresse."
    )

    # Gegenprobe, und sie muss rot werden koennen: dieselbe Stadt mit
    # veroeffentlicht=True gehoert in die Auswahl. Faellt der Filter aus, ist
    # die Zeile oben rot; filtert er alles weg, diese hier.
    monkeypatch.setattr(export_html, "STAEDTE",
                        export_html.STAEDTE[:-1] + (_probstadt(True),))
    assert "probstadt" in {s.slug for s in export_html.ausgelieferte_staedte()}, (
        "Eine Stadt mit veroeffentlicht=True fehlt in der ausgelieferten "
        "Auswahl. Dann filtert ausgelieferte_staedte() nicht, sondern leert."
    )


def test_kein_eigener_freigabe_schalter_ohne_eigenen_namen():
    """Jede Stadt hat ihren eigenen Freigabe-Schalter, der alte Sammelmarker
    schaltet nichts. Eine dritte Stadt darf nicht ueber den Schalter einer
    anderen live gehen."""
    probe = export_html.Stadt(
        slug="probstadt", name="Probstadt", quelle_satz="", status_text="",
        status_farbe="ruht", haftung_satz="", lizenz_url="", lizenz_text="",
        kachel_status="", kachel_marke="", kachel_marke_klasse="",
        karte_moeglich=True, veroeffentlicht=False)

    assert probe.marker.name == "LIVE_FREIGEGEBEN_PROBSTADT"
    for andere in export_html.STAEDTE:
        assert probe.marker != andere.marker, (
            f"Die dritte Stadt teilt ihren Freigabe-Schalter mit {andere.slug}."
        )


# ── Der bekannte Rest: was diese Datei NICHT beweist ─────────────────────────

def test_plausibilitaetsschwelle_traegt_bei_geringer_dichte_nicht_mehr():
    """Kennzeichnung einer gemessenen LUECKE, kein Nachweis einer Zusicherung.

    ``pruefe_plausibel`` laeuft fuer jede Stadt — aber ihre Wirkung haengt an
    der Meldungsdichte, und Bonn ist rund fuenfzigmal duenner als Koeln.

      Koeln    93,0 Meldungen je Tag gemessen, Schwelle 5,0  (Abstand 18,6-fach)
      Bonn      1,7 Meldungen je Tag im Jahr 2024 (alle Kategorien)

    Bei gleichem Sicherheitsabstand ergibt das fuer Bonn eine Schwelle von
    0,09 je Tag. In ``pruefe_plausibel`` steht ``erwartet = max(1, int(tage *
    mindest_je_tag))`` — auf die Woche des taeglichen Laufs gerechnet verlangt
    das GENAU EINE Meldung, wo in Wahrheit zwoelf kommen. Ein Ausfall, der eine
    einzige Meldung durchlaesst, geht damit als gueltiger Lauf durch.

    Dieser Test haelt den Zustand fest, damit er nicht in Vergessenheit
    geraet. Er wird rot, sobald ein wirksamerer Riegel eingebaut wird — und das
    ist dann die richtige Gelegenheit, ihn durch den Nachweis des neuen Riegels
    zu ersetzen. Ein gruener Test ueber einer bekannten Luecke ist besser als
    eine Notiz in einem Bericht, den niemand mehr liest.

    Entschieden ist der Riegel NICHT: die Schwelle einer Stadt gehoert an deren
    eigene Daten gemessen, und die Bonner Schnittstelle war am 17.08.2026 nicht
    erreichbar.
    """
    jetzt = datetime(2026, 8, 17)
    woche = open311.Zeitraum(von=jetzt - timedelta(days=8),
                             bis=jetzt - timedelta(days=1))

    # Eine einzige Meldung in einer Woche, in der zwoelf zu erwarten waeren:
    # geht durch. Das ist die Luecke.
    open311.pruefe_plausibel([{}], woche, 0.09, jetzt=jetzt)

    # Nur der vollstaendige Ausfall wird noch gefangen.
    with pytest.raises(open311.AbrufUnplausibel):
        open311.pruefe_plausibel([], woche, 0.09, jetzt=jetzt)

    # Zum Vergleich: bei Koelner Dichte traegt dieselbe Pruefung sehr wohl.
    with pytest.raises(open311.AbrufUnplausibel):
        open311.pruefe_plausibel([{}] * 30, woche, 5.0, jetzt=jetzt)
