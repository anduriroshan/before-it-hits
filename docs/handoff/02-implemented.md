# 02. What is implemented

Everything below is built, tested offline, and deployed (revision `before-it-hits-00029-5n2`) unless marked
otherwise. File paths are relative to the repo root. For the day-by-day history see `PROGRESS.md`.

## 1. Status against the original plan

Legend: ✅ built and live, ◐ partly built, ☐ not built.

| ID | Feature | Status | Notes |
|---|---|---|---|
| F1 | Draw an area | ✅ | Box tool, presets, name a place, whole-city question with no box. Max box 120 km² |
| F2 | Ask in plain language | ✅ | Scenario agent, conversation memory, topic routing. English only (☐ Malayalam) |
| F3 | Bring in the forecast | ✅ | Google Weather API, 48 h, several window lengths; the figure is only used when the question is about what is coming |
| F4 | Where water collects | ✅ | `analyse.py` + `hydrology.py` (numba), 10 m grid, relative rank 1 to 5, fill order |
| F5 | Validated depth layer | ✅ Kochi only | KSDMA rasters shown as published, with agreement stated against its baseline |
| F6 | Heat by area | ✅ | Forecast heat, five weather fields, Landsat surface-heat layer with hottest/coolest places. ☐ "people in the hottest parts" |
| F7 | Who is nearby | ✅ | People in the worst spots (by area), population density field. **Since 15 Oct from a modelled 100 m grid (GHS-POP 2020) scaled to the published total**, placed on buildings inside a spot; checked against Singapore's census (see 05) |
| F8 | What gets cut off | ◐ | Roads through pooling spots + a rain-driven playback ✅. "Areas cut off" is **deliberately not claimed**: it needs somewhere to be cut off *from* (critical places, L13, not built); without that it returns graph noise (~400 "islands" on a 35 km² box) |
| F9 | Prepare within what the city has | ✅ | Ranked, capped by the real budget line; has a real "nothing to buy" branch |
| F10 | Before / after | ✅ | Computed by the engine, always labelled a best case |
| F11 | Write it up | ✅ | Narrator briefing, checked. ☐ Malayalam |
| F12 | Say what we don't know | ✅ | Resolution caveat and per-layer confidence on every selection; limits on every run |
| F13 | What's being reported | ✅ | News tab and news chat. Cross-links both ways (built 15 Oct, **not yet deployed**): a screening answer offers "What is being reported in <place>?"; a news claim whose cited headline names a place offers "Screen <place>". The index stays city-wide: a place only picks the headlines that name it, and `place_note` says whether any did |
| F14 | Watch and warn | ☐ | Cut on purpose (first on the cut list) |
| F15 | Replay what happened | ✅ | 28 May 2024 Kochi cloudburst; honest negative, shown in full |
| F16 | Test without waiting for rain | ☐ | Harness and synthetic worlds not built |

Layers (L): L0 basemap ✅, L1 DTM ✅, L2 water ✅, L3 flow and depressions ✅, L4 buildings ✅, L7 roads ✅,
L8 land cover ☐, **L9 surface temperature ✅ (Landsat, new 14 Oct)**, L10 and L11 census and population grid ◐
(city totals only; see density), L12 modelled population ✅, L13 critical places ☐, L14 what the city has ✅,
L15 rainfall forecast ✅, L16 observations ◐ (NEA gauges, Singapore only), L18 official hazard maps ✅ (Kochi).

## 2. Data staged (per city, once)

`data/regions/<city>/` is staged by `engine/tools/` and baked into the container image.

| File | What | Kochi | Singapore |
|---|---|---|---|
| `dtm.tif` | DeltaDTM v1.1 terrain, native 1 arcsec (about 30 m), cap value 30 m | 901×901 | 1621×973 |
| `water.fgb`, `waterways.fgb` | OSM water | ✅ | ✅ |
| `roads.fgb` | OSM roads (30,852 in Kochi) | ✅ | ✅ |
| `buildings.fgb` | OSM footprints (154,506 in Kochi; near-complete in Singapore) | ✅ | ✅ |
| `official/` | KSDMA depth rasters, 6 return periods (10 to 500 yr), about 92 m | ✅ | none exists |
| `lst.tif` | **Landsat surface-temperature median composite**, on the terrain's own grid | 55 scenes, Jan to Apr | 56 scenes, all months |
| `pop.tif` | **GHS-POP 2020 population count**, 100 m source resampled (through people per m²) onto a 3 arcsecond grid; staged total checked against the source's own | 2.47 M in the box, scale to published 0.857; 34% of its people in cells with no mapped building | 6.26 M, scale 0.946; 19% |
| `region.json` | bounds, presets, sources, licences, staged dates, population, news queries, preparedness budget, `surface_heat` provenance | ✅ | ✅ |

