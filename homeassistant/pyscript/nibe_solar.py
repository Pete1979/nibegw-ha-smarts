"""Nibe solar-surplus control — self-consume exported PV as stored heat.

Reads net grid power (kW, negative = exporting). When the house is exporting
surplus, request hot-water Luxury (the tank is a free thermal battery) and add a
space-heat boost (capped by a comfort ceiling), so free solar is stored as heat
instead of exported cheaply. Publishes sensors only; the guarded write path
(nibe_write_enable) actuates the pump.

Forecast-gated: activates on real export surplus only while the solar forecast
still shows meaningful production ahead today, holds through passing clouds, and
releases as soon as the sun is basically done for the day (or after a long
no-surplus fallback). Maximises self-consumption without importing to make hot
water. Inert until input_boolean.nibe_solar_enable is on.

Level 2 "leave room for solar": on a sunny-forecast morning (before the surplus
arrives) request Economy hot water so the tank stays cool with spare capacity to
absorb the coming midday solar, instead of paying grid power to fill it early.
This NEVER touches the pump's legionella / periodic-increase cycle: comfort mode
does not gate the periodic increase, and reserve additionally stands down whenever
a boost/legionella run is detected (BT7 high) or the tank is critically cold.

Price-gated: at Expensive / Very Expensive spot hours the surplus is worth more
exported (sold high) than stored, since hot water can be made later when cheap.
The boost stands down while the price level is at/above nibe_solar_price_max_level
OR the absolute spot price is at/above nibe_solar_export_price (currency/kWh);
the surplus then flows to the grid.
"""

import time

GRID = "sensor.nibe_grid_power"                               # kW, - = export surplus
FORECAST_REMAIN = "sensor.nibe_solar_forecast_remaining"      # kWh of sun left today
PRICE_LEVEL = "sensor.nibe_price_level_num"                   # -2 v.cheap .. +2 v.exp
SPOT = "sensor.nibe_spot_price"                               # current spot, currency/kWh
BT7 = "sensor.bt7_hw_top_40013"                               # hot water top temp
ROOM = "sensor.nibe_room_temp"
SETPOINT = "input_number.nibe_setpoint"

_STATE = {"active": False, "below_since": None}


def _num(entity, default):
    try:
        return float(state.get(entity))
    except Exception:  # missing/unknown entity raises in pyscript
        return default


def _on(entity):
    try:
        return state.get(entity) == "on"
    except Exception:
        return False


@time_trigger("startup", "cron(*/2 * * * *)")
def nibe_solar_update(**kwargs):
    try:
        _run()
        state.set("sensor.nibe_solar_debug", "ok", {"message": ""})
    except Exception as exc:
        log.error(f"nibe_solar failed: {exc}")
        state.set("sensor.nibe_solar_debug", "error", {"message": str(exc)})


def _publish(active, heat, hw, remaining=None, sun_ahead=None,
             price_block=None, price_level=None, spot=None, reason=None,
             bt7=None, tank_room=None):
    attrs = {"friendly_name": "Nibe solar boost", "icon": "mdi:solar-power"}
    if remaining is not None:
        attrs["forecast_remaining_kwh"] = round(remaining, 2)
    if sun_ahead is not None:
        attrs["sun_ahead"] = sun_ahead
    if price_block is not None:
        attrs["price_block"] = price_block
        attrs["price_level"] = price_level
        attrs["spot_price"] = spot
        attrs["export_reason"] = reason
    state.set("sensor.nibe_solar_active", "on" if active else "idle", attrs)
    state.set("sensor.nibe_solar_heat_offset", heat,
              {"unit_of_measurement": "°C", "icon": "mdi:solar-power",
               "friendly_name": "Nibe solar heat offset"})
    hw_attrs = {"friendly_name": "Nibe solar HW request"}
    if bt7 is not None:
        hw_attrs["bt7_top"] = bt7
        hw_attrs["tank_has_room"] = tank_room
    state.set("sensor.nibe_solar_hw_mode", hw, hw_attrs)


