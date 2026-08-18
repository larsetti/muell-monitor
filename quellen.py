#!/usr/bin/env python3
r"""
Quellen-Konfiguration (T-49, 15.08.2026)
=========================================
Bis hierher war die Stadt eine Modulkonstante in ``tracker.py``. Wer eine
zweite Stadt abrufen wollte, haette den Wert im Quelltext aendern muessen — und
damit den Berliner Betrieb angefasst, um Koeln zu bedienen. Diese Datei loest
das auf: jede Stadt ist ein Eintrag, der Tracker bekommt ihn als Parameter.

    python tracker.py                 laeuft Berlin, unveraendert
    python tracker.py --stadt koeln   laeuft Koeln

Es gibt weiterhin genau EINEN Tracker. Was sich je Stadt unterscheidet, steht
hier und nirgends sonst:

  * woher die Meldungen kommen (Berliner Ordnungsamt-Feed oder Open311)
  * welche Meldungen als muellnah gelten
  * wie eine Rohmeldung auf unsere Spalten faellt
  * wie gross das Wiederkehr-Fenster ist
  * ob der Freitext ueberhaupt uebernommen wird

Berlin bleibt dabei unangetastet. Die Schnittstelle liefert seit dem 22.04.2026
nichts mehr; kommt sie zurueck, laeuft der Berliner Eintrag ohne Umbau weiter.

Bonn faehrt dieselbe Open311-Schnittstelle und waere hier ein dritter Eintrag
mit eigener Kategorienliste. Bonn ist in diesem Auftrag ausdruecklich NICHT
gebaut — die Vorrichtung steht, die Entscheidung nicht.
"""

import logging
import re
from datetime import datetime

import betreff_filter
import open311

log = logging.getLogger(__name__)


class Quelle:
    """Eine Datenquelle. Eine Stadt, eine Schnittstelle, eine Kategorienliste."""

    # Kennung der Stadt, zugleich Wert der Spalte ``stadt`` und Praefix der
    # Meldungs-Kennung.
    stadt: str = ""
    name: str = ""

    # Wiederkehr-Fenster in Tagen. Zwei Meldungen derselben Zelle innerhalb
    # dieser Spanne gelten als Wiederkehr. Siehe die Unterklassen — der Wert
    # ist stadtspezifisch und NICHT aus Berlin uebernehmbar.
    wiederkehr_fenster_tage: int = 21

    # Wird der Buerger-Freitext in die Datenbank uebernommen?
    freitext_uebernehmen: bool = True

    # Darf die Abwesenheit einer Meldung im Abruf als "aus der Quelle
    # verschwunden" gewertet werden (A-4, Schritt 1)?
    #
    # Das geht NUR, wenn ein Abruf den vollstaendigen Bestand der Quelle
    # liefert. Berlin tat genau das: ein rollendes Fenster, ein Abruf, alles
    # drin, was es noch gab — wer fehlte, war geloescht.
    #
    # Eine Open311-Quelle liefert stattdessen einen ZEITRAUM. Wer in einem
    # Abruf der letzten drei Tage fehlt, ist nicht geloescht, sondern schlicht
    # aelter. Wuerde der Abgleich hier laufen, waere nach dem ersten Lauf der
    # gesamte uebrige Bestand als weggefallen vorgemerkt und 30 Tage spaeter
    # geloescht — beim Rueckimport also genau die 24 Monate Tiefe, fuer die er
    # gemacht wird. Der 20-Prozent-Riegel wuerde das bremsen und damit die
    # Loeschroutine dauerhaft blockieren; aus stillem Datenverlust wuerde ein
    # dauerhafter Rechtsverstoss. Dieselbe Ueberlegung wie Befund 2 in T-51,
    # nur eine Ebene tiefer: dort ging es um die fehlende Stadt, hier um die
    # fehlende Vollstaendigkeit.
    #
    # Die zweite Frist (Reduktion auf Aggregate nach 24 Monaten) laeuft davon
    # unberuehrt weiter und gilt fuer jede Stadt.
    quellabgleich_moeglich: bool = True

    def hole_meldungen(self, zeitraum=None) -> list[dict]:
        raise NotImplementedError

    def fenster_tage(self, zeitraum=None) -> float | None:
        """Fensterlänge des Abrufs in Tagen, ``None`` bei einem Bestandsabruf.

        Der Mengenriegel aus T-79 vergleicht einen Abruf mit dem eigenen
        Vorlauf dieser Stadt. Dafür muss er die beiden Abruf-Arten
        auseinanderhalten: ein Bestandsabruf ist mit einem Bestand zu
        vergleichen, ein Zeitraum-Abruf je Tag. Die Vorgabe ist der
        Bestandsabruf, weil das die ältere und die Berliner Bauart ist.
        """
        return None

    def kennung(self, meldung: dict) -> str:
        raise NotImplementedError

    def aufbereiten(self, meldung: dict, jetzt: str) -> dict | None:
        """Rohmeldung auf unsere Spalten abbilden.

        Rueckgabe ist ``None``, wenn die Meldung nicht muellnah ist und deshalb
        gar nicht erst gespeichert wird (Datenminimierung), sonst ein dict mit
        den Schluesseln id, datum, kategorie, betreff, bezirk, lat, lon,
        status, strasse, plz.
        """
        raise NotImplementedError


