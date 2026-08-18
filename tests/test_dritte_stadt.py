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
Gruen allein sagt nichts. Vierzehn Rueckbauten der geprueften Sperren sind
einzeln eingebaut und gefahren worden, **alle vierzehn** meldet diese Datei rot
(17.08.2026, gegen eine vollstaendige Kopie mit Assets). Die ersten Fassungen des
Bildadressen-, des Sperr- und des Rueckhalte-Tests waren dabei WERTLOS und sind
genau daran aufgefallen — sie konnten gar nicht rot werden. Was daran falsch war,
steht jeweils im Test.

Dabei ist eine Luecke im BAU herausgekommen, die kein Test des Projekts
bewachte: streicht man ``stadt=self.stadt`` aus dem Aufruf des Freitextfilters
in ``quellen.Open311Quelle.aufbereiten``, blieben alle 372 Tests gruen. Jede
Stadt bekaeme dann den Berliner Regelsatz statt ihres eigenen. Heute folgenlos,
weil Koeln seinen Freitext nicht uebernimmt — scharf, sobald eine Stadt ihn
uebernimmt. Bewacht jetzt von
``test_der_eigene_regelsatz_einer_stadt_erreicht_den_filter_auch``.

Nachtrag 18.08.2026 — die eine offene Luecke ist geschlossen (T-79)
-------------------------------------------------------------------
Hier stand, ein Rueckbau bleibe bewusst gruen: die Plausibilitaetsschwelle
trage bei Bonner Dichte nicht mehr, und der Riegel dafuer gehoere an die Daten
der betroffenen Stadt gemessen. Er ist gebaut und steht in
``plausibilitaet.py`` — er misst einen Abruf nicht mehr an einer gesetzten
Zahl, sondern am eigenen Vorlauf DIESER Stadt aus ``fetch_log``. Damit kuerzt
sich die Dichte heraus, und der Riegel sitzt in ``tracker.run`` statt in
``open311.py``, weil er sonst Berlin wieder nicht saehe.

Zwei Pruefsteine an echten Daten: er erkennt Berlins Ausfall ab dem ersten
leeren Lauf am 23.04.2026 (Untergrenze 26.364 gegen einen Vorlauf von 105.456),
und ueber 724 nachgestellte Koelner Wochen aus 731 Tagen echtem Bestand
beanstandet er nichts — die knappste Annaeherung liegt bei Faktor 2,57.

Was NICHT geschlossen ist, steht als eigener Test am Ende dieser Datei: im
Rueckimport bleibt die alte, dichteabhaengige Regel die einzige Pruefung.

