# 04. Architecture

## The dividing line

> **Static geography is staged once. Only live conditions come from an API at request time.**

Staging is slow, rate-limited and breakable (Overpass allows 4 slots and returned 504 twice; a public DEM
clipping service allows 50 calls a day; 4TU went down for maintenance mid-build). None of that belongs in a
request a judge is watching, so none of it is. The request path reads local files and calls a few small,
fast, genuinely-hourly APIs.

```
STAGED ONCE (engine/tools/, needs gcloud + network)        PER REQUEST (stateless, FastAPI on Cloud Run)
  DeltaDTM terrain  ── Earth Engine ──▶ dtm.tif    ─┐
  OSM water/roads/buildings ─ Overpass ▶ *.fgb     ─┤      windowed read of just that box
  KSDMA flood depth ── downloads ─────▶ official/  ─┼───▶  fill depressions, D8 flow, accumulate, rank
  Landsat 8/9 thermal ─ Earth Engine ─▶ lst.tif    ─┤      clip the official layer, compare
  provenance, budget, population ─────▶ region.json ─┘      build the road graph, flag routes
                                                            read the surface-heat picture of the box
LIVE AT REQUEST TIME                                         │
  Google Weather API (rain, heat, 16-point grid)             ▼
  Google Air Quality API                              facts bundle ─▶ Narrator ─▶ Verifier ─▶ screen
  Gemini (scenario, narrator, verifier, guide, news)
  GDELT headlines (cached), NEA rain gauges (Singapore)
  Maps JS (browser): basemap, traffic, AQ tiles. Display only.
```

## Components

### Engine (`engine/bih_engine/`)
Pure functions over staged files plus a few small live fetchers. No model. See `02-implemented.md` for the
module table. Key facts: analysis grid **10 m** (`res_m`); terrain source resolution about 30 m, resampled to a
local metric grid; `hydrology.fill_and_route` is numba-compiled (the Dockerfile warms the numba cache at build
time so the first box does not pay for it); the **road graph is built per request** with networkx rather than
stored (a drawn box is arbitrary, so a city graph would have to be clipped anyway, and building costs about what
clipping does); `Region` accepts a `gs://` root as well as a local path, but the data is baked into the image.

### Agents (`engine/bih_agents/`)
`coordinator.run(...)` is the entry point. It takes `question`, `bbox` (may be `None`), `region`, `slider_mm`,
`history`, `area_label`, `place_resolved`, and an `emit` callback for the trace. It returns one dict whatever
happens: `kind` is `analysis`, `explain`, `needs_area`, `place_needed` or `city_overview`. See 05.

### Service (`service/app.py`)
One FastAPI app. Serves the static `web/` directory and every `/api` route (table in 02). Highlights:

- `/api/ask` streams newline-delimited JSON (not SSE: the browser reads it with a plain `fetch`, and
  `EventSource` cannot POST). The engine runs in a worker thread that pushes events to a queue; the first line is
  sent only after the request validated, so **every failure after that is an in-band `error` event** (the status
  is already 200).
- `scope_for(region, raw_bbox)` decides box versus city for the conditions endpoints and returns the `scope`
  object the UI prints. `parse_bbox` treats invalid input as "no box", not an error.
- Box limits: `MAX_AREA_KM2 = 120`; the box must be inside the staged region.
- CORS is wide open (`*`); the Maps key is the browser's, the other two stay on the server.

### Web app (`web/`)
Plain JavaScript, no build step, one `state` object plus a few module-level registries. `app.js` is organised
in commented sections: tools; **data layers** (definitions, ramps, panel); **the two kinds of field** (scatter and
raster sources, inverse-distance weighting); **the overlay** (a canvas `OverlayView`); the colour scales; **one
clock**; map and labels; analysis; roads over time (F8); preparations (F9); who is nearby (F7); **heat and air**
(scope, surface heat); air quality; news; tabs; the Rain tab; **the agent chat**; one agent turn; sending.

Patterns that matter:

- **Fields are canvases.** A layer is a small raster (72×72 for weather, the density or surface-heat grid
  otherwise) painted through a 256-step colour lookup and **scaled up by the browser**, which is both the
  smoothing and the honest picture. One `drawImage` per frame lets a field animate at 60 fps.
- **Weights are precomputed.** Inverse-distance weights depend only on point positions, so an animation frame is
  one weighted sum per cell.
- **One clock** (`clock`, `stepTo`, `setLine`) drives fields and road playback. Each field is aligned to the bar
  by its own first timestamp.
- **Loaders take tickets** (`++l.token`, `seq.forecast`, `seq.now`, `conditionsFor`). Clearing the area voids
  any in-flight load, so a late answer from an earlier area cannot reappear after an erase.
- **Layers that follow the box** are refreshed by `refreshAreaLayers()` when a box is set or erased
  (`layerArea(kind)` decides which area a layer asks for). Weather and surface-heat layers swap in place when the
  new data lands, so the picture and the time bar do not blink off.
- **`draw(render, intoId)`** wraps every render so a bug in our drawing is never reported as a provider outage.
- **Pooling and road drawing** use Google's `Data` layer; spots are coloured by rank (`POOLING_FILL`), roads by
  severity (`ROAD_SEV`).

## Data layout (`data/regions/<city>/`)

