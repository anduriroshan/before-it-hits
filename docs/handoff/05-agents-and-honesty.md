# 05. The agent layer and the rules no change may break

This is the most important document to read before touching `engine/bih_agents/`. The product's value is that
it is *checkably* honest. Most of the ways to damage it are quiet: a prompt example, a string in the facts
bundle, a hard-coded check. Each one is described here with the incident that taught it.

## The rule

> **The language models decide what to ask. The engine decides what is true.**

- A model **never produces a figure.** Every number a user reads comes from the engine or a named API, and
  that is enforced **in code** (`verifier.py`), not asked for in a prompt.
- A model failing **degrades** the product, it never breaks it. Every caller can say "the briefing is
  unavailable" and still show the screening, because the screening is computed, not written.

## The flow of one question (`coordinator.run`)

```
message + history
   │
   ▼
A2 Scenario  ── model ──▶  spec {intent, topic, scope, mode, rainfall, duration, place, audience}
   │                        settle(): a figure only survives if its exact words are in the question
   │
   ├─ scope=city & analyse ──▶ _run_city_overview: forecast + engine over each preset, ranked
   ├─ intent=explain        ──▶ A8 Guide answers from the written reference, no analysis
   ├─ place named, no box   ──▶ "place_needed": the BROWSER finds it and asks again with a box
   ├─ no box                ──▶ asks for an area
   ├─ topic ∈ {population, heat, air_quality} ──▶ _run_topic: fetch only that data
   │
   └─ topic ∈ {flood, overview}:
        A3 Forecast (only if the question is about what is coming)
        A4 Engine analyse_area            ← the only thing that makes a figure
        facts.build  (+ heat, surface heat, air quality, best effort)
        ┌───────────────────────────────────────────┐
        │ A6 Narrator drafts claims from the bundle  │  up to MAX_REVISIONS = 2 trips
        │ A7 Verifier: arithmetic + claims           │◀─ sent back with the problems
        └───────────────────────────────────────────┘
        enforce(): remove failed bullets; a failed headline or caveat withholds the whole briefing
```

Every step is emitted as it happens, **including failures**. A trace that shows only the happy path is
decoration; this one shows a forecast outage, a model that could not be reached, and each claim removed.

## The facts bundle (`facts.py`): the only thing a model is shown

The Narrator writes from the bundle and nothing else, and the Verifier's hard check is simply: **a number may
appear in the briefing only if it appears in the bundle.** Sections: `area`, `rainfall`, `pooling`,
`roads_through_the_worst_spots`, `confidence`, `limits`, `official`, `people`, `agreement`, `heat`,
`surface_heat`, `air_quality`. Topic bundles (population, heat, air quality) carry only what was fetched.

### Things kept out of the bundle, on purpose

- **Any depth in metres from our own model.** `assert_no_own_depth` fails loudly if a key containing "depth"
  appears outside `official`. The authority's own depths are included under `official`, labelled as theirs.
- **Dates and version strings**, and **any digits in descriptive prose.** Every number anywhere in the bundle
  (including inside strings and keys) joins the set of figures a briefing may quote. A stray "30 m" or "2022"
  in a sentence makes `30` and `2022` quotable. **Incident (14 Oct):** the surface-heat `source` string said
  "at about 30 m" and a test caught that a "30 °C" claim would have passed the trace check. Descriptive text is
  now written without digits, and `test_surface_heat.py` guards it.
- **Key names are prompt text.** The Narrator reads them literally. A key called `share_..._pct` came back as
  "a 1.2 pct share of box". Write keys the way a sentence would want them
  (`this_box_runs_hotter_than_the_whole_city_by_c`).

## The Verifier (`verifier.py`): two checks that fail differently

1. **Arithmetic, in code.** Every figure in the draft is extracted by regex and matched to the bundle, allowing
   rounding (0 to 2 decimals) and percentage forms and nothing else. A model cannot argue with it.
2. **Claims, by a model** (`CAREFUL_MODEL`, low thinking). Catches a draft whose every number is real and whose
   meaning is wrong ("most of the area floods" has no numbers). Rules: `unsupported`, `own_depth`,
   `unattributed`, `scope`, `timing`, `dispatch`, `overstated`, `certainty`.

Plus deterministic checks: `check_caveat` (the caveat must mention drains **for flood and overview answers
only**), `check_official_units` (if the authority never stated its unit, the word "inferred" must appear).

**Fails closed.** An unsupported claim is removed, not softened. A failed headline or a missing caveat withholds
the whole briefing. If the Verifier cannot be reached, nothing is published.

**Measured, not assumed:** at high thinking the claim check took 63 s for no extra catches; at low it takes 2.9 s
and catches all eight planted drafts. Two of the eight rules (`scope`, `unattributed`) exist because a probe
found drafts nothing else caught.

