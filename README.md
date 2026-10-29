# Geodaten-Import-Pipeline (WFS/CSV → PostgreSQL/PostGIS)

Konfigurierbare, containerisierte Import-Pipeline für Geodaten. Sie liest **WFS-Dienste** und
**CSV-Dateien** (lokal oder per URL), prüft und transformiert die Daten und überführt sie in ein
homogenisiertes PostgreSQL/PostGIS-Modell aus drei Tabellen: **Metadaten, Geometrien, Attribute**.
Änderungen werden historisiert, nach jedem Lauf entsteht ein Import-Bericht (JSON und Markdown).

Lizenz: [Apache-2.0](LICENSE) · Python 3.12 · PostgreSQL 16 / PostGIS 3.4 · ausschließlich Open-Source-Komponenten
([Lizenzübersicht](THIRD_PARTY_LICENSES.md))

## Was die Pipeline kann

| Anforderung | Umsetzung |
|---|---|
| WFS-Quellen mit Paging | WFS 2.0.0 (1.1.0 ohne Paging), STARTINDEX/COUNT mit Erkennung von Diensten, die Paging ignorieren; Vollständigkeitsprüfung über `RESULTTYPE=hits`; GML 3.x und GeoJSON |
| Unterschiedliche Schemata → ein Zielmodell | Attributabbildung je Quelle: `nur`, `ausschliessen`, `umbenennen`, `konstanten`, `typen` (Text, Zahl, Ganzzahl, Datum, Wahrheitswert) |
| CSV-Quellen | Datei oder URL, Trennzeichen automatisch, Dezimalkomma, UTF-8/Windows-1252, Kopfzeilen überspringen, x/y- oder WKT-Spalte, Zeilenfilter |
| Koordinatensysteme | Transformation in PostGIS ins Ziel-CRS EPSG:25833; korrekte Achsenreihenfolge für `urn:ogc:def:crs:EPSG::4326` (getestet) |
| Datenqualität | Geometrien je Objekt geprüft, ungültige mit `ST_MakeValid` repariert, unbrauchbare verworfen (ohne den Lauf abzubrechen), Plausibilitätsraum, doppelte IDs, mehrteilige Objekte zusammenführen |
| Historisierung | Versionen mit `gueltig_von`/`gueltig_bis`, Änderungserkennung über Inhalts-Hash der geparsten Geometrie + Attribute, Stichtagsabfrage `geodaten.objekte_zum_stichtag(...)` |
| Löschschutz | Fehlt ein auffälliger Anteil der Objekte (Standard > 50 %), werden keine Löschungen übernommen |
| Fehlertoleranz je Quelle | Eigene Transaktion je Quelle; Wiederholung mit wachsender Wartezeit; Ausfall einer Quelle blockiert den Lauf nicht |
| Automatisierung | Zeitplan per Cron-Ausdruck im Container, manueller Start per Befehl, Laufsperre gegen parallele Läufe |
| Konfiguration ohne Codeänderung | Je Quelle eine YAML-Datei in `config/quellen/`; Zugangsdaten nur über Umgebungsvariablen |
| Import-Bericht | Je Lauf JSON (maschinenlesbar) + Markdown (lesbar), zusätzlich in `pipeline.import_lauf` gespeichert |
| Tests | Unit-Tests und End-to-End-Test (WFS + CSV → PostGIS → Bericht) gegen eine echte Datenbank |

## Schnellstart

```bash
cp .env.example .env              # Passwort setzen
docker compose up -d --build      # Datenbank + Pipeline im Zeitplanbetrieb
docker compose run --rm pipeline geoimport lauf          # Import jetzt manuell starten
docker compose --profile test run --rm --build tests     # alle Tests inkl. End-to-End
```

Der Bericht des letzten Laufs liegt in `berichte/letzter_bericht.md` bzw. `.json`.

Die Beispielkonfiguration bindet echte, offene Quellen an:

