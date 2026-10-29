"""Abbildung der Quellattribute auf das homogenisierte Zielschema.

Unterschiedliche Quellen nennen dasselbe anders (``krs_name``, ``Gebietsname``,
``name``). Über ``attribute.umbenennen`` landen sie im Zielmodell unter einem
gemeinsamen Schlüssel; ``typen`` sorgt für einheitliche Datentypen
(z. B. ``01.10.1990`` -> ``1990-10-01``).
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Iterator
from dataclasses import replace
from datetime import date
from typing import Any

from .konfiguration import AttributAbbildung, CsvQuelle, WfsQuelle
from .quellen.basis import MAX_BEISPIELE, Objekt, QuellenErgebnis

_WAHR = {"true", "wahr", "ja", "j", "yes", "y", "1", "x"}
_FALSCH = {"false", "falsch", "nein", "n", "no", "0", ""}
_DATUM_DE = re.compile(r"^(\d{1,2})\.(\d{1,2})\.(\d{4})$")


class Umwandlungsfehler(ValueError):
    pass


def umwandeln(wert: Any, typ: str) -> Any:
    """Wandelt einen Attributwert in den Zieltyp um."""
    if wert is None:
        return None
    text = str(wert).strip()
    if typ == "text":
        return text
    if typ in {"zahl", "ganzzahl"}:
        normiert = text.replace(" ", "")
        if "," in normiert:
            normiert = normiert.replace(".", "").replace(",", ".")
        try:
            zahl = float(normiert)
        except ValueError as fehler:
            raise Umwandlungsfehler(f"{text!r} ist keine Zahl") from fehler
        if typ == "ganzzahl":
            if not zahl.is_integer():
                raise Umwandlungsfehler(f"{text!r} ist keine ganze Zahl")
            return int(zahl)
        return zahl
    if typ == "datum":
        treffer = _DATUM_DE.match(text)
        try:
            if treffer:
                tag, monat, jahr = (int(t) for t in treffer.groups())
                return date(jahr, monat, tag).isoformat()
            return date.fromisoformat(text[:10]).isoformat()
        except ValueError as fehler:
            raise Umwandlungsfehler(f"{text!r} ist kein Datum") from fehler
    if typ == "bool":
        if text.lower() in _WAHR:
            return True
        if text.lower() in _FALSCH:
            return False
        raise Umwandlungsfehler(f"{text!r} ist kein Wahrheitswert")
    raise Umwandlungsfehler(f"Unbekannter Typ {typ}")  # pragma: no cover


def attribute_abbilden(
    attribute: dict[str, Any], abbildung: AttributAbbildung, fehler: list[str] | None = None
) -> dict[str, Any]:
    """Wendet nur/ausschliessen/umbenennen/konstanten/typen an; leere Werte entfallen."""
    if abbildung.nur is not None:
        attribute = {k: attribute[k] for k in abbildung.nur if k in attribute}
    ergebnis: dict[str, Any] = {}
    for schluessel, wert in attribute.items():
        if schluessel in abbildung.ausschliessen or wert is None or wert == "":
            continue
        ergebnis[abbildung.umbenennen.get(schluessel, schluessel)] = wert
    ergebnis.update(abbildung.konstanten)
    for schluessel, typ in abbildung.typen.items():
        if schluessel not in ergebnis:
            continue
        try:
            ergebnis[schluessel] = umwandeln(ergebnis[schluessel], typ)
        except Umwandlungsfehler as fehlermeldung:
            if fehler is not None:
                fehler.append(f"{schluessel}: {fehlermeldung}")
            ergebnis[schluessel] = str(ergebnis[schluessel])
    return ergebnis


def objekt_id_bestimmen(objekt: Objekt, id_feld: str | list[str] | None) -> str | None:
    if id_feld is None:
        return None
    felder = [id_feld] if isinstance(id_feld, str) else id_feld
    teile = [objekt.attribute.get(feld) for feld in felder]
    if any(teil in (None, "") for teil in teile):
        return None
    return "/".join(str(teil) for teil in teile)


def objekte_abbilden(
    objekte: Iterable[Objekt], quelle: WfsQuelle | CsvQuelle, ergebnis: QuellenErgebnis
) -> Iterator[Objekt]:
    """Bestimmt die Objekt-ID, bildet Attribute ab und sammelt das Quellschema."""
    schema: set[str] = set()
    ohne_id = 0
    typfehler: list[str] = []
    typfehler_anzahl = 0

    for objekt in objekte:
        ergebnis.abgerufen += 1
        schema.update(objekt.attribute)
        objekt_id = objekt_id_bestimmen(objekt, quelle.id_feld)
        if objekt_id is None:
            objekt_id = objekt.ersatz_id
            if quelle.id_feld is not None:
                ohne_id += 1
        fehler_objekt: list[str] = []
        attribute = attribute_abbilden(objekt.attribute, quelle.attribute, fehler_objekt)
        if fehler_objekt:
            typfehler_anzahl += len(fehler_objekt)
            typfehler.extend(fehler_objekt[: max(0, MAX_BEISPIELE - len(typfehler))])
        yield replace(objekt, objekt_id=objekt_id, attribute=attribute)

    ergebnis.attributschema = sorted(schema)
    if quelle.id_feld is None:
        ergebnis.warnen(
            "Kein id_feld konfiguriert - Ersatz-ID (gml:id bzw. Zeilennummer) verwendet; "
            "Änderungserkennung nur verlässlich, wenn diese stabil ist"
        )
    elif ohne_id:
        ergebnis.warnen(f"{ohne_id} Objekt(e) ohne Wert im id_feld - Ersatz-ID verwendet")
    if quelle.attribute.nur:
        fehlend = sorted(set(quelle.attribute.nur) - schema)
        if fehlend and ergebnis.abgerufen:
            ergebnis.warnen(f"Konfigurierte Attribute fehlen in der Quelle: {', '.join(fehlend)}")
    if typfehler:
        ergebnis.warnen(
            f"{typfehler_anzahl} Typumwandlung(en) fehlgeschlagen, Wert als Text übernommen "
            f"(z. B. {'; '.join(typfehler)})"
        )
