"""Nibe fan control — pyscript port of NibePi's runFan.

Regulates the exhaust fan speed (%) to reach a target airflow (BS1, m³/h),
choosing the target by mode:
  normal -> low (compressor on low freq) -> DM boost (low degree minutes)
  -> CO2 boost (high CO2). Only regulates when not defrosting.

Writes the exhaust fan-speed register via the native nibe_heatpump number
entity. Writes only happen when input_boolean.nibe_write_enable is on (master
guard) AND input_boolean.nibe_fan_enable is on.
"""

# Native nibe_heatpump entities (F730 via the NibeGW gateway).
# The regulated value is the NORMAL-mode fan-speed setpoint register 47265 (a %);
# we read it and nudge it against the BS1 airflow error, then write it back.
BS1_FLOW_ENTITY = "sensor.eb100_bs1_air_flow_40050"        # BS1 airflow (m³/h)
FAN_SPEED_ENTITY = "number.exhaust_fan_speed_normal_47265"  # fan speed % setpoint (read+write)
CPR_SET_ENTITY = "sensor.compressor_frequency_actual_43136"
ALARM_ENTITY = "sensor.alarm_45001"
EVAPORATOR_ENTITY = "sensor.eb100_bt16_evaporator_temp_40020"
DM_ENTITY = "number.degree_minutes_16_bit_43005"
CO2_ENTITY = "sensor.nibe_co2"  # optional site alias (nibe_site.yaml); F730 has no CO2 sensor

FAN_SPEED_WRITE = "number.exhaust_fan_speed_normal_47265"  # reg 47265
DEFROST_ALARM = 183

_STATE = {"last_speed": None}


def _num(entity, default):
    try:
        return float(state.get(entity))
    except Exception:  # missing entity raises in pyscript
        return default


def _get(entity):
    return _num(entity, None)


def _on(entity):
    try:
        return state.get(entity) == "on"
    except Exception:
        return False


def _set_speed(speed):
    speed = int(max(0, min(100, speed)))
    if _STATE["last_speed"] == speed:
        return
    # Master guard: never touch the pump unless writes are explicitly enabled.
    if state.get("input_boolean.nibe_write_enable") != "on":
        return
    _STATE["last_speed"] = speed
    service.call("number", "set_value", entity_id=FAN_SPEED_WRITE, value=speed)
    log.info(f"nibe_fan: exhaust fan speed -> {speed}%")


@time_trigger("startup", "cron(*/1 * * * *)")
def nibe_fan_update(**kwargs):
    try:
        _run_fan()
        state.set("sensor.nibe_fan_debug", "ok", {"message": ""})
    except Exception as exc:
        log.error(f"nibe_fan failed: {exc}")
        state.set("sensor.nibe_fan_debug", "error", {"message": str(exc)})


def _run_fan():
    if not _on("input_boolean.nibe_fan_enable"):
        state.set("sensor.nibe_fan_status", "disabled", {"friendly_name": "Nibe fan status"})
        return

    flow = _get(BS1_FLOW_ENTITY)
    speed = _get(FAN_SPEED_ENTITY)
    if flow is None or speed is None:
        state.set("sensor.nibe_fan_status", "waiting for pump data",
                  {"friendly_name": "Nibe fan status"})
        return

    # choose target airflow by mode (later modes win)
    mode = "normal"
    target = _num("input_number.nibe_fan_flow_normal", 200)
    cpr = _get(CPR_SET_ENTITY)
    if _on("input_boolean.nibe_fan_low_enable") and cpr is not None \
            and cpr < _num("input_number.nibe_fan_low_cpr_freq", 30):
        mode, target = "low", _num("input_number.nibe_fan_flow_low", 150)
    dm = _get(DM_ENTITY)
    if _on("input_boolean.nibe_fan_dmboost_enable") and dm is not None \
            and dm < _num("input_number.nibe_fan_dmboost_dm", -300):
        mode, target = "dm_boost", _num("input_number.nibe_fan_flow_dmboost", 250)
    co2 = _get(CO2_ENTITY)
    if _on("input_boolean.nibe_fan_co2_enable") and co2 is not None \
            and co2 > _num("input_number.nibe_fan_co2_high", 1000):
        mode, target = "co2_boost", _num("input_number.nibe_fan_flow_high", 300)

    # don't regulate while defrosting or evaporator too cold
    alarm = _get(ALARM_ENTITY)
    evap = _get(EVAPORATOR_ENTITY)
    if (alarm is not None and int(alarm) == DEFROST_ALARM) or (evap is not None and evap <= 0):
        state.set("sensor.nibe_fan_status", "paused (defrost/cold)",
                  {"friendly_name": "Nibe fan status", "mode": mode, "target": target})
        return

    # step the fan speed toward the airflow target (nibepi-style)
    new = speed
    if flow > target + 20:
        new = speed - 5
    elif flow > target + 10:
        new = speed - 1
    elif flow < target - 20:
        new = speed + 5
    elif flow < target - 10:
        new = speed + 1
    if new != speed:
        _set_speed(new)

    state.set("sensor.nibe_fan_status", mode,
              {"friendly_name": "Nibe fan status", "target_airflow": target,
               "actual_airflow": flow, "fan_speed": speed})
