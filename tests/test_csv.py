import httpx
import pytest

from geoimport.fehler import QuellenFehler
from geoimport.http import HttpClient
from geoimport.konfiguration import CsvQuelle, HttpEinstellungen
from geoimport.quellen.basis import QuellenErgebnis
from geoimport.quellen.csv_quelle import CsvLeser

ORTE = (
    "Stand: 01.10.2026\n"
    "Name;Ort;Länge;Breite;Typ\n"
    "TH Brandenburg;Brandenburg an der Havel;12,5357;52,4116;Hochschule\n"
    "Uni Potsdam;Potsdam;13,0218;52,4005;Universität\n"
    "Ohne Koordinate;Nirgendwo;;;Test\n"
)


def leser_fuer(tmp_path, inhalt: str | bytes, **einstellungen) -> tuple[CsvLeser, QuellenErgebnis]:
    datei = tmp_path / "orte.csv"
    datei.write_bytes(inhalt if isinstance(inhalt, bytes) else inhalt.encode("utf-8"))
    werte = {"id": "orte", "typ": "csv", "bezeichnung": "Orte", "pfad": "orte.csv",
             "geometrie": {"x": "Länge", "y": "Breite", "crs": 4326}, "dezimaltrennzeichen": ",",
             "ueberspringe_zeilen": 1}
    werte.update(einstellungen)
    quelle = CsvQuelle(**werte)
    ergebnis = QuellenErgebnis("orte", "csv", "Orte")
    client = HttpClient(HttpEinstellungen())
    return CsvLeser(quelle, client, ergebnis, tmp_path), ergebnis


def test_dezimalkomma_trennzeichen_erkennung_und_verworfene_zeilen(tmp_path):
    leser, ergebnis = leser_fuer(tmp_path, ORTE)
    objekte = list(leser.objekte())
    assert [o.geom_text for o in objekte] == ["POINT(12.5357 52.4116)", "POINT(13.0218 52.4005)"]
    assert objekte[0].attribute == {"Name": "TH Brandenburg", "Ort": "Brandenburg an der Havel", "Typ": "Hochschule"}
    assert objekte[0].geom_srid == 4326
    assert ergebnis.verworfen == 1
    assert any("Datenzeile 3" in w for w in ergebnis.warnungen)


def test_windows_kodierung_wird_erkannt(tmp_path):
    leser, ergebnis = leser_fuer(tmp_path, ORTE.encode("cp1252"))
    objekte = list(leser.objekte())
    assert len(objekte) == 2
    assert any("Windows-1252" in w for w in ergebnis.warnungen)


def test_filter_und_wkt_spalte(tmp_path):
    inhalt = "id,art,wkt\n1,a,POINT(13 52)\n2,b,\"LINESTRING(13 52, 13.1 52.1)\"\n"
    leser, _ = leser_fuer(tmp_path, inhalt, geometrie={"wkt": "wkt", "crs": 4326}, dezimaltrennzeichen=".",
                          ueberspringe_zeilen=0, filter={"art": ["b"]})
    objekte = list(leser.objekte())
    assert len(objekte) == 1
    assert objekte[0].geom_text.startswith("LINESTRING")
    assert objekte[0].attribute == {"id": "2", "art": "b"}


def test_fehlende_spalte_ist_quellenfehler(tmp_path):
    leser, _ = leser_fuer(tmp_path, ORTE, geometrie={"x": "lon", "y": "lat"})
    with pytest.raises(QuellenFehler, match="Spalte\\(n\\) fehlen.*lon"):
        list(leser.objekte())


def test_csv_per_url_mit_parametern(tmp_path):
    abgerufen = []

    def antwort(anfrage: httpx.Request) -> httpx.Response:
        abgerufen.append(str(anfrage.url))
        return httpx.Response(200, content="x;y;name\n13,1;52,2;A\n".encode())

    quelle = CsvQuelle(id="u", typ="csv", bezeichnung="U", url="https://daten.test/export", parameter={"format": "csv"},
                       geometrie={"x": "x", "y": "y"}, dezimaltrennzeichen=",")
    ergebnis = QuellenErgebnis("u", "csv", "U")
    client = HttpClient(HttpEinstellungen(), transport=httpx.MockTransport(antwort))
    objekte = list(CsvLeser(quelle, client, ergebnis, tmp_path).objekte())
    assert objekte[0].geom_text == "POINT(13.1 52.2)"
    assert abgerufen == ["https://daten.test/export?format=csv"]
