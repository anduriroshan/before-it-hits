# Before It Hits

**Live: https://before-it-hits-z5r5gsxkza-el.a.run.app** — pick a city, draw a box, name a place, or just ask about the whole city.

Two cities are staged: **Kochi** (where an authority publishes a depth map to check against) and **Singapore** (where none does). Adding a city is a config entry plus the staging tools — no engine change.

Cities get a rainfall forecast and issue a warning for the whole city. The water still gathers in
the same low spots it gathered in last year. Draw a box on the map and ask what happens *there*:
which places the ground traps water in, why, which roads run through them, and what the official
depth map says about the same ground. The same box also answers for **heat** (which parts of it run
hotter, from a satellite) and **air quality**, and shows what is being **reported** about it, each
kept in its own lane and never blended into one score.

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

## Replaying a real storm — and reporting where it fails

We also replayed the **28 May 2024 Kochi cloudburst** (IMD-confirmed; ~100 mm in one hour) against
a second, independent ground truth: the neighbourhoods newspapers reported underwater. Scored the
same way as the KSDMA test — a flag is a top-ranked spot that fills; detection is the share of
reported places near a flag; baseline is the share of the area a flag covers.

The honest result is the headline, not a footnote: **6 of 8 reported localities were flagged — but
a flag covers 90% of the area at that rainfall, so enrichment is 0.83× and the test is *not
discriminating*.** 100 mm in an hour floods most of low-lying central Kochi in the model, so being
near a flag separates nothing. This is the same saturation the KSDMA 500-year layer hits, now for a
real event, and it is exactly the regime the relative ranking is *not* built for.

It also surfaces a specific, expected miss: **Vyttila**, a notorious flood junction, is not
flagged — its flooding is tidal and drainage-driven, which a rain-ponding model with no tides and
no storm drains cannot claim to capture. That the replay shows this rather than hiding it is the
point of running it. Where the ranking *does* carry signal is the neighbourhood-sized box at a
discriminating return period: the KSDMA agreement above, 1.25–3.26×. The replay is precomputed
(`engine/tools/replay_kochi.py`), shipped as JSON, and shown in full in the app.

## Ask it in plain language — and watch something check the answer

