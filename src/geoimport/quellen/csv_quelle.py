"""CSV-Quellen (lokale Datei oder URL) mit flexibler Spaltenzuordnung.

Unterstützt Koordinaten in zwei Spalten (x/y bzw. Länge/Breite) oder eine
WKT-Spalte, frei wählbares Trennzeichen (sonst automatisch erkannt),
Dezimalkomma, Kodierung (UTF-8 mit Rückfall auf Windows-1252) und einfache
Zeilenfilter. Zeilen ohne verwertbare Koordinate werden verworfen und im
Import-Bericht gezählt, der Lauf geht weiter.
"""

from __future__ import annotations

import csv
import io
import logging
from collections.abc import Iterator
from pathlib import Path

from ..fehler import QuellenFehler
from ..http import HttpClient
from ..konfiguration import CsvQuelle
from .basis import MAX_BEISPIELE, Objekt, QuellenErgebnis

log = logging.getLogger(__name__)

_KANDIDATEN_TRENNZEICHEN = ";,\t|"


class CsvLeser:
    def __init__(self, quelle: CsvQuelle, http: HttpClient, ergebnis: QuellenErgebnis, basis_verzeichnis: Path) -> None:
        self.quelle = quelle
        self.http = http
        self.ergebnis = ergebnis
        self.basis_verzeichnis = Path(basis_verzeichnis)

    def _laden(self) -> bytes:
        q = self.quelle
        if q.url:
            antwort = self.http.get(q.url, q.parameter or None, q.anmeldung)
            self.ergebnis.seiten += 1
            return antwort.content
        pfad = Path(q.pfad)
        if not pfad.is_absolute():
            pfad = self.basis_verzeichnis / pfad
        try:
            return pfad.read_bytes()
        except OSError as fehler:
            raise QuellenFehler(f"CSV-Datei nicht lesbar: {pfad} ({fehler.strerror})") from fehler

    def _dekodieren(self, roh: bytes) -> str:
        if self.quelle.kodierung:
            try:
                return roh.decode(self.quelle.kodierung)
            except UnicodeDecodeError as fehler:
                raise QuellenFehler(f"CSV passt nicht zur Kodierung {self.quelle.kodierung}: {fehler}") from fehler
        try:
            return roh.decode("utf-8-sig")
        except UnicodeDecodeError:
            self.ergebnis.warnen("CSV ist nicht UTF-8 - als Windows-1252 gelesen (kodierung explizit setzen)")
            return roh.decode("cp1252")

    def _trennzeichen(self, probe: str) -> str:
        if self.quelle.trennzeichen:
            return self.quelle.trennzeichen
        try:
            return csv.Sniffer().sniff(probe, delimiters=_KANDIDATEN_TRENNZEICHEN).delimiter
        except csv.Error:
            kopf = probe.splitlines()[0] if probe else ""
            return max(_KANDIDATEN_TRENNZEICHEN, key=kopf.count)

    def _zahl(self, text: str | None) -> float | None:
        if text is None:
            return None
        text = text.strip()
        if not text:
            return None
        if self.quelle.dezimaltrennzeichen == ",":
            text = text.replace(".", "").replace(",", ".") if "," in text else text
        try:
            return float(text)
        except ValueError:
            return None

    def objekte(self) -> Iterator[Objekt]:
        q = self.quelle
        text = self._dekodieren(self._laden())
        zeilen = io.StringIO(text)
        for _ in range(q.ueberspringe_zeilen):
            zeilen.readline()
        rest = zeilen.read()
        if not rest.strip():
            raise QuellenFehler("CSV ist leer")
        leser = csv.DictReader(io.StringIO(rest), delimiter=self._trennzeichen(rest[:8192]))
        spalten = [s.strip() for s in (leser.fieldnames or [])]
        leser.fieldnames = spalten

        g = q.geometrie
        geometriespalten = [s for s in (g.x, g.y, g.wkt) if s]
        benoetigt = geometriespalten + list(q.filter)
        fehlend = [s for s in benoetigt if s not in spalten]
        if fehlend:
            raise QuellenFehler(f"Spalte(n) fehlen in der CSV: {', '.join(fehlend)} (vorhanden: {', '.join(spalten)})")

        verworfen_beispiele: list[str] = []
        for nummer, zeile in enumerate(leser, start=1):
            if not self._filter_passt(zeile):
                continue
            if g.wkt:
                geom_text = (zeile.get(g.wkt) or "").strip() or None
                geom_format = "wkt"
            else:
                x, y = self._zahl(zeile.get(g.x)), self._zahl(zeile.get(g.y))
                geom_text = f"POINT({x!r} {y!r})" if x is not None and y is not None else None
                geom_format = "wkt"
            if geom_text is None:
                self.ergebnis.verworfen += 1
                if len(verworfen_beispiele) < MAX_BEISPIELE:
                    verworfen_beispiele.append(str(nummer))
                continue
            attribute = {
                spalte: (wert.strip() if isinstance(wert, str) else wert)
                for spalte, wert in zeile.items()
                if spalte and spalte not in geometriespalten
            }
            yield Objekt(f"zeile_{nummer}", geom_format, geom_text, g.crs, attribute)

        if verworfen_beispiele:
            self.ergebnis.warnen(
                f"{self.ergebnis.verworfen} Zeile(n) ohne verwertbare Koordinate verworfen "
                f"(z. B. Datenzeile {', '.join(verworfen_beispiele)})"
            )

    def _filter_passt(self, zeile: dict[str, str]) -> bool:
        for spalte, erlaubt in self.quelle.filter.items():
            wert = (zeile.get(spalte) or "").strip()
            if isinstance(erlaubt, list):
                if wert not in erlaubt:
                    return False
            elif wert != erlaubt:
                return False
        return True
