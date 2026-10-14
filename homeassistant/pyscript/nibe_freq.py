"""Nibe frequency regulation — pyscript port of NibePi's frekvensreglering.

At high electricity price, cap the compressor MAX frequency (reg 47104) on an
outdoor-temperature curve instead of only cutting the heat curve, and optionally
block the additional (immersion) heater. Restores the pump default (normal_max)
when the price is back to normal.

Every write is gated by input_boolean.nibe_write_enable AND the feature toggle
input_boolean.nibe_freq_enable, so nothing touches the pump until both are on.
"""

FREQ_MAX = "number.max_comp_freq_47104"
ALLOW_ADD = "switch.allow_additive_heating_47370"
OUTDOOR = "sensor.bt1_outdoor_temperature_40004"
LEVEL = "sensor.nibe_price_level_num"

_STATE = {"last_max": None, "last_add": None}


def _num(entity, default):
    try:
        return float(state.get(entity))
    except Exception:  # missing entity raises in pyscript
        return default


def _on(entity):
    try:
        return state.get(entity) == "on"
    except Exception:  # brand-new helper not yet seen by pyscript -> treat as off
        return False


def _write_enabled():
    return _on("input_boolean.nibe_write_enable")


def _set_max(value, reason):
    value = int(round(value))
    if _STATE["last_max"] == value:
        return
    if not _write_enabled():
        return
    _STATE["last_max"] = value
    service.call("number", "set_value", entity_id=FREQ_MAX, value=value)
    log.info(f"nibe_freq: max comp freq -> {value} Hz ({reason})")


def _set_add(allow):
    want = "on" if allow else "off"
    if _STATE["last_add"] == want:
        return
    if not _write_enabled():
        return
    _STATE["last_add"] = want
    service.call("switch", "turn_on" if allow else "turn_off", entity_id=ALLOW_ADD)
    log.info(f"nibe_freq: allow additive heating -> {want}")


def _target_max():
    out = _num(OUTDOOR, 0)
    tmin = _num("input_number.nibe_freq_min_temp", 10)   # at/below -> max freq
    tmax = _num("input_number.nibe_freq_max_temp", 10)   # at/above -> min freq
    fmax = _num("input_number.nibe_freq_max_freq", 70)
    fmin = _num("input_number.nibe_freq_min_freq", 36)
    if tmax <= tmin:
        return fmax if out < tmin else fmin
    if out <= tmin:
        return fmax
    if out >= tmax:
        return fmin
    frac = (out - tmin) / (tmax - tmin)
    return fmax + (fmin - fmax) * frac


@time_trigger("startup", "cron(*/5 * * * *)")
def nibe_freq_update(**kwargs):
    try:
        _run()
        state.set("sensor.nibe_freq_debug", "ok", {"message": ""})
    except Exception as exc:
        log.error(f"nibe_freq failed: {exc}")
        state.set("sensor.nibe_freq_debug", "error", {"message": str(exc)})


def _run():
    normal_max = _num("input_number.nibe_freq_normal_max", 120)

    if not _on("input_boolean.nibe_freq_enable"):
        # Feature off: restore the pump default only if we ever changed it.
        if _STATE["last_max"] is not None and _STATE["last_max"] != int(normal_max):
            _set_max(normal_max, "disabled -> restore")
        if _STATE["last_add"] == "off":
            _set_add(True)
        state.set("sensor.nibe_freq_status", "off",
                  {"friendly_name": "Nibe frequency regulation"})
        return

    level = _num(LEVEL, 0)
    high = _num("input_number.nibe_freq_high_level", 1)

    if level >= high:
        tgt = _target_max()
        _set_max(tgt, f"high price (level {level:+g})")
        _set_add(not _on("input_boolean.nibe_freq_prevent_aux"))
        state.set("sensor.nibe_freq_status", f"limiting {int(round(tgt))} Hz",
                  {"friendly_name": "Nibe frequency regulation", "price_level": level})
    else:
        _set_max(normal_max, "normal price")
        _set_add(True)
        state.set("sensor.nibe_freq_status", "normal",
                  {"friendly_name": "Nibe frequency regulation", "price_level": level})
