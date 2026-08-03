#!/usr/bin/env python3
"""
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

_REGELN = {name: re.compile(muster, re.IGNORECASE)
           for name, muster in REGELN.items()}

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


def treffer(betreff: str) -> list[str]:
    """Namen der Regeln aus Gruppe 1, die auf den Betreff zutreffen."""
    text = betreff or ""
    return sorted(name for name, muster in _REGELN.items() if muster.search(text))


def _gruppe(kategorie: str, betreff: str) -> str | None:
    """Abfallgruppe ableiten, solange der Freitext noch da ist."""
    import export_html
    return export_html.kategorisiere(f"{kategorie or ''} {betreff or ''}")


def entschaerfe(betreff: str, kategorie: str = "") -> tuple[str, list[str]]:
    """Gibt den zu speichernden Betreff und die ausgelösten Regeln zurück.

    Ohne Treffer kommt der Betreff unverändert zurück — bis auf eine etwaige
    Hausnummer, die immer fällt.
    """
    text = betreff or ""
    regeln = treffer(text)

    if regeln:
        # Gruppe VOR dem Verwerfen ableiten, sonst geht die Kategorisierung
        # dieser Meldung verloren.
        gruppe = _gruppe(kategorie, text)
        ersatz = f"{MARKER} - {gruppe}" if gruppe else MARKER
        return ersatz, regeln

    ohne_nummer = _HAUSNUMMER.sub(r"\1", text)
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
    """
    werte = conn.execute(
        "SELECT betreff, COUNT(*) FROM meldungen "
        "WHERE betreff IS NOT NULL AND betreff <> '' GROUP BY betreff"
    ).fetchall()

    aenderungen: list[tuple[str, str, int, list[str]]] = []
    for alt, anzahl in werte:
        neu, regeln = entschaerfe(alt)
        if neu != alt:
            aenderungen.append((alt, neu, anzahl, regeln))

    if aenderungen and not dry_run:
        conn.executemany("UPDATE meldungen SET betreff = ? WHERE betreff = ?",
                         [(neu, alt) for alt, neu, _, _ in aenderungen])
        conn.commit()

    lebenslagen = [a for a in aenderungen if a[3] != ["hausnummer"]]
    return {
        "werte_geprueft": len(werte),
        "werte_geaendert": len(aenderungen),
        "zeilen_geaendert": sum(a[2] for a in aenderungen),
        "lebenssituation": len(lebenslagen),
        "hausnummer": len(aenderungen) - len(lebenslagen),
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
        for alt, neu, anzahl, regeln in ergebnis["aenderungen"]:
            print(f"  [{','.join(regeln)}] {anzahl}x")
            print(f"      vorher:  {alt!r}")
            print(f"      nachher: {neu!r}")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
