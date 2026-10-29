# Installation und Betrieb

## Voraussetzungen

- Docker mit Compose-Plugin (getestet mit Docker 29.5, Compose v2)
- Netzzugang zu den konfigurierten Quellen

## Installation

```bash
git clone https://github.com/Mark296688/geodaten-import-pipeline.git
cd geodaten-import-pipeline
cp .env.example .env          # mindestens POSTGRES_PASSWORD setzen
docker compose up -d --build
```

Das startet zwei Dienste:

| Dienst | Image | Aufgabe |
|---|---|---|
| `db` | `postgis/postgis:16-3.4` | PostgreSQL 16 mit PostGIS 3.4, Port nur lokal (`127.0.0.1:${DB_PORT}`) |
| `pipeline` | `geoimport:0.1` (aus dem `Dockerfile`, Stufe `betrieb`) | Zeitplanbetrieb, läuft als unprivilegierter Benutzer |

Beim ersten Lauf legt die Pipeline das Datenmodell selbst an (Migrationen in
`src/geoimport/migrationen/`, protokolliert in `pipeline.schema_migration`). Ohne Lauf geht das mit
`docker compose run --rm pipeline geoimport migrieren`.

Soll eine **vorhandene** PostgreSQL-Instanz genutzt werden, entfällt der Dienst `db`; es genügt,
`DATABASE_URL` für den Dienst `pipeline` auf diese Instanz zu setzen. Die Erweiterung `postgis`
muss dort verfügbar sein.

## Konfiguration

| Ort | Inhalt |
|---|---|
| `.env` | Datenbank-Zugang, Zeitplan (`GEOIMPORT_ZEITPLAN`), Log-Level |
| `config/pipeline.yaml` | Ziel-CRS, Zeitplan, Löschschutz-Schwelle, Plausibilitätsraum, HTTP-Zeitlimits und Wiederholungen |
| `config/quellen/*.yaml` | je Quelle eine Datei (siehe [Entwicklerdokumentation](entwickler.md)) |

Das Verzeichnis `config/` wird schreibgeschützt in den Container eingebunden. Änderungen an Quellen
wirken beim nächsten Lauf ohne Neubau des Images. Zugangsdaten stehen nie in den YAML-Dateien,
sondern werden als `${VARIABLE}` aus der Umgebung gelesen.

Konfiguration prüfen:

```bash
docker compose run --rm pipeline geoimport pruefen
```

## Läufe starten

| Wie | Befehl |
|---|---|
| Zeitgesteuert | Dienst `pipeline` läuft dauerhaft, Standard täglich 03:00 (Europe/Berlin) |
| Manuell, alle aktiven Quellen | `docker compose run --rm pipeline geoimport lauf` |
| Manuell, einzelne Quelle | `docker compose run --rm pipeline geoimport lauf --quelle bb_kreise` |
| Probelauf ohne Speichern | `docker compose run --rm pipeline geoimport lauf --trockenlauf` |
| Sofort beim Start und danach nach Plan | Kommando des Dienstes auf `geoimport zeitplan --sofort` setzen |

Eine Laufsperre in der Datenbank (`pg_try_advisory_lock`) verhindert, dass zwei Läufe gleichzeitig
schreiben. Ein zweiter Start bricht mit Rückgabewert 3 ab.

Rückgabewerte von `geoimport lauf`: `0` alle Quellen erfolgreich, `1` mindestens eine Quelle
fehlgeschlagen, `2` alle fehlgeschlagen oder Konfigurationsfehler, `3` anderer Lauf aktiv.

## Import-Bericht

Nach jedem Lauf schreibt die Pipeline in `berichte/`:

- `<Zeitstempel>_<Lauf-ID>.json`: maschinenlesbar, vollständige Kennzahlen je Quelle
- `<Zeitstempel>_<Lauf-ID>.md`: lesbare Zusammenfassung
- `letzter_bericht.json` / `letzter_bericht.md`: immer der jüngste Lauf

Derselbe JSON-Bericht steht in der Tabelle `pipeline.import_lauf` (Spalte `bericht`).

Inhalt je Quelle: Typ, URL/Layer, Status (`ok`, `warnung`, `fehler`), Startzeit, Dauer, abgerufene
Seiten, erwartete Objektzahl (laut Dienst), abgerufen, neu, geändert, gelöscht, unverändert,
verworfen, repariert, aktueller Bestand, Löschschutz ausgelöst, Warnungen, Hinweise, Fehlermeldung.
Gesamtstatus: `ok`, `warnung`, `teilweise` (mindestens eine Quelle fehlgeschlagen) oder `fehler`.

## Typische Meldungen und was zu tun ist

| Meldung im Bericht | Bedeutung | Vorgehen |
|---|---|---|
| `HTTP 502/503 ...`, `Quelle nicht erreichbar` | Dienst nach allen Wiederholungen nicht erreichbar | nichts; der Bestand bleibt unverändert, der nächste Lauf holt nach |
| `WFS meldet Fehler: ...` | Dienst lehnt die Anfrage ab (z. B. Layername geändert) | Layer per GetCapabilities prüfen, Quell-YAML anpassen |
| `Schemaabweichung: neue Attribute / Attribute fehlen` | Quelle hat ihr Attributschema geändert | Attributabbildung prüfen |
| `Vollständigkeit: Dienst meldet X Objekte, abgerufen wurden Y` | Paging liefert nicht alles | `seitengroesse` erhöhen oder `paging: aus` |
| `Dienst ignoriert STARTINDEX` | kein Paging beim Dienst, automatisch ohne Paging abgerufen | ggf. `paging: aus` setzen |
| `Löschschutz: ...` | auffällig viele Objekte fehlen, Löschungen nicht übernommen | Quelle prüfen; bei gewollter Bereinigung `loeschschutz_quote` der Quelle kurzzeitig auf `1` |
| `... ungültige Geometrie(n) mit ST_MakeValid repariert` | Selbstüberschneidungen o. Ä. in der Quelle | Information; repariert gespeichert |
| `... wegen Geometriefehler verworfen` | Geometrie nicht lesbar oder außerhalb des Plausibilitätsraums | Quelle bzw. `crs`/Achsenfolge prüfen |
| `Kein id_feld konfiguriert` | Änderungserkennung hängt an Ersatz-IDs | stabiles Schlüsselattribut als `id_feld` eintragen |

## Datensicherung

```bash
docker compose exec db pg_dump -U geoimport -d geodaten -Fc > sicherung_$(date +%F).dump
```

## Tests im Betrieb

```bash
docker compose --profile test run --rm --build tests
```

Die Tests verwenden die separate Datenbank `geodaten_test` (angelegt über `docker/initdb/`) und
berühren keine Betriebsdaten.
