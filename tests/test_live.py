"""Abruf gegen die echten Brandenburger Dienste - nur manuell: ``pytest -m live``."""

from pathlib import Path

import pytest

from geoimport.http import HttpClient
from geoimport.konfiguration import lade_konfiguration
from geoimport.quellen import QuellenErgebnis, leser_erzeugen

pytestmark = pytest.mark.live

BEISPIEL = Path(__file__).resolve().parents[1] / "config"


@pytest.mark.parametrize("quelle_id", ["bb_kreise", "bb_gemeinden"])
def test_wfs_brandenburg(quelle_id):
    konfiguration = lade_konfiguration(BEISPIEL)
    quelle = next(q for q in konfiguration.quellen if q.id == quelle_id)
    quelle.max_objekte = 15
    ergebnis = QuellenErgebnis(quelle.id, quelle.typ, quelle.bezeichnung)
    with HttpClient(konfiguration.pipeline.http) as http:
        objekte = list(leser_erzeugen(quelle, http, ergebnis, BEISPIEL).objekte())
    assert len(objekte) == 15
    assert ergebnis.seiten == 2 if quelle.seitengroesse == 10 else ergebnis.seiten >= 1
    assert all(o.geom_text and "srsName" in o.geom_text for o in objekte)
    assert all(o.attribute.get(quelle.id_feld) for o in objekte)
