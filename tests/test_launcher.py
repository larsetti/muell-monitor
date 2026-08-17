"""Tests für die drei Launcher (T-62, 15.08.2026).

Der Befund, der zu dieser Datei geführt hat: die neue Städte-Struktur steckte
hinter dem Zusatz --staedte, die drei Launcher riefen aber "python
export_html.py" ohne Zusatz auf. Gebaut wurde damit täglich eine Seite, die
"Müll-Hotspot Monitor Berlin" heißt und trotzdem 4.688 statt 4.660 Standorte,
75.297 statt 74.397 Meldungen und "Ehrenfeld" in der Berliner Bezirksauswahl
zeigt, mit dem Datum des jüngsten Kölner Abrufs im Datenstand-Streifen statt
des 22.04.2026. Das war wörtlich Befund H-04, den T-39 am 13.08.2026
geschlossen hatte, nur über einen anderen Weg zurückgekommen (K-01 der Abnahme
vom 15.08.2026). Dazu K-02: Köln wurde von keinem der drei Automatik-Wege je
abgerufen.

Diese Tests prüfen nicht den Wortlaut der Launcher, sondern ihre Wirkung. Die
Argumente, die im Launcher hinter export_html.py stehen, werden ausgeschnitten
und durch die echte Befehlszeilen-Auswertung geschickt. Was dabei entsteht,
muss die Städte-Struktur sein. Ein Launcher, der wieder stadtblind baut, wird
hier rot, ganz gleich wie er das schreibt.
"""
import re
import sys
from pathlib import Path

import pytest

TECHNIK = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(TECHNIK))

import export_html  # noqa: E402
import quellen  # noqa: E402


LAUNCHER = {
    "run_tracker.bat": TECHNIK / "run_tracker.bat",
    "daily_update.yml": TECHNIK / ".github" / "workflows" / "daily_update.yml",
    "pi_setup.sh": TECHNIK / "pi_setup.sh",
}


def _inhalt(name: str) -> str:
    pfad = LAUNCHER[name]
    assert pfad.exists(), f"Launcher {name} fehlt unter {pfad}"
    return pfad.read_text(encoding="utf-8")


def _ohne_kommentare(text: str) -> str:
    """Kommentarzeilen weg, sonst zählt jede Erklärung als Aufruf mit.

    Die Kommentare dieser drei Dateien zitieren, was vorher dort stand — und
    das ist genau der Aufruf, den diese Tests verbieten sollen.
    """
    return "\n".join(z for z in text.splitlines()
                     if not z.lstrip().startswith(("#", "::", "rem ", "REM ")))


def _aufrufe(text: str, skript: str) -> list[list[str]]:
    """Alle Aufrufe von <skript> in einem Launcher, je als Argumentliste.

    Das "python" davor ist Pflicht, sonst würde jede Erwähnung des Dateinamens
    im Fließtext mitgezählt. Ein Aufruf endet am Zeilenende oder am nächsten
    Trennzeichen der Shell.
    """
    muster = rf"python[0-9.]*\s+{re.escape(skript)}([^\n;&|]*)"
    return [treffer.group(1).split()
            for treffer in re.finditer(muster, _ohne_kommentare(text))]


# ── K-02: jede Stadt wird von jedem Automatik-Weg abgerufen ──────────────────

@pytest.mark.parametrize("name", sorted(LAUNCHER))
@pytest.mark.parametrize("stadt", sorted(quellen.ALLE))
def test_jeder_launcher_ruft_jede_stadt_ab(name, stadt):
    """Eine Quelle in quellen.ALLE, die kein Launcher abruft, ist gebaut und
    tot. Genau das war Köln zwischen dem 15.08.2026 und diesem Test."""
    aufrufe = _aufrufe(_inhalt(name), "tracker.py")
    assert aufrufe, f"{name} ruft tracker.py gar nicht auf"
    getroffen = [a for a in aufrufe if "--stadt" in a
                 and a[a.index("--stadt") + 1:a.index("--stadt") + 2] == [stadt]]
    assert getroffen, (
        f"{name} ruft tracker.py nie mit --stadt {stadt} auf. Ohne die "
        f"ausdrückliche Angabe läuft nur die Vorgabe {export_html.LEGACY_STADT}, "
        f"und {stadt} wird im Betrieb nie abgerufen.")


