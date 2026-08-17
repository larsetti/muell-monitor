"""Tests für das Städte-Gerüst (T-49, vorbereitet am 14.08.2026).

Die Struktur ist: / = Städte-Auswahl, /berlin/ und /koeln/ je eine Stadtseite.
Geprüft wird vor allem das, was beim Freischalten schiefgehen kann:

  * Jede Stadt hat ihren EIGENEN Freigabe-Schalter. Berlins Schalter darf Köln
    nicht mitreißen und umgekehrt. Das ist die Zusage an Lars vom 14.08.2026:
    live schalten nur auf ausdrückliche Anweisung, und dann nur die genannte
    Stadt.
  * Die Rechtstexte haben genau eine Quelle (maintenance.html). Keine Stadtseite
    darf mit eigenen, auseinandergelaufenen Pflichtangaben herauskommen.
  * Verweise auf assets/ müssen eine Ebene höher zeigen, sonst lädt eine
    Stadtseite stumm ohne Schrift, Symbole und Kartenbibliothek.
  * Fehlt ein Anker in der Wartungsseite, bricht der Bau ab, statt eine Stadt
    mit dem Text einer anderen auszuliefern (Lehre aus T-39).
"""
import re
import sys
from pathlib import Path

import pytest

TECHNIK = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(TECHNIK))

import export_html  # noqa: E402


# ── Hilfen ───────────────────────────────────────────────────────────────────

def stadt(slug):
    for s in export_html.STAEDTE:
        if s.slug == slug:
            return s
    raise AssertionError(f"Stadt {slug} steht nicht in STAEDTE")


@pytest.fixture
def ziel(tmp_path):
    return tmp_path / "ausgabe"


@pytest.fixture
def ohne_marker(monkeypatch, tmp_path):
    """Verlegt die Freigabe-Schalter in einen leeren Ordner.

    Sonst hinge das Ergebnis davon ab, ob auf diesem Rechner zufällig ein
    Schalter liegt — ein Test, der bei Lars grün und im nächsten Klon rot ist.
    """
    monkeypatch.setattr(export_html, "ROOT", tmp_path / "schalter")
    (tmp_path / "schalter").mkdir()
    return tmp_path / "schalter"


# ── Freigabe je Stadt ────────────────────────────────────────────────────────

def test_ohne_schalter_bleibt_jede_stadt_auf_wartung(ziel, ohne_marker):
    code = export_html.baue_staedte(ziel)
    assert code == 0
    for s in export_html.STAEDTE:
        seite = (ziel / s.slug / "index.html").read_text(encoding="utf-8")
        assert "Die öffentliche Ansicht ist derzeit nicht verfügbar." in seite, (
            f"{s.name} zeigt ohne Freigabe-Schalter keine Wartungsseite")


def test_berlins_schalter_schaltet_koeln_nicht_frei(ziel, ohne_marker):
    """Der Kern der Zusage: eine Freigabe gilt genau für eine Stadt."""
    (ohne_marker / "LIVE_FREIGEGEBEN_BERLIN").write_text("")
    export_html.baue_staedte(ziel)
    koeln = (ziel / "koeln" / "index.html").read_text(encoding="utf-8")
    assert "Die öffentliche Ansicht ist derzeit nicht verfügbar." in koeln, (
        "Berlins Freigabe hat Köln mitgezogen")
    assert "__APP_DATA_PLACEHOLDER__" not in koeln
    assert "hotspots" not in koeln.lower() or "Wartung" in koeln


def test_koelns_schalter_schaltet_berlin_nicht_frei(ziel, ohne_marker):
    (ohne_marker / "LIVE_FREIGEGEBEN_KOELN").write_text("")
    export_html.baue_staedte(ziel)
    berlin = (ziel / "berlin" / "index.html").read_text(encoding="utf-8")
    assert "Die öffentliche Ansicht ist derzeit nicht verfügbar." in berlin, (
        "Kölns Freigabe hat Berlin mitgezogen")


