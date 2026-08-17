#!/usr/bin/env python3
# Roher Zeichenkettenliteral, weil unten Windows-Pfade stehen: '\d' in
# '..\dsgvo\...' laese Python sonst als Maskierung und warnte bei jedem Lauf.
r"""
Betreff-Filter (Abhilfe A-14 der DSFA vom 28.07.2026, Risiko R-10)
==================================================================
Bürger schreiben beim Melden einen freien Betreff. Die allermeisten nennen
schlicht die Abfallart ("Abfall - Sperrmüll"). Einzelne beschreiben aber die
Lebenssituation eines Menschen an einem genauen Ort, teils ohne und teils mit
Namensnennung. Das ist eine Aussage über eine Person, verknüpft mit einer
Koordinate — genau das, was Risiko R-10 meint.

Alle Beispiele in dieser Datei und in den Tests sind erfunden. Die tatsächlich
betroffenen Wortlaute stehen im Prüfbericht unter
``..\audits\dsgvo\2026-08-03-a14-betreff-filter.md`` (Zone A, nicht öffentlich)
und gehören nicht in einen öffentlichen Code-Speicher — sonst veröffentlichte
der Filter genau das, was er entfernen soll.

Dieses Modul erkennt solche Betreffs und entschärft sie, **bevor** sie in die
Datenbank geschrieben werden, und zieht denselben Maßstab bei jedem Lauf über
den vorhandenen Bestand.

Warum eine Wortliste mit Kontextregeln und kein gelerntes Verfahren
-------------------------------------------------------------------
Geprüft wurden beide Wege an den echten Daten (538 verschiedene Betreff-Werte,
zwei von der Folgenabschätzung unabhängig benannte Treffer):

  * Ein gelerntes Verfahren trägt hier nicht. Bei 0,37 Prozent Positivanteil
    gibt es keine Aufteilung in Lern- und Prüfmenge; die gesamte Bewertung
    besteht aus zwei Fällen. Im Test (Zeichen-3-Gramme, Kosinus, Leave-one-out)
    lag der jeweils gesuchte Fall einmal auf Platz 1 und einmal auf Platz 3 —
    hinter zwei harmlosen Matratzen-Meldungen, die ihm ähnlicher waren als er
    sich selbst. Der Grund ist strukturell: die Ähnlichkeit wird vom
    Abfall-Wortschatz getragen, den alle 538 Werte teilen, nicht von dem einen
    heiklen Wort. Entscheidend ist aber ein anderer Punkt: die **Trefferquote
    wäre nicht messbar**. Eine Wortliste hat eine bekannte Schwäche (ein Wort
    steht nicht darauf), ein auf zwei Beispielen gelerntes Verfahren hat eine
    unbekannte.
  * Ein vortrainiertes Sprachmodell wäre fachlich überlegen, würde aber
    Freitext von Bürgern an einen Dritten übermitteln. Cloudflare und Google
    sind mit A-12 bewusst als Empfänger entfernt worden; ein neuer Empfänger
    für genau die heikelsten Textstellen wäre das Gegenteil davon und zöge eine
    neue Runde der Folgenabschätzung nach sich.

Die Wortliste hat also nicht gewonnen, weil sie die beste Erkennung liefert,
sondern weil ihr Versagen sichtbar und nachbesserbar ist. Wer eine Formulierung
findet, die durchrutscht, trägt sie hier nach.

Zwei Regelgruppen
-----------------
1. ``lebenssituation`` — der eigentliche Auftrag aus A-14. Trifft eine Regel,
   wird der **ganze** Freitext verworfen und durch einen Vermerk plus die
   maschinell abgeleitete Abfallgruppe ersetzt. Der Freitext ist nicht zu
   retten: die heikle Angabe steckt in seinem Sinn, nicht in einem Wort, das
   sich herausschneiden ließe.
2. ``hausnummer`` — über A-14 hinaus, aber dieselbe Spalte und derselbe
   Schreibweg. Im Mai 2026 sind 105.100 Hausnummern aus dem Feld ``strasse``
   entfernt worden; im Freitext stehen sie weiterhin (Bauart "Wilde
   Müllablagerungen, Musterstraße 81, 13158 Berlin"). Hier wird nur die Nummer
   entfernt, der Rest des Satzes bleibt stehen.

Warum der Freitext nicht einfach ganz entfällt: ``kategorie`` ist im gesamten
Bestand leer (82.780 von 82.780 Zeilen). Die Abfallgruppe von 85,9 Prozent
aller Meldungen wird ausschließlich aus dem Betreff abgeleitet. Ein pauschales
Verwerfen würde die Kategorien-Auswertung der Karte zerstören, ohne dass dem
ein Schutzgewinn gegenüberstünde — die 525 unauffälligen Werte tragen nichts
Personenbezogenes.

Der Betreff verlässt die Datenbank nicht: ``export_html.load_data`` liest ihn
nur, um ``kategorisiere`` zu füttern, und bettet ihn nirgends in die
ausgelieferte Seite ein. R-10 ist damit ein Bestands-, kein
Veröffentlichungsrisiko. Nach 24 Monaten fällt der Freitext ohnehin weg (A-4),
dieser Filter deckt die Zeit davor ab.

Aufruf:
    python betreff_filter.py --dry-run   zeigt die Wirkung ohne zu schreiben
    python betreff_filter.py --apply     zieht den Bestand nach
Im Normalbetrieb läuft der Bestandsabgleich automatisch bei jedem tracker.py-Lauf.
"""

