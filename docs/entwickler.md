# Entwicklerdokumentation

## Neue Quelle anbinden (ohne Codeänderung)

Eine neue Datei in `config/quellen/` genügt. Beim nächsten Lauf wird sie importiert.

### WFS

```yaml
id: bb_kreise                       # eindeutig, nur a-z, 0-9, _
typ: wfs
bezeichnung: Landkreise Brandenburg
url: https://isk.geobasis-bb.de/ows/vg_wfs
layer: app:krs                      # FeatureType aus GetCapabilities
crs: 25833                          # angefragtes CRS (SRSNAME); leer = Standard des Dienstes
ausgabeformat: gml                  # gml (Standard) oder geojson
seitengroesse: 1000                 # Paging über STARTINDEX/COUNT
paging: auto                        # auto | aus
sortierung: krss                    # SORTBY, empfohlen für stabiles Paging
bbox: [300000, 5700000, 480000, 5950000]   # optional, im crs der Quelle
max_objekte: 100                    # optional, z. B. zum Testen
geometrie_feld: geom                # nur nötig, wenn ein Objekt mehrere Geometrien hat
zusatzparameter: {}                 # weitere KVP-Parameter, z. B. herstellerspezifische
id_feld: krss                       # stabiles Schlüsselattribut (Liste = zusammengesetzt)
doppelte_ids: erstes                # erstes | zusammenfuehren (Teilflächen -> Multi-Geometrie)
anmeldung: {benutzer: "${WFS_NUTZER}", passwort: "${WFS_PASSWORT}"}   # optional, HTTP Basic
lizenz: Datenlizenz Deutschland - Namensnennung - Version 2.0
quellenvermerk: "© GeoBasis-DE/LGB"
attribute:
  nur: [krss, krs_name]             # optional: nur diese übernehmen
  ausschliessen: []                 # optional
  umbenennen: {krss: schluessel, krs_name: name}
  konstanten: {ebene: kreis}
  typen: {stand: datum}             # text | zahl | ganzzahl | datum | bool
```

Vorgehen für einen unbekannten Dienst:

1. `?SERVICE=WFS&REQUEST=GetCapabilities` aufrufen: Layername (`<Name>`), unterstützte CRS und
   Ausgabeformate ablesen.
2. Mit `max_objekte: 5` und `geoimport lauf --quelle <id> --trockenlauf` die Attribute im Bericht
   und in den Warnungen ansehen.
3. Stabiles `id_feld` festlegen (amtlicher Schlüssel, nicht `gml:id`) und die Attributabbildung auf
   die gemeinsamen Zielnamen (`schluessel`, `name`, `art`, ...) ausrichten.
4. `max_objekte` entfernen, Trockenlauf wiederholen, dann regulär laufen lassen.

Hinweise zu Koordinaten:

- GML wird mit seinem `srsName` gelesen. Bei `urn:ogc:def:crs:EPSG::4326` gilt Breite/Länge, PostGIS
  dreht die Achsen korrekt (Test `test_achsenreihenfolge_wgs84`).
- GeoJSON ist nach RFC 7946 immer WGS84 Länge/Breite; `crs` nur setzen, wenn ein Dienst nachweislich
  anders ausliefert.
- Ziel ist immer EPSG:25833 (Spaltentyp im Datenmodell). Ein anderes Ziel-CRS braucht eine Migration.

### CSV

```yaml
id: hochschulen
typ: csv
bezeichnung: Hochschulstandorte
pfad: daten/hochschulen.csv         # relativ zum Konfigurationsverzeichnis; alternativ url:
# url: https://example.org/export
# parameter: {format: csv}          # Query-Parameter für url
kodierung: utf-8                    # leer = UTF-8, Rückfall Windows-1252 mit Warnung
trennzeichen: ";"                   # leer = automatisch erkennen
dezimaltrennzeichen: ","            # für Koordinaten in x/y-Spalten
ueberspringe_zeilen: 0              # Vorspann vor der Kopfzeile
geometrie: {x: lon, y: lat, crs: 4326}     # oder {wkt: geometrie, crs: 25833}
filter: {bundesland: Brandenburg}   # optional, Gleichheit oder Liste erlaubter Werte
id_feld: kennung
attribute:
  umbenennen: {Name: name}
```

