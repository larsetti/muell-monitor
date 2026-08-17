#!/usr/bin/env python3
"""
Sicherung der Betriebsdatenbank (T-64, 15.08.2026)
===================================================
Anlass ist Befund S-03 der Abnahme vom 15.08.2026. Drei Umstaende trafen
zusammen: ordnungsamt.db ist gitignoriert und existiert genau einmal, sie
ueberlebt also keinen Rechnerwechsel; `retention.py --apply` schrieb weder nach
tracker.log noch ins Abrufprotokoll; und genau dieser Weg hat am 15.08.2026
rund 710 unwiederbringliche Meldungen entfernt. Der Berliner Bestand ist nicht
nachbeschaffbar — die Schnittstelle war ein rollendes Fenster, erledigte
Meldungen fielen nach 30 Tagen heraus, und die Behoerde hat die Jahre 2024 und
2025 selbst nicht mehr.

WOHIN GESICHERT WIRD, und warum ausgerechnet dorthin
-----------------------------------------------------
Vorgabewert ist `C:\\Users\\larsw\\Sicherungen\\Muell-Monitor\\`, also ein
gewoehnlicher lokaler Ordner ausserhalb jedes Cloud-Ordners. Das ist Absicht
und keine Bequemlichkeit:

  - Eine 43-MB-Datei, die sich bei jedem Lauf vollstaendig aendert, gehoert
    nicht in einen synchronisierten Ordner. OneDrive, Nextcloud und iCloud
    wuerden sie bei jedem Lauf neu hochladen und dabei gegeneinander laufen.
  - Eine Sicherung im selben Ordner wie das Original teilt dessen Schicksal.
    Genau das war bei `ordnungsamt.db.bak-2026-08-03-vor-a14` (T-41) und
    `ordnungsamt_test_copy.db` (T-48) der Fall: beide lagen im Technik-Ordner,
    beide wurden zu Altlasten, beide mussten am 14.08.2026 einzeln geprueft und
    entsorgt werden.
  - Der Bestand enthaelt Ortsdaten. Eine Kopie in einen geteilten Ordner zu
    legen, waere eine Uebermittlung an einen Auftragsverarbeiter, die weder in
    der Folgenabschaetzung noch im Verarbeitungsverzeichnis steht.

Deshalb prueft `pruefe_ziel()` den Zielpfad und WEIGERT sich fail-closed, in
einen bekannten Sync-Ordner zu schreiben. Die Liste steht in SYNC_ORDNER.

DIE SICHERUNG ERZEUGT KEINE ZWEITE ALTLAST
-------------------------------------------
Aus T-41 und T-48 kommt die Lehre, dass eine Kopie, um die sich niemand
kuemmert, selbst zum Problem wird. `aufraeumen()` begrenzt den Bestand deshalb
doppelt, und beide Grenzen greifen unabhaengig voneinander:

  - nach ANZAHL (`--behalten`, Vorgabe 7): mehr als sieben Staende sind fuer
    einen taeglichen Lauf ohne Nutzen.
  - nach ALTER (`--max-alter-tage`, Vorgabe 30): das ist die datenschutz-
    rechtlich wichtigere Grenze. Eine Sicherung haelt geloeschte Meldungen
    weiter vor und verlaengert damit faktisch die Aufbewahrung nach Art. 5
    Abs. 1 lit. e DSGVO. Dreissig Tage sind die Frist, die die
    Folgenabschaetzung ohnehin schon fuer den Wegfall aus der Quelle setzt;
    laenger darf eine Sicherung den Bestand nicht ueberleben.

WARUM sqlite3.backup UND NICHT shutil.copy
-------------------------------------------
Am 15.08.2026 lief waehrend der Abnahme ein Rueckimport gegen dieselbe Datei.
Eine Dateikopie haette dabei einen zerrissenen Stand erwischt. Die
Backup-Schnittstelle von SQLite zieht dagegen einen in sich stimmigen Stand,
auch waehrend geschrieben wird, und `PRAGMA integrity_check` belegt das
anschliessend.

Der Preis dafuer: die Sicherung ist fachlich gleich, aber NICHT byte-gleich zur
Quelle — freie Seiten und Seitenreihenfolge koennen abweichen. Die
ausgewiesene SHA-256-Summe gehoert deshalb zur Sicherungsdatei selbst und ist
kein Vergleichswert gegen das Original. Wer beide Summen gegeneinander haelt
und Gleichheit erwartet, verwechselt die Backup-Schnittstelle mit shutil.copy.
Der Beleg dafuer, dass nichts fehlt, sind `PRAGMA integrity_check` und die
Zaehlungen je Stadt, die bei jedem Lauf mitprotokolliert werden.

Aufruf:
    python sicherung.py                       # sichern und aufraeumen
    python sicherung.py --liste               # vorhandene Staende anzeigen
    python sicherung.py --anlass vor-t70      # Anlass im Dateinamen vermerken
    python sicherung.py --behalten 3 --max-alter-tage 14
"""

