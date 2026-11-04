# Open data used by Corner Case

All pulled on 2026-11-04 through the Socrata API (no key; small queries). Cached here so the
build is reproducible offline.

| File | Dataset | Publisher | Used for |
|---|---|---|---|
| `centerline_midtown.json` | Centerline (`inkn-q76z`) | NYC DOT / DoITT | Intersection locations, traffic direction (`trafdir`), lanes, street widths |
| `lpi_midtown.json` | VZV Leading Pedestrian Interval Signals (`xc4v-ntf4`) | NYC DOT | Which corners already have a walk head start |
| `barnes_midtown.json` | Exclusive Pedestrian Signal (Barnes Dance) Locations (`8kuj-2n3u`) | NYC DOT | Exclusive pedestrian phase at 7 Av & W 32 St |
| `ped_demand_midtown.json` | Pedestrian Mobility Plan Pedestrian Demand (`fwpa-qxaf`) | NYC DOT | Demand rank of cross-street segments |
| `plazas_midtown.json` | NYC DOT Pedestrian Plazas, polygon (`k5k6-6jex`) | NYC DOT | Times Square and 33rd Street plaza corners |
| `ped_counts_manhattan.json` | Bi-Annual Pedestrian Counts (`cqsj-cfgu`) | NYC DOT | Three count points on/at 7 Av that scale walker exposure |
| `atvc_manhattan_summary.json`, `atvc_7ave_timessq_hourly.json` | Automated Traffic Volume Counts (`7ym2-wayt`) | NYC DOT | 7 Av southbound volume at Times Square, 2022, 17:00 hour (1,072 veh/h) |
| `subway_entrances_midtown.json` | MTA Subway Entrances and Exits: 2024 (`i9wp-a4ja`, data.ny.gov) | MTA | Entrances within 120 m of each corner |
| `subway_ridership_2024_midtown.json` | MTA Subway Hourly Ridership: 2020-2024 (`wujg-7c2s`, data.ny.gov), 2024 totals | MTA | Walking exposure near each corner |

Crashes: `data/crashes/manhattan_ped_2023_2026.json`, NYC Open Data "Motor Vehicle Collisions -
Crashes" (`h9gi-nx95`), see `data/crashes/SOURCE.md`. Crash data is never used to build simulator
inputs; it is only used in step 1 (the map) and step 4 (scoring).

NYC Open Data is provided under the NYC Open Data Terms of Use; MTA data under the NY State Open
Data terms.

OpenStreetMap was tried for geometry (Overpass API) but the public servers were at capacity during
the build; the city's own Centerline dataset replaced it.
