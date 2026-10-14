# 03. What is left

Written at the end of **Wed 14 Oct**. **Submission is Sun 18 Oct.** The standing rule: nothing is deferred to
"after the hackathon". Everything below has a day, a "done means", and a place on the cut list.
Sizes are rough estimates (S under 1 h, M 1 to 3 h, L half a day or more), not measurements.

## Status after the Thursday list (15 Oct)

| Item | State |
|---|---|
| P0.1 live check | **Done in headless Chrome against the live URL, both cities**: nine layers load, surface heat on a box and the Heat-tab *Show*, the time bar plays, the ordinary-day card at 10 mm and the ₹22,80,000 plan at 120 mm, Singapore switch, agent on population / heat / flood / whole-city. **One failure: the live air-quality tiles are blank.** The browser key (`before-it-hits web (dev)`) allows only `maps-backend` and `geocoding-backend`, so Google answers 403 `API_KEY_SERVICE_BLOCKED` for `airquality.googleapis.com` and Chrome drops the tile (`ERR_BLOCKED_BY_ORB`). Fix, not yet applied (it changes a key's restrictions): `gcloud services api-keys update 33d387e4-9f83-4567-b70f-ae9f739899dc --project before-it-hits --api-target=service=maps-backend.googleapis.com --api-target=service=geocoding-backend.googleapis.com --api-target=service=airquality.googleapis.com` |
| **New bug, found by the live check** | A population question for a box answered with the **whole city's** population ("roughly 2,117,990 people" for 19 km²). Fixed (`density.people_in_area`, test added); the live site still has it until the next deploy. A local Edappally box now says about 72,000 |
| **P1.5 population grid (decided and built 15 Oct)** | `engine/tools/fetch_population.py` stages `pop.tif` (GHS-POP 2020, 100 m, through people per m²; the staged total matches the source's own to 0.1%); `bih_engine/popgrid.py` reads it; exposure, the density layer and the population answer all go through it, with the footprint method as the fallback. **Measured, not argued**: `validate_population_sg.py` scores both against SingStat's Census 2020 by planning area, 48 areas: footprint-only puts **33.6% of the city in the wrong planning area (median area off 62%, rank correlation 0.51)**, the grid **7.0% (12%, 0.96)**. WorldPop was tried first and lost to GHS-POP on both the census (median 18%) and on landing on mapped buildings. Two honest limits: the census counts residents only, and Kochi has no small-area truth. About a third of Kochi's grid people sit in cells with no mapped building (OSM is partial), so inside a spot they are spread evenly over the cell |
| P0.4 Singapore in git | `.gitignore` whitelists `data/regions/singapore/region.json` only (the rasters stay out). **Needs the user's commit** |
| P1.1 cross-links | Built both ways, 3 tests, driven in headless Chrome locally; **not deployed** (Friday). Direction "screen it" was driven with a hand-built news answer, because GDELT answered 503 for Singapore that day; the grounding of the place was checked with the real model on canned headlines |
| P1.4 truthfulness | (a) heat entry in `hazards.py` now mentions the satellite; (b) Narrator wording for a place with no named road, and the Heat tab's tag, now say "no named road close by"; (c) Guide reference mentions the cross-links |
| P0.3 README | Refreshed (heat, air, layers, clock, routing, cross-links, counts, data table). Still to do Saturday: measured timings, and the air-quality layer sentence assumes the key fix above |

## Where we are against the 13 Oct plan (`plan/MULTI_HAZARD.md`)

The plan was Wed: heat engine; Thu: heat tab and layer switch; Fri: compound answer, cross-links; Sat: freeze.
**Heat, air quality, layers, the clock and the satellite heat picture are done and live, a day ahead of it.**
Every item on that plan's cut list is built (density view, air overlay, traffic toggle, heat spatial ranking).
What remains of the plan is the **cross-hazard news work** and the **ship work**.

## Schedule

| Day | Work | Done means |
|---|---|---|
| **Thu 15** | P0.1 live check of everything shipped; P0.4 Singapore in git; P1.1 News ↔ engine cross-links (build); P1.4 small truthfulness fixes; P0.3 start README | live URL checked in a real browser on both cities; cross-links working locally with tests |
| **Fri 16** | P1.1 deploy; P1.2 compound answer with a "reported" news section; P1.3 hazard-driven news topics (cheap version); decide P1.5; P0.2 draft the demo script | one question answered across hazards, verified; demo script v1 |
| **Sat 17** | **FREEZE at midday: no new features.** P0.2 rehearse; nine prepared answers; P0.3 README and PROGRESS final; P0.5 backup plan; final deploy; live smoke test on both cities | submission-ready, rehearsed at least twice, a fallback recording exists |
| **Sun 18** | Submit. No new features | submitted |

If behind, cut in the order in "Cut list" below. **Never cut P0.**

## P0: must be done before submission

| # | Item | Size | Why / detail |
|---|---|---|---|
| P0.1 | **Verify the live site end to end, in a real browser, on both cities.** | M | Locally everything was driven in headless Chrome; the live site was only smoke-tested through its API. Check: the nine layers; the thin time bar playing; surface heat on a box then the Heat tab *Show*; the agent on a population, heat, flood and whole-city question; Singapore switch; the ordinary-day card at 10 mm and the plan at 120 mm. **Specifically unverified: the Google air-quality heatmap tiles on the live site** (the browser key must be allowed to call `airquality.googleapis.com`; if the layer is blank, add it in the GCP Console) |
| P0.2 | **Demo script and rehearsal, with one prepared answer per refusal.** | L | `PROGRESS.md` still lists it as undone. Nine "we will not claim" limits (`01-product-scope.md`); a judge can ask any of them. A suggested flow is at the end of this file |
| P0.3 | **Refresh `README.md`.** | M | The public README is stale: it covers flooding, the two tiers and the replay well, but says nothing of heat, air quality, the layer system, the clock, topic routing or the satellite picture, and its test and layer counts are old. This is what a judge reads first. Also finish `PROGRESS.md` |
| P0.4 | **Singapore provenance in git.** | S | `.gitignore` whitelists only Kochi's `region.json`, so the repo link carries no Singapore manifest (its sources, licences, population, `surface_heat`). Add `!data/regions/singapore` / `!data/regions/singapore/region.json` the way Kochi's is done. Decide whether to also commit the small rasters or leave them reproducible. The user commits |
| P0.5 | **Demo backup.** | M | The demo depends on Gemini, GDELT and the Weather API, live. Have: a pre-warmed instance, a recorded run-through as a fallback, and a known-good box and question per city. Decide what to say if GDELT is slow (it is thin for Kochi: lean on Singapore for news) |

## P1: features worth finishing, in order

| # | Item | Size | Detail and done means |
|---|---|---|---|
| P1.1 | **News ↔ engine cross-links** | L | A news answer that names a place offers "screen it" (runs the Agent tab for that place); an engine answer offers "what is being reported here" (runs the news agent for that place and hazard). The News agent and the Agent chat are separate today. Done: both directions work for a preset and for a named place; tests with both models stubbed; news stays labelled *reported* and changes no number |
| P1.2 | **Compound question with a "reported" section** | M | "What is the risk in Edappally tomorrow, all things considered?" already runs flood, heat, surface heat and air quality in one verified briefing (`topic: overview`). Missing: the news. Add a separate, labelled "reported" block, built by the news agent, **never blended into a ranking or the Narrator's bundle**. Done: one question, one briefing plus a clearly separate reported block |
| P1.3 | **News topics from the hazard registry (cheap version)** | S/M | News topics are a list owned by `news_agent.py`; the welcome, the Guide and the intro read `hazards.py`. Make a hazard added to `hazards.py` get its news topic and Guide text for free. **Do not** do the full registry refactor in the 13 Oct plan (`screen`/`facts`/`limits` per hazard); there is no time and no benefit for a demo |
| P1.4 | **Small truthfulness fixes** | S | (a) `hazards.py`'s "heat" entry describes only the forecast; add "and which parts of an area run hotter, from a satellite" so the welcome message and Guide match what exists. (b) Narrator phrasing when a place has no named road reads awkwardly ("a spot that is not near a named road"): tighten the prompt example. (c) Update the Guide reference for anything P1 changes |
| P1.5 | ~~Per-area population~~ **DONE 15 Oct** | L | Built and measured: GHS-POP 2020 grid scaled to the published total, placed on buildings inside a spot. Against Singapore's census by planning area it misplaces 7% of the city against 34% for the footprint method it replaced. Kochi has no such check. See the status table |
| P1.6 | People in the hottest parts | M | The footprint-share method (`exposure.estimate`) fed the surface-heat hot patches. In the original plan, not built. Only if P1.5 is skipped and time remains |

## P2: polish found while testing

- **Surface-heat blockiness at high zoom.** Cells look square at zoom 16 and above. That is honest (the sensor
  is about 100 m) and the layer note says so, but a slightly softer upscale or a visible "about 100 m" next to the
  scale may look better. Do not sharpen.
- **Verifier "sent back" loops** add about 6 s when they trigger. Acceptable; it is also the best demo moment.
- **No service-level tests.** There are 285 engine and agent tests but none that call the FastAPI routes
  (`TestClient`). A handful for `/api/surface-heat`, `/api/weather-grid`, `/api/ask` with stubbed models would
  have caught the Singapore row-count and the topic-routing bugs earlier. Worth an hour if time allows.
- **Narrow-screen check** of the time bar, field scale and layers panel (not verified).
- **`prefers-reduced-motion`** and contrast of the white-on-tint time bar were not audited.
- `scratch/` (gitignored) is full of probes; harmless.

## P3: planned, not built, **not for this deadline**

Say so plainly if asked; each is in `PROGRESS.md` as an open box. Firestore persistence of runs and traces (runs
are not persisted at all); follow-ups that *change* the previous scenario ("what about twice the rain?": each
question is independent today); Malayalam briefings and area messages (verify English, translate, then re-check the
figures); critical places from OSM/Places (L13), which would also make a real "areas cut off *from* a hospital"
analysis possible; runoff coefficient from land cover (L8) instead of a flat 0.8; GLO-30 terrain fallback outside
DeltaDTM coverage; staging to Cloud Storage; fill-sequence animation (an order, never a clock); F14 Watcher (push
alerts); F16 test harness with synthetic worlds and a success index; a fairer replay using a lower-return-period
event; measuring (not asserting) the Singapore drainage bias; a second news source (a licence decision).

## Cut list (what can still be cut, in order, if behind)

1. P1.6 people in the hottest parts
2. ~~P1.5 per-area population~~ (done)
3. P1.3 hazard-driven news topics
4. P1.2 the "reported" block in the compound answer (keep each answer on its own)
5. P2 polish
6. P1.1 cross-links, last, because it is the most visible new idea in the plan

**Never cut:** P0.1 live verification, P0.2 the demo script and rehearsal, P0.3 the README, and the Saturday freeze.
A flood answer that never fails on stage is worth more than three that sometimes do.

## Risks, in order of how likely they are to hurt the demo

| Risk | Mitigation |
|---|---|
| **GDELT is slow, rate-limited and thin for Kochi** (3 flood headlines in two weeks, none for heat or air) | the cache is warmed at start-up; the agent says so rather than invent; lead the news demo with Singapore |
| **Gemini model availability or latency** on the day | model names are env-overridable (`BIH_FAST_MODEL`, `BIH_CAREFUL_MODEL`); the screening still works without them; recorded fallback (P0.5) |
| **Cold start** (about 8 s) | `--min-instances 1`; hit `/api/health` before presenting |
| **AQ tiles blank on the live site** | verify in P0.1; add `airquality.googleapis.com` to the browser key's allowed APIs if needed |
| **A key restriction is not what the docs say** | the three keys' current restrictions were not re-inspected on 14 Oct; check in the GCP Console (04) |
| **Judges misread surface heat as air temperature** | the layer note, the Heat-tab fold, the scale label and the Verifier all say it; rehearse the one-sentence answer |
| **Overclaiming under questioning** | the nine prepared answers (P0.2) |
| **Repo link shows only Kochi** | P0.4 |
| **No persisted runs** | acceptable; a live demo does not need them |

## Decisions needed from the user

1. ~~Per-area population (P1.5)~~: decided yes on 15 Oct and built.
2. **Singapore in git (P0.4):** whitelist its `region.json`, and its rasters or not?
3. **Backup recording (P0.5):** record one, and who narrates?
4. **Demo emphasis:** lead with Kochi (validated tier, the replay) or Singapore (news, live gauges, the drainage
   bias)? Suggested: Kochi for depth and honesty, Singapore for the second city and live data.
5. **Malayalam:** confirm it stays out for this submission.
6. Which P1 item to drop first if Friday goes badly.

## A suggested demo flow (unrehearsed: a starting point, not a script)

1. Open the live URL (Kochi). One sentence: "Cities warn for the whole city; water gathers in the same low spots
   every year. This answers at the scale of a street."
2. **Whole-city question, no box:** "What's expected in Kochi tomorrow?" Shows the agent ranking presets and the
   live forecast.
3. **Draw a box over Edappally**, ask "which spots fill first here?". Narrate the trace folding to "Checked by the
   verifier"; open it; point at any removed claim. This is the heart of the product.
4. **Details:** roads that go under first (press play on the thin time bar); the government flood map beside our
   screening ("shown as published, never merged"); how sure are we.
5. **What can the city do:** at light rain it says "nothing to buy" and spends ₹0; at 120 mm it gives a costed plan
   inside the city's real ₹5 crore line, with a labelled best-case before/after.
6. **Layers:** Heat index (play the clock), then **Surface heat**: "the forecast says about one number for this
   box; the satellite sees the Stadium Round corner run 4 °C above the rest". Open the Heat tab, press *Show*.
7. Ask "how hot is it right now" to show topic routing: four steps, no flood engine.
8. **Switch to Singapore:** no official layer, and the UI says so; live NEA gauges; richer news.
9. **Close on the limits:** the nine refusals, the replay honestly reported as non-discriminating, and Vyttila as
   the expected miss. The limits are the pitch.

## Definition of done for submission

- [ ] Live URL up, warm, both cities, checked in a real browser after the final deploy
- [ ] The nine prepared answers rehearsed; demo run through twice; a fallback recording exists
- [ ] `README.md` current (heat, air, layers, clock, routing, satellite heat, counts, measured timings)
- [ ] `PROGRESS.md` final; `docs/handoff/` updated if anything above changed
- [ ] Singapore manifest in git; repo link resolves; secrets absent (`.env.local` is ignored; check `git log -p` for keys)
- [ ] 285+ tests green; no console errors on a full run-through
- [ ] Submitted by Sun 18 Oct
