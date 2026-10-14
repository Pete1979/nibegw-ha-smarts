"""Faithful Python port of the nibepi electricity-price control algorithm.

Reproduces the "Lokal AI" logic from node-red-contrib-nibepi
(config_node.js: optimizeElectricityTrading + runPrice + priceAdjustCurve),
so the same price-based heat/hot-water behaviour can run in Home Assistant
against an ESP32 NibeGW gateway instead of a Raspberry Pi.

The algorithm is a price-arbitrage classifier, NOT machine learning:
  1. Fetch today's + tomorrow's spot prices (elprisetjustnu.se).
  2. Find the best set of non-overlapping cheap->expensive "trades" within a
     look-ahead window whose spread exceeds min_spread.
  3. Split the resulting buy hours by median into VERY_CHEAP / CHEAP and the
     sell hours by median into EXPENSIVE / VERY_EXPENSIVE; everything else is
     NORMAL.
  4. Map the current hour's level to a heat-curve offset and hot-water mode.

Pure stdlib so it can run anywhere (CLI now, pyscript/MQTT service later).
"""

from __future__ import annotations

import json
import math
import urllib.request
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Optional

# --- price point -----------------------------------------------------------


@dataclass(frozen=True)
class PricePoint:
    """A single hourly price slot."""

    value: float  # price in öre/kWh (SEK_per_kWh * 100)
    ts: int  # epoch milliseconds of the slot start
    date: str  # ISO start string, used as the identity key (matches JS)


@dataclass
class Trade:
    buy: PricePoint
    sell: PricePoint
    spread: float


# --- arbitrage optimizer (port of optimizeElectricityTrading) --------------


def _find_trading_opportunities(
    sorted_prices: list[PricePoint],
    original: list[PricePoint],
    time_window_hours: float,
    min_spread: float,
) -> list[Trade]:
    """For each cheap slot look ahead within the window for a pricier slot."""
    slots_in_window = time_window_hours
    if len(original) > 1:
        slot_minutes = (original[1].ts - original[0].ts) / 60000
        if slot_minutes > 0:
            slots_in_window = round((time_window_hours * 60) / slot_minutes)

    ts_index = {p.ts: i for i, p in enumerate(original)}
    opportunities: list[Trade] = []
    for low in sorted_prices:  # cheapest first
        buy_index = ts_index.get(low.ts, -1)
        if buy_index == -1:
            continue
        end_index = min(buy_index + int(slots_in_window), len(original))
        for sell_index in range(buy_index + 1, end_index):
            spread = original[sell_index].value - low.value
            if spread > min_spread:
                opportunities.append(Trade(buy=low, sell=original[sell_index], spread=spread))

    opportunities.sort(key=lambda t: t.spread, reverse=True)
    return opportunities


def _find_optimal_combination(opportunities: list[Trade]) -> list[Trade]:
    """Greedy: seed with each opportunity, add non-conflicting ones, keep best."""
    if not opportunities:
        return []

    best: list[Trade] = []
    best_profit = -1.0
    for i, seed in enumerate(opportunities):
        combination = [seed]
        for j, cand in enumerate(opportunities):
            if i == j:
                continue
            conflict = any(
                t.buy.ts == cand.buy.ts
                or t.sell.ts == cand.sell.ts
                or t.buy.ts == cand.sell.ts
                or t.sell.ts == cand.buy.ts
                for t in combination
            )
            if not conflict:
                combination.append(cand)
        total = sum(t.spread for t in combination)
        if total > best_profit:
            best_profit = total
            best = combination
    return best


def optimize_electricity_trading(
    price_data: list[PricePoint],
    time_window_hours: float,
    min_spread: float,
) -> tuple[list[PricePoint], list[PricePoint]]:
    """Return (buy_points, sell_points) of the optimal non-overlapping trades."""
    if not price_data:
        return [], []
    sorted_by_price = sorted(price_data, key=lambda p: p.value)
    total_spread = sorted_by_price[-1].value - sorted_by_price[0].value
    if total_spread <= min_spread:
        return [], []
    opportunities = _find_trading_opportunities(
        sorted_by_price, price_data, time_window_hours, min_spread
    )
    if not opportunities:
        return [], []
    optimal = _find_optimal_combination(opportunities)
    return [t.buy for t in optimal], [t.sell for t in optimal]


