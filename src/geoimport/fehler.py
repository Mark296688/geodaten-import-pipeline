"""Fehlerklassen der Pipeline."""


class GeoimportFehler(Exception):
    """Basisklasse aller fachlichen Fehler."""


class KonfigurationsFehler(GeoimportFehler):
    """Die Konfiguration ist unvollständig oder ungültig."""


class QuellenFehler(GeoimportFehler):
    """Eine Quelle ist nicht erreichbar oder liefert unbrauchbare Daten.

    Betrifft immer nur die eine Quelle: Die Pipeline protokolliert den Fehler
    im Import-Bericht und fährt mit der nächsten Quelle fort.
    """


class LaufAktiv(GeoimportFehler):
    """Ein anderer Importlauf hält gerade die Sperre."""
