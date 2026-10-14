"""Nibe price-based control — pyscript port of NibePi's 'Lokal AI' (runPrice).

Reads Nordpool prices (via sensor.nibe_spot_price -> its `source` attribute) +
the tuning helpers, runs the arbitrage classifier from tools/price_optimizer.py,
and publishes:
    sensor.nibe_price_level        (VERY_CHEAP..VERY_EXPENSIVE)
    sensor.nibe_price_level_num    (-2..+2, for graphs)
    sensor.nibe_price_current      (öre/kWh)
    sensor.nibe_price_heat_offset  (°C, -> reg 47011 via total offset)
    sensor.nibe_price_hw_mode      (0=eco 1=normal 2=lux, -> reg 47041)

Runs on startup and every 15 min.
"""

import math
from datetime import datetime

SPOT = "sensor.nibe_spot_price"  # site alias; attribute `source` = Nordpool sensor
INDOOR_SENSOR = "sensor.nibe_room_temp"
SETPOINT = "input_number.nibe_setpoint"
LEVEL_NUM = {"VERY_CHEAP": -2, "CHEAP": -1, "NORMAL": 0, "EXPENSIVE": 1, "VERY_EXPENSIVE": 2}
HW_NUM = {"Economy": 0, "Normal": 1, "Luxury": 2}


def _num(entity_id, default):
    try:
        return float(state.get(entity_id))
    except (ValueError, TypeError):
        return default


def _hourly_points(raw):
    """Average Nordpool sub-hourly slots into hourly öre points."""
    groups = {}
    for it in raw or []:
        start = it["start"]
        if not isinstance(start, str):  # pyscript exposes these as datetime objects
            start = start.isoformat()
        groups.setdefault(start[:13], []).append({"start": start, "value": it["value"]})
    pts = []
    for hour, items in groups.items():
        avg = sum([i["value"] for i in items]) / len(items)
        start = f"{hour}:00:00{items[0]['start'][19:]}"
        ts = int(datetime.fromisoformat(start).timestamp() * 1000)
        pts.append({"value": avg * 100.0, "ts": ts, "date": start})
    pts.sort(key=lambda p: p["ts"])
    return pts


def _opportunities(sorted_pts, original, window_h, min_spread):
    slots = window_h
    if len(original) > 1:
        mins = (original[1]["ts"] - original[0]["ts"]) / 60000
        if mins > 0:
            slots = round(window_h * 60 / mins)
    idx = dict([(p["ts"], i) for i, p in enumerate(original)])
    ops = []
    for low in sorted_pts:
        bi = idx.get(low["ts"], -1)
        if bi == -1:
            continue
        for si in range(bi + 1, min(bi + int(slots), len(original))):
            spread = original[si]["value"] - low["value"]
            if spread > min_spread:
                ops.append({"buy": low, "sell": original[si], "spread": spread})
    ops.sort(key=lambda o: o["spread"], reverse=True)
    return ops


def _best_combo(ops):
    best, best_profit = [], -1.0
    for i, seed in enumerate(ops):
        combo = [seed]
        for j, cand in enumerate(ops):
            if i == j:
                continue
            clash = any([
                t["buy"]["ts"] == cand["buy"]["ts"]
                or t["sell"]["ts"] == cand["sell"]["ts"]
                or t["buy"]["ts"] == cand["sell"]["ts"]
                or t["sell"]["ts"] == cand["buy"]["ts"]
                for t in combo
            ])
            if not clash:
                combo.append(cand)
        profit = sum([t["spread"] for t in combo])
        if profit > best_profit:
            best, best_profit = combo, profit
    return best