import argparse
import re
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent / "ordnungsamt.db"

# Vermerk anstelle des verworfenen Freitexts. Bewusst ohne eckige Klammern,
# damit er nicht als unausgefüllter Platzhalter missverstanden wird, und
# bewusst so formuliert, dass er selbst keine Regel unten auslöst (sonst würde
# jeder Lauf denselben Datensatz erneut anfassen — siehe Test zur Wiederholbarkeit).
MARKER = "Freitext entfernt (A-14)"

# ── Regelgruppe 1: Lebenssituation ───────────────────────────────────────────
# Jede Regel ist ein Wortlaut, der eine Aussage über einen Menschen trägt und
# nicht über Abfall. Wortgrenzen sind Pflicht: ohne sie steckt "zelt" in
# "Einzelteile" und "roma" in "Aroma".
#
# Bewusst NICHT aufgenommen, weil sie im Abfall-Zusammenhang harmlos sind und
# massenhaft falsch anschlagen würden:
#   "illegal", "wild"  — beides reguläre Wörter für illegale Ablagerungen und
#                        zugleich Schlüsselwörter der Kategorisierung
#   "behindert"        — meint hier fast immer "versperrt den Gehweg"
#   "freier"           — steckt in "Rechtsfreier Raum" (an den echten Daten
#                        aufgefallen: ein Fehltreffer bei 538 Werten)
#   "Tafel"            — Schokoladentafel, Schiefertafel, Hinweistafel
REGELN = {
    "obdachlosigkeit": r"\b(obdachlos\w*|wohnungslos\w*|penner|schlafplatz"
                       r"|[üu]bernacht\w*|notunterkunft|campieren|zelt\w*"
                       r"|lagerplatz)\b|verlassenes lager",
    "sucht": r"\b(drogen\w*|junkie\w*|fixer|spritzen?|heroin|crack|methadon"
             r"|alkoholiker\w*|s[äa]ufer|trinkerszene)\b",
    "prostitution": r"\b(prostitution|menschenhandel|bordell|stra[ßs]enstrich)\b",
    "herkunft": r"\b(fl[üu]chtling\w*|gefl[üu]chtete\w*|asyl\w*|migrant\w*"
                r"|ausl[äa]nder\w*|roma|sinti|zigeuner|clan|s[üu]dl[äa]ndisch\w*)\b",
    "unterkunft": r"\bbewohner (des|der|von)\b|\bunterkunft\b|\bobdach\b",
    "gesundheit": r"\b(psychisch\w*|verwahrlost\w*|messie\w*|demen[zt]\w*"
                  r"|pflegefall)\b",
    "armut": r"\b(flaschensammler\w*|bettler\w*|betteln|hartz|b[üu]rgergeld"
             r"|sozialamt)\b",
    # Namensnennung: eine Rollenbezeichnung, direkt gefolgt von einem
    # großgeschriebenen Wort. Trifft die Bauart "Inhaberin <Nachname>" und
    # "Familie <Nachname>", nicht "Herrenlose Mülltonne" (dort fehlt das
    # Leerzeichen nach "Herr").
    "person": r"\b(inhaber|inhaberin|eigent[üu]mer|eigent[üu]merin|mieter"
              r"|mieterin|familie|herr|frau)\s+[A-ZÄÖÜ][a-zäöüß]{2,}",
}

