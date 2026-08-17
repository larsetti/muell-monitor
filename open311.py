#!/usr/bin/env python3
r"""
Open311-Adapter (T-49, 15.08.2026)
===================================
Anbindung an den offenen Standard Open311 (GeoReport v2). Erste angebundene
Stadt ist Koeln; Bonn faehrt dieselbe Schnittstelle und ist deshalb spaeter nur
eine zweite Eintragung in ``quellen.py``, kein zweiter Adapter.

Dieses Modul kennt die SCHNITTSTELLE. Was eine Stadt daraus macht — welche
Kategorien als muellnah gelten, welches Wiederkehr-Fenster gilt, ob der
Freitext uebernommen wird — steht in ``quellen.py``.

Am echten Endpunkt gemessen (15.08.2026, 4.000 Meldungen vom 01.07. bis
12.08.2026, dazu Stichproben aus 2023, 2024 und 2026)
------------------------------------------------------------------------
Nicht aus der Standard-Doku abgeleitet, sondern abgerufen und ausgezaehlt:

  Feld                 belegt    Anmerkung
  service_request_id   100,0 %   Bauart "15405-2026", nicht rein numerisch
  requested_datetime   100,0 %   ISO 8601 mit Zeitzone, "2026-07-01T03:27:49+02:00"
  service_code         100,0 %   gepflegte Systematik, 42 Eintraege in services.json
  service_name         100,0 %   Klartext zum Code, kontrolliertes Vokabular
  status               100,0 %   nur "open" und "closed"
  lat / long           100,0 %   Dezimalgrad
  address_string       100,0 %   MIT Hausnummer, siehe unten
  title                100,0 %   nur "#<id> <service_name>", kein eigener Inhalt
  updated_datetime     100,0 %   bei "closed" der Zeitpunkt der Erledigung
  description           92,6 %   frei geschriebener Buergertext
  media_url             73,6 %   Bildadresse
  status_note            0,0 %   durchgehend leer

Drei Abweichungen von der Standard-Doku, die beim Zuordnen zaehlen:
  1. Das Adressfeld heisst ``address_string``, nicht ``address``. Ein Adapter,
     der ``address`` liest, bekommt bei 100 Prozent der Meldungen None und
     merkt es nicht.
  2. ``title`` ist kein Freitext, sondern aus Kennung und Kategorie
     zusammengesetzt. Es traegt nichts bei und wird nicht uebernommen.
  3. ``agency_responsible``, ``expected_datetime``, ``zipcode`` und
     ``service_notice`` kommen gar nicht vor.

Die Falle mit der Seitenzahl (Auflage 3c aus T-49)
--------------------------------------------------
``page`` ist NULLBASIERT. Ein Abruf ohne ``page`` liefert deshalb nicht etwa
alles und auch keine leere Antwort, sondern **stumm die erste Seite mit
hoechstens 100 Meldungen** — gemessen: der Abruf 01.09. bis 08.09.2024 ohne
``page`` gab 100 Meldungen zurueck, dieselbe Woche durchgeblaettert gab 531.
Wer nicht blaettert, importiert also 19 Prozent des Zeitraums und haelt das fuer
den ganzen. Das ist derselbe Fehlertyp wie die stille Nicht-Ersetzung aus T-39:
das Ergebnis sieht aus wie ein geglueckter Lauf.

``page_size`` wird ignoriert, die Seitengroesse liegt fest bei 100. Eine leere
Seite ist das regulaere Ende eines Zeitraums. Eine leere ERSTE Seite dagegen ist
kein Ergebnis, sondern ein Fehler — deshalb ``pruefe_plausibel``.
"""

import logging
import time
from dataclasses import dataclass
from datetime import datetime, timedelta

import requests

log = logging.getLogger(__name__)

# Gemessen: page_size wird ignoriert, jede volle Seite hat genau 100 Eintraege.
SEITENGROESSE = 100

# Obergrenze gegen eine Schnittstelle, die immer dieselbe Seite liefert. Bei 100
# Meldungen je Seite sind das 500.000 Meldungen — mehr als jede der beteiligten
# Staedte je hatte, also kein echter Zeitraum stoesst daran.
MAX_SEITEN = 5000

# Hoeflichkeitspause zwischen zwei Seitenabrufen. Der Rueckimport zieht rund 700
# Seiten; ohne Pause waere das ein Lastspitzen-Abruf auf einem Buergerportal.
PAUSE_SEKUNDEN = 0.3