@pytest.mark.parametrize("name", sorted(LAUNCHER))
def test_kein_tracker_aufruf_ohne_stadt(name):
    """Ein Aufruf ohne --stadt läuft stillschweigend die Vorgabe. Sobald eine
    dritte Stadt dazukommt, sieht so ein Aufruf aus wie "alle" und ist es
    nicht."""
    for argumente in _aufrufe(_inhalt(name), "tracker.py"):
        assert "--stadt" in argumente, (
            f"{name}: Aufruf von tracker.py ohne --stadt "
            f"(Argumente: {argumente or 'keine'})")


# ── K-01: kein Launcher darf den Bau wieder stadtblind auslösen ─────────────

@pytest.mark.parametrize("name", sorted(LAUNCHER))
def test_jeder_launcher_baut_die_staedte_struktur(name, tmp_path, monkeypatch):
    """Der Kern dieser Datei.

    Die Argumente aus dem Launcher gehen durch die echte Auswertung. Entsteht
    dabei eine einzelne Seite statt der Städte-Struktur, ist der Befund K-01
    zurück und dieser Test rot.
    """
    aufrufe = _aufrufe(_inhalt(name), "export_html.py")
    assert len(aufrufe) == 1, (
        f"{name} ruft export_html.py {len(aufrufe)}-mal auf, erwartet ist "
        f"genau einmal")

    monkeypatch.setattr(export_html, "ROOT", tmp_path)
    monkeypatch.setattr(export_html, "OUT_PATH", tmp_path / "SOLL_NICHT_ENTSTEHEN.html")
    code = export_html.cli(aufrufe[0])

    assert code == 0, f"{name}: Bau meldete Exit-Code {code}"
    assert not (tmp_path / "SOLL_NICHT_ENTSTEHEN.html").exists(), (
        f"{name} baut noch die alte Einzelseite statt der Städte-Struktur")
    # Geprüft wird gegen die ausgelieferten Städte, nicht gegen STAEDTE. Seit
    # T-74 (17.08.2026) sind das zwei verschiedene Mengen: STAEDTE ist das
    # vollständige Gerüst, ausgeliefert wird davon eine Auswahl.
    for stadt in export_html.ausgelieferte_staedte():
        seite = tmp_path / stadt.slug / "index.html"
        assert seite.exists(), f"{name}: {stadt.name} wurde nicht gebaut"
    startseite = (tmp_path / "index.html").read_text(encoding="utf-8")
    for stadt in export_html.ausgelieferte_staedte():
        assert f'href="{stadt.slug}/"' in startseite, (
            f"{name}: Die Startseite verweist nicht auf /{stadt.slug}/")


# ── T-74: eine zurückgehaltene Stadt darf der Bau nicht wieder anlegen ──────

