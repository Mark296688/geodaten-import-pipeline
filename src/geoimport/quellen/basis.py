"""Gemeinsame Datentypen aller Quelltypen."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

MAX_BEISPIELE = 5


@dataclass(slots=True)
class Objekt:
    """Ein Geoobjekt, wie es eine Quelle liefert - noch nicht abgebildet.

    Die Geometrie bleibt Text im Quellformat (GML, GeoJSON oder WKT). Geparst,
    transformiert und repariert wird sie erst in PostGIS, damit für alle
    Quelltypen dieselbe, geprüfte Geometrielogik gilt.
    """

    ersatz_id: str
    geom_format: str
    geom_text: str | None
    geom_srid: int | None
    attribute: dict[str, Any]
    objekt_id: str | None = None


@dataclass
class QuellenErgebnis:
    """Kennzahlen und Meldungen einer Quelle in einem Lauf (Teil des Import-Berichts)."""

    quelle_id: str
    typ: str
    bezeichnung: str
    url: str | None = None
    layer: str | None = None
    status: str = "offen"
    gestartet_am: str | None = None
    dauer_s: float = 0.0
    seiten: int = 0
    erwartet: int | None = None
    abgerufen: int = 0
    neu: int = 0
    geaendert: int = 0
    geloescht: int = 0
    unveraendert: int = 0
    verworfen: int = 0
    repariert: int = 0
    objekte_aktuell: int | None = None
    loeschschutz_ausgeloest: bool = False
    warnungen: list[str] = field(default_factory=list)
    hinweise: list[str] = field(default_factory=list)
    fehler: str | None = None
    attributschema: list[str] = field(default_factory=list, repr=False)

    def warnen(self, text: str) -> None:
        if text not in self.warnungen:
            self.warnungen.append(text)

    def hinweisen(self, text: str) -> None:
        if text not in self.hinweise:
            self.hinweise.append(text)

    def als_dict(self) -> dict[str, Any]:
        daten = {k: v for k, v in self.__dict__.items() if k != "attributschema"}
        return daten
