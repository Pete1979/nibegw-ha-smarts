"""Unit tests for the nibepi price-algorithm port.

Run: python -m pytest test_price_optimizer.py   (or: python test_price_optimizer.py)
"""

from __future__ import annotations

from datetime import datetime

import price_optimizer as po


def _pts(values, start_hour=0):
    """Build hourly PricePoints from a list of öre values."""
    out = []
    for i, v in enumerate(values):
        d = f"2026-08-07T{start_hour + i:02d}:00:00+02:00"
        ts = int(datetime.fromisoformat(d).timestamp() * 1000)
        out.append(po.PricePoint(value=float(v), ts=ts, date=d))
    return out


def test_no_trades_when_spread_below_min():
    pts = _pts([100, 110, 105, 108])  # total spread 10 < min_spread 35
    buy, sell = po.optimize_electricity_trading(pts, time_window_hours=12, min_spread=35)
    assert buy == [] and sell == []
    levels = po.classify_hours(pts, 12, 35)
    assert set(levels.values()) == {"NORMAL"}


def test_simple_cheap_then_expensive():
    # cheap early, expensive later, big spread -> one trade
    pts = _pts([50, 60, 200, 210])
    buy, sell = po.optimize_electricity_trading(pts, time_window_hours=12, min_spread=35)
    buy_dates = {p.date for p in buy}
    sell_dates = {p.date for p in sell}
    assert buy_dates and sell_dates
    # cheapest hour must be a buy, priciest must be a sell
    assert pts[0].date in buy_dates
    assert pts[3].date in sell_dates
    # buys and sells never overlap
    assert buy_dates.isdisjoint(sell_dates)


def test_lookahead_window_limits_sell():
    # cheap at h0, expensive only far in the future beyond the window
    pts = _pts([50] + [55] * 5 + [300])  # spike at h6, window=3 -> unreachable from h0
    buy, sell = po.optimize_electricity_trading(pts, time_window_hours=3, min_spread=35)
    # h0 cannot reach the h6 spike within a 3h window
    assert all(p.date != pts[6].date or p not in buy for p in buy)
    # widen the window -> the spike becomes reachable as a sell
    buy2, sell2 = po.optimize_electricity_trading(pts, time_window_hours=12, min_spread=35)
    assert pts[6].date in {p.date for p in sell2}


def test_levels_split_by_median():
    # two clear cheap hours and two clear expensive hours
    pts = _pts([10, 20, 200, 220])
    levels = po.classify_hours(pts, 12, 35)
    # cheapest -> VERY_CHEAP, next -> CHEAP; priciest -> VERY_EXPENSIVE, other -> EXPENSIVE
    assert levels[pts[0].date] == "VERY_CHEAP"
    assert levels[pts[1].date] == "CHEAP"
    assert levels[pts[3].date] == "VERY_EXPENSIVE"
    assert levels[pts[2].date] == "EXPENSIVE"


def test_single_buy_is_cheap_not_very_cheap():
    # floor(1/2)==0 -> the single buy hour lands in CHEAP, matching the JS
    pts = _pts([10, 200])
    levels = po.classify_hours(pts, 12, 35)
    assert levels[pts[0].date] == "CHEAP"
    assert levels[pts[1].date] in ("EXPENSIVE", "VERY_EXPENSIVE")


def test_recommend_uses_se4_config():
    cfg = po.PriceConfig()  # SE4 defaults
    assert po.recommend("VERY_CHEAP", cfg).heat_offset == 2
    assert po.recommend("CHEAP", cfg).heat_offset == 1
    assert po.recommend("NORMAL", cfg).heat_offset == 0
    assert po.recommend("VERY_CHEAP", cfg).hotwater_mode == 1
    assert po.recommend("NORMAL", cfg).hotwater_mode == 0


def test_indoor_guard_blocks_heat_cut_when_cold():
    cfg = po.PriceConfig()  # temp_low -1.5, guard on
    # room already at/below setpoint-1.5 -> negative offset suppressed
    cold = po.recommend("VERY_EXPENSIVE", cfg, indoor_temp=19.0, indoor_setpoint=21.0)
    assert cold.heat_offset == 0
    # room still warm -> full negative offset applied
    warm = po.recommend("VERY_EXPENSIVE", cfg, indoor_temp=21.5, indoor_setpoint=21.0)
    assert warm.heat_offset == -4


def test_recommend_clamps_to_plus_minus_10():
    cfg = po.PriceConfig()
    cfg.heat_offset["VERY_CHEAP"] = 50
    cfg.heat_offset["VERY_EXPENSIVE"] = -50
    assert po.recommend("VERY_CHEAP", cfg).heat_offset == 10
    assert po.recommend("VERY_EXPENSIVE", cfg).heat_offset == -10


def test_config_from_json_roundtrip(tmp_path=None):
    import json
    import os
    import tempfile

    data = {"area": "SE4", "min_spread": 40, "heat_offset": {"CHEAP": 3, "NORMAL": 0,
            "VERY_CHEAP": 5, "EXPENSIVE": -1, "VERY_EXPENSIVE": -2}}
    fd, path = tempfile.mkstemp(suffix=".json")
    try:
        with os.fdopen(fd, "w") as fh:
            json.dump(data, fh)
        cfg = po.PriceConfig.from_json(path)
        assert cfg.min_spread == 40
        assert cfg.heat_offset["CHEAP"] == 3
    finally:
        os.remove(path)


if __name__ == "__main__":
    import sys

    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {name}")
            except AssertionError as exc:
                failures += 1
                print(f"FAIL {name}: {exc}")
            except Exception as exc:  # noqa: BLE001
                failures += 1
                print(f"ERROR {name}: {exc}")
    print(f"\n{'ALL PASSED' if failures == 0 else str(failures) + ' FAILED'}")
    sys.exit(1 if failures else 0)
