-- Geometrie aus GML, GeoJSON oder WKT lesen, ins Ziel-CRS transformieren und prüfen.
--
-- Ein einzelnes kaputtes Objekt darf den Import einer Quelle nicht abbrechen:
-- Fehler werden abgefangen und als Status zurückgegeben, das Objekt wird
-- im Import-Bericht als "verworfen" gezählt.
--
-- Achsenreihenfolge: ST_GeomFromGML wertet srsName aus. Bei URN-Angaben
-- (urn:ogc:def:crs:EPSG::4326) gilt die EPSG-Achsenfolge Breite/Länge und
-- PostGIS dreht die Achsen; bei "EPSG:4326" gilt Länge/Breite. GeoJSON ist nach
-- RFC 7946 immer Länge/Breite.

CREATE FUNCTION pipeline.geometrie_parsen(
    format     text,
    text_roh   text,
    quell_srid integer,
    ziel_srid  integer,
    OUT geom   geometry,
    OUT status text
)
LANGUAGE plpgsql STABLE AS $$
DECLARE
    g geometry;
BEGIN
    IF text_roh IS NULL OR btrim(text_roh) = '' THEN
        status := 'fehler: keine Geometrie';
        RETURN;
    END IF;

    g := CASE format
        WHEN 'gml'     THEN ST_GeomFromGML(text_roh)
        WHEN 'geojson' THEN ST_GeomFromGeoJSON(text_roh)
        WHEN 'wkt'     THEN ST_GeomFromText(text_roh)
    END;
    IF g IS NULL THEN
        status := 'fehler: unbekanntes Format ' || coalesce(format, '-');
        RETURN;
    END IF;

    IF ST_SRID(g) = 0 THEN
        IF quell_srid IS NULL THEN
            status := 'fehler: Koordinatensystem unbekannt (crs in der Quelle angeben)';
            RETURN;
        END IF;
        g := ST_SetSRID(g, quell_srid);
    END IF;
    IF ST_SRID(g) <> ziel_srid THEN
        g := ST_Transform(g, ziel_srid);
    END IF;

    IF ST_IsEmpty(g) THEN
        status := 'fehler: leere Geometrie';
        RETURN;
    END IF;
    IF NOT ST_IsValid(g) THEN
        g := ST_MakeValid(g);
        status := 'repariert';
    ELSE
        status := 'ok';
    END IF;
    geom := g;
EXCEPTION WHEN OTHERS THEN
    geom := NULL;
    status := 'fehler: ' || SQLERRM;
END;
$$;

COMMENT ON FUNCTION pipeline.geometrie_parsen IS 'GML/GeoJSON/WKT -> Geometrie im Ziel-CRS; repariert ungültige Geometrien, fängt Fehler je Objekt ab';
