"""End-to-End-Test: WFS + CSV -> PostGIS -> Import-Bericht (Abnahmekriterium).

Läuft gegen eine echte PostgreSQL/PostGIS-Datenbank (DATABASE_URL) und einen
lokalen Test-Dienst. Geprüft werden über mehrere Läufe: Erstimport, stabile
Wiederholung, Änderungen/Löschungen mit Historie, Löschschutz, Trockenlauf,
Fehlertoleranz je Quelle, Laufsperre und der Import-Bericht (JSON + Markdown).
"""

from __future__ import annotations

import json

import psycopg
import pytest

from geoimport import datenbank
from geoimport.ablauf import lauf_ausfuehren
from geoimport.cli import main
from geoimport.fehler import LaufAktiv
from geoimport.konfiguration import lade_konfiguration
from hilfen import kreis, quadrat

pytestmark = pytest.mark.e2e

FLIEGE = [(400000, 5800000), (401000, 5801000), (401000, 5800000), (400000, 5801000), (400000, 5800000)]

KREISE_V1 = [
    kreis(1, "Havelland"),
    kreis(2, "Potsdam-Mittelmark"),
    kreis(3, "Teltow-Fläming"),
    kreis(4, "Selbstüberschneidung", ring=FLIEGE),
    kreis(5, "Prignitz"),
    kreis(6, "Kaputte Geometrie", ring="370000 abc 5800000"),
]

ORTE_V1 = (
    "Name;Ort;Länge;Breite;Kennung\n"
    "TH Brandenburg;Brandenburg an der Havel;12,5357;52,4116;THB\n"
    "Universität Potsdam;Potsdam;13,0218;52,4005;UP\n"
    "BTU Cottbus-Senftenberg;Cottbus;14,3257;51,7671;BTU\n"
    "Ohne Koordinate;Nirgendwo;;;X\n"
)


def konfiguration_schreiben(tmp_path, basis_url: str):
    verzeichnis = tmp_path / "config"
    (verzeichnis / "quellen").mkdir(parents=True)
    (verzeichnis / "pipeline.yaml").write_text(
        f"""pipeline:
  ziel_crs: 25833
  berichte_verzeichnis: {(tmp_path / 'berichte').as_posix()}
  loeschschutz_quote: 0.5
  plausibilitaet_bbox: [11.2, 51.3, 14.8, 53.6]
  http: {{versuche: 2, wartezeit_s: 0, timeout_s: 10}}
""",
        encoding="utf-8",
    )
    (verzeichnis / "quellen" / "test_kreise.yaml").write_text(
        f"""id: test_kreise
typ: wfs
bezeichnung: Test-Kreise (WFS)
url: {basis_url}/wfs
layer: app:kreis
crs: 25833
seitengroesse: 2
id_feld: schl
attribute:
  umbenennen: {{schl: schluessel, stand: stand_am}}
  konstanten: {{ebene: kreis}}
  typen: {{stand_am: datum}}
""",
        encoding="utf-8",
    )
    (verzeichnis / "quellen" / "test_orte.yaml").write_text(
        f"""id: test_orte
typ: csv
bezeichnung: Test-Orte (CSV per URL)
url: {basis_url}/orte.csv
kodierung: cp1252
dezimaltrennzeichen: ","
id_feld: Kennung
geometrie: {{x: Länge, y: Breite, crs: 4326}}
attribute:
  umbenennen: {{Name: name, Ort: ort, Kennung: schluessel}}
""",
        encoding="utf-8",
    )
    (verzeichnis / "quellen" / "test_kaputt.yaml").write_text(
        f"id: test_kaputt\ntyp: wfs\nbezeichnung: Nicht erreichbarer Dienst\nurl: {basis_url}/kaputt\nlayer: app:x\n",
        encoding="utf-8",
    )
    return verzeichnis


def nach_quelle(bericht):
    return {q["quelle_id"]: q for q in bericht["quellen"]}


def abfrage(dsn, sql, *params):
    with psycopg.connect(dsn) as verbindung:
        return verbindung.execute(sql, params).fetchall()