# ── Regelwerk je Stadt (T-49, 15.08.2026) ────────────────────────────────────
# Auflage A-18 der Folgenabschaetzung in der Fassung 1.1: der Freitextfilter
# gilt JE STADT. Der Berliner Satz oben bleibt woertlich, wie er ist — er ist an
# 538 verschiedenen Werten geprueft, und jede Aenderung daran wuerde beim
# naechsten Lauf den vorhandenen Bestand anfassen.
#
# Fuer Koeln ist der Satz an den echten Daten gemessen worden (15.08.2026,
# 3.705 Freitexte aus 4.000 Meldungen vom 01.07. bis 12.08.2026):
#
#   * Die Berliner Regeln allein greifen bei 75 von 3.705 Freitexten (2,0 %).
#   * Sie erzeugen dabei sichtbare Fehltreffer, weil Koelner Meldungen in
#     ganzen Saetzen geschrieben sind: "verwahrlostes Fahrrad" loest
#     ``gesundheit`` aus, "Zeltinger Str." loest ``obdachlosigkeit`` aus,
#     "Frau heftig" loest ``person`` aus.
#   * Was die Berliner Liste NICHT kennt, steckt in den Koelner Texten sehr
#     wohl drin: Kfz-Kennzeichen (19 Treffer), Telefonnummern (6),
#     Mailadressen (2), Hausnummern in der Form "Nr. 36" (112).
#
# Daraus folgt beides: die Zusatzregeln unten, UND die Entscheidung in
# ``quellen.py``, den Koelner Freitext gar nicht erst zu uebernehmen. Eine
# Wortliste, die 2 Prozent erwischt, traegt keinen Bestand aus frei
# geschriebenen Saetzen. Der Filter ist hier die zweite Reihe, nicht die erste.
REGELN_ZUSATZ_KOELN = {
    # Amtliches Kennzeichen. Bewusst ohne IGNORECASE gedacht, aber die
    # gemeinsame Uebersetzung unten arbeitet mit IGNORECASE; die Ziffernfolge
    # traegt die Unterscheidung.
    "kennzeichen": r"\b[A-ZÄÖÜ]{1,3}\s?-\s?[A-Z]{1,2}\s?\d{1,4}\b",
    "kontaktdaten": r"[\w.+-]+@[\w-]+\.\w{2,}|\b0\d{2,4}[\s/-]?\d[\d\s-]{5,}\b",
}

REGELN_KOELN = {**REGELN, **REGELN_ZUSATZ_KOELN}

REGELN_JE_STADT = {
    "berlin": REGELN,
    "koeln": REGELN_KOELN,
}

# Ohne Eintrag gilt der Berliner Satz. Eine neue Stadt bekommt damit einen
# Schutz und nicht keinen — schwaecher als noetig ist besser als offen.
STADT_STANDARD = "berlin"

_REGELN = {name: re.compile(muster, re.IGNORECASE)
           for name, muster in REGELN.items()}

_REGELN_JE_STADT = {
    stadt: {name: re.compile(muster, re.IGNORECASE)
            for name, muster in satz.items()}
    for stadt, satz in REGELN_JE_STADT.items()
}


def regeln_fuer(stadt: str | None):
    return _REGELN_JE_STADT.get(stadt or STADT_STANDARD,
                                _REGELN_JE_STADT[STADT_STANDARD])