def _classify(points, window_h, min_spread):
    levels = dict([(p["date"], "NORMAL") for p in points])
    if not points:
        return levels
    sp = sorted(points, key=lambda p: p["value"])
    if sp[-1]["value"] - sp[0]["value"] <= min_spread:
        return levels
    ops = _opportunities(sp, points, window_h, min_spread)
    if not ops:
        return levels
    combo = _best_combo(ops)
    buys = sorted([t["buy"] for t in combo], key=lambda p: p["value"])
    sells = sorted([t["sell"] for t in combo], key=lambda p: p["value"])
    bm, sm = math.floor(len(buys) / 2), math.floor(len(sells) / 2)
    very_cheap = set([p["date"] for p in buys[:bm]])
    cheap = set([p["date"] for p in buys[bm:]])
    expensive = set([p["date"] for p in sells[:sm]])
    very_exp = set([p["date"] for p in sells[sm:]])
    for d in levels:
        if d in very_cheap:
            levels[d] = "VERY_CHEAP"
        elif d in cheap:
            levels[d] = "CHEAP"
        elif d in very_exp:
            levels[d] = "VERY_EXPENSIVE"
        elif d in expensive:
            levels[d] = "EXPENSIVE"
    return levels


@time_trigger("startup", "cron(*/15 * * * *)")
def nibe_price_update(**kwargs):
    try:
        _nibe_price_compute()
        state.set("sensor.nibe_price_debug", "ok", {"message": ""})
    except Exception as exc:
        log.error(f"nibe_price failed: {exc}")
        state.set("sensor.nibe_price_debug", "error", {"message": str(exc)})


def _nibe_price_compute():
    source = (state.getattr(SPOT) or {}).get("source")
    if not source:
        log.warning(f"nibe_price: {SPOT} has no 'source' attribute (see nibe_site.yaml)")
        return
    attrs = state.getattr(source) or {}
    raw = list(attrs.get("raw_today") or []) + list(attrs.get("raw_tomorrow") or [])
    points = _hourly_points(raw)
    if not points:
        log.warning("nibe_price: no Nordpool price data yet")
        return

    window_h = _num("input_number.nibe_price_window", 12)
    min_spread = _num("input_number.nibe_price_min_spread", 35)
    levels = _classify(points, window_h, min_spread)

    now_ms = int(datetime.now().astimezone().timestamp() * 1000)
    cur = points[0]
    for p in points:
        if p["ts"] <= now_ms:
            cur = p
        else:
            break
    level = levels[cur["date"]]

    enabled = state.get("input_boolean.nibe_price_enable") == "on"
    lname = level.lower()
    heat = _num(f"input_number.nibe_price_heat_{lname}", 0) if enabled else 0

    # indoor guard: don't cut heat on expensive hours if the room is already cool
    if heat < 0 and state.get("input_boolean.nibe_price_temp_guard") == "on":
        sp = _num(SETPOINT, 21)
        low = _num("input_number.nibe_price_temp_low", -1.5)
        room = _num(INDOOR_SENSOR, sp)
        if room <= sp + low:
            heat = 0
    heat = max(-10, min(10, heat))

    hw_opt = state.get(f"input_select.nibe_price_hw_{lname}")
    hw = HW_NUM.get(hw_opt, 0) if enabled else 0

    state.set("sensor.nibe_price_current", round(cur["value"], 2),
              {"unit_of_measurement": "öre/kWh", "icon": "mdi:flash", "friendly_name": "Nibe price now"})
    state.set("sensor.nibe_price_level", level,
              {"icon": "mdi:cash-clock", "friendly_name": "Nibe price level"})
    state.set("sensor.nibe_price_level_num", LEVEL_NUM[level],
              {"friendly_name": "Nibe price level (num)"})
    state.set("sensor.nibe_price_heat_offset", heat,
              {"unit_of_measurement": "°C", "icon": "mdi:tune-vertical",
               "friendly_name": "Nibe price heat offset"})
    state.set("sensor.nibe_price_hw_mode", hw,
              {"icon": "mdi:water-thermometer", "friendly_name": "Nibe price HW mode"})
    log.info(f"nibe_price: {level} @ {round(cur['value'],1)} öre -> heat {heat:+g}, hw {hw}")