# --- level classification (port of runPrice level logic) -------------------

LEVELS = ("VERY_CHEAP", "CHEAP", "NORMAL", "EXPENSIVE", "VERY_EXPENSIVE")


def classify_hours(
    price_data: list[PricePoint],
    time_window_hours: float,
    min_spread: float,
) -> dict[str, str]:
    """Map each slot's `date` key to a price level."""
    buy, sell = optimize_electricity_trading(price_data, time_window_hours, min_spread)
    levels = {p.date: "NORMAL" for p in price_data}
    if not buy:
        return levels

    buy_sorted = sorted(buy, key=lambda p: p.value)
    sell_sorted = sorted(sell, key=lambda p: p.value)
    buy_median = math.floor(len(buy_sorted) / 2)
    sell_median = math.floor(len(sell_sorted) / 2)

    very_cheap = {p.date for p in buy_sorted[:buy_median]}
    cheap = {p.date for p in buy_sorted[buy_median:]}
    expensive = {p.date for p in sell_sorted[:sell_median]}
    very_expensive = {p.date for p in sell_sorted[sell_median:]}

    for d in levels:
        # precedence matches the JS if/elif chain
        if d in very_cheap:
            levels[d] = "VERY_CHEAP"
        elif d in cheap:
            levels[d] = "CHEAP"
        elif d in very_expensive:
            levels[d] = "VERY_EXPENSIVE"
        elif d in expensive:
            levels[d] = "EXPENSIVE"
    return levels


# --- tuning config (defaults = a real NibePi setup, area SE4) ---------------


@dataclass
class PriceConfig:
    area: str = "SE4"
    time_window_hours: float = 12.0  # config.price.time
    min_spread: float = 35.0  # öre; config.price.min_spread

    heat_enable: bool = True  # enable_heat_s1
    # heat curve offset (°C) per level -> Nibe register 47011 (Offset S1)
    heat_offset: dict[str, int] = field(
        default_factory=lambda: {
            "VERY_CHEAP": 2,
            "CHEAP": 1,
            "NORMAL": 0,
            "EXPENSIVE": -2,
            "VERY_EXPENSIVE": -4,
        }
    )

    hotwater_enable: bool = True  # hotwater_enable
    # hot-water comfort mode per level -> Nibe register 47041 (0=eco,1=normal,2=lux)
    hotwater_mode: dict[str, int] = field(
        default_factory=lambda: {
            "VERY_CHEAP": 1,
            "CHEAP": 1,
            "NORMAL": 0,
            "EXPENSIVE": 0,
            "VERY_EXPENSIVE": 0,
        }
    )

    # indoor guard: only lower heat on expensive hours if the room is still warm
    enable_temp_guard: bool = True  # enable_temp_s1
    temp_low: float = -1.5  # temp_low_s1: allowed drop below setpoint

    @classmethod
    def from_json(cls, path: str) -> "PriceConfig":
        with open(path, encoding="utf-8") as fh:
            raw = json.load(fh)
        return cls(**raw)


@dataclass
class Recommendation:
    level: str
    heat_offset: int
    hotwater_mode: int
    current_price_ore: float


def recommend(
    level: str,
    cfg: PriceConfig,
    indoor_temp: Optional[float] = None,
    indoor_setpoint: Optional[float] = None,
    current_price_ore: float = 0.0,
) -> Recommendation:
    """Map a price level to the heat offset + hot-water mode to write.

    Mirrors priceAdjustCurve: on EXPENSIVE / VERY_EXPENSIVE the negative heat
    offset is only applied if the indoor temperature is still above
    setpoint + temp_low (or the temp guard is disabled).
    """
    heat = cfg.heat_offset.get(level, 0) if cfg.heat_enable else 0

    if heat < 0 and cfg.enable_temp_guard and indoor_temp is not None and indoor_setpoint is not None:
        if indoor_temp <= indoor_setpoint + cfg.temp_low:
            heat = 0  # too cold already -> don't cut heat

    heat = max(-10, min(10, heat))
    hw = cfg.hotwater_mode.get(level, 0) if cfg.hotwater_enable else 0
    return Recommendation(
        level=level,
        heat_offset=heat,
        hotwater_mode=hw,
        current_price_ore=round(current_price_ore, 2),
    )