# Die Kontaktangabe im User-Agent ist die im Impressum genannte Projektadresse,
# nicht die private des Betreibers. Sie steht in einem oeffentlichen Speicher und
# geht bei jedem Abruf an ein fremdes Buergerportal — eine private Adresse waere
# an beiden Stellen falsch aufgehoben. Am 17.08.2026 ausgetauscht.
KOPFZEILEN = {
    "User-Agent": "Muell-Monitor/1.0 (offene Daten, Kontakt: info@muell-monitor.de)",
    "Accept": "application/json",
}


class AbrufFehler(RuntimeError):
    """Die Schnittstelle war nicht erreichbar oder antwortete unbrauchbar."""


class AbrufUnplausibel(AbrufFehler):
    """Der Abruf lief technisch durch, das Ergebnis ist aber nicht glaubhaft.

    Auflage 3c aus T-49: eine leere oder auffaellig duenne Antwort darf NICHT
    als "nichts passiert" durchgehen. Sie wird wie ein Ausfall behandelt, damit
    ein Lauf auffaellt, statt still einen halben Bestand zu importieren.
    """


@dataclass(frozen=True)
class Zeitraum:
    """Ein halboffener Abrufzeitraum [von, bis)."""
    von: datetime
    bis: datetime

    @property
    def tage(self) -> float:
        return max(0.0, (self.bis - self.von).total_seconds() / 86400)

    def als_parameter(self) -> dict:
        return {
            "start_date": self.von.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "end_date": self.bis.strftime("%Y-%m-%dT%H:%M:%SZ"),
        }

    def __str__(self) -> str:
        return f"{self.von:%Y-%m-%d} bis {self.bis:%Y-%m-%d}"


# Wiederholversuche bei Fehlern, die voruebergehend sein KOENNEN: abgebrochene
# Verbindungen, Zeitueberschreitungen, Serverfehler. Sie sind noetig, weil sonst
# ein einzelner Schluckauf des Netzes als dauerhafte Luecke in der Quelle
# vermerkt wird — genau das ist am 15.08.2026 beim ersten echten Rueckimport
# passiert, als eine zweite Sitzung parallel dieselbe Schnittstelle abfragte und
# der Server eine Verbindung fallen liess.
#
# Der dauerhafte HTTP 500 einzelner Seiten (siehe hole_zeitraum) ueberlebt die
# Wiederholung unveraendert und wird danach zu Recht als Luecke gefuehrt. Die
# Wiederholung kostet in dem Fall zwei zusaetzliche Anfragen; das ist der Preis
# dafuer, die beiden Faelle ueberhaupt unterscheiden zu koennen.
VERSUCHE = 3
PAUSE_BEI_FEHLER = 2.0


def hole_seite(url: str, zeitraum: Zeitraum | None, seite: int,
               timeout: int = 60, sitzung=None, versuche: int = VERSUCHE,
               pause_bei_fehler: float = PAUSE_BEI_FEHLER) -> list[dict]:
    """Eine einzelne Seite. ``seite`` ist nullbasiert und immer ausgeschrieben.

    Der Parameter hat bewusst keinen Vorgabewert: eine vergessene Seitenzahl
    soll ein Programmierfehler sein und nicht stumm Seite 0 abrufen.

    Wiederholt bei Verbindungs- und Serverfehlern, nicht bei 4xx — eine
    abgelehnte Anfrage wird durch Wiederholen nicht richtiger.
    """
    parameter = {"page": seite}
    if zeitraum is not None:
        parameter.update(zeitraum.als_parameter())
    hole = (sitzung or requests).get

    letzter_fehler = None
    for versuch in range(1, max(1, versuche) + 1):
        try:
            antwort = hole(url, params=parameter, headers=KOPFZEILEN,
                           timeout=timeout)
        except requests.RequestException as fehler:
            letzter_fehler = AbrufFehler(
                f"Seite {seite} nicht abrufbar: {fehler}")
        else:
            if antwort.status_code == 200:
                break
            letzter_fehler = AbrufFehler(
                f"Seite {seite}: HTTP {antwort.status_code}")
            if antwort.status_code < 500:
                # 4xx wiederholt sich genauso. Sofort aufgeben.
                raise letzter_fehler
        if versuch < max(1, versuche):
            log.info("Seite %d, Versuch %d von %d gescheitert (%s) — neuer "
                     "Versuch in %.1f s.", seite, versuch, versuche,
                     letzter_fehler, pause_bei_fehler)
            if pause_bei_fehler:
                time.sleep(pause_bei_fehler)
    else:
        raise letzter_fehler

    try:
        daten = antwort.json()
    except ValueError as fehler:
        raise AbrufFehler(f"Seite {seite}: keine gueltige JSON-Antwort "
                          f"({fehler})") from fehler
    if isinstance(daten, dict):
        # Der Standard sieht bei Fehlern ein Objekt statt einer Liste vor.
        raise AbrufFehler(f"Seite {seite}: Objekt statt Liste erhalten "
                          f"({str(daten)[:200]})")
    if not isinstance(daten, list):
        raise AbrufFehler(f"Seite {seite}: unerwarteter Antworttyp "
                          f"{type(daten).__name__}")
    return daten


