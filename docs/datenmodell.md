# Datenmodell

Schema `geodaten` mit drei Tabellen plus Schema `pipeline` für Laufprotokoll und Migrationen.
Definition: `src/geoimport/migrationen/001_grundschema.sql`.

```
geodaten.metadaten 1 ──< n geodaten.geometrien 1 ──< n geodaten.attribute
                                │
                                └── n >── 1 pipeline.import_lauf
```

## geodaten.metadaten — eine Zeile je Quelle

| Spalte | Typ | Bedeutung |
|---|---|---|
| id | serial | Primärschlüssel |
| quelle_id | text, eindeutig | ID aus der Konfiguration |
| bezeichnung, beschreibung | text | Beschreibung der Quelle |
| quelltyp | text | `wfs` oder `csv` |
| quelle_url, layer | text | Herkunft |
| quell_crs | integer | EPSG-Code der Quelle (falls bekannt) |
| lizenz, quellenvermerk | text | Nutzungsbedingungen, Namensnennung |
| attributschema | jsonb | Attributnamen beim letzten Lauf (Grundlage der Schemaabweichungs-Warnung) |
| letzter_import, letzter_status | timestamptz, text | Ergebnis des letzten Laufs (`ok`, `warnung`, `fehler`) |
| objekte_aktuell | integer | aktueller Bestand |

## geodaten.geometrien — eine Zeile je Objekt-Version

| Spalte | Typ | Bedeutung |
|---|---|---|
| id | bigserial | Primärschlüssel der Version |
| metadaten_id | integer | Quelle |
| objekt_id | text | stabile ID aus der Quelle (`id_feld`) |
| version | integer | 1, 2, 3 … je Objekt |
| geom | geometry(Geometry, 25833) | Geometrie in ETRS89 / UTM 33N, geprüft und ggf. repariert |
| inhalt_hash | text | MD5 über normalisierte, auf 1 mm gerundete Geometrie (EWKB) und Attribute |
| gueltig_von, gueltig_bis | timestamptz | Gültigkeitszeitraum; aktuelle Version: `gueltig_bis IS NULL` |
| geloescht | boolean | Version wurde geschlossen, weil das Objekt in der Quelle fehlte |
| import_lauf_id | uuid | Lauf, der die Version angelegt hat |

Indizes: eindeutig (`metadaten_id`, `objekt_id`) für aktuelle Versionen, GiST auf `geom`,
Zeitraumindex.

## geodaten.attribute — Sachattribute je Version

| Spalte | Typ | Bedeutung |
|---|---|---|
| geometrie_id | bigint | Version in `geometrien` |
| schluessel | text | Attributname im Zielschema (nach `umbenennen`) |
| wert | text | Wert als Text |
| datentyp | text | `text`, `zahl`, `ganzzahl`, `datum` (ISO 8601), `bool` |

## Historisierung

| Ereignis in der Quelle | Wirkung |
|---|---|
| Objekt neu | Version 1 mit `gueltig_von = Laufzeitpunkt` |
| Geometrie oder Attribute geändert | bisherige Version erhält `gueltig_bis`, neue Version n+1 |
| unverändert | keine Schreiboperation |
| Objekt fehlt | aktuelle Version erhält `gueltig_bis` und `geloescht = true` (außer bei Löschschutz) |
| Objekt taucht wieder auf | neue Version mit fortlaufender Nummer |

Verglichen wird die in PostGIS geparste Geometrie, nicht der GML-Text. Dienste, die bei jedem Abruf
neue `gml:id`-Werte vergeben, erzeugen deshalb keine Scheinänderungen.

## Abfragebeispiele

```sql
-- aktueller Stand mit Attributen als JSON (auch direkt in QGIS ladbar)
SELECT quelle_id, objekt_id, attribute->>'name' AS name, geom
FROM geodaten.objekte_aktuell
WHERE quelle_id = 'bb_gemeinden';

-- Stand zu einem Stichtag
SELECT * FROM geodaten.objekte_zum_stichtag('2026-10-01 00:00+02');

-- Änderungsgeschichte eines Objekts
SELECT version, gueltig_von, gueltig_bis, geloescht
FROM geodaten.geometrien g JOIN geodaten.metadaten m ON m.id = g.metadaten_id
WHERE m.quelle_id = 'bb_kreise' AND g.objekt_id = '12051'
ORDER BY version;

-- räumliche Verschneidung: Naturschutzgebiete je Landkreis (Fläche in ha)
SELECT k.attribute->>'name' AS kreis, count(*) AS gebiete,
       round(sum(ST_Area(ST_Intersection(k.geom, n.geom)))::numeric / 10000) AS flaeche_ha
FROM geodaten.objekte_aktuell k
JOIN geodaten.objekte_aktuell n ON n.quelle_id = 'bb_naturschutzgebiete' AND ST_Intersects(k.geom, n.geom)
WHERE k.quelle_id = 'bb_kreise'
GROUP BY 1 ORDER BY 3 DESC;

-- letzte Läufe
SELECT gestartet_am, status, bericht->'zusammenfassung' FROM pipeline.import_lauf ORDER BY gestartet_am DESC LIMIT 5;
```

## Anpassung an ein vorhandenes Zielmodell

Das Modell bildet die Dreiteilung Metadaten/Geometrien/Attribute ab. Weicht ein vorhandenes Modell in
Tabellen- oder Spaltennamen ab, wird die Abbildung in `laden.py` (SQL der Schritte Abgleich und
Einfügen) und die Migration angepasst; Quellen, Abbildung und Bericht bleiben unverändert.
