# 07. Runbook

Everything here was run on this machine (Windows 11, PowerShell) during the build. Commands are written for
**PowerShell**. Paths assume the repo root `C:\Users\anduri.roshan\Downloads\before-it-hits`.

## Environment

| | |
|---|---|
| OS / shell | Windows 11 Enterprise; PowerShell is primary. The Bash tool works but its npm-audit hook reports a blocking error when `jq` is missing (the command still ran): prefer PowerShell |
| Python | project venv `.venv\Scripts\python.exe` (**has numba**, so the full suite runs locally). The system Python 3.14 does not |
| Node | v24 (built-in global `WebSocket`; no puppeteer installed) |
| gcloud | SDK 577; named configuration `bih` → project `before-it-hits`, account `roshanandhuri@gmail.com` (personal). The default config is a *client* project: **always pass `--project before-it-hits`** |
| Docker | none; deploys use `--source .` (Cloud Build) |
| Chrome | used headless over the DevTools protocol to verify the UI |
| Secrets | `.env.local` (gitignored): `MAPS_API_KEY`, `WEATHER_API_KEY`, `GEMINI_API_KEY`. **Never print values** |

## Run it locally

`.env.local` must reach the server's process environment. `Start-Process` loses the environment, so run the
server as a background task from a script that loads it first:

```powershell
$root = "C:\Users\anduri.roshan\Downloads\before-it-hits"
Get-Content "$root\.env.local" | ForEach-Object {
  if ($_ -match '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$') {
    [Environment]::SetEnvironmentVariable($matches[1], $matches[2].Trim('"'), 'Process') } }
$env:BIH_NO_DENSITY_WARM = "1"      # optional: skip the start-up density warm-up in dev
Set-Location $root
& "$root\.venv\Scripts\python.exe" -m uvicorn app:app --app-dir service --host 127.0.0.1 --port 8080
```

Open **http://localhost:8080** (not `127.0.0.1`): the browser Maps key is referrer-restricted to `localhost:8080`
and the Cloud Run URLs. Health: `/api/health` → `{"ok":true,"regions":{"kochi":"…","singapore":"…"}}`. It can take
10 to 15 s to answer right after start-up while the warmers run.

**After changing any Python or a prompt, restart the server** (prompts are module-level constants). Static files
in `web/` are served from disk and need only a page reload. Stop an old server:

```powershell
Get-NetTCPConnection -LocalPort 8080 -State Listen | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force }
```

Environment switches: `BIH_NO_NEWS_WARM`, `BIH_NO_DENSITY_WARM`, `BIH_FAST_MODEL`, `BIH_CAREFUL_MODEL`,
`BIH_LLM_TIMEOUT_S` (40), `BIH_VERIFIER_THINKING` (low), `BIH_REGION_ROOT`, `BIH_REPLAY_ROOT`,
`BIH_TERRAIN_BASE`.

## Tests

From the repo root:

```powershell
& .\.venv\Scripts\python.exe -m pytest engine -q          # expect: 578 passed, about 10 to 30 s
& .\.venv\Scripts\python.exe -m pytest engine/tests/test_surface_heat.py -q
```

All offline. The agent tests stub both models out. There are no browser or route-level tests.

### The question battery: run it before any demo and after any prompt change

The unit tests cannot say whether a *model* routes a question correctly, and a prompt change can break a
question nobody thought to test. `engine/tools/question_battery.py` runs 146 realistic, awkward and adversarial
questions through the real pipeline (real Gemini, real engine, in-process, no server needed), imitating the
browser's history and preset-place resolution, and writes every answer to a JSON file:

```powershell
& .\.venv\Scripts\python.exe -X utf8 engine/tools/question_battery.py --out battery.json --workers 6   # about 6 minutes
& .\.venv\Scripts\python.exe -X utf8 engine/tools/question_battery.py --only G --out g.json            # one family (G = other hazards)
```

It grades nothing: it records what each question was routed to, the answer, the time, and how often the checker
sent a draft back, plus four cheap tripwires (a promised jam, a casualty count, a model-invented depth, a
"you should evacuate"). **Read the answers.** The tripwires false-positive on a refusal that quotes the phrase
and on an authority's own attributed depth; the point is a person looking at 146 answers. It is also the
prepared answer for every limit in the demo. Families: A core, B time, C traffic, D advice and facilities,
E things it will not claim, F cities, G other hazards, H news and history, I conversation, J units, K
underspecified, L languages, M several hazards, N whole city, O the conversation that started this, P and Q
added with the router, R a road named in the question. Add a question here the moment someone asks one that goes wrong.

`tzdata` is pinned in `service/requirements.txt`: "tonight" and "tomorrow" need the city's timezone and
`python:3.12-slim` does not reliably ship the system zone files.

## Stage data (only when adding or refreshing a city; needs `gcloud` auth)

