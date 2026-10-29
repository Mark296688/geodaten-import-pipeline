"""WFS-Quellen (OGC Web Feature Service 2.0.0, eingeschränkt 1.1.0).

Erfahrungen aus realen Diensten, die hier abgefangen werden:

* Dienste melden ``ImplementsResultPaging=FALSE``, unterstützen STARTINDEX
  aber trotzdem - oder umgekehrt. Deshalb wird Paging versucht und geprüft:
  Liefert Seite 2 dasselbe erste Objekt wie Seite 1, wird ohne Paging neu
  abgerufen.
* ``numberReturned`` ist bei manchen Servern 0, obwohl Objekte geliefert
  werden. Gezählt wird deshalb selbst; ``numberMatched`` aus einer
  RESULTTYPE=hits-Abfrage dient als Vollständigkeitsprüfung.
* Geometrien tragen bei jeder Abfrage neue, zufällige ``gml:id``-Werte. Die
  Änderungserkennung vergleicht deshalb nie GML-Text, sondern die in PostGIS
  geparste Geometrie.
* GeoJSON-Ausgaben liegen nach RFC 7946 in WGS84 (Länge/Breite), auch wenn
  der Dienst intern ETRS89/UTM verwendet.
"""

from __future__ import annotations

import io
import json
import logging
import re
import xml.etree.ElementTree as ET
from collections.abc import Iterator
from typing import Any

from ..fehler import QuellenFehler
from ..http import HttpClient
from ..konfiguration import WfsQuelle
from .basis import Objekt, QuellenErgebnis

log = logging.getLogger(__name__)

GML_NAMENSRAEUME = {"http://www.opengis.net/gml", "http://www.opengis.net/gml/3.2"}
XLINK_HREF = "{http://www.w3.org/1999/xlink}href"
XSI_NIL = "{http://www.w3.org/2001/XMLSchema-instance}nil"
FEHLER_WURZELN = {"ExceptionReport", "ServiceExceptionReport"}
MAX_SEITEN = 100_000


def _namensraum(tag: str) -> str:
    return tag[1:].split("}", 1)[0] if tag.startswith("{") else ""