def test_ende_zu_ende(dsn, dienst, tmp_path):
    dienst.wfs_objekte = list(KREISE_V1)
    dienst.csv_text = ORTE_V1
    konfiguration = lade_konfiguration(konfiguration_schreiben(tmp_path, dienst.basis_url))

    # --- Lauf 1: Erstimport, eine Quelle nicht erreichbar --------------------------------
    bericht, (json_pfad, md_pfad) = lauf_ausfuehren(konfiguration, dsn=dsn)
    q = nach_quelle(bericht)
    assert bericht["status"] == "teilweise"
    assert q["test_kreise"]["status"] == "warnung"
    assert (q["test_kreise"]["neu"], q["test_kreise"]["verworfen"], q["test_kreise"]["repariert"]) == (5, 1, 1)
    assert q["test_kreise"]["seiten"] == 4 and q["test_kreise"]["erwartet"] == 6
    assert q["test_orte"]["neu"] == 3 and q["test_orte"]["verworfen"] == 1
    assert q["test_kaputt"]["status"] == "fehler" and "HTTP 503" in q["test_kaputt"]["fehler"]
    assert bericht["zusammenfassung"]["neu"] == 8

    # Import-Bericht: JSON identisch zum Rückgabewert, Markdown mit Fehlern und Warnungen
    assert json.loads(json_pfad.read_text(encoding="utf-8")) == bericht
    markdown = md_pfad.read_text(encoding="utf-8")
    assert "test_kaputt" in markdown and "Nicht erreichbare oder fehlgeschlagene Quellen" in markdown
    assert "ST_MakeValid" in markdown and "Geometriefehler" in markdown
    assert abfrage(dsn, "SELECT status, bericht->>'lauf_id' FROM pipeline.import_lauf") == [
        ("teilweise", bericht["lauf_id"])
    ]

    # Datenmodell: Ziel-CRS, Transformation der CSV-Punkte, Attribute mit Datentypen
    assert abfrage(dsn, "SELECT DISTINCT ST_SRID(geom) FROM geodaten.geometrien") == [(25833,)]
    [(lon, lat)] = abfrage(
        dsn,
        "SELECT round(ST_X(ST_Transform(geom, 4326))::numeric, 6), round(ST_Y(ST_Transform(geom, 4326))::numeric, 6) "
        "FROM geodaten.geometrien WHERE objekt_id = 'THB'",
    )
    assert (float(lon), float(lat)) == (12.5357, 52.4116)
    assert abfrage(
        dsn,
        "SELECT a.wert, a.datentyp FROM geodaten.attribute a JOIN geodaten.geometrien g ON g.id = a.geometrie_id "
        "WHERE g.objekt_id = '00001' AND a.schluessel = 'stand_am'",
    ) == [("2026-07-02", "datum")]
    assert abfrage(dsn, "SELECT ST_IsValid(geom) FROM geodaten.geometrien WHERE objekt_id = '00004'") == [(True,)]
    assert abfrage(dsn, "SELECT quelle_id, letzter_status FROM geodaten.metadaten ORDER BY quelle_id") == [
        ("test_kaputt", "fehler"), ("test_kreise", "warnung"), ("test_orte", "warnung"),
    ]

    # --- Lauf 2: gleiche Daten, aber neue zufällige gml:id - nichts darf als geändert gelten ---
    bericht = lauf_ausfuehren(konfiguration, dsn=dsn, auswahl=["test_kreise", "test_orte"])[0]
    q = nach_quelle(bericht)
    assert (q["test_kreise"]["neu"], q["test_kreise"]["geaendert"], q["test_kreise"]["unveraendert"]) == (0, 0, 5)
    assert q["test_orte"]["unveraendert"] == 3
    [(stichtag,)] = abfrage(dsn, "SELECT now()")

    # --- Lauf 3: Änderungen in der Quelle ----------------------------------------------------
    kreise_v2 = [
        kreis(1, "Havelland"),
        kreis(2, "Potsdam-Mittelmark (neu benannt)"),              # Attribut geändert
        kreis(3, "Teltow-Fläming", ring=quadrat(385000, 5800000, 2000)),  # Geometrie geändert
        kreis(4, "Selbstüberschneidung", ring=FLIEGE),
        kreis(7, "Uckermark"),                                     # neu; 5 (Prignitz) entfällt
        kreis(6, "Kaputte Geometrie", ring="370000 abc 5800000"),
    ]
    dienst.wfs_objekte = kreise_v2
    bericht = lauf_ausfuehren(konfiguration, dsn=dsn, auswahl=["test_kreise"])[0]
    k = nach_quelle(bericht)["test_kreise"]
    assert (k["neu"], k["geaendert"], k["geloescht"], k["unveraendert"]) == (1, 2, 1, 2)
    assert k["objekte_aktuell"] == 5

    # Historie: alte Versionen geschlossen, Löschung markiert, Stichtagsabfrage liefert alten Stand
    assert abfrage(
        dsn, "SELECT version, gueltig_bis IS NULL FROM geodaten.geometrien WHERE objekt_id = '00002' ORDER BY version"
    ) == [(1, False), (2, True)]
    assert abfrage(dsn, "SELECT geloescht, gueltig_bis IS NOT NULL FROM geodaten.geometrien WHERE objekt_id = '00005'") == [
        (True, True)
    ]
    alt = dict(abfrage(dsn, "SELECT objekt_id, attribute->>'name' FROM geodaten.objekte_zum_stichtag(%s) "
                            "WHERE quelle_id = 'test_kreise'", stichtag))
    assert alt["00002"] == "Potsdam-Mittelmark" and "00005" in alt and "00007" not in alt
    aktuell = dict(abfrage(dsn, "SELECT objekt_id, attribute->>'name' FROM geodaten.objekte_aktuell "
                                "WHERE quelle_id = 'test_kreise'"))
    assert aktuell["00002"] == "Potsdam-Mittelmark (neu benannt)" and "00005" not in aktuell

    # --- Lauf 4: Quelle liefert plötzlich fast nichts -> Löschschutz -------------------------
    dienst.wfs_objekte = kreise_v2[:1]
    k = nach_quelle(lauf_ausfuehren(konfiguration, dsn=dsn, auswahl=["test_kreise"])[0])["test_kreise"]
    assert k["loeschschutz_ausgeloest"] is True and k["geloescht"] == 0
    assert any("Löschschutz" in w for w in k["warnungen"])
    assert abfrage(dsn, "SELECT count(*) FROM geodaten.objekte_aktuell WHERE quelle_id = 'test_kreise'") == [(5,)]

    # --- Lauf 5: Trockenlauf meldet Änderungen, speichert aber nichts -----------------------
    dienst.wfs_objekte = list(KREISE_V1)
    vorher = abfrage(dsn, "SELECT count(*) FROM geodaten.geometrien")
    k = nach_quelle(lauf_ausfuehren(konfiguration, dsn=dsn, auswahl=["test_kreise"], trockenlauf=True)[0])["test_kreise"]
    assert k["geaendert"] == 2 and k["neu"] == 1
    assert abfrage(dsn, "SELECT count(*) FROM geodaten.geometrien") == vorher

    # --- Manueller Start über die Kommandozeile: Rückgabewert 1 = Quelle fehlgeschlagen ------
    rueckgabe = main(["-k", str(tmp_path / "config"), "lauf"])
    assert rueckgabe == 1