@pytest.mark.parametrize("name", sorted(LAUNCHER))
def test_kein_launcher_baut_eine_zurueckgehaltene_stadt(name, tmp_path, monkeypatch):
    """Der eigentliche Riegel hinter T-74.

    Köln soll auf muell-monitor.de 404 liefern (Lars-Entscheidung 17.08.2026).
    Die Datei aus dem Speicher zu nehmen reicht dafür nicht: bei GitHub Pages
    ist der Speicher die Seite, und der tägliche Lauf baut die Stadtseiten neu
    und pusht sie mit "git add */index.html". Ohne diesen Riegel wäre die
    Adresse am Tag nach dem Entfernen wieder da.

    Geprüft wird deshalb die Wirkung des echten Launcher-Aufrufs: keine Seite
    und kein Verweis von der Startseite. Zusätzlich, dass eine vorhandene alte
    Seite dabei weggeräumt wird — sonst bliebe genau die Fassung liegen, die
    weg soll.
    """
    zurueckgehalten = [s for s in export_html.STAEDTE if not s.veroeffentlicht]
    if not zurueckgehalten:
        pytest.skip("derzeit wird keine Stadt zurückgehalten")

    monkeypatch.setattr(export_html, "ROOT", tmp_path)
    monkeypatch.setattr(export_html, "OUT_PATH", tmp_path / "SOLL_NICHT_ENTSTEHEN.html")
    for stadt in zurueckgehalten:
        alt = tmp_path / stadt.slug / "index.html"
        alt.parent.mkdir(parents=True, exist_ok=True)
        alt.write_text("<html>ALTE WARTUNGSSEITE</html>", encoding="utf-8")

    export_html.cli(_aufrufe(_inhalt(name), "export_html.py")[0])

    startseite = (tmp_path / "index.html").read_text(encoding="utf-8")
    for stadt in zurueckgehalten:
        assert not (tmp_path / stadt.slug / "index.html").exists(), (
            f"{name}: {stadt.name} ist wieder gebaut worden, die Adresse "
            f"/{stadt.slug}/ antwortet damit wieder")
        assert f'href="{stadt.slug}/"' not in startseite, (
            f"{name}: Die Startseite verweist auf /{stadt.slug}/, das 404 "
            f"liefert")
        umlaut = export_html.UMLAUT_PFADE.get(stadt.slug)
        if umlaut:
            assert not (tmp_path / umlaut / "index.html").exists(), (
                f"{name}: /{umlaut}/ leitet auf eine Adresse weiter, die 404 "
                f"liefert")


def test_das_geruest_bleibt_vollstaendig():
    """Zurückgehalten ist nicht zurückgebaut.

    Die Städte-Struktur wird gebraucht, sobald die anwaltliche Freigabe (T-03)
    da ist. T-74 nimmt eine Veröffentlichung weg, kein Merkmal — der Eintrag,
    die Quelle und der eigene Freigabe-Schalter bleiben stehen.
    """
    slugs = {s.slug for s in export_html.STAEDTE}
    assert {"berlin", "koeln"} <= slugs, (
        f"Eine Stadt ist aus STAEDTE verschwunden, vorhanden: {sorted(slugs)}")
    for slug in ("berlin", "koeln"):
        assert slug in quellen.ALLE, f"{slug} hat keine Quelle mehr"


@pytest.mark.parametrize("name", sorted(LAUNCHER))
def test_kein_launcher_nimmt_den_ausnahmeweg(name):
    """--eine-seite ist der Notweg von Hand, nicht der tägliche Betrieb.

    Der Test oben würde das auch fangen. Diese Fassung sagt im Fehlerfall
    deutlicher, was passiert ist.
    """
    for argumente in _aufrufe(_inhalt(name), "export_html.py"):
        assert "--eine-seite" not in argumente, (
            f"{name} baut wieder eine Einzelseite. Der tägliche Lauf muss die "
            f"Städte-Struktur bauen, sonst mischt eine Seite die Städte.")


# ── T-49 (a): die gebauten Ordner müssen auch gepusht werden ────────────────

@pytest.mark.parametrize("name", sorted(LAUNCHER))
def test_jeder_launcher_stellt_die_stadtseiten_ein(name):
    """Die Startseite verweist auf /berlin/ und /koeln/. Nimmt der Push die
    Ordner nicht mit, zeigen beide Kacheln auf 404."""
    text = _inhalt(name)
    zeilen = [z for z in text.splitlines()
              if "git add" in z and not z.lstrip().startswith(("#", "::"))]
    assert zeilen, f"{name} hat keine git-add-Zeile"
    for zeile in zeilen:
        assert "index.html" in zeile
        abgedeckt = "*/index.html" in zeile or all(
            f"{s.slug}/" in zeile or f"{s.slug}\\" in zeile
            for s in export_html.STAEDTE)
        assert abgedeckt, (
            f"{name}: git add nimmt die Stadtordner nicht mit: {zeile.strip()}")


