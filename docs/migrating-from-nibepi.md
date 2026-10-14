# Migrating from NibePi

[NibePi](https://github.com/anerdins/node-red-contrib-nibepi) runs on a Raspberry Pi
with an RS485 HAT: Node-RED nodes talk to the pump and republish every register over
MQTT (`nibe/modbus/<register>`). It works, but upstream has been quiet for years and the
SD card is the weak point — this project started when a Pi Zero 2 W running NibePi died
after SD/boot failures on three cards.

## What changes

| | NibePi | This repo |
|---|---|---|
| Hardware | Raspberry Pi + RS485 HAT + SD card | LilyGo T-CAN485 (ESP32, boots from flash) |
| Protocol bridge | Node-RED + `nibepi` backend | ESPHome + NibeGW |
| HA data path | MQTT discovery (`nibe/modbus/*`) | core Nibe Heat Pump integration (UDP) |
| Smarts | Node-RED plugins | HA packages + pyscript |
| Settings | NibePi web UI | HA helpers / dashboard, defaults in YAML |

The RS485 connection to the pump is the same: the T-CAN485 goes on the terminals the
NibePi HAT used. Mind the [ground jumper](hardware.md#️-the-ground-fix-read-this-first).

## Feature mapping

| NibePi (Swedish UI) | Here |
|---|---|
| Inomhusreglering S1 | indoor compensation (`nibe_indoor.yaml`) |
| Utomhustemperatur blockering / Stoppa värmeproduktion / Gradminut återställning | overshoot protection (same package) |
| Elprisreglering ("Lokal AI") | price control (`nibe_price.yaml` + `nibe_price.py`) |
| Fläktstyrning (incl. low mode, DM boost, CO₂) | fan regulation |
| Frekvensreglering | frequency regulation |
| Varmvatten (auto-lux, prioritet) | hot water |
| — | solar surplus (new) |

Not ported: virtual RMU-40, Plejd, filter monitoring and forecast (weather) control.

## Steps

1. Note your NibePi settings (screenshots of each plugin page) — you'll want the same
   values. The package defaults here match a real NibePi setup and are a good start.
2. Power down, swap the Pi for the T-CAN485, flash it ([hardware.md](hardware.md)).
3. Add the Nibe Heat Pump integration and enable the [entities](entities.md).
4. Remove the old MQTT-discovered `nibe/modbus/*` entities. Any automations or dashboard
   cards that used them need to point at the new integration's entities (e.g. MQTT
   `nibe/modbus/48132/set` → `select.temporary_lux_48132`).
5. Install the packages, set the values from step 1, watch, then turn on writes.

If you fed NibePi an external indoor temperature over MQTT (a common trick to use a house
average as "main room temp"), point `sensor.nibe_room_temp` in `nibe_site.yaml` at the same
sensor.
