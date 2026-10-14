# 01. Product and scope

## What it is, and who it is for

**Before It Hits** is a multi-hazard screening product for city officers, built for the **AI Builder Cup**
hackathon (Google Cloud / AI). A city gets one rainfall warning for the whole city, but the water collects in
the same low spots it collected in last year. This product answers at the scale of a neighbourhood:

- **Rain and flooding.** Which places the ground traps water in, in what order they fill, which roads run
  through them, roughly how many people live there, what the official depth map says about the same ground,
  and which preparations the city can afford.
- **Heat.** The forecast for the air, and, separately, a satellite picture of which parts of an area run hotter
  at the surface, named by the road beside them.
- **Air quality.** The provider's reading, attributed, never rewritten.
- **What is being reported.** News headlines per hazard, displayed beside the findings and never merged in.

The audience is a **city officer planning preparation**, with a "resident" wording option. It is a planning and
preparedness tool. It is **not** an emergency-response, dispatch or evacuation tool.

**Judging context.** Submission is a **live demo plus a repo link** (confirmed 11 Oct). The user is aiming for
the top of the field, so polish and honesty both count. A flood answer that never fails on stage is worth more
than three that sometimes do.

## How a person uses it

1. Pick a city (Kochi or Singapore).
2. Do any of: **draw a box** (the Box tool), **pick a preset** (Edappally, Palarivattom, Vyttila, Kakkanad for
   Kochi; Orchard Road, Bukit Timah Rd, Geylang / Kallang, Jurong East for Singapore), **name a place**, or
   **ask about the whole city** with no box at all.
3. Ask in plain language in the Agent tab. The agents run and stream their steps; the map, the Details tab and
   the Heat / Air / Rain tabs update to match.
4. Turn on map layers (a Ventusky-style weather map with one shared clock) and read the panels.

## The idea that shapes everything: two tiers that explain each other

A 30 m elevation model carries about half a metre of vertical error, the same size as the flood depths we would
otherwise quote. So the claim is split in two and the halves are shown side by side, never merged:

| | **Screening tier** | **Validated tier** |
|---|---|---|
| Works | on any box, anywhere | only where an authority publishes |
| Says | *which* low points fill first, and why | *how deep*, officially |
| Output | pooling rank 1 to 5, catchment, fill order | published depth by return period |
| Source | our terrain physics, computed per box | KSDMA / UNEP, displayed as published (Kochi only) |

**We never emit a depth in metres from our own model.** Depth is used internally only, to flag depressions
shallower than the terrain error. A test asserts no depth field reaches the API.

Evidence the screening finds something: our top-ranked spots fall inside the authority's flood extent
**1.25 to 3.26 times more often than chance** (Palarivattom 2.78×, Vyttila 1.79×, Edappally 1.52×,
Kakkanad 1.25×), reported as enrichment over the inundated share and never as raw overlap. It is *agreement*,
not accuracy: the official layer is about 92 m and models river and backwater flooding, ours is 10 m and finds
local rain ponding.

## What it refuses to claim

Written down before building, because each is a question a judge could ask on stage. **If a feature or a line
of copy crosses one, that is a bug.**

1. **No absolute depth from our model.** At 30 m the vertical error equals the depth we would report. Rank only.
2. **No clock on the fill animation.** Fill order is an *order*, not an arrival time. Timing needs drainage data
   that is not public anywhere.
3. **No per-building claims.** Buildings only route water and spread population. Never a statement about an
   individual building or household.
4. **No indoor heat.** It needs roof, floor and ventilation data that does not exist for Indian cities. Heat is
   area-level only.
5. **No dispatch or evacuation routing.** Planning and preparedness only. Google's terms bar emergency-response
   use, and we could not validate dispatch anyway.
6. **No drainage, no tides.** Storm-drain networks are not public anywhere and neither elevation source has
   bathymetry. **We overstate pooling where drains work**, and that is disclosed on every run.

Added with the heat work (14 Oct):

