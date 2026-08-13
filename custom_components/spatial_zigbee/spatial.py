"""The Zigbee mesh on the floor plan.

Zigbee is a real mesh and it knows it. Every router keeps a neighbour
table -- who it can hear, how well, and whether that neighbour is its
parent or its child -- and that table is the actual shape of the network.
It is not in the device registry and nothing in Home Assistant draws it.

Two sources, in order of preference, exactly as the Z-Wave adapter does:

1. The running ZHA gateway, which holds every device with its neighbour
   table, LQI and RSSI. This is ZHA's internals, it has moved between
   releases more than once, and every read here is therefore defensive.
2. The config entries and the device registry, which are public, stable
   and always there. No neighbour table, but every Zigbee device in its
   room around a coordinator.

If (1) is not shaped the way this expects, the layer falls back to (2) and
says so in its metadata rather than vanishing. A floor plan that loses its
Zigbee layer after a Home Assistant update is worse than one that loses
the LQI numbers.

Which devices are Zigbee is asked via the **config entries**, never via
``identifiers``. An integration is free to identify its devices however it
likes -- some use no identifier at all -- and the config entry a device was
created under is the one thing that is always true.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.event import async_track_time_interval

from .spatial_hub_provider import edge, node, spatial_provider

_LOGGER = logging.getLogger(__name__)

ZHA_DOMAIN = "zha"
COORDINATOR_ID = "coordinator"

# Zigbee neighbour tables are refreshed slowly by the network itself, so
# asking faster than this would only redraw the same picture.
REFRESH = timedelta(seconds=60)

# LQI is a 0-255 link quality indicator, not a dBm. 255 is "in the same
# room", below 100 is a link that works until somebody shuts a door.
_GOOD = 200
_FAIR = 100


def _quality(lqi: Any) -> str:
    """One of the four words every provider speaks, from an LQI reading."""
    try:
        value = float(lqi)
    except (TypeError, ValueError):
        return "unknown"
    if value >= _GOOD:
        return "good"
    if value >= _FAIR:
        return "fair"
    return "poor"


def _attr(source: Any, *names: str) -> Any:
    """The first of these names that exists, attribute or key.

    ZHA and zigpy have renamed these fields across releases and expose
    some of them as objects and some as dicts. Asking for every spelling
    is cheaper than pinning a version.
    """
    for name in names:
        value = getattr(source, name, None)
        if value is None and isinstance(source, dict):
            value = source.get(name)
        if value is not None:
            return value
    return None


def _gateway(hass: HomeAssistant) -> Any:
    """The running ZHA gateway, or None if anything is not as expected.

    Deliberately paranoid: this reaches into another integration's runtime
    data, which is not a contract anybody promised us. ZHA has stored this
    as a bare dict, as an object with a gateway attribute, and latterly
    behind a proxy -- so all three are tried and none is required.
    """
    try:
        data = hass.data.get(ZHA_DOMAIN)
        if data is None:
            return None
        candidates = [data]
        if isinstance(data, dict):
            candidates.extend(data.values())
        for candidate in candidates:
            proxy = _attr(candidate, "gateway_proxy")
            for holder in (proxy, candidate):
                gateway = _attr(holder, "gateway", "zha_gateway")
                if gateway is not None and _attr(gateway, "devices") is not None:
                    return gateway
            if _attr(candidate, "devices") is not None and _attr(
                candidate, "application_controller", "coordinator_zha_device"
            ):
                return candidate
    except Exception:  # noqa: BLE001 - never take the layer down with it
        _LOGGER.debug("ZHA internals not readable", exc_info=True)
    return None


def _ieee(value: Any) -> str:
    """A Zigbee address as one stable string, whatever type it arrived as."""
    return str(value or "").strip().lower()


def _area_of(hass: HomeAssistant, ieee: str) -> str | None:
    """Where the user already put this node's device.

    ZHA identifies a device as ``("zha", str(ieee))``. Reading it here is
    a lookup, not a filter -- a miss costs an area, not the node, which is
    what makes it safe to depend on the format at all.
    """
    if not ieee:
        return None
    try:
        registry = dr.async_get(hass)
    except (AttributeError, KeyError):  # pragma: no cover
        return None
    device = registry.async_get_device(identifiers={(ZHA_DOMAIN, ieee)})
    return device.area_id if device else None


# What a device is *for* in the mesh, which is the thing a mesh map exists
# to show: routers are the backbone, end devices hang off it.
_ROLES = {
    "coordinator": "Koordinator",
    "router": "Router",
    "enddevice": "Endgerät",
    "end_device": "Endgerät",
}


def _role(device: Any) -> str:
    raw = _attr(device, "device_type", "logical_type")
    name = str(getattr(raw, "name", raw) or "").strip().lower().replace(" ", "")
    return _ROLES.get(name, "unbekannt")


def _neighbours(device: Any) -> list[tuple[str, Any]]:
    """Who this device can hear, from its own neighbour table.

    zigpy has spelled the neighbour's address ``ieee`` and, when the entry
    wraps a device object, ``device.ieee``. Both are asked for; an entry
    that yields neither is skipped rather than guessed at.
    """
    table = _attr(device, "neighbors", "neighbours")
    if not isinstance(table, (list, tuple)):
        return []
    found: list[tuple[str, Any]] = []
    for neighbour in table:
        ieee = _attr(neighbour, "ieee") or _attr(
            _attr(neighbour, "device"), "ieee"
        )
        if not ieee:
            continue
        found.append((_ieee(ieee), _attr(neighbour, "lqi")))
    return found


def _from_gateway(hass: HomeAssistant, gateway: Any) -> dict[str, list]:
    devices = _attr(gateway, "devices") or {}
    if isinstance(devices, dict):
        devices = list(devices.values())

    nodes = []
    edges = []
    known: set[str] = set()
    coordinator: str | None = None

    for zha_device in devices:
        ieee = _ieee(_attr(zha_device, "ieee"))
        if not ieee:
            continue
        known.add(ieee)
        role = _role(zha_device)
        if role == "Koordinator":
            coordinator = ieee
        available = bool(_attr(zha_device, "available") or False)
        # A battery end device that is asleep is not broken, and painting
        # half a house red every night is the mistake this avoids.
        state = "online" if available else (
            "asleep" if role == "Endgerät" else "offline"
        )
        nodes.append(
            node(
                f"node-{ieee}",
                label=str(
                    _attr(zha_device, "user_given_name", "name") or f"Zigbee {ieee}"
                ),
                area_id=_area_of(hass, ieee),
                state=state,
                icon="mdi:zigbee" if state == "online" else "mdi:sleep",
                rolle=role,
                ieee=ieee,
                nwk=_attr(zha_device, "nwk"),
                hersteller=_attr(zha_device, "manufacturer") or "",
                modell=_attr(zha_device, "model") or "",
                lqi=_attr(zha_device, "lqi"),
                rssi=_attr(zha_device, "rssi"),
                stromquelle=_attr(zha_device, "power_source") or "",
                quelle="gateway",
            )
        )

    # Neighbour tables are symmetric in practice -- A lists B and B lists A
    # -- so the same link arrives twice with two LQI readings. Keep one
    # line per pair and the better of the two readings: the link is as good
    # as its best direction, and stacking two lines only thickens the pixel.
    links: dict[tuple[str, str], Any] = {}
    for zha_device in devices:
        ieee = _ieee(_attr(zha_device, "ieee"))
        if not ieee:
            continue
        for neighbour, lqi in _neighbours(zha_device):
            if neighbour not in known or neighbour == ieee:
                continue
            key = tuple(sorted((ieee, neighbour)))
            best = links.get(key)
            try:
                better = best is None or float(lqi) > float(best)
            except (TypeError, ValueError):
                better = best is None
            if better:
                links[key] = lqi

    for (left, right), lqi in links.items():
        edges.append(
            edge(
                f"node-{left}",
                f"node-{right}",
                value=lqi,
                quality=_quality(lqi),
                lqi=lqi,
            )
        )

    # A mesh with no neighbour tables yet -- freshly paired, or a
    # coordinator that has not been asked -- would draw as loose dots.
    # Fall back to the star so there is still a picture.
    if not links and coordinator:
        for ieee in known:
            if ieee == coordinator:
                continue
            edges.append(
                edge(
                    f"node-{coordinator}",
                    f"node-{ieee}",
                    quality="unknown",
                    # Dashed, because "reachable" must never read as
                    # "wired like this".
                    dashed=True,
                )
            )

    return {"nodes": nodes, "edges": edges}


def _zha_devices(hass: HomeAssistant) -> list[Any]:
    """Every device Home Assistant created under a ZHA config entry."""
    try:
        registry = dr.async_get(hass)
    except (AttributeError, KeyError):  # pragma: no cover
        return []
    devices: dict[str, Any] = {}
    for entry in hass.config_entries.async_entries(ZHA_DOMAIN):
        for device in dr.async_entries_for_config_entry(registry, entry.entry_id):
            devices[device.id] = device
    return sorted(devices.values(), key=lambda device: device.id)


def _from_registry(hass: HomeAssistant) -> dict[str, list]:
    """Everything the public registries know, which is the star and no more."""
    devices = _zha_devices(hass)
    if not devices:
        return {"nodes": [], "edges": []}

    nodes = [
        node(COORDINATOR_ID, label="Zigbee Koordinator", state="unknown",
             icon="mdi:zigbee", rolle="Koordinator", quelle="registry")
    ]
    edges = []
    for device in devices:
        nodes.append(
            node(
                f"device-{device.id}",
                label=getattr(device, "name_by_user", None)
                or getattr(device, "name", "")
                or "Zigbee Gerät",
                area_id=getattr(device, "area_id", None),
                icon="mdi:zigbee",
                hersteller=getattr(device, "manufacturer", "") or "",
                modell=getattr(device, "model", "") or "",
                quelle="registry",
            )
        )
        edges.append(
            edge(COORDINATOR_ID, f"device-{device.id}", quality="unknown", dashed=True)
        )
    return {"nodes": nodes, "edges": edges}


def async_setup_spatial(hass: HomeAssistant, entry: Any) -> None:
    def data() -> dict[str, list]:
        gateway = _gateway(hass)
        if gateway is None:
            return _from_registry(hass)
        try:
            result = _from_gateway(hass, gateway)
        except Exception:  # noqa: BLE001
            # One bad read must not cost the layer. The registry always
            # answers, so there is a picture either way.
            _LOGGER.debug("falling back to the device registry", exc_info=True)
            return _from_registry(hass)
        # A gateway that is up but has told us nothing yet is not a reason
        # to show an empty room.
        return result if result["nodes"] else _from_registry(hass)

    provider = spatial_provider(
        hass,
        entry,
        name="Zigbee",
        icon="mdi:zigbee",
        data=data,
        version="260808",
    )

    # ZHA fires no signal this integration could listen to, so the honest
    # option is a slow tick rather than pretending to be pushed.
    entry.async_on_unload(
        async_track_time_interval(
            hass, lambda _now: provider.async_notify(), REFRESH
        )
    )