def _kennung(meldung: dict) -> str:
    return str(meldung.get("service_request_id") or "")


def pruefe_plausibel(meldungen: list[dict], zeitraum: Zeitraum | None,
                     mindest_je_tag: float, jetzt: datetime = None) -> None:
    """Auflage 3c: eine leere Antwort ist kein Ergebnis.

    Geprueft wird nur, was sich ohne Kenntnis der Quelle pruefen laesst:

      * Ein Zeitraum, der VOLLSTAENDIG in der Vergangenheit liegt, hatte
        nachweislich Meldungen — Koeln lag im gemessenen Zeitraum bei 93
        Meldungen je Tag. Kommen weniger als ``mindest_je_tag`` je Tag zurueck,
        ist der Abruf unglaubwuerdig und wird als Fehler behandelt.
      * Ein Zeitraum, der in die Zukunft reicht oder gerade erst begonnen hat,
        darf duenn sein. Dort greift nur die Untergrenze von einer Meldung.

    ``mindest_je_tag`` ist bewusst weit unter dem gemessenen Wert angesetzt. Die
    Pruefung soll einen Ausfall erkennen, nicht einen ruhigen Tag beanstanden.
    """
    jetzt = jetzt or datetime.utcnow()
    anzahl = len(meldungen)

    if zeitraum is None:
        if anzahl == 0:
            raise AbrufUnplausibel(
                "Der Abruf ohne Zeitraum lieferte 0 Meldungen. Die Quelle "
                "liefert im Normalbetrieb die letzten 90 Tage; ein leeres "
                "Ergebnis ist ein Ausfall, kein Befund.")
        return

    reicht_in_die_zukunft = zeitraum.bis > jetzt
    if reicht_in_die_zukunft:
        # Nur der Teil bis jetzt kann Meldungen tragen.
        tage = max(0.0, (min(zeitraum.bis, jetzt) - zeitraum.von).total_seconds() / 86400)
    else:
        tage = zeitraum.tage

    if tage <= 0:
        return

    erwartet = max(1, int(tage * mindest_je_tag))
    if reicht_in_die_zukunft:
        # Ein angebrochener Tag darf leer sein — aber nicht ein Zeitraum, der
        # schon mehrere volle Tage zurueckreicht.
        erwartet = 1 if tage < 2 else max(1, int((tage - 1) * mindest_je_tag))

    if anzahl < erwartet:
        raise AbrufUnplausibel(
            f"Zeitraum {zeitraum}: {anzahl} Meldungen erhalten, mindestens "
            f"{erwartet} erwartet ({mindest_je_tag} je Tag auf {tage:.1f} Tage). "
            f"Eine so duenne Antwort wird NICHT als 'keine Meldungen' gewertet, "
            f"sondern als Ausfall. Pruefen: ist die Schnittstelle erreichbar, "
            f"steht der Zeitraum richtig, wurde die Seitenzahl mitgegeben?")


