# Handoff pack: Before It Hits

State at the **end of Wed 14 Oct 2026**. Submission is **Sun 18 Oct 2026**: a live demo plus a repo link.
Four working days remain: Thu 15, Fri 16, **Sat 17 (feature freeze)**, Sun 18 (submit only, no new features).

This pack exists so a fresh chat can start from files instead of from a very long conversation. Everything
here was checked against the code and the live service on 14 Oct; where something was *not* checked it says so.

## The product in one paragraph

You draw a box on a map of a city, or name a place, or ask about the whole city, and an agent tells you
what a hazard could do *there*: which low spots trap rain water and which roads run through them, how hot the
ground runs block by block, what the air is like, who lives nearby, and what the city can afford to do
before it arrives. A team of agents answers, and the whole run is streamed to the screen. The one design rule
that everything else serves: **language models decide what to ask; the engine decides what is true.** No model
ever produces a number, and a separate checker deletes any claim the engine's results do not support.

## Where things stand

| | |
|---|---|
| Live | https://before-it-hits-z5r5gsxkza-el.a.run.app (older alias `https://before-it-hits-1019287218541.asia-south1.run.app` also serves) |
| Deployed | Cloud Run `before-it-hits`, `asia-south1`, revision `before-it-hits-00029-5n2`, `--min-instances 1` |
| Tests | **332 passing** (`engine/tests`, 25 files) |
| Git | `main`, clean at `71aaf17` *before these docs were added* (the user commits by hand) |
| Cities | **Kochi** (the deep one: official depth map to check against) and **Singapore** (no official layer, best drainage in the world) |
| Hazards | Rain and flooding (deep), heat (forecast + satellite surface), air quality (live feed). News, traffic and population are overlays |
| Skills | `ponytail` (a "smallest complete change" coding ruleset) is installed at user level; say "stop ponytail" to turn it off |

## Read in this order

| # | File | Read it for |
|---|---|---|
| 1 | [`03-remaining-work.md`](03-remaining-work.md) | **What to do next**, the schedule to Sunday, the cut list, open decisions, known gaps |
| 2 | [`01-product-scope.md`](01-product-scope.md) | What the product is, what it claims, what it refuses to claim, what is out of scope |
| 3 | [`02-implemented.md`](02-implemented.md) | Everything built, by area, with file paths, endpoints and test counts |
| 4 | [`05-agents-and-honesty.md`](05-agents-and-honesty.md) | The agent layer and the rules no change may break |
| 5 | [`04-architecture.md`](04-architecture.md) | How it fits together: data staging, engine, service, web app, deployment |
| 6 | [`06-ui-design-language.md`](06-ui-design-language.md) | Layout, colour, components, copy tone, the honesty patterns the UI uses |
| 7 | [`07-runbook.md`](07-runbook.md) | Run it, test it, stage data, verify the UI, deploy, and the traps already hit |

Older, still-true documents: `README.md` (public; **stale on heat, air, layers**, see 03), `PROGRESS.md`
(chronological tracker, very detailed), `plan/` (the original spec as a static site; content in
`plan/assets/data.js`), `plan/MULTI_HAZARD.md` (the 13 Oct plan for heat, air and news).

## First 15 minutes in a new chat

1. Read this file and `03-remaining-work.md`.
2. `git status` and `git log --oneline -5`; from the repo root run the suite:
   `& .\.venv\Scripts\python.exe -m pytest engine -q` (expect 332 passed).
3. Start the dev server (`07-runbook.md`, "Run it locally") and open http://localhost:8080. It must be
   `localhost:8080`: the browser Maps key is referrer-restricted to that and to the Cloud Run URLs.
4. Skim `MEMORY.md` (it loads automatically) and the two newest memories,
   `before-it-hits-surface-heat` and `before-it-hits-agent-scope`.
5. Confirm with the user what they want first. The default is the Thursday list in 03.

## Standing rules from the user

These were given directly and still apply. Breaking one costs trust faster than any bug.

1. **Never run `git commit` or `git push` unless asked.** The user commits by hand. Report how many paths
   are uncommitted at the end of a piece of work.
2. **There is no "post-hackathon".** Never answer "later". Answer with a day, a "done means", and what gets
   cut first. ("Building one feature won't lead us to top 50.")
3. **PowerShell, not Bash.** The Bash tool is wrapped by an npm-audit hook that prints a blocking error when
   `jq` is missing, even though the command ran. PowerShell is unaffected.
4. **Never rewrite a repo file with a PowerShell `Get-Content`/`Set-Content` round trip.** It decodes
   BOM-less UTF-8 as ANSI and double-encodes every non-ASCII character (`·` becomes `Â·`). It cost a round of
   debugging on `web/app.js`. Use the Edit or Write tools, or a Python script run with `python -I`.
5. **Deploy without `--set-env-vars`.** That flag replaces all three API keys. Omitting it keeps them.
6. **Never print or hardcode key values.** Names only. The keys live in the gitignored `.env.local`.
7. **Verify UI changes in headless Chrome over CDP, then deploy, then smoke-test the live URL.** Say plainly
   what was and was not verified.
8. **Prefer agents to forms.** A whole-city question must not require drawing a box. Make the agent understand
   the request rather than adding a control.
9. **Always say which patch of ground** an answer is about: the drawn box, or the whole city.
10. **Report outcomes faithfully.** Failing tests, skipped steps and weak results are stated, not smoothed over.

## How this user works

Blunt, fast, high bar. Their critiques are specific and usually right ("you've just added percentages, think
better"). They ask diagnostic questions ("is it lack of zoomed-in detail?"): answer with a *measurement*, then
fix what is actually wrong. They want the work finished, verified, deployed and summarised in a few plain
lines: what changed, why, what it cannot do, what is uncommitted. They are aiming for the top of a hackathon
where polish counts as much as honesty, so a panel that looks like machine output loses on both.

## Repo map

```
engine/bih_engine/   deterministic engine: terrain, hydrology, routes, exposure, heat, surface heat, ...
engine/bih_agents/   the agent layer: coordinator, scenario, narrator, verifier, guide, news, planner, ...
engine/tools/        staging jobs (Overpass, Earth Engine, KSDMA, Landsat): never run at request time
engine/tests/        285 offline tests; the agent tests stub both models out
service/app.py       FastAPI: every /api route, static web files, start-up warmers
web/                 index.html, app.js (~3,200 lines, no build step), style.css
data/regions/<city>/ staged geography (gitignored except Kochi's region.json): see 04
replay/              the precomputed 28 May 2024 Kochi replay
plan/                the original plan as a static site
docs/handoff/        this pack
scratch/             throwaway probes; gitignored
```
