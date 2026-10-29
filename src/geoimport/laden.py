"""Laden einer Quelle in das Zieldatenmodell mit Änderungserkennung und Historisierung.

Ablauf je Quelle (eine Datenbanktransaktion - scheitert etwas, bleibt der
vorherige Stand dieser Quelle unverändert):

1. Objekte per COPY in eine temporäre Staging-Tabelle schreiben
2. Geometrien in PostGIS parsen, ins Ziel-CRS transformieren, ggf. reparieren
3. Unbrauchbare Objekte und doppelte Objekt-IDs aussortieren (Bericht)
4. Inhalts-Hash bilden und mit dem aktuellen Stand vergleichen:
   neu / geändert / unverändert / gelöscht
5. Löschschutz: Fehlen auffällig viele Objekte, werden keine Löschungen
   übernommen (typisch für halb ausgelieferte Dienste)
6. Geänderte und gelöschte Versionen schließen, neue Versionen anlegen
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable

import psycopg

from .konfiguration import CsvQuelle, PipelineEinstellungen, WfsQuelle
from .quellen.basis import MAX_BEISPIELE, Objekt, QuellenErgebnis

log = logging.getLogger(__name__)

_STAGING = """
CREATE TEMP TABLE staging (
    nr           serial PRIMARY KEY,
    objekt_id    text NOT NULL,
    geom_format  text NOT NULL,
    geom_text    text,
    geom_srid    integer,
    attribute    jsonb NOT NULL,
    geom         geometry,
    geo_status   text,
    inhalt_hash  text
) ON COMMIT DROP
"""

_PARSEN = """
UPDATE staging s
SET geom = p.geom, geo_status = p.status
FROM staging s2
CROSS JOIN LATERAL pipeline.geometrie_parsen(s2.geom_format, s2.geom_text, s2.geom_srid, %(ziel)s) p
WHERE s2.nr = s.nr
"""

_PLAUSIBILITAET = """
UPDATE staging
SET geo_status = 'fehler: außerhalb des erwarteten Raums', geom = NULL
WHERE geom IS NOT NULL
  AND NOT ST_Intersects(ST_Transform(geom, 4326), ST_MakeEnvelope(%(x1)s, %(y1)s, %(x2)s, %(y2)s, 4326))
"""

_DUPLIKATE = """
WITH d AS (
    SELECT nr, row_number() OVER (PARTITION BY objekt_id ORDER BY geom IS NULL, nr) AS rang FROM staging
)
DELETE FROM staging s USING d WHERE d.nr = s.nr AND d.rang > 1
RETURNING s.objekt_id
"""

_ZUSAMMENFUEHREN = """
WITH gruppen AS (
    -- Attribute: deterministisch das Teil mit der kleinsten Attributkombination, unabhängig von der Lieferreihenfolge
    SELECT objekt_id, (array_agg(nr ORDER BY attribute::text, nr))[1] AS erste_nr, count(*) AS teile,
           count(DISTINCT attribute) AS varianten,
           ST_Multi(ST_Union(geom ORDER BY ST_AsEWKB(geom))) AS geom_neu
    FROM staging
    WHERE geom IS NOT NULL
    GROUP BY objekt_id
    HAVING count(*) > 1
)
UPDATE staging s SET geom = g.geom_neu
FROM gruppen g
WHERE s.nr = g.erste_nr
RETURNING g.objekt_id, g.teile, g.varianten, g.erste_nr
"""

_METADATEN = """
INSERT INTO geodaten.metadaten (quelle_id, bezeichnung, quelltyp, quelle_url, layer, quell_crs,
                                lizenz, quellenvermerk, beschreibung)
VALUES (%(quelle_id)s, %(bezeichnung)s, %(quelltyp)s, %(url)s, %(layer)s, %(crs)s,
        %(lizenz)s, %(quellenvermerk)s, %(beschreibung)s)
ON CONFLICT (quelle_id) DO UPDATE SET
    bezeichnung = EXCLUDED.bezeichnung, quelltyp = EXCLUDED.quelltyp, quelle_url = EXCLUDED.quelle_url,
    layer = EXCLUDED.layer, quell_crs = EXCLUDED.quell_crs, lizenz = EXCLUDED.lizenz,
    quellenvermerk = EXCLUDED.quellenvermerk, beschreibung = EXCLUDED.beschreibung, aktualisiert_am = now()
RETURNING id, attributschema
"""

_ABGLEICH = """
CREATE TEMP TABLE abgleich ON COMMIT DROP AS
SELECT s.nr, s.objekt_id, g.id AS alt_id,
       CASE WHEN g.id IS NULL THEN 'neu'
            WHEN g.inhalt_hash <> s.inhalt_hash THEN 'geaendert'
            ELSE 'unveraendert' END AS art
FROM staging s
LEFT JOIN geodaten.geometrien g
       ON g.metadaten_id = %(m)s AND g.gueltig_bis IS NULL AND g.objekt_id = s.objekt_id
