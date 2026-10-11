# Before It Hits

**Live: https://before-it-hits-z5r5gsxkza-el.a.run.app** — pick a city, draw a box, or click a preset area.

Two cities are staged: **Kochi** (where an authority publishes a depth map to check against) and **Singapore** (where none does). Adding a city is a config entry plus the staging tools — no engine change.

Cities get a rainfall forecast and issue a warning for the whole city. The water still gathers in
the same low spots it gathered in last year. Draw a box on the map and ask what happens *there*:
which places the ground traps water in, why, which roads run through them, and what the official
depth map says about the same ground.

---

## The idea: two tiers that explain each other

A 30 m elevation model carries about half a metre of vertical error — the same size as the flood
depths we would otherwise be quoting. So what the product claims is split in two, and the two are
shown side by side and never merged into one number.

| | **Screening tier** | **Validated tier** |
|---|---|---|
| Works | on any box, anywhere | only where an authority publishes |
| Says | *which* low points fill first, and why | *how deep*, officially |
| Output | relative pooling rank 1–5, catchment, fill order | published depth by return period |
| Source | our terrain physics, computed per box | KSDMA / UNEP, displayed as published |

**We never emit a depth in metres from our own model.** Depth is used internally only, to flag
depressions shallower than the terrain error. A test asserts no depth field reaches the API.

## Does the screening actually find anything?

Our top-ranked spots fall inside the authority's flood extent **1.25–3.26× more often than chance**
(1-in-50-year layer):

| Area | Enrichment |
|---|---|
| Palarivattom | 2.78× |
| Vyttila | 1.79× |
| Edappally | 1.52× |
| Kakkanad | 1.25× |

Reported as enrichment over the inundated share, never as raw overlap: at a 500-year return period
~68% of a Kochi box is inundated, so raw overlap would look fine while meaning nothing. Above 40%
inundated the result is flagged as non-discriminating and the UI says so.

This is *agreement*, not accuracy. The official layer is ~92 m and models river and backwater
flooding; the screening is 10 m and finds local rainfall ponding. They answer different questions.

## What this will not claim

Written down before building, because each is a question a judge could ask on stage.

- **No absolute depths from our model.** At 30 m the vertical error equals the depth we would report.
- **No clock on the fill sequence.** It is an order, not arrival times. Timing needs drainage data.
- **No per-building claims.** Buildings only route water and spread population.
- **No dispatch or evacuation routing.** Planning and preparedness only.
- **No drainage network, no tides.** Storm-drain networks are not public anywhere, so **pooling is
  overstated where drains work**. This is shown on every single run, not buried in an appendix.

## Architecture

**Static geography is staged once. Only live conditions come from an API at request time.**

```
Staged once (engine/tools/)        Per request (stateless)
  dtm.tif          COG      ─┐      windowed read of just that box
  water/roads/     FlatGeo-  ├───▶  fill depressions, D8 flow, accumulate
  buildings.fgb    buf       │      rank by ponded volume
  official/*.tif   KSDMA    ─┘      clip the official layer, compare
  region.json      provenance       build the road graph, flag routes
```

Terrain is read by HTTP range request; FlatGeobuf carries a packed R-tree so a bbox read fetches
only the bytes it needs — **54 ms for a 7 km² box against 713 ms for the whole region**.

Overpass rate-limits to 4 slots, rejects clients without a descriptive User-Agent, and takes
seconds to minutes for a city-sized query. The DEM clipping service allows 50 calls/day. 4TU went
down for maintenance mid-build. None of that belongs in a request a judge is watching — so none of
it is.

**The road graph is not stored.** It is built per box with networkx. A drawn box is arbitrary, so a
city-wide graph would have to be clipped anyway, and clipping costs about what building costs.

### Measured

| Box | Total (warm) | Breakdown |
|---|---|---|
| 7 km² | **0.78 s** | terrain 0.01, layers 0.34, water 0.03, roads 0.08 |
| 35 km² | ~1.3 s | |
| 75 km² | ~1.6 s | |

Cold start is ~8 s, which is why the service runs with `--min-instances 1`.

## A second city, and why Singapore

Singapore gets ~2,300 mm of rain a year in short, intense bursts, and has one of the best drainage networks in the world — which we have no data for. Our screening should therefore overstate pooling there more than anywhere else. That is the product's largest disclosed bias, made visible rather than argued about.

It also exercises the honest path: **no authority publishes a depth-valued flood layer for Singapore**, so the UI says so instead of quietly dropping the panel. That is the normal case nearly everywhere.

One trap worth recording: DeltaDTM is a *coastal* model that caps at 30 m, writing anything higher as exactly 30.0. Singapore's interior is above that. Read naively, the cap becomes a flat plateau ringed by false depressions, so capped cells are masked as no-data and counted per box. Low-lying flood-prone areas — where flooding actually happens — are 96-100% usable.

## Data

| Layer | Source | Licence |
|---|---|---|
| Bare-earth terrain | DeltaDTM v1.1 (Pronk et al. 2024), via the Earth Engine community catalog | CC BY 4.0 |
| Water, roads, buildings | OpenStreetMap via Overpass | ODbL 1.0 — © OpenStreetMap contributors |
| Official flood depth | KSDMA, produced with UNEP | published for public use; terms not stated |
| Basemap | Google Maps | display only — nothing measured or traced from it |

Staged data is **not committed** — it is large, third-party and fully reproducible. Only
`region.json` is kept, because it carries the provenance and licence of every layer.

Two things worth knowing about the KSDMA data, both found by checking rather than assuming:

- The `<district>.zip` files on the hazard-maps page are **landslide** susceptibility, not flood.
  The flood rasters are behind four separate "Raster File Zip" links.
- They are depth, not probability: values rise monotonically with return period at **100.0%** of
  shared wet cells, and inundated area grows 11.4% → 21.4% from 10-yr to 500-yr. **Units are not
  stated at source**; magnitude reads as centimetres, so everything records and displays them as
  inferred.

## Running it

```bash
python -m venv .venv
./.venv/Scripts/python.exe -m pip install -r service/requirements.txt geopandas pytest

# Stage the region (needs network; Overpass is slow and rate-limited)
python engine/tools/fetch_deltadtm_kochi.py      # terrain, via your gcloud login
python engine/tools/stage_region.py --buildings  # OSM water, roads, buildings
python engine/tools/stage_official.py <dir-with-ksdma-tifs>

echo "MAPS_API_KEY=..." > .env.local              # browser key, referrer-restricted
./run_dev.sh                                      # http://localhost:8080
```

Tests: `./.venv/Scripts/python.exe -m pytest engine` — 49 tests, all offline, no network or
staged data needed.

Deploy: `gcloud run deploy before-it-hits --source . --region asia-south1 ...` (no local Docker;
Cloud Build builds the image). Add the service URL to the API key's allowed referrers.

## Layout

```
engine/bih_engine/   terrain, hydrology, osm, routes, official, store  — no I/O beyond reads
engine/tools/        staging jobs: the only code that talks to Overpass or a data portal
engine/tests/        49 tests on synthetic terrain where the answer is known
service/             FastAPI: bbox + rainfall in, screening + official layer out
web/                 map, modal tool palette, results panel
plan/                the design written before building
PROGRESS.md          what is done and what is not
```

## Status

The engine, both tiers and the map work end to end. Not yet built: the 28 May 2024 replay scoring,
the agent layer, population and exposure, preparations within the city's budget, and the synthetic
test harness. `PROGRESS.md` tracks all of it honestly.
