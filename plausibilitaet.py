"""Ausfall-Erkennung, die nicht an der Meldungsdichte einer Stadt hängt (T-79).

Warum es diese Datei gibt
-------------------------
Bis zum 18.08.2026 war die einzige Ausfall-Erkennung ``open311.pruefe_plausibel``
mit einer **fest eingetragenen Zahl je Stadt** (``mindest_meldungen_je_tag``,
für Köln 5,0). Zwei Mängel, beide gemessen:

1. **Die feste Zahl trägt nur bei dichten Städten.** Köln liefert 93 Meldungen
   je Tag, Bonn lag 2024 bei 1,7 — rund fünfzigfach auseinander. Bei gleichem
   Sicherheitsabstand ergäbe das für Bonn 0,09 je Tag; wegen
   ``erwartet = max(1, int(tage * mindest_je_tag))`` verlangt die Woche des
   täglichen Laufs dann **genau eine** Meldung, wo zwölf fällig sind. Ein
   Ausfall, der eine einzige Meldung durchlässt, geht als gültiger Lauf durch.
   Der Sockel macht das unvermeidlich, egal wie klein die Zahl gesetzt wird.
2. **Sie sah Berlin gar nicht.** ``pruefe_plausibel`` sitzt in ``open311.py``
   und wird ausschließlich aus ``hole_zeitraum`` gerufen. Berlins Feed hat
   seinen eigenen Leser in ``quellen.BerlinQuelle`` und läuft dort nie durch.
   Berlins Quelle liefert seit dem 23.04.2026 nachweislich **0** Meldungen je
   Lauf (``fetch_log``, 14 Läufe in Folge) — und keine Prüfung hat je
   angeschlagen. Das ist wortgleich die Fehlerklasse aus T-55 und T-66: ein
   Schutz, der an einem Code-Weg einer Stadt hängt, fällt bei der nächsten
   stumm aus.

Die Bauart: der eigene Vorlauf statt einer gesetzten Zahl
--------------------------------------------------------
Verglichen wird ein Abruf mit dem, was **dieselbe Stadt** zuletzt geliefert
hat. Die Zahl steht damit nicht mehr in ``quellen.py``, sondern entsteht aus
``fetch_log`` — und die Dichte kürzt sich heraus: eine Stadt mit 1,7 Meldungen
je Tag wird an 1,7 gemessen, eine mit 93 an 93.

Zwei Abruf-Arten, die nicht vermischt werden dürfen:

  * **Bestandsabruf** (Berlin): ein Abruf liefert den vollständigen Bestand,
    zuletzt 105.456 Meldungen. Verglichen wird Bestand gegen Bestand.
  * **Zeitraum-Abruf** (Open311, Köln und Bonn): ein Abruf liefert ein Fenster.
    Verglichen wird je Tag, damit der tägliche Lauf über sieben Tage und ein
    Rückimport über 24 Monate dieselbe Größe ergeben.

``fetch_log.zeitraum_tage`` hält die Fensterlänge fest, ``NULL`` bedeutet
Bestandsabruf. Ältere Zeilen ohne diesen Wert gelten als Bestandsabruf — das
ist für Berlin richtig und schließt Kölns zwei Rückimport-Zeilen vom
18.08.2026 bewusst aus, statt sie falsch einzuordnen.

Warum der Vorlauf nicht altern darf
-----------------------------------
Die Vergleichsläufe werden **nach Anzahl** gewählt und nicht nach Alter: die
letzten ``MAX_LAEUFE`` Läufe, die etwas geliefert haben. Ein Vorlauf mit
Verfallsdatum hätte den Fehler, den er verhindern soll — nach ein paar Wochen
Ausfall wäre der Ausfall der neue Normalstand und die Prüfung stumm. Berlins
Vorlauf steht deshalb bis heute auf 105.456, und jeder Lauf mit 0 schlägt an.

Aus demselben Grund zählt ein Lauf, der selbst als unplausibel erkannt wurde,
**nicht** in den Vorlauf (``fetch_log.plausibel``). Sonst zöge eine Quelle, die
über Wochen langsam abbaut, ihren eigenen Maßstab mit nach unten.

Wo die Grenze liegt, ehrlich benannt
------------------------------------
Der Riegel erkennt einen Ausfall, keine dünne Woche. Bei ``ANTEIL = 0,25``
schlägt er an, sobald ein Abruf unter ein Viertel des eigenen Vorlaufs fällt.
Für eine dünne Stadt heißt das: ein Ausfall, der drei von zwölf Meldungen
durchlässt, kommt weiterhin durch. Das ist keine Nachlässigkeit, sondern die
Grenze der Statistik — bei 1,7 Meldungen je Tag ist der Unterschied zwischen
"vier" und "zwölf" in einer einzelnen Woche nicht sicher von Zufall zu
trennen. Gemessen ist beides im Bericht zu T-79.
"""