def test_koeln_ohne_daten_geht_auch_mit_schalter_nicht_live(ziel, ohne_marker):
    """Eine leere Karte zu veröffentlichen wäre schlimmer als gar keine."""
    (ohne_marker / "LIVE_FREIGEGEBEN_KOELN").write_text("")
    code = export_html.baue_stadt(stadt("koeln"), ziel)
    assert code == 4, "Freigabe ohne Daten muss Exit-Code 4 liefern"
    seite = (ziel / "koeln" / "index.html").read_text(encoding="utf-8")
    assert "Die öffentliche Ansicht ist derzeit nicht verfügbar." in seite


def test_alter_sammelmarker_schaltet_nichts_mehr_frei(ziel, ohne_marker):
    """LIVE_FREIGEGEBEN galt für die ganze Seite. In der Städte-Struktur darf
    er nichts mehr bewirken, sonst ginge beim Umstellen alles auf einmal live."""
    (ohne_marker / "LIVE_FREIGEGEBEN").write_text("")
    export_html.baue_staedte(ziel)
    for s in export_html.STAEDTE:
        seite = (ziel / s.slug / "index.html").read_text(encoding="utf-8")
        assert "Die öffentliche Ansicht ist derzeit nicht verfügbar." in seite, (
            f"Der alte Sammelmarker hat {s.name} freigeschaltet")


def test_rechtstext_sperre_gilt_auch_je_stadt(ziel, ohne_marker, monkeypatch):
    """T-25 / T-40 bleiben in der neuen Struktur wirksam."""
    (ohne_marker / "LIVE_FREIGEGEBEN_BERLIN").write_text("")
    monkeypatch.setattr(export_html, "rechtstexte_freigegeben", lambda: False)
    assert export_html.baue_stadt(stadt("berlin"), ziel) == 3
    seite = (ziel / "berlin" / "index.html").read_text(encoding="utf-8")
    assert "Die öffentliche Ansicht ist derzeit nicht verfügbar." in seite


# ── Rechtstexte: genau eine Quelle ───────────────────────────────────────────

def _impressum_ebene(html: str) -> str:
    return html[html.index('<div id="impOverlay"'):html.index("</body>")]


def test_jede_stadtseite_traegt_die_pflichtangaben_der_quelle(ziel, ohne_marker):
    export_html.baue_staedte(ziel)
    quelle = _impressum_ebene(
        export_html.MAINTENANCE_PATH.read_text(encoding="utf-8"))
    for s in export_html.STAEDTE:
        seite = _impressum_ebene((ziel / s.slug / "index.html").read_text(encoding="utf-8"))
        # Stadt-spezifisch sind nur Haftungssatz und Datenlizenz. Alles andere
        # muss Wort für Wort aus der einen Quelle stammen.
        for pflicht in ("Angaben gemäß § 5 DDG", "Lars Wittkopf",
                        "Verantwortlich für den Inhalt nach § 18 Abs. 2 MStV",
                        "Widerspruchsrecht", "Art. 21 DSGVO",
                        "Widerspruch%20nach%20Art.%2021%20DSGVO"):
            assert pflicht in seite, f"{s.name}: {pflicht!r} fehlt"
            assert pflicht in quelle


def test_startseite_traegt_pflichtangaben_und_stil_aus_der_quelle(ziel, ohne_marker):
    export_html.baue_staedte(ziel)
    seite = (ziel / "index.html").read_text(encoding="utf-8")
    assert "__STIL__" not in seite and "__IMPRESSUM_BLOCK__" not in seite
    assert "__STAEDTE_KACHELN__" not in seite
    assert '<div id="impOverlay"' in seite
    assert "Angaben gemäß § 5 DDG" in seite
    assert "Widerspruch%20nach%20Art.%2021%20DSGVO" in seite