def hole_zeitraum(url: str, zeitraum: Zeitraum | None, mindest_je_tag: float,
                  max_seiten: int = MAX_SEITEN, pause: float = PAUSE_SEKUNDEN,
                  jetzt: datetime = None, sitzung=None,
                  fortschritt=None, luecken: list = None,
                  max_luecken_am_stueck: int = 5,
                  versuche: int = VERSUCHE,
                  pause_bei_fehler: float = PAUSE_BEI_FEHLER) -> list[dict]:
    """Alle Meldungen eines Zeitraums, ueber alle Seiten.

    Blaettert IMMER, auch wenn die erste Seite nicht voll ist — der Aufwand ist
    eine Anfrage, der Fehler ohne Blaettern waere ein stumm halbierter Import.

    Bricht ab, wenn eine Seite dieselben Kennungen liefert wie die vorige. Ohne
    diesen Riegel wuerde eine Schnittstelle, die ``page`` ignoriert, endlos
    dieselbe Seite liefern und der Import liefe voll Dubletten.

    ``luecken``: wird eine Liste uebergeben, gilt eine unlesbare Seite NICHT
    mehr als Abbruchgrund. Sie wird stattdessen dort vermerkt und das
    Blaettern geht weiter. Gedacht fuer den Rueckimport — siehe unten, warum
    das noetig ist und warum es trotzdem kein Durchwinken ist.

    Am 15.08.2026 am echten Endpunkt gemessen: einzelne Seiten aelterer
    Zeitraeume antworten DAUERHAFT mit HTTP 500, waehrend die Nachbarseiten
    normal liefern (Seite 11 von 14.09. bis 14.10.2024 scheitert bei jedem
    Versuch, Seite 10 und 12 nicht). Der Fehler haengt am Inhalt, nicht an der
    Last: vier Wiederholungen scheitern gleich, und dieselben Meldungen fallen
    auch dann aus, wenn man den Zeitraum kleiner schneidet. Es ist also eine
    echte Luecke in der Quelle und kein Ausfall bei uns.

    Ohne ``luecken`` bleibt es beim Abbruch. Das ist die richtige Vorgabe fuer
    den taeglichen Lauf: dort ist eine unlesbare Seite ein Grund, nichts zu
    tun. Der Rueckimport dagegen soll wegen einer kaputten Seite nicht zwei
    Jahre Tiefe liegen lassen — er vermerkt sie und weist sie im Bericht aus.
    """
    alle: list[dict] = []
    gesehen: set[str] = set()
    luecken_am_stueck = 0
    for seite in range(0, max_seiten):
        try:
            daten = hole_seite(url, zeitraum, seite, sitzung=sitzung,
                               versuche=versuche,
                               pause_bei_fehler=pause_bei_fehler)
            luecken_am_stueck = 0
        except AbrufFehler as fehler:
            if luecken is None:
                raise
            luecken.append({"zeitraum": str(zeitraum), "seite": seite,
                            "grund": str(fehler)})
            luecken_am_stueck += 1
            log.warning("Zeitraum %s, Seite %d nicht lesbar (%s). Die Seite "
                        "wird als LUECKE vermerkt, nicht als Ende des "
                        "Zeitraums.", zeitraum, seite, fehler)
            if luecken_am_stueck >= max_luecken_am_stueck:
                log.error("%d Seiten am Stueck nicht lesbar — hier ist nicht "
                          "eine Seite kaputt, sondern die Quelle weg. Abbruch.",
                          luecken_am_stueck)
                raise
            if pause:
                time.sleep(pause)
            continue
        if not daten:
            break
        kennungen = {_kennung(m) for m in daten}
        neue = kennungen - gesehen
        if not neue:
            log.warning("Seite %d lieferte ausschliesslich bereits gesehene "
                        "Kennungen — die Schnittstelle blaettert nicht. Abbruch "
                        "nach %d Meldungen.", seite, len(alle))
            break
        for meldung in daten:
            if _kennung(meldung) not in gesehen:
                gesehen.add(_kennung(meldung))
                alle.append(meldung)
        if fortschritt:
            fortschritt(seite, len(alle))
        if len(daten) < SEITENGROESSE:
            # Angebrochene Seite: das ist das Ende des Zeitraums. Eine weitere
            # Anfrage waere sicherer, kostet aber bei 700 Seiten 700 Anfragen.
            break
        if pause:
            time.sleep(pause)
    else:
        log.warning("Obergrenze von %d Seiten erreicht — der Zeitraum ist "
                    "moeglicherweise nicht vollstaendig abgerufen.", max_seiten)

    pruefe_plausibel(alle, zeitraum, mindest_je_tag, jetzt=jetzt)
    return alle


# ── Feld-Zuordnung ───────────────────────────────────────────────────────────

