"""HTTP-Zugriff mit Wiederholungen und verständlichen Fehlermeldungen."""

from __future__ import annotations

import logging
import time
from typing import Callable

import httpx

from .fehler import QuellenFehler
from .konfiguration import Anmeldung, HttpEinstellungen

log = logging.getLogger(__name__)

_WIEDERHOLBAR = {429, 500, 502, 503, 504}


class HttpClient:
    """Dünne Hülle um ``httpx.Client``.

    Wiederholt Verbindungsfehler und vorübergehende Serverfehler mit
    exponentiell wachsender Wartezeit und übersetzt alle übrigen Fehler in
    ``QuellenFehler`` - damit scheitert höchstens die betroffene Quelle.
    """

    def __init__(
        self,
        einstellungen: HttpEinstellungen,
        transport: httpx.BaseTransport | None = None,
        schlafen: Callable[[float], None] = time.sleep,
    ) -> None:
        self._einstellungen = einstellungen
        self._schlafen = schlafen
        self._client = httpx.Client(
            timeout=einstellungen.timeout_s,
            headers={"User-Agent": einstellungen.user_agent},
            follow_redirects=True,
            transport=transport,
        )

    def __enter__(self) -> HttpClient:
        return self

    def __exit__(self, *_: object) -> None:
        self._client.close()

    def get(self, url: str, params: dict[str, str] | None = None, anmeldung: Anmeldung | None = None) -> httpx.Response:
        auth = (anmeldung.benutzer, anmeldung.passwort) if anmeldung else None
        versuche = self._einstellungen.versuche
        for versuch in range(1, versuche + 1):
            try:
                antwort = self._client.get(url, params=params, auth=auth)
            except httpx.TransportError as fehler:
                if versuch == versuche:
                    raise QuellenFehler(f"Quelle nicht erreichbar ({type(fehler).__name__}): {url}") from fehler
                self._warten(versuch, f"{type(fehler).__name__}")
                continue
            if antwort.status_code in _WIEDERHOLBAR and versuch < versuche:
                self._warten(versuch, f"HTTP {antwort.status_code}")
                continue
            if antwort.status_code >= 400:
                auszug = antwort.text[:300].replace("\n", " ").strip()
                raise QuellenFehler(f"HTTP {antwort.status_code} von {antwort.url}: {auszug}")
            return antwort
        raise AssertionError("nicht erreichbar")  # pragma: no cover

    def _warten(self, versuch: int, grund: str) -> None:
        dauer = min(self._einstellungen.wartezeit_s * 2 ** (versuch - 1), 60)
        log.warning("Versuch %s fehlgeschlagen (%s), neuer Versuch in %.1f s", versuch, grund, dauer)
        self._schlafen(dauer)