def test_startseite_verlinkt_jede_stadt_genau_einmal(ziel, ohne_marker):
    export_html.baue_staedte(ziel)
    seite = (ziel / "index.html").read_text(encoding="utf-8")
    for s in export_html.STAEDTE:
        assert seite.count(f'href="{s.slug}/"') == 1, (
            f"{s.name} ist nicht genau einmal verlinkt")
        assert s.name in seite


def test_stadtseiten_nennen_ihre_eigene_quelle_und_lizenz(ziel, ohne_marker):
    export_html.baue_staedte(ziel)
    berlin = (ziel / "berlin" / "index.html").read_text(encoding="utf-8")
    koeln = (ziel / "koeln" / "index.html").read_text(encoding="utf-8")
    assert "Berliner Ordnungsamts" in berlin and "DL-DE-BY-2.0" in berlin
    assert "Stadt Köln" in koeln and "DL-DE-ZERO-2.0" in koeln
    assert "Stadt Köln" not in berlin, "Die Berliner Seite nennt die falsche Quelle"
    assert "Berliner Ordnungsamt" not in koeln, "Die Kölner Seite nennt die falsche Quelle"
    assert "<title>Müll-Monitor Berlin · Wartung</title>" in berlin
    assert "<title>Müll-Monitor Köln · Wartung</title>" in koeln


def test_berliner_seite_behauptet_keine_laufende_erfassung(ziel, ohne_marker):
    """Die Zusicherung, die T-39 und T-45 an anderer Stelle entfernt haben.

    Die Quelle liefert seit dem 22.04.2026 nichts mehr; eine Seite, die
    'Datenerfassung aktiv' meldet, sagt genau das Gegenteil.
    """
    export_html.baue_staedte(ziel)
    berlin = (ziel / "berlin" / "index.html").read_text(encoding="utf-8")
    assert "Datenerfassung aktiv" not in berlin
    assert "kontinuierlich aktualisiert" not in berlin
    assert "22.04.2026" in berlin


@pytest.mark.parametrize("slug", [s.slug for s in export_html.STAEDTE])
def test_statuspunkt_pulsiert_auf_keiner_stadtseite(ziel, ohne_marker, slug):
    """Der grüne, pulsierende Punkt sagt 'läuft gerade'.

    Er stammt aus der Vorlage und hat den ersten Browser-Test überlebt, weil
    eine Anweisung im style-Attribut das ::before-Element nicht erreicht.
    Weder eine ruhende Quelle noch eine Stadt in Vorbereitung darf ihn zeigen.
    """
    export_html.baue_staedte(ziel)
    seite = (ziel / slug / "index.html").read_text(encoding="utf-8")
    stil = seite[seite.index("</head>") - 4000:seite.index("</head>")]
    assert ".status::before" in stil, f"{slug}: Statuspunkt wird nicht überschrieben"
    assert "animation: none" in stil, f"{slug}: Statuspunkt pulsiert weiter"
    assert "#38a169" not in stil.split(".status::before")[-1], (
        f"{slug}: Statuspunkt bleibt grün")


# ── Pfade und Verweise ───────────────────────────────────────────────────────

def test_stadtseiten_verweisen_eine_ebene_hoeher_auf_assets(ziel, ohne_marker):
    export_html.baue_staedte(ziel)
    for s in export_html.STAEDTE:
        seite = (ziel / s.slug / "index.html").read_text(encoding="utf-8")
        assert '="assets/' not in seite, (
            f"{s.name}: Verweis auf assets/ zeigt ins Leere, die Seite liegt "
            f"eine Ebene tiefer")
        assert '="../assets/' in seite


def test_startseite_verweist_ohne_praefix_auf_assets(ziel, ohne_marker):
    export_html.baue_staedte(ziel)
    seite = (ziel / "index.html").read_text(encoding="utf-8")
    assert '="../assets/' not in seite, "Die Startseite liegt in der Wurzel"
    assert '="assets/' in seite


