from urllib.parse import parse_qs

import httpx
import pytest

from geoimport.fehler import QuellenFehler
from geoimport.http import HttpClient
from geoimport.konfiguration import HttpEinstellungen, WfsQuelle
from geoimport.quellen.basis import QuellenErgebnis
from geoimport.quellen.wfs import WfsLeser
from hilfen import AUSNAHME, kreis, wfs_antwort

KREISE = [kreis(i, f"Kreis {i}") for i in range(1, 6)]


def leser(handler, **einstellungen) -> tuple[WfsLeser, QuellenErgebnis, list[dict]]:
    abrufe: list[dict] = []

    def protokollieren(anfrage: httpx.Request) -> httpx.Response:
        params = {k.upper(): v[0] for k, v in parse_qs(anfrage.url.query.decode()).items()}
        abrufe.append(params)
        return handler(params)

    client = HttpClient(HttpEinstellungen(versuche=2, wartezeit_s=0), transport=httpx.MockTransport(protokollieren))
    quelle = WfsQuelle(id="kreise", typ="wfs", bezeichnung="Kreise", url="https://wfs.test/ows", layer="app:kreis",
                       crs=25833, **einstellungen)
    ergebnis = QuellenErgebnis("kreise", "wfs", "Kreise")
    return WfsLeser(quelle, client, ergebnis), ergebnis, abrufe


def gml(objekte=KREISE, **kwargs):
    return lambda params: httpx.Response(200, text=wfs_antwort(objekte, params, **kwargs))


def test_paging_holt_alle_seiten_und_prueft_vollstaendigkeit():
    wfs, ergebnis, abrufe = leser(gml(), seitengroesse=2)
    objekte = list(wfs.objekte())
    assert [o.attribute["schl"] for o in objekte] == [k["id"] for k in KREISE]
    assert ergebnis.seiten == 3
    assert ergebnis.erwartet == 5
    assert ergebnis.warnungen == []
    seitenabrufe = [a for a in abrufe if a.get("RESULTTYPE") != "hits"]
    assert [a["STARTINDEX"] for a in seitenabrufe] == ["0", "2", "4"]
    assert all(a["SRSNAME"] == "urn:ogc:def:crs:EPSG::25833" for a in seitenabrufe)


def test_dienst_ignoriert_startindex_dann_ohne_paging():
    wfs, ergebnis, abrufe = leser(gml(paging_ignorieren=True), seitengroesse=2)
    objekte = list(wfs.objekte())
    assert len(objekte) == 5
    assert len({o.ersatz_id for o in objekte}) == 5
    assert any("ignoriert STARTINDEX" in w for w in ergebnis.warnungen)
    assert "COUNT" not in abrufe[-1]


def test_gml_attribute_geometrie_und_leere_werte():
    wfs, _, _ = leser(gml(KREISE[:1]))
    objekt = next(wfs.objekte())
    assert objekt.ersatz_id == "kreis_00001"
    assert objekt.geom_format == "gml"
    assert objekt.geom_text.startswith("<") and "Polygon" in objekt.geom_text
    assert "Inlined geometry" not in objekt.geom_text
    assert objekt.attribute == {"schl": "00001", "name": "Kreis 1", "art": "Landkreis", "stand": "02.07.2026",
                                "leer": None}


def test_max_objekte_begrenzt_abruf():
    wfs, _, abrufe = leser(gml(), seitengroesse=2, max_objekte=3)
    assert len(list(wfs.objekte())) == 3
    assert not any(a.get("RESULTTYPE") == "hits" for a in abrufe)


def test_wfs_fehlermeldung_wird_quellenfehler():
    wfs, _, _ = leser(lambda params: httpx.Response(200, text=AUSNAHME))
    with pytest.raises(QuellenFehler, match="Unbekannter Feature-Typ"):
        list(wfs.objekte())


def test_html_statt_gml_wird_quellenfehler():
    wfs, _, _ = leser(lambda params: httpx.Response(200, text="<html><body>Wartung</body></html>"),
                      paging="aus")
    with pytest.raises(QuellenFehler, match="Unerwartete WFS-Antwort <html>"):
        list(wfs.objekte())


def test_wiederholung_bei_503_dann_fehler():
    wfs, _, abrufe = leser(lambda params: httpx.Response(503, text="Service Unavailable"), paging="aus")
    with pytest.raises(QuellenFehler, match="HTTP 503"):
        list(wfs.objekte())
    assert len(abrufe) == 2


def test_geojson_ausgabe_wird_als_wgs84_gelesen():
    geojson = {
        "type": "FeatureCollection",
        "features": [{"type": "Feature", "id": "nsg_1", "geometry": {"type": "Point", "coordinates": [13.99, 53.15]},
                      "properties": {"Gebietsnummer": "2849-503", "Detail": {"a": 1}, "Liste": [1, 2]}}],
    }
    wfs, _, abrufe = leser(lambda params: httpx.Response(200, json=geojson), paging="aus", ausgabeformat="geojson")
    wfs.quelle.crs = None
    objekt = next(wfs.objekte())
    assert objekt.geom_format == "geojson"
    assert objekt.geom_srid == 4326
    assert objekt.attribute == {"Gebietsnummer": "2849-503", "Detail.a": 1, "Liste": "[1, 2]"}
    assert abrufe[0]["OUTPUTFORMAT"] == "application/geo+json"