## Topic routing: what a new check must respect

Adding something to the one path every question takes is the way this broke before. On 14 Oct a population
question ran the 8 s flood engine, heat and air quality, then printed "What-if · 0 mm". The user's reaction was
"isn't the agentic architecture planned well?". The rules now:

- The Scenario agent returns a `topic`. The Coordinator runs **only what the topic needs.**
- Anything **topic-specific must be conditioned on the topic.** The Verifier's "mention stormwater drains" check
  rejected every population and heat answer until it was conditioned on `topic in (None, flood, overview)`.
- UI chips and the trace show only work that ran. No rainfall or pooling chip unless the engine ran.
- Prompts are module-level constants: **restart the dev server** for a prompt change to take effect.

## What each hazard's bundle must say, and the Narrator must not

| Source | Must say | Must never say |
|---|---|---|
| Flood screening | rank is relative to the box; limits; drains are not in the data | a depth, a time of arrival, "most of the area" |
| `official` | whose layer it is, its unit, "inferred" if the unit is inferred | attach their depth to one of our spots |
| `heat` | a point forecast for the air, not a map | |
| `surface_heat` | "surface"; typical of a dry-season morning, **not today, not a forecast**; patches a few blocks across | compare it with the heat index or air temperature; call it "the temperature" or "how hot it is"; describe a single building or roof |
| `air_quality` | a regional reading, the provider's figures | a health effect for a person |
| `people` | roughly, by area | a number for one building or home |
| anything | the box, not the city ("in this box") | "across Kochi" about a box's findings |

## Other agents

- **Scenario (A2)** never invents a rainfall. It returns the exact substring the number came from, and `settle`
  checks that substring is in the question. "A really heavy storm" falls back to the slider the user can see.
  "The same storm" is carried over by *code* from the previous answer, never by the model. A named place must
  literally appear in the message (it cannot be taken from history).
- **Guide (A8)** answers from a written product reference (`guide.py`). It may use only figures from the
  reference or earlier verified answers. A hazard not in `hazards.py` is "not available yet" and must not be
  described. **Update the reference when a feature ships**; it is how the product describes itself.
- **Planner (A5)** may use no number the costed list does not already contain, and no dispatch word. It fails
  closed to no note.
- **Road outlook** invents no measurement; every figure is checked against the findings; fails closed.
- **News (A13)** is display-only by contract. The model picks topics from a list *we* own (it never writes a
  query); the answer is built only from the headlines fetched; **code deletes** any claim that cites no headline
  or carries a figure no headline has, and shows the deletion. It reports and never advises. A planted
  "SYSTEM COMPROMISED" headline was ignored: a claim needs a citation and a traceable figure to survive.

## Honesty patterns in code that are easy to remove by accident

- **Null is not zero.** A cell, hour or point with no value stays `None` and renders transparent or is dropped
  from the sum. A gap must never read as cold, dry or empty. (density, weather grid, surface heat)
- **A render fault is not a provider outage.** `draw(render, intoId)` in `app.js` separates them.
- **Scope travels with the answer.** Heat, Air, surface heat and the weather grid return a `scope`
  (`box` or `city`, with km² and a note) and the UI prints it.
- **Colour scales are stable.** The weather fields use fixed bounds; surface heat uses the *city's* p2 to p98.
  Rescaling to a box paints a degree of difference as an emergency.
- **A place only counts if it stands out.** Surface-heat "hottest places" must be in the box's hottest tenth
  *and* a local maximum within 500 m. Without both, one hot patch was padded to three places of ordinary
  ground, and one block was reported at both its edges.
- **The ordinary day.** `preparations.rank` returns "nothing to buy" when the rainfall is light by the IMD's own
  daily classes (up to 15.5 mm) or when no spot fills. A named outside authority's threshold, never one we
  invent. Never default a decision panel to "spend money".
- **Only claim the resolution you have.** The Landsat thermal sensor resolves about 100 m; the grid is 30 m.
  Saying "30 m" was a real error and was corrected in a dozen places.

## Guard rails for any new agent or data source

1. Does a model ever see a number it could repeat that is not in the bundle? Fix the bundle.
2. Does any string you add contain digits? Remove them or accept that they become quotable.
3. Could a stale or empty value render as a real one (0, cold, dry, "none")? Make it null and say so.
4. Does the answer say which patch of ground it is about?
5. Does it cross one of the nine refusals in `01-product-scope.md`? Then it is a bug.
6. Is it routed by topic, with a topic-conditioned verifier check if it needs one?
7. Does it fail closed (remove or withhold) rather than soften?
8. Is there a test that holds **when the model is wrong**? Stub the models; test the part that must still hold.
