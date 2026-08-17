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


# ── K-11: der Rückgabewert nennt den schwersten Grund ───────────────────────
#
# Befund K-11 der Abnahme vom 15.08.2026, geschlossen unter T-68 am 17.08.2026.
# baue_staedte gab max(codes) zurück und nannte das im Docstring "der
# schwerste". Die Exit-Codes sind aber nach Erscheinen vergeben, nicht nach
# Gewicht: 4 (keine Daten) ist größer als 3 (Rechtstexte nicht freigegeben)
# und damit gewann der harmlosere Grund. Auf die ausgelieferten Seiten wirkt
# sich das nicht aus, beide Zustaende schreiben ohnehin die Wartungsseite. Es
# geht um das Protokoll.

def test_schwererer_grund_gewinnt_gegen_groesseren_code(ziel, ohne_marker):
    """Der Kern von K-11. Beide Schalter gesetzt, Köln ohne Daten: Berlin
    liefert 3, Köln 4 — zurueckkommen muss die 3."""
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

    # Ausgangslage: die beiden Gründe treten wirklich beide auf. Ohne diese
    # Prüfung könnte der Test grün sein, weil es gar keine 4 gab.
    assert 3 in codes.values(), (
        f"Ausgangslage nicht hergestellt, keine Stadt meldet 3: {codes}")
    assert 4 in codes.values(), (
        f"Ausgangslage nicht hergestellt, keine Stadt meldet 4: {codes}")
    assert gesamt == 3, (
        f"baue_staedte gibt {gesamt} zurück, die Städte meldeten {codes}. "
        f"Erwartet war 3 — 'Rechtstexte nicht freigegeben' ist der schwerere "
        f"Grund als 'keine Daten', auch wenn 4 die größere Zahl ist "
        f"(Befund K-11). Es wird nach EXIT_RANG sortiert, nicht nach max()."
    )


def test_unbekannter_exit_code_verschwindet_nicht_still(ziel, ohne_marker):
    """Wer später einen fünften Grund einführt und EXIT_RANG nicht
    nachzieht, soll ihn zu sehen bekommen statt ihn zu verlieren."""
    import unittest.mock
    with unittest.mock.patch.object(export_html, "baue_stadt",
                                    lambda s, z: 7 if s.slug == "koeln" else 3):
        gesamt = export_html.baue_staedte(ziel)
    assert gesamt == 7, (
        f"Ein Exit-Code außerhalb von EXIT_RANG ({gesamt} statt 7) ist "
        f"stillschweigend verlorengegangen. Unbekanntes muss gewinnen."
    )


def test_ohne_besonderheit_bleibt_es_bei_null(ziel, ohne_marker):
    """Gegenprobe: die Rangfolge darf keinen Grund erfinden, wo keiner ist."""
    assert export_html.baue_staedte(ziel) == 0