@pytest.mark.parametrize("modus", ["erstes", "zusammenfuehren"])
def test_mehrteilige_objekte(dsn, dienst, tmp_path, modus):
    dienst.wfs_objekte = [
        kreis(1, "Schutzgebiet A"),
        {**kreis(1, "Schutzgebiet A", ring=quadrat(390000, 5810000)), "gml_id": "kreis_00001_2"},  # 2. Teilfläche
        kreis(2, "Schutzgebiet B"),
    ]
    verzeichnis = konfiguration_schreiben(tmp_path, dienst.basis_url)
    datei = verzeichnis / "quellen" / "test_kreise.yaml"
    datei.write_text(datei.read_text(encoding="utf-8") + f"doppelte_ids: {modus}\n", encoding="utf-8")
    konfiguration = lade_konfiguration(verzeichnis)

    k = nach_quelle(lauf_ausfuehren(konfiguration, dsn=dsn, auswahl=["test_kreise"])[0])["test_kreise"]
    [(typ, teile)] = abfrage(
        dsn, "SELECT ST_GeometryType(geom), ST_NumGeometries(geom) FROM geodaten.geometrien WHERE objekt_id = '00001'"
    )
    if modus == "zusammenfuehren":
        assert (k["neu"], k["verworfen"], k["warnungen"]) == (2, 0, [])
        assert k["hinweise"] == ["2 Teilobjekte mit gleicher ID zu 1 Multi-Objekten zusammengeführt"]
        assert (typ, teile) == ("ST_MultiPolygon", 2)
    else:
        assert (k["neu"], k["verworfen"]) == (2, 1)
        assert any("doppelte Objekt-ID" in w for w in k["warnungen"])
        assert (typ, teile) == ("ST_Polygon", 1)

    # Wiederholung ohne Änderung: zusammengeführte Geometrie muss stabil sein
    k = nach_quelle(lauf_ausfuehren(konfiguration, dsn=dsn, auswahl=["test_kreise"])[0])["test_kreise"]
    assert (k["neu"], k["geaendert"], k["unveraendert"]) == (0, 0, 2)