# ── T-64: der tägliche Lauf sichert, bevor er löscht ────────────────────────
#
# Der tägliche Lauf ist die einzige Automatik, die Daten *entfernt*: `tracker.run`
# ruft am Ende `retention.anwenden` auf, und die läuft über jede Stadt. Sobald
# Köln täglich läuft, tickt Berlins Frist mit jedem Kölner Lauf weiter — und der
# Berliner Bestand ist nicht nachbeschaffbar, die Quelle liefert seit dem
# 22.04.2026 nichts. `retention.py --apply` ist deshalb seit T-64 fail-closed;
# der tägliche Weg war es bis hierher nicht, obwohl er dasselbe tut.

# Woran in der jeweiligen Datei zu erkennen ist, dass die fail-closed-Kette
# wieder zu Ende ist. Alles danach läuft unabhängig von der Sicherung.
ENDE_DER_SPERRE = {
    "run_tracker.bat": ":nach_erfassung",   # Sprungmarke hinter beiden Zweigen
    "pi_setup.sh": "}",                     # das &&-Bündel ist geschlossen
}


def _stelle(text: str, skript: str) -> int:
    """Zeichenposition des ersten Aufrufs von <skript>, -1 wenn keiner da ist."""
    treffer = re.search(rf"python[0-9.]*\s+{re.escape(skript)}",
                        _ohne_kommentare(text))
    return treffer.start() if treffer else -1


@pytest.mark.parametrize("name", sorted(LAUNCHER))
def test_jeder_launcher_sichert_vor_der_erfassung(name):
    """Eine Sicherung, die nach dem Löschen gezogen wird, sichert den Zustand
    NACH dem Verlust. Die Reihenfolge ist der ganze Punkt."""
    text = _inhalt(name)
    sicherung = _stelle(text, "sicherung.py")
    tracker = _stelle(text, "tracker.py")

    assert sicherung >= 0, (
        f"{name} ruft sicherung.py nicht auf. Der tägliche Lauf löscht über "
        f"retention.anwenden, und zwar in jeder Stadt. Ohne Sicherung ist ein "
        f"Fehler dort unwiederbringlich — Berlin lässt sich nicht neu abrufen "
        f"(T-64).")
    assert tracker >= 0, f"{name} ruft tracker.py gar nicht auf"
    assert sicherung < tracker, (
        f"{name} sichert erst NACH dem ersten Tracker-Lauf. Damit hält die "
        f"Sicherung den Zustand nach dem Löschen fest und ist wertlos für "
        f"genau den Fall, für den es sie gibt (T-64).")


@pytest.mark.parametrize("name", sorted(LAUNCHER))
def test_die_sicherung_blockiert_keinen_frischen_aufbau(name):
    """`--wenn-vorhanden` ist kein Schönheitsfehler, sondern die Bedingung
    dafür, dass die fail-closed-Kette überhaupt anlaufen kann.

    Bei einem frischen Aufbau gibt es noch keine Datenbank: ohne den Zusatz
    scheitert die Sicherung, der Tracker läuft nie, und weil er nie läuft,
    entsteht auch nie eine Datenbank. Die Kette blockiert sich selbst.
    """
    aufrufe = _aufrufe(_inhalt(name), "sicherung.py")
    assert aufrufe, f"{name} ruft sicherung.py nicht auf"
    for argumente in aufrufe:
        assert "--wenn-vorhanden" in argumente, (
            f"{name}: sicherung.py ohne --wenn-vorhanden (Argumente: "
            f"{argumente or 'keine'}). Ein frischer Aufbau käme damit nie in "
            f"Gang.")