# ── Berlin ───────────────────────────────────────────────────────────────────

class BerlinQuelle(Quelle):
    """Der Berliner Ordnungsamt-Feed, unveraendert.

    Der gesamte Inhalt dieser Klasse ist aus ``tracker.run`` hierher gezogen,
    Zeile fuer Zeile und ohne fachliche Aenderung. Es gibt bewusst keinen
    zweiten, kopierten Tracker: der Unterschied zwischen den Staedten liegt in
    dieser Datei, der Ablauf in ``tracker.run``.
    """

    stadt = "berlin"
    name = "Berlin"

    # 14 Tage regulaere Entsorgung plus 7 Tage Puffer, so seit dem ersten Bau.
    wiederkehr_fenster_tage = 21

    # Berlin liefert ``kategorie`` in ALLEN 82.057 Zeilen leer (T-63, nachgemessen
    # am 17.08.2026; vorher stand hier 82.780, die Zahl vor der Loeschroutine vom
    # 15.08.). Die Abfallgruppe
    # von 85,9 Prozent der Meldungen stammt allein aus dem Freitext — er ist
    # hier nicht entbehrlich, sondern die einzige Kategorienquelle. Deshalb der
    # Filter aus A-14 und nicht der Verzicht.
    freitext_uebernehmen = True

    # Ein Berliner Abruf lieferte den vollstaendigen Bestand. Wer darin fehlte,
    # war aus der Quelle entfernt. Deshalb greift der Abgleich hier — und nur
    # hier.
    quellabgleich_moeglich = True

    def hole_meldungen(self, zeitraum=None) -> list[dict]:
        # Spaeter Import, weil ``tracker`` seinerseits diese Datei liest.
        # Zugleich der Grund, warum hier ueber das Modul und nicht ueber einen
        # gebundenen Namen aufgerufen wird: die Tests ersetzen
        # ``tracker.fetch_meldungen`` und muessen weiter greifen.
        import tracker
        return tracker.fetch_meldungen()

    def kennung(self, meldung: dict) -> str:
        import tracker
        return tracker.make_id(meldung, self.stadt)

    def aufbereiten(self, meldung: dict, jetzt: str) -> dict | None:
        import tracker

        kennung = self.kennung(meldung)
        lat, lon = tracker.extract_coords(meldung)

        datum = (meldung.get("erstellungsDatum") or meldung.get("datum") or
                 meldung.get("erstelltAm") or meldung.get("created_at") or jetzt[:10])
        datum_roh = datum
        if datum and len(datum) >= 10 and datum[2] == ".":
            # Bauart "DD.MM.YYYY ..." aus der Schnittstelle
            try:
                datum = datetime.strptime(datum[:10], "%d.%m.%Y").strftime("%Y-%m-%d")
            except ValueError:
                log.warning("Unbekanntes Datumsformat fuer Meldung %s: %r",
                            kennung, datum_roh)
                datum = jetzt[:10]

        # DSGVO-Datenminimierung: Nicht-Muell-Meldungen nicht persistieren.
        # Sie koennen Beschwerden gegen identifizierbare Personen enthalten.
        if not tracker.is_muell(meldung):
            return None

        # H-02: strasse-Rueckfall strikt — kein Rueckgriff auf generische
        # Adressfelder (adresse, address, ort), die Hausnummern enthalten
        # koennten.
        strasse = meldung.get("strasse") or meldung.get("street") or ""
        strasse = re.sub(r"\s+\d+[a-zA-Z]?(\s*[-/]\s*\d+[a-zA-Z]?)?\s*$", "",
                         strasse).strip()
        plz = meldung.get("plz") or meldung.get("postleitzahl") or meldung.get("zip") or ""

        # A-14: Betrefftexte, die eine Lebenssituation offenlegen, werden VOR
        # dem Schreiben entschaerft — nicht erst beim Export. Einmal
        # gespeichert waere die Angabe im Bestand, in jeder Sicherung und in
        # jedem Klon.
        kategorie = meldung.get("kategorie") or meldung.get("category", "")
        betreff_roh = meldung.get("betreff") or meldung.get("subject", "")
        betreff, regeln = betreff_filter.entschaerfe(betreff_roh, kategorie,
                                                     stadt=self.stadt)
        if regeln:
            # Bewusst ohne den Originaltext im Protokoll — sonst stuende die
            # Angabe in tracker.log, die der Filter gerade aus der Datenbank
            # heraushaelt.
            log.info("Betreff entschaerft (Meldung %s, Regel %s)",
                     kennung, ",".join(regeln))

        return {
            "id": kennung,
            "datum": datum,
            "kategorie": kategorie,
            "betreff": betreff,
            "bezirk": meldung.get("bezirk") or meldung.get("district", ""),
            "lat": lat,
            "lon": lon,
            "status": meldung.get("status", ""),
            "strasse": strasse,
            "plz": str(plz),
        }