WHERE s.geom IS NOT NULL
"""

_GELOESCHT = """
SELECT g.id, g.objekt_id FROM geodaten.geometrien g
WHERE g.metadaten_id = %(m)s AND g.gueltig_bis IS NULL
  AND NOT EXISTS (SELECT 1 FROM staging s WHERE s.objekt_id = g.objekt_id)
"""

_EINFUEGEN = """
WITH neu AS (
    INSERT INTO geodaten.geometrien (metadaten_id, objekt_id, version, geom, inhalt_hash, gueltig_von, import_lauf_id)
    SELECT %(m)s, s.objekt_id,
           coalesce((SELECT max(h.version) FROM geodaten.geometrien h
                     WHERE h.metadaten_id = %(m)s AND h.objekt_id = s.objekt_id), 0) + 1,
           s.geom, s.inhalt_hash, now(), %(lauf)s
    FROM abgleich a JOIN staging s ON s.nr = a.nr
    WHERE a.art IN ('neu', 'geaendert')
    RETURNING id, objekt_id
)
INSERT INTO geodaten.attribute (geometrie_id, schluessel, wert, datentyp)
SELECT n.id, kv.key, kv.value #>> '{}',
       coalesce(%(typen)s::jsonb ->> kv.key,
                CASE jsonb_typeof(kv.value) WHEN 'number' THEN 'zahl' WHEN 'boolean' THEN 'bool' ELSE 'text' END)