Alle Beispieltexte sind erfunden. Echte Buerger-Freitexte gehoeren nicht in
einen oeffentlichen Code-Speicher.
"""

import sqlite3
from datetime import datetime, timedelta

import pytest

import betreff_filter
import export_html
import open311
import plausibilitaet
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


def test_der_eigene_regelsatz_einer_stadt_erreicht_den_filter_auch():
    """Dass eine Stadt ihren EIGENEN Regelsatz bekommt, war unbewacht.

    Gefunden in der Mutationsprobe zu dieser Datei: streicht man
    ``stadt=self.stadt`` aus dem Aufruf von ``betreff_filter.entschaerfe`` in
    ``quellen.Open311Quelle.aufbereiten``, bleiben **alle 372 Tests, die es vor
    diesem Test gab, gruen** (gemessen am 17.08.2026 an einer vollstaendigen
    Kopie mit Assets; mit ihm sind es 373, und genau einer wird rot).
    Jede Stadt bekaeme dann den Berliner Satz.

    Heute ist das folgenlos, weil Koeln seinen Freitext gar nicht uebernimmt und
    das kontrollierte Vokabular der Stadt keine Kennzeichen enthaelt. Es wird in
    dem Moment scharf, in dem eine Stadt Freitext uebernimmt — und genau das
    steht bei Bonn zur Entscheidung, falls die Stadt keine durchgaengige
    Kategorie mitliefert. Dann fielen die Zusatzregeln der Stadt
    (Kfz-Kennzeichen, Mailadressen, Telefonnummern) still aus.

    Geprueft an Koeln, weil es die einzige Stadt mit eigenem Satz ist. Das
    Kennzeichen ist erfunden.
    """
    text = "Schrottrad neben Wagen K-AB 123 abgestellt"

    # Der Berliner Satz kennt keine Kennzeichen, der Koelner schon. Faellt
    # dieser Unterschied weg, ist der Rueckbau nicht mehr erkennbar — dann ist
    # diese Zeile rot und nicht die Zusicherung darunter.
    assert "kennzeichen" not in betreff_filter.treffer(text, "berlin")
    assert "kennzeichen" in betreff_filter.treffer(text, "koeln"), (
        "Der Koelner Zusatzregelsatz erkennt kein Kfz-Kennzeichen mehr."
    )

    koeln = quellen.KOELN
    meldung = prob_meldung(1, code="1.1", name="Wilder Müll", beschreibung=text)
    original = koeln.freitext_uebernehmen
    try:
        # Nur fuer diese Probe den Freitext uebernehmen, damit er ueberhaupt in
        # den Filter laeuft. Der Verzicht bleibt die echte Einstellung.
        koeln.freitext_uebernehmen = True
        satz = koeln.aufbereiten(meldung, "2026-08-17T00:00:00")
    finally:
        koeln.freitext_uebernehmen = original

    assert "K-AB 123" not in satz["betreff"], (
        f"Das Kennzeichen steht im Betreff: {satz['betreff']!r}. Der Aufruf von "
        f"betreff_filter.entschaerfe in quellen.Open311Quelle.aufbereiten muss "
        f"die Stadt mitgeben (stadt=self.stadt), sonst gilt fuer jede Stadt der "
        f"Berliner Satz und ihre eigenen Zusatzregeln laufen ins Nichts."
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


# ── T-79: der Riegel, der die Dichte-Luecke schliesst ────────────────────────
#
# HIER STAND BIS ZUM 18.08.2026 EIN GRUENER TEST UEBER EINER BEKANNTEN LUECKE,
# ``test_plausibilitaetsschwelle_traegt_bei_geringer_dichte_nicht_mehr``. Er
# hielt fest, dass ``open311.pruefe_plausibel`` bei duenner Dichte auf "genau
# eine Meldung" zusammenfaellt: Koeln 93,0 Meldungen je Tag bei Schwelle 5,0,
# Bonn 1,7 je Tag, und wegen ``erwartet = max(1, int(tage * mindest_je_tag))``
# verlangt die Woche des taeglichen Laufs dann eine Meldung, wo zwoelf faellig
# sind. Ein Ausfall, der eine einzige durchlaesst, ging als gueltiger Lauf
# durch.
#
# Ersetzt durch die Tests unten, die den Nachweis des neuen Riegels fuehren.
# EINE EHRLICHE ANMERKUNG DAZU: der alte Test ist NICHT von selbst rot
# geworden. Er prueft ``open311.pruefe_plausibel`` unmittelbar, und die
# Funktion ist unveraendert geblieben — der neue Riegel sitzt eine Ebene
# hoeher, in ``tracker.run``, weil er sonst Berlin wieder nicht sehen wuerde
# (Berlins Feed laeuft nie durch ``open311.hole_zeitraum``). Was von der alten
# Schwaeche uebrig bleibt, steht im letzten Test dieser Datei und ist dort
# benannt statt weggeraeumt.

def _protokoll(conn, stadt: str, werte, tage, plausibel=1):
    """Vorlauf im Abrufprotokoll anlegen — so, wie ihn echte Laeufe hinterlassen."""
    for n, wert in enumerate(werte):
        conn.execute(
            "INSERT INTO fetch_log (fetched_at, count_total, count_new, "
            "count_muell, stadt, zeitraum_tage, plausibel) VALUES (?,?,?,?,?,?,?)",
            (f"2026-01-{n + 1:02d}T04:00:00", wert, 0, 0, stadt, tage, plausibel))
    conn.commit()


def _leeres_protokoll():
    conn = sqlite3.connect(":memory:")
    tracker.init_db(conn)
    return conn


def test_der_mengenriegel_traegt_auch_bei_geringer_dichte():
    """Der Nachweis, der die Luecke von oben schliesst.

    Dieselbe Lage wie im ersetzten Test: eine Stadt mit 1,7 Meldungen je Tag,
    ein taeglicher Lauf ueber sieben Tage, also rund zwoelf Meldungen je Abruf.
    Ein Ausfall laesst eine einzige durch.

    Die alte Regel verlangte ``max(1, int(7 * 0.09))`` = 1 und liess das
    durchgehen. Der Mengenriegel misst am eigenen Vorlauf dieser Stadt und
    verlangt ein Viertel davon — bei zwoelf Meldungen je Abruf also drei.
    """
    conn = _leeres_protokoll()
    _protokoll(conn, "probstadt", [12, 11, 13, 12, 14, 10, 12, 13], tage=7.0)

    # Die alte Regel liess genau das durch. Sie steht hier als Gegenprobe, damit
    # der Unterschied im Test sichtbar ist und nicht nur im Bericht.
    jetzt = datetime(2026, 8, 17)
    woche = open311.Zeitraum(von=jetzt - timedelta(days=8),
                             bis=jetzt - timedelta(days=1))
    open311.pruefe_plausibel([{}], woche, 0.09, jetzt=jetzt)

    with pytest.raises(plausibilitaet.AbrufUnplausibel) as fehler:
        plausibilitaet.pruefe(conn, "probstadt", anzahl=1, tage=7.0)

    assert "1 Meldungen erhalten" in str(fehler.value)
    assert "mindestens 3" in str(fehler.value), (
        "Der Riegel muss die Untergrenze aus dem eigenen Vorlauf ziehen "
        f"(12 je Abruf, ein Viertel davon), nicht aus einer gesetzten Zahl. "
        f"Meldung war: {fehler.value}")


def test_der_mengenriegel_misst_dichte_und_duenne_stadt_am_selben_massstab():
    """Der Kern von T-79: das Urteil haengt am Verhaeltnis, nicht an der Dichte.

    Zwei Staedte, rund fuenfzigfach auseinander — Koeln mit 650 Meldungen je
    Wochen-Abruf, eine duenne Stadt mit 12. Beide verlieren durch einen Ausfall
    fuenf Sechstel ihres Aufkommens. Beide muessen auffallen.

    Genau das konnte die feste Zahl je Stadt nicht: sie war fuer Koeln um den
    Faktor 18,6 vom Normalstand entfernt und fuer eine duenne Stadt um den
    Faktor 0,08.
    """
    for stadt, normal in (("dichtestadt", 650), ("duennestadt", 12)):
        conn = _leeres_protokoll()
        _protokoll(conn, stadt, [normal] * 8, tage=7.0)

        # Ein Sechstel des Normalstands: Ausfall, in beiden Groessenordnungen.
        with pytest.raises(plausibilitaet.AbrufUnplausibel):
            plausibilitaet.pruefe(conn, stadt, anzahl=normal // 6, tage=7.0)

        # Zwei Drittel des Normalstands: ruhige Woche, keine Beanstandung.
        plausibilitaet.pruefe(conn, stadt, anzahl=int(normal * 0.66), tage=7.0)


def test_der_mengenriegel_beanstandet_koelns_duennste_echte_woche_nicht():
    """Pruefstein 2, mit den gemessenen Zahlen aus dem echten Bestand.

    Ueber 731 Tage Koelner Bestand (12.12.2023 bis 15.08.2026, als gleitende
    Sieben-Tage-Fenster) liegt die Woche im Median bei 318 Meldungen, die
    duennste echte Woche bei 175 (11.02.2026, Karneval). Das sind 55 Prozent
    des Medians — der Riegel steht bei 25 Prozent, also bleibt der Faktor 2,2
    dazwischen.

    Nachgemessen wurde der ganze Verlauf: 724 Laeufe, null Beanstandungen, die
    knappste Annaeherung mit Faktor 2,57. Dieser Test haelt den engsten Punkt
    fest, damit ein spaeter angehobener Anteil hier auflaeuft und nicht erst im
    Betrieb.
    """
    conn = _leeres_protokoll()
    _protokoll(conn, "koeln", [318] * 8, tage=7.0)

    vermerk = plausibilitaet.pruefe(conn, "koeln", anzahl=175, tage=7.0)

    assert vermerk["untergrenze"] == 79
    assert 175 / vermerk["untergrenze"] > 2.0, (
        "Zwischen Koelns duennster echter Woche und dem Ausloeser muss Luft "
        "bleiben. Der Riegel soll einen Ausfall erkennen, keine Karnevalswoche.")


def test_ein_ausfall_wird_nicht_selbst_zum_massstab():
    """Der Vorlauf darf nicht altern, sonst hebt ein langer Ausfall sich selbst auf.

    Berlins Quelle liefert seit dem 23.04.2026 nichts. Waehlte der Riegel
    seinen Vorlauf nach Alter statt nach Anzahl, waere der Ausfall nach
    wenigen Wochen der neue Normalstand und die Pruefung stumm — der Fehler,
    den sie verhindern soll.

    Nachgestellt mit Berlins echten Zahlen: 13 Laeufe mit 105.456 Meldungen im
    Bestand, danach vierzehn Laeufe mit null. Auch der vierzehnte muss noch
    anschlagen.
    """
    conn = _leeres_protokoll()
    _protokoll(conn, "berlin", [105456] * 13, tage=None)

    for lauf in range(14):
        with pytest.raises(plausibilitaet.AbrufUnplausibel):
            plausibilitaet.pruefe(conn, "berlin", anzahl=0, tage=None)
        # Ein gerissener Lauf geht als solcher ins Protokoll und darf den
        # Massstab nicht senken.
        _protokoll(conn, "berlin", [-1], tage=None, plausibel=0)

    werte = plausibilitaet.lies_vorlauf(conn, "berlin", ist_zeitraum_abruf=False)
    assert plausibilitaet.basis(werte) == 105456, (
        "Nach vierzehn Ausfaellen steht der Massstab nicht mehr auf dem letzten "
        "gesunden Stand. Damit wuerde der Ausfall sich selbst zum Normalstand "
        "erklaeren.")

    # Der schwierigere Fall: ein TEILausfall. Er liefert etwas, wird vom Riegel
    # aber abgelehnt — und darf trotzdem nicht in den Massstab. Ihn nur an der
    # Fehlermarke -1 zu erkennen reicht nicht, denn diese Zeile traegt eine
    # echte Zahl. Dafuer ist die Spalte ``plausibel`` da.
    _protokoll(conn, "berlin", [8000] * 10, tage=None, plausibel=0)
    werte = plausibilitaet.lies_vorlauf(conn, "berlin", ist_zeitraum_abruf=False)
    assert plausibilitaet.basis(werte) == 105456, (
        "Zehn abgelehnte Teilausfaelle mit je 8.000 Meldungen haben den Massstab "
        "gesenkt. Eine langsam abbauende Quelle zoege ihn damit hinter sich her, "
        "bis der Ausfall der Normalstand ist.")


def test_der_massstab_ist_der_median_und_nicht_der_mittelwert():
    """Ein einzelner Ausreisser nach oben darf die Untergrenze nicht anheben.

    Ein nachgeholter Abruf oder ein Rueckimport bringt einmalig ein Vielfaches
    des Alltags. Ueber den Mittelwert gerechnet misst die Pruefung danach an
    einer Zahl, die es im Alltag nie gab — und jeder normale Lauf waere ein
    Ausfall.
    """
    conn = _leeres_protokoll()
    _protokoll(conn, "ausreisser", [12, 11, 13, 12, 14, 10, 12, 5000], tage=7.0)

    werte = plausibilitaet.lies_vorlauf(conn, "ausreisser", ist_zeitraum_abruf=True)
    # Median der Tagesmengen: 12/7 herum, nicht (5000+...)/8.
    assert plausibilitaet.basis(werte) < 2.0

    # Der ganz normale naechste Lauf darf davon nicht beanstandet werden.
    plausibilitaet.pruefe(conn, "ausreisser", anzahl=12, tage=7.0)


def test_jede_stadt_sagt_selbst_welche_abruf_art_sie_fuehrt():
    """Ohne diese Angabe landen Bestand und Zeitfenster im selben Topf.

    Berlin liefert den Bestand und kennt kein Fenster, eine Open311-Stadt
    liefert im taeglichen Lauf sieben Tage. Die Angabe stammt von der Quelle
    selbst und nicht von einer Liste in ``tracker.py`` — sonst waere sie beim
    Eintragen der dritten Stadt genau die Zeile, die jemand vergisst.
    """
    assert quellen.BERLIN.fenster_tage() is None
    assert quellen.KOELN.fenster_tage() == 7.0
    assert probquelle().fenster_tage() == 7.0


def test_bestandsabruf_und_zeitraum_abruf_werden_nicht_vermischt():
    """Berlins Bestand ist kein Massstab fuer Koelns Woche.

    Berlin liefert je Abruf den vollstaendigen Bestand (zuletzt 105.456), eine
    Open311-Stadt ein Zeitfenster. Wuerde beides in denselben Topf fallen,
    haette Koeln eine Untergrenze im fuenfstelligen Bereich und jeder Lauf
    waere ein Ausfall.

    Deshalb traegt ``fetch_log.zeitraum_tage`` die Fensterlaenge, und ``NULL``
    steht fuer den Bestandsabruf. Aeltere Zeilen ohne den Wert gelten als
    Bestandsabruf — fuer Berlin richtig, und Koelns zwei Rueckimport-Zeilen
    fallen damit aus dem Vorlauf, statt ihn um den Faktor 100 anzuheben.
    """
    conn = _leeres_protokoll()
    _protokoll(conn, "misch", [105456] * 8, tage=None)      # Bestandsabruf
    _protokoll(conn, "misch", [64386], tage=None)           # Rueckimport-Altzeile

    # Als Zeitraum-Abruf gibt es keinen brauchbaren Vorlauf — also kein Urteil,
    # statt eines Urteils an der falschen Groesse.
    vermerk = plausibilitaet.pruefe(conn, "misch", anzahl=650, tage=7.0)
    assert vermerk["vorlauf_laeufe"] == 0
    assert vermerk["untergrenze"] == 0

    # Als Bestandsabruf traegt derselbe Vorlauf sehr wohl.
    with pytest.raises(plausibilitaet.AbrufUnplausibel):
        plausibilitaet.pruefe(conn, "misch", anzahl=650, tage=None)


def test_ohne_vorlauf_wird_eine_neue_stadt_nicht_auf_verdacht_beanstandet():
    """Eine frisch angebundene Stadt hat keinen Vorlauf — und bekommt kein Urteil.

    Das ist Absicht und keine Luecke: ohne eigene Vergangenheit gibt es nichts,
    woran sich eine Menge messen liesse. Der vollstaendig leere Abruf bleibt
    davon unberuehrt, den faengt ``tracker.run`` ohne jeden Vorlauf (H-02b).
    """
    conn = _leeres_protokoll()
    _protokoll(conn, "neustadt", [12, 11, 13, 12], tage=7.0)   # vier, noetig sind fuenf

    vermerk = plausibilitaet.pruefe(conn, "neustadt", anzahl=1, tage=7.0)
    assert vermerk["untergrenze"] == 0
    assert vermerk["basis"] is None


def test_ein_gerissener_mengenriegel_endet_wie_ein_ausfall(tmp_path, monkeypatch):
    """Der Riegel im Betrieb — und zwar an BERLIN, das die alte Pruefung nie sah.

    ``open311.pruefe_plausibel`` sitzt in ``hole_zeitraum``. Berlins Feed hat
    seinen eigenen Leser in ``quellen.BerlinQuelle`` und laeuft dort nie durch;
    Berlins Quelle liefert seit dem 23.04.2026 nachweislich 0 Meldungen je Lauf,
    und nichts hat je angeschlagen. Dieselbe Fehlerklasse wie T-55 und T-66.

    Geprueft wird das Verhalten, nicht die Rechnung: ein gerissener Riegel muss
    denselben Weg nehmen wie ein leerer Abruf — Rueckgabewert 1, Fehlermarke im
    Abrufprotokoll, KEIN Neuaufbau der Zellen, also auch kein Push.
    """
    db_pfad = tmp_path / "riegel.db"
    monkeypatch.setattr(tracker, "DB_PATH", db_pfad)

    conn = sqlite3.connect(db_pfad)
    tracker.init_db(conn)
    _protokoll(conn, "berlin", [100] * 8, tage=None)
    conn.close()

    # Drei muellnahe Meldungen in einer Zelle. Ohne Riegel waere das ein
    # geglueckter Lauf, der eine Zelle anlegt — der Vorlauf sagt aber 100.
    duenn = [{"id": str(i), "kategorie": "Sperrmüll", "betreff": "",
              "bezirk": "Mitte", "lat": 52.5, "lon": 13.4, "status": "offen",
              "erstellungsDatum": f"0{i + 1}.01.2026"} for i in range(3)]
    monkeypatch.setattr(tracker, "fetch_meldungen", lambda: duenn)

    assert tracker.run() == 1, (
        "Ein Abruf mit 3 von 100 erwarteten Meldungen muss wie ein Ausfall "
        "enden. Kommt hier 0 zurueck, hat der Lauf als geglueckt gegolten und "
        "der Launcher haette gepusht.")

    conn = sqlite3.connect(db_pfad)
    zellen = conn.execute("SELECT COUNT(*) FROM hotspots").fetchone()[0]
    letzte = conn.execute(
        "SELECT count_total, plausibel FROM fetch_log ORDER BY id DESC LIMIT 1"
    ).fetchone()
    conn.close()

    assert zellen == 0, (
        "Aus einem unplausiblen Abruf darf nichts neu aufgebaut werden (H-02b).")
    assert letzte == (-1, 0), (
        "Der gerissene Lauf muss als Fehlermarke und als unplausibel im "
        "Abrufprotokoll stehen, sonst zieht er den eigenen Massstab nach unten.")


# ── Der bekannte Rest: was diese Datei NICHT beweist ─────────────────────────

def test_die_alte_schwelle_bleibt_im_rueckimport_die_einzige_pruefung():
    """Kennzeichnung des Restes, den T-79 NICHT geschlossen hat.

    ``rueckimport.py`` ruft ``open311.hole_zeitraum`` unmittelbar und erreicht
    ``tracker.run`` nie. Dort gilt weiterhin die alte, dichteabhaengige Regel
    mit ihrem Sockel ``max(1, ...)`` — und sie kann dort auch nicht durch den
    Mengenriegel ersetzt werden: der misst am eigenen Vorlauf, und beim
    erstmaligen Rueckimport einer Stadt gibt es noch keinen.

    Tragweite, ehrlich: der Rueckimport ist ein beaufsichtigter Einzelvorgang
    mit eigenem Bericht, kein taeglicher Lauf. Der taegliche Lauf, um den es in
    T-79 ging, ist abgedeckt. Dieser Test haelt den Rest fest, damit er nicht
    fuer erledigt gehalten wird.
    """
    jetzt = datetime(2026, 8, 17)
    woche = open311.Zeitraum(von=jetzt - timedelta(days=8),
                             bis=jetzt - timedelta(days=1))

    # Bei duenner Dichte verlangt die alte Regel weiterhin genau eine Meldung.
    open311.pruefe_plausibel([{}], woche, 0.09, jetzt=jetzt)