# ── Koeln (Open311) ──────────────────────────────────────────────────────────

# Welche Kategorien der Stadt als muellnah gelten, und auf welche unserer
# Abfallgruppen sie fallen. Grundlage ist services.json (42 Eintraege, am
# 15.08.2026 abgerufen) und die Auszaehlung von 4.000 echten Meldungen.
#
# Der Wert ist die Abfallgruppe aus export_html.KATEGORIE_GRUPPEN oder None.
# None heisst: muellnah ja, Gruppe offen. Das ist Absicht — eine falsche
# Zuordnung waere schlechter als eine fehlende, weil sie in der Karte als
# Aussage erscheint.
KOELN_KATEGORIEN = {
    # code       (Bezeichnung der Stadt,                     unsere Gruppe)
    "1.1":     ("Wilder Müll",                              "illegal"),
    "2.3":     ("Schrott-Kfz",                              "schrottfahrzeug"),
    # Lars-Entscheidung 15.08.2026: eigene, siebte Abfallgruppe statt
    # unzugeordnet oder falsch einsortiert. Siehe export_html.KATEGORIE_GRUPPEN.
    "1.5":     ("Schrottfahrräder",                         "schrottfahrrad"),
    "1.3.4":   ("Altkleidercontainer-Standort vermüllt",    "illegal"),
    "1.4.3":   ("Glascontainer-Standort vermüllt",          "illegal"),
}

