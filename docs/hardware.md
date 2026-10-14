# Hardware — the T-CAN485 gateway

## Parts

| Part | Notes |
|---|---|
| [LilyGo T-CAN485](https://github.com/Xinyuan-LilyGO/T-CAN485) | ESP32-D0WDQ6, `MAX13487` auto-direction RS485 transceiver, 5–12 V input |
| 2-wire cable for A/B | Short twisted pair is fine |
| 2-wire cable for power | From the pump's 12 V accessory supply |
| 1 short jumper wire | **Required** — see the ground fix below |

The gateway talks to the pump's **RS485 accessory bus** (the same port a MODBUS40 or
NibePi would use). [NibeGW](https://github.com/elupus/esphome-nibe) emulates a MODBUS40
accessory and ACKs the pump's telegrams, so the pump doesn't raise a communication alarm.

## Wiring

> Check your pump's installer manual for the accessory terminal block (on the F730 it's
> on the input board). Power the pump off before opening it.

| Pump terminal | T-CAN485 |
|---|---|
| RS485 **A** | RS485 **A** |
| RS485 **B** | RS485 **B** |
| **+12 V** | power **+** |
| **GND** | power **−** |
| — | **jumper: RS485 connector `GND` → power `−`** |

### ⚠️ The ground fix (read this first)

The board's RS485 3-pin connector is `RS_B / GND / RS_A`. On first power-up the gateway
logged only garbage bytes (`D4 BF 29 FF 69 …`, `Ignoring byte` in the ESPHome log) and
never a valid frame. Swapping A/B made it go completely silent.

**Cause:** the T-CAN485's RS485 transceiver ground is **galvanically isolated** from the
ESP32/main ground (it sits on its own boost-converter rail). Left floating it drifts out
of the transceiver's common-mode range, so the pump's differential signal is mis-sampled
as garbage in one A/B orientation and nothing in the other.

**Fix:**
1. **Jumper the RS485 connector's `GND` pin to the power `−` terminal.** That bonds the
   isolated RS485 ground to the pump's ground. Safe here because the board is powered
   from the pump's own 12 V supply (same ground).
2. Keep A/B in the orientation that produced the *flood* of garbage — that was the right
   polarity; it only looked wrong because of the floating ground.

**Quick check:** continuity between the RS485 `GND` pin and power `−`. No beep = floating
ground = this problem. Chasing A/B polarity or UART inversion won't help — it's a ground
problem, not a polarity one.

**It works when:** the pump's service menu → *search for installed accessories* finds the
gateway, there is no communication alarm, and the ESPHome log has no `Ignoring byte` lines.

## Flashing

1. `cp esphome/secrets.example.yaml esphome/secrets.yaml` and fill it in. Give the gateway
   a **fixed IP** (static or a DHCP reservation) — HA sends requests *to* it.
2. Flash it, either from the ESPHome Device Builder add-on or the CLI:

   ```bash
   python3 -m venv esphome-venv && ./esphome-venv/bin/pip install esphome
   ./esphome-venv/bin/esphome run esphome/nibegw-t-can485.yaml          # first time over USB
   ./esphome-venv/bin/esphome upload esphome/nibegw-t-can485.yaml --device <gateway-ip>  # later, OTA
   ```

3. In the pump's service menu, enable the accessory/Modbus input and run the accessory
   search.

## Home Assistant side

*Settings → Devices & services → Add integration → **Nibe Heat Pump***:

| Field | Value |
|---|---|
| Connection | **NibeGW** |
| Model | your pump, e.g. **F730** |
| Remote IP | the gateway's IP |
| Listening port | `9999` |
| Remote read / write port | `9999` / `10000` |

The gateway pushes telegrams to HA on `9999`; HA sends reads/writes to the gateway over
UDP. If the gateway and HA are on **different VLANs**, allow UDP 9999/10000 in **both**
directions. The `ha_ip` secret also limits which host may send requests to the gateway.

Most of the ~500+ entities the integration creates are **disabled by default** — enable the
ones in [entities.md](entities.md).

## Notes

- `power_save_mode: none` on Wi-Fi matters: the protocol is UDP and dropped packets mean
  missed telegrams.
- `api.reboot_timeout: 0s` stops the gateway rebooting when HA restarts.
- The ESP32 boots from flash — no SD card to wear out.