import argparse
import hashlib
import logging
import os
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

DB_PATH = Path(__file__).parent / "ordnungsamt.db"
LOG_DATEI = Path(__file__).parent / "tracker.log"

# Vorgabeziel: lokal, ausserhalb jedes Cloud-Ordners. Ueberschreibbar per
# Umgebungsvariable, damit der Heim-Server (T-54) denselben Code mit eigenem
# Pfad benutzen kann, ohne dass hier ein zweiter Zweig entsteht.
ZIEL_VORGABE = Path(os.environ.get(
    "MM_SICHERUNG_ZIEL", r"C:\Users\larsw\Sicherungen\Muell-Monitor"))

BEHALTEN_VORGABE = int(os.environ.get("MM_SICHERUNG_BEHALTEN", "7"))
MAX_ALTER_TAGE_VORGABE = int(os.environ.get("MM_SICHERUNG_MAX_ALTER_TAGE", "30"))

PRAEFIX = "ordnungsamt.db."

# Pfadbestandteile, die einen synchronisierten Ordner verraten. Kleingeschrieben
# verglichen. Die Liste stammt aus der globalen Vorgabe "Sync-Ordner sind
# Lesequellen" vom 31.07.2026.
SYNC_ORDNER = ("onedrive", "nextcloud", "icloud", "icloudphotos",
               "icloud drive", "dropbox", "google drive", "googledrive",
               "sharepoint", "box sync", "pcloud", "mega", "sync.com")


class ZielUnzulaessig(Exception):
    """Der Zielordner liegt in einem synchronisierten Baum oder im Projekt."""


def _log() -> logging.Logger:
    """Eigener Logger auf dieselbe Datei wie tracker.py.

    Bewusst NICHT ueber `import tracker`: tracker importiert retention, und
    retention ruft dieses Modul auf. Ein Import waere ein Zirkel. Der Handler
    wird nur einmal gehaengt, damit ein zweiter Aufruf keine Doppelzeilen
    erzeugt.
    """
    log = logging.getLogger("sicherung")
    if not log.handlers:
        log.setLevel(logging.INFO)
        formatierer = logging.Formatter(
            "%(asctime)s [%(levelname)s] %(message)s")
        datei = logging.FileHandler(LOG_DATEI, encoding="utf-8")
        datei.setFormatter(formatierer)
        log.addHandler(datei)
        strom = logging.StreamHandler()
        strom.setFormatter(formatierer)
        log.addHandler(strom)
    return log


