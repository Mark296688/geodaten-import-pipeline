import pytest

from geoimport.abbildung import Umwandlungsfehler, attribute_abbilden, objekte_abbilden, umwandeln
from geoimport.konfiguration import AttributAbbildung, WfsQuelle
from geoimport.quellen.basis import Objekt, QuellenErgebnis


@pytest.mark.parametrize(
    "wert, typ, erwartet",
    [
        ("01.10.1990", "datum", "1990-10-01"),
        ("2026-07-02", "datum", "2026-07-02"),
        ("106.61785928", "zahl", 106.61785928),
        ("1.234,5", "zahl", 1234.5),
        ("42", "ganzzahl", 42),
        ("ja", "bool", True),
        ("0", "bool", False),
    ],
)
def test_umwandeln(wert, typ, erwartet):
    assert umwandeln(wert, typ) == erwartet


@pytest.mark.parametrize("wert, typ", [("31.02.2026", "datum"), ("abc", "zahl"), ("4,5", "ganzzahl"), ("vielleicht", "bool")])
def test_umwandeln_fehler(wert, typ):
    with pytest.raises(Umwandlungsfehler):
        umwandeln(wert, typ)


def test_schemas_verschiedener_quellen_auf_ein_zielschema():
    kreis = attribute_abbilden(
        {"krss": "12051", "krs_name": "Brandenburg an der Havel", "krs_art_kurz": "KFS", "leer": ""},
        AttributAbbildung(umbenennen={"krss": "schluessel", "krs_name": "name"}, ausschliessen=["krs_art_kurz"],
                          konstanten={"ebene": "kreis"}),
    )
    schutzgebiet = attribute_abbilden(
        {"Gebietsnummer": "2849-503", "Gebietsname": "Hintenteiche", "Inkrafttreten": "01.10.1990", "Anderes": "x"},
        AttributAbbildung(nur=["Gebietsnummer", "Gebietsname", "Inkrafttreten"],
                          umbenennen={"Gebietsnummer": "schluessel", "Gebietsname": "name", "Inkrafttreten": "seit"},
                          typen={"seit": "datum"}),
    )
    assert kreis == {"schluessel": "12051", "name": "Brandenburg an der Havel", "ebene": "kreis"}
    assert schutzgebiet == {"schluessel": "2849-503", "name": "Hintenteiche", "seit": "1990-10-01"}


def test_objekt_id_ersatz_und_warnungen():
    quelle = WfsQuelle(id="q", typ="wfs", bezeichnung="Q", url="u", layer="l", id_feld=["a", "b"],
                       attribute=AttributAbbildung(typen={"z": "zahl"}))
    ergebnis = QuellenErgebnis("q", "wfs", "Q")
    objekte = [
        Objekt("gml_1", "gml", "<x/>", None, {"a": "1", "b": "2", "z": "3,5"}),
        Objekt("gml_2", "gml", "<x/>", None, {"a": "1", "z": "kaputt"}),
    ]
    ergebnis_objekte = list(objekte_abbilden(objekte, quelle, ergebnis))
    assert [o.objekt_id for o in ergebnis_objekte] == ["1/2", "gml_2"]
    assert ergebnis_objekte[0].attribute["z"] == 3.5
    assert ergebnis_objekte[1].attribute["z"] == "kaputt"
    assert ergebnis.abgerufen == 2
    assert ergebnis.attributschema == ["a", "b", "z"]
    assert any("ohne Wert im id_feld" in w for w in ergebnis.warnungen)
    assert any("Typumwandlung" in w for w in ergebnis.warnungen)
