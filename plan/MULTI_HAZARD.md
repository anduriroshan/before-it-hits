# Beyond rain: heat, air quality and a news agent that spans them

Written 13 Oct 2026. Submission is **18 Oct**, so there is no "later": this is a schedule for 14-17 Oct
with the cut lines decided now, not when we are behind. 18 Oct is submit-only.

## What the product becomes

One box, every hazard. Draw a box (or name a place) and the agent answers for whichever hazards the
question touches: rain pooling, heat, air quality, and what the news is saying about each. The honesty
spine does not change per hazard; it is the thing that carries over:

- no language model produces a number; every figure is the engine's or a named API's
- ranks are relative to the box, never an absolute the data cannot support
- every hazard ships with its own "what this does not claim", shown on every run
- news is shown beside the findings, never merged into them

## Architecture: what changes, and what does not

**Does not change:** the coordinator's shape (scenario -> tools -> narrate <-> verify), the Narrator and
Verifier (they read a facts bundle and nothing else), the NDJSON streaming, the chat, the map shell.

**Changes:**

| Layer | Today | Becomes |
|---|---|---|
| Registry | `bih_agents/hazards.py` lists live hazards for the intro + Guide | each entry also carries `forcing`, `screen`, `facts`, `limits`; the UI tabs, legend, welcome and Guide read it |
| A2 Scenario | `mode`, `rainfall_mm`, `scope`, `place` | + `hazards: [flood\|heat\|air]` (enum from the registry; a hazard not built is never offered) |
| Engine | `analyse_area` (flood) | one contract per hazard: `screen(box, forcing, region) -> {areas (rank 1-5 geojson), exposure, caveats, confidence, limits}`. Flood is wrapped, not rewritten |
| Facts bundle | `facts.build` (flood) | `facts.build_<hazard>`; the Verifier's rules gain one line per hazard (e.g. heat: "no absolute temperature from our model") |
| UI | Agent / Rain / Details / News | Agent / **one tab per live hazard** / Details / News. The legend and map layer follow the active hazard. A "layers" switch on the map: Flood, Heat, Air, Traffic |
| Exposure | people in worst flood spots | the same footprint-share method, fed any hazard's top-ranked areas |

The one decision that keeps this cheap: **a hazard is a function from (box, forcing) to ranked areas.** The
rest of the product only ever sees ranked areas, a forcing description, caveats and a limits list.

## Heat

**What we can honestly say:** which parts of a box run hotter than the rest of the city on a hot day, and
roughly how many people live in the hottest parts; plus the forecast heat for the next two days.

| Part | Source | Notes |
|---|---|---|
| Hot-spot ranking | Landsat 8/9 land-surface temperature, dry-season median, staged once per city via Earth Engine (already enabled) | `data/regions/<city>/lst.tif`, read per box like `dtm.tif`. 30 m. Rank 1-5 against the **city**, not the box, so "hot" means hot for this city |
| Forecast heat | Google Weather API hourly temperature, feels-like, humidity, UV (same key as rain) | reduced to the worst rolling window, like rain. Shown as the provider's numbers, attributed |
| Who is exposed | `exposure.estimate` on the top-ranked heat patches | same self-consistent footprint share, same caveats |
| Context | OSM building density and green cover as the two things the ranking can be read against | "why is it hot here": built-up share and tree cover, both from layers we already stage or can |

**Refuses to claim:** an air temperature for any spot (LST is a *skin* temperature from a satellite pass, not
what a person feels); a health outcome; anything per building; timing within the day. Cloud: Kochi's monsoon
sky is the data risk, hence a dry-season (Jan-Apr) composite for Kochi, and the staging note says so.

**Cut if behind:** the spatial ranking. The fallback is a forecast-only Heat tab (live, honest, thinner).

## Air quality

Not a terrain screening; it is a live feed, and it is presented as one.