# ── Regelgruppe 2: Hausnummern im Freitext ───────────────────────────────────
# Straßenwort, gefolgt von einer Hausnummer oder einer Spanne ("4-10",
# "73 und 85"). Entfernt wird nur die Nummer.
# Der Buchstaben-Zusatz ("12a") hängt ohne Leerzeichen an der Zahl. Stünde dort
# ein \s*, verschluckte die Regel das nächste Wort: aus "Str. 17 in 10405" wurde
# "Str.n 10405" und aus "Beispielring 73 und 85" wurde "Beispielringnd 85".
# An den echten Daten aufgefallen.
_HAUSNUMMER = re.compile(
    r"(\b(?:[A-ZÄÖÜa-zäöüß-]{2,}\s*)?"
    r"(?:stra[ßs]e|str\.?|weg|platz|allee|damm|ring|ufer|gasse|steig|pfad))"
    r"\s+\d+[a-zA-Z]?\b(?:\s*(?:-|bis|und|/)\s*\d+[a-zA-Z]?\b)*",
    re.IGNORECASE,
)


def treffer(betreff: str, stadt: str | None = None) -> list[str]:
    """Namen der Regeln aus Gruppe 1, die auf den Betreff zutreffen."""
    text = betreff or ""
    return sorted(name for name, muster in regeln_fuer(stadt).items()
                  if muster.search(text))


def _gruppe(kategorie: str, betreff: str) -> str | None:
    """Abfallgruppe ableiten, solange der Freitext noch da ist."""
    import export_html
    return export_html.kategorisiere(f"{kategorie or ''} {betreff or ''}")


# Zweite Bauart einer Hausnummer, die in den Kölner Freitexten vorkommt und in
# den Berliner nicht: "Hs Nr. 36", "Hausnummer 49". 112 von 3.705 gemessen.
# Bewusst NICHT für Berlin aktiv — der Berliner Bestand ist an der vorhandenen
# Regel geprüft, und eine zusätzliche Regel würde beim nächsten Lauf 82.780
# Zeilen erneut anfassen, ohne dass jemand die Wirkung gemessen hätte.
_HAUSNUMMER_WORT = re.compile(
    r"\b(?:Hs\.?\s*)?(?:Nr\.?|Hausnummer|Haus-?Nr\.?)\s*\d+\s*[a-zA-Z]?"
    r"(?:\s*(?:-|bis|und|/)\s*\d+\s*[a-zA-Z]?)*",
    re.IGNORECASE,
)

_HAUSNUMMER_ZUSATZ_JE_STADT = {
    "koeln": (_HAUSNUMMER_WORT,),
}


def entschaerfe(betreff: str, kategorie: str = "",
                stadt: str | None = None) -> tuple[str, list[str]]:
    """Gibt den zu speichernden Betreff und die ausgelösten Regeln zurück.

    Ohne Treffer kommt der Betreff unverändert zurück — bis auf eine etwaige
    Hausnummer, die immer fällt.

    ``stadt`` wählt das Regelwerk (A-18). Ohne Angabe gilt der Berliner Satz.
    """
    text = betreff or ""
    regeln = treffer(text, stadt)

    if regeln:
        # Gruppe VOR dem Verwerfen ableiten, sonst geht die Kategorisierung
        # dieser Meldung verloren.
        gruppe = _gruppe(kategorie, text)
        ersatz = f"{MARKER} - {gruppe}" if gruppe else MARKER
        return ersatz, regeln

    ohne_nummer = _HAUSNUMMER.sub(r"\1", text)
    for zusatz in _HAUSNUMMER_ZUSATZ_JE_STADT.get(stadt or STADT_STANDARD, ()):
        ohne_nummer = zusatz.sub("", ohne_nummer)
    if ohne_nummer != text:
        # Doppelte Leerzeichen und ein Komma-Rest wie "…straße , 13086" glätten.
        ohne_nummer = re.sub(r"\s{2,}", " ", ohne_nummer)
        return ohne_nummer, ["hausnummer"]

    return text, []