def _lokal(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _ist_gml(element: ET.Element) -> bool:
    return isinstance(element.tag, str) and _namensraum(element.tag) in GML_NAMENSRAEUME


class WfsLeser:
    def __init__(self, quelle: WfsQuelle, http: HttpClient, ergebnis: QuellenErgebnis) -> None:
        self.quelle = quelle
        self.http = http
        self.ergebnis = ergebnis
        self._zaehler = 0

    # ------------------------------------------------------------------ Abfrage

    def _parameter(self) -> dict[str, str]:
        q = self.quelle
        params = {"SERVICE": "WFS", "VERSION": q.version, "REQUEST": "GetFeature"}
        params["TYPENAMES" if q.version == "2.0.0" else "TYPENAME"] = q.layer
        if q.crs:
            params["SRSNAME"] = f"urn:ogc:def:crs:EPSG::{q.crs}" if q.version == "2.0.0" else f"EPSG:{q.crs}"
        if q.ausgabeformat == "geojson":
            params["OUTPUTFORMAT"] = "application/geo+json"
        if q.sortierung:
            params["SORTBY"] = q.sortierung
        if q.bbox:
            werte = ",".join(f"{wert:g}" for wert in q.bbox)
            if q.crs:
                werte += f",urn:ogc:def:crs:EPSG::{q.crs}" if q.version == "2.0.0" else f",EPSG:{q.crs}"
            params["BBOX"] = werte
        params.update(q.zusatzparameter)
        return params

    def _treffer_zaehlen(self) -> int | None:
        """Fragt die Gesamtzahl der Objekte ab (RESULTTYPE=hits); None, wenn nicht ermittelbar."""
        params = {k: v for k, v in self._parameter().items() if k not in {"OUTPUTFORMAT", "SORTBY"}}
        params["RESULTTYPE"] = "hits"
        try:
            antwort = self.http.get(self.quelle.url, params, self.quelle.anmeldung)
        except QuellenFehler as fehler:
            log.info("%s: Trefferzahl nicht ermittelbar (%s)", self.quelle.id, fehler)
            return None
        treffer = re.search(rb'numberMatched="(\d+)"', antwort.content[:4000])
        return int(treffer.group(1)) if treffer else None

    def objekte(self) -> Iterator[Objekt]:
        q = self.quelle
        basis = self._parameter()

        if q.paging == "aus" or q.version != "2.0.0":
            params = dict(basis)
            if q.max_objekte:
                params["COUNT" if q.version == "2.0.0" else "MAXFEATURES"] = str(q.max_objekte)
            yield from self._seite(params)
            return

        erwartet = None if q.max_objekte else self._treffer_zaehlen()
        self.ergebnis.erwartet = erwartet
        geliefert: set[str] = set()
        anzahl = 0
        start = 0
        erste_seite: list[str] = []

        while True:
            groesse = q.seitengroesse if not q.max_objekte else min(q.seitengroesse, q.max_objekte - anzahl)
            seite = list(self._seite({**basis, "COUNT": str(groesse), "STARTINDEX": str(start)}))
            kennungen = [self._kennung(objekt) for objekt in seite]
            if start == 0:
                erste_seite = kennungen
            elif kennungen and kennungen == erste_seite:
                # Seite n ist identisch mit Seite 1: der Dienst wertet STARTINDEX nicht aus
                self.ergebnis.warnen(
                    "Dienst ignoriert STARTINDEX (kein Paging) - Abruf wurde ohne Paging in einem Stück wiederholt"
                )
                for objekt in self._seite(dict(basis)):
                    kennung = self._kennung(objekt)
                    if kennung not in geliefert:
                        geliefert.add(kennung)
                        anzahl += 1
                        yield objekt
                break
            for objekt, kennung in zip(seite, kennungen):
                geliefert.add(kennung)
                anzahl += 1
                yield objekt
            if len(seite) < groesse or (q.max_objekte and anzahl >= q.max_objekte):
                break
            start += len(seite)
            if self.ergebnis.seiten >= MAX_SEITEN:
                raise QuellenFehler(f"Abbruch nach {MAX_SEITEN} Seiten - Paging-Schleife?")

        if erwartet is not None and anzahl != erwartet:
            self.ergebnis.warnen(
                f"Vollständigkeit: Dienst meldet {erwartet} Objekte (RESULTTYPE=hits), abgerufen wurden {anzahl}"
            )

    def _kennung(self, objekt: Objekt) -> str:
        """Fingerabdruck zum Wiedererkennen eines Objekts zwischen Abrufen (ohne laufende Ersatznummer)."""
        stabile_id = "" if objekt.ersatz_id.startswith(f"{self.quelle.layer}#") else objekt.ersatz_id
        return stabile_id + json.dumps(objekt.attribute, sort_keys=True, default=str)

    def _seite(self, params: dict[str, str]) -> Iterator[Objekt]:
        antwort = self.http.get(self.quelle.url, params, self.quelle.anmeldung)
        self.ergebnis.seiten += 1
        inhalt = antwort.content
        if self.quelle.ausgabeformat == "geojson" and not inhalt.lstrip().startswith(b"<"):
            yield from self._geojson(inhalt)
        else:
            yield from self._gml(inhalt, antwort.headers.get("content-type", ""))

    # ------------------------------------------------------------------ GML

    def _gml(self, inhalt: bytes, inhaltstyp: str) -> Iterator[Objekt]:
        tiefe = 0
        fehlermeldung = False
        try:
            for ereignis, element in ET.iterparse(io.BytesIO(inhalt), events=("start", "end")):
                if ereignis == "start":
                    if tiefe == 0:
                        name = _lokal(element.tag)
                        if name in FEHLER_WURZELN:
                            fehlermeldung = True
                        elif name != "FeatureCollection":
                            raise QuellenFehler(f"Unerwartete WFS-Antwort <{name}> (Content-Type {inhaltstyp})")
                    tiefe += 1
                    continue
                tiefe -= 1
                if tiefe == 1 and not fehlermeldung:
                    name = _lokal(element.tag)
                    if name in {"member", "featureMember", "featureMembers"}:
                        for merkmal in element:
                            if isinstance(merkmal.tag, str):
                                yield self._aus_gml(merkmal)
                        element.clear()
                elif tiefe == 0 and fehlermeldung:
                    text = " ".join(t.strip() for t in element.itertext() if t.strip())
                    raise QuellenFehler(f"WFS meldet Fehler: {text[:500]}")
        except ET.ParseError as fehler:
            auszug = inhalt[:200].decode("utf-8", "replace").replace("\n", " ")
            raise QuellenFehler(f"Antwort ist kein gültiges XML ({fehler}; Content-Type {inhaltstyp}): {auszug}") from fehler

    def _aus_gml(self, merkmal: ET.Element) -> Objekt:
        self._zaehler += 1
        gml_id = next((merkmal.get(f"{{{ns}}}id") for ns in GML_NAMENSRAEUME if merkmal.get(f"{{{ns}}}id")), None)
        attribute: dict[str, Any] = {}
        geom_text: str | None = None
        geom_feld: str | None = None

        for kind in merkmal:
            if not isinstance(kind.tag, str) or _ist_gml(kind):
                continue  # gml:boundedBy, gml:name, Kommentare
            name = _lokal(kind.tag)
            geometrie = next((e for e in kind if _ist_gml(e)), None)
            if geometrie is not None:
                if self.quelle.geometrie_feld and name != self.quelle.geometrie_feld:
                    continue
                if geom_text is None:
                    geometrie.tail = None
                    geom_text = ET.tostring(geometrie, encoding="unicode")
                    geom_feld = name
                else:
                    self.ergebnis.warnen(
                        f"Objekte haben mehrere Geometrieeigenschaften - übernommen wird {geom_feld!r}; "
                        "anderes Feld über geometrie_feld wählen"
                    )
                continue
            self._attribut(attribute, name, kind)

        ersatz_id = gml_id or f"{self.quelle.layer}#{self._zaehler}"
        return Objekt(ersatz_id, "gml", geom_text, self.quelle.crs, attribute)

    def _attribut(self, ziel: dict[str, Any], name: str, element: ET.Element) -> None:
        kinder = [k for k in element if isinstance(k.tag, str)]
        if kinder:
            for kind in kinder:
                self._attribut(ziel, f"{name}.{_lokal(kind.tag)}", kind)
            return
        if element.get(XSI_NIL) == "true":
            wert = None
        else:
            wert = (element.text or "").strip() or element.get(XLINK_HREF)
        if name in ziel and ziel[name] not in (None, "") and wert:
            ziel[name] = f"{ziel[name]}; {wert}"  # mehrfach vorkommende Eigenschaft
        else:
            ziel[name] = wert

    # ------------------------------------------------------------------ GeoJSON

    def _geojson(self, inhalt: bytes) -> Iterator[Objekt]:
        try:
            daten = json.loads(inhalt)
        except ValueError as fehler:
            raise QuellenFehler(f"Antwort ist kein gültiges GeoJSON: {fehler}") from fehler
        if not isinstance(daten, dict) or daten.get("type") != "FeatureCollection":
            raise QuellenFehler("GeoJSON-Antwort ist keine FeatureCollection")
        srid = self.quelle.crs or 4326
        for merkmal in daten.get("features") or []:
            self._zaehler += 1
            attribute: dict[str, Any] = {}
            _flach(attribute, "", merkmal.get("properties") or {})
            geometrie = merkmal.get("geometry")
            ersatz_id = str(merkmal.get("id") or f"{self.quelle.layer}#{self._zaehler}")
            yield Objekt(ersatz_id, "geojson", json.dumps(geometrie) if geometrie else None, srid, attribute)


def _flach(ziel: dict[str, Any], praefix: str, wert: Any) -> None:
    if isinstance(wert, dict):
        for schluessel, inhalt in wert.items():
            _flach(ziel, f"{praefix}.{schluessel}" if praefix else schluessel, inhalt)
    elif isinstance(wert, list):
        ziel[praefix] = json.dumps(wert, ensure_ascii=False)
    else:
        ziel[praefix] = wert