def _run():
    net = _num(GRID, 0.0)          # + = importing, - = exporting
    surplus = max(0.0, -net)       # kW currently exported
    state.set("sensor.nibe_solar_surplus", round(surplus, 2),
              {"unit_of_measurement": "kW", "icon": "mdi:solar-power",
               "friendly_name": "Nibe solar surplus"})

    if not _on("input_boolean.nibe_solar_enable"):
        _STATE["active"] = False
        _STATE["below_since"] = None
        _publish(False, 0, 0)
        return

    on_th = _num("input_number.nibe_solar_export_on", 1.0)
    off_th = _num("input_number.nibe_solar_export_off", 0.3)
    release = _num("input_number.nibe_solar_release_min", 45) * 60
    remaining = _num(FORECAST_REMAIN, 0.0)                       # kWh of sun left today
    sun_ahead = remaining >= _num("input_number.nibe_solar_forecast_min", 2.0)
    now = time.time()
    # Forecast-gated + sticky: only commit while the sun is still coming; hold
    # through cloud gaps; release the moment the day's production is basically
    # done (or after a long no-surplus fallback for a rare mid-day cloud bank).
    if not sun_ahead:
        _STATE["active"] = False
        _STATE["below_since"] = None
    elif surplus >= on_th:
        _STATE["active"] = True
        _STATE["below_since"] = None
    elif surplus <= off_th:
        if _STATE["below_since"] is None:
            _STATE["below_since"] = now
        if _STATE["active"] and (now - _STATE["below_since"]) >= release:
            _STATE["active"] = False
    else:
        _STATE["below_since"] = None
    active = _STATE["active"]

    # Price gate: at Expensive / Very Expensive hours the surplus is worth more
    # sold to the grid than stored (hot water can be made later when cheap), so
    # stand the boost down. surplus/forecast machine keeps tracking regardless.
    price_level = int(_num(PRICE_LEVEL, 0))
    spot = _num(SPOT, 0.0)                             # current spot, currency/kWh
    price_block = False
    reason = None
    if _on("input_boolean.nibe_solar_price_gate_enable"):
        by_level = price_level >= _num("input_number.nibe_solar_price_max_level", 1)
        thr = _num("input_number.nibe_solar_export_price", 0)   # currency/kWh, 0 = off
        by_price = thr > 0 and spot >= thr
        price_block = by_level or by_price
        if by_price and by_level:
            reason = "level+price"
        elif by_price:
            reason = "price"
        elif by_level:
            reason = "level"
    store = active and not price_block

    # Hot water: request Luxury while storing, but only while the tank top (BT7)
    # still has room. Pushing the Luxury top-end into an already-hot tank trips
    # the compressor's envelope -> short 3-5 min runs -> "short operating times"
    # alarm; once BT7 is near full, store nothing (export instead).
    bt7 = _num(BT7, 0.0)
    hw_full = _num("input_number.nibe_solar_hw_full_temp", 50.0)
    tank_room = bt7 < hw_full
    hw = 2 if (store and _on("input_boolean.nibe_solar_hw_enable") and tank_room) else 0

    # Space heat: fixed boost while storing, suppressed once the room is warm.
    heat = 0
    if store and _on("input_boolean.nibe_solar_heat_enable"):
        sp = _num(SETPOINT, 21.0)
        room = _num(ROOM, sp)
        ceiling = _num("input_number.nibe_solar_comfort_ceiling", 1.0)
        if room < sp + ceiling:
            heat = int(_num("input_number.nibe_solar_heat_max", 4))

    _publish(store, heat, hw, remaining, sun_ahead, price_block, price_level, spot, reason,
             bt7, tank_room)
    _reserve(active, remaining)


def _reserve(active, remaining):
    # Level 2: on a sunny-forecast morning, before the surplus actually arrives,
    # request Economy hot water so the tank keeps spare capacity for the coming
    # solar instead of being filled with paid grid power. Handoff is automatic:
    # once real surplus flows, solar boost goes active -> reserve stands down ->
    # HW target jumps to Luxury.
    reserve = False
    bt7 = _num(BT7, 50.0)
    legionella_temp = _num("input_number.nibe_solar_legionella_temp", 60.0)
    floor = _num("input_number.nibe_solar_reserve_floor", 40.0)
    legionella = bt7 >= legionella_temp   # periodic increase / boost in progress
    too_cold = bt7 <= floor               # protect household hot water
    hour = time.localtime().tm_hour
    start = _num("input_number.nibe_solar_reserve_start_hour", 5)
    end = _num("input_number.nibe_solar_reserve_end_hour", 12)
    in_window = start <= hour < end
    sunny = remaining >= _num("input_number.nibe_solar_reserve_forecast_min", 8.0)
    if _on("input_boolean.nibe_solar_reserve_enable"):
        # Reserve only while a real solar day is still ahead, in the morning
        # window, surplus not yet flowing, tank neither cold nor mid-legionella.
        reserve = (sunny and in_window and not active
                   and not too_cold and not legionella)
    state.set(
        "sensor.nibe_solar_hw_reserve", "on" if reserve else "idle",
        {"friendly_name": "Nibe solar HW reserve", "icon": "mdi:water-off",
         "bt7": bt7, "in_window": in_window, "sunny_forecast": sunny,
         "legionella_run": legionella, "tank_too_cold": too_cold})