Terrain is a cloud-optimised GeoTIFF read by windowed range request; vectors are **FlatGeobuf** (a packed
Hilbert R-tree, so a bbox read fetches only the bytes it needs). `region.json` is the manifest: bounds, title,
presets, `terrain` provenance, `vectors` counts and licences, `not_available` (the honest gaps), `official`,
`population`, `news_query`/`news_place`, `preparations` (the city's documented budget line and measures),
`live_rainfall`, `timezone`, `surface_heat`. `engine/regions.py` is the human-edited source for city definitions;
`stage_region.py` rewrites the manifest from it and **preserves** the keys other tools own (`official`,
`surface_heat`). Run it with `--manifest-only` to change copy without touching Overpass.

## Caches and warmers

| What | Where | Lifetime |
|---|---|---|
| Weather time cube (per rounded bbox, size, hours) | `weather_grid.py`, memory | 10 min |
| News per city/topic | `news.py`, memory | fresh 30 min; stale served instantly and refreshed behind the request; a failure remembered 90 s; calls paced 5.5 s apart |
| Density raster per area | `_density_cache` in `app.py` | session, 64 entries |
| Forecast, heat, AQ | not cached | provider is fast |
| Start-up warm | news and density for each city | background threads |

## External dependencies and how each fails

| Dependency | Used for | If it fails |
|---|---|---|
| Google Weather API | rain forecast, heat, weather grid | rain falls back to the slider and says so; the heat tab says it is unavailable |
| Google Air Quality API | Air tab, AQ in bundles | the Air tab says so; the bundle simply lacks it |
| Gemini (3 models by role) | scenario, narrator, verifier, guide, planner, news, road outlook | screening still shown; briefing "unavailable"; **nothing unchecked is ever published** |
| GDELT | news | answers `pending` (HTTP 202) rather than hold a request; the News tab says "the index is slow today" |
| NEA data.gov.sg | Singapore live gauges | the panel is absent |
| Maps JS | basemap, traffic, AQ tiles | a visible reason on a fallback panel; the analysis stays reachable |
| Overpass, Earth Engine, KSDMA | **staging only** | never on the request path |

## Keys (three, not interchangeable; values only in the gitignored `.env.local`)

| Name | Used by | Restriction |
|---|---|---|
| `MAPS_API_KEY` | the **browser** (Maps JS, geocoding, AQ tiles); served via `/api/config` | documented as referrer-restricted to `localhost:8080` and the Cloud Run URLs (not re-inspected on 14 Oct). A referrer-restricted key **cannot be used from the server** (no Referer header) |
| `WEATHER_API_KEY` | server: Weather API, Air Quality API | created restricted to `weather.googleapis.com`. `air_quality.py` falls back to `MAPS_API_KEY` if this one is absent. The AQ API **answered correctly from Cloud Run on 14 Oct**, but which key authorised it, and the key's current restriction list, were **not inspected**: check in the GCP Console before relying on either |
| `GEMINI_API_KEY` | server: Gemini | restricted to `generativelanguage.googleapis.com`. A key created now cannot call `gemini-2.5-*`; the 3.x line is used |

## Performance (measured)

Screening, warm: 7 km² **0.78 s**, 35 km² about 1.3 s, 75 km² about 1.6 s. Density: 0.2 s a box, about 5 s the
first time for a city (then cached). Surface heat: about 0.3 s a box, 0.08 s a city. Weather grid: about 3.6 s
(16 parallel requests × 24 hours), cached 10 min. A full agent run is about 10 s, mostly two model calls; a
topic run (population, heat, air) is about 4 to 8 s. Cold start about 8 s, which is why `--min-instances 1`.

## How to add things

**A city.** Add an entry to `engine/regions.py` (bbox, title, timezone, presets, news queries, population block
with a measured `built_footprint_m2`, `surface_heat` months, `population_grid` year). Then, with `gcloud`
authenticated: `fetch_terrain.py <city>`, `stage_region.py <city> --buildings`, optionally `stage_official.py`,
`fetch_lst.py <city>`, `fetch_population.py <city>`. No engine change: the screening is terrain physics, not
city-specific. Mind the `.gitignore`, which whitelists only Kochi's and Singapore's `region.json`; a new city
needs its own two lines. A city with no `pop.tif` falls back to spreading its published total over footprints.

**A map layer.** Add an entry to `LAYER_DEFS`, a ramp in `RAMPS`, a `loadXField` loader and a branch in
`createLayer`; add its kind to `layerArea` and to `refreshAreaLayers` if it follows the box; give it a one-line
note that says what it is drawn from; add a line to the Guide reference in `guide.py`.

**A hazard.** Today a hazard is: an engine reader, a facts section (`facts.add_*`, digit-free prose), a topic in
the Scenario schema and prompt, a branch in `_run_topic`, a Narrator rule and a Verifier `certainty` line, a tab
and/or a layer, an entry in `hazards.py`, and tests. The 13 Oct plan proposed a formal registry
(`screen`, `facts`, `limits` per hazard); it was **not built** (see 03).

## Gotchas already hit

- **Google Maps empties the element it is built on.** Anything floating over the map must be a *sibling* of
  `#map`, not a child (`#mapwrap` contains `#map`, `#toolbar`, `#legend`, `#fieldscale`, `#tl-float`).
- **`[hidden]` loses to any id-level `display` rule.** `style.css` carries a global
  `[hidden] { display: none !important; }`. Keep it.
- **Never gate UI chrome behind a map event.** Everything is set up synchronously after the map is constructed.
- **Drag-to-draw** uses native pointer events with `setPointerCapture` and Web Mercator maths; the Maps drawing
  library never worked here.
- **Cloud Run swallows the exact path `/healthz`.** Health is `/api/health`. Do not rename it.
- **`python:3.12-slim` lacks `libexpat1`**, which the GDAL inside the rasterio wheel needs. The Dockerfile
  installs it.
- **`.gcloudignore` must exist**, or gcloud falls back to `.gitignore` and the image ships with no data.
- **Earth Engine can return a grid one row or column longer** than asked; `fetch_lst.py` crops it and refuses a
  larger mismatch.