def test_slugs_bleiben_ascii():
    """URL-Pfade ohne Umlaute: /koeln, nicht /köln."""
    for s in export_html.STAEDTE:
        assert s.slug.isascii(), f"{s.slug!r} ist kein ASCII-Pfad"
        assert re.fullmatch(r"[a-z0-9-]+", s.slug), f"{s.slug!r} taugt nicht als Pfad"


def test_umlaut_adresse_leitet_auf_den_ascii_pfad(ziel, ohne_marker):
    export_html.baue_staedte(ziel)
    weiter = (ziel / "köln" / "index.html").read_text(encoding="utf-8")
    assert 'url=../koeln/' in weiter
    assert 'noindex' in weiter, "Die Weiterleitung darf nicht doppelt indexiert werden"


def test_baut_nichts_ausserhalb_des_ziels(ziel, ohne_marker):
    export_html.baue_staedte(ziel)
    erwartet = {
        ziel / "index.html",
        ziel / "berlin" / "index.html",
        ziel / "koeln" / "index.html",
        ziel / "köln" / "index.html",
    }
    assert set(p for p in ziel.rglob("*") if p.is_file()) == erwartet


# ── Fail-closed statt stiller Fehlersetzung ──────────────────────────────────

def test_fehlender_anker_bricht_den_bau_ab(tmp_path, monkeypatch):
    """Wird die Wartungsseite umformuliert, darf nicht stumm der alte Text
    stehenbleiben — sonst trägt eine Stadtseite die Quellenangabe einer anderen."""
    kaputt = tmp_path / "maintenance.html"
    kaputt.write_text("<html><body>ohne die erwarteten Anker</body></html>",
                      encoding="utf-8")
    monkeypatch.setattr(export_html, "MAINTENANCE_PATH", kaputt)
    with pytest.raises(RuntimeError, match="Anker"):
        export_html.wartungsseite_fuer(stadt("koeln"))


def test_fehlender_platzhalter_der_startseite_bricht_ab(tmp_path, monkeypatch, ziel):
    ohne = tmp_path / "startseite.html"
    ohne.write_text("<html><body>ohne Platzhalter</body></html>", encoding="utf-8")
    monkeypatch.setattr(export_html, "STARTSEITE_VORLAGE", ohne)
    with pytest.raises(RuntimeError, match="Platzhalter"):
        export_html.baue_startseite(ziel)


def test_doppelter_platzhalter_bricht_ab(tmp_path, monkeypatch, ziel):
    """Der Fehler vom 14.08.2026, festgehalten.

    Stand __STIL__ ein zweites Mal in einem CSS-Kommentar, wurde dort der
    komplette Stilblock samt </style> eingesetzt. Das Stil-Element schloss
    sich mitten im Dokument, der Rest erschien als Text. Das Bauen lief dabei
    fehlerfrei durch — gesehen hat es erst der Browser.
    """
    doppelt = tmp_path / "startseite.html"
    doppelt.write_text(
        "<html><head>__STIL__<style>/* __STIL__ */</style></head>"
        "<body>__STAEDTE_KACHELN__ __IMPRESSUM_BLOCK__</body></html>",
        encoding="utf-8")
    monkeypatch.setattr(export_html, "STARTSEITE_VORLAGE", doppelt)
    with pytest.raises(RuntimeError, match="2-mal"):
        export_html.baue_startseite(ziel)


def test_startseite_hat_genau_ein_offenes_und_geschlossenes_stil_element(ziel, ohne_marker):
    """Gegenprobe zum selben Fehler am fertigen Ergebnis."""
    export_html.baue_staedte(ziel)
    seite = (ziel / "index.html").read_text(encoding="utf-8")
    assert seite.count("<style>") == seite.count("</style>"), (
        "Stil-Elemente sind nicht paarig, die Seite zerfaellt im Browser")
    kopf = seite.split("</head>")[0]
    assert seite.count("</style>") == kopf.count("</style>"), (
        "Ein </style> steht ausserhalb des Kopfes")


