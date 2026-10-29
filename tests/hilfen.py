"""Testhilfen: GML-Erzeugung und ein lokaler Test-Dienst (WFS + CSV per HTTP).

Der Test-WFS verhält sich bewusst wie reale deegree-Dienste in Brandenburg:
zufällige gml:id-Werte an Geometrien bei jedem Abruf, numberReturned="0",
Paging über STARTINDEX/COUNT und Trefferzahl über RESULTTYPE=hits.
"""

from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse


def quadrat(x: float, y: float, kante: float = 1000) -> list[tuple[float, float]]:
    return [(x, y), (x + kante, y), (x + kante, y + kante), (x, y + kante), (x, y)]


def kreis(nummer: int, name: str, ring=None, art: str = "Landkreis") -> dict:
    return {
        "id": f"{nummer:05d}",
        "name": name,
        "art": art,
        "stand": "02.07.2026",
        "ring": ring if ring is not None else quadrat(370000 + nummer * 5000, 5800000),
    }


def gml_sammlung(objekte: list[dict], anzahl_gesamt: int | str = "unknown") -> str:
    mitglieder = []
    for objekt in objekte:
        if isinstance(objekt["ring"], str):
            koordinaten = objekt["ring"]
        else:
            koordinaten = " ".join(f"{x} {y}" for x, y in objekt["ring"])
        mitglieder.append(
            f"""  <wfs:member>
    <app:kreis gml:id="{objekt.get('gml_id', 'kreis_' + objekt['id'])}">
      <gml:boundedBy><gml:Envelope srsName="urn:ogc:def:crs:EPSG::25833">
        <gml:lowerCorner>0 0</gml:lowerCorner><gml:upperCorner>1 1</gml:upperCorner>
      </gml:Envelope></gml:boundedBy>
      <app:schl>{objekt['id']}</app:schl>
      <app:name>{objekt['name']}</app:name>
      <app:art>{objekt['art']}</app:art>
      <app:stand>{objekt['stand']}</app:stand>
      <app:leer></app:leer>
      <app:geom>
        <!--Inlined geometry-->
        <gml:Polygon gml:id="GEOMETRY_{uuid.uuid4()}" srsName="urn:ogc:def:crs:EPSG::25833">
          <gml:exterior><gml:LinearRing><gml:posList>{koordinaten}</gml:posList></gml:LinearRing></gml:exterior>
        </gml:Polygon>
      </app:geom>
    </app:kreis>
  </wfs:member>"""
        )
    return (
        "<?xml version='1.0' encoding='UTF-8'?>\n"
        '<wfs:FeatureCollection xmlns:wfs="http://www.opengis.net/wfs/2.0" '
        'xmlns:gml="http://www.opengis.net/gml/3.2" xmlns:app="http://www.deegree.org/app" '
        f'numberMatched="{anzahl_gesamt}" numberReturned="0">\n' + "\n".join(mitglieder) + "\n</wfs:FeatureCollection>"
    )


AUSNAHME = """<?xml version="1.0" encoding="UTF-8"?>
<ows:ExceptionReport xmlns:ows="http://www.opengis.net/ows/1.1" version="2.0.0">
  <ows:Exception exceptionCode="InvalidParameterValue" locator="typeNames">
    <ows:ExceptionText>Unbekannter Feature-Typ app:gibtsnicht</ows:ExceptionText>
  </ows:Exception>
</ows:ExceptionReport>"""


def wfs_antwort(objekte: list[dict], params: dict[str, str], paging_ignorieren: bool = False) -> str:
    if params.get("RESULTTYPE") == "hits":
        return gml_sammlung([], anzahl_gesamt=len(objekte))
    start = 0 if paging_ignorieren else int(params.get("STARTINDEX", 0))
    anzahl = int(params["COUNT"]) if "COUNT" in params else len(objekte)
    return gml_sammlung(objekte[start : start + anzahl])


@dataclass
class LokalerDienst:
    """Zustand des lokalen Test-Dienstes; Tests ändern ihn zwischen zwei Läufen."""

    wfs_objekte: list[dict] = field(default_factory=list)
    csv_text: str = ""
    csv_kodierung: str = "cp1252"
    paging_ignorieren: bool = False
    abrufe: list[str] = field(default_factory=list)
    basis_url: str = ""

    def starten(self) -> ThreadingHTTPServer:
        dienst = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args) -> None:  # leise Tests
                pass

            def _senden(self, status: int, inhalt: bytes, typ: str) -> None:
                self.send_response(status)
                self.send_header("Content-Type", typ)
                self.send_header("Content-Length", str(len(inhalt)))
                self.end_headers()
                self.wfile.write(inhalt)

            def do_GET(self) -> None:  # noqa: N802
                url = urlparse(self.path)
                params = {k.upper(): v[0] for k, v in parse_qs(url.query).items()}
                dienst.abrufe.append(self.path)
                if url.path == "/wfs":
                    if params.get("TYPENAMES") == "app:gibtsnicht":
                        self._senden(200, AUSNAHME.encode(), "text/xml")
                        return
                    text = wfs_antwort(dienst.wfs_objekte, params, dienst.paging_ignorieren)
                    self._senden(200, text.encode("utf-8"), "text/xml; subtype=gml/3.2.1")
                elif url.path == "/orte.csv":
                    self._senden(200, dienst.csv_text.encode(dienst.csv_kodierung), "text/csv")
                elif url.path == "/kaputt":
                    self._senden(503, b"Service Unavailable", "text/plain")
                else:
                    self._senden(404, b"nicht gefunden", "text/plain")

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.basis_url = f"http://127.0.0.1:{server.server_address[1]}"
        threading.Thread(target=server.serve_forever, daemon=True).start()
        return server