def pruefe_ziel(ziel: Path, quelle: Path = None) -> Path:
    """Fail-closed: weist synchronisierte Ordner und den Projektordner ab.

    Der zweite Teil ist die Lehre aus T-41 und T-48 — eine Sicherung neben dem
    Original teilt dessen Schicksal und wird uebersehen, bis sie eine Altlast
    ist.
    """
    ziel = Path(ziel).expanduser()
    teile = [t.lower() for t in ziel.absolute().parts]
    # Verglichen wird am ANFANG eines Pfadbestandteils, nicht irgendwo darin.
    # So werden 'OneDrive', 'OneDrive - Firma', 'Nextcloud3' und 'iCloudPhotos'
    # erkannt, ein Arbeitsordner namens 'C--Users-larsw-OneDrive-Business' aber
    # nicht — der enthaelt den Namen nur, er IST kein Sync-Ordner. Ein reiner
    # Teilstring-Vergleich hat genau diesen Fall am 15.08.2026 falsch geblockt.
    for verdaechtig in SYNC_ORDNER:
        if any(t.startswith(verdaechtig) for t in teile):
            raise ZielUnzulaessig(
                f"'{ziel}' liegt unter '{verdaechtig}' und wird damit von einem "
                f"Cloud-Dienst synchronisiert. Eine 43-MB-Datei, die sich bei "
                f"jedem Lauf vollstaendig aendert, gehoert dort nicht hin — und "
                f"der Bestand enthaelt Ortsdaten, deren Uebermittlung an einen "
                f"Auftragsverarbeiter weder in der Folgenabschaetzung noch im "
                f"Verarbeitungsverzeichnis steht. Ein lokaler Ordner ausserhalb "
                f"jedes Cloud-Baums, etwa {ZIEL_VORGABE}."
            )
    quelle = Path(quelle or DB_PATH).absolute()
    if ziel.absolute() == quelle.parent:
        raise ZielUnzulaessig(
            f"'{ziel}' ist der Ordner der Datenbank selbst. Eine Sicherung "
            f"daneben teilt jedes Schicksal des Originals und wird uebersehen, "
            f"bis sie eine Altlast ist (siehe T-41 und T-48)."
        )
    return ziel


