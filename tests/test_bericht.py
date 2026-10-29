import json

from geoimport import bericht as berichte


def quelle(quelle_id, status="ok", **werte):
    daten = {"quelle_id": quelle_id, "typ": "wfs", "url": "https://x", "status": status, "abgerufen": 0, "neu": 0,
             "geaendert": 0, "geloescht": 0, "unveraendert": 0, "verworfen": 0, "repariert": 0, "dauer_s": 1.25,
             "warnungen": [], "fehler": None}
    daten.update(werte)
    return daten


def test_gesamtstatus():
    assert berichte.gesamtstatus([quelle("a"), quelle("b")]) == "ok"
    assert berichte.gesamtstatus([quelle("a", warnungen=["w"])]) == "warnung"
    assert berichte.gesamtstatus([quelle("a"), quelle("b", "fehler")]) == "teilweise"
    assert berichte.gesamtstatus([quelle("a", "fehler")]) == "fehler"


def test_bericht_dateien(tmp_path):
    quellen = [quelle("kreise", neu=3, abgerufen=3, warnungen=["Schemaabweichung: neue Attribute: x"]),
               quelle("kaputt", "fehler", fehler="QuellenFehler: HTTP 503")]
    bericht = {"lauf_id": "0123456789abcdef", "gestartet_am": "2026-10-29T14:05:12+01:00", "dauer_s": 2.5,
               "status": berichte.gesamtstatus(quellen), "trockenlauf": False, "ziel_crs": 25833,
               "zusammenfassung": berichte.zusammenfassen(quellen), "quellen": quellen}
    json_pfad, md_pfad = berichte.schreiben(bericht, tmp_path)
    assert json_pfad.name == "2026-10-29T14-05-12_01234567.json"
    assert json.loads(json_pfad.read_text(encoding="utf-8")) == bericht
    markdown = md_pfad.read_text(encoding="utf-8")
    assert "teilweise erfolgreich" in markdown
    assert "| kreise | WFS | ok | 3 | 3 |" in markdown
    assert "## Nicht erreichbare oder fehlgeschlagene Quellen" in markdown and "HTTP 503" in markdown
    assert "Schemaabweichung" in markdown
    assert (tmp_path / "letzter_bericht.json").exists()
