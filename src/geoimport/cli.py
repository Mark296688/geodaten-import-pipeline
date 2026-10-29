"""Kommandozeile: ``geoimport lauf | zeitplan | migrieren | pruefen``.

Rückgabewerte von ``lauf``: 0 = alle Quellen erfolgreich, 1 = mindestens eine
Quelle fehlgeschlagen, 2 = alle fehlgeschlagen oder Konfigurationsfehler,
3 = anderer Lauf aktiv. Damit lassen sich Cron, CI oder Monitoring direkt
anbinden.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys

from . import __version__, datenbank
from .ablauf import lauf_ausfuehren
from .fehler import GeoimportFehler, KonfigurationsFehler, LaufAktiv
from .konfiguration import lade_konfiguration

log = logging.getLogger("geoimport")

_RUECKGABE = {"ok": 0, "warnung": 0, "teilweise": 1, "fehler": 2}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="geoimport", description="Geodaten-Import WFS/CSV -> PostgreSQL/PostGIS")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument(
        "-k", "--konfiguration", default=os.environ.get("GEOIMPORT_KONFIGURATION", "config"),
        help="Konfigurationsverzeichnis oder -datei (Standard: config bzw. $GEOIMPORT_KONFIGURATION)",
    )
    parser.add_argument("--log-level", default=os.environ.get("LOG_LEVEL", "INFO"))
    befehle = parser.add_subparsers(dest="befehl", required=True)

    lauf = befehle.add_parser("lauf", help="Einen Importlauf jetzt ausführen (manueller Start)")
    lauf.add_argument("-q", "--quelle", action="append", help="Nur diese Quelle(n) importieren (mehrfach möglich)")
    lauf.add_argument("--trockenlauf", action="store_true", help="Alles prüfen und berichten, nichts speichern")

    zeitplan = befehle.add_parser("zeitplan", help="Läufe nach Cron-Ausdruck ausführen (Dauerbetrieb im Container)")
    zeitplan.add_argument("--sofort", action="store_true", help="Zusätzlich direkt beim Start einen Lauf ausführen")

    befehle.add_parser("migrieren", help="Datenmodell anlegen bzw. aktualisieren")
    befehle.add_parser("pruefen", help="Konfiguration prüfen und Quellen auflisten")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    logging.basicConfig(
        level=args.log_level.upper(),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )
    try:
        konfiguration = lade_konfiguration(args.konfiguration)
        if args.befehl == "lauf":
            bericht, dateien = lauf_ausfuehren(konfiguration, auswahl=args.quelle, trockenlauf=args.trockenlauf)
            print(dateien[1].read_text(encoding="utf-8"))
            return _RUECKGABE[bericht["status"]]
        if args.befehl == "zeitplan":
            from .zeitplan import zeitplan_starten

            zeitplan_starten(konfiguration, sofort=args.sofort)
            return 0
        if args.befehl == "migrieren":
            with datenbank.verbinden() as verbindung:
                neu = datenbank.migrieren(verbindung)
            print("Migrationen angewendet: " + (", ".join(neu) if neu else "keine (Datenmodell aktuell)"))
            return 0
        if args.befehl == "pruefen":
            print(f"Konfiguration gültig: {len(konfiguration.quellen)} Quelle(n)")
            for quelle in konfiguration.quellen:
                ziel = getattr(quelle, "layer", None) or ""
                status = "aktiv" if quelle.aktiv else "inaktiv"
                print(f"  - {quelle.id:<28} {quelle.typ.upper():<4} {status:<8} {quelle.bezeichnung} {ziel}")
            return 0
    except LaufAktiv as fehler:
        log.warning("%s", fehler)
        return 3
    except KonfigurationsFehler as fehler:
        log.error("%s", fehler)
        return 2
    except GeoimportFehler as fehler:
        log.error("%s", fehler)
        return 2
    return 2  # pragma: no cover


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