| Part | Source | Notes |
|---|---|---|
| Now + 24 h | Google Air Quality API (current conditions, hourly forecast): AQI, dominant pollutant | provider's figures, attributed, never rewritten. **Probe first**: confirm Kochi and Singapore are covered and the API is enabled |
| Map layer | the same API's heatmap tiles as a map overlay | display only |
| Exposure | none | AQI is a regional reading; we do not say who is exposed |

**Refuses to claim:** a health effect for a person; a reading finer than the provider's grid; a forecast
beyond what the API returns.

**Cut if behind:** the heatmap overlay (keep the numbers).

## Traffic and population density (the cheap ones)

- **Traffic**: Google's own traffic layer as a toggle on the map. No data work. Traffic *news* is already a topic
  in the news agent. It is display only (Google's terms bar emergency-response use, same as the rest).
- **Population density**: the exposure model already spreads the published population over building
  footprints. A density view is that, rendered by area. Same caveats, no new data.
- **Humidity**: part of the heat tab (it is in the Weather API reply and drives feels-like).

## The news agent across hazards (started 13 Oct)

Built today: the News tab has a chat under the headlines. The agent routes a question to topics from a list
**we** own (flooding, heat, traffic, air quality), fetches each from the cached index, and answers only from
the headlines it was shown. Each claim cites its headlines; a claim with no citation, or with a figure that is in
no headline, is deleted and the deletion is shown. It reports and never advises.

Still to do, in order:

1. Topics generated from the hazard registry, so a hazard added there gets its news topic for free.
2. **Cross-links both ways**: a news answer naming a place offers "screen it" (runs the engine in the Agent tab);
   an engine answer offers "what is being reported here" (runs the news agent for that place and hazard).
3. A compound question ("what is the risk in Edappally tomorrow, all things considered?") runs each live hazard,
   plus news, and the Narrator writes one briefing from one facts bundle. The Verifier checks it the same way.
   News stays in its own section of the answer, labelled "reported", never blended into a ranking.

## Schedule

| Day | Work | Done means |
|---|---|---|
| **Tue 13** | Intro made hazard-neutral; legend fixed; news agent + chat; F10 before/after | tests green, deployed |
| **Wed 14** | Heat engine: stage LST for both cities, `heat.py` (rank, polygonise, exposure), forecast heat reducer, tests. Registry gains `screen`/`facts`; A2 gains `hazards` | a heat result for an Edappally box and an Orchard Road box, offline tests |
| **Thu 15** | Heat tab + layer switch + per-hazard legend. Heat facts bundle + Verifier line. Air: probe, then tab with now/forecast, then the overlay | Heat and Air usable in the UI on both cities |
| **Fri 16** | Compound answer; news topics from the registry; cross-links; traffic toggle; density view; Guide reference | one question answered across hazards, verified |
| **Sat 17** | **Freeze.** Demo script and rehearsal, one prepared answer per "will not claim", both cities regression-checked, deploy, live smoke test, README / PROGRESS | submission-ready |
| **Sun 18** | Submit. No new features | |

## Cut list (in this order)

1. Population-density view
2. Air heatmap overlay (keep the numbers)
3. Compound briefing (keep each hazard's own answer)
4. Heat spatial ranking (keep the forecast-only Heat tab)
5. Traffic toggle

## Risks to check first, not last

- **News index.** GDELT is slow, rate-limited (429/503 seen today) and thin for Kochi: three headlines in two
  weeks, all dated 1 Oct, for flooding; none for heat or air. The agent says so plainly rather than inventing, but a
  demo should lean on Singapore's richer coverage, and the cache is warmed at start-up. If a second source is wanted,
  that is a licence decision (the plan splits news by licence) and a call for the owner.
- **Landsat in the monsoon.** Cloud. Dry-season composite, stated on the result.
- **Air Quality API coverage and quota.** Probe on Wed morning before building a tab on it.
- **Scope vs. a working demo.** Every hazard added is another thing to rehearse. A flood answer that never fails on
  stage is worth more than three that sometimes do, which is why Saturday is a freeze and the cut list is ordered.