import logging

log = logging.getLogger(__name__)


class AbrufUnplausibel(RuntimeError):
    """Der Abruf lief technisch durch, das Ergebnis ist aber nicht glaubhaft.

    Eigene Klasse statt ``open311.AbrufUnplausibel``, weil dieser Riegel für
    **jede** Stadt gilt und nicht nur für die Open311-Städte. ``tracker.run``
    fängt beide und behandelt sie gleich.
    """


# Anteil des eigenen Vorlaufs, unter dem ein Abruf als Ausfall gilt.
#
# Gemessen an Kölns echtem Bestand (731 Tage, 12.12.2023 bis 15.08.2026, jede
# Woche als gleitendes Sieben-Tage-Fenster):
#
#   Median einer Woche   318 Meldungen
#   dünnste Woche        175 Meldungen   (11.02.2026, Karneval)
#   Verhältnis           0,55
#
# Die dünnste echte Woche liegt also bei 55 Prozent des Medians. Ein Viertel
# lässt zwischen ihr und dem Auslöser den Faktor 2,2 — genug, dass ein ruhiger
# Zeitraum nicht beanstandet wird, und eng genug, dass ein halbierter Abruf
# nicht durchkommt.
ANTEIL = 0.25

# So viele frühere Läufe braucht es, bevor überhaupt ein Mengen-Urteil gefällt
# wird. Darunter gibt es keinen Vorlauf, an dem sich messen ließe — eine neu
# angebundene Stadt wird also nicht auf Verdacht beanstandet. Der leere Abruf
# bleibt davon unberührt, den fängt tracker.run ohne jeden Vorlauf.
MIN_LAEUFE = 5

# So viele frühere Läufe gehen höchstens ein. Bei einem täglichen Lauf sind das
# rund drei Wochen: träge genug, dass ein einzelner ruhiger Tag den Maßstab
# nicht verschiebt, beweglich genug, dass eine dauerhaft gewachsene Stadt nicht
# ewig an alten Zahlen gemessen wird.
MAX_LAEUFE = 20


def lies_vorlauf(conn, stadt: str, ist_zeitraum_abruf: bool,
                 max_laeufe: int = MAX_LAEUFE) -> list[float]:
    """Die letzten Läufe dieser Stadt, umgerechnet auf eine vergleichbare Größe.

    Rückgabe ist eine Liste von Werten je Lauf — bei einem Zeitraum-Abruf
    Meldungen **je Tag**, bei einem Bestandsabruf der Bestand selbst.

    Ausgeschlossen sind Läufe ohne Ergebnis (``count_total <= 0``, das schließt
    die Fehlermarke -1 aus H-02b ein) und Läufe, die selbst als unplausibel
    vermerkt wurden. Beides sind Ausfälle und kein Maßstab.
    """
    zeilen = conn.execute("""
        SELECT count_total, zeitraum_tage
          FROM fetch_log
         WHERE stadt = ?
           AND count_total > 0
           AND (plausibel IS NULL OR plausibel = 1)
         ORDER BY id DESC
         LIMIT ?
    """, (stadt, max_laeufe * 4)).fetchall()

    werte: list[float] = []
    for zeile in zeilen:
        gesamt = zeile[0]
        tage = zeile[1]
        if ist_zeitraum_abruf:
            # Ein Bestandsabruf ist keine Vergleichsgröße für ein Fenster:
            # Berlins 105.456 gegen Kölns Woche zu halten, wäre Unsinn.
            if tage is None or tage <= 0:
                continue
            werte.append(gesamt / tage)
        else:
            if tage is not None:
                continue
            werte.append(float(gesamt))
        if len(werte) >= max_laeufe:
            break
    return werte


