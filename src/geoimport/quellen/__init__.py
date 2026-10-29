"""Quelltypen. Ein neuer Quelltyp implementiert ``objekte()`` und wird hier registriert."""

from __future__ import annotations

from ..http import HttpClient
from ..konfiguration import CsvQuelle, WfsQuelle
from .basis import Objekt, QuellenErgebnis
from .csv_quelle import CsvLeser
from .wfs import WfsLeser

__all__ = ["Objekt", "QuellenErgebnis", "leser_erzeugen"]


def leser_erzeugen(quelle: WfsQuelle | CsvQuelle, http: HttpClient, ergebnis: QuellenErgebnis, basis_verzeichnis):
    """Liefert den passenden Leser für eine konfigurierte Quelle."""
    if isinstance(quelle, WfsQuelle):
        return WfsLeser(quelle, http, ergebnis)
    if isinstance(quelle, CsvQuelle):
        return CsvLeser(quelle, http, ergebnis, basis_verzeichnis)
    raise TypeError(f"Unbekannter Quelltyp: {type(quelle).__name__}")  # pragma: no cover