FROM neu n
JOIN staging s ON s.objekt_id = n.objekt_id
CROSS JOIN LATERAL jsonb_each(s.attribute) kv
"""


def metadaten_anlegen(cur: psycopg.Cursor, quelle: WfsQuelle | CsvQuelle) -> tuple[int, list[str]]:
    cur.execute(
        _METADATEN,
        {
            "quelle_id": quelle.id,
            "bezeichnung": quelle.bezeichnung,
            "quelltyp": quelle.typ,
            "url": quelle.url if quelle.url else getattr(quelle, "pfad", None),
            "layer": getattr(quelle, "layer", None),
            "crs": quelle.crs if isinstance(quelle, WfsQuelle) else quelle.geometrie.crs,
            "lizenz": quelle.lizenz,
            "quellenvermerk": quelle.quellenvermerk,
            "beschreibung": quelle.beschreibung,
        },
    )
    metadaten_id, schema = cur.fetchone()
    return metadaten_id, list(schema or [])


def status_vermerken(verbindung: psycopg.Connection, quelle: WfsQuelle | CsvQuelle, status: str) -> None:
    """Hält einen Fehlschlag in den Metadaten fest (eigene kleine Transaktion)."""
    with verbindung.transaction(), verbindung.cursor() as cur:
        metadaten_id, _ = metadaten_anlegen(cur, quelle)
        cur.execute(
            "UPDATE geodaten.metadaten SET letzter_status = %s, aktualisiert_am = now() WHERE id = %s",
            (status, metadaten_id),
        )


def importieren(
    verbindung: psycopg.Connection,
    quelle: WfsQuelle | CsvQuelle,
    objekte: Iterable[Objekt],
    ergebnis: QuellenErgebnis,
    lauf_id: str,
    einstellungen: PipelineEinstellungen,
) -> None:
    """Lädt die Objekte einer Quelle. Muss innerhalb einer Transaktion laufen."""
    with verbindung.cursor() as cur:
        metadaten_id, altes_schema = metadaten_anlegen(cur, quelle)

        cur.execute(_STAGING)
        with cur.copy("COPY staging (objekt_id, geom_format, geom_text, geom_srid, attribute) FROM STDIN") as kopie:
            for objekt in objekte:
                kopie.write_row(
                    (
                        objekt.objekt_id,
                        objekt.geom_format,
                        objekt.geom_text,
                        objekt.geom_srid,
                        json.dumps(objekt.attribute, ensure_ascii=False, sort_keys=True, default=str),
                    )
                )

        _schema_vergleichen(ergebnis, altes_schema)

        cur.execute(_PARSEN, {"ziel": einstellungen.ziel_crs})
        bbox = quelle.plausibilitaet_bbox or einstellungen.plausibilitaet_bbox
        if bbox:
            cur.execute(_PLAUSIBILITAET, dict(zip(("x1", "y1", "x2", "y2"), bbox)))

        cur.execute("SELECT count(*) FILTER (WHERE geo_status = 'repariert') FROM staging")
        ergebnis.repariert = cur.fetchone()[0]
        if ergebnis.repariert:
            ergebnis.warnen(f"{ergebnis.repariert} ungültige Geometrie(n) mit ST_MakeValid repariert")

        cur.execute("SELECT objekt_id, geo_status FROM staging WHERE geom IS NULL ORDER BY nr")
        unbrauchbar = cur.fetchall()
        if unbrauchbar:
            ergebnis.verworfen += len(unbrauchbar)
            beispiele = "; ".join(f"{oid}: {status}" for oid, status in unbrauchbar[:MAX_BEISPIELE])
            ergebnis.warnen(f"{len(unbrauchbar)} Objekt(e) wegen Geometriefehler verworfen ({beispiele})")

        if quelle.doppelte_ids == "zusammenfuehren":
            cur.execute(_ZUSAMMENFUEHREN)
            gruppen = cur.fetchall()
            if gruppen:
                cur.execute(
                    "DELETE FROM staging WHERE objekt_id = ANY(%s) AND nr <> ALL(%s)",
                    ([zeile[0] for zeile in gruppen], [zeile[3] for zeile in gruppen]),
                )
                teile = sum(zeile[1] for zeile in gruppen)
                ergebnis.hinweisen(f"{teile} Teilobjekte mit gleicher ID zu {len(gruppen)} Multi-Objekten zusammengeführt")
                abweichend = [zeile[0] for zeile in gruppen if zeile[2] > 1]
                if abweichend:
                    ergebnis.warnen(
                        f"Attribute unterscheiden sich zwischen Teilobjekten bei {len(abweichend)} Objekt(en) - "
                        f"Attribute eines Teils übernommen, deterministisch gewählt (z. B. {', '.join(abweichend[:MAX_BEISPIELE])})"
                    )

        cur.execute(_DUPLIKATE)
        doppelt = [zeile[0] for zeile in cur.fetchall()]
        if doppelt and quelle.doppelte_ids == "erstes":
            ergebnis.verworfen += len(doppelt)
            ergebnis.warnen(
                f"{len(doppelt)} doppelte Objekt-ID(s) - jeweils erstes Vorkommen übernommen "
                f"(z. B. {', '.join(doppelt[:MAX_BEISPIELE])})"
            )

        # Hash über die normalisierte, auf 1 mm gerundete Geometrie: Dienste, die Stützpunkte oder
        # Teilflächen in anderer Reihenfolge liefern, erzeugen so keine Scheinänderungen.
        cur.execute(
            "UPDATE staging SET inhalt_hash = md5(encode(ST_AsEWKB(ST_Normalize(ST_SnapToGrid(geom, 0.001))), 'hex') "
            "|| attribute::text) WHERE geom IS NOT NULL"
        )
        cur.execute(_ABGLEICH, {"m": metadaten_id})
        cur.execute("SELECT art, count(*) FROM abgleich GROUP BY art")
        zaehler = dict(cur.fetchall())
        ergebnis.neu = zaehler.get("neu", 0)
        ergebnis.geaendert = zaehler.get("geaendert", 0)
        ergebnis.unveraendert = zaehler.get("unveraendert", 0)

        cur.execute("SELECT count(*) FROM geodaten.geometrien WHERE metadaten_id = %s AND gueltig_bis IS NULL",
                    (metadaten_id,))
        bestand = cur.fetchone()[0]
        cur.execute(_GELOESCHT, {"m": metadaten_id})
        geloescht = cur.fetchall()
        quote = quelle.loeschschutz_quote if quelle.loeschschutz_quote is not None else einstellungen.loeschschutz_quote
        if geloescht and len(geloescht) > quote * bestand:
            ergebnis.loeschschutz_ausgeloest = True
            ergebnis.warnen(
                f"Löschschutz: {len(geloescht)} von {bestand} Objekten fehlen in der Quelle "
                f"(Schwelle {quote:.0%}) - Löschungen NICHT übernommen, Quelle prüfen"
            )
            geloescht = []
        ergebnis.geloescht = len(geloescht)

        cur.execute(
            "UPDATE geodaten.geometrien g SET gueltig_bis = now() FROM abgleich a "
            "WHERE a.art = 'geaendert' AND g.id = a.alt_id"
        )
        if geloescht:
            cur.execute(
                "UPDATE geodaten.geometrien SET gueltig_bis = now(), geloescht = true WHERE id = ANY(%s)",
                ([zeile[0] for zeile in geloescht],),
            )
        cur.execute(
            _EINFUEGEN,
            {"m": metadaten_id, "lauf": lauf_id, "typen": json.dumps(quelle.attribute.typen)},
        )

        cur.execute(
            """
            UPDATE geodaten.metadaten SET
                attributschema = %(schema)s::jsonb, letzter_import = now(), letzter_status = %(status)s,
                objekte_aktuell = (SELECT count(*) FROM geodaten.geometrien
                                   WHERE metadaten_id = %(m)s AND gueltig_bis IS NULL),
                aktualisiert_am = now()
            WHERE id = %(m)s
            RETURNING objekte_aktuell
            """,
            {
                "schema": json.dumps(ergebnis.attributschema, ensure_ascii=False),
                "status": "warnung" if ergebnis.warnungen else "ok",
                "m": metadaten_id,
            },
        )
        ergebnis.objekte_aktuell = cur.fetchone()[0]


def _schema_vergleichen(ergebnis: QuellenErgebnis, altes_schema: list[str]) -> None:
    if not altes_schema or not ergebnis.abgerufen:
        return
    neu = sorted(set(ergebnis.attributschema) - set(altes_schema))
    fehlend = sorted(set(altes_schema) - set(ergebnis.attributschema))
    if neu:
        ergebnis.warnen(f"Schemaabweichung: neue Attribute in der Quelle: {', '.join(neu)}")
    if fehlend:
        ergebnis.warnen(f"Schemaabweichung: Attribute fehlen gegenüber dem letzten Lauf: {', '.join(fehlend)}")