def sha256(pfad: Path) -> str:
    h = hashlib.sha256()
    with open(pfad, "rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def _stand(conn: sqlite3.Connection) -> dict:
    """Zaehlt, was in der Sicherung steckt — damit im Protokoll steht, WAS
    gesichert wurde und nicht nur DASS gesichert wurde."""
    zahlen = {}
    try:
        zahlen["meldungen"] = dict(conn.execute(
            "SELECT stadt, COUNT(*) FROM meldungen GROUP BY stadt").fetchall())
        zahlen["hotspots"] = dict(conn.execute(
            "SELECT stadt, COUNT(*) FROM hotspots GROUP BY stadt").fetchall())
    except sqlite3.OperationalError:
        pass
    return zahlen


def vorhandene(ziel: Path) -> list[Path]:
    if not ziel.is_dir():
        return []
    return sorted((p for p in ziel.iterdir()
                   if p.is_file() and p.name.startswith(PRAEFIX)),
                  key=lambda p: p.stat().st_mtime, reverse=True)


def aufraeumen(ziel: Path, behalten: int = BEHALTEN_VORGABE,
               max_alter_tage: int = MAX_ALTER_TAGE_VORGABE,
               jetzt: datetime = None, dry_run: bool = False) -> list[Path]:
    """Begrenzt den Sicherungsbestand nach Anzahl UND Alter.

    Beide Grenzen greifen unabhaengig: was zu alt ist, faellt auch dann, wenn
    weniger als `behalten` Staende da sind. Die juengste Sicherung bleibt aber
    immer stehen — eine Altersgrenze darf nicht dazu fuehren, dass nach einer
    laengeren Pause gar keine Sicherung mehr existiert.
    """
    jetzt = jetzt or datetime.now()
    alle = vorhandene(ziel)
    if not alle:
        return []
    grenze = jetzt - timedelta(days=max_alter_tage)
    zu_loeschen = []
    for i, p in enumerate(alle):
        if i == 0:
            continue  # die juengste bleibt immer
        if i >= behalten:
            zu_loeschen.append(p)
        elif datetime.fromtimestamp(p.stat().st_mtime) < grenze:
            zu_loeschen.append(p)
    if not dry_run:
        for p in zu_loeschen:
            p.unlink()
    return zu_loeschen


def sichere(quelle: Path = None, ziel: Path = None, anlass: str = "",
            behalten: int = BEHALTEN_VORGABE,
            max_alter_tage: int = MAX_ALTER_TAGE_VORGABE,
            jetzt: datetime = None, protokoll: bool = True) -> dict:
    """Zieht einen in sich stimmigen Stand und raeumt den Bestand auf.

    Gibt ein Wortverzeichnis mit Pfad, Groesse, Pruefsumme und Zaehlungen
    zurueck. Wirft ZielUnzulaessig, wenn das Ziel synchronisiert wird — die
    Sicherung findet dann NICHT statt, statt still an den falschen Ort zu
    laufen.
    """
    quelle = Path(quelle or DB_PATH)
    if not quelle.is_file():
        raise FileNotFoundError(f"Keine Datenbank unter {quelle}")
    ziel = pruefe_ziel(ziel or ZIEL_VORGABE, quelle)
    ziel.mkdir(parents=True, exist_ok=True)

    jetzt = jetzt or datetime.now()
    marke = jetzt.strftime("%Y-%m-%dT%H%M%S")
    name = PRAEFIX + marke + (f"-{anlass}" if anlass else "")
    pfad = ziel / name

    # Lesend oeffnen und mit der Backup-Schnittstelle ziehen: das liefert auch
    # dann einen stimmigen Stand, wenn parallel geschrieben wird.
    lesend = sqlite3.connect("file:" + quelle.as_posix() + "?mode=ro", uri=True)
    schreibend = sqlite3.connect(str(pfad))
    try:
        lesend.backup(schreibend)
        zahlen = _stand(schreibend)
        integritaet = schreibend.execute("PRAGMA integrity_check").fetchone()[0]
    finally:
        schreibend.close()
        lesend.close()

    if integritaet != "ok":
        pfad.unlink(missing_ok=True)
        raise RuntimeError(
            f"Die gezogene Sicherung besteht PRAGMA integrity_check nicht "
            f"({integritaet}). Sie wurde verworfen, damit kein unbrauchbarer "
            f"Stand als Sicherung gilt.")

    entfernt = aufraeumen(ziel, behalten=behalten,
                          max_alter_tage=max_alter_tage, jetzt=jetzt)

    ergebnis = {
        "pfad": pfad,
        "groesse": pfad.stat().st_size,
        "sha256": sha256(pfad),
        "integritaet": integritaet,
        "zahlen": zahlen,
        "entfernt": entfernt,
        "vorhanden": len(vorhandene(ziel)),
    }

    if protokoll:
        meldungen = ", ".join(f"{s} {n}" for s, n in
                              sorted(ergebnis["zahlen"].get("meldungen", {}).items()))
        _log().info(
            "Sicherung: %s (%.1f MB, sha256 %s..., integrity_check %s) — "
            "Meldungen: %s — %d Stand(e) im Ziel, %d alte entfernt%s",
            pfad, ergebnis["groesse"] / 1024 / 1024, ergebnis["sha256"][:12],
            integritaet, meldungen or "keine", ergebnis["vorhanden"],
            len(entfernt), f" (Anlass: {anlass})" if anlass else "")
    return ergebnis


def main():
    p = argparse.ArgumentParser(
        description="Sicherung der Betriebsdatenbank an einen Ort ausserhalb "
                    "des Projektordners und ausserhalb jedes Cloud-Ordners")
    p.add_argument("--db", default=str(DB_PATH), help="Pfad zur Datenbank")
    p.add_argument("--ziel", default=str(ZIEL_VORGABE), help="Zielordner")
    p.add_argument("--anlass", default="",
                   help="kurzer Vermerk im Dateinamen, etwa 'vor-t70'")
    p.add_argument("--behalten", type=int, default=BEHALTEN_VORGABE,
                   help=f"wie viele Staende bleiben (Vorgabe {BEHALTEN_VORGABE})")
    p.add_argument("--max-alter-tage", type=int, default=MAX_ALTER_TAGE_VORGABE,
                   help=f"aeltere Staende fallen weg (Vorgabe "
                        f"{MAX_ALTER_TAGE_VORGABE} Tage)")
    p.add_argument("--liste", action="store_true",
                   help="vorhandene Staende anzeigen, nichts schreiben")
    p.add_argument("--wenn-vorhanden", action="store_true",
                   help="fehlt die Datenbank noch, ist das kein Fehler "
                        "(Rueckgabewert 0). Fuer die taeglichen Laeufe.")
    args = p.parse_args()

    try:
        ziel = pruefe_ziel(Path(args.ziel), Path(args.db))
    except ZielUnzulaessig as e:
        print(f"ABBRUCH: {e}", file=sys.stderr)
        return 2

    if args.liste:
        staende = vorhandene(ziel)
        print(f"Zielordner: {ziel}")
        if not staende:
            print("  Keine Sicherung vorhanden.")
        for s in staende:
            groesse = s.stat().st_size / 1024 / 1024
            zeit = datetime.fromtimestamp(s.stat().st_mtime)
            print(f"  {s.name}  {groesse:.1f} MB  {zeit:%Y-%m-%d %H:%M}")
        print(f"\n  {len(staende)} Stand(e).")
        return 0

    # T-64, letzter Handgriff: die drei Launcher rufen dieses Skript fail-closed
    # vor dem Tracker auf — ohne Sicherung wird nicht erfasst, weil der Tracker
    # ueber retention loescht und der Berliner Bestand nicht nachbeschaffbar ist.
    # Genau diese Kette wuerde einen frischen Aufbau aber DAUERHAFT blockieren:
    # dort gibt es noch keine Datenbank, sichere() wirft FileNotFoundError, der
    # Tracker liefe nie, und weil er nie liefe, entstuende auch nie eine
    # Datenbank. Deshalb ist "noch nichts da" hier ausdruecklich kein Fehler —
    # es gibt nichts zu verlieren. Ein FEHLGESCHLAGENER Sicherungsversuch bleibt
    # einer, auch mit diesem Zusatz.
    if args.wenn_vorhanden and not Path(args.db).is_file():
        print(f"Keine Datenbank unter {args.db} — nichts zu sichern. Das ist "
              f"beim ersten Lauf der Normalfall und kein Fehler.")
        return 0

    try:
        e = sichere(Path(args.db), ziel, anlass=args.anlass,
                    behalten=args.behalten, max_alter_tage=args.max_alter_tage)
    except (ZielUnzulaessig, FileNotFoundError, RuntimeError) as fehler:
        print(f"ABBRUCH: {fehler}", file=sys.stderr)
        return 2

    print(f"Gesichert: {e['pfad']}")
    print(f"  Groesse:         {e['groesse']:,} Byte")
    print(f"  SHA-256:         {e['sha256']}")
    print(f"  integrity_check: {e['integritaet']}")
    for tabelle, werte in e["zahlen"].items():
        print(f"  {tabelle}: " + ", ".join(f"{s} {n}" for s, n in sorted(werte.items())))
    print(f"  Staende im Ziel: {e['vorhanden']}")
    if e["entfernt"]:
        print(f"  Alte entfernt:   {len(e['entfernt'])}")
        for a in e["entfernt"]:
            print(f"    {a.name}")
    print("\nWas diese Sicherung NICHT leistet: sie liegt auf derselben "
          "Maschine.\nGegen Plattenausfall, Diebstahl, Feuer oder "
          "Verschluesselungstrojaner\nschuetzt sie nicht. Dafuer braucht es "
          "die Offline-Festplatte.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