| Quelle | Typ | Besonderheit |
|---|---|---|
| Landkreise und kreisfreie Städte Brandenburg (LGB, `vg_wfs`) | WFS, GML 3.2, EPSG:25833 | Paging in kleinen Seiten; der Dienst meldet `ImplementsResultPaging=FALSE`, unterstützt STARTINDEX aber |
| Gemeinden Brandenburg (LGB, `vg_wfs`) | WFS, GML 3.2 | 413 Objekte in 5 Seiten |
| Naturschutzgebiete Brandenburg (LfU, `schutzg_wfs`) | WFS, GeoJSON (WGS84) | anderes Attributschema; 641 Teilflächen → 480 Gebiete zusammengeführt; Datum `01.10.1990` → `1990-10-01` |
| Hochschulstandorte Brandenburg (OpenStreetMap, Overpass) | CSV per URL | Punkte aus Länge/Breite, Quellenvermerk ODbL |

## Erfahrungen mit realen Diensten

Die Beispielquellen wurden im Oktober 2026 live importiert (Erstlauf 951 Objekte neu, Wiederholungslauf
951 unverändert, siehe [Beispielbericht](docs/beispielbericht.md)). Dabei aufgetretene Eigenheiten, die die
Pipeline abfängt:

- Die deegree-Dienste melden `ImplementsResultPaging=FALSE`, werten STARTINDEX aber aus. Die Pipeline
  versucht Paging und erkennt Dienste, die es tatsächlich ignorieren (Seite n identisch mit Seite 1).
- `numberReturned` ist `0`, obwohl Objekte geliefert werden. Gezählt wird selbst, die Vollständigkeit wird
  gegen `RESULTTYPE=hits` geprüft.
- Geometrien tragen bei jedem Abruf neue, zufällige `gml:id`-Werte. Ein Vergleich über den GML-Text würde
  jeden Lauf alle Objekte als geändert melden; verglichen wird deshalb die geparste, normalisierte Geometrie.
- Naturschutzgebiete bestehen aus mehreren Teilflächen mit derselben Gebietsnummer (641 Teilflächen,
  480 Gebiete); sie werden zu MultiPolygonen zusammengeführt, unabhängig von der Lieferreihenfolge.
- GeoJSON-Ausgaben liegen in WGS84 Länge/Breite, GML in ETRS89/UTM 33N.
- Die Overpass-API war zeitweise überlastet (HTTP 502): drei Versuche mit wachsender Wartezeit, danach
  Fehler im Bericht, die übrigen Quellen wurden normal importiert.

## Aufbau

```
Quellen (WFS / CSV)
   │  quellen/wfs.py, quellen/csv_quelle.py      Abruf, Paging, Parsen, Wiederholungen
   ▼
Abbildung (abbildung.py)                          Objekt-ID, Attributschema → Zielschema, Typen
   ▼
Laden (laden.py, eine Transaktion je Quelle)      COPY → Staging → PostGIS: parsen, transformieren,
   │                                              reparieren → Abgleich neu/geändert/gelöscht → Versionen
   ▼
PostgreSQL/PostGIS: geodaten.metadaten · geodaten.geometrien · geodaten.attribute
   ▼
Import-Bericht (bericht.py): berichte/*.json + *.md, pipeline.import_lauf
```

## Dokumentation

- [Installation und Betrieb](docs/betrieb.md): Docker-Setup, Konfiguration, Zeitplan, Berichte, Fehlerbilder
- [Entwicklerdokumentation](docs/entwickler.md): neue WFS-/CSV-Quellen anbinden, neuen Quelltyp ergänzen, Tests
- [Datenmodell](docs/datenmodell.md): Tabellen, Historisierung, Abfragebeispiele
- [Lizenzübersicht der Drittkomponenten](THIRD_PARTY_LICENSES.md)

## Stand

Prototyp der Modular Ops GmbH (Oktober 2026). Funktionsumfang wie oben, getestet gegen die
genannten Live-Dienste und im End-to-End-Test. Noch nicht enthalten sind u. a. OGC-Filter (FES)
beim Abruf, Authentifizierung jenseits von HTTP Basic und Streaming sehr großer Antworten ohne Paging.
