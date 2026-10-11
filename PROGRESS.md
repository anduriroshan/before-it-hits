# Before It Hits: build tracker

Submission **18 Oct 2026**. Started tracking 11 Oct (day 1 of 8). Spec lives in `plan/` (content in `plan/assets/data.js`).
Tick a box when the work lands and has been run, not when it is started. Plan IDs: **F** feature, **L** layer, **A** agent.

**Now:** two cities live at https://before-it-hits-z5r5gsxkza-el.a.run.app, and the agent layer answers questions about a drawn box with every figure checked against the engine. **Next: F15 replay scoring (P7)** — the strongest remaining evidence claim, and the one thing that needs historical rainfall.

**Submission:** live demo + repo link (confirmed 11 Oct). So the deployed URL must stay working — it runs with `--min-instances 1` because a cold start is ~8 s against ~0.8 s warm.
**Last updated:** 12 Oct 2026 (day 2)

## P0 Foundations
- [x] Plan written and revised (`plan/`)
- [x] Git repo and GitHub remote
- [x] GCP project `before-it-hits`, billing on, gcloud config `bih`
- [x] APIs enabled: Run, Build, Artifact Registry, Storage, Vertex AI, Firestore, BigQuery, Scheduler, Pub/Sub, Secret Manager, Maps (JS, Weather, Places, Geocoding), Earth Engine
- [x] Project memory rebuilt after the folder rename
- [ ] First commit and push of `engine/` (waiting on your OK)
- [x] Maps API key created, restricted to localhost:8080 referrers + Maps JS/Geocoding, in gitignored `.env.local`
- [x] Maps key referrers now cover localhost and both Cloud Run URLs. Left as an env var rather than Secret Manager on purpose: a Maps *browser* key is public by design (it ships in the page source), so the referrer restriction is the real control
- [x] Region: `asia-south1` (Mumbai), nearest to Kochi and to the judges
- [ ] Cloud Storage bucket for the terrain store. **Deliberately deferred:** the 39 MB staged region is baked into the container instead, which removes a network dependency from the request path. `Region` already accepts a gs:// root when that changes

