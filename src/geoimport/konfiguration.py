"""Laden und Prüfen der Konfiguration.

Aufbau eines Konfigurationsverzeichnisses::

    config/
      pipeline.yaml          globale Einstellungen (Ziel-CRS, Zeitplan, HTTP ...)
      quellen/
        bb_kreise.yaml       je Quelle eine Datei
        hochschulen.yaml

Eine neue Quelle wird angebunden, indem eine weitere YAML-Datei in ``quellen/``
abgelegt wird - die Kernlogik bleibt unverändert. Werte der Form ``${NAME}``
oder ``${NAME:-vorgabe}`` werden aus Umgebungsvariablen ersetzt, damit
Zugangsdaten nie in Konfigurationsdateien stehen.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Annotated, Literal, Union

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from .fehler import KonfigurationsFehler

_UMGEBUNG = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")


def umgebung_ersetzen(text: str) -> str:
    """Ersetzt ``${NAME}`` und ``${NAME:-vorgabe}`` durch Umgebungsvariablen."""

    def ersetze(treffer: re.Match[str]) -> str:
        name, vorgabe = treffer.group(1), treffer.group(2)
        wert = os.environ.get(name)
        if wert is None:
            if vorgabe is None:
                raise KonfigurationsFehler(f"Umgebungsvariable {name} ist nicht gesetzt")
            return vorgabe
        return wert

    return _UMGEBUNG.sub(ersetze, text)


class _Streng(BaseModel):
    """Unbekannte Schlüssel sind Fehler - Tippfehler fallen sofort auf."""

    model_config = ConfigDict(extra="forbid")


class AttributAbbildung(_Streng):
    """Abbildung der Quellattribute auf das homogenisierte Zielschema."""

    nur: list[str] | None = Field(None, description="Nur diese Quellattribute übernehmen")
    ausschliessen: list[str] = Field(default_factory=list)
    umbenennen: dict[str, str] = Field(default_factory=dict, description="Quellname -> Zielname")
    konstanten: dict[str, str | int | float | bool] = Field(default_factory=dict)
    typen: dict[str, Literal["text", "zahl", "ganzzahl", "datum", "bool"]] = Field(
        default_factory=dict, description="Zielname -> Datentyp"
    )


class Anmeldung(_Streng):
    benutzer: str
    passwort: str


class _QuelleBasis(_Streng):
    id: str = Field(pattern=r"^[a-z0-9_]+$", description="Stabiler Schlüssel der Quelle")
    bezeichnung: str
    aktiv: bool = True
    beschreibung: str | None = None
    lizenz: str | None = None
    quellenvermerk: str | None = None
    id_feld: str | list[str] | None = Field(
        None, description="Quellattribut(e) mit stabiler Objekt-ID; Liste = zusammengesetzter Schlüssel"
    )
    attribute: AttributAbbildung = Field(default_factory=AttributAbbildung)
    doppelte_ids: Literal["erstes", "zusammenfuehren"] = Field(
        "erstes",
        description="Mehrere Objekte mit gleicher ID: erstes übernehmen (Warnung) oder Teilgeometrien zu einem "
        "Multi-Objekt zusammenführen (z. B. Schutzgebiete aus mehreren Teilflächen)",
    )
    anmeldung: Anmeldung | None = None
    loeschschutz_quote: float | None = Field(None, ge=0, le=1)
    plausibilitaet_bbox: tuple[float, float, float, float] | None = Field(
        None, description="Erwarteter Raum in WGS84 (min_lon, min_lat, max_lon, max_lat)"
    )


class WfsQuelle(_QuelleBasis):
    typ: Literal["wfs"]
    url: str
    layer: str
    version: Literal["2.0.0", "1.1.0"] = "2.0.0"
    crs: int | None = Field(None, description="Angefragtes Koordinatensystem (EPSG-Code)")
    ausgabeformat: Literal["gml", "geojson"] = "gml"
    seitengroesse: int = Field(1000, ge=1, le=100_000)
    paging: Literal["auto", "aus"] = "auto"
    sortierung: str | None = None
    bbox: tuple[float, float, float, float] | None = None
    max_objekte: int | None = Field(None, ge=1)
    geometrie_feld: str | None = None
    zusatzparameter: dict[str, str] = Field(default_factory=dict)


class CsvGeometrie(_Streng):
    x: str | None = None
    y: str | None = None
    wkt: str | None = None
    crs: int = 4326

    @model_validator(mode="after")
    def _eine_variante(self) -> CsvGeometrie:
        if self.wkt is None and (self.x is None or self.y is None):
            raise ValueError("geometrie braucht entweder x und y oder wkt")
        if self.wkt is not None and (self.x is not None or self.y is not None):
            raise ValueError("geometrie: entweder x/y oder wkt angeben, nicht beides")
        return self


class CsvQuelle(_QuelleBasis):
    typ: Literal["csv"]
    url: str | None = None
    pfad: str | None = None
    parameter: dict[str, str] = Field(default_factory=dict, description="Query-Parameter für url")
    kodierung: str | None = None
    trennzeichen: str | None = None
    dezimaltrennzeichen: Literal[".", ","] = "."
    ueberspringe_zeilen: int = Field(0, ge=0)
    geometrie: CsvGeometrie
    filter: dict[str, str | list[str]] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _url_oder_pfad(self) -> CsvQuelle:
        if (self.url is None) == (self.pfad is None):
            raise ValueError("CSV-Quelle braucht genau eines von url oder pfad")
        return self


Quelle = Annotated[Union[WfsQuelle, CsvQuelle], Field(discriminator="typ")]


class HttpEinstellungen(_Streng):
    timeout_s: float = Field(60, gt=0)
    versuche: int = Field(3, ge=1, le=10)
    wartezeit_s: float = Field(2, ge=0, description="Basis für exponentielles Warten zwischen Versuchen")
    user_agent: str = "geoimport/0.1 (+https://github.com/Mark296688/geodaten-import-pipeline)"


class PipelineEinstellungen(_Streng):
    ziel_crs: int = 25833
    zeitplan: str = "0 3 * * *"
    zeitzone: str = "Europe/Berlin"
    berichte_verzeichnis: str = "berichte"
    loeschschutz_quote: float = Field(0.5, ge=0, le=1)
    plausibilitaet_bbox: tuple[float, float, float, float] | None = None
    http: HttpEinstellungen = Field(default_factory=HttpEinstellungen)


class Konfiguration(_Streng):
    pipeline: PipelineEinstellungen = Field(default_factory=PipelineEinstellungen)
    quellen: list[Quelle] = Field(default_factory=list)
    basis_verzeichnis: Path = Field(default=Path("."), exclude=True)

    @field_validator("quellen")
    @classmethod
    def _eindeutige_ids(cls, quellen: list) -> list:
        gesehen: set[str] = set()
        for quelle in quellen:
            if quelle.id in gesehen:
                raise ValueError(f"Quellen-ID {quelle.id!r} ist doppelt vergeben")
            gesehen.add(quelle.id)
        return quellen

    def aktive_quellen(self, auswahl: list[str] | None = None) -> list[WfsQuelle | CsvQuelle]:
        if auswahl:
            unbekannt = set(auswahl) - {q.id for q in self.quellen}
            if unbekannt:
                raise KonfigurationsFehler(f"Unbekannte Quelle(n): {', '.join(sorted(unbekannt))}")
            return [q for q in self.quellen if q.id in auswahl]
        return [q for q in self.quellen if q.aktiv]


def _yaml_lesen(pfad: Path) -> object:
    try:
        return yaml.safe_load(umgebung_ersetzen(pfad.read_text(encoding="utf-8"))) or {}
    except yaml.YAMLError as fehler:
        raise KonfigurationsFehler(f"{pfad}: kein gültiges YAML ({fehler})") from fehler


def _fehlertext(pfad: Path, fehler: ValidationError) -> str:
    zeilen = [f"{pfad}: Konfiguration ungültig"]
    for eintrag in fehler.errors():
        ort = ".".join(str(teil) for teil in eintrag["loc"])
        zeilen.append(f"  - {ort}: {eintrag['msg']}")
    return "\n".join(zeilen)


def lade_konfiguration(pfad: str | Path) -> Konfiguration:
    """Lädt eine Konfigurationsdatei oder ein Konfigurationsverzeichnis."""
    pfad = Path(pfad)
    if not pfad.exists():
        raise KonfigurationsFehler(f"Konfiguration nicht gefunden: {pfad}")

    if pfad.is_dir():
        basis = pfad
        haupt = pfad / "pipeline.yaml"
        roh = _yaml_lesen(haupt) if haupt.exists() else {}
        if not isinstance(roh, dict):
            raise KonfigurationsFehler(f"{haupt}: erwartet wird ein Mapping")
        quellen = list(roh.get("quellen") or [])
        for datei in sorted((pfad / "quellen").glob("*.y*ml")):
            inhalt = _yaml_lesen(datei)
            quellen.extend(inhalt if isinstance(inhalt, list) else [inhalt])
        roh["quellen"] = quellen
        quelle_der_meldung = pfad
    else:
        basis = pfad.parent
        roh = _yaml_lesen(pfad)
        quelle_der_meldung = pfad

    if not isinstance(roh, dict):
        raise KonfigurationsFehler(f"{quelle_der_meldung}: erwartet wird ein Mapping")
    try:
        konfiguration = Konfiguration.model_validate(roh)
    except ValidationError as fehler:
        raise KonfigurationsFehler(_fehlertext(quelle_der_meldung, fehler)) from fehler

    if zeitplan := os.environ.get("GEOIMPORT_ZEITPLAN"):
        konfiguration.pipeline.zeitplan = zeitplan
    if verzeichnis := os.environ.get("GEOIMPORT_BERICHTE"):
        konfiguration.pipeline.berichte_verzeichnis = verzeichnis
    konfiguration.basis_verzeichnis = basis.resolve()
    return konfiguration
