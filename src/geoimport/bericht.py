"""Import-Bericht je Lauf: maschinenlesbar (JSON) und als lesbare Zusammenfassung (Markdown)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

_STATUS_TEXT = {
    "ok": "erfolgreich",
    "warnung": "erfolgreich mit Warnungen",
    "teilweise": "teilweise erfolgreich",
    "fehler": "fehlgeschlagen",
}


def gesamtstatus(quellen: list[dict[str, Any]]) -> str:
    if not quellen:
        return "ok"
    fehler = sum(1 for q in quellen if q["status"] == "fehler")
    if fehler == len(quellen):
        return "fehler"
    if fehler:
        return "teilweise"
    return "warnung" if any(q["warnungen"] for q in quellen) else "ok"


def zusammenfassen(quellen: list[dict[str, Any]]) -> dict[str, int]:
    summe = {"quellen": len(quellen), "erfolgreich": 0, "fehlgeschlagen": 0}
    for schluessel in ("abgerufen", "neu", "geaendert", "geloescht", "unveraendert", "verworfen", "repariert"):
        summe[schluessel] = sum(q[schluessel] for q in quellen)
    for q in quellen:
        summe["fehlgeschlagen" if q["status"] == "fehler" else "erfolgreich"] += 1
    return summe


def _zahl(wert: float) -> str:
    return f"{wert:.1f}".replace(".", ",")


def als_markdown(bericht: dict[str, Any]) -> str:
    s = bericht["zusammenfassung"]
    zeilen = [
        f"# Import-Bericht {bericht['gestartet_am'][:19].replace('T', ' ')}",
        "",
        f"- **Status:** {_STATUS_TEXT.get(bericht['status'], bericht['status'])}",
        f"- **Lauf-ID:** `{bericht['lauf_id']}`" + (" (Trockenlauf, nichts gespeichert)" if bericht["trockenlauf"] else ""),
        f"- **Dauer:** {_zahl(bericht['dauer_s'])} s",
        f"- **Quellen:** {s['quellen']} ({s['erfolgreich']} erfolgreich, {s['fehlgeschlagen']} fehlgeschlagen)",
        f"- **Datensätze:** {s['neu']} neu, {s['geaendert']} geändert, {s['geloescht']} gelöscht, "
        f"{s['unveraendert']} unverändert, {s['verworfen']} verworfen",
        f"- **Ziel-CRS:** EPSG:{bericht['ziel_crs']}",
        "",
        "## Quellen",
        "",
        "| Quelle | Typ | Status | abgerufen | neu | geändert | gelöscht | unverändert | verworfen | Dauer (s) |",
        "|---|---|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for q in bericht["quellen"]:
        zeilen.append(
            f"| {q['quelle_id']} | {q['typ'].upper()} | {q['status']} | {q['abgerufen']} | {q['neu']} | "
            f"{q['geaendert']} | {q['geloescht']} | {q['unveraendert']} | {q['verworfen']} | {_zahl(q['dauer_s'])} |"
        )

    fehlgeschlagen = [q for q in bericht["quellen"] if q["fehler"]]
    if fehlgeschlagen:
        zeilen += ["", "## Nicht erreichbare oder fehlgeschlagene Quellen", ""]
        zeilen += [f"- **{q['quelle_id']}** ({q['url'] or '-'}): {q['fehler']}" for q in fehlgeschlagen]

    mit_warnung = [q for q in bericht["quellen"] if q["warnungen"]]
    if mit_warnung:
        zeilen += ["", "## Warnungen", ""]
        for q in mit_warnung:
            zeilen += [f"- **{q['quelle_id']}:** {w}" for w in q["warnungen"]]

    mit_hinweis = [q for q in bericht["quellen"] if q.get("hinweise")]
    if mit_hinweis:
        zeilen += ["", "## Hinweise", ""]
        for q in mit_hinweis:
            zeilen += [f"- **{q['quelle_id']}:** {h}" for h in q["hinweise"]]
    zeilen.append("")
    return "\n".join(zeilen)


def schreiben(bericht: dict[str, Any], verzeichnis: str | Path) -> tuple[Path, Path]:
    """Schreibt JSON und Markdown; ``letzter_bericht.*`` zeigt immer auf den jüngsten Lauf."""
    verzeichnis = Path(verzeichnis)
    verzeichnis.mkdir(parents=True, exist_ok=True)
    stamm = f"{bericht['gestartet_am'][:19].replace(':', '-')}_{bericht['lauf_id'][:8]}"
    json_text = json.dumps(bericht, ensure_ascii=False, indent=2)
    markdown = als_markdown(bericht)
    json_pfad = verzeichnis / f"{stamm}.json"
    md_pfad = verzeichnis / f"{stamm}.md"
    json_pfad.write_text(json_text, encoding="utf-8")
    md_pfad.write_text(markdown, encoding="utf-8")
    (verzeichnis / "letzter_bericht.json").write_text(json_text, encoding="utf-8")
    (verzeichnis / "letzter_bericht.md").write_text(markdown, encoding="utf-8")
    return json_pfad, md_pfad