# --- price source: elprisetjustnu.se --------------------------------------

_UA = "nibe-price/1.0 (+homeassistant)"  # default urllib UA gets a 403


def _fetch_day(day: date, area: str) -> list[dict]:
    url = f"https://www.elprisetjustnu.se/api/v1/prices/{day.year}/{day:%m-%d}_{area}.json"
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    with urllib.request.urlopen(req, timeout=15) as resp:
        if resp.status != 200:
            return []
        return json.load(resp)


def _to_hourly(raw: list[dict]) -> list[PricePoint]:
    """Average sub-hourly slots into hourly points (matches nibepi grouping)."""
    groups: dict[str, list[dict]] = {}
    for item in raw:
        hour = item["time_start"][:13]  # "2026-08-07T10"
        groups.setdefault(hour, []).append(item)

    points: list[PricePoint] = []
    for hour, items in groups.items():
        avg = sum(i["SEK_per_kWh"] for i in items) / len(items)
        tz = items[0]["time_start"][19:]  # "+02:00"
        start = f"{hour}:00:00{tz}"
        ts = int(datetime.fromisoformat(start).timestamp() * 1000)
        points.append(PricePoint(value=avg * 100, ts=ts, date=start))
    points.sort(key=lambda p: p.ts)
    return points


def fetch_prices(area: str, ref: Optional[date] = None) -> list[PricePoint]:
    """Fetch today + tomorrow (tomorrow may be empty before ~14:00)."""
    ref = ref or date.today()
    raw = _fetch_day(ref, area)
    try:
        raw += _fetch_day(ref + timedelta(days=1), area)
    except Exception:
        pass  # tomorrow not published yet
    return _to_hourly(raw)


# --- CLI -------------------------------------------------------------------


def _current_index(points: list[PricePoint], now_ms: int) -> int:
    idx = 0
    for i, p in enumerate(points):
        if p.ts <= now_ms:
            idx = i
        else:
            break
    return idx


def main() -> None:
    import argparse

    ap = argparse.ArgumentParser(description="nibepi price-level classifier (SE4)")
    ap.add_argument("--area", default=None, help="price area, e.g. SE4")
    ap.add_argument("--config", default=None, help="path to a PriceConfig JSON")
    args = ap.parse_args()

    cfg = PriceConfig.from_json(args.config) if args.config else PriceConfig()
    if args.area:
        cfg.area = args.area

    points = fetch_prices(cfg.area)
    if not points:
        print(f"No price data for {cfg.area}.")
        return

    levels = classify_hours(points, cfg.time_window_hours, cfg.min_spread)
    now_ms = int(datetime.now().astimezone().timestamp() * 1000)
    cur = _current_index(points, now_ms)

    print(f"Area {cfg.area}  |  window {cfg.time_window_hours:g}h  |  min_spread {cfg.min_spread:g} öre")
    print("-" * 60)
    for i, p in enumerate(points):
        lvl = levels[p.date]
        mark = " <== now" if i == cur else ""
        hh = datetime.fromisoformat(p.date).strftime("%a %H:%M")
        print(f"  {hh}  {p.value:6.1f} öre  {lvl:<14}{mark}")

    rec = recommend(levels[points[cur].date], cfg, current_price_ore=points[cur].value)
    print("-" * 60)
    print(f"NOW: {rec.level}  ->  heat offset {rec.heat_offset:+d} °C (reg 47011),"
          f" hot-water mode {rec.hotwater_mode} (reg 47041)")


if __name__ == "__main__":
    main()