```powershell
& .\.venv\Scripts\python.exe engine\tools\fetch_terrain.py   <city>              # DeltaDTM via Earth Engine
& .\.venv\Scripts\python.exe engine\tools\stage_region.py    <city> --buildings # Overpass → FlatGeobuf + region.json
& .\.venv\Scripts\python.exe engine\tools\stage_region.py    <city> --manifest-only   # rewrite manifest, no Overpass
& .\.venv\Scripts\python.exe engine\tools\fetch_lst.py       <city>              # Landsat surface temperature
& .\.venv\Scripts\python.exe engine\tools\fetch_population.py <city>             # GHS-POP grid -> pop.tif (about a minute)
& .\.venv\Scripts\python.exe engine\tools\validate_population_sg.py             # Singapore census check -> manifest
& .\.venv\Scripts\python.exe engine\tools\stage_official.py  <dir of KSDMA rasters>   # Kochi only (hard-wired)
& .\.venv\Scripts\python.exe engine\tools\replay_kochi.py                       # the 28 May 2024 replay
```

`stage_official.py` is **Kochi-specific**: it takes the directory of already-downloaded KSDMA rasters, not a city
name. `fetch_osm_kochi.py` and `fetch_deltadtm_kochi.py` are the original Kochi-only fetchers; `stage_region.py`
and `fetch_terrain.py` are the general ones.

`fetch_lst.py` takes about 1 to 2 minutes (the Earth Engine median is the slow step) and prints nothing until it
finishes. It writes `lst.tif` on the terrain's own grid and a `surface_heat` block into `region.json`, and crops
the one extra row Earth Engine sometimes adds. Months and years per city live in `engine/regions.py`.
Overpass can return 504 or 406 (it needs a descriptive User-Agent); retry later.

## Verify the UI (headless Chrome over CDP)

The pattern used throughout the build: start Chrome with a remote-debugging port, drive it from Node with the
global `WebSocket`, evaluate JavaScript in the page, screenshot, and **read the screenshot**. Screenshots caught
things JavaScript checks cannot (mojibake, a canvas that painted nothing, an overlapping panel).

```powershell
# adjust the path if Chrome lives elsewhere: (Get-Command chrome).Source
& "C:\Program Files\Google\Chrome\Application\chrome.exe" --headless=new --remote-debugging-port=9222 `
  --user-data-dir="$env:TEMP\bih-chrome" --window-size=1580,900 about:blank
# then confirm it is up:  Invoke-RestMethod http://127.0.0.1:9222/json
```

Minimal driver (`probe.mjs`; run with `node probe.mjs`). Note the global `WebSocket` has **no `.on()`**; use
`addEventListener`:

```js
const tabs = await fetch('http://127.0.0.1:9222/json').then(r => r.json());
const ws = new WebSocket(tabs.find(t => t.type === 'page').webSocketDebuggerUrl);
await new Promise(r => ws.addEventListener('open', r));
let id = 0; const waiting = new Map(); const errors = [];
ws.addEventListener('message', m => {
  const d = JSON.parse(typeof m.data === 'string' ? m.data : m.data.toString());
  if (d.id && waiting.has(d.id)) { waiting.get(d.id)(d); waiting.delete(d.id); }
  if (d.method === 'Runtime.exceptionThrown') errors.push(d.params.exceptionDetails.exception?.description);
  if (d.method === 'Runtime.consoleAPICalled' && d.params.type === 'error')
    errors.push((d.params.args || []).map(a => a.value || a.description || '').join(' '));
});
const send = (method, params = {}) => new Promise((res, rej) => {
  const i = ++id; const t = setTimeout(() => rej(new Error('timeout ' + method)), 120000);
  waiting.set(i, d => { clearTimeout(t); d.error ? rej(new Error(d.error.message)) : res(d.result); });
  ws.send(JSON.stringify({ id: i, method, params })); });
const ev = async e => (await send('Runtime.evaluate', { expression: e, returnByValue: true, awaitPromise: true })).result.value;
const sleep = ms => new Promise(r => setTimeout(r, ms));
await send('Runtime.enable'); await send('Page.enable');

