"""Ein Importlauf über alle (oder ausgewählte) Quellen.

Fehlertoleranz: Jede Quelle läuft in einer eigenen Transaktion. Scheitert eine
Quelle (nicht erreichbar, ungültige Antwort, Datenbankfehler), wird nur ihre
Transaktion zurückgerollt, der Fehler im Bericht vermerkt und mit der nächsten
Quelle weitergemacht.
"""

from __future__ import annotations

import logging
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import psycopg

from . import bericht as berichte
from . import datenbank, laden
from .abbildung import objekte_abbilden
from .fehler import LaufAktiv
from .http import HttpClient
from .konfiguration import Konfiguration
from .quellen import QuellenErgebnis, leser_erzeugen

log = logging.getLogger(__name__)


def _jetzt() -> datetime:
    return datetime.now(timezone.utc).astimezone()


def lauf_ausfuehren(
    konfiguration: Konfiguration,
    *,
    auswahl: list[str] | None = None,
    trockenlauf: bool = False,
    dsn: str | None = None,
    http: HttpClient | None = None,
) -> tuple[dict[str, Any], tuple[Path, Path]]:
    """Führt einen Importlauf aus und gibt Bericht und Berichtsdateien zurück."""
    quellen = konfiguration.aktive_quellen(auswahl)
    lauf_id = str(uuid.uuid4())
    gestartet = _jetzt()
    uhr = time.monotonic()
    ergebnisse: list[QuellenErgebnis] = []

    with datenbank.verbinden(dsn) as verbindung:
        datenbank.migrieren(verbindung)
        if not datenbank.sperre_holen(verbindung):
            raise LaufAktiv("Ein anderer Importlauf ist aktiv - dieser Lauf wird nicht gestartet")
        try:
            datenbank.ziel_crs_pruefen(verbindung, konfiguration.pipeline.ziel_crs)
            datenbank.lauf_beginnen(verbindung, lauf_id, gestartet, trockenlauf)
            eigener_client = http is None
            client = http or HttpClient(konfiguration.pipeline.http)
            try:
                for quelle in quellen:
                    ergebnisse.append(_quelle_importieren(verbindung, client, konfiguration, quelle, lauf_id, trockenlauf))
            finally:
                if eigener_client:
                    client.__exit__()

            quellen_daten = [e.als_dict() for e in ergebnisse]
            bericht = {
                "lauf_id": lauf_id,
                "gestartet_am": gestartet.isoformat(timespec="seconds"),
                "beendet_am": _jetzt().isoformat(timespec="seconds"),
                "dauer_s": round(time.monotonic() - uhr, 2),
                "status": berichte.gesamtstatus(quellen_daten),
                "trockenlauf": trockenlauf,
                "ziel_crs": konfiguration.pipeline.ziel_crs,
                "zusammenfassung": berichte.zusammenfassen(quellen_daten),
                "quellen": quellen_daten,
            }
            datenbank.lauf_abschliessen(verbindung, lauf_id, bericht)
        finally:
            datenbank.sperre_freigeben(verbindung)

    verzeichnis = Path(konfiguration.pipeline.berichte_verzeichnis)
    if not verzeichnis.is_absolute():
        verzeichnis = Path.cwd() / verzeichnis
    dateien = berichte.schreiben(bericht, verzeichnis)
    log.info(
        "Lauf %s beendet: %s, %s neu / %s geändert / %s gelöscht, Bericht %s",
        lauf_id[:8], bericht["status"], bericht["zusammenfassung"]["neu"],
        bericht["zusammenfassung"]["geaendert"], bericht["zusammenfassung"]["geloescht"], dateien[1],
    )
    return bericht, dateien


def _quelle_importieren(
    verbindung: psycopg.Connection,
    client: HttpClient,
    konfiguration: Konfiguration,
    quelle,
    lauf_id: str,
    trockenlauf: bool,
) -> QuellenErgebnis:
    ergebnis = QuellenErgebnis(
        quelle_id=quelle.id,
        typ=quelle.typ,
        bezeichnung=quelle.bezeichnung,
        url=quelle.url if quelle.url else getattr(quelle, "pfad", None),
        layer=getattr(quelle, "layer", None),
        gestartet_am=_jetzt().isoformat(timespec="seconds"),
    )
    uhr = time.monotonic()
    log.info("Quelle %s (%s) wird importiert", quelle.id, quelle.typ)
    try:
        with verbindung.transaction():
            leser = leser_erzeugen(quelle, client, ergebnis, konfiguration.basis_verzeichnis)
            objekte = objekte_abbilden(leser.objekte(), quelle, ergebnis)
            laden.importieren(verbindung, quelle, objekte, ergebnis, lauf_id, konfiguration.pipeline)
            if trockenlauf:
                raise psycopg.Rollback()
        ergebnis.status = "warnung" if ergebnis.warnungen else "ok"
    except Exception as fehler:  # noqa: BLE001 - Fehler je Quelle abfangen ist hier gewollt
        ergebnis.status = "fehler"
        ergebnis.fehler = f"{type(fehler).__name__}: {fehler}"
        ergebnis.neu = ergebnis.geaendert = ergebnis.geloescht = ergebnis.unveraendert = 0
        log.error("Quelle %s fehlgeschlagen: %s", quelle.id, ergebnis.fehler)
        if not trockenlauf:
            try:
                laden.status_vermerken(verbindung, quelle, "fehler")
            except psycopg.Error as nachfolgefehler:  # pragma: no cover
                log.error("Status für %s nicht speicherbar: %s", quelle.id, nachfolgefehler)
    ergebnis.dauer_s = round(time.monotonic() - uhr, 2)
    return ergebnis
