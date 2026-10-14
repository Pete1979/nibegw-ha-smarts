# Entities

## From the Nibe Heat Pump integration

The smarts reference these entities directly. The names below are what the integration
creates for an **F730**; enable them under *Settings → Entities* (most start disabled).
Enabling an entity reloads the integration, so all Nibe entities go `unavailable` for a
minute — that's normal.

If your model names an entity differently, search-and-replace it across
`homeassistant/` (and `grafana/` if you use it).

### Read

| Entity | Register | Used by |
|---|---|---|
| `sensor.bt1_outdoor_temperature_40004` | BT1 outdoor | indoor (outdoor block), frequency curve |
| `sensor.bt7_hw_top_40013` | BT7 hot water top | solar, hot-water priority |
| `sensor.bt6_hw_load_40014` | BT6 hot water charge | hot-water auto-lux |
| `sensor.bt50_room_temp_s1_40033` | BT50 room sensor | default room temp in `nibe_site.yaml` |
| `sensor.eb100_bs1_air_flow_40050` | BS1 airflow (m³/h) | fan regulation feedback |
| `sensor.eb100_bt16_evaporator_temp_40020` | BT16 evaporator | fan (pause when ≤ 0 °C) |
| `sensor.compressor_frequency_actual_43136` | compressor Hz | fan low mode |
| `sensor.alarm_45001` | alarm code | fan (pause during defrost = 183) |
| `sensor.prio_43086` | current priority | dashboard |
| `sensor.compr_in_power_43141` | compressor input power | Grafana |
| `sensor.bt2_supply_temp_s1_40008`, `sensor.eb100_ep14_bt3_return_temp_40012` | supply / return | Grafana |
| `sensor.hp_consumed_energy_due_to_{heating_41850,hot_water_41848,ventilation_41846}` | lifetime kWh | dashboard, Grafana, Energy dashboard |

### Write

| Entity | Register | Written by |
|---|---|---|
| `number.heat_offset_s1_47011` | heat curve offset S1 | write package (indoor + price + solar) |
| `select.hot_water_comfort_mode_47041` | HW comfort mode | write package (price / solar), needs `nibe_hw_write_enable` |
| `select.temporary_lux_48132` | temporary lux | hot-water pyscript, dashboard buttons |
| `number.exhaust_fan_speed_normal_47265` | fan speed normal (%) | fan pyscript |
| `number.degree_minutes_16_bit_43005` | degree minutes | indoor DM reset |
| `number.max_comp_freq_47104` | max compressor frequency | frequency pyscript |
| `switch.allow_additive_heating_47370` | allow immersion heater | frequency pyscript |

## Site aliases (`nibe_site.yaml`)

Everything that isn't the pump goes through these, so the rest of the code never
needs editing:

| Alias | Point it at | Needed for |
|---|---|---|
| `input_number.nibe_setpoint` | (helper, defined in the file) | indoor, price guard, solar ceiling |
| `sensor.nibe_room_temp` | your room temperature — ideally a mean of several rooms | indoor, price guard, solar |
| `sensor.nibe_spot_price` (+ `source` attribute) | your Nordpool sensor | price control, solar price gate |
| `sensor.nibe_grid_power` | net grid power in kW, **negative = exporting** | solar |
| `sensor.nibe_solar_forecast_remaining` | kWh of solar still expected today | solar |
| `sensor.nibe_co2` | indoor CO₂ (ppm) | fan CO₂ boost |

## Published by the smarts

| Entity | Meaning |
|---|---|
| `sensor.nibe_indoor_offset` | indoor compensation offset (°C) |
| `sensor.nibe_price_level` / `_level_num` | VERY_CHEAP … VERY_EXPENSIVE / −2 … +2 |
| `sensor.nibe_price_current` | current price (öre/kWh) |
| `sensor.nibe_price_heat_offset` / `_hw_mode` | what price control asks for |
| `sensor.nibe_solar_surplus` / `_active` / `_heat_offset` / `_hw_mode` / `_hw_reserve` | solar state |
| `sensor.nibe_total_heat_offset` | **final** offset → register 47011 |
| `sensor.nibe_hw_mode_target` | **final** HW mode (0/1/2) → register 47041 |
| `sensor.nibe_heat_block_reason` | `outdoor block` / `indoor overshoot` / `none` |
| `sensor.nibe_fan_status`, `sensor.nibe_hw_status`, `sensor.nibe_freq_status` | per-feature status |
| `sensor.nibe_*_debug` | `ok` or `error` + message from each pyscript module |