await send('Page.navigate', { url: 'http://localhost:8080/?region=kochi' });  await sleep(6000);
await ev(`setArea([76.30, 9.96, 76.34, 10.00], 'Edappally test box')`);          // the app's own path for a box
await ev(`document.getElementById('tab-heat').click()`);                          await sleep(3500);
console.log(await ev(`document.getElementById('heat-surface').innerText`));
const shot = await send('Page.captureScreenshot', { format: 'png' });
(await import('node:fs')).writeFileSync('shot.png', Buffer.from(shot.data, 'base64'));
console.log('errors:', errors.length ? errors : 'none');  ws.close();
```

Useful hooks in the page (top-level functions and objects, not a module): `setArea(bbox, label)`,
`setLayerVisible(id, on)`, `dataLayers`, `state`, `clock`, `stepTo(step, frac)`. Layer ids: `heat`, `surface`,
`temperature`, `humidity`, `rain`, `wind`, `population`, `airquality`, `traffic`. Tabs: `tab-agent`, `tab-rain`,
`tab-details`, `tab-heat`, `tab-air`, `tab-news`. The Edappally test box used throughout is
`[76.30, 9.96, 76.34, 10.00]` (19.5 km²); Orchard Road in Singapore is `[103.825, 1.295, 103.845, 1.312]`.

**Test artefacts that look like bugs but are not:** `innerText` of anything inside a closed `<details>` is
empty; `#rain` has `step="5"` so a 9 snaps to 10; a probe that samples the first canvas may be sampling the
static population layer.

## Test an agent question without the browser

`/api/ask` streams newline-delimited JSON. **Decode the bytes as UTF-8 yourself** (see traps):

```powershell
$u = "http://127.0.0.1:8080"          # or the live URL
$body = @{ question = "which part of this area is hottest"; bbox = @(76.30,9.96,76.34,10.00);
           region = "kochi"; rainfall_mm = 100; area_label = "Edappally area" } | ConvertTo-Json
$r = Invoke-WebRequest "$u/api/ask" -Method Post -Body $body -ContentType "application/json" -UseBasicParsing -TimeoutSec 180
$ev = [Text.Encoding]::UTF8.GetString($r.Content).Trim() -split "`n" | Where-Object { $_.Trim() } | ForEach-Object { $_ | ConvertFrom-Json }
($ev | Where-Object event -eq step | ForEach-Object { "$($_.agent):$($_.status)" }) -join ", "
($ev | Where-Object event -eq done).briefing.headline
```

Expected shapes: a population, heat or air question is `A2 Scenario → <Topic> → A6 Narrator → A7 Verifier`
(about four steps, `topic` set, no `A4 Engine`); a flood question adds `A4 Engine (no model)`, `Heat`,
`Surface heat`, `Air quality`.

## Deploy

```powershell
Set-Location "C:\Users\anduri.roshan\Downloads\before-it-hits"
gcloud run deploy before-it-hits --source . --region asia-south1 --allow-unauthenticated --memory 2Gi --cpu 2 --min-instances 1 --max-instances 5 --timeout 120 --concurrency 20 --project before-it-hits --quiet
```

- **Do NOT add `--set-env-vars`.** It replaces all three keys. Omitting it keeps `MAPS_API_KEY`,
  `WEATHER_API_KEY`, `GEMINI_API_KEY`. (Confirm names only: `gcloud run services describe before-it-hits
  --region asia-south1 --project before-it-hits --format="value(spec.template.spec.containers[0].env[].name)"`.)
- It takes about 3 to 5 minutes. Run it as a background task and wait for it; do not poll.
- It ships whatever is in the working tree (`--source .`), committed or not. `.gcloudignore` excludes `.git`,
  `.venv`, `scratch`, `data/osm`, `.env*`. **`.gcloudignore` must exist**, or the staged data would be dropped.
  `.dockerignore` additionally excludes `engine/tests`, `engine/bench`, `engine/tools`, `plan`.
- Live: `https://before-it-hits-z5r5gsxkza-el.a.run.app` (alias
  `https://before-it-hits-1019287218541.asia-south1.run.app`).

### Smoke test after every deploy

```powershell
$u = "https://before-it-hits-z5r5gsxkza-el.a.run.app"
Invoke-RestMethod "$u/api/health"                                              # both regions listed
$c = New-Object System.Net.WebClient; $c.Encoding = [Text.Encoding]::UTF8       # UTF-8, or comparisons lie
$c.DownloadString("$u/api/surface-heat?region=kochi&bbox=76.30,9.96,76.34,10.00") | ConvertFrom-Json |
  ForEach-Object { "$($_.typical_low_c)-$($_.typical_high_c) C, hottest: $($_.hottest[0].nearest_road.name)" }
# expect: 33.4-39.1 C, hottest: Stadium Round
$c.DownloadString("$u/api/air-quality-current?region=kochi") | ConvertFrom-Json | ForEach-Object { "AQI $($_.aqi) $($_.category)" }
```

Then one agent question per city (above), and compare the deployed static files with local ones **decoded as
UTF-8** (a default-encoding download produced a false "files differ" on 14 Oct).

### Roll back

```powershell
gcloud run revisions list --service before-it-hits --region asia-south1 --project before-it-hits
gcloud run services update-traffic before-it-hits --region asia-south1 --project before-it-hits --to-revisions=<REVISION>=100
```

### Logs

```powershell
gcloud run services logs read before-it-hits --region asia-south1 --project before-it-hits --limit 80
```

