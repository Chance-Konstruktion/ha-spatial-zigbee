# Spatial Zigbee

> **Vorlaeufige Beschreibung.** Aus CHANGELOG und Quelltext rekonstruiert, nicht die Originalfassung des Autors -- diese liegt auf GitHub und wird hier ersetzt, sobald sie verfuegbar ist.


Spatial-Hub-Provider fuer Koordinator, Router und End Devices des Zigbee-Netzes.

Diese Integration meldet dem [Spatial Hub](https://gitlab.schanz.ipv64.net/chance-konstruktion/ha-spatial-hub) Koordinator, Router und End Devices des Zigbee-Netzes,
damit sie auf dem Grundriss auftauchen. Sie ist eine ganz normale Home
Assistant-Integration und laeuft auch dann, wenn der Hub gar nicht
installiert ist -- dann passiert schlicht nichts.

## Was hier als Kante gemeldet wird

Die Routing-Topologie: welches Geraet haengt an welchem Router. Zigbee kennt seine Nachbarn samt Verbindungsqualitaet, und genau das ist eine Kante.

## Stand

**Geruest.** Aufbau, Manifest, Config Flow und der Provider-Shim stehen.
Was noch fehlt, ist die Funktion `_spatial_data()` in
[`custom_components/spatial_zigbee/__init__.py`](custom_components/spatial_zigbee/__init__.py):
Sie liefert derzeit eine leere Liste, weil das Auslesen der Topologie fuer
jeden Transport anders aussieht.

Die Integration ist damit installierbar und tut nichts Falsches -- sie hat
nur noch nichts zu erzaehlen.

## Installation

Dieser Knopf oeffnet das Repository in deiner eigenen HACS-Installation und traegt es dabei automatisch als benutzerdefiniertes Repository ein:

[![Öffne deine Home-Assistant-Instanz und dieses Repository in HACS.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=Chance-Konstruktion&repository=ha-spatial-zigbee&category=integration)

Ueber HACS als benutzerdefiniertes Repository, oder von Hand:
`custom_components/spatial_zigbee/` nach `config/custom_components/` kopieren und
Home Assistant neu starten.

[![Öffne deine Home-Assistant-Instanz und starte die Einrichtung der Integration.](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=spatial_zigbee)

## Warum nichts aus `spatial_hub` importiert wird

Die Datei `spatial_hub_provider.py` ist eine **Kopie** aus dem Hub, kein
Import. Der Hub kann fehlen, in einer anderen Version vorliegen oder
entfernt werden, waehrend diese Integration weiterlaeuft. Der Shim schreibt
nur ein Dict nach `hass.data` und feuert ein Dispatcher-Signal -- beides
kostet nichts, wenn niemand zuhoert.

Naeheres in der [SDK-Dokumentation des Hubs](https://gitlab.schanz.ipv64.net/chance-konstruktion/ha-spatial-hub/-/blob/main/sdk/README.md).

## Lizenz

MIT, siehe [LICENSE](LICENSE).

