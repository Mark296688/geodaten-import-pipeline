"""Zeitgesteuerter Dauerbetrieb (Cron-Ausdruck aus der Konfiguration bzw. GEOIMPORT_ZEITPLAN)."""

from __future__ import annotations

import logging
from datetime import datetime

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from .ablauf import lauf_ausfuehren
from .fehler import GeoimportFehler, KonfigurationsFehler
from .konfiguration import Konfiguration

log = logging.getLogger(__name__)


def _geplanter_lauf(konfiguration: Konfiguration) -> None:
    try:
        lauf_ausfuehren(konfiguration)
    except GeoimportFehler as fehler:
        log.error("Geplanter Lauf nicht ausgeführt: %s", fehler)
    except Exception:  # noqa: BLE001 - der Planer muss weiterlaufen
        log.exception("Geplanter Lauf mit unerwartetem Fehler abgebrochen")


def zeitplan_starten(konfiguration: Konfiguration, *, sofort: bool = False) -> None:
    einstellungen = konfiguration.pipeline
    try:
        ausloeser = CronTrigger.from_crontab(einstellungen.zeitplan, timezone=einstellungen.zeitzone)
    except ValueError as fehler:
        raise KonfigurationsFehler(f"Ungültiger Zeitplan {einstellungen.zeitplan!r}: {fehler}") from fehler
    planer = BlockingScheduler(timezone=einstellungen.zeitzone)
    # max_instances=1 und coalesce: verpasste oder überlappende Termine führen zu genau einem Lauf
    planer.add_job(_geplanter_lauf, ausloeser, args=[konfiguration], id="import",
                   max_instances=1, coalesce=True, misfire_grace_time=3600)
    if sofort:
        _geplanter_lauf(konfiguration)
    log.info("Zeitplan aktiv: %r (%s), nächster Lauf %s", einstellungen.zeitplan, einstellungen.zeitzone,
             ausloeser.get_next_fire_time(None, datetime.now(ausloeser.timezone)))
    try:
        planer.start()
    except (KeyboardInterrupt, SystemExit):
        log.info("Zeitplan beendet")
