"""Die Geraetesuche fragt den Konfigurationseintrag von ZHA.

``async_get_device(identifiers=...)`` ist abgekuendigt: eine Kennung ist
ueber Konfigurationseintraege hinweg nicht mehr eindeutig, deshalb will
Core den Eintrag genannt bekommen. Der alte Aufruf endet in Home
Assistant 2027.8.

Die gesuchte Kennung gehoert ZHA und nicht dieser Ebene -- also werden
ZHAs eigene Eintraege gefragt. Beide Welten stehen hier: ein heutiger
Core mit der Suche je Eintrag, und ein aelterer ohne sie. Die
Integration nennt 2024.4 als Untergrenze.
"""

from __future__ import annotations

from types import SimpleNamespace

from custom_components.spatial_zigbee.spatial import (
    ZHA_DOMAIN,
    _geraet_zu_kennung,
)

KENNUNG = (ZHA_DOMAIN, "00:12:4b:00:1c:a1:b2:c3")


class _Hass:
    def __init__(self, *entry_ids):
        self.config_entries = SimpleNamespace(
            async_entries=lambda domain: [
                SimpleNamespace(entry_id=kennung) for kennung in entry_ids
            ]
            if domain == ZHA_DOMAIN
            else []
        )


class _NeuesRegister:
    """Kann die Suche je Eintrag -- wie Core seit 2025.9."""

    def __init__(self, treffer: dict):
        self._treffer = treffer
        self.gefragt: list[tuple] = []

    def async_get_device_by_identifier(self, kennung, entry_id):
        self.gefragt.append((kennung, entry_id))
        return self._treffer.get(entry_id)

    def async_get_device(self, identifiers=None):  # pragma: no cover
        raise AssertionError("der abgekuendigte Weg darf hier nicht laufen")


class _AltesRegister:
    """Kennt nur den alten Aufruf -- wie Core vor 2025.9."""

    def __init__(self, geraet):
        self._geraet = geraet
        self.gefragt = None

    def async_get_device(self, identifiers=None):
        self.gefragt = identifiers
        return self._geraet


def test_sucht_im_eintrag_von_zha():
    geraet = object()
    register = _NeuesRegister({"zha-1": geraet})
    assert _geraet_zu_kennung(_Hass("zha-1"), register, KENNUNG) is geraet
    assert register.gefragt == [(KENNUNG, "zha-1")]


def test_zweiter_koordinator_wird_auch_gefragt():
    geraet = object()
    register = _NeuesRegister({"zha-2": geraet})
    assert _geraet_zu_kennung(_Hass("zha-1", "zha-2"), register, KENNUNG) is geraet
    assert register.gefragt == [(KENNUNG, "zha-1"), (KENNUNG, "zha-2")]


def test_ohne_treffer_kommt_nichts():
    assert _geraet_zu_kennung(_Hass("zha-1"), _NeuesRegister({}), KENNUNG) is None


def test_rueckfall_auf_alten_core():
    geraet = object()
    register = _AltesRegister(geraet)
    assert _geraet_zu_kennung(_Hass("zha-1"), register, KENNUNG) is geraet
    assert register.gefragt == {KENNUNG}