def test_zusammenfuehren_unabhaengig_von_lieferreihenfolge(dsn, dienst, tmp_path):
    """Teilflächen mit abweichenden Attributen, Dienst liefert beim zweiten Lauf andersherum."""
    teile = [
        kreis(1, "Schutzgebiet A"),
        {**kreis(1, "Schutzgebiet A, Teilfläche Nord", ring=quadrat(390000, 5810000)), "gml_id": "kreis_00001_2"},
        {**kreis(1, "Schutzgebiet A", ring=quadrat(392000, 5810000)), "gml_id": "kreis_00001_3"},
        kreis(2, "Schutzgebiet B"),
    ]
    dienst.wfs_objekte = list(teile)
    verzeichnis = konfiguration_schreiben(tmp_path, dienst.basis_url)
    datei = verzeichnis / "quellen" / "test_kreise.yaml"
    datei.write_text(datei.read_text(encoding="utf-8") + "doppelte_ids: zusammenfuehren\n", encoding="utf-8")
    konfiguration = lade_konfiguration(verzeichnis)

    k = nach_quelle(lauf_ausfuehren(konfiguration, dsn=dsn, auswahl=["test_kreise"])[0])["test_kreise"]
    assert k["neu"] == 2 and any("Attribute unterscheiden sich" in w for w in k["warnungen"])
    assert abfrage(dsn, "SELECT ST_NumGeometries(geom) FROM geodaten.geometrien WHERE objekt_id = '00001'") == [(3,)]

    dienst.wfs_objekte = list(reversed(teile))
    k = nach_quelle(lauf_ausfuehren(konfiguration, dsn=dsn, auswahl=["test_kreise"])[0])["test_kreise"]
    assert (k["neu"], k["geaendert"], k["unveraendert"]) == (0, 0, 2)


def test_laufsperre_verhindert_parallele_laeufe(dsn, dienst, tmp_path):
    dienst.csv_text = ORTE_V1
    konfiguration = lade_konfiguration(konfiguration_schreiben(tmp_path, dienst.basis_url))
    with psycopg.connect(dsn, autocommit=True) as andere:
        datenbank.migrieren(andere)
        assert datenbank.sperre_holen(andere)
        with pytest.raises(LaufAktiv):
            lauf_ausfuehren(konfiguration, dsn=dsn, auswahl=["test_orte"])


@pytest.mark.parametrize(
    "gml, erwartet",
    [
        # URN-Schreibweise: EPSG-Achsenfolge Breite/Länge
        ('<gml:Point xmlns:gml="http://www.opengis.net/gml/3.2" srsName="urn:ogc:def:crs:EPSG::4326">'
         "<gml:pos>52.4116 12.5357</gml:pos></gml:Point>", (12.5357, 52.4116)),
        # Kurzform EPSG:4326: Länge/Breite
        ('<gml:Point xmlns:gml="http://www.opengis.net/gml/3.2" srsName="EPSG:4326">'
         "<gml:pos>12.5357 52.4116</gml:pos></gml:Point>", (12.5357, 52.4116)),
    ],
)
def test_achsenreihenfolge_wgs84(dsn, gml, erwartet):
    with psycopg.connect(dsn, autocommit=True) as verbindung:
        datenbank.migrieren(verbindung)
        lon, lat, status = verbindung.execute(
            "SELECT round(ST_X(ST_Transform(geom, 4326))::numeric, 6), round(ST_Y(ST_Transform(geom, 4326))::numeric, 6), "
            "status FROM pipeline.geometrie_parsen('gml', %s, NULL, 25833)",
            (gml,),
        ).fetchone()
    assert (float(lon), float(lat)) == erwartet and status == "ok"