Zeilen ohne verwertbare Koordinate werden verworfen und im Bericht mit Zeilennummer gemeldet.

## Projektstruktur

```
src/geoimport/
  cli.py              Befehle lauf | zeitplan | migrieren | pruefen
  konfiguration.py    YAML laden, Umgebungsvariablen, Prüfung (pydantic)
  ablauf.py           ein Lauf: Sperre, Quellen nacheinander, Fehler je Quelle, Bericht
  quellen/
    basis.py          Objekt (Quellformat) und QuellenErgebnis (Kennzahlen)
    wfs.py            WFS-Abruf, Paging, GML/GeoJSON-Parsing
    csv_quelle.py     CSV-Abruf und -Parsing
  abbildung.py        Objekt-ID, Attributabbildung, Typumwandlung
  laden.py            Staging, Geometrieprüfung, Abgleich, Historisierung
  bericht.py          JSON- und Markdown-Bericht
  zeitplan.py         Cron-Betrieb (APScheduler)
  datenbank.py        Verbindung, Migrationen, Laufsperre
  migrationen/*.sql   Datenmodell, versioniert
tests/                Unit- und End-to-End-Tests, lokaler Test-WFS (hilfen.py)
config/               Beispielkonfiguration mit Live-Quellen aus Brandenburg
```

## Neuen Quelltyp ergänzen (z. B. GeoPackage, OGC API Features)

1. Konfigurationsmodell in `konfiguration.py` von `_QuelleBasis` ableiten, mit eigenem `typ`-Literal,
   und in die Union `Quelle` aufnehmen.
2. Leser-Klasse in `quellen/` mit Methode `objekte()` schreiben, die `Objekt`-Instanzen liefert:
   Ersatz-ID, Geometrie als Text im Format `gml`, `geojson` oder `wkt`, Quell-SRID und ein
   flaches Attribut-Dictionary. Paging, Wiederholungen (über `HttpClient`) und Zähler
   (`ergebnis.seiten`, `ergebnis.warnen(...)`) liegen beim Leser.
3. In `quellen/__init__.py` in `leser_erzeugen` registrieren.

Geometrieprüfung, Transformation, Abbildung, Historisierung und Bericht gelten dann automatisch.

## Datenmodell ändern

Neue SQL-Datei `src/geoimport/migrationen/NNN_beschreibung.sql` anlegen (fortlaufende Nummer). Sie
wird beim nächsten Lauf genau einmal angewendet und in `pipeline.schema_migration` vermerkt.
Bestehende Migrationsdateien nie nachträglich ändern.

## Tests

```bash
docker compose --profile test run --rm --build tests   # alles, inkl. End-to-End gegen PostGIS
```

Lokal ohne Docker (Unit-Tests; End-to-End nur mit `DATABASE_URL`):

```bash
pip install -e ".[test]"
pytest
DATABASE_URL=postgresql://geoimport:...@localhost:5433/geodaten_test pytest
pytest -m live            # optional: Abruf gegen die echten Brandenburger Dienste
```

Der End-to-End-Test (`tests/test_ende_zu_ende.py`) startet einen lokalen Test-WFS, der sich wie die
realen deegree-Dienste verhält (zufällige `gml:id` je Abruf, `numberReturned="0"`), sowie eine
CSV-Quelle per URL und eine nicht erreichbare Quelle. Geprüft werden über mehrere Läufe:
Erstimport, stabile Wiederholung, Änderungen und Löschungen mit Historie, Stichtagsabfrage,
Löschschutz, Trockenlauf, Fehlertoleranz, Laufsperre, Kommandozeile und der Import-Bericht.