Staged data is gitignored (large, third-party, reproducible) except **each city's `region.json`** (Singapore's
was whitelisted in `.gitignore` on 15 Oct and is waiting to be committed) and the OSM vectors that were
committed early. Singapore's rasters and vectors stay out of git on purpose.

## 3. The engine (`engine/bih_engine/`): deterministic, no model

| Module | Does | Notes |
|---|---|---|
| `store.py` | `Region`: manifest, windowed reads of terrain and FlatGeobuf vectors | a bbox read of a 7 km² box takes 54 ms against 713 ms for the whole region |
| `terrain.py` | windowed DTM read onto a local metric grid; masks the 30 m cap; `despike_low` | |
| `hydrology.py` | depression filling, D8 flow, accumulation (numba) | a depression shallower than the terrain error is dropped |
| `analyse.py` | the screening: pooling spots, rank 1 to 5, fill order, `rain_to_fill_mm`, nearest named road, road flood thresholds | **never returns a depth** from our model |
| `osm.py`, `routes.py` | OSM parsing and rasterising; road graph built **per request** (not stored) | bridges and tunnels are excluded from "flooded": water does not run along them |
| `official.py` | clips the published raster to the box; bands for display; **enrichment** vs chance | shown under the screening, never merged |
| `exposure.py` | people in the worst spots. Grid method (`popgrid.people_in`) where a city has `pop.tif`; the older published-total-over-footprints method is the fallback | by area, never per home. `people_in_box` comes from the same grid, so the layer, the Details card and the agent agree |
| `popgrid.py` | **the population grid**: `raster` (density for a box, area-averaged), `people_in` (a cell's people on its mapped buildings by footprint; spread evenly where a cell has none), `density_for` (the one door: grid if staged, else footprints) | scaled so the staged box adds up to the published total; a modelled zero draws as nothing |
| `preparations.py` | greedy ranking under the city's real budget; the **ordinary-day gate** | gate uses IMD daily rainfall classes (light rain up to 15.5 mm); returns ₹0 and says why |
| `forecast.py` | Google Weather API rainfall for a box, 48 h, peak windows | a tool, not a model |
| `heat.py` | heat index, temperature, humidity, wind; five classification levels; peak | |
| `weather_grid.py` | 16 points × 24 hours time cube for the map fields; five variables; stable colour bounds | cached 10 min; a missing hour stays `None`, never 0 |
| `surface_heat.py` | **the Landsat picture of a box**: block-averaged raster, hottest/coolest places, city-scale ramp | about 0.3 s a box; see 05 for what it may claim |
| `density.py` | the footprint-only density raster (vectorised numpy), now the **fallback** for a city with no grid | about 150 m cells for a box, about 600 m for a city; null where there is no footprint; also owns the cell-size rule `popgrid` uses |
| `air_quality.py` | Google Air Quality API: current, hourly forecast, readable units | provider's figures, never rewritten |
| `rainfall_obs.py` | live NEA rain gauges (Singapore, keyless) | observed rain, not a forecast |
| `replay.py` | scores the screening against a real past storm | |

Staging tools (`engine/tools/`): `fetch_terrain.py`, `fetch_deltadtm_kochi.py`, `fetch_osm_kochi.py`,
`stage_region.py` (Overpass → FlatGeobuf, writes `region.json`), `stage_official.py` (KSDMA),
`fetch_lst.py` (**Landsat via Earth Engine**), `fetch_population.py` (**GHS-POP via Earth Engine**),
`validate_population_sg.py` (Singapore census check, writes its result into the manifest), `replay_kochi.py`.

Measured: 7 km² warm total **0.78 s**, 35 km² about 1.3 s, 75 km² about 1.6 s. Cold start about 8 s, which is
why the service runs with `--min-instances 1`.

## 4. The agent layer (`engine/bih_agents/`)

| Agent | File | Model? | Job |
|---|---|---|---|
| A1 Coordinator | `coordinator.py` | no | holds the message and history, **routes by topic**, runs the specialists, keeps the trace; never raises for a model failure |
| A2 Scenario | `scenario.py` | fast | question → parameters (mode, rainfall, scope, intent, **topic**, place). A rainfall figure only counts if its exact words are in the question |
| A3 Forecast | inside `coordinator.py` | no | live forecast, only when the question is about what is coming |
| A4 Engine | `bih_engine` | **no** | the only thing that produces a figure |
| A5 Planner | `planner.py` | fast | a short plain read on the ranked preparations, trade-offs only; may use no number the list lacks |
| A6 Narrator | `narrator.py` | fast | writes separate claims from the facts bundle and nothing else |
| A7 Verifier | `verifier.py` | careful | arithmetic check in code + claim check by model; **fails closed** |
| A8 Guide | `guide.py` | fast | answers questions about the product from a written reference; runs no analysis |
| A13 News | `news.py`, `news_agent.py` | fast | live GDELT headlines per hazard; a news chat that cites only the headlines it was shown |
| Road outlook | `road_outlook.py` | fast | qualitative, region-specific read on the road timeline, grounded in cached news |
| (support) | `facts.py`, `hazards.py`, `llm.py` | | facts bundle builder; the one list of live hazards; the one place that talks to Gemini |

**What a drawn box does (15 Oct).** `predictBox` asks for the rain prediction at once (forecast, engine, briefing;
the pooling stays on the map and nothing hides it). The same turn shows an "options" row immediately and the
answer carries a copy: one chip per entry in `hazards.py` that has an `option` (heat, wind and direction, air
quality, rain re-run), plus "Who lives here" and "Ask about this area". Heat, wind, air and people also turn on
their map layer (`OPTION_LAYER`), so the answer can be watched. A hazard added to `hazards.py` with an `option`
gets its chip with no change to `app.js`. After a non-rain answer the rain-specific follow-ups are not shown.

**Wind (15 Oct).** A topic like the others: `heat.py` parses `wind_gust_kmh` and `wind_dir_deg` (Google's
`degrees` is where the wind comes FROM), `facts.add_wind` writes now / 6, 12, 24 h on / strongest hour, each with
`blowing_from` and `blowing_towards` in eight compass words, `coordinator._add_wind` fetches it, the Narrator has a
rule. The map layer (`weather_grid.field(kind="wind")` also returns `dirs`) is a vector field in `app.js`: colour for
speed, a grid of arrows and animated streaks for direction, interpolated by u/v components never by angle
(`windUV`), following the one clock. Streak pace has a floor so light winds still read as moving; colour and arrow
length carry the speed, and the layer note says so.

**Topic routing (14 Oct).** The Scenario agent returns `topic`: `flood`, `population`, `heat`, `air_quality`, `wind`
or `overview`. A population, heat or air-quality question runs only what it needs (about 4 steps, no flood
engine, no other hazards). `flood` and `overview` run the full pipeline and add heat, surface heat and air
quality to the bundle. A whole-city question runs the engine over each preset area and ranks them.

**Models** are named by role and env-overridable: writing `gemini-3.5-flash` (`BIH_FAST_MODEL`), checking
`gemini-3.8-flash` (`BIH_CAREFUL_MODEL`).

## 5. The service (`service/app.py`)

| Route | Purpose |
|---|---|
| `GET /api/health` | liveness. **Not `/healthz`**: Cloud Run's frontend swallows that exact path |
| `GET /api/regions`, `/api/region`, `/api/config` | city list, one city's manifest, the browser Maps key |
| `POST /api/analyse` | the screening for a box (deterministic; no model) |
| `POST /api/ask` | the agent run, streamed as newline-delimited JSON (`step`, `place_needed`, `done`, `error`) |
| `POST /api/forecast`, `GET /api/rainfall-now` | rainfall forecast for a box; live gauges where a city has them |
| `POST /api/road-outlook`, `POST /api/preparations` | the road read; the costed plan |
| `GET /api/replay` | the precomputed 28 May 2024 replay |
| `GET /api/heat`, `/api/air-quality-current`, `/api/air-quality` | conditions **for the box or the city**, each carrying its `scope`; AQ tile URL template |
| `GET /api/weather-grid` | the time cube for one variable, over the box or the city |
| `GET /api/surface-heat` | the Landsat picture; places only when a box is drawn |
| `GET /api/population-density` | the density raster, cached per area |
| `GET /api/news`, `POST /api/news-ask` | headlines per topic; the news chat |

Start-up warmers (off with `BIH_NO_NEWS_WARM`, `BIH_NO_DENSITY_WARM`): news for each city, density for each city.

## 6. The web app (`web/`)

No build step: `index.html`, `app.js` (about 3,200 lines, section-commented), `style.css`.

- **Map shell.** Google Maps (basemap display only). Modal tool palette: **Move / Box / Erase**, a label-density
  control (Full / Clean / Minimal), a **Layers** button. Keys `V` `B` `E` `L` `Esc`, `Space` for play.
  Drag-to-draw is built on native pointer events, not the Maps drawing library. A map-failure fallback keeps the
  analysis reachable.
- **Side panel, six tabs.** **Agent** (a real chat) / **Rain** / **Details** / **Heat** / **Air** / **News**.
- **Agent tab.** Streamed trace folded to one line per answer ("Checked by the verifier · 5 steps · 23 s"),
  fact chips taken from the engine's own output, follow-up chips, an inline *What if it rains more?* slider that
  runs the hypothesis through the same agents, Stop, Ask again, Copy, conversation memory (last six turns),
  named places resolved by the browser (presets, then geocoder, switching city if needed), the Guide for "what
  does this mean". Removed claims are shown with the rule that removed them.
- **Rain tab.** Plain headline ("Little or no rain · 1.6 mm over 48 h"), hourly chart in the city's time zone,
  live NEA gauges where available, the manual what-if override.
- **Details tab.** Three-sentence summary, then collapsible cards: worst spots, people, **roads that go under
  first** (with the playback readout, hourly rain strip and what-if storm length), **what the city can do**
  (costed plan or the ordinary-day card, before/after), the government flood map, how sure are we, the replay.
- **Heat tab.** Scope line (box or city), current heat index and level, peak forecast, hourly chart, then
  **"Where the ground runs hotter"**: the box's typical surface range, hottest and coolest places by nearest named
  road, a *Show* button that pans the map and rings the place, and a "what this is and is not" fold.
- **Air tab.** AQI, category, dominant pollutant, concentrations with units, hourly forecast, the provider's
  health recommendations, scope line.
- **News tab.** Headlines per topic, folds into its title bar, a news chat underneath.

## 7. Map layers (the Layers panel)

Nine layers stack with per-layer opacity. Every layer carries a one-line note saying what it is drawn from, and
every active field gets a colour scale on the right built from the same ramp as the picture.

| Layer | Source | Resolution | Follows the box? | Notes |
|---|---|---|---|---|
| Heat index (forecast) | Weather API, 16 points × 24 h | 9.3 km city, 1.5 km box | ✅ | animated; the note states the box's own spread |
| **Surface heat (satellite)** | Landsat composite | sensor about 100 m, grid 30 m | ✅ | typical season, surface not air, city-scale colours |
| Temperature, Humidity, Rain per hour | Weather API | as above | ✅ | animated |
| **Wind speed** (with direction) | Weather API, speed and direction per point and hour | as above | ✅ | colour = speed; arrows + moving streaks = the way the air is going; animated by the clock |
| People per km² | GHS-POP 2020 grid scaled to the census total | about 150 m box / 600 m city | ✅ | approximation by area; null where the grid holds nobody |
| Air quality | Google's own heatmap tiles | provider's | n/a | shown as published; see 03 (unverified on the live site) |
| Traffic | Google TrafficLayer | | n/a | display only |

**One clock** (the thin bar at the bottom of the map) drives everything time-shaped: weather fields morph by
linear interpolation between forecast hours, and the road playback lights roads at the rainfall fallen by the
same moment. Scrubbing never re-runs the engine.

## 8. Evidence and honesty checks that exist

- KSDMA agreement, 1.25 to 3.26× over chance, with a non-discriminating gate above 40% inundated.
- The 28 May 2024 replay: **6/8 localities flagged but a flag covers 90% of the area, enrichment 0.83×: not
  discriminating.** Vyttila is an expected miss (tidal and drainage flooding). Reported as the headline.
- Verifier: all eight planted failure drafts caught, clean one passed, 2.9 s a draft at low thinking.
- Surface heat checked against vegetation before it was built on: Spearman −0.33 (Kochi) and −0.32 (Singapore);
  the most-vegetated quarter of pixels is 1.6 °C and 3.3 °C cooler.
- News agent resisted a planted prompt-injection headline.

## 9. Tests: 405, all offline

| File | Tests | Covers |
|---|---|---|
| `test_agents.py` | 33 | coordinator, narrator, verifier, facts bundle |
| `test_conversation.py` | 33 | history, named places, guide, scenario settle |
| `test_preparations.py` | 27 | ranking, budget, quiet-day gate, planner note |
| `test_surface_heat.py` | 20 | raster, ranking gates, facts bundle, coordinator step |
| `test_news_agent.py` | 23 | topic routing, citation, deletion of uncited claims, a claim's verified place |
| `test_analyse.py` | 13 | screening, no depth leak |
| `test_weather_grid.py` | 12 | time cube, null hours, colour bounds |
| `test_news_cache.py` | 12 | cache, pacing, pending |
| `test_air_quality.py` | 11 | parsing, units |
| `test_density.py` | 12 | raster, null, p95 cut, people in an area (not the city total) |
| `test_popgrid.py` | 12 | grid scaling, people follow the roof, open ground holds nobody, unmapped cells spread evenly, exposure and fallback |
| `test_wind.py` | 12 | the 180 degree trap (from vs towards), digit-free prose, traceable figures, the topic skips the flood engine, every hazard has an option; plus 3 in `test_weather_grid.py` / `test_heat.py` |
| `test_forecast.py` / `test_official.py` | 10 / 10 | forecast peaks; official clip and enrichment |
| `test_hydrology.py` / `test_replay.py` | 9 / 9 | depression fill; replay scoring |
| `test_osm.py` / `test_routes.py` | 8 / 8 | |
| `test_exposure.py` / `test_heat.py` | 7 / 7 | |
| `test_despike.py` / `test_news.py` | 6 / 6 | |
| `test_road_outlook.py` | 5 | |
| `test_rainfall_obs.py` / `test_terrain_local.py` | 4 / 4 | |

The agent tests **stub both models out**: what is worth testing is the part that still holds when the model is
wrong. There are no browser or service-level tests; UI is verified by driving headless Chrome (07).

## 10. Deployment

Cloud Run `before-it-hits`, `asia-south1`, 2 GiB, 2 CPU, 1 to 5 instances, concurrency 20. Built from the
Dockerfile by Cloud Build (no local Docker). Three env vars on the service: `MAPS_API_KEY`, `WEATHER_API_KEY`,
`GEMINI_API_KEY`. The staged regions ship inside the image (`COPY data/regions`).

## 11. Notable fixes already made (so they are not re-made)

- A render error in the Heat tab was being reported as "the provider is unavailable" (a false claim about the
  world). Render faults are now separated and reported as a fault in the page.
- Roads and the preparation plan could be computed on two different rainfalls (the forecast re-seeds the slider
  mid-analysis). The rainfall is now pinned before anything async.
- "The plan can say nothing": a light-rain day returns an ordinary-day card instead of a shopping list.
- Weather layers never followed the box; now they do, and say how little a forecast varies inside a small box.
- A population question ran the flood engine, heat and air quality and printed "What-if · 0 mm". Topic routing
  fixed it; the Verifier's hard-coded "caveat must mention drains" check now applies only to flood answers.
- Surface heat was first described as "30 m"; the thermal sensor is about 100 m. Corrected everywhere.
- Mojibake across `app.js` from a PowerShell round trip; repaired. **Do not repeat** (see README rule 4).
- **A population question reported the whole city's population as the box's** ("roughly 2,117,990 people" for
  a 19 km² box, live on rev 00029). `density.raster` returned the city total as `people_total` and the
  topic path labelled it "in the whole box". Now `people_in_area` (the footprint share, two figures) is what
  the answer uses, and the city total is `city_people`. Found on the 15 Oct live check; fixed locally, **not yet deployed**.
