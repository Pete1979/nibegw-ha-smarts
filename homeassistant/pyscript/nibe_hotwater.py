"""Nibe hot water — pyscript port of NibePi's hotwaterPlugin.

- Auto-luxury: detect a big hot-water draw (BT6 charge temp dropping fast) and
  trigger a temporary luxury boost.
- Priority: force hot water when BT7 (top) falls below a start temp.

Drives the Temporary Lux register via the native nibe_heatpump select entity.
Writes only happen when input_boolean.nibe_write_enable AND
input_boolean.nibe_hw_write_enable are on.
"""

import time

# Native nibe_heatpump entities (F730 via the NibeGW gateway).
BT6_ENTITY = "sensor.bt6_hw_load_40014"   # BT6 hot water charge/load
BT7_ENTITY = "sensor.bt7_hw_top_40013"    # BT7 hot water top

LUX_SELECT = "select.temporary_lux_48132"
LUX_ON = "ONE TIME INCREASE"
LUX_OFF = "OFF"

_STATE = {"last_lux": None, "bt6_hist": []}


def _num(entity, default):
    try:
        return float(state.get(entity))
    except Exception:  # missing entity raises in pyscript ("name ... is not defined")
        return default


def _get(entity):
    return _num(entity, None)


def _set_lux(option, reason):
    # Master guards: never touch the pump unless writes are explicitly enabled.
    if state.get("input_boolean.nibe_write_enable") != "on":
        return
    if state.get("input_boolean.nibe_hw_write_enable") != "on":
        return
    if _STATE["last_lux"] == option:
        return
    _STATE["last_lux"] = option
    service.call("select", "select_option", entity_id=LUX_SELECT, option=option)
    log.info(f"nibe_hw: temporary lux -> {option} ({reason})")


@time_trigger("startup", "cron(*/2 * * * *)")
def nibe_hotwater_update(**kwargs):
    try:
        _run_hw()
        state.set("sensor.nibe_hw_debug", "ok", {"message": ""})
    except Exception as exc:
        log.error(f"nibe_hw failed: {exc}")
        state.set("sensor.nibe_hw_debug", "error", {"message": str(exc)})


def _run_hw():
    bt6 = _get(BT6_ENTITY)
    bt7 = _get(BT7_ENTITY)
    if bt6 is None and bt7 is None:
        state.set("sensor.nibe_hw_status", "waiting for pump data",
                  {"friendly_name": "Nibe hot water status"})
        return

    now = time.time()
    status = []

    if state.get("input_boolean.nibe_hw_priority_enable") == "on" and bt7 is not None:
        if bt7 <= _num("input_number.nibe_hw_priority_start", 45):
            _set_lux(LUX_ON, "priority start")
            status.append("priority ON")
        elif bt7 >= _num("input_number.nibe_hw_priority_stop", 48):
            _set_lux(LUX_OFF, "priority satisfied")
            status.append("priority satisfied")

    if state.get("input_boolean.nibe_hw_autolux_enable") == "on" and bt6 is not None:
        window = _num("input_number.nibe_hw_autolux_window", 10) * 60
        diff = _num("input_number.nibe_hw_autolux_diff", 5)
        _STATE["bt6_hist"].append((now, bt6))
        _STATE["bt6_hist"] = [h for h in _STATE["bt6_hist"] if h[0] >= now - window]
        oldest = _STATE["bt6_hist"][0][1]
        if oldest - bt6 >= diff:
            _set_lux(LUX_ON, "big draw detected")
            status.append("auto-lux ON")
        elif bt6 >= oldest:
            _set_lux(LUX_OFF, "recovered")

    state.set("sensor.nibe_hw_status", ", ".join(status) or "idle",
              {"friendly_name": "Nibe hot water status", "bt6": bt6, "bt7": bt7})