# Ausdruecklich NICHT muellnah, obwohl die Bezeichnung danach klingt. Steht
# hier, damit die Entscheidung nachlesbar ist und nicht bei jeder Durchsicht
# neu getroffen wird.
KOELN_BEWUSST_AUSSEN = {
    "1.3.1": "Altkleidercontainer voll — ein voller, aber regulaerer Container "
             "ist kein Ablagerungsort. Die Standorte sind ortsfest und wuerden "
             "als dauerhafte Scheinschwerpunkte in der Karte stehen.",
    "1.4.1": "Glascontainer voll — dieselbe Begruendung.",
    "1.3.2": "Altkleidercontainer defekt — Sachschaden, keine Ablagerung.",
    "1.3.3": "Altkleidercontainer nicht von AWB — Ordnungsfrage, keine Ablagerung.",
    "1.6":   "Gully verstopft — Entwaesserung, kein Abfall.",
    "1.2":   "Graffiti — Sachbeschaedigung, kein Abfall.",
    "3.2":   "Koelner Gruen — Gruenpflege; Gartenabfall waere darin nicht "
             "zuverlaessig von Pflegemaengeln zu trennen.",
}


class Open311Quelle(Quelle):
    """Adapter fuer den offenen Standard Open311 (GeoReport v2).

    Bonn faehrt dieselbe Schnittstelle. Eine zweite Stadt ist deshalb eine
    zweite Eintragung mit eigener ``kategorien``-Tabelle, kein zweiter Adapter.
    """

    # Ein Open311-Abruf liefert einen Zeitraum, nicht den Bestand. Siehe die
    # ausfuehrliche Begruendung an der Basisklasse.
    quellabgleich_moeglich = False

    def __init__(self, stadt, name, url, kategorien, wiederkehr_fenster_tage,
                 mindest_meldungen_je_tag, freitext_uebernehmen=False,
                 erste_meldung=None, standard_zeitraum_tage=7):
        self.stadt = stadt
        self.name = name
        self.url = url
        self.kategorien = kategorien
        self.wiederkehr_fenster_tage = wiederkehr_fenster_tage
        self.mindest_meldungen_je_tag = mindest_meldungen_je_tag
        self.freitext_uebernehmen = freitext_uebernehmen
        # Aeltestes Datum, das die Quelle fuehrt. Nur zur Anzeige und als
        # Untergrenze im Rueckimport.
        self.erste_meldung = erste_meldung
        # Zeitraum des taeglichen Laufs, wenn keiner angegeben wird. Ohne
        # eigenen Wert liefert die Quelle ihren Standard — bei Koeln sind das
        # die letzten 90 Tage, also rund 8.400 Meldungen und 84 Seiten je Tag.
        # Das waere jeden Tag ein Rueckimport. Eine Woche reicht und ueberlappt
        # genug, dass nachtraeglich eingehende Meldungen nicht durchfallen.
        self.standard_zeitraum_tage = standard_zeitraum_tage

    # ── Abruf ────────────────────────────────────────────────────────────────

    def hole_meldungen(self, zeitraum=None, **kwargs) -> list[dict]:
        if zeitraum is None and self.standard_zeitraum_tage:
            zeitraum = open311.zeitraum_tage(datetime.utcnow(),
                                             self.standard_zeitraum_tage)
        return open311.hole_zeitraum(
            self.url, zeitraum, self.mindest_meldungen_je_tag, **kwargs)

    def fenster_tage(self, zeitraum=None) -> float | None:
        """Ein Open311-Abruf ist immer ein Zeitraum, nie ein Bestand.

        Ohne übergebenen Zeitraum nimmt ``hole_meldungen`` den Standard der
        Stadt — hier dieselbe Rechnung, damit im Abrufprotokoll die Länge
        steht, die tatsächlich abgerufen wurde.
        """
        if zeitraum is not None:
            return zeitraum.tage
        return float(self.standard_zeitraum_tage) if self.standard_zeitraum_tage else None

    def kennung(self, meldung: dict) -> str:
        import tracker
        roh = str(meldung.get("service_request_id") or "").strip()
        if not roh:
            raise ValueError(
                f"Meldung ohne service_request_id: {str(meldung)[:200]}. Ohne "
                f"stabile Kennung waere jeder Lauf ein Neuimport.")
        return f"{self.stadt}{tracker.STADT_TRENNER}{roh}"

    # ── Zuordnung ────────────────────────────────────────────────────────────

    def ist_muellnah(self, meldung: dict) -> bool:
        return str(meldung.get("service_code") or "") in self.kategorien

    def gruppe(self, meldung: dict) -> str:
        """Unsere Abfallgruppe, deterministisch aus dem Kategorie-Code.

        Kein Schluesselwort-Raten im Freitext wie bei Berlin: die Stadt liefert
        eine gepflegte Systematik fuer 100 Prozent der Meldungen mit. Genau das
        ist der Grund, warum der Freitext hier entbehrlich ist (A-18).
        """
        eintrag = self.kategorien.get(str(meldung.get("service_code") or ""))
        return (eintrag[1] or "") if eintrag else ""

    def aufbereiten(self, meldung: dict, jetzt: str) -> dict | None:
        if not self.ist_muellnah(meldung):
            return None

        lat = meldung.get("lat")
        lon = meldung.get("long")
        try:
            lat, lon = float(lat), float(lon)
        except (TypeError, ValueError):
            lat, lon = None, None

        datum = open311.datum_aus(meldung) or jetzt[:10]
        strasse, plz, stadtteil = open311.zerlege_adresse(
            meldung.get("address_string"))

        # A-17 (DSFA-Fassung 1.1): media_url wird weder uebernommen noch
        # abgerufen. In der Stichprobe trugen 73,6 Prozent der Meldungen eine
        # Bildadresse; Bilder aus dem oeffentlichen Raum zeigen Gesichter,
        # Kennzeichen und Hausfassaden. Das Feld wird hier nicht einmal
        # gelesen — es gibt keinen Zweig, der es doch noch mitnimmt.

        # A-18: der Freitext wird fuer diese Stadt NICHT uebernommen. Die
        # Begruendung steht in der Modul-Dokumentation und im Bericht; sie
        # stuetzt sich darauf, dass ``service_name`` die Kategorie fuer 100
        # Prozent der Meldungen traegt und der Freitext damit entbehrlich ist.
        # Gespeichert wird das kontrollierte Vokabular der Stadt.
        bezeichnung = str(meldung.get("service_name") or "").strip()
        if self.freitext_uebernehmen:
            bezeichnung = str(meldung.get("description") or "").strip() or bezeichnung

        # Auch das kontrollierte Vokabular laeuft durch den Filter. Es soll
        # heute nichts finden — aber der Weg ist derselbe wie bei Berlin, und
        # wenn eine Stadt ihre Kategorienliste um eine Formulierung erweitert,
        # die eine Lebenslage benennt, greift der Schutz ohne Codeaenderung.
        gruppe = self.gruppe(meldung)
        betreff, regeln = betreff_filter.entschaerfe(bezeichnung, gruppe,
                                                     stadt=self.stadt)
        if regeln:
            log.info("Betreff entschaerft (Meldung %s, Regel %s)",
                     self.kennung(meldung), ",".join(regeln))

        return {
            "id": self.kennung(meldung),
            "datum": datum,
            # Die Abfallgruppe steht hier als Klartext. export_html leitet die
            # Gruppe zur Anzeigezeit erneut aus kategorie+betreff ab; jeder
            # Gruppenname ist zugleich sein eigenes Schluesselwort, die
            # Zuordnung kommt also unveraendert wieder heraus. Ein Test haelt
            # das fuer jede Gruppe fest.
            "kategorie": gruppe,
            "betreff": betreff,
            "bezirk": stadtteil,
            "lat": lat,
            "lon": lon,
            "status": open311.status_aus(meldung),
            "strasse": strasse,
            "plz": plz,
        }


