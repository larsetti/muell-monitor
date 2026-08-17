#!/usr/bin/env python3
r"""
Rueckimport aus einer Open311-Quelle (T-49, 15.08.2026)
========================================================
Holt die zurueckliegenden Meldungen einer Stadt nach, monatsweise, ueber
denselben Weg wie der laufende Betrieb: dieselbe Quelle, dieselbe
Feld-Zuordnung, dieselben Auflagen. Es gibt hier keinen zweiten Import-Pfad,
der an einer Auflage vorbeikaeme.

    python rueckimport.py --stadt koeln                zeigt nur, was kaeme
    python rueckimport.py --stadt koeln --ausfuehren   importiert wirklich

Der Trockenlauf ist die VORGABE. Wer nichts angibt, schreibt nichts.

Woher der Startzeitpunkt kommt
-------------------------------
Nicht aus dem Quelltext. Er wird aus der Aufbewahrungsfrist abgeleitet:
``retention.aggregat_grenze(jetzt, stadt)``.

Lars-Entscheidung vom 15.08.2026: der Rueckimport wird auf 24 Monate gekuerzt,
die Folgenabschaetzung bleibt unveraendert. Koeln wird also NICHT ab dem
12.12.2023 geholt. Alles Aeltere wuerde beim ersten regulaeren Lauf zu
Monats-Aggregaten reduziert und der Rest unter drei Meldungen ersatzlos
verworfen — der Import waere Arbeit ohne Ertrag.

Ein festes Datum im Quelltext liefe in einem Jahr wieder gegen dieselbe Grenze.
Deshalb steht hier keines. Wird die Frist spaeter angehoben (die Vorrichtung
dafuer ist MM_RETENTION_AGGREGAT_MONATE_KOELN aus T-51), reicht der Rueckimport
ohne Codeaenderung weiter zurueck.

Die Frist der Quelle begrenzt zusaetzlich: Koeln fuehrt Meldungen ab dem
12.12.2023, frueher gibt es nichts zu holen.

Was der Rueckimport NICHT tut
------------------------------
Er ruft die Loeschroutine nur fuer SEINE Stadt auf (A-20: die Fristen laufen im
Rueckimport mit). Die anderen Staedte fasst er nicht an. Das ist bewusst
anders als beim taeglichen Lauf, der alle Staedte bedient: ein Rueckimport ist
ein Eingriff in eine Stadt und soll nicht nebenbei den Bestand einer anderen
veraendern.
"""

import argparse
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

import open311
import quellen
import retention
import tracker

DB_PATH = Path(__file__).parent / "ordnungsamt.db"

# Der Zeitraum wird in Scheiben abgerufen. Ein Monat je Scheibe ist ein
# Kompromiss: gross genug, dass die Zahl der Anfragen ueberschaubar bleibt,
# klein genug, dass die Plausibilitaetspruefung je Scheibe etwas aussagt. Bei
# rund 93 Meldungen je Tag sind das etwa 28 Seiten je Monat.
SCHEIBE_TAGE = 30


def monatsscheiben(von: datetime, bis: datetime, tage: int = SCHEIBE_TAGE):
    """Zerlegt einen Zeitraum in aufeinanderfolgende Scheiben."""
    zeiger = von
    while zeiger < bis:
        ende = min(zeiger + timedelta(days=tage), bis)
        yield open311.Zeitraum(von=zeiger, bis=ende)
        zeiger = ende


def startzeitpunkt(quelle, jetzt: datetime) -> tuple[datetime, str]:
    """Startzeitpunkt und die Begruendung dafuer, im Klartext."""
    grenze = retention.aggregat_grenze(jetzt, quelle.stadt)
    monate = retention.frist_aggregat_monate(quelle.stadt)
    begruendung = (f"Aufbewahrungsfrist {monate} Monate "
                   f"(retention.aggregat_grenze), alles davor wuerde beim "
                   f"naechsten regulaeren Lauf zu Aggregaten reduziert")

    erste = getattr(quelle, "erste_meldung", None)
    if erste:
        quellstart = datetime.fromisoformat(erste)
        if quellstart > grenze:
            return quellstart, (f"die Quelle fuehrt erst ab {erste}; die "
                                f"{monate}-Monats-Grenze laege frueher")
    return grenze, begruendung


def _vorhandene_kennungen(conn, stadt: str) -> set[str]:
    return {r[0] for r in conn.execute(
        "SELECT id FROM meldungen WHERE stadt = ?", (stadt,))}