def test_startseite_ohne_rechtsquelle_bricht_ab(tmp_path, monkeypatch, ziel):
    kaputt = tmp_path / "maintenance.html"
    kaputt.write_text("<html><body>keine Impressum-Ebene</body></html>",
                      encoding="utf-8")
    monkeypatch.setattr(export_html, "MAINTENANCE_PATH", kaputt)
    with pytest.raises(RuntimeError):
        export_html.baue_startseite(ziel)


# ── Altverhalten ─────────────────────────────────────────────────────────────

def test_altes_verhalten_ist_unveraendert(tmp_path, monkeypatch):
    """main() baut weiterhin genau eine Seite nach OUT_PATH.

    Die Städte-Struktur ist ein zusätzlicher Weg (--staedte), kein Umbau des
    laufenden Betriebs. Der Pi-Cronjob ruft weiter export_html.py ohne
    Argumente auf und schreibt weiter index.html.
    """
    ausgabe = tmp_path / "index.html"
    monkeypatch.setattr(export_html, "OUT_PATH", ausgabe)
    monkeypatch.setattr(export_html, "GO_LIVE_MARKER", tmp_path / "gibt-es-nicht")
    assert export_html.main() == 0
    assert ausgabe.exists()
    inhalt = ausgabe.read_text(encoding="utf-8")
    assert "Die öffentliche Ansicht ist derzeit nicht verfügbar." in inhalt
    assert '="assets/' in inhalt, "Die Wurzelseite braucht keinen Präfix"


# ── K-11: der Rueckgabewert nennt den schwersten Grund ───────────────────────
#
# Befund K-11 der Abnahme vom 15.08.2026, geschlossen unter T-68 am 17.08.2026.
# baue_staedte gab max(codes) zurueck und nannte das im Docstring "der
# schwerste". Die Exit-Codes sind aber nach Erscheinen vergeben, nicht nach
# Gewicht: 4 (keine Daten) ist groesser als 3 (Rechtstexte nicht freigegeben)
# und damit gewann der harmlosere Grund. Auf die ausgelieferten Seiten wirkt
# sich das nicht aus, beide Zustaende schreiben ohnehin die Wartungsseite. Es
# geht um das Protokoll.

def test_schwererer_grund_gewinnt_gegen_groesseren_code(ziel, ohne_marker):
    """Der Kern von K-11. Beide Schalter gesetzt, Koeln ohne Daten: Berlin
    liefert 3, Koeln 4 — zurueckkommen muss die 3."""
    (ohne_marker / "LIVE_FREIGEGEBEN_BERLIN").write_text("")
    (ohne_marker / "LIVE_FREIGEGEBEN_KOELN").write_text("")

    codes = {}
    echte_funktion = export_html.baue_stadt

    def mitschreiben(s, z):
        codes[s.slug] = echte_funktion(s, z)
        return codes[s.slug]

    import unittest.mock
    with unittest.mock.patch.object(export_html, "baue_stadt", mitschreiben):
        with unittest.mock.patch.object(export_html, "rechtstexte_freigegeben",
                                        lambda: False):
            gesamt = export_html.baue_staedte(ziel)

    # Ausgangslage: die beiden Gruende treten wirklich beide auf. Ohne diese
    # Pruefung koennte der Test gruen sein, weil es gar keine 4 gab.
    assert 3 in codes.values(), (
        f"Ausgangslage nicht hergestellt, keine Stadt meldet 3: {codes}")
    assert 4 in codes.values(), (
        f"Ausgangslage nicht hergestellt, keine Stadt meldet 4: {codes}")
    assert gesamt == 3, (
        f"baue_staedte gibt {gesamt} zurueck, die Staedte meldeten {codes}. "
        f"Erwartet war 3 — 'Rechtstexte nicht freigegeben' ist der schwerere "
        f"Grund als 'keine Daten', auch wenn 4 die groessere Zahl ist "
        f"(Befund K-11). Es wird nach EXIT_RANG sortiert, nicht nach max()."
    )


