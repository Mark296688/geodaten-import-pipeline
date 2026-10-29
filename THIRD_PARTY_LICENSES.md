# Lizenzübersicht der Drittkomponenten

Der Code dieses Repositorys steht unter der Apache License 2.0 (siehe [LICENSE](LICENSE)). Alle
eingesetzten Komponenten sind Open Source. Versionen: Stand des Docker-Images vom 29.10.2026
(`pip list` im Image).

## Laufzeit (Python-Pakete)

| Komponente | Version | Lizenz | Zweck |
|---|---|---|---|
| httpx | 0.28.1 | BSD-3-Clause | HTTP-Abruf der WFS- und CSV-Quellen |
| httpcore | 1.0.9 | BSD-3-Clause | Abhängigkeit von httpx |
| h11 | 0.16.0 | MIT | Abhängigkeit von httpx |
| anyio | 4.15.1 | MIT | Abhängigkeit von httpx |
| idna | 3.20 | BSD-3-Clause | Abhängigkeit von httpx |
| certifi | 2026.10.23 | MPL-2.0 | Zertifikatsbündel für HTTPS |
| PyYAML | 6.0.3 | MIT | Konfigurationsdateien |
| pydantic | 2.14.1 | MIT | Prüfung der Konfiguration |
| pydantic_core | 2.50.1 | MIT | Abhängigkeit von pydantic |
| annotated-types | 0.8.0 | MIT | Abhängigkeit von pydantic |
| typing-inspection | 0.4.4 | MIT | Abhängigkeit von pydantic |
| typing_extensions | 4.16.0 | PSF-2.0 | Abhängigkeit von pydantic |
| psycopg / psycopg-binary | 3.3.6 | LGPL-3.0-only | PostgreSQL-Treiber (unverändert als Bibliothek genutzt) |
| APScheduler | 3.11.3 | MIT | Zeitplan (Cron-Ausdrücke) |
| tzlocal | 5.4.5 | MIT | Abhängigkeit von APScheduler |
| tzdata | 2026.6 | Apache-2.0 | Zeitzonendaten (Europe/Berlin) im Container |

## Nur für Tests

| Komponente | Version | Lizenz |
|---|---|---|
| pytest | 9.2.0 | MIT |
| pluggy | 1.7.0 | MIT |
| iniconfig | 2.3.1 | MIT |
| packaging | 26.3 | Apache-2.0 OR BSD-2-Clause |
| Pygments | 2.21.0 | BSD-2-Clause |

## Infrastruktur (Container-Images)

| Komponente | Version | Lizenz |
|---|---|---|
| PostgreSQL | 16.4 | PostgreSQL License |
| PostGIS (Image `postgis/postgis:16-3.4`) | 3.4 | GPL-2.0-or-later |
| Python (Image `python:3.12-slim`) | 3.12 | PSF-2.0; Basissystem Debian (verschiedene freie Lizenzen) |

## Daten der Beispielkonfiguration

Die Daten selbst sind nicht Teil des Repositorys; sie werden zur Laufzeit abgerufen.

| Quelle | Lizenz / Quellenvermerk |
|---|---|
| Verwaltungsgrenzen Brandenburg (LGB, vg_wfs) | Datenlizenz Deutschland - Namensnennung - 2.0, © GeoBasis-DE/LGB |
| Schutzgebiete Brandenburg (LfU, schutzg_wfs) | Datenlizenz Deutschland - Namensnennung - 2.0, © LfU Brandenburg |
| OpenStreetMap über Overpass-API | Open Database License 1.0, © OpenStreetMap-Mitwirkende |

Hinweis zu LGPL (psycopg): Die Bibliothek wird unverändert als eigenständiges Paket installiert und
dynamisch eingebunden; Nutzer können sie jederzeit gegen eine andere Version austauschen.