# ── Bestandsabgleich ─────────────────────────────────────────────────────────

def bestand_nachziehen(conn: sqlite3.Connection, dry_run: bool = False) -> dict:
    """Wendet dieselben Regeln auf die bereits gespeicherten Meldungen an.

    Läuft über die verschiedenen Werte, nicht über die Zeilen: 538 Prüfungen
    statt 82.780. Wiederholbar — ein bereits entschärfter Wert löst keine Regel
    mehr aus und wird nicht erneut angefasst.

    T-49 / A-18: gruppiert nach STADT UND Wert, und schreibt auch stadtscharf
    zurück. Zwei Gründe. Erstens gilt je Stadt ein eigenes Regelwerk, ein Wert
    kann also in der einen Stadt fallen und in der anderen stehen bleiben.
    Zweitens würde ein stadtblindes UPDATE das Ergebnis der einen Stadt auf die
    gleichlautenden Zeilen der anderen übertragen — dieselbe Sorte stiller
    Übergriff, die T-51 aus der Löschroutine entfernt hat.
    """
    werte = conn.execute(
        "SELECT stadt, betreff, COUNT(*) FROM meldungen "
        "WHERE betreff IS NOT NULL AND betreff <> '' GROUP BY stadt, betreff"
    ).fetchall()

    # (stadt, alt, neu, anzahl, regeln)
    aenderungen: list[tuple[str, str, str, int, list[str]]] = []
    for stadt, alt, anzahl in werte:
        neu, regeln = entschaerfe(alt, stadt=stadt)
        if neu != alt:
            aenderungen.append((stadt, alt, neu, anzahl, regeln))

    if aenderungen and not dry_run:
        conn.executemany(
            "UPDATE meldungen SET betreff = ? WHERE stadt = ? AND betreff = ?",
            [(neu, stadt, alt) for stadt, alt, neu, _, _ in aenderungen])
        conn.commit()

    lebenslagen = [a for a in aenderungen if a[4] != ["hausnummer"]]
    return {
        "werte_geprueft": len(werte),
        "werte_geaendert": len(aenderungen),
        "zeilen_geaendert": sum(a[3] for a in aenderungen),
        "lebenssituation": len(lebenslagen),
        "hausnummer": len(aenderungen) - len(lebenslagen),
        # (stadt, alt, neu, anzahl, regeln)
        "aenderungen": aenderungen,
    }


def main() -> int:
    p = argparse.ArgumentParser(
        description="Betreff-Filter nach Abhilfe A-14 der DSFA vom 28.07.2026")
    p.add_argument("--dry-run", action="store_true", help="nur anzeigen, nichts schreiben")
    p.add_argument("--apply", action="store_true", help="Bestand nachziehen")
    p.add_argument("--db", default=str(DB_PATH), help="Pfad zur Datenbank")
    args = p.parse_args()
    if args.apply == args.dry_run:
        p.error("genau eines von --dry-run oder --apply angeben")

    conn = sqlite3.connect(args.db)
    ergebnis = bestand_nachziehen(conn, dry_run=args.dry_run)
    conn.close()

    print(f"Datenbank: {args.db}")
    print(f"Modus:     {'DRY-RUN (kein Schreiben)' if args.dry_run else 'LIVE'}")
    print(f"  verschiedene Betreff-Werte geprüft: {ergebnis['werte_geprueft']}")
    print(f"  davon zu ändern:                    {ergebnis['werte_geaendert']}"
          f"  ({ergebnis['lebenssituation']} Lebenssituation, "
          f"{ergebnis['hausnummer']} Hausnummer)")
    print(f"  betroffene Zeilen:                  {ergebnis['zeilen_geaendert']}")
    if ergebnis["aenderungen"]:
        print()
        for stadt, alt, neu, anzahl, regeln in ergebnis["aenderungen"]:
            print(f"  [{stadt}] [{','.join(regeln)}] {anzahl}x")
            print(f"      vorher:  {alt!r}")
            print(f"      nachher: {neu!r}")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