def test_unbekannter_exit_code_verschwindet_nicht_still(ziel, ohne_marker):
    """Wer spaeter einen fuenften Grund einfuehrt und EXIT_RANG nicht
    nachzieht, soll ihn zu sehen bekommen statt ihn zu verlieren."""
    import unittest.mock
    with unittest.mock.patch.object(export_html, "baue_stadt",
                                    lambda s, z: 7 if s.slug == "koeln" else 3):
        gesamt = export_html.baue_staedte(ziel)
    assert gesamt == 7, (
        f"Ein Exit-Code ausserhalb von EXIT_RANG ({gesamt} statt 7) ist "
        f"stillschweigend verlorengegangen. Unbekanntes muss gewinnen."
    )


def test_ohne_besonderheit_bleibt_es_bei_null(ziel, ohne_marker):
    """Gegenprobe: die Rangfolge darf keinen Grund erfinden, wo keiner ist."""
    assert export_html.baue_staedte(ziel) == 0


# ── Interne Kommentare gehoeren nicht in die ausgelieferte Seite ─────────────
#
# Nebenbefund vom 17.08.2026. Die Vorlagen sind ausfuehrlich kommentiert, und
# das soll so bleiben. Ausgeliefert wurden die Kommentare bis hierher mit,
# samt interner Kennungen und samt der Beschreibung frueherer Schwaechen — im
# Quelltext der Wartungsseite stand woertlich, welche Fassung "bis hierher die
# IP jedes Besuchers an Google weitergegeben" hat. Behoben ist das alles; eine
# oeffentliche Seite muss ihre eigene Fehlerhistorie trotzdem nicht
# mitliefern.

# Muster, die in einer ausgelieferten Seite nichts zu suchen haben.
INTERNE_SPUREN = (
    r"\bT-\d+\b",                    # Todo-Kennungen
    r"\b[AHKMSCI]-\d+\b",            # Abhilfen und Befunde
    r"\bDSFA\b",
    r"\bBefund\b",
    r"\bAbhilfe\b",
)


def _interne_spuren(text: str) -> list[str]:
    treffer = []
    for muster in INTERNE_SPUREN:
        treffer += [f"{muster}: {m.group(0)}" for m in re.finditer(muster, text)]
    return treffer


@pytest.mark.parametrize("slug", [s.slug for s in export_html.STAEDTE])
def test_stadtseite_liefert_keine_internen_kennungen_aus(ziel, ohne_marker, slug):
    export_html.baue_staedte(ziel)
    seite = (ziel / slug / "index.html").read_text(encoding="utf-8")
    assert "<!--" not in seite, (
        f"Die Seite {slug}/index.html enthaelt noch HTML-Kommentare. "
        f"ohne_interne_kommentare laeuft nicht auf diesem Weg."
    )
    spuren = _interne_spuren(seite)
    assert not spuren, (
        f"Die ausgelieferte Seite {slug}/index.html nennt interne Kennungen: "
        f"{spuren[:5]}"
    )


def test_startseite_liefert_keine_internen_kennungen_aus(ziel, ohne_marker):
    export_html.baue_staedte(ziel)
    seite = (ziel / "index.html").read_text(encoding="utf-8")
    assert "<!--" not in seite
    assert not _interne_spuren(seite)