def basis(werte: list[float]) -> float | None:
    """Der Maßstab aus dem Vorlauf: der Median, nicht der Mittelwert.

    Der Median deshalb, weil ein einzelner Ausreißer nach oben (ein
    nachgeholter Abruf, ein Rückimport) den Mittelwert anhebt und die Prüfung
    danach an einer Zahl misst, die es im Alltag nie gab.
    """
    if len(werte) < MIN_LAEUFE:
        return None
    geordnet = sorted(werte)
    mitte = len(geordnet) // 2
    if len(geordnet) % 2:
        return geordnet[mitte]
    return (geordnet[mitte - 1] + geordnet[mitte]) / 2


def untergrenze(basis_wert: float | None, tage: float | None,
                anteil: float = ANTEIL) -> int:
    """Wie viele Meldungen dieser Abruf mindestens tragen muss.

    ``0`` heißt: kein Mengen-Urteil möglich. Das ist kein Durchwinken — der
    vollständig leere Abruf wird davon unabhängig in ``tracker.run`` gefangen.
    Es heißt nur, dass der Vorlauf für eine Aussage über die *Menge* nicht
    reicht.

    Bewusst **ohne** den Sockel ``max(1, ...)`` aus ``pruefe_plausibel``: der
    Sockel war genau der Grund, warum die Prüfung bei dünner Dichte auf "eine
    Meldung" zusammenfiel und damit nichts mehr aussagte. Hier entsteht die
    Zahl aus dem eigenen Vorlauf und braucht keinen Ersatzwert.
    """
    if basis_wert is None:
        return 0
    erwartet = anteil * basis_wert
    if tage is not None:
        erwartet *= tage
    return int(erwartet)


def pruefe(conn, stadt: str, anzahl: int, tage: float | None,
           anteil: float = ANTEIL) -> dict:
    """Der Riegel. Wirft ``AbrufUnplausibel``, wenn der Abruf zu dünn ist.

    ``tage`` ist die Fensterlänge des Abrufs oder ``None`` für einen
    Bestandsabruf. Rückgabe ist ein Vermerk für das Protokoll und für
    ``fetch_log``.
    """
    ist_zeitraum_abruf = tage is not None
    werte = lies_vorlauf(conn, stadt, ist_zeitraum_abruf)
    basis_wert = basis(werte)
    grenze = untergrenze(basis_wert, tage, anteil)

    vermerk = {
        "stadt": stadt,
        "anzahl": anzahl,
        "tage": tage,
        "vorlauf_laeufe": len(werte),
        "basis": basis_wert,
        "untergrenze": grenze,
    }

    if grenze <= 0:
        log.info("Mengenriegel für %s ohne Urteil: %d frühere Läufe im Vorlauf, "
                 "nötig sind %d. Der Abruf mit %d Meldungen wird nicht an einer "
                 "Menge gemessen.", stadt, len(werte), MIN_LAEUFE, anzahl)
        return vermerk

    if anzahl < grenze:
        einheit = (f"{basis_wert:.1f} je Tag auf {tage:.1f} Tage"
                   if ist_zeitraum_abruf else f"{basis_wert:.0f} im Bestand")
        raise AbrufUnplausibel(
            f"Abruf für {stadt}: {anzahl} Meldungen erhalten, mindestens "
            f"{grenze} erwartet. Der eigene Vorlauf aus {len(werte)} früheren "
            f"Läufen liegt bei {einheit}; der Auslöser steht bei "
            f"{anteil:.0%} davon. Eine so dünne Antwort wird NICHT als 'keine "
            f"Meldungen' gewertet, sondern als Ausfall. Prüfen: ist die "
            f"Schnittstelle erreichbar, steht der Zeitraum richtig, wurde die "
            f"Seitenzahl mitgegeben?")

    log.info("Mengenriegel für %s bestanden: %d Meldungen, Untergrenze %d "
             "(Vorlauf %d Läufe).", stadt, anzahl, grenze, len(werte))
    return vermerk
