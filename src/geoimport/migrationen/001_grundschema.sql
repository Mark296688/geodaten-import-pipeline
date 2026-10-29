-- Zieldatenmodell: drei Tabellen (Metadaten, Geometrien, Attribute) im Schema "geodaten"
-- plus Protokoll der Importläufe im Schema "pipeline".
--
-- Historisierung: Jede Änderung an einem Objekt erzeugt eine neue Version in
-- "geometrien" (gueltig_von/gueltig_bis). Die aktuelle Version hat
-- gueltig_bis IS NULL. Attribute hängen an der Version, nicht am Objekt -
-- damit ist jeder frühere Stand vollständig rekonstruierbar.

CREATE EXTENSION IF NOT EXISTS postgis;

CREATE SCHEMA IF NOT EXISTS geodaten;
CREATE SCHEMA IF NOT EXISTS pipeline;

CREATE TABLE pipeline.import_lauf (
    id            uuid PRIMARY KEY,
    gestartet_am  timestamptz NOT NULL,
    beendet_am    timestamptz,
    status        text,
    trockenlauf   boolean NOT NULL DEFAULT false,
    bericht       jsonb
);
COMMENT ON TABLE pipeline.import_lauf IS 'Ein Eintrag je Importlauf, inkl. vollständigem Import-Bericht (JSON)';

CREATE TABLE geodaten.metadaten (
    id               serial PRIMARY KEY,
    quelle_id        text NOT NULL UNIQUE,
    bezeichnung      text NOT NULL,
    quelltyp         text NOT NULL CHECK (quelltyp IN ('wfs', 'csv')),
    quelle_url       text,
    layer            text,
    quell_crs        integer,
    lizenz           text,
    quellenvermerk   text,
    beschreibung     text,
    attributschema   jsonb NOT NULL DEFAULT '[]'::jsonb,
    letzter_import   timestamptz,
    letzter_status   text,
    objekte_aktuell  integer NOT NULL DEFAULT 0,
    erstellt_am      timestamptz NOT NULL DEFAULT now(),
    aktualisiert_am  timestamptz NOT NULL DEFAULT now()
);
COMMENT ON TABLE geodaten.metadaten IS 'Eine Zeile je Datenquelle (WFS-Layer oder CSV) mit Herkunft, Lizenz und Importstatus';
COMMENT ON COLUMN geodaten.metadaten.attributschema IS 'Attributnamen der Quelle beim letzten Lauf - Grundlage der Schemaabweichungs-Warnung';

CREATE TABLE geodaten.geometrien (
    id              bigserial PRIMARY KEY,
    metadaten_id    integer NOT NULL REFERENCES geodaten.metadaten(id),
    objekt_id       text NOT NULL,
    version         integer NOT NULL CHECK (version >= 1),
    geom            geometry(Geometry, 25833) NOT NULL,
    inhalt_hash     text NOT NULL,
    gueltig_von     timestamptz NOT NULL,
    gueltig_bis     timestamptz,
    geloescht       boolean NOT NULL DEFAULT false,
    import_lauf_id  uuid REFERENCES pipeline.import_lauf(id),
    UNIQUE (metadaten_id, objekt_id, version),
    CHECK (gueltig_bis IS NULL OR gueltig_bis >= gueltig_von)
);
COMMENT ON TABLE geodaten.geometrien IS 'Versionierte Geometrien (ETRS89 / UTM 33N, EPSG:25833); aktuelle Version: gueltig_bis IS NULL';
COMMENT ON COLUMN geodaten.geometrien.objekt_id IS 'Stabile ID aus der Quelle (id_feld) - Schlüssel der Änderungserkennung';
COMMENT ON COLUMN geodaten.geometrien.inhalt_hash IS 'MD5 über normalisierte, auf 1 mm gerundete Geometrie und Attribute - gleich = unverändert';
COMMENT ON COLUMN geodaten.geometrien.geloescht IS 'Version wurde geschlossen, weil das Objekt in der Quelle nicht mehr vorkommt';

CREATE UNIQUE INDEX geometrien_aktuell_uidx ON geodaten.geometrien (metadaten_id, objekt_id) WHERE gueltig_bis IS NULL;
CREATE INDEX geometrien_geom_gix ON geodaten.geometrien USING gist (geom);
CREATE INDEX geometrien_zeitraum_idx ON geodaten.geometrien (metadaten_id, gueltig_von, gueltig_bis);

CREATE TABLE geodaten.attribute (
    id            bigserial PRIMARY KEY,
    geometrie_id  bigint NOT NULL REFERENCES geodaten.geometrien(id) ON DELETE CASCADE,
    schluessel    text NOT NULL,
    wert          text,
    datentyp      text NOT NULL DEFAULT 'text' CHECK (datentyp IN ('text', 'zahl', 'ganzzahl', 'datum', 'bool')),
    UNIQUE (geometrie_id, schluessel)
);
COMMENT ON TABLE geodaten.attribute IS 'Sachattribute je Geometrie-Version (Schlüssel/Wert), homogenisiert über die Attributabbildung';

-- Bequeme Sicht auf den aktuellen Stand, z. B. für QGIS oder die Plattform
CREATE VIEW geodaten.objekte_aktuell AS
SELECT g.id,
       m.quelle_id,
       g.objekt_id,
       g.version,
       g.gueltig_von,
       g.geom,
       coalesce(jsonb_object_agg(a.schluessel, a.wert) FILTER (WHERE a.id IS NOT NULL), '{}'::jsonb) AS attribute
FROM geodaten.geometrien g
JOIN geodaten.metadaten m ON m.id = g.metadaten_id
LEFT JOIN geodaten.attribute a ON a.geometrie_id = g.id
WHERE g.gueltig_bis IS NULL
GROUP BY g.id, m.quelle_id;

-- Stand zu einem beliebigen Zeitpunkt
CREATE FUNCTION geodaten.objekte_zum_stichtag(stichtag timestamptz)
RETURNS TABLE (id bigint, quelle_id text, objekt_id text, version integer, geom geometry, attribute jsonb)
LANGUAGE sql STABLE AS $$
    SELECT g.id, m.quelle_id, g.objekt_id, g.version, g.geom,
           coalesce(jsonb_object_agg(a.schluessel, a.wert) FILTER (WHERE a.id IS NOT NULL), '{}'::jsonb)
    FROM geodaten.geometrien g
    JOIN geodaten.metadaten m ON m.id = g.metadaten_id
    LEFT JOIN geodaten.attribute a ON a.geometrie_id = g.id
    WHERE g.gueltig_von <= stichtag AND (g.gueltig_bis IS NULL OR g.gueltig_bis > stichtag)
    GROUP BY g.id, m.quelle_id
$$;
