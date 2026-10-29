from __future__ import annotations

import os

import psycopg
import pytest

from hilfen import LokalerDienst


@pytest.fixture
def dienst():
    test_dienst = LokalerDienst()
    server = test_dienst.starten()
    yield test_dienst
    server.shutdown()


@pytest.fixture
def dsn():
    """Leere Testdatenbank; Tests ohne DATABASE_URL werden übersprungen."""
    adresse = os.environ.get("DATABASE_URL")
    if not adresse:
        pytest.skip("DATABASE_URL nicht gesetzt - E2E-Tests brauchen PostgreSQL/PostGIS")
    with psycopg.connect(adresse, autocommit=True) as verbindung:
        verbindung.execute("DROP SCHEMA IF EXISTS geodaten CASCADE")
        verbindung.execute("DROP SCHEMA IF EXISTS pipeline CASCADE")
    return adresse