def parse_zeitpunkt(wert) -> datetime | None:
    """ISO 8601 einlesen. Der Rueckgabewert traegt die Zeitzone der Quelle.

    Die Quelle liefert "2026-07-01T03:27:49+02:00". Gespeichert wird wie bei
    Berlin nur das Datum, die Uhrzeit faellt in ``datum_aus`` weg — deshalb
    wird hier bewusst nicht auf UTC umgerechnet: das Meldedatum ist das Datum
    vor Ort, und eine Umrechnung wuerde Meldungen kurz nach Mitternacht auf den
    Vortag schieben.
    """
    if not wert:
        return None
    text = str(wert).strip().replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def datum_aus(meldung: dict) -> str | None:
    zeitpunkt = parse_zeitpunkt(meldung.get("requested_datetime"))
    return zeitpunkt.strftime("%Y-%m-%d") if zeitpunkt else None


def zerlege_adresse(address_string: str) -> tuple[str, str, str]:
    """``address_string`` in (Strasse ohne Hausnummer, PLZ, Stadtteil).

    Gemessene Bauarten:
        "50823 Koeln - Ehrenfeld, Subbelrather Str. 167"
        "50739 Koeln, Wilensteinweg 13"

    Die Hausnummer steht bei 3.998 von 4.000 Meldungen am Ende. Sie faellt hier
    und nicht spaeter: im Mai 2026 sind aus dem Berliner Bestand 105.100
    Hausnummern entfernt worden, weil sie einmal drin waren. Was gar nicht erst
    geschrieben wird, muss niemand nachtraeglich suchen.
    """
    text = (address_string or "").strip()
    if not text:
        return "", "", ""

    plz = ""
    stadtteil = ""
    kopf, trenner, rest = text.partition(",")
    if not trenner:
        # Kein Komma: alles ist Strasse, PLZ und Stadtteil fehlen.
        return _hausnummer_entfernen(text), "", ""

    teile = kopf.split()
    if teile and teile[0].isdigit() and len(teile[0]) == 5:
        plz = teile[0]
        rest_kopf = " ".join(teile[1:])
        # "Koeln - Ehrenfeld" -> Stadtteil "Ehrenfeld"; "Koeln" -> kein Stadtteil
        if "-" in rest_kopf:
            stadtteil = rest_kopf.split("-", 1)[1].strip()
    strasse = _hausnummer_entfernen(rest.strip())
    return strasse, plz, stadtteil


def _hausnummer_entfernen(strasse: str) -> str:
    """Hausnummer am Ende entfernen — dieselbe Regel wie in ``tracker.run``.

    Bewusst dieselbe Bauart wie dort (Ziffernfolge, optionaler Buchstabe,
    optionale Spanne), damit nicht zwei Regeln auseinanderlaufen.

    Danach der Sonderfall, der am 15.08.2026 im echten Bestand aufgefallen ist:
    einzelne Koelner Adressen tragen ueberhaupt keinen Strassennamen, sondern
    nur die Hausnummer ("50969 Koeln - Zollstock, 4"). Die Regel oben greift
    dort nicht, weil ihr das Leerzeichen davor fehlt — uebrig bliebe eine
    "Strasse" namens "4", also genau eine Hausnummer in dem Feld, aus dem im
    Mai 2026 105.100 Stueck entfernt worden sind. Was nur aus Ziffern und einem
    angehaengten Buchstaben besteht, ist kein Strassenname und faellt.
    """
    import re
    ohne = re.sub(r"\s+\d+\s*[a-zA-Z]?(\s*[-/]\s*\d+\s*[a-zA-Z]?)?\s*$", "",
                  (strasse or "").strip())
    ohne = ohne.strip().rstrip(",").strip()
    if re.fullmatch(r"\d+\s*[a-zA-Z]?(\s*[-/]\s*\d+\s*[a-zA-Z]?)?", ohne):
        return ""
    return ohne


# Die Quelle kennt genau zwei Werte. Uebersetzt wird in das Vokabular, das im
# Berliner Bestand steht, damit die Spalte nicht zwei Sprachen fuehrt.
STATUS_UEBERSETZUNG = {
    "closed": "Erledigt",
    "open": "In Bearbeitung",
}


def status_aus(meldung: dict) -> str:
    roh = str(meldung.get("status") or "").strip().lower()
    return STATUS_UEBERSETZUNG.get(roh, roh)


def zeitraum_tage(bis: datetime, tage: int) -> Zeitraum:
    """Hilfsgroesse fuer den laufenden Betrieb: die letzten ``tage`` Tage."""
    return Zeitraum(von=bis - timedelta(days=tage), bis=bis)