KOELN = Open311Quelle(
    stadt="koeln",
    name="Köln",
    url="https://sags-uns.stadt-koeln.de/georeport/v2/requests.json",
    kategorien=KOELN_KATEGORIEN,
    # 21 Tage, und das ist seit T-56 (15.08.2026) ein gemessener Wert und kein
    # Platzhalter mehr. Lars-Entscheidung vom selben Tag.
    #
    # HIER STAND BIS ZUM 15.08.2026 DAS GEGENTEIL, und zwar aus einem Fehler,
    # den man leicht wieder macht. Die erste Messung lief an sechs Wochen und
    # ergab, 88,9 Prozent aller Abstaende zwischen zwei Meldungen derselben
    # Zelle laegen unter 21 Tagen — das Merkmal traege also keine Aussage mehr,
    # der passende Wert liege eher bei 10 Tagen. Beides war falsch. In einem
    # Beobachtungsfenster von sechs Wochen KANN kein Abstand ueber 42 Tage
    # sichtbar werden; die Verteilung war oben abgeschnitten, nicht kurz.
    #
    # Nachgemessen an den vollen 24 Monaten des Rueckimports (34.021 muellnahe
    # Koelner Meldungen, 24.220 Abstaende in 4.777 Zellen):
    #
    #                       Median   <= 10 d   <= 21 d
    #   Koeln, 24 Monate      36 d     22,5 %    36,8 %
    #   Koeln, 6 Wochen        7 d     61,3 %    87,0 %   <- das Artefakt
    #   Berlin, 24 Monate     12 d     46,6 %    64,2 %
    #
    # Koeln ist bei 21 Tagen also TRENNSCHAERFER als Berlin bei demselben Wert:
    # 34,0 Prozent der Koelner Zellen ab drei Meldungen haben keine einzige
    # Wiederkehr, in Berlin sind es 19,2 Prozent. Der Zaehler streut sauber
    # (Median 1, P90 6, Hoechstwert 74). Es gibt keinen Anlass, ihn zu senken
    # oder das Merkmal fuer Koeln abzuschalten.
    #
    # Die Abstaende unterscheiden sich allerdings deutlich je Abfallgruppe:
    # illegal 39 Tage im Median, schrottfahrzeug 58, schrottfahrrad 66. Ein
    # Fenster JE GRUPPE waere fachlich genauer als eines je Stadt. Das Modell
    # kennt heute nur eines je Stadt; die Aenderung saesse in
    # tracker.berechne_hotspots und ist bewusst nicht Teil von T-56.
    #
    # Nicht nachpruefbar bleibt der Anker "Schlusszeitpunkt des Tickets" (Koeln
    # schliesst "Wilder Müll" im Median nach 0,7 Tagen). Diese Zahl stammt aus
    # derselben Sechs-Wochen-Stichprobe und laesst sich aus dem Bestand nicht
    # wiederholen: der Schlusszeitpunkt wird nach A-18 gar nicht gespeichert.
    # Fuer die Wahl des Fensters spielt er keine Rolle mehr — die Abstaende
    # selbst sind gemessen, und sie tragen den Wert.
    wiederkehr_fenster_tage=21,
    # Gemessen 93 Meldungen je Tag ueber alle Kategorien. Die Untergrenze liegt
    # bewusst zwei Groessenordnungen darunter: sie soll einen Ausfall erkennen,
    # nicht einen ruhigen Tag beanstanden.
    mindest_meldungen_je_tag=5.0,
    freitext_uebernehmen=False,
    erste_meldung="2023-12-12",
)

BERLIN = BerlinQuelle()

ALLE = {q.stadt: q for q in (BERLIN, KOELN)}


def hole(stadt: str) -> Quelle:
    """Quelle zu einer Stadt. Ein unbekannter Name ist ein Fehler und wird
    nicht stillschweigend zu Berlin."""
    try:
        return ALLE[stadt]
    except KeyError:
        raise KeyError(
            f"Unbekannte Stadt {stadt!r}. Bekannt sind: "
            f"{', '.join(sorted(ALLE))}.") from None
