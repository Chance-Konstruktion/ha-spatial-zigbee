"""Der Adapter gegen ein echtes Home Assistant.

Was diese Datei prueft und was nicht -- das gehoert an den Anfang, sonst
liest sich ein gruener Lauf hier groesser, als er ist.

Warum ohne ZHA

Ohne die fremde Integration im Haus laeuft dieser Adapter durch seine
gesamte Ausfallbehandlung: kein Gateway, keine Statistik, kein Treiber.
Genau dieser Weg ist der wichtigste, den es zu sichern gibt. Wirft
``data()`` dabei eine Ausnahme, verwirft der Hub **die ganze Ebene** fuer
diesen Durchlauf -- nicht nur diesen Knoten. Und es ist der Weg, den jede
Home-Assistant-Version neu brechen kann, weil er auf fremdem Innenleben
steht.

Anders als die Suite in ``tests/`` steht hier nichts Nachgebautes: echtes
``hass``, echte Registries, echte Config Entries, echter Dispatcher. Was
hier haelt, haelt gegen die Schnittstelle, die die Integration im Haus
wirklich vorfindet.

Nicht geprueft ist der Fall mit laufender ZHA-Integration. Die
braucht Hardware -- einen Stick, ein Netz, Nachbarn -- und laesst sich in
einer CI nicht ehrlich nachstellen. Dafuer ist die Suite in ``tests/``
da, die diese Innereien nachbaut.
"""

from __future__ import annotations

import json

import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry
from spatial_hub_conformance import check

from custom_components.spatial_zigbee.spatial import async_setup_spatial

EIGENE_DOMAIN = "spatial_zigbee"


@pytest.fixture
def eintrag(hass: HomeAssistant):
    eigener = MockConfigEntry(domain=EIGENE_DOMAIN, title="Spatial Zigbee")
    eigener.add_to_hass(hass)
    return eigener


@pytest.fixture
def registrierung(hass: HomeAssistant, eintrag):
    """Der Adapter, aufgebaut gegen ein echtes Home Assistant."""
    async_setup_spatial(hass, eintrag)
    providers = hass.data.get("spatial_hub_providers") or {}
    assert EIGENE_DOMAIN in providers, (
        f"Der Adapter hat sich nicht angemeldet. Vorhanden: "
        f"{sorted(providers)}"
    )
    return providers[EIGENE_DOMAIN]


# ── Anmeldung ─────────────────────────────────────────────────────────


def test_der_adapter_meldet_sich_am_hub_an(registrierung):
    """Ohne Eintrag in hass.data existiert die Ebene fuer den Hub nicht."""
    assert registrierung["provider_id"] == EIGENE_DOMAIN
    assert registrierung["name"]
    assert callable(registrierung["data"])


def test_die_anmeldung_haelt_den_hub_vertrag_ein(registrierung):
    """Der mitgelieferte Konformitaets-Satz, gegen ein echtes hass.

    Er prueft die Dinge, die Grundrisse im Feld zerlegen: Knoten-IDs, die
    sich zwischen zwei Abfragen aendern und dabei jede gespeicherte
    Position wegwerfen; Kanten auf Knoten, die es nicht gibt; Metadaten,
    die nicht durch den Websocket passen.
    """
    probleme = check(registrierung)
    assert not probleme, "Verstoesse gegen den Hub-Vertrag:\n  " + "\n  ".join(probleme)


# ── Das Verhalten ohne die fremde Integration ─────────────────────────


def test_ohne_zha_liefert_data_eine_leere_ebene(registrierung):
    """Kein ZHA im Haus heisst leer -- nicht kaputt.

    Der teure Fehler waere eine Ausnahme: der Hub verwirft dann die ganze
    Ebene fuer diesen Durchlauf, und im Log steht ein Stapelabzug statt
    "hier ist nichts".
    """
    nutzlast = registrierung["data"]()
    if isinstance(nutzlast, list):
        nutzlast = {"nodes": nutzlast}
    assert nutzlast.get("nodes") == []


def test_data_bleibt_ueber_wiederholte_abfragen_ruhig(registrierung):
    """Der Hub fragt im Takt. Der zweite Aufruf muss so ruhig sein wie der
    erste -- ein Zustand, der beim ersten Mal angelegt und beim zweiten
    Mal falsch gelesen wird, faellt sonst erst nach Minuten auf."""
    for _ in range(3):
        registrierung["data"]()


def test_die_nutzlast_ueberlebt_den_websocket(registrierung):
    """Alles geht als JSON an den Browser.

    Ein datetime, ein set oder eine eigene Klasse in den Metadaten nimmt
    das ganze Modell mit -- fuer jeden Anbieter, nicht nur diesen.
    """
    nutzlast = registrierung["data"]()
    json.dumps(nutzlast)


# ── Der Lebenszyklus ──────────────────────────────────────────────────


def _entladehaken(eintrag) -> list:
    """Die Rueckbau-Aufgaben, die der Adapter am Config Entry hinterlegt hat.

    Home Assistant fuehrt sie in einem privaten Feld. Ein Test darf da
    hineinsehen -- er wuerde es sonst gar nicht pruefen koennen --, aber
    er soll nicht so tun, als sei das eine Zusage: heisst das Feld eines
    Tages anders, wird hier uebersprungen und nicht faelschlich gruen.
    """
    for name in ("_on_unload", "_on_unload_callbacks"):
        haken = getattr(eintrag, name, None)
        if haken is not None:
            return list(haken)
    pytest.skip(
        "Home Assistant fuehrt die Entlade-Haken nicht mehr unter "
        "_on_unload -- der Test braucht einen neuen Zugang"
    )


async def test_der_auffrischer_wird_am_entry_hinterlegt(hass, eintrag):
    """``async_track_time_interval`` laeuft hier wirklich gegen Home Assistant.

    In ``tests/`` ist es eine Attrappe, die den Aufruf bloss notiert. Ob
    die Signatur noch stimmt, sagt nur Home Assistant selbst -- und wenn
    sie es nicht mehr tut, steht der Plan still, ohne dass irgendwo etwas
    rot wird. Sichtbar ist das Ergebnis daran, dass der Adapter seinen
    Abbestellungs-Haken am Entry hinterlassen hat.
    """
    async_setup_spatial(hass, eintrag)
    assert _entladehaken(eintrag), (
        "Der Adapter hat nichts zum Rueckbau hinterlegt -- weder die "
        "Abmeldung am Hub noch den Zeitgeber"
    )


async def test_entladen_hinterlaesst_keine_geisterebene(hass, eintrag):
    """Eine entfernte Integration, deren Ebene im Plan stehen bleibt, ist
    schlimmer als eine, die nie da war: sie zeigt Zustaende von gestern.
    """
    async_setup_spatial(hass, eintrag)
    assert EIGENE_DOMAIN in hass.data["spatial_hub_providers"]

    for haken in _entladehaken(eintrag):
        ergebnis = haken()
        if ergebnis is not None and hasattr(ergebnis, "__await__"):
            await ergebnis

    assert EIGENE_DOMAIN not in (hass.data.get("spatial_hub_providers") or {}), (
        "Der Anbieter steht nach dem Entladen noch im Hub"
    )