7. **Surface heat is a typical season, not a forecast and not today.**
8. **Surface heat is the surface, not the air.** It is never set beside the forecast heat index.
9. **The satellite sees patches about 100 m across** (delivered on a 30 m grid). It shows a few blocks at best
   and never a single roof.

Three guard rails enforced in the architecture:

- **No personal data.** Population is modelled from published totals; messages are drafts a person sends, never
  sent by us.
- **News is display-only.** It is never stored, never scored, and changes no number on the page.
- **Nothing is derived from map imagery.** The Google basemap is for display only.

## Scope: in

| Area | In scope today |
|---|---|
| Flood | screening tier on any box; validated tier where staged; roads through pooling spots with a rain playback; people in the worst spots (by area); preparations within the city's real budget; before/after (best case) |
| Heat | live forecast heat index, temperature, humidity; five animated weather fields; Landsat surface-heat layer with hottest/coolest places |
| Air | current AQI, pollutant concentrations, hourly forecast, health recommendations (the provider's), Google's heatmap tiles |
| People | population density as a smooth field by area (a modelled 100 m grid scaled to the published total); exposure in the worst flood spots |
| News | per-hazard headline lists and a news chat that cites only the headlines it was shown |
| Agents | scenario reading, topic routing, forecast tool, narrator, verifier, guide, planner, road outlook, news agent |
| Cities | Kochi, Singapore. Adding a city is a config entry plus the staging tools: no engine change |
| Evidence | KSDMA agreement; the 28 May 2024 Kochi replay, reported as an honest negative |

## Scope: out, and why

| Out | Why |
|---|---|
| Dispatch, evacuation, routing people | terms of use; unvalidatable |
| Anything per building or household | not supportable by the data |
| Absolute flood depth from our model | vertical error |
| When water arrives or how long it stays | no drainage data |
| A health outcome for any person | not our claim to make |
| Indoor heat | no data |
| An air temperature for a spot from satellite data | LST is skin temperature |
| Emergency-response use of Google traffic | Google's terms; traffic is display only |

## Scope: planned in the original plan and **not built**

See `03-remaining-work.md` for the honest triage. In short: Watcher (F14, push alerts), test harness with
synthetic worlds (F16, A14), Malayalam briefings and messages, Firestore persistence of runs, critical places
(hospitals, schools: L13), runoff coefficient from land cover (L8) instead of a flat 0.8, GLO-30 terrain
fallback outside DeltaDTM coverage, fill-sequence animation, cross-links between the news agent and the engine.

## The two cities, and why these two

- **Kochi.** Monsoon city on the backwaters, 0 to 7 m above sea level, fully inside DeltaDTM coverage. Chosen
  because **KSDMA publishes depth-valued flood rasters by return period**, which is what the validated tier is
  checked against. Population from the 2011 Census (Kochi urban agglomeration, 2,117,990). Known gaps: OSM
  building coverage is partial, news coverage in the GDELT index is thin.
- **Singapore.** About 2,300 mm of rain a year in short intense bursts, and one of the world's best drainage
  networks, which we have no data for. So pooling should be overstated here more than anywhere: the largest
  disclosed bias, made visible. No authority publishes a depth layer, so the UI says so instead of dropping the
  panel, which is the normal case nearly everywhere. Live NEA rain gauges (keyless). Near-complete OSM
  footprints. Population from SingStat mid-2023 (5,917,648). DeltaDTM caps at 30 m, so capped cells are masked
  and counted; low-lying flood-prone areas are 96 to 100% usable.

## Mapping to the original plan IDs

The plan (`plan/assets/data.js`) names 16 features (F1 to F16), 18 layers (L0 to L18) and 14 agents (A1 to
A14). Status of each is in `02-implemented.md`. **Naming drift to know about:** in the plan **A8 is the
Watcher**; in the code **A8 is the Guide** agent. The plan's A9 (Source Scout), A11 (Document) and A12
(Calibration) were build-time agents and were not built as agents.
