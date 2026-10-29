from pathlib import Path

import pytest

from geoimport.fehler import KonfigurationsFehler
from geoimport.konfiguration import CsvQuelle, WfsQuelle, lade_konfiguration

BEISPIEL = Path(__file__).resolve().parents[1] / "config"


def test_beispielkonfiguration_ist_gueltig():
    konfiguration = lade_konfiguration(BEISPIEL)
    ids = {q.id for q in konfiguration.quellen}
    assert {"bb_kreise", "bb_gemeinden", "bb_naturschutzgebiete", "hochschulen_osm"} <= ids
    assert konfiguration.pipeline.ziel_crs == 25833
    assert any(isinstance(q, WfsQuelle) for q in konfiguration.quellen)
    assert any(isinstance(q, CsvQuelle) for q in konfiguration.quellen)


def test_neue_quelle_ohne_codeaenderung(tmp_path):
    (tmp_path / "quellen").mkdir()
    (tmp_path / "pipeline.yaml").write_text("pipeline:\n  zeitplan: '15 2 * * 1'\n", encoding="utf-8")
    (tmp_path / "quellen" / "neu.yaml").write_text(
        "id: meine_quelle\ntyp: csv\nbezeichnung: Test\npfad: daten.csv\ngeometrie: {x: lon, y: lat}\n",
        encoding="utf-8",
    )
    konfiguration = lade_konfiguration(tmp_path)
    assert [q.id for q in konfiguration.quellen] == ["meine_quelle"]
    assert konfiguration.pipeline.zeitplan == "15 2 * * 1"
    assert konfiguration.basis_verzeichnis == tmp_path.resolve()


def test_zugangsdaten_aus_umgebungsvariablen(tmp_path, monkeypatch):
    monkeypatch.setenv("WFS_NUTZER", "nutzer1")
    monkeypatch.setenv("WFS_PASSWORT", "geheim")
    datei = tmp_path / "k.yaml"
    datei.write_text(
        "quellen:\n- id: q\n  typ: wfs\n  bezeichnung: Q\n  url: https://example.org/wfs\n  layer: a:b\n"
        "  anmeldung: {benutzer: '${WFS_NUTZER}', passwort: '${WFS_PASSWORT}'}\n",
        encoding="utf-8",
    )
    quelle = lade_konfiguration(datei).quellen[0]
    assert quelle.anmeldung.benutzer == "nutzer1"
    assert quelle.anmeldung.passwort == "geheim"


def test_fehlende_umgebungsvariable_wird_gemeldet(tmp_path):
    datei = tmp_path / "k.yaml"
    datei.write_text("pipeline:\n  zeitplan: '${GIBT_ES_NICHT_123}'\n", encoding="utf-8")
    with pytest.raises(KonfigurationsFehler, match="GIBT_ES_NICHT_123"):
        lade_konfiguration(datei)


@pytest.mark.parametrize(
    "quelle, meldung",
    [
        ("id: q\ntyp: wfs\nbezeichnung: Q\nlayer: a:b\n", "url"),
        ("id: q\ntyp: csv\nbezeichnung: Q\npfad: a.csv\ngeometrie: {x: lon}\n", "x und y oder wkt"),
        ("id: q\ntyp: csv\nbezeichnung: Q\ngeometrie: {wkt: g}\n", "genau eines von url oder pfad"),
        ("id: Q-1\ntyp: wfs\nbezeichnung: Q\nurl: u\nlayer: l\n", "id"),
        ("id: q\ntyp: wfs\nbezeichnung: Q\nurl: u\nlayer: l\nseitengrösse: 5\n", "seitengrösse"),
    ],
)
def test_ungueltige_quellen_werden_verstaendlich_gemeldet(tmp_path, quelle, meldung):
    (tmp_path / "quellen").mkdir()
    (tmp_path / "quellen" / "q.yaml").write_text(quelle, encoding="utf-8")
    with pytest.raises(KonfigurationsFehler, match=meldung):
        lade_konfiguration(tmp_path)


def test_doppelte_ids(tmp_path):
    (tmp_path / "quellen").mkdir()
    for name in ("a", "b"):
        (tmp_path / "quellen" / f"{name}.yaml").write_text(
            "id: gleich\ntyp: wfs\nbezeichnung: Q\nurl: u\nlayer: l\n", encoding="utf-8"
        )
    with pytest.raises(KonfigurationsFehler, match="doppelt"):
        lade_konfiguration(tmp_path)