# ── Interne Kommentare gehören nicht in die ausgelieferte Seite ─────────────
#
# Nebenbefund vom 17.08.2026. Die Vorlagen sind ausführlich kommentiert, und
# das soll so bleiben. Ausgeliefert wurden die Kommentare bis hierher mit,
# samt interner Kennungen und samt der Beschreibung frueherer Schwaechen — im
# Quelltext der Wartungsseite stand wörtlich, welche Fassung "bis hierher die
# IP jedes Besuchers an Google weitergegeben" hat. Behoben ist das alles; eine
# öffentliche Seite muss ihre eigene Fehlerhistorie trotzdem nicht
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
        f"ohne_interne_kommentare läuft nicht auf diesem Weg."
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
    """Die Gegenprobe, und sie ist die wichtigere Hälfte. Entfernt wird beim
    Bauen, nicht in der Quelle — sonst geht die Begründung verloren, warum
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

    Er arbeitet mit Mustern und nicht mit einem echten Zerteiler für HTML,
    CSS und JavaScript. Das trägt genau solange, wie diese zwei Annahmen
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

    # 2. Keine mehrzeilige Zeichenkette in Schrägstrich-Anführung, deren
    #    Zeile mit "/*" oder "//" beginnt. Nur dort könnte der Code-Filter
    #    eine Zeile für einen Kommentar halten, die keiner ist.
        for literal in re.finditer(r"`(?:[^`\\]|\\.)*`", inhalt, re.S):
            for zeile in literal.group(0).split("\n"):
                assert not zeile.lstrip().startswith(("/*", "//")), (
                    f"{datei.name}: eine mehrzeilige Zeichenkette hat eine "
                    f"Zeile, die mit einem Kommentarzeichen beginnt "
                    f"({zeile.strip()[:60]!r}). Der Code-Filter wuerde sie "
                    f"entfernen und damit echten Inhalt."
                )

    # Und die Seite muss nach dem Filtern noch vollständig sein.
    export_html.baue_staedte(ziel)
    for slug in [s.slug for s in export_html.STAEDTE] + [""]:
        seite = (ziel / slug / "index.html").read_text(encoding="utf-8")
        assert seite.rstrip().endswith("</html>"), f"{slug or 'Startseite'} abgeschnitten"
        assert "</body>" in seite and "</style>" in seite, (
            f"{slug or 'Startseite'}: Struktur unvollständig nach dem Filtern")


# ── T-76: die Sperre sitzt im Bau, nicht nur im Test ────────────────────────
#
# Die Tests darüber prüfen den Filter und seine zwei Annahmen. Sie greifen
# genau solange, wie jemand sie laufen lässt — der tägliche Bau läuft ohne
# Test, unbeaufsichtigt, auf drei Wegen. Seit dem 17.08.2026 sieht deshalb
# export_html.pruefe_ausgeliefert jede fertige Seite selbst noch einmal an,
# bevor sie geschrieben wird, und bricht fail-closed ab.
#
# Das Ergebnis eines Abbruchs ist dasselbe wie bei jedem anderen Baufehler:
# baue_ausgeliefert schreibt an jeder ausgelieferten Adresse die Ersatzseite
# und gibt Exit-Code 2 zurück. Absichtlich kein fünfter Code — 2 sagt, was an
# den Adressen steht, und der Grund steht im Protokoll.


def _wartungsquelle_mit(tmp_path, monkeypatch, einschub: str) -> Path:
    """Kopiert die echte maintenance.html und setzt einschub vor </body>.

    Die echte Vorlage, damit alle Anker und der Stilblock erhalten bleiben —
    ein Testdokument von zehn Zeilen würde vorher an _ersetze_einmal scheitern
    und damit am falschen Grund.
    """
    quelle = export_html.MAINTENANCE_PATH.read_text(encoding="utf-8")
    assert "</body>" in quelle
    kopie = tmp_path / "maintenance_mit_leck.html"
    kopie.write_text(quelle.replace("</body>", f"{einschub}\n</body>", 1),
                     encoding="utf-8")
    monkeypatch.setattr(export_html, "MAINTENANCE_PATH", kopie)
    return kopie


def test_kennung_ausserhalb_eines_kommentars_haelt_die_auslieferung_an(
        ziel, ohne_marker, tmp_path, monkeypatch):
    """Der Fall, den der Kommentarfilter von sich aus nicht sehen kann.

    Eine Kennung in sichtbarem Text ist kein Kommentar, also entfernt der
    Filter sie nicht — und ohne diese Sperre ginge sie still hinaus.
    """
    _wartungsquelle_mit(tmp_path, monkeypatch,
                        '<p class="hinweis">Siehe Befund H-04 (DSFA), T-40.</p>')

    code = export_html.baue_ausgeliefert(ziel)

    assert code == 2, (
        f"Eine Kennung in der fertigen Seite muss den Bau anhalten und in die "
        f"Ersatzseiten-Klammer laufen (Exit-Code 2), war {code}.")
    seiten = [ziel / "index.html"]
    seiten += [ziel / s.slug / "index.html"
               for s in export_html.ausgelieferte_staedte()]
    for seite in seiten:
        inhalt = seite.read_text(encoding="utf-8")
        assert inhalt == export_html.FALLBACK_WARTUNG_HTML, (
            f"{seite} ist nicht die Ersatzseite. Fail-closed heißt: die "
            f"undichte Seite wird nicht ausgeliefert, und an ihrer Stelle "
            f"steht die Ersatzseite (A-11).")
        assert "H-04" not in inhalt and "T-40" not in inhalt


def test_kommentarrest_haelt_die_auslieferung_an():
    """Geschachtelter Kommentar: der Filter kann ihn nicht vollständig.

    "<!-- a <!-- b -->" verbraucht das erste Muster bis zum ersten "-->" und
    lässt den Rest als sichtbaren Text stehen. Genau dagegen prüft die Sperre
    auch auf "-->" und nicht nur auf "<!--".
    """
    roh = "<html><body><!-- innen <!-- tiefer --> Rest --></body></html>"
    gefiltert = export_html.ohne_interne_kommentare(roh)
    assert "-->" in gefiltert, (
        "Ausgangslage nicht hergestellt: der Filter hat den geschachtelten "
        "Kommentar diesmal vollständig entfernt.")
    with pytest.raises(export_html.SeiteNichtAuslieferbar, match="-->"):
        export_html.pruefe_ausgeliefert(gefiltert, "Testseite")


def test_unbeendeter_blockkommentar_im_stilblock_haelt_die_auslieferung_an():
    """Ohne abschliessendes "*/" findet das Blockmuster des Filters nichts, der
    Kommentar bleibt stehen.

    Bewusst OHNE Kennung im Text: sonst schlüge die Kennungsprüfung an und
    dieser Zweig wäre ungeprüft. Gemessen am 17.08.2026 — mit "Befund H-04" im
    Kommentar blieb die Prüfung der Code-Blöcke von keinem Test gedeckt.
    """
    roh = ("<html><head><style>\n/* kein Ende, und kein Wort darin ist eine "
           "Kennung\n.a { color: red; }\n</style></head><body></body></html>")
    gefiltert = export_html.ohne_interne_kommentare(roh)
    assert "/*" in gefiltert, "Ausgangslage nicht hergestellt"
    with pytest.raises(export_html.SeiteNichtAuslieferbar, match="style-Block"):
        export_html.pruefe_ausgeliefert(gefiltert, "Testseite")


def test_halb_abgeschnittener_blockkommentar_haelt_die_auslieferung_an():
    """Beginnt ein Blockkommentar mitten in einer Zeile, setzt der Filter nicht
    an — und die schliessende Zeile bleibt als "*/" stehen."""
    roh = ("<html><head><style>\n.a { color: red; } /* Anfang mitten in der "
           "Zeile\n  noch Text\n*/\n</style></head><body></body></html>")
    gefiltert = export_html.ohne_interne_kommentare(roh)
    with pytest.raises(export_html.SeiteNichtAuslieferbar, match="style-Block"):
        export_html.pruefe_ausgeliefert(gefiltert, "Testseite")


def test_eine_ungefilterte_seite_kommt_nicht_durch():
    """Der Fall, für den die Sperre am Ende dasteht: ein neuer Bau-Weg, der
    ohne_interne_kommentare vergisst. Die echte Wartungsvorlage, ungefiltert,
    muss abgewiesen werden — und zwar schon an ihren Kommentaren."""
    roh = export_html.MAINTENANCE_PATH.read_text(encoding="utf-8")
    with pytest.raises(export_html.SeiteNichtAuslieferbar, match="<!--"):
        export_html.pruefe_ausgeliefert(roh, "ungefilterte Wartungsvorlage")

    # Und dieselbe Vorlage ohne ihre HTML-Kommentare, damit auch die
    # Zeilenkommentare der Code-Blöcke geprüft sind: template.html hatte 14
    # Stück davon, das ist der grössere Teil des Befunds vom 17.08.2026.
    nur_html_entfernt = export_html._KOMMENTAR.sub("", roh)
    with pytest.raises(export_html.SeiteNichtAuslieferbar):
        export_html.pruefe_ausgeliefert(nur_html_entfernt, "halb gefiltert")


def test_der_datenblock_ist_von_der_kennungspruefung_ausgenommen():
    """Sonst hielte ein Kölner Autobahnname den täglichen Bau an.

    A-4, A-3 und A-555 führen durch Köln. Steht so ein Name im Straßenfeld
    einer Zelle, sieht das Muster für Befund-Kürzel eine Kennung. Der
    Datenblock ist kein Vorlagentext und hat seine eigenen Sperren (A-1, A-2,
    A-7, A-14).
    """
    daten = '{"hotspots":[{"strasse":"A-4 Auffahrt Klettenberg"}]}'
    seite = f"<html><body><script>const D={daten};</script></body></html>"
    export_html.pruefe_ausgeliefert(seite, "Testseite", daten=daten)
    # Gegenprobe: ohne die Ausnahme schlägt genau dieselbe Seite an. Das hält
    # fest, dass die Ausnahme wirkt und nicht bloß das Muster zahnlos ist.
    with pytest.raises(export_html.SeiteNichtAuslieferbar, match="A-4"):
        export_html.pruefe_ausgeliefert(seite, "Testseite")


def test_die_eingebauten_seiten_kommen_durch_die_sperre():
    """FALLBACK_WARTUNG_HTML und die Umlaut-Weiterleitung laufen bewusst NICHT
    durch die Sperre — die Ersatzseite ist die Abhilfe selbst, sie darf nicht an
    ihr hängenbleiben. Dass sie sauber sind, hält dieser Test fest."""
    export_html.pruefe_ausgeliefert(export_html.FALLBACK_WARTUNG_HTML,
                                    "Ersatz-Wartungsseite")


def test_die_echten_gebauten_seiten_kommen_durch_die_sperre(ziel, ohne_marker):
    """Gegenprobe zum Ganzen: die Sperre darf den Normalfall nicht anhalten."""
    assert export_html.baue_staedte(ziel) == 0
    for slug in [s.slug for s in export_html.STAEDTE] + [""]:
        seite = (ziel / slug / "index.html").read_text(encoding="utf-8")
        export_html.pruefe_ausgeliefert(seite, slug or "Startseite")


def test_altweg_schreibt_die_ersatzseite_wenn_die_wartungsseite_leckt(
        tmp_path, monkeypatch):
    """Auch der Einzelseiten-Weg (--eine-seite) darf nichts Undichtes ablegen.

    Er hat keine Ersatzseiten-Klammer um sich, also behandelt
    _wartungsseite_schreiben den Fall wie eine fehlende Wartungsquelle: A-11,
    Ersatzseite, Exit-Code 2.
    """
    _wartungsquelle_mit(tmp_path, monkeypatch, "<p>Abhilfe A-12 steht hier.</p>")
    ausgabe = tmp_path / "index.html"
    ausgabe.write_text("<html>ALTE LIVE-SEITE 52.50000_13.40000</html>",
                       encoding="utf-8")
    monkeypatch.setattr(export_html, "OUT_PATH", ausgabe)
    monkeypatch.setattr(export_html, "GO_LIVE_MARKER", tmp_path / "gibt-es-nicht")

    assert export_html.main() == 2
    inhalt = ausgabe.read_text(encoding="utf-8")
    assert inhalt == export_html.FALLBACK_WARTUNG_HTML, (
        "Die alte Live-Seite mit Cluster-ID steht noch da. Eine nicht "
        "auslieferbare Wartungsseite ist derselbe Fall wie eine fehlende "
        "(A-11 / Sec M-02).")


def test_kommentarfilter_laesst_zeichenketten_mit_schraegstrichen_in_ruhe(ziel, ohne_marker):
    """Die naheliegende Verschlimmbesserung wäre, jedes '//' zu entfernen
    statt nur die am Zeilenanfang. Dann fiele die Hälfte jeder Adresse weg —
    die Datenlizenz, die Kartenkacheln, jeder Verweis nach draussen."""
    export_html.baue_staedte(ziel)
    seite = (ziel / "berlin" / "index.html").read_text(encoding="utf-8")
    assert "https://" in seite, (
        "Aus der ausgelieferten Seite sind die Adressen verschwunden. Der "
        "Code-Filter greift zu weit: er darf nur Kommentare am Zeilenanfang "
        "entfernen."
    )
    assert "govdata.de" in seite, "Die Datenlizenz fehlt in der Fusszeile"
