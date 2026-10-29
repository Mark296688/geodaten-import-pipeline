"""Datenbankverbindung, Migrationen, Laufsperre und Laufprotokoll."""

from __future__ import annotations

import json
import logging
import os
from importlib import resources
from typing import Any

import psycopg

from .fehler import KonfigurationsFehler

log = logging.getLogger(__name__)

# Fester Schlüssel für pg_try_advisory_lock: verhindert parallele Importläufe,
# z. B. wenn ein manueller Start in einen geplanten Lauf fällt.
SPERR_SCHLUESSEL = 7_310_250_001


def verbinden(dsn: str | None = None) -> psycopg.Connection:
    dsn = dsn or os.environ.get("DATABASE_URL")
    if not dsn:
        raise KonfigurationsFehler("DATABASE_URL ist nicht gesetzt (z. B. postgresql://nutzer:pass@db:5432/geodaten)")
    return psycopg.connect(dsn, autocommit=True, application_name="geoimport")


def migrieren(verbindung: psycopg.Connection) -> list[str]:
    """Spielt alle noch nicht angewendeten SQL-Migrationen in Reihenfolge ein."""
    verbindung.execute(
        "CREATE SCHEMA IF NOT EXISTS pipeline;"
        "CREATE TABLE IF NOT EXISTS pipeline.schema_migration ("
        " version text PRIMARY KEY, angewendet_am timestamptz NOT NULL DEFAULT now())"
    )
    with verbindung.transaction():
        verbindung.execute("LOCK TABLE pipeline.schema_migration IN EXCLUSIVE MODE")
        angewendet = {zeile[0] for zeile in verbindung.execute("SELECT version FROM pipeline.schema_migration")}
        neu: list[str] = []
        dateien = sorted(
            (d for d in resources.files("geoimport.migrationen").iterdir() if d.name.endswith(".sql")),
            key=lambda d: d.name,
        )
        for datei in dateien:
            version = datei.name.removesuffix(".sql")
            if version in angewendet:
                continue
            log.info("Migration %s wird angewendet", version)
            verbindung.execute(datei.read_text(encoding="utf-8"))
            verbindung.execute("INSERT INTO pipeline.schema_migration (version) VALUES (%s)", (version,))
            neu.append(version)
    return neu


def sperre_holen(verbindung: psycopg.Connection) -> bool:
    return bool(verbindung.execute("SELECT pg_try_advisory_lock(%s)", (SPERR_SCHLUESSEL,)).fetchone()[0])


def sperre_freigeben(verbindung: psycopg.Connection) -> None:
    verbindung.execute("SELECT pg_advisory_unlock(%s)", (SPERR_SCHLUESSEL,))


def ziel_crs_pruefen(verbindung: psycopg.Connection, ziel_crs: int) -> None:
    srid = verbindung.execute("SELECT Find_SRID('geodaten', 'geometrien', 'geom')").fetchone()[0]
    if srid != ziel_crs:
        raise KonfigurationsFehler(
            f"pipeline.ziel_crs ist {ziel_crs}, das Datenmodell speichert aber EPSG:{srid} - "
            "Ziel-CRS nur zusammen mit einer Migration ändern"
        )


def lauf_beginnen(verbindung: psycopg.Connection, lauf_id: str, gestartet_am: Any, trockenlauf: bool) -> None:
    verbindung.execute(
        "INSERT INTO pipeline.import_lauf (id, gestartet_am, status, trockenlauf) VALUES (%s, %s, 'laeuft', %s)",
        (lauf_id, gestartet_am, trockenlauf),
    )


def lauf_abschliessen(verbindung: psycopg.Connection, lauf_id: str, bericht: dict[str, Any]) -> None:
    verbindung.execute(
        "UPDATE pipeline.import_lauf SET beendet_am = %s, status = %s, bericht = %s::jsonb WHERE id = %s",
        (bericht["beendet_am"], bericht["status"], json.dumps(bericht, ensure_ascii=False), lauf_id),
    )