## Traps already hit

| Trap | What happens | What to do |
|---|---|---|
| **PowerShell file round trip** | `Get-Content f -Raw` … `Set-Content f -Encoding utf8` re-reads BOM-less UTF-8 as ANSI and double-encodes every non-ASCII character | use the Edit/Write tools, or `python -I`. To repair: re-encode each non-ASCII run through `cp1252` then `latin-1`, taking the one that decodes as valid UTF-8. Watch for a **lost trailing byte** (`â€` + U+009D = a closing quote) |
| **Default-encoding downloads** | `WebClient.DownloadString` / `Invoke-WebRequest` `.Content` without UTF-8 mangles `°`, `µ`, `—` and makes identical files look different | set `Encoding = UTF8`, or `GetString($r.Content)` |
| **`Invoke-WebRequest` in non-interactive PowerShell** | fails with "NonInteractive mode" | add `-UseBasicParsing` |
| **`Start-Sleep` before another command** | blocked by the harness | background the work and wait for its notification, or use a Monitor until-loop |
| **Bash tool + npm-audit hook** | prints a blocking hook error when `jq` is missing; the command ran anyway | use PowerShell |
| **Maps key referrer restriction** | the map is grey on `127.0.0.1` | use `localhost:8080` |
| **Google Maps empties its element** | a toolbar built inside `#map` vanishes | make overlays siblings under `#mapwrap` |
| **Cloud Run `/healthz`** | the exact path is swallowed, 404 with Google's HTML, nothing in logs | health is `/api/health` |
| **`python:3.12-slim` without `libexpat1`** | `ImportError: libexpat.so.1` at build | the Dockerfile apt-installs it |
| **No `.gcloudignore`** | gcloud falls back to `.gitignore`; the image ships with no staged data | keep the file |
| **`--set-env-vars`** | wipes the three keys | omit it |
| **A key created now cannot call `gemini-2.5-*`** | 404 "no longer available to new users" | the 3.x models are used; names are env-overridable |
| **Earth Engine grid off by one** | `grid mismatch: got (974, 1621), terrain is (973, 1621)` | `fetch_lst.py` crops one extra row/column and refuses more |
| **DeltaDTM 30 m cap** | Singapore's interior reads as a flat plateau ringed by false depressions | capped cells are masked and counted per box |
| **GDELT** | 429/503, 10 to 20 s a call, one call per ~5 s per IP | cached, paced, warmed; the endpoint answers `pending` |
| **A prompt change "does nothing"** | module-level constants | restart the server |
| **Headless Chrome serves the old `app.js`** | the profile caches static files; a probe finds `typeof newFunction === 'undefined'` | `Network.enable` then `Network.setCacheDisabled {cacheDisabled: true}` before navigating |
| **`?region=singapore` does not switch city** | the page loads Kochi whatever the query string says | set the `#region` select and dispatch `change`, or call `selectRegion('singapore')` |
| **`Runtime.evaluate` "Object reference chain is too long"** | a predicate such as `until("!l.loading && l.instance")` returned a Maps object | coerce with `!!` |
| **A layer's `instance` is truthy but nothing paints** | the air-quality tiles were 403, `ERR_BLOCKED_BY_ORB` in `Network.loadingFailed` | listen for `Network.loadingFailed` and `responseReceived`, not just the layer state |
| **A new string with digits in the facts bundle** | the digits become quotable figures | write descriptive text without digits (05) |

## Skills and tools

- **ponytail** is installed at user level (`C:\Users\anduri.roshan\.claude\skills\ponytail*`): `ponytail`,
  `ponytail-review`, `ponytail-audit`, `ponytail-debt`, `ponytail-gain`, `ponytail-help`. Source
  `github.com/DietrichGebert/ponytail` at commit `2961b52fc1fe6a5a87c7a959a79512e5c2f3ffe5` (v5.2.0, MIT),
  copied byte-for-byte after reading every file; the commit is recorded in `ponytail\_SOURCE.txt`. It is a
  ruleset for coding sessions: the smallest complete change, reuse before writing, no unrequested abstractions,
  keep validation, errors, security and accessibility. Levels `lite`, `full` (default), `ultra`; say "stop
  ponytail" or "normal mode" to turn it off. **Only the plain skills are installed.** The plugin variant also
  installs hooks that run Node on every session start and prompt and nudge edits to `settings.json`; it was left
  out on purpose. To get it anyway: `/plugin marketplace add DietrichGebert/ponytail`, then
  `/plugin install ponytail@ponytail`. To remove the skills: delete the six `ponytail*` folders.
- **Memory.** `MEMORY.md` indexes the project memories (loaded automatically). Newest: `before-it-hits-surface-heat`,
  `before-it-hits-agent-scope`, `before-it-hits-handoff` (points at this pack).