Ask about a whole city ("what's the rainfall in Kochi tomorrow?") and the agent runs the engine on
every preset area and ranks them by risk — no box needed. Name a place ("will Edappally flood
tonight?") and the browser finds it and zooms in. Draw a box and type a question, and the agents run
in about ten to twenty seconds with every step streamed to the screen as it finishes.

The Scenario agent also reads *what the question is about*. A question about people, heat or air
quality runs only what it needs (about four steps, no flood engine); a flood question adds the heat,
satellite-heat and air-quality findings to the same checked briefing; "what does this mean?" goes to a
Guide that answers from a written reference and analyses nothing.

> *"Which spots fill first here, and how does it compare with the official map?"*

The division of labour is the whole design:

**the language models decide what to ask; the engine decides what is true.**

| | Agent | Does |
|---|---|---|
| A2 | Scenario | question → parameters: what-if, forecast, which rainfall, which official layer |
| A3 | Forecast | the real 48 h forecast, only when the question is actually about what is coming |
| A4 | Engine | **not a model.** The only thing here that produces a figure |
| A5 | Planner | a short plain read on the costed preparations; may use no number the list lacks |
| A6 | Narrator | writes the briefing from a facts bundle, and nothing else |
| A7 | Verifier | checks every word of it against the findings, and removes what does not hold |
| A8 | Guide | answers questions about the product from a written reference; runs no analysis |
| A13 | News | answers from live headlines only, every claim cited (see below) |

### The Verifier is the point

It runs two checks that fail in different ways.

**The arithmetic check is code and cannot be talked out of it.** Every figure in the draft is
pulled out and matched against the figures in the facts bundle, allowing ordinary rounding and
percentage forms and nothing else. That is what makes *no figure is ever produced by a language
model* a property of the system rather than a line in a prompt.

**The claim check needs judgement, so it uses a model.** It catches what the arithmetic cannot:
a draft whose every number is real and whose meaning is wrong. "Most of the area floods" contains
no numbers at all. Seven named failure modes, each one a limit the product had already written
down: unsupported, own_depth, unattributed, scope, timing, dispatch, overstated, certainty.

Measured on eight planted drafts, one per rule plus a clean one: all eight caught, clean one
passed, 2.9 s each. Thinking harder made it slower and no better — the findings are right there in
the prompt, so this is a comparison, not a deduction.

**It fails closed.** An unsupported claim is removed, not softened. A failed headline or a missing
caveat withholds the whole briefing. If the Verifier itself cannot be reached, nothing is
published — a briefing whose only value is that something checked it is worth nothing unchecked.
The screening underneath is unaffected, because it is computed rather than written.

Live, on the first run of the question above, it caught the Narrator writing that a spot fills
*"starting early"* — fill order is an order, not a clock — sent it back, and the revision dropped
it. The removal is shown on screen, not hidden.

## Roads over time, and asking the agent to predict

Draw a box, then **ask the agent** — "what happens here tonight?", "what if 100 mm falls in an
hour?". It reads the question, pulls the **live forecast** (or takes the figure you stated), runs
the engine, and renders the map, the pooling and a **road-timeline playback**. The rainfall slider
is just a manual what-if for testing; the agent is the real driver, and it decides the rainfall and
window, never a model inventing one.

The playback: every ground-level road that crosses a pooling spot gets a `rain_to_flood` threshold
— the least cumulative rainfall at which a spot on it fills — and a severity. Scrub the timeline or
hit play and roads light up red→yellow in the order they flood, like a traffic heatmap. It is
**modelled road difficulty from rain pooling, not measured traffic**, on a rainfall-accumulation
clock that assumes water does not drain away (the worst case) — stated on the panel every time.

Because terrain differs, the physics already differs city to city. The softer, region-specific part
— how fast a city actually jams — lives in local knowledge, so a **road-outlook agent** reads the
deterministic findings *and* recent local news and writes a short qualitative note. It invents no
measurement: every figure is checked against the findings and it fails closed.

## Heat and air, each in its own lane

**Heat has two sources, and the product refuses to compare them.** The *forecast* (Google Weather API)
gives the air: heat index, temperature, humidity, wind, a level (caution, extreme caution, danger) and
the peak in the next two days, at one point per box. The *satellite* says which parts of the box run
hotter than the rest: Landsat 8 and 9 surface temperature, a median of every clear pass (55 scenes for
Kochi, January to April; 56 for Singapore, all months), drawn as a layer and ranked into the hottest and
coolest places, each named by the road beside it ("the ground beside Stadium Round runs 4 °C above the rest
of the box"). It is a **typical mid-morning in that season — not today and not a forecast**; it is the
**surface**, not the air, so it is never set beside the heat index; the sensor sees patches about 100 m
across, so it shows a few blocks at best and never a roof. Before building on it we checked it against
vegetation: Spearman −0.33 (Kochi) and −0.32 (Singapore), and the most-vegetated quarter of pixels is
1.6 °C and 3.3 °C cooler, which is what a real surface-temperature signal should show.

**Air quality** is the provider's reading (Google Air Quality API), attributed and never rewritten: the
current index, dominant pollutant, concentrations with units, an hourly forecast and the provider's own
health recommendations. A regional reading, and said to be.

## The map: nine layers and one clock

The Layers panel stacks nine layers, each with its own opacity and a one-line note on what it is drawn
from: heat index, **surface heat (satellite)**, temperature, humidity, rain per hour, wind, people per km²,
air quality and traffic. The weather fields follow the box you drew (finer inside a small box, and the note
states how little a forecast actually varies across one), and every active field gets a colour scale drawn
from the same ramp as the picture. The people layer is a smooth field, null where there is no footprint at
all, because "nobody lives here" and "we have no footprint here" are different statements.

**One thin clock** at the bottom of the map drives everything time-shaped: the weather fields morph between
forecast hours, and the road playback lights roads at the rainfall that has fallen by the same moment.
Scrubbing never re-runs the engine.

## Who is nearby, and what the city is saying

**People (F7).** For each box we model roughly how many people are in the worst spots by spreading
the city's published population across mapped building footprints by area, then summing the
footprint inside the top-ranked spots. The ratio is written so it is self-consistent — the same
partial OSM footprints sit in both the numerator and the denominator — so even OSM's under-mapping
of buildings cancels to first order. It is reported *by area, never per building*, with no floor
counts (footprint stands in for floor area), rounded hard, and shown with its caveats every time.
A modelled distribution, not a roll call.

**News (A13).** The News tab pulls live headlines about the city from GDELT's open index,
de-duplicates them, and — when a model is available — writes a two-line grounded digest. Under the
headlines is a **chat**: ask what is happening, or what happened weeks ago, across flooding, heat,
traffic and air quality. The agent picks topics from a list we own (it never writes a search query),
answers only from the headlines it was shown, and every claim cites them. Code deletes a claim that
cites nothing or carries a figure no headline contains, and shows the deletion. It reports and never
advises. It is **display-only by contract**: never stored, never scored, and it changes no number on
the page. It is prompt-injection resistant — a planted "ignore previous instructions" headline cannot
become a claim, because a claim needs a citation and a traceable figure to survive.

The News tab and the screening **link both ways**, and the link only ever moves a question across. A
screening answer offers "What is being reported in Edappally?", which asks the news agent about the same
place and hazard. A news claim that names a place offers "Screen *place*", which runs the screening for
it — but only where a headline the claim cites actually contains the place (code checks that, not the
model). The news index is city-wide and thin at street level, so a neighbourhood gets only the headlines
that name it, and when none does the answer says exactly that ("No headline names Edappally, so this is
what was reported about Kochi as a whole") rather than pretend otherwise.

**Before / after (F10).** Under the preparation plan, the same box is run twice: as it is, and with the
spots the plan acts on taken out of the exposed set. The difference in worst spots, people and road
through them is computed by the engine, not estimated. It is labelled a **best case** every time: it
assumes each pump or cleared drain fully takes its spot out of the flood, and barricades are not
counted because they keep traffic out rather than reduce what the water reaches. "See it on the map"
outlines the treated spots so the box can be read with and without the plan.

## What this will not claim

Written down before building, because each is a question a judge could ask on stage.

- **No absolute depths from our model.** At 30 m the vertical error equals the depth we would report.
- **No clock on the fill sequence.** It is an order, not arrival times. Timing needs drainage data.
- **No per-building claims.** Buildings only route water and spread population.
- **No indoor heat.** It needs roof, floor and ventilation data that does not exist for Indian cities.
- **No dispatch or evacuation routing.** Planning and preparedness only.
- **No drainage network, no tides.** Storm-drain networks are not public anywhere, so **pooling is
  overstated where drains work**. This is shown on every single run, not buried in an appendix.
- **Surface heat is a typical season, not a forecast and not today; it is the surface, not the air; and
  it is blocks, not buildings.** It is never set beside the forecast, and the checker removes a claim that
  does.

## Architecture

**Static geography is staged once. Only live conditions come from an API at request time.**

```
Staged once (engine/tools/)        Per request (stateless)
  dtm.tif          COG      ─┐      windowed read of just that box
  water/roads/     FlatGeo-  ├───▶  fill depressions, D8 flow, accumulate
  buildings.fgb    buf       │      rank by ponded volume
  official/*.tif   KSDMA    ─┘      clip the official layer, compare
  region.json      provenance       build the road graph, flag routes
                                    ↓
                                    facts bundle → Narrator → Verifier → screen
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
| Surface temperature | Landsat 8 and 9 Collection 2 Level 2 (USGS), via Earth Engine | public domain |
| Population | 2011 Census (Kochi urban agglomeration); SingStat mid-2023 (Singapore) | published totals, spread by area |
| Rainfall, heat, air quality (live) | Google Maps Platform Weather and Air Quality APIs | provider's figures, attributed |
| Live rain gauges (Singapore) | NEA via data.gov.sg | keyless, 5-minute totals |
| News | GDELT DOC 2.0 | open index, shown as headlines with their publishers, never stored |
| Basemap | Google Maps | display only — nothing measured or traced from it |

Staged data is **not committed** — it is large, third-party and fully reproducible. Only each city's
`region.json` is kept (Kochi's and Singapore's), because it carries the provenance and licence of every
layer, the population it used, and for surface heat the scenes, dates and caveats.

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

# Stage a city (needs network; Overpass is slow and rate-limited)
python engine/tools/fetch_terrain.py kochi              # terrain, via your gcloud login
python engine/tools/stage_region.py kochi --buildings   # OSM water, roads, buildings
python engine/tools/fetch_lst.py kochi                  # Landsat surface temperature (Earth Engine)
python engine/tools/stage_official.py <dir-with-ksdma-tifs>   # Kochi only

# Three keys, three different jobs, none interchangeable
cat > .env.local <<'KEYS'
MAPS_API_KEY=...      # browser key, referrer-restricted. Public by design: it ships in the page.
                      # Allowed APIs: Maps JavaScript, Geocoding, and Air Quality (the heatmap tiles)
WEATHER_API_KEY=...   # server key (Weather + Air Quality). A referrer-restricted key cannot work server-side
GEMINI_API_KEY=...    # server key, restricted to generativelanguage.googleapis.com
KEYS
./run_dev.sh                                      # http://localhost:8080
```

Without `GEMINI_API_KEY` the agent layer reports itself unavailable and the ask box does not
appear. Everything else works unchanged.

Tests: `./.venv/Scripts/python.exe -m pytest engine` — 289 tests, all offline, no network, no
staged data and no model. The agent tests stub both models out on purpose: what is worth testing
there is the part that holds when the model is wrong, and that part is deterministic by design.

Deploy: `gcloud run deploy before-it-hits --source . --region asia-south1 ...` (no local Docker;
Cloud Build builds the image). Add the service URL to the API key's allowed referrers.

## Layout

```
engine/bih_engine/   deterministic, no model: terrain, hydrology, osm, routes, official, exposure,
                     preparations, heat, surface_heat, density, air_quality, weather_grid, replay, store
engine/bih_agents/   scenario, narrator, verifier, guide, planner, news and news_agent, coordinator, facts
engine/tools/        staging jobs: the only code that talks to Overpass, Earth Engine or a data portal
engine/tests/        289 tests, mostly on synthetic data where the answer is known
service/             FastAPI: every /api route, and the static web files
web/                 map, layers and one clock, and the right-hand panel: Agent (chat with memory),
                     Rain, Details, Heat, Air, News
plan/                the design written before building
PROGRESS.md          what is done and what is not
```

## Status

The engine, both tiers, the map and its nine layers and clock, the agent layer with topic routing,
heat (forecast and satellite), air quality, population and exposure, the news-grounded road outlook,
preparations within the city's budget with a best-case before/after, the news agent with its links to
the screening, and the 28 May 2024 replay work end to end, on two cities. Not built, and said so: the
seeded synthetic harness, push alerts, Malayalam briefings, persistence of runs, and a hospital-and-school
layer (which is why "areas cut off from a hospital" is not claimed). `PROGRESS.md` tracks all of it
honestly.