def lauf(quelle, conn, jetzt: datetime, ausfuehren: bool,
         bis: datetime = None, scheibe_tage: int = SCHEIBE_TAGE,
         still: bool = False) -> dict:
    """Der eigentliche Rueckimport. ``ausfuehren=False`` schreibt nichts."""
    von, warum = startzeitpunkt(quelle, jetzt)
    bis = bis or jetzt
    jetzt_iso = jetzt.isoformat()

    def sag(*a):
        if not still:
            print(*a)

    sag(f"Stadt:        {quelle.name} ({quelle.stadt})")
    sag(f"Modus:        {'AUSFUEHREN (schreibt)' if ausfuehren else 'TROCKENLAUF (schreibt nichts)'}")
    sag(f"Zeitraum:     {von:%Y-%m-%d} bis {bis:%Y-%m-%d} "
        f"({(bis - von).days} Tage)")
    sag(f"Startgrund:   {warum}")
    sag(f"Quelle:       {quelle.url}")
    sag("")

    bekannt = _vorhandene_kennungen(conn, quelle.stadt)
    je_monat: dict[str, dict] = {}
    # Einzelne Seiten aelterer Zeitraeume antworten dauerhaft mit HTTP 500 —
    # am 15.08.2026 gemessen und als Eigenheit der Quelle bestaetigt, nicht als
    # Ausfall bei uns. Sie werden hier gesammelt und im Ergebnis ausgewiesen,
    # damit aus einer Luecke keine stille Vollstaendigkeit wird.
    luecken: list[dict] = []
    gesamt = {"abgerufen": 0, "muellnah": 0, "neu": 0, "schon_da": 0,
              "ohne_ort": 0, "mit_bild": 0, "mit_freitext": 0,
              "luecken": luecken}

    for zeitraum in monatsscheiben(von, bis, scheibe_tage):
        try:
            meldungen = quelle.hole_meldungen(zeitraum,
                                              pause=open311.PAUSE_SEKUNDEN,
                                              luecken=luecken)
        except open311.AbrufFehler as fehler:
            # Auflage 3c: ein duenner oder leerer Abschnitt ist ein Fehler und
            # KEIN Befund. Der Rueckimport bricht ab, statt eine Luecke
            # stillschweigend als "in diesem Monat war nichts" zu verbuchen.
            sag("")
            sag(f"ABBRUCH bei {zeitraum}: {fehler}")
            gesamt["abgebrochen"] = str(fehler)
            gesamt["je_monat"] = je_monat
            return gesamt

        schluessel = f"{zeitraum.von:%Y-%m}"
        eintrag = je_monat.setdefault(
            schluessel, {"abgerufen": 0, "muellnah": 0, "neu": 0,
                         "mit_bild": 0, "mit_freitext": 0})

        for m in meldungen:
            eintrag["abgerufen"] += 1
            gesamt["abgerufen"] += 1
            if m.get("media_url"):
                eintrag["mit_bild"] += 1
                gesamt["mit_bild"] += 1
            if str(m.get("description") or "").strip():
                eintrag["mit_freitext"] += 1
                gesamt["mit_freitext"] += 1

            satz = quelle.aufbereiten(m, jetzt_iso)
            if satz is None:
                continue
            eintrag["muellnah"] += 1
            gesamt["muellnah"] += 1

            if satz["id"] in bekannt:
                gesamt["schon_da"] += 1
                continue
            if satz["lat"] is None or satz["lon"] is None:
                gesamt["ohne_ort"] += 1

            eintrag["neu"] += 1
            gesamt["neu"] += 1
            # Auch im Trockenlauf mitschreiben, was schon gezaehlt wurde. Sonst
            # meldet der Trockenlauf eine Meldung, die in zwei Scheiben
            # auftaucht, zweimal als neu — und die Zahl, an der Lars den Umfang
            # abschaetzt, waere zu hoch.
            bekannt.add(satz["id"])
            if ausfuehren:
                conn.execute("""
                    INSERT INTO meldungen
                        (id, fetched_at, datum, kategorie, betreff, bezirk, lat, lon,
                         status, is_muell, strasse, plz, stadt)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                """, (satz["id"], jetzt_iso, satz["datum"], satz["kategorie"],
                      satz["betreff"], satz["bezirk"], satz["lat"], satz["lon"],
                      satz["status"], 1, satz["strasse"], str(satz["plz"]),
                      quelle.stadt))

        if ausfuehren:
            conn.commit()
        luecken_hier = [l for l in luecken if l["zeitraum"] == str(zeitraum)]
        eintrag["luecken"] = len(luecken_hier)
        sag(f"  {schluessel}: {eintrag['abgerufen']:6d} abgerufen, "
            f"{eintrag['muellnah']:5d} muellnah, {eintrag['neu']:5d} neu, "
            f"{eintrag['mit_bild']:5d} Bildadressen verworfen, "
            f"{eintrag['mit_freitext']:5d} Freitexte nicht uebernommen"
            + (f", {len(luecken_hier)} SEITE(N) NICHT LESBAR"
               if luecken_hier else ""))

    gesamt["je_monat"] = je_monat

    if ausfuehren:
        # A-20: die Loeschfristen laufen im Rueckimport mit — aber nur fuer
        # DIESE Stadt. Ein Rueckimport soll nicht nebenbei den Bestand einer
        # anderen Stadt veraendern.
        fristen = retention.anwenden(conn, jetzt, stadt=quelle.stadt)
        gesamt["fristen"] = fristen
        zellen = tracker.berechne_hotspots(conn, quelle)
        gesamt["zellen"] = zellen
        conn.execute("""
            INSERT INTO fetch_log (fetched_at, count_total, count_new, count_muell, stadt)
            VALUES (?,?,?,?,?)
        """, (jetzt_iso, gesamt["abgerufen"], gesamt["neu"], gesamt["muellnah"],
              quelle.stadt))
        conn.commit()

    return gesamt


