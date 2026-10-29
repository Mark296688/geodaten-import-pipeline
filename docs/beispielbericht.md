# Beispiel: Import-Bericht eines Wiederholungslaufs

Echter Lauf vom 29.10.2026 mit der Beispielkonfiguration gegen die Live-Dienste (zweiter Lauf direkt nach dem Erstimport mit 951 neuen Objekten). Obwohl die WFS-Dienste bei jedem Abruf neue `gml:id`-Werte vergeben, erkennt die Pipeline alle 951 Objekte korrekt als unverändert. Die maschinenlesbare Fassung liegt in [beispielbericht.json](beispielbericht.json).

---

## Import-Bericht 2026-10-29 13:54:10

- **Status:** erfolgreich mit Warnungen
- **Lauf-ID:** `b56d7c9d-a3a6-43b4-967e-90f39b71de3f`
- **Dauer:** 151,6 s
- **Quellen:** 4 (4 erfolgreich, 0 fehlgeschlagen)
- **Datensätze:** 0 neu, 0 geändert, 0 gelöscht, 951 unverändert, 0 verworfen
- **Ziel-CRS:** EPSG:25833

### Quellen

| Quelle | Typ | Status | abgerufen | neu | geändert | gelöscht | unverändert | verworfen | Dauer (s) |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|
| bb_gemeinden | WFS | ok | 413 | 0 | 0 | 0 | 413 | 0 | 56,7 |
| bb_kreise | WFS | ok | 18 | 0 | 0 | 0 | 18 | 0 | 10,8 |
| bb_naturschutzgebiete | WFS | warnung | 641 | 0 | 0 | 0 | 480 | 0 | 43,4 |
| hochschulen_osm | CSV | ok | 40 | 0 | 0 | 0 | 40 | 0 | 40,7 |

### Warnungen

- **bb_naturschutzgebiete:** Attribute unterscheiden sich zwischen Teilobjekten bei 6 Objekt(en) - Attribute eines Teils übernommen, deterministisch gewählt (z. B. 3049-501, 3241-502, 3543-501, 3949-501, 3949-502)

### Hinweise

- **bb_naturschutzgebiete:** 241 Teilobjekte mit gleicher ID zu 80 Multi-Objekten zusammengeführt
