"""Gemeinsame Absicherung fuer alle Tests.

Die Sperrliste fuehrt neben der Tabelle in der Datenbank eine Zweitschrift als
Datei neben den Skripten. Ohne Umleitung wuerde jeder Test, der eine Zelle
sperrt, in die echte Betriebsdatei schreiben. Diese Vorrichtung biegt den Pfad
fuer JEDEN Test auf ein Wegwerf-Verzeichnis um.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import sperrliste  # noqa: E402


@pytest.fixture(autouse=True)
def sperrliste_isolieren(tmp_path, monkeypatch):
    monkeypatch.setattr(sperrliste, "SPERRLISTE_DATEI", tmp_path / "sperrliste.txt")
