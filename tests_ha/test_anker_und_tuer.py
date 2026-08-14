"""Anker und Entitaets-Verknuepfung, gegen echte Register.

Die Suite nebenan prueft bewusst den Weg **ohne** ZHA -- den Ausfallpfad,
der jede Home-Assistant-Version neu brechen kann. Damit bleibt der Weg
*mit* Gateway ungeprueft, und genau dort sitzt das Neue: die Entitaet, die
einen Punkt zur Tuer nach Home Assistant macht, und die Anker, aus denen
der Hub einen Ort rechnet.

Nachgestellt ist hier deshalb nur das Innenleben von ZHA -- ein paar
Objekte mit den Attributen, die zigpy hat. Alles andere ist echt: echtes
``hass``, echtes Geraete- und Entitaetsregister, echte Bereiche. Die
Verknuepfung Adresse -> Geraet -> Bereich -> Entitaet laeuft also durch
denselben Code wie im Haus, und nur die Funkdaten sind gestellt.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import (
    area_registry as ar,
    device_registry as dr,
    entity_registry as er,
)
from pytest_homeassistant_custom_component.common import MockConfigEntry

from spatial_hub_conformance import SpatialHubConformance

from custom_components.spatial_zigbee.spatial import _from_gateway, _gewicht

ZHA = "zha"


def _zha_geraet(ieee: str, rolle: str, nachbarn=(), **rest):
    """Ein zigpy-artiges Geraet -- nur die Felder, die der Adapter liest."""
    return SimpleNamespace(
        ieee=ieee,
        name=rest.pop("name", f"Geraet {ieee}"),
        device_type=SimpleNamespace(name=rolle),
        available=rest.pop("available", True),
        neighbors=[
            SimpleNamespace(ieee=adresse, lqi=lqi) for adresse, lqi in nachbarn
        ],
        **rest,
    )


@pytest.fixture
def haus(hass: HomeAssistant):
    """Zwei Raeume, zwei eingetragene Zigbee-Geraete, eines ohne Raum."""
    bereiche = ar.async_get(hass)
    kueche = bereiche.async_create("Küche")
    keller = bereiche.async_create("Keller")

    zha_eintrag = MockConfigEntry(domain=ZHA, title="ZHA")
    zha_eintrag.add_to_hass(hass)
    geraete = dr.async_get(hass)
    entitaeten = er.async_get(hass)

    angelegt = {}
    for ieee, bereich, name in (
        ("00:11:22:33:44:55:66:01", kueche.id, "Küchenlampe"),
        ("00:11:22:33:44:55:66:02", keller.id, "Kellerlampe"),
    ):
        geraet = geraete.async_get_or_create(
            config_entry_id=zha_eintrag.entry_id,
            identifiers={(ZHA, ieee)},
            name=name,
        )
        geraete.async_update_device(geraet.id, area_id=bereich)
        # Eine Diagnose-Entitaet zuerst anlegen, damit belegt ist, dass sie
        # NICHT gewaehlt wird: wer auf eine Lampe tippt, will Licht.
        entitaeten.async_get_or_create(
            "sensor", ZHA, f"{ieee}-lqi", device_id=geraet.id,
            original_name=f"{name} Verbindung",
            entity_category=er.EntityCategory.DIAGNOSTIC,
            suggested_object_id=f"{name.lower()}_lqi",
        )
        licht = entitaeten.async_get_or_create(
            "light", ZHA, ieee, device_id=geraet.id, original_name=name,
            suggested_object_id=name.lower(),
        )
        angelegt[ieee] = licht.entity_id

    return {"kueche": kueche, "keller": keller, "entitaeten": angelegt}


def _gateway(*geraete):
    return SimpleNamespace(gateway=SimpleNamespace(
        devices={g.ieee: g for g in geraete}))


def test_knoten_bekommt_bereich_und_entitaet_aus_dem_register(
    hass: HomeAssistant, haus
) -> None:
    """Die Tuer nach Home Assistant.

    Ohne ``entity_id`` ist ein Zigbee-Punkt eine Sackgasse: kein Klick zum
    Geraet, keine Entitaetenliste im Aufklapper -- und der Hub kann nichts
    ergaenzen, weil seine Anreicherung genau daran haengt.
    """
    ergebnis = _from_gateway(hass, _gateway(
        _zha_geraet("00:11:22:33:44:55:66:01", "Router"),
    ).gateway)
    knoten = {k["id"]: k for k in ergebnis["nodes"]}
    lampe = knoten["node-00:11:22:33:44:55:66:01"]

    assert lampe["area_id"] == haus["kueche"].id
    assert lampe["entity_id"] == haus["entitaeten"]["00:11:22:33:44:55:66:01"]
    # Die Diagnose-Entitaet steht hinten an.
    assert "lqi" not in lampe["entity_id"]


def test_geraet_ohne_raum_wird_an_seine_nachbarn_verankert(
    hass: HomeAssistant, haus
) -> None:
    """Der eigentliche Gewinn: aus der Nachbartabelle wird ein Ort.

    Ein frisch angelerntes Geraet hat keinen Raum -- niemand traegt einen
    ein, bevor er weiss, wo das Ding landet. Seine Nachbartabelle sagt es
    trotzdem: die Kuechenlampe hoert es mit LQI 240, die Kellerlampe mit
    60. Damit gehoert es in die Kueche, und der Hub rechnet das aus den
    Ankern.
    """
    neu = "00:11:22:33:44:55:66:09"
    ergebnis = _from_gateway(hass, _gateway(
        _zha_geraet("00:11:22:33:44:55:66:01", "Router",
                    nachbarn=[(neu, 240)]),
        _zha_geraet("00:11:22:33:44:55:66:02", "Router",
                    nachbarn=[(neu, 60)]),
        _zha_geraet(neu, "Endgerät", nachbarn=[
            ("00:11:22:33:44:55:66:01", 240),
            ("00:11:22:33:44:55:66:02", 60),
        ]),
    ).gateway)
    knoten = {k["id"]: k for k in ergebnis["nodes"]}
    frisch = knoten[f"node-{neu}"]

    # Das Shim laesst leere Felder weg -- ein fehlender Schluessel ist
    # hier die Aussage "kein Bereich", nicht ein Fehler.
    assert not frisch.get("area_id"), "das Geraet hat wirklich keinen Raum"
    anker = {a["id"]: a["weight"] for a in frisch["anchors"]}
    assert set(anker) == {
        "node-00:11:22:33:44:55:66:01",
        "node-00:11:22:33:44:55:66:02",
    }
    # Die Kueche zieht deutlich staerker -- sonst waere die Messung
    # weggeworfen und der Punkt landete zwischen den Raeumen.
    assert (anker["node-00:11:22:33:44:55:66:01"]
            > 4 * anker["node-00:11:22:33:44:55:66:02"])


def test_wer_einen_raum_hat_bekommt_keine_anker(
    hass: HomeAssistant, haus
) -> None:
    """Eine Eintragung wird nicht durch Funk ersetzt.

    Sonst wandert ein fest verbautes Geraet jede Nacht durchs Haus, weil
    die Verbindungsguete schwankt. Der Hub wuerde die Anker ohnehin nicht
    gegen einen gesetzten Bereich verwenden -- sie erst gar nicht zu
    senden spart die Uebertragung und macht die Absicht sichtbar.
    """
    ergebnis = _from_gateway(hass, _gateway(
        _zha_geraet("00:11:22:33:44:55:66:01", "Router",
                    nachbarn=[("00:11:22:33:44:55:66:02", 250)]),
        _zha_geraet("00:11:22:33:44:55:66:02", "Router",
                    nachbarn=[("00:11:22:33:44:55:66:01", 250)]),
    ).gateway)

    for knoten in ergebnis["nodes"]:
        assert not knoten.get("anchors"), f"{knoten['id']} hat unnoetige Anker"


def test_nur_verortete_nachbarn_taugen_als_anker(
    hass: HomeAssistant, haus
) -> None:
    """Ein Anker auf etwas selbst nur Geratenes verteilt eine Vermutung.

    Zwei frische Geraete, die sich gegenseitig hoeren, wissen zusammen
    nichts ueber ihren Ort. Nur der Bezug auf ein Geraet mit Raum traegt
    eine Aussage.
    """
    a, b = "00:11:22:33:44:55:66:0a", "00:11:22:33:44:55:66:0b"
    ergebnis = _from_gateway(hass, _gateway(
        _zha_geraet("00:11:22:33:44:55:66:01", "Router", nachbarn=[(a, 200)]),
        _zha_geraet(a, "Endgerät", nachbarn=[
            ("00:11:22:33:44:55:66:01", 200), (b, 250)]),
        _zha_geraet(b, "Endgerät", nachbarn=[(a, 250)]),
    ).gateway)
    knoten = {k["id"]: k for k in ergebnis["nodes"]}

    # a haengt an der verorteten Kuechenlampe, nicht an b.
    assert [x["id"] for x in knoten[f"node-{a}"]["anchors"]] == [
        "node-00:11:22:33:44:55:66:01"]
    # b hoert nur a, und a liegt selbst nirgends -- also kein Anker.
    assert not knoten[f"node-{b}"].get("anchors")


@pytest.mark.parametrize(
    "lqi, erwartet",
    [(255, 1.0), (0, 0.01), (-5, 0.01), ("weg", 0.01), (None, 0.01)],
)
def test_gewicht_bleibt_im_rahmen(lqi, erwartet) -> None:
    """Kein Gewicht darf null, negativ oder unsinnig gross werden.

    Der Hub verwirft solche Werte zwar, aber ein Anbieter, der sie sendet,
    hat schon vorher etwas falsch gerechnet.
    """
    assert _gewicht(lqi) == pytest.approx(erwartet, abs=0.001)


def test_gewicht_waechst_ueberproportional() -> None:
    """Doppelte Guete muss mehr als doppelt so stark ziehen.

    Linear gewichtet zoege ein Nachbar zwei Waende weiter fast so stark
    wie der im selben Raum, und der Punkt landete auf dem Flur.
    """
    assert _gewicht(200) / _gewicht(100) == pytest.approx(4.0, abs=0.1)


# ── Der volle Konformitaetssatz, gegen eine NICHT leere Nutzlast ──────


class TestKonformitaetMitGateway(SpatialHubConformance):
    """Der Satz aus dem SDK, an einem Mesh mit Inhalt.

    Die uebrige Suite prueft den Weg ohne ZHA, und eine leere Nutzlast
    erfuellt jeden Vertrag muehelos -- sie beweist nichts. Erst hier
    laufen die Pruefungen gegen echte Knoten, echte Kanten und echte
    Anker: stabile Kennungen ueber zwei Abrufe, Metadaten, die sich ueber
    den Websocket senden lassen, keine Kante ins Leere, und Ankergewichte,
    die positiv und endlich sind.

    Wenn diese Klasse faellt, ist der Adapter vom Vertrag abgewichen --
    nicht der Vertrag vom Adapter.
    """

    @pytest.fixture(autouse=True)
    def _haus_und_gateway(self, hass, haus):
        """Home Assistant und das Mesh, bevor der Satz die Anmeldung holt."""
        self._hass = hass
        yield

    def build_registration(self):
        from custom_components.spatial_zigbee.spatial import async_setup_spatial

        eintrag = MockConfigEntry(domain="spatial_zigbee", title="Spatial Zigbee")
        eintrag.add_to_hass(self._hass)
        neu = "00:11:22:33:44:55:66:09"
        self._hass.data[ZHA] = _gateway(
            _zha_geraet("00:11:22:33:44:55:66:01", "Koordinator",
                        nachbarn=[("00:11:22:33:44:55:66:02", 220), (neu, 240)]),
            _zha_geraet("00:11:22:33:44:55:66:02", "Router",
                        nachbarn=[("00:11:22:33:44:55:66:01", 220), (neu, 60)]),
            _zha_geraet(neu, "Endgerät", nachbarn=[
                ("00:11:22:33:44:55:66:01", 240),
                ("00:11:22:33:44:55:66:02", 60),
            ]),
        )
        async_setup_spatial(self._hass, eintrag)
        return self._hass.data["spatial_hub_providers"]["spatial_zigbee"]