@pytest.mark.parametrize("name", sorted(LAUNCHER))
def test_export_und_push_haengen_nicht_an_der_sicherung(name):
    """Die Gegenprobe zur fail-closed-Kette: sie darf NICHT zu weit greifen.

    A-11 verlangt, dass gebaut und gepusht wird, auch wenn vorher etwas
    schiefging — die Wartungsseite *ist* die Abhilfe, und ein ausgelassener
    Push ließe eine alte Live-Seite mit Ortsdaten stehen. Die Sperre darf also
    nur die Erfassung anhalten, nicht den Rest.
    """
    text = _inhalt(name)
    assert _stelle(text, "export_html.py") >= 0, (
        f"{name} ruft export_html.py nicht auf")

    if name == "daily_update.yml":
        # In der Workflow-Datei hängt ein Schritt über "if:" am Ergebnis eines
        # anderen. Genau diese Bedingung darf nur an den Tracker-Schritten
        # stehen, nicht am Export und nicht am Commit.
        block = text.split("- name: Datenbank sichern", 1)[1]
        for abschnitt in block.split("- name: ")[1:]:
            titel = abschnitt.splitlines()[0].strip()
            haengt_ab = "steps.sicherung.outcome" in abschnitt.split("run:")[0]
            if titel.startswith("Tracker"):
                assert haengt_ab, f"Schritt '{titel}' hängt nicht an der Sicherung"
            else:
                assert not haengt_ab, (
                    f"Schritt '{titel}' hängt an der Sicherung. Export und Push "
                    f"müssen auch dann laufen, wenn vorher etwas schiefging "
                    f"(A-11).")
    else:
        # In Batch und Shell wird die Kette über eine Sprungmarke bzw. über ein
        # &&-Bündel gebaut. Geprüft wird, dass der Export HINTER deren Ende
        # steht. Ein Vergleich mit dem letzten Tracker-Aufruf wäre hier falsch:
        # pi_setup.sh nennt am Dateiende in einer echo-Zeile einen
        # Beispielaufruf, der genauso aussieht wie ein echter.
        ohne = _ohne_kommentare(text)
        ab_sicherung = ohne[_stelle(text, "sicherung.py"):]
        ende = ENDE_DER_SPERRE[name]
        assert ende in ab_sicherung, (
            f"{name}: das Ende der fail-closed-Kette ({ende!r}) ist nicht mehr "
            f"da. Entweder wurde die Kette umgebaut, dann diesen Test "
            f"nachziehen — oder sie umschliesst jetzt mehr als die Erfassung.")
        assert ab_sicherung.index(ende) < ab_sicherung.index("export_html.py"), (
            f"{name}: export_html.py steht INNERHALB der fail-closed-Kette. "
            f"Schlägt die Sicherung fehl, würde damit auch nicht mehr gebaut "
            f"und nicht mehr gepusht — und eine alte Live-Seite mit Ortsdaten "
            f"bliebe stehen (A-11).")


# ── Die Befehlszeile selbst ─────────────────────────────────────────────────

def test_ohne_argumente_entsteht_die_staedte_struktur(tmp_path, monkeypatch):
    """Der Normalweg ist seit T-62 die Städte-Struktur. Wer nichts angibt,
    bekommt sie."""
    monkeypatch.setattr(export_html, "ROOT", tmp_path)
    assert export_html.cli([]) == 0
    assert (tmp_path / "index.html").exists()
    assert (tmp_path / "berlin" / "index.html").exists()


def test_ziel_lenkt_den_bau_in_die_vorschau(tmp_path, monkeypatch):
    monkeypatch.setattr(export_html, "ROOT", tmp_path)
    vorschau = tmp_path / "_vorschau"
    assert export_html.cli(["--ziel", str(vorschau)]) == 0
    assert (vorschau / "berlin" / "index.html").exists()
    assert not (tmp_path / "berlin").exists(), (
        "Der Bau in die Vorschau hat die ausgelieferte Struktur angefasst")


def test_altlasten_schalter_aendern_nichts(tmp_path, monkeypatch):
    """--staedte und --jetzt-umstellen stehen noch in Merkzetteln und in der
    Projekt-CLAUDE.md. Sie dürfen nicht mit einem Fehler abbrechen und auch
    nichts anderes bewirken."""
    monkeypatch.setattr(export_html, "ROOT", tmp_path)
    ziel = tmp_path / "mit_altlast"
    assert export_html.cli(["--staedte", "--ziel", str(ziel),
                            "--jetzt-umstellen"]) == 0
    assert (ziel / "berlin" / "index.html").exists()