def main() -> int:
    p = argparse.ArgumentParser(
        description="Rueckimport zurueckliegender Meldungen aus einer Open311-Quelle")
    p.add_argument("--stadt", required=True, choices=sorted(quellen.ALLE),
                   help="Stadt, deren Vergangenheit geholt wird")
    p.add_argument("--ausfuehren", action="store_true",
                   help="wirklich importieren; ohne diesen Zusatz nur anzeigen")
    p.add_argument("--db", default=str(DB_PATH), help="Pfad zur Datenbank")
    p.add_argument("--scheibe-tage", type=int, default=SCHEIBE_TAGE,
                   help=f"Groesse einer Abrufscheibe in Tagen (Vorgabe {SCHEIBE_TAGE})")
    args = p.parse_args()

    quelle = quellen.hole(args.stadt)
    if not isinstance(quelle, quellen.Open311Quelle):
        print(f"Abbruch: {quelle.name} ist keine Open311-Quelle. Ein "
              f"Rueckimport ueber einen Zeitraum ist dort nicht moeglich.")
        return 1

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    tracker.init_db(conn)

    jetzt = datetime.utcnow()
    vorher = conn.execute(
        "SELECT stadt, COUNT(*) FROM meldungen GROUP BY stadt").fetchall()

    ergebnis = lauf(quelle, conn, jetzt, ausfuehren=args.ausfuehren,
                    scheibe_tage=args.scheibe_tage)

    nachher = conn.execute(
        "SELECT stadt, COUNT(*) FROM meldungen GROUP BY stadt").fetchall()
    conn.close()

    print()
    print("ERGEBNIS")
    print(f"  abgerufen:                       {ergebnis['abgerufen']:7d}")
    print(f"  davon muellnah:                  {ergebnis['muellnah']:7d}")
    print(f"  davon neu:                       {ergebnis['neu']:7d}")
    print(f"  bereits im Bestand:              {ergebnis['schon_da']:7d}")
    print(f"  Bildadressen verworfen (A-17):   {ergebnis['mit_bild']:7d}")
    print(f"  Freitexte nicht uebernommen:     {ergebnis['mit_freitext']:7d}")
    if ergebnis.get("abgebrochen"):
        print(f"  ABGEBROCHEN: {ergebnis['abgebrochen']}")
    luecken = ergebnis.get("luecken") or []
    if luecken:
        print()
        print(f"  NICHT LESBARE SEITEN: {len(luecken)} — geschaetzt bis zu "
              f"{len(luecken) * open311.SEITENGROESSE} Meldungen fehlen. Das ist "
              f"eine Luecke IN DER QUELLE, kein Ausfall bei uns. Sie steht hier, "
              f"damit der Bestand nicht faelschlich als vollstaendig gilt:")
        for l in luecken:
            print(f"    {l['zeitraum']}, Seite {l['seite']}: {l['grund']}")
    if ergebnis.get("fristen"):
        f = ergebnis["fristen"]
        print(f"  Loeschfristen (nur {args.stadt}): "
              f"{f['quellabgang_geloescht']} wegen Quellabgang, "
              f"{f['altbestand_aggregiert']} auf Aggregate reduziert")
    if ergebnis.get("zellen"):
        print(f"  Zellen neu berechnet:            {ergebnis['zellen']['zellen']:7d}")
    print()
    print("  Bestand vorher:  " + ", ".join(f"{s}={n}" for s, n in vorher))
    print("  Bestand nachher: " + ", ".join(f"{s}={n}" for s, n in nachher))
    if not args.ausfuehren:
        print()
        print("  TROCKENLAUF — es wurde nichts geschrieben. Mit --ausfuehren "
              "wirklich importieren.")
    # Rueckgabewerte: 0 sauber, 2 abgebrochen, 3 durchgelaufen aber mit
    # Luecken. Der dritte Wert ist Absicht — ein Lauf mit Luecken darf nicht
    # aussehen wie ein Lauf ohne.
    if ergebnis.get("abgebrochen"):
        return 2
    return 3 if luecken else 0


if __name__ == "__main__":
    sys.exit(main())
