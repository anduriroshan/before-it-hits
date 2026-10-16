# Before It Hits — live demo script

**Written for: the presenter, demoing live to the AI Builder Cup judges.** Live URL:
https://before-it-hits-z5r5gsxkza-el.a.run.app · Target length: 5 minutes + Q&A. Have the tab open and warm
(the service keeps one instance hot) before you start.

The one sentence to open and close with: **"Every number you see is computed by a deterministic engine and
checked by a separate agent — the language model never produces a figure, and it says so when it can't."**

---

## Before you start (2 minutes, off-stage)
- Open the live URL in a clean browser window, full screen. Pick **Singapore**, click the **Geylang / Kallang**
  preset once so the first analysis is already warm. Then click **New chat** to reset for the live run.
- Have a second tab on **Kochi** → **Edappally** warm too (for the official-map beat).
- Zoom the browser so the panel text is readable from the back of the room (Ctrl + once or twice).
- If the network is shaky, the backup recording (see "Backup" below) plays the same flow.

## The flow (what to click, what to say)

**1. The problem (20s, no clicks).**
"A city gets one rain warning for the whole city. But water collects in the same low streets it did last year.
An officer preparing for a storm needs the answer at the scale of a neighbourhood — not 'Singapore', but *which
streets*." 

**2. Draw the question (40s).** Pick **Singapore**, click **Geylang / Kallang** (or press **Box** and drag a
rectangle). 
- Say: "I draw a box and it immediately pulls the live rain forecast, runs the terrain engine, and writes a
  checked answer." 
- Point at the **blue pooling polygons** on the map and the **answer**: "These are the low spots, ranked 1 to 5.
  The answer names the roads through them and roughly how many people live there — and the caveat always says
  stormwater drains aren't in the data, so it's a screening, not a flood forecast."
- Click the **"Checked by the verifier · N steps"** line to expand the trace: "You can see the agents that ran —
  the model reads the question and writes prose, the engine produced every number, and a checker verified each
  one traces back."

**3. The honesty — this is the differentiator (60s).** Type each, let the answer land, move fast:
- **"How deep will the water get?"** → "It refuses to give a depth, because a 30-metre terrain model has half a
  metre of error — the size of the depth itself. It ranks instead. Most demos would make up a number here."
- **"Will my house flood?"** → "Declines a single building. It only speaks at area scale."
- **"Is it safe here?"** → "It won't give a safe/unsafe verdict or combine hazards into one score — it gives each
  hazard separately and says they're not merged."
- Say: "These refusals are written down before we built anything. If the product ever crosses one, that's a bug."

**4. The traffic answer — the one that started this (30s).** Type **"How would this affect traffic here?"**
- "It says first that it holds no traffic data — then gives what published studies actually found for rain of
  this strength, the roads through the low spots, and the limits of those studies. It never invents a jam."

**5. The map is alive (30s).** Open **Layers**, toggle **Surface heat (satellite)**, then **People per km²**,
then **Wind**. "The same box answers for heat, population, wind, air and earthquake — each in its own lane, each
on one shared clock." Press **play** on the time bar to show the rain stepping forward.

**6. The validated tier (40s).** Switch to the **Kochi** tab (Edappally warm). Open **Details** →
**"Compare with the government's flood map"**.
- "In Kochi, an authority publishes a depth map, so we show *their* depths unchanged, and we report how often our
  screening agrees with them — 1.25 to 2.78 times better than chance. Where no authority publishes, like
  Singapore, we say so instead of pretending."
- Scroll to **"What the city can do"**: "And it ranks preparations against the city's real budget line, with an
  honest 'nothing to buy today' when the rain is ordinary."

**7. Close (15s).** "Two cities, and adding a third is a config file — no engine change. Every figure computed and
checked; every limit stated out loud. That's **Before It Hits**." Repeat the opening sentence.

## Prepared answers for the questions a judge will ask

| If a judge asks / types | Say / show |
|---|---|
| "Can it tell me the exact flood depth?" | No — and that's deliberate. Show the depth refusal. 30 m terrain = ~0.5 m error = the depth itself. Kochi shows the *authority's* depths, attributed, never ours. |
| "How do I know the AI isn't making numbers up?" | Expand the verifier trace. Every number must trace to the engine's output; the checker is a second model plus an arithmetic check in code. Unsupported claims are removed or the answer is withheld. |
| "What if I ask in another language?" | It understands the question (try Hindi/Malay), but answers in English today — a known, stated limit. |
| "Is this using Google Cloud?" | Cloud Run, Google Weather API, Air Quality API, Maps; Gemini for reading and writing (on Vertex AI); Earth Engine for terrain, population and surface heat. |
| "What's the weakest part?" | We over-state pooling where drains work — we have no public drainage data — and we say so on every run. Singapore is the clearest case; that's the honest bias, made visible. |
| "Can it tell people to evacuate?" | No. Type "Should we evacuate?" — it refuses. It's a preparation tool; Google's terms bar emergency-response use and we couldn't validate dispatch anyway. |
| "Does it work for a city you didn't prepare?" | Typing a place runs the browser geocoder and draws a box; the screening runs anywhere there's terrain. The two deep cities are Kochi and Singapore. |
| "Prove it predicts real floods" | Details → "Tested against a real storm": the 28 May 2024 Kochi cloudburst, reported as an honest negative (at 100 mm/hr most of the area floods, so the test doesn't discriminate). We show the failure, not hide it. |

## If something breaks on stage
- **An answer is slow (>20s):** keep talking through the trace; it is reading the live forecast and checking.
- **An answer is withheld** ("briefing was withheld"): that's the safety net — say so. "The checker couldn't
  support a claim, so it refused to show it. That's the product working." Ask the question again or move on.
- **The map is grey / Maps error:** reload once. The pooling result still computes; open **Details** to show it.
- **Total failure:** switch to the **backup recording**.

## Backup
Record a clean 3–4 minute screen capture of the flow above against the live URL the day before, with audio.
Keep it on the demo machine and on a phone. A judge values a smooth recorded run over a live run that stalls.

## Do NOT, on stage
- Don't claim a depth, a time-to-flood, a probability, a traffic jam, a casualty count, or that an area is "safe"
  — the product refuses these on purpose; don't undercut that by saying them yourself.
- Don't say "AI predicts" — say "the engine computes, the model writes, the checker verifies."