## P1 Engine: where water collects (F4), the core
- [x] Windowed read of a box from cloud-optimised tiles, multi-tile mosaic (`terrain.py`)
- [x] Local-file terrain source; non-finite cells become drainage outlets
- [x] Depression fill, D8 flow, accumulation in numba (`hydrology.py`)
- [x] Catchment = accumulation summed over a depression's last exits. Two bugs caught: max-in-footprint undercounted ~3x; summing every exit double-counted water that leaves and re-enters (a catchment larger than the whole grid on real terrain). Now equals an independent flow-path oracle exactly, on synthetic and real terrain
- [x] ScreeningResult contract from the plan; no depth in metres is ever emitted
- [x] 58 tests, including mass conservation, determinism and an independent flow-path oracle
- [x] Latency measured: compute 0.03 to 0.21 s for 7 to 75 km2; first cold read 5.5 s (laptop to S3)
- [x] DeltaDTM Kochi clip fetched via Earth Engine API, provenance sidecar written
- [x] GLO-30 vs DeltaDTM compared: bare earth cuts pits 2 to 3x, median pit depth 1.3 m to about 0.3 m
- [x] Ranking metric: fill_order = least rain to fill (the animation sequence); pooling_index = water that ends up stuck, min(inflow, capacity). Provisional until checked against KSDMA (P2)
- [x] Despike low outlier pixels at source resolution (3 m below the 3x3 median; 11 to 49 pixels per box, count reported)
- [x] Pits deeper than 5 m excluded from the ranking and listed separately with a reason (water body or artifact)
- [x] Oversized catchments resolved: with OSM water and the catchment fix, no pit collects over 25% of the 35 or 75 km2 boxes (largest 96 ha in 75 km2)
- [x] OSM water (L2): 2,867 water polygons and 1,300 waterways staged for Kochi (`engine/tools/fetch_osm_kochi.py`); water as outlets, waterways burned 2 m and made outlets; disclosed in the result
- [x] OSM roads (L7) and buildings (L4) staged: 30,852 roads, 154,506 footprints. Buildings burn as walls; **road trenches are off by default** - measured, a 0.2 m trench took pits 291 -> 480 on a 35 km2 box and pushed pits-inside-error 76% -> 83%, i.e. it manufactures artifacts. Re-test now buildings exist
- [x] Road graph per box (networkx, built per request not stored): roads running through top-ranked (index >= 4) pooling spots, by name and length
- [ ] F8 "what gets cut off" needs facilities (L13). Connected-components alone returned ~400 "isolated areas" on a 35 km2 box - graph noise, so it is computed but deliberately not in the response
- [ ] Runoff coefficient from WorldCover (L8) instead of a flat 0.8
- [x] Region store: `data/regions/kochi/` = dtm.tif + FlatGeobuf layers + `region.json` manifest (sources, licences, dates, known gaps). Bbox reads use the packed R-tree: 54 ms for a 7 km2 box vs 713 ms for the whole region
- [ ] Stage the same layout to GCS and re-measure from Cloud Run (local paths and gs:// URLs read identically)
- [ ] Profile switch: DeltaDTM inside coverage, GLO-30 outside and flagged degraded
- [x] Engine HTTP service (FastAPI, `service/app.py`): POST /api/analyse, plus /healthz, /api/region, /api/config. Stateless; strips internal depth fields; rejects boxes outside the region, inverted boxes and boxes over 120 km2
- [x] Deployed to Cloud Run: https://before-it-hits-z5r5gsxkza-el.a.run.app (also reachable at the legacy `before-it-hits-1019287218541.asia-south1.run.app`). 2 Gi / 2 vCPU, min-instances 1, built by Cloud Build from the Dockerfile with no local Docker
- [x] Live timings verified: 0.79 s Palarivattom, 0.97 s Vyttila, 1.30 s Kakkanad; ~8 s cold

## P2 Validated tier (F5) and forecast (F3)
- [x] KSDMA rasters found and staged. **The plan's assumption held, but the route to them did not:** the `<district>.zip` files on the hazard-maps page are GSI *landslide* susceptibility, not flood. The flood data is four separate "Raster File Zip" links (historical + RCP8.5, each split 10/25/50 and 100/200/500 yr). Staged the six historical layers clipped to Kochi: ~92 m (3 arcsec), ~50 KB each
- [x] Confirmed these are depth rasters rather than probability, by evidence not assumption: values rise monotonically with return period at **100.0%** of shared wet cells, and inundated area grows 11.4% -> 21.4% from 10-yr to 500-yr. Units read as cm but are **not stated at source**, so they are recorded and displayed as inferred
- [x] Clip to the box and show as published, never resampled (`bih_engine/official.py`). Depth bands are for colour only and the band edges are returned with the data
- [x] Screening scored against the KSDMA raster. **Our top-ranked spots land inside the official flood extent 1.25-3.26x more often than chance** (Palarivattom 2.78x, Vyttila 1.79x, Edappally 1.52x, Kakkanad 1.25x at 1-in-50-yr). Reported as enrichment over the inundated share, because raw overlap is meaningless once most of the box is wet; at 500-yr the test is flagged non-discriminating and says so
- [ ] Rainfall to return-period mapping needs IDF curves we have not sourced. The forecast module deliberately refuses to infer one, and says so in its own caveats; the return period stays an explicit UI choice
- [x] Weather API forecast for a box (L15): hourly QPF reduced to the worst rolling 1/3/6/12/24 h window, because intensity floods streets and a daily total does not. Separate endpoint from /api/analyse so a weather outage costs the rainfall panel and nothing else. Server key is separate from the browser key (a referrer-restricted key cannot be used server-side)
- [x] What-if mode kept alongside: both staged cities are forecast quiet right now (Kochi 0.5 mm, Singapore 4.3 mm over 48 h), so the slider is what makes a demo possible
- [ ] Replay mode (a real past storm) — needs historical rainfall, not the forecast API
- [x] Panel says plainly when no validated layer is selected or none exists for the region

## Second city: Singapore (staged 11 Oct)
- [x] Singapore staged: 187,348 roads, 1,983 water polygons, 3,720 waterways. 0.7-1.8 s per preset box
- [x] **No authority publishes a depth-valued flood layer for Singapore**, so it exercises the honest "no validated layer here" path (F12) rather than hiding it
- [x] DeltaDTM caps at 30 m and Singapore's interior is higher: capped cells now masked as no-data rather than read as a real elevation, which would invent flat plateaus and a ring of false depressions. `capped_px` is reported per box
- [x] Overpass mirror fallback in the staging tool — the main endpoint returned 504 repeatedly
- [ ] Singapore buildings (not staged; the roads query alone took several Overpass rounds)
- [ ] Singapore is the sharpest test of our largest known bias: it has world-class drainage and we have no drainage data, so pooling should be overstated there more than anywhere. Worth measuring rather than asserting

## P3 People, routes, heat (F7, F8, F6)
- [ ] Modelled population from WorldPop and census totals, with the 74 to 76 ward crosswalk (F7)
- [ ] Critical places from OSM and Places (L13)
- [ ] Routes through pooling spots, and areas left with no clear route (F8)
- [ ] Heat by area from Landsat (F6): timebox it, first thing to cut

## P4 Decide (F9, F10)
- [ ] City capabilities from documents: 5 crore preparedness line, 50,000 per ward debris, Operation Breakthrough canals (L14, A11)
- [ ] Preparation candidates and greedy ranking under the budget
- [ ] Planner (A5) drops what the city cannot do and explains trade-offs
- [ ] Before / after exposure (F10)

## P5 Agents (A1, A2, A3, A6, A7) — the layer, built 12 Oct
- [x] `engine/bih_agents/`, on Cloud Run, with the trace streamed to the screen as newline-delimited JSON while the run is still going. Not server-sent events: the browser reads it with a plain fetch and EventSource cannot POST
- [x] Coordinator (A1): scenario → tools → narrate ⇄ verify, emitting every step including the failures. Never raises for a model failure; the screening answers regardless
- [x] Scenario (A2): question → parameters. **A figure only counts if it is in the asker's own words** — the agent returns the exact substring it took the number from and code checks that substring really is in the question. "A really heavy storm" cannot become 150 mm; it falls back to the slider the user can see
- [x] Forecast (A3): a tool, not a model, until there are bulletins worth reading. Only runs when the question is actually about what is coming — measured: without that restriction "which spots fill first here" silently replaced the user's 100 mm with a 0.3 mm forecast
- [x] Narrator (A6): writes from a facts bundle and nothing else. Output is separate claims, not prose, so one bad claim can be removed without guessing where a sentence ends
- [x] Verifier (A7), **the point of the layer**. Two checks that fail differently: the arithmetic check is code and matches every figure in the draft against the bundle (rounding and percentage forms allowed, nothing else); the claim check is a model and catches the draft whose every number is real and whose meaning is wrong. Fails closed — an unsupported claim is removed not softened, a failed headline or missing caveat withholds the whole briefing, and an unreachable Verifier publishes nothing
- [x] Eight named failure modes, one planted draft each plus a clean one: all eight caught, clean one passed, 2.9 s per draft on `gemini-3.8-flash` at low thinking. **Measured, not assumed:** the first version ran at high thinking and took 63 s for no extra catches, and two of the eight rules (scope, unattributed) exist because the probe found drafts nothing caught
- [x] Caught live on the first real run: the Narrator wrote that a spot fills "starting early" — fill order is an order, not a clock — and the Verifier sent it back
- [x] `nearest_road` filled in at last (it was `None` since P1): spots are named by the road they sit on, within 120 m and named roads only. 87% of spots named in Palarivattom, 48% in Vyttila. "The low point beside Pipeline Road" instead of `d_28`
- [x] 95 tests. The agent tests stub both models out: what is worth testing is the part that holds when the model is wrong
- [ ] Planner (A5): needs P4 capabilities first
- [ ] News (A13): GDELT stored path; grounded panel display-only and never stored
- [ ] Firestore for runs and traces (never grounded output). Runs are currently not persisted at all
- [ ] Follow-ups that change the existing scenario ("what about twice the rain?") — each question is independent today
- [ ] Malayalam briefing. The honest order is verify the English, translate, then re-check the figures in the translation

## P6 Web app (F1, F11, F12, F13)
- [x] Modal tool palette on the map (Move / Box / Erase, keys V / B / E / Esc), both map overlays collapsible, Google's own controls moved clear of them
- [x] City switcher. Adding a region is a config entry in `engine/regions.py` plus the staging tools — no engine change, because the screening is terrain physics and not city-specific
- [x] Map with rectangle tool, 2D. Drag-to-draw is built on plain map mouse events, **not** the Maps drawing library: the one interaction the product depends on should not have an extra dependency to fail. Drawing is armed by the button so an ordinary drag still pans
- [x] Preset areas (Edappally, Palarivattom, Vyttila, Kakkanad - all named in the 28 May 2024 reporting) and a map-failure fallback, so the analysis is reachable even if Maps does not load. `gm_authFailure` and a load timeout now report why instead of leaving a grey box
- [x] Pooling spots layer, coloured by rank, click for catchment and fill order
- [ ] Fill-sequence animation (order, not a clock)
- [x] KSDMA layer shown alongside under the screening, never merged; return-period selector; agreement stated with its baseline
- [x] Ask box, streamed agent trace and the briefing in the panel. Removed claims are shown with the rule that removed them, not hidden
- [ ] Malayalam area messages (drafts, never sent)
- [x] Resolution caveat and per-layer confidence rendered on every selection (F12)
- [ ] News panel

## P7 Prove it (F15, F16)
- [ ] Replay 28 May 2024: score top-ranked spots against named flooded localities and against the KSDMA raster
- [ ] Seeded synthetic city and world agents (A14); 20 scenarios; detection rate, false alarms, success index
- [ ] Report simulated and real scores separately, never averaged

## P8 Ship
- [x] Smoke-tested on the public URL: all four presets, both tiers, and the error paths (outside region, inverted box, over 120 km2, unpublished return period)
- [ ] Demo script and rehearsal; one prepared answer per "we will not claim" limit
- [x] README: the two tiers, the enrichment result, the staged-vs-live architecture, measured timings, data provenance and licences, and what is not built yet
- [ ] Submit

## Proposed schedule (adjust freely)
| Day | Focus |
|---|---|
| Mon 12 | Finish P1: metric, OSM layers, GCS staging, engine service |
| Tue 13 | P2 and the core of P3: KSDMA, return period, forecast, people, routes |
| Wed 14 | P7 replay scoring and P3 people; P4 preparations if time |
| Thu 15 | P6: web app working end to end on one box |
| Fri 16 | P7 and News: replay scoring, harness |
| Sat 17 | P8: deploy, demo script, rehearsal, buffer |
| Sun 18 | Submit. No new features. |

## Cut list, in this order, if behind
1. F14 Watcher (the plan already marks it "next")
2. F6 Heat
3. 3D view (already optional)
4. Harness from 20 scenarios to about 8
5. Malayalam messages (keep English briefing)

## Needs a decision
- OK to commit and push `engine/`, `data/deltadtm_kochi.json`, `PROGRESS.md`?
- Submission format (live demo, video, repo link, or a mix). The plan doesn't say, and it affects P8.