def test_die_vorlagen_behalten_ihre_kommentare(ohne_marker):
    """Die Gegenprobe, und sie ist die wichtigere Haelfte. Entfernt wird beim
    Bauen, nicht in der Quelle — sonst geht die Begruendung verloren, warum
    eine Stelle so aussieht, wie sie aussieht."""
    for datei in (export_html.MAINTENANCE_PATH, export_html.TEMPLATE,
                  export_html.STARTSEITE_VORLAGE):
        inhalt = datei.read_text(encoding="utf-8")
        assert "<!--" in inhalt, (
            f"{datei.name} hat keine Kommentare mehr. Sie sollen in der "
            f"Vorlage stehenbleiben und nur aus dem Ergebnis verschwinden."
        )


def test_kommentarfilter_schneidet_nichts_ausserhalb_von_kommentaren(ziel, ohne_marker):
    """Die beiden Voraussetzungen, unter denen der Filter sicher ist.

    Er arbeitet mit Mustern und nicht mit einem echten Zerteiler fuer HTML,
    CSS und JavaScript. Das traegt genau solange, wie diese zwei Annahmen
    stimmen — deshalb stehen sie hier und nicht nur im Kommentar.
    """
    # 1. Kein "<!--" oder "-->" in einem Skript- oder Stilblock. Sonst wuerde
    #    das mehrzeilige HTML-Muster dort echten Inhalt abschneiden.
    for datei in (export_html.MAINTENANCE_PATH, export_html.TEMPLATE,
                  export_html.STARTSEITE_VORLAGE):
        inhalt = datei.read_text(encoding="utf-8")
        for block in re.finditer(r"<(script|style)\b[^>]*>(.*?)</\1>",
                                 inhalt, re.S | re.I):
            assert "<!--" not in block.group(2) and "-->" not in block.group(2), (
                f"{datei.name}: ein {block.group(1)}-Block enthaelt "
                f"Kommentar-Zeichen. ohne_interne_kommentare wuerde dort "
                f"echten Inhalt abschneiden."
            )

    # 2. Keine mehrzeilige Zeichenkette in Schraegstrich-Anfuehrung, deren
    #    Zeile mit "/*" oder "//" beginnt. Nur dort koennte der Code-Filter
    #    eine Zeile fuer einen Kommentar halten, die keiner ist.
        for literal in re.finditer(r"`(?:[^`\\]|\\.)*`", inhalt, re.S):
            for zeile in literal.group(0).split("\n"):
                assert not zeile.lstrip().startswith(("/*", "//")), (
                    f"{datei.name}: eine mehrzeilige Zeichenkette hat eine "
                    f"Zeile, die mit einem Kommentarzeichen beginnt "
                    f"({zeile.strip()[:60]!r}). Der Code-Filter wuerde sie "
                    f"entfernen und damit echten Inhalt."
                )

    # Und die Seite muss nach dem Filtern noch vollstaendig sein.
    export_html.baue_staedte(ziel)
    for slug in [s.slug for s in export_html.STAEDTE] + [""]:
        seite = (ziel / slug / "index.html").read_text(encoding="utf-8")
        assert seite.rstrip().endswith("</html>"), f"{slug or 'Startseite'} abgeschnitten"
        assert "</body>" in seite and "</style>" in seite, (
            f"{slug or 'Startseite'}: Struktur unvollstaendig nach dem Filtern")


def test_kommentarfilter_laesst_zeichenketten_mit_schraegstrichen_in_ruhe(ziel, ohne_marker):
    """Die naheliegende Verschlimmbesserung waere, jedes '//' zu entfernen
    statt nur die am Zeilenanfang. Dann fiele die Haelfte jeder Adresse weg —
    die Datenlizenz, die Kartenkacheln, jeder Verweis nach draussen."""
    export_html.baue_staedte(ziel)
    seite = (ziel / "berlin" / "index.html").read_text(encoding="utf-8")
    assert "https://" in seite, (
        "Aus der ausgelieferten Seite sind die Adressen verschwunden. Der "
        "Code-Filter greift zu weit: er darf nur Kommentare am Zeilenanfang "
        "entfernen."
    )
    assert "govdata.de" in seite, "Die Datenlizenz fehlt in der Fusszeile"