def test_ausnahmeweg_rendert_nur_eine_stadt(tmp_path, monkeypatch):
    """Der alte Weg ist nicht mehr stadtblind.

    Vorher rief main() render_live() ohne Stadt auf, also load_data(None) über
    den Gesamtbestand. Genau daraus entstand die Seite, die Berlin heißt und
    Köln mitzählt.
    """
    vorlage = tmp_path / "template.html"
    vorlage.write_text("<html>__APP_DATA_PLACEHOLDER__ __LAST_UPDATE__</html>",
                       encoding="utf-8")
    marker = tmp_path / "LIVE_FREIGEGEBEN"
    marker.write_text("", encoding="utf-8")

    gerufen = {}

    def merke(ziel=None, praefix="", stadt=None):
        gerufen["stadt"] = stadt

    monkeypatch.setattr(export_html, "TEMPLATE", vorlage)
    monkeypatch.setattr(export_html, "GO_LIVE_MARKER", marker)
    monkeypatch.setattr(export_html, "render_live", merke)

    assert export_html.cli(["--eine-seite"]) == 0
    assert gerufen["stadt"] == export_html.LEGACY_STADT, (
        f"Der Einzelseiten-Weg rendert {gerufen['stadt']!r} statt nur "
        f"{export_html.LEGACY_STADT!r} — das ist der stadtblinde Bau von K-01.")


# ── A-11 bleibt auf dem neuen Normalweg erhalten ────────────────────────────

def test_ohne_wartungsquelle_entsteht_die_ersatzseite(tmp_path, monkeypatch):
    """A-11 hing bisher am Einzelseiten-Weg.

    Fehlt maintenance.html, bricht der Städte-Bau ab. Ohne Klammer bliebe eine
    alte Live-Seite mit Ortsdaten an jeder Adresse stehen, obwohl der Lauf
    "fehlgeschlagen" meldet. Der Push passiert trotzdem, das ist die Zusage aus
    A-11.
    """
    monkeypatch.setattr(export_html, "ROOT", tmp_path)
    monkeypatch.setattr(export_html, "MAINTENANCE_PATH", tmp_path / "fehlt.html")
    alt = tmp_path / "berlin" / "index.html"
    alt.parent.mkdir(parents=True)
    alt.write_text("<html>ALTE LIVE-SEITE MIT ORTSDATEN 52.5001_13.4001</html>",
                   encoding="utf-8")

    assert export_html.cli([]) == 2
    seiten = [tmp_path / "index.html"]
    seiten += [tmp_path / s.slug / "index.html"
               for s in export_html.ausgelieferte_staedte()]
    assert alt in seiten, "Berlin muss unter den ausgelieferten Adressen sein"
    for seite in seiten:
        inhalt = seite.read_text(encoding="utf-8")
        assert inhalt == export_html.FALLBACK_WARTUNG_HTML, (
            f"{seite} ist nicht die Ersatz-Wartungsseite")
        assert "52.5001_13.4001" not in inhalt
    # T-74: eine zurückgehaltene Stadt bekommt auch im Fehlerfall keine
    # Adresse. Eine Ersatzseite dort wäre wieder eine Antwort statt 404.
    for stadt in export_html.STAEDTE:
        if stadt.veroeffentlicht:
            continue
        assert not (tmp_path / stadt.slug / "index.html").exists(), (
            f"{stadt.name} ist zurückgehalten, hat aber eine Ersatzseite "
            f"bekommen")


# ── Stadtseite und Quelle gehören zusammen ──────────────────────────────────

def test_jede_stadtseite_hat_eine_quelle():
    """Eine Stadt in STAEDTE ohne Eintrag in quellen.ALLE bekäme eine Seite,
    die nie Daten sieht — und der Launcher-Test oben würde sie nicht bemerken,
    weil er über quellen.ALLE läuft."""
    ohne = [s.slug for s in export_html.STAEDTE if s.slug not in quellen.ALLE]
    assert not ohne, f"Stadtseiten ohne Quelle in quellen.ALLE: {ohne}"
