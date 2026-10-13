/* Draw a box, ask the engine what happens there, draw the answer.
   The map is a basemap and a drawing surface only: nothing is measured or traced from it.

   Two deliberate choices in here:
   - Tools are modal (move / box), like any drawing app. Exactly one is active, so a drag is never
     ambiguous between panning and selecting.
   - Drawing runs on native pointer events against the map container, with pixel-to-LatLng worked
     out from the map's own bounds, rather than on the Maps drawing library, Maps' own mouse events
     or an OverlayView projection. Each of those was tried and each failed quietly in its own way.
     Pointer capture means a drag ending outside the map still completes, and touch works too. */

const $ = (id) => document.getElementById(id);
const POOLING_FILL = { 5: '#0b3d91', 4: '#1d6fd0', 3: '#4a9fe0', 2: '#8fc5ec', 1: '#c8e2f5' };
const MIN_SIDE_DEG = 0.0015;
const OFFICIAL_FILL = ['#cfe8ff', '#86c0f0', '#3d8fd1', '#1b5fa8', '#0b3566'];  // about 150 m: below this there is nothing to analyse
// Yellow -> red like a traffic map, with no near-black at the top: a few hundred dark strokes
// overlapping read as a smear, and the darkest colour was what made the layer look heavy.
const ROAD_SEV = { 5: '#b3122b', 4: '#e03131', 3: '#f76707', 2: '#f59f00', 1: '#f2c94c' };
// Line weight by road class. A lane and a trunk road should not be drawn the same.
const ROAD_W = { motorway: 3.4, trunk: 3.4, primary: 3.0, secondary: 2.6, tertiary: 2.2 };

const state = {
  map: null, rect: null, layer: null, region: null,
  busy: false, mapReady: false, tool: 'pan', drag: null, lastBbox: null, officialLayer: null,
  regions: [], region: null, regionRect: null, forecast: null, rainMode: 'whatif',
  agents: false, newsFor: null, roadLayer: null, whatif: false, tab: 'agent',
  tl: { feats: [], dur: 6, step: 6, total: 100, cum: [], steps: 6, real: false },
};

/* Late answers from the loaders (forecast, gauges, preparations, road outlook) must not land on an
   area that has since been replaced or erased. Each loader takes a ticket; clearing the area voids
   them all, and a newer request voids the older one. */
const seq = { forecast: 0, now: 0, prep: 0, outlook: 0 };
const voidLoaders = () => { for (const k of Object.keys(seq)) seq[k] += 1; };

function setStatus(text, isError = false) {
  const el = $('status');
  el.hidden = !text;
  el.textContent = text || '';
  el.classList.toggle('error', Boolean(isError));
}

/* Only for a map that never came up. Once it is working, a later failure must not rip the tools
   away: the toolbar vanishing a moment after it appeared is exactly the sort of silent wrong
   turn this file has already produced twice. */
function mapFailed(why) {
  if (state.mapReady) {
    setStatus(`The map is working, but something else failed: ${why}`, true);
    return;
  }
  $('mapfallback').hidden = false;
  $('mapfallback-why').textContent = why;
  $('toolbar').hidden = true;
  $('legend').hidden = true;
}

/* Maps calls this itself when the key is rejected; without it the failure is a silent grey box. */
window.gm_authFailure = () => mapFailed(  // Google calls this itself when the key is rejected

  'Google rejected the API key for this page. The key is restricted to http://localhost:8080 — ' +
  'if you opened the page on another port or host, that is why.');

async function boot() {
  let cfg, list;
  try {
    [cfg, list] = await Promise.all([
      fetch('/api/config').then((r) => r.json()),
      fetch('/api/regions').then((r) => r.json()),
    ]);
  } catch (err) {
    setStatus(`Could not reach the engine: ${err.message}`, true);
    return;
  }
  state.regions = list.regions || [];
  if (!state.regions.length) {
    setStatus('The engine has no staged region.', true);
    return;
  }
  wireAsk(cfg.agents);           // before a region is chosen: the welcome message depends on it
  wireTabs();
  setTab('agent');
  buildRegionPicker();
  selectRegion(state.regions[0].name);
  wireToolbar();
  wireTimeline();

  if (!cfg.maps_api_key) {
    mapFailed('No Maps API key is configured. Set MAPS_API_KEY in .env.local and restart.');
    return;
  }
  const s = document.createElement('script');
  s.src = `https://maps.googleapis.com/maps/api/js?key=${cfg.maps_api_key}&v=weekly&language=en&callback=initMap`;
  s.async = true;
  s.onerror = () => mapFailed('The Maps script could not be fetched. Check the network connection.');
  document.head.appendChild(s);

  // Only meaningful if initMap never ran; mapFailed() ignores it once the map is up.
  setTimeout(() => { if (!state.mapReady) mapFailed('The Maps script did not finish loading.'); }, 12000);
}

function buildRegionPicker() {
  const sel = $('region');
  sel.replaceChildren(...state.regions.map((r) => {
    const o = document.createElement('option');
    o.value = r.name;
    o.textContent = r.country && r.country !== r.title ? `${r.title}, ${r.country}` : r.title;
    return o;
  }));
  sel.addEventListener('change', () => selectRegion(sel.value));
}

/* Switching city resets everything derived from the old one: results, box, official layer. */
function selectRegion(name, opts = {}) {
  const region = state.regions.find((r) => r.name === name);
  if (!region) return;
  state.region = region;
  $('region').value = name;

  const counts = region.vectors.counts || {};
  $('regionline').textContent =
    `${(counts.roads || 0).toLocaleString()} roads` +
    (counts.buildings ? ` · ${counts.buildings.toLocaleString()} buildings` : '');
  $('regionnote').textContent = region.note || '';
  $('credits').innerHTML =
    `Terrain: ${(region.terrain && region.terrain.source) || 'DeltaDTM v1.1'}. ` +
    `Roads and water: ${region.vectors.licence}. Basemap © Google — display only.`;

  buildReturnPeriods(region);
  $('rain-nogauge').hidden = Boolean(region.live_rainfall);
  loadReplay(region);
  resetNews(region);
  clearAll({ quiet: true });
  if (!opts.keepChat) chatReset();
  if (state.mapReady) frameRegion(region);
}

function frameRegion(region) {
  const [w, s, e, n] = region.bbox_wsen;
  const bounds = { south: s, west: w, north: n, east: e };
  if (state.regionRect) state.regionRect.setMap(null);
  state.regionRect = new google.maps.Rectangle({
    bounds, map: state.map, clickable: false,
    strokeColor: '#0e7490', strokeOpacity: 0.5, strokeWeight: 1.5, fillOpacity: 0,
  });
  state.map.fitBounds(bounds, 20);
}

function buildReturnPeriods(region) {
  const sel = $('rp');
  const periods = (region.official && region.official.return_periods) || [];
  sel.replaceChildren();
  const none = document.createElement('option');
  none.value = '';
  none.textContent = periods.length ? 'none — screening only' : 'none published for this city';
  sel.appendChild(none);
  for (const rp of periods) {
    const o = document.createElement('option');
    o.value = String(rp);
    o.textContent = `1-in-${rp}-year`;
    sel.appendChild(o);
  }
  sel.disabled = !periods.length;
  sel.value = periods.includes(50) ? '50' : '';
  $('rpout').textContent = sel.value ? `1-in-${sel.value}-year` : (periods.length ? 'off' : 'none published');
  $('legend-note').textContent = periods.length
    ? 'Ranks are relative to this box, not depths.'
    : `No authority publishes a depth layer for ${region.title}, so there is nothing to check against here.`;
  if (!sel.dataset.wired) {
    sel.dataset.wired = '1';
    sel.addEventListener('change', () => {
      $('rpout').textContent = sel.value ? `1-in-${sel.value}-year` : 'off';
      rerun();
    });
  }
}

/* ---- tools ---------------------------------------------------------------------------------- */

const TOOL_HINT = {
  pan: 'Drag to move the map. Pick the box tool to select an area.',
  box: 'Drag on the map to draw a box.',
};

function setTool(tool) {
  state.tool = tool;
  for (const b of document.querySelectorAll('#toolbar .tool[data-tool]')) {
    b.setAttribute('aria-pressed', String(b.dataset.tool === tool));
  }
  $('map').classList.toggle('drawing', tool === 'box');
  $('hint').textContent = TOOL_HINT[tool];
  $('hint').hidden = tool !== 'box';       // the hint only matters while drawing
  // While the box tool is active the map must not pan, or a drag would do both at once.
  if (state.mapReady) state.map.setOptions({ draggable: tool !== 'box', draggableCursor: tool === 'box' ? 'crosshair' : null });
}

function clearAll(opts = {}) {
  if (state.rect) { state.rect.setMap(null); state.rect = null; }
  if (state.layer) { state.layer.setMap(null); state.layer = null; }
  if (state.officialLayer) { state.officialLayer.setMap(null); state.officialLayer = null; }
  clearRoadLayer();
  $('timeline').hidden = true;
  $('prep').hidden = true;
  voidLoaders();
  clearedArea(Boolean(state.lastBbox) && !opts.quiet);
  state.lastBbox = null;
  state.areaLabel = null;
  $('results').hidden = true;
  setStatus('');
  setTool('pan');
}

function toggleCollapse(which) {
  const el = $(which);
  const open = el.classList.toggle('collapsed') === false;
  const btn = el.querySelector('.collapse');
  btn.setAttribute('aria-expanded', String(open));
  btn.title = open ? `Hide the ${which === 'toolbar' ? 'tools' : 'legend'}`
                   : `Show the ${which === 'toolbar' ? 'tools' : 'legend'}`;
}

function wireToolbar() {
  for (const el of [$('toolbar'), $('legend')]) {
    el.addEventListener('click', (ev) => {
      const c = ev.target.closest('.collapse');
      if (c) { toggleCollapse(c.dataset.collapse); ev.stopPropagation(); }
    });
  }
  $('toolbar').addEventListener('click', (ev) => {
    const btn = ev.target.closest('button');
    if (!btn || btn.classList.contains('collapse')) return;
    if (btn.dataset.action === 'erase') clearAll();
    else if (btn.dataset.action === 'labels') cycleLabels();
    else if (btn.dataset.tool) setTool(btn.dataset.tool);
  });
  document.addEventListener('keydown', (ev) => {
    if (ev.target.matches('input, textarea')) return;
    const k = ev.key.toLowerCase();
    if (k === 'v') setTool('pan');
    else if (k === 'b') setTool('box');
    else if (k === 'e') clearAll();
    else if (ev.key === 'Escape') { cancelDrag(); setTool('pan'); }
  });
}

/* ---- map ------------------------------------------------------------------------------------ */

/* ---- map labels ------------------------------------------------------------------------------
   Google's default basemap puts a business or attraction on nearly every building, which buries the
   pooling layer. Three densities, "Clean" by default: shops, restaurants, transit icons and road
   shields go, while the names that orient you stay -- areas, streets, roads, water, parks. "Minimal"
   also drops the names of small local streets and of neighbourhoods, keeping districts and main roads. */
const POI_OFF = [
  { featureType: 'poi', elementType: 'labels', stylers: [{ visibility: 'off' }] },
  { featureType: 'poi.business', stylers: [{ visibility: 'off' }] },
  { featureType: 'transit', elementType: 'labels', stylers: [{ visibility: 'off' }] },
  { featureType: 'road', elementType: 'labels.icon', stylers: [{ visibility: 'off' }] },
  { featureType: 'administrative.land_parcel', stylers: [{ visibility: 'off' }] },
];
const MAP_STYLES = {
  full: [],
  clean: POI_OFF,
  // Keeps district-level names and main roads; drops the dense neighbourhood and side-street names.
  minimal: [...POI_OFF,
    { featureType: 'road.local', elementType: 'labels', stylers: [{ visibility: 'off' }] },
    { featureType: 'administrative.neighborhood', elementType: 'labels', stylers: [{ visibility: 'off' }] }],
};
const LABEL_ORDER = ['full', 'clean', 'minimal'];
const LABEL_NAMES = { full: 'Full', clean: 'Clean', minimal: 'Minimal' };

try { state.labels = localStorage.getItem('bih.labels'); } catch { state.labels = null; }
if (!MAP_STYLES[state.labels]) state.labels = 'clean';

function applyLabels() {
  if (state.mapReady) state.map.setOptions({ styles: MAP_STYLES[state.labels] });
  $('labels-mode').textContent = LABEL_NAMES[state.labels];
  $('labels-btn').title = `Map labels: ${LABEL_NAMES[state.labels]}. Click to change how many are shown.`;
}

function cycleLabels() {
  state.labels = LABEL_ORDER[(LABEL_ORDER.indexOf(state.labels) + 1) % LABEL_ORDER.length];
  try { localStorage.setItem('bih.labels', state.labels); } catch { /* private mode: not fatal */ }
  applyLabels();
}

window.initMap = function initMap() {
  try {
    const [w, s, e, n] = state.region.bbox_wsen;
    const bounds = new google.maps.LatLngBounds({ lat: s, lng: w }, { lat: n, lng: e });
    state.map = new google.maps.Map($('map'), {
      center: bounds.getCenter(), zoom: 12,
      streetViewControl: false, fullscreenControl: false,
      styles: MAP_STYLES[state.labels],
      // Google's controls default to the top-left, where our tool palette lives. Map type goes to
      // the top-right as a compact dropdown; zoom sits below it, clear of the legend.
      mapTypeControl: true,
      mapTypeControlOptions: {
        position: google.maps.ControlPosition.TOP_RIGHT,
        style: google.maps.MapTypeControlStyle.DROPDOWN_MENU,
      },
      zoomControlOptions: { position: google.maps.ControlPosition.RIGHT_CENTER },
    });

    // Everything below is set up synchronously. An earlier version waited for the map's 'idle'
    // event, which meant one unlucky async error left the toolbar hidden with nothing on screen
    // to say why. UI chrome should never depend on a map event firing.
    enableBoxDrawing();
    applyLabels();
    // Stroke widths are in pixels, so the road layer is restyled as the zoom changes.
    state.map.addListener('zoom_changed', () => {
      if (state.roadLayer) styleRoads(cumAt(state.tl.step));
    });
    state.mapReady = true;
    frameRegion(state.region);   // outlines where the engine can answer
    $('toolbar').hidden = false;
    $('mapfallback').hidden = true;
    $('legend').hidden = false;
    setStatus('');
    // Selecting the default tool is cosmetic. If it throws, the map and the tools are still
    // usable, so it must not take the whole page down with it.
    try {
      setTool('box');   // arriving with the box tool ready is the point of the product
    } catch (err) {
      setStatus(`Could not select the box tool: ${err.message}`, true);
    }
  } catch (err) {
    mapFailed(`The map failed to start: ${err.message}`);
  }
};

/* Screen pixel -> LatLng, from the map's own bounds and the container size.
   Longitude is linear across the viewport; latitude is not, so it is interpolated in Web Mercator
   Y. Doing the arithmetic here rather than through an OverlayView projection means the drawing
   tool depends on nothing but getBounds(), which is always available once the map exists. */
const mercY = (lat) => Math.log(Math.tan(Math.PI / 4 + (lat * Math.PI) / 360));
const latOfMercY = (y) => ((2 * Math.atan(Math.exp(y)) - Math.PI / 2) * 180) / Math.PI;

function pointToLatLng(ev) {
  const bounds = state.map && state.map.getBounds();
  if (!bounds) return null;               // before the first render there is nothing to measure
  const r = $('map').getBoundingClientRect();
  if (!r.width || !r.height) return null;
  const ne = bounds.getNorthEast();
  const sw = bounds.getSouthWest();

  const fx = (ev.clientX - r.left) / r.width;
  const fy = (ev.clientY - r.top) / r.height;

  let west = sw.lng();
  let east = ne.lng();
  if (east < west) east += 360;           // viewport straddling the antimeridian
  const lng = ((west + fx * (east - west) + 540) % 360) - 180;

  const yTop = mercY(ne.lat());
  const yBottom = mercY(sw.lat());
  const lat = latOfMercY(yTop + fy * (yBottom - yTop));
  return new google.maps.LatLng(lat, lng);
}

function enableBoxDrawing() {
  const el = $('map');

  el.addEventListener('pointerdown', (ev) => {
    if (state.tool !== 'box' || ev.button !== 0) return;
    const ll = pointToLatLng(ev);
    if (!ll) {
      setStatus('The map is still settling — try that drag again in a moment.');
      return;
    }
    ev.preventDefault();
    ev.stopPropagation();
    // Capture means a release anywhere still reaches us, so a drag can never be left half-open.
    try { el.setPointerCapture(ev.pointerId); } catch { /* not fatal */ }
    state.drag = { id: ev.pointerId, start: ll };
    if (state.rect) { state.rect.setMap(null); state.rect = null; }
  });

  el.addEventListener('pointermove', (ev) => {
    if (!state.drag || ev.pointerId !== state.drag.id) return;
    const ll = pointToLatLng(ev);
    if (ll) showRect(bboxOf(state.drag.start, ll), false);
  });

  const end = (ev) => {
    if (!state.drag || ev.pointerId !== state.drag.id) return;
    const start = state.drag.start;
    state.drag = null;
    try { el.releasePointerCapture(ev.pointerId); } catch { /* already released */ }
    const ll = pointToLatLng(ev);
    if (!ll) return;
    const bbox = bboxOf(start, ll);
    if (bbox[2] - bbox[0] < MIN_SIDE_DEG || bbox[3] - bbox[1] < MIN_SIDE_DEG) {
      if (state.rect) { state.rect.setMap(null); state.rect = null; }
      setStatus('That box was too small — drag out a larger rectangle.');
      return;
    }
    setTool('pan');
    predictBox(bbox);
  };

  el.addEventListener('pointerup', end);
  el.addEventListener('pointercancel', cancelDrag);
}

function cancelDrag() {
  if (!state.drag) return;
  state.drag = null;
  if (state.rect) { state.rect.setMap(null); state.rect = null; }
}

const bboxOf = (a, b) => [
  Math.min(a.lng(), b.lng()), Math.min(a.lat(), b.lat()),
  Math.max(a.lng(), b.lng()), Math.max(a.lat(), b.lat()),
];

function showRect(bbox, fit = true) {
  if (!state.mapReady) return;
  const [w, s, e, n] = bbox;
  const bounds = { south: s, west: w, north: n, east: e };
  if (state.rect) {
    state.rect.setBounds(bounds);
  } else {
    state.rect = new google.maps.Rectangle({
      bounds, map: state.map, clickable: false,
      strokeColor: '#b45309', strokeWeight: 2, fillColor: '#b45309', fillOpacity: 0.06,
    });
  }
  if (fit) state.map.fitBounds(bounds, 60);
}

/* ---- analysis ------------------------------------------------------------------------------- */

/* Forecast-first: a drawn box or a preset predicts from the live forecast by default. The agent
   pulls the forecast, runs the engine on it, and drives the map + playback from the real hourly
   shape - no slider involved. The what-if slider below is an explicit override, for testing a
   heavier or different storm than the forecast. */
const DEFAULT_PREDICT_Q =
  'Using the live rainfall forecast, what could happen to this area and its roads over the coming day?';

function predictBox(bbox, label) {
  setTab('agent');
  if (state.agents) {
    setArea(bbox, label || 'Drawn box');
    sendQuestion(DEFAULT_PREDICT_Q, { auto: true });
  } else {
    state.lastBbox = bbox;
    state.areaLabel = label || 'Drawn box';
    state.whatif = false;
    analyse(bbox);         // no agent layer configured: fall back to a direct screening
  }
}

function rerun() {
  if (!state.lastBbox) return;
  if (state.whatif || !state.agents) { analyse(state.lastBbox); return; }
  chatNote('Official layer changed \u00b7 re-running the prediction');
  sendQuestion(DEFAULT_PREDICT_Q, { auto: true });
}

async function analyse(bbox) {
  if (state.busy) return;
  state.busy = true;
  state.lastBbox = bbox;
  loadForecast(bbox);   // fire-and-forget: a weather outage must not stop the screening
  loadRainNow(bbox);    // live gauge rainfall, where a city has an open feed (Singapore / NEA)
  setStatus('Reading the terrain and working out where water goes…');
  try {
    const res = await fetch('/api/analyse', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        bbox, rainfall_mm: Number($('rain').value),
        region: state.region.name,
        return_period: $('rp').value ? Number($('rp').value) : null,
      }),
    });
    const body = await res.json();
    if (!res.ok) throw new Error(body.detail || 'the engine could not analyse that box');
    render(body);
    setStatus('');
    notifyDetails();
    if (!state.agents) setTab('details');   // no chat to read the answer in
  } catch (err) {
    setStatus(err.message, true);
    $('results').hidden = true;
  } finally {
    state.busy = false;
  }
}

/* Live observed rainfall from the region's gauge network, where one is wired (Singapore / NEA).
   Fire-and-forget and region-gated: a gauge outage, or a city with no open feed, costs nothing. */
async function loadRainNow(bbox) {
  const my = ++seq.now;
  const el = $('rainnow');
  el.hidden = true;
  const live = state.region && state.region.live_rainfall;
  if (!live) return;
  try {
    const q = `region=${encodeURIComponent(state.region.name)}&bbox=${bbox.join(',')}`;
    const d = await fetch(`/api/rainfall-now?${q}`).then((r) => r.json());
    if (my !== seq.now || !d.available) return;
    renderRainNow(d, live);
  } catch { /* observed rain is context only; the screening is unaffected */ }
}

function renderRainNow(d, live) {
  const el = $('rainnow');
  el.hidden = false;
  const w = d.wettest_nearby;
  const t = d.at ? new Date(d.at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) : '';
  const wet = w && w.mm_5min > 0;
  const head = wet
    ? `<b>${w.mm_5min} mm</b>/5 min at ${esc(w.name)}`
    : `all quiet \u2014 0 mm across ${d.gauges_reporting} nearby gauges`;
  const near = (d.nearest || []).slice(0, 3)
    .map((g) => `${esc(g.name)} ${g.mm_5min} mm (${g.km} km)`).join(' \u00b7 ');
  el.innerHTML =
    `<div class="rn-head">Live rain now <span class="tag-live">${esc(live.label)}${t ? ' \u00b7 ' + t : ''}</span></div>`
    + `<div class="rn-now">${head}</div>`
    + `<div class="muted tiny">nearest: ${near}</div>`
    + `<div class="muted tiny">Observed at the gauges \u2014 not a forecast and not our model.</div>`;
}

/* The forecast is live and can fail. It is fetched separately from the analysis so that a
   weather outage costs you the rainfall panel and nothing else. */
async function loadForecast(bbox) {
  const my = ++seq.forecast;
  $('rain-summary').innerHTML = '<p class="muted small">checking the forecast\u2026</p>';
  $('rainchart').hidden = true;
  try {
    const r = await fetch('/api/forecast', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ bbox, hours: 48 }),
    });
    const body = await r.json();
    if (my !== seq.forecast) return;
    if (!r.ok) throw new Error(body.detail || 'no forecast available');
    state.forecast = body;
    renderForecast();
  } catch (err) {
    if (my !== seq.forecast) return;
    state.forecast = null;
    $('rain-summary').innerHTML = `<p class="muted small">No live forecast right now (${esc(err.message)}). `
      + 'Use \u201cTry a heavier storm\u201d below to set the rainfall by hand.</p>';
  }
}

function renderForecast() {
  const f = state.forecast;
  if (!f) return;
  const day = f.peaks['24'] || f.peaks[Object.keys(f.peaks).pop()] || { mm: f.total_mm };
  $('rain-summary').innerHTML =
    `<div class="rs-class">${rainClass(day.mm)}</div>`
    + `<div class="rs-line"><b>${f.total_mm} mm</b> expected over the next ${f.hours_ahead} hours`
    + ` \u00b7 the wettest 24 hours: <b>${day.mm} mm</b></div>`
    + '<div class="rs-effect" id="rain-effect"></div>'
    + '<div class="rs-fine">The words follow the India Meteorological Department\u2019s daily rainfall '
    + 'classes. This is a model forecast, not a measurement.</div>';
  const windows = Object.entries(f.peaks)
    .map(([h, p]) => `<span class="fwin">wettest ${h} h: <b>${p.mm}</b> mm</span>`).join('');
  $('forecast').innerHTML = `
    <div class="windows">${windows}</div>
    <div class="why">A forecast for one point at the centre of the area, not a rainfall map: a heavy burst
      can be smaller than the area, and the corners can differ. It comes from the Google Weather API.
      No flood return period is worked out from it.</div>`;
  renderRainChart(f);
  applyRainEffect();
  seedWhatIf();
}

/* An override starts from the real prediction, so moving it is "what if it were different". */
function seedWhatIf() {
  const f = state.forecast;
  const seed = f && (f.peaks['3'] || f.peaks[Object.keys(f.peaks)[0]] || {}).mm;
  if (seed == null || state.whatif) return;
  $('rain').value = String(Math.max(0, Math.round(seed)));
  // The slider moves in 5 mm steps, so say the forecast's own figure rather than the snapped one.
  $('rainout').textContent = `${+Number(seed).toFixed(1)} mm \u00b7 forecast`;
}

/* ---- roads over time: a rain-driven playback (F8) -------------------------------------------
   Honest framing lives in the panel: this is modelled road difficulty from rain pooling, not a
   traffic measurement. Each road carries the rainfall at which a spot it crosses fills; the slider
   is a rainfall-accumulation clock (assumes no drainage -> worst case), so as rain builds the
   roads light up in the order they flood. Severity colours the worst spot each road sits in. */
function clearRoadLayer() {
  if (state.roadLayer) { state.roadLayer.setMap(null); state.roadLayer = null; }
  $('tl-float').hidden = true;
}

/* The cumulative-rainfall curve the playback steps through. With the live forecast the agent hands us
   the real per-hour amounts of the exact window it ran the engine on, so the last step is the total
   the engine used. A what-if has no real shape, so its total is spread evenly across the chosen storm
   length: an assumption, and the card says so. */
function buildCumulative(total, dur, profile) {
  if (profile && profile.length) {
    const cum = [0];
    for (let i = 0; i < profile.length; i += 1) cum.push(cum[i] + profile[i]);
    return cum;
  }
  const cum = [];
  for (let k = 0; k <= dur; k += 1) cum.push(dur ? total * (k / dur) : total);
  return cum;
}

function setDurationSelect(dur) {
  const opts = [...$('tl-duration').options].map((o) => Number(o.value));
  const near = opts.reduce((a, b) => (Math.abs(b - dur) < Math.abs(a - dur) ? b : a));
  $('tl-duration').value = String(near);
}

/* Real clock times for the forecast window, in the city's own time zone: "Wed 3 PM", not "hour 24". */
function clockAt(step, o = { weekday: 'short', hour: 'numeric' }) {
  if (!state.tl.start) return `+${step} h`;
  const t = new Date(new Date(state.tl.start).getTime() + step * 3600e3);
  const tz = state.region && state.region.timezone;
  try { return new Intl.DateTimeFormat([], { timeZone: tz || undefined, ...o }).format(t); }
  catch { return new Intl.DateTimeFormat([], o).format(t); }
}

/* The hourly rain the scrubber is stepping through, drawn above it, so it is plain to see that this is
   the forecast's own data: how much falls, and in which hours. Scaled to at least 1 mm an hour so a
   drizzle does not look like a storm. */
function renderPlaybackStrip(profile) {
  const bars = $('tl-bars');
  const axis = $('tl-axis');
  if (!state.tl.real) { bars.hidden = true; axis.hidden = true; return; }
  const max = Math.max(1, ...profile);
  bars.innerHTML = profile.map((mm, i) =>
    `<span class="tb" style="height:${mm > 0 ? Math.max(10, (mm / max) * 100) : 4}%" `
    + `title="${clockAt(i)} · ${fmtMm(mm)} mm"></span>`).join('');
  axis.children[0].textContent = clockAt(0);
  axis.children[1].textContent = clockAt(profile.length);
  bars.hidden = false;
  axis.hidden = false;
}

/* `driver` is set when the answer came from the agent: the rainfall, the window and, in forecast
   mode, the real hourly shape it settled on. The playback follows the agent, not the slider. */
function renderTimeline(res, driver) {
  const feats = ((res.roads_timeline || {}).features) || [];
  const sec = $('timeline');
  clearRoadLayer();
  if (!feats.length) { sec.hidden = true; return; }
  sec.hidden = false;
  state.tl.feats = feats;

  const profile = driver && driver.profile;
  state.tl.real = Boolean(profile && profile.length);
  state.tl.start = state.tl.real ? driver.profileStart : null;
  if (driver) {
    state.tl.total = driver.total;
    state.tl.dur = Math.max(1, Math.round(driver.dur || 6));
  } else {
    state.tl.total = Number($('rain').value);
    state.tl.dur = Number($('tl-duration').value);
  }
  state.tl.cum = buildCumulative(state.tl.total, state.tl.dur, profile);
  state.tl.steps = state.tl.cum.length - 1;
  $('tl-step').max = state.tl.steps;
  if (!state.tl.real) setDurationSelect(state.tl.dur);
  $('tl-duration').parentElement.style.display = state.tl.real ? 'none' : '';
  renderPlaybackStrip(profile || []);

  // Where the numbers on the scrubber come from, in plain sight: a forecast, or a hypothesis.
  const meta = driver && driver.forecastMeta;
  const fetched = meta && meta.fetched_at
    ? new Date(meta.fetched_at).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit',
        timeZone: (state.region && state.region.timezone) || undefined }) : null;
  $('tl-src').innerHTML = state.tl.real
    ? `<span class="live">Live forecast</span> · Google Weather API${fetched ? ` · fetched ${fetched}` : ''}`
    : `<b>What-if, not a forecast</b> · ${fmtMm(state.tl.total)} mm assumed to fall evenly over ${state.tl.dur} h`;

  const floods = Math.min(...feats.map((f) => f.properties.rain_to_flood_mm));
  const dry = state.tl.real && state.tl.total < floods;
  $('timeline-sub').textContent = dry
    ? 'In this forecast the rain stays below what any road here needs to go under, so nothing is '
      + 'marked. Use “Try a heavier storm” to see what a bigger one would do.'
    : `${feats.length} road stretches run through the low spots. Drag the bar on the map to watch them `
      + 'go under as the rain builds, the most vulnerable first.';
  $('tl-note').innerHTML =
    'Modelled road difficulty from where rain collects &mdash; not a traffic measurement, and not a '
    + 'forecast of standing water hours later. It adds up the rain assuming <b>none of it drains '
    + 'away</b> (the worst case, since storm-drain data is not public), so a long storm saturates; '
    + 'streets with working drains clear sooner.'
    + (state.tl.real ? ' The hourly rain is the live forecast’s own.' : '');

  if (state.mapReady) {
    const layer = new google.maps.Data({ map: state.map });
    layer.addGeoJson(res.roads_timeline);
    state.roadLayer = layer;
    $('tl-float').hidden = false;
  }
  stepTo(state.tl.steps);               // open on the full window; drag back to watch it build
  loadRoadOutlook(res);                 // the agent's qualitative, region-specific read (if any)
}

/* Thin by default and thinner when zoomed out, where a stroke width in pixels covers far more
   street. Severity is carried by colour and, only slightly, by weight. */
function roadWeight(highway, sev) {
  const base = ROAD_W[highway] || 1.5;
  const z = state.map ? state.map.getZoom() : 13;
  const zoomK = Math.min(1.7, Math.max(0.55, 0.55 + (z - 12) * 0.2));    // zoom 12 -> .55, 17 -> 1.55
  return Math.round(base * zoomK * (0.9 + sev * 0.04) * 10) / 10;
}

function styleRoads(cum) {
  if (!state.roadLayer) return;
  state.roadLayer.setStyle((f) => {
    const on = f.getProperty('rain_to_flood_mm') <= cum;
    const sev = f.getProperty('severity');
    return {
      strokeColor: ROAD_SEV[sev], strokeOpacity: on ? 0.9 : 0.0,
      strokeWeight: on ? roadWeight(f.getProperty('highway'), sev) : 0,
      zIndex: 50 + sev, clickable: false,
    };
  });
}

function cumAt(step) {
  const c = state.tl.cum || [];
  if (!c.length) return state.tl.total;
  return c[Math.max(0, Math.min(step, c.length - 1))];
}

function stepTo(step) {
  state.tl.step = step;
  $('tl-step').value = step;
  const cum = cumAt(step);
  styleRoads(cum);                       // instant: one setStyle, no animation
  const on = state.tl.feats.filter((f) => f.properties.rain_to_flood_mm <= cum);
  const km = on.reduce((a, f) => a + f.properties.length_m, 0) / 1000;
  [...$('tl-bars').children].forEach((b, i) => b.classList.toggle('past', i < step));
  const when = state.tl.real ? `by ${clockAt(step)}` : `${step} h into the storm`;
  const roads = on.length ? `${on.length} stretches · ${km.toFixed(1)} km` : 'no roads under yet';
  $('tl-read').innerHTML = `<b>~${fmtMm(cum)} mm</b> ${when} · ${roads}`;
  const ARTERIAL = ['motorway', 'trunk', 'primary', 'secondary', 'tertiary'];
  const seen = new Set();               // a long road can have several stretches; list it once
  const worst = on.filter((f) => f.properties.name && ARTERIAL.includes(f.properties.highway))
    .sort((a, b) => b.properties.severity - a.properties.severity
      || a.properties.rain_to_flood_mm - b.properties.rain_to_flood_mm)
    .filter((f) => !seen.has(f.properties.name) && seen.add(f.properties.name))
    .slice(0, 6);
  $('tl-roads').replaceChildren(...worst.map((f) => {
    const p = f.properties;
    const li = document.createElement('li');
    li.innerHTML = `<span class="rsev s${p.severity}"></span><b>${esc(p.name)}</b>`
      + `<span class="meta">goes under from ~${p.rain_to_flood_mm} mm of rain</span>`;
    return li;
  }));
}

function wireTimeline() {
  $('tl-step').oninput = (e) => stepTo(Number(e.target.value));
  $('tl-duration').onchange = () => {
    state.tl.dur = Number($('tl-duration').value);
    state.tl.cum = buildCumulative(state.tl.total, state.tl.dur, null);
    state.tl.steps = state.tl.cum.length - 1;
    $('tl-step').max = state.tl.steps;
    stepTo(state.tl.steps);
  };
}

/* The agent's read is optional and must never block the playback: the deterministic timeline above
   stands on its own. This adds region-specific, qualitative context, grounded in recent news. */
/* ---- preparations within budget (F9) -------------------------------------------------------
   The costed list is deterministic (the engine plus the city's own documented budget lines); the
   planner note on top is the agent's, and even that may use no number the list does not already
   contain. Preparation and planning only -- never dispatch or evacuation. */
function inr(n) {
  if (n == null) return '';
  return '\u20b9' + Math.round(n).toLocaleString('en-IN');
}

async function loadPreparations(rainfallMm) {
  const my = ++seq.prep;
  const sec = $('prep');
  sec.hidden = true;
  if (!state.lastBbox || !state.region || state.region.preparations === false) return;
  try {
    const d = await fetch('/api/preparations', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ bbox: state.lastBbox, region: state.region.name, rainfall_mm: rainfallMm }),
    }).then((r) => { if (!r.ok) throw new Error('no prep'); return r.json(); });
    if (my !== seq.prep || !d.available || !d.items || !d.items.length) return;
    renderPreparations(d);
  } catch { /* the screening and the list stand without this */ }
}

function renderPreparations(d) {
  $('prep').hidden = false;
  const people = d.covers && d.covers.people_in_worst_spots;
  $('prep-sub').innerHTML =
    'A prioritised, costed list drawn from the city\u2019s own preparedness budget line'
    + (people ? `, prioritising the worst spots \u2014 where a modelled <b>~${people.toLocaleString('en-IN')}</b> people are exposed.` : '.');

  const used = d.budget_used_inr, total = d.budget_total_inr;
  const pct = total ? Math.min(100, Math.max(2, Math.round((used / total) * 100))) : 0;
  $('prep-budget').innerHTML =
    `<div class="pb-row"><span><b>${inr(used)}</b> of ${inr(total)}</span>`
    + `<span class="muted">${d.items.length} measure${d.items.length > 1 ? 's' : ''}`
    + (d.spots_not_listed ? ` \u00b7 ${d.spots_not_listed} more below the cut` : '')
    + `</span></div>`
    + `<div class="pb-bar"><span style="width:${pct}%"></span></div>`
    + (d.budget_line ? `<p class="muted tiny">${esc(d.budget_line)}</p>` : '');

  $('prep-items').replaceChildren(...d.items.map((it) => {
    const li = document.createElement('li');
    li.innerHTML =
      `<span class="rsev s${it.pooling_rank}"></span>`
      + `<div class="pi-body"><b>${esc(it.measure)}</b>`
      + `<div class="meta">${esc(it.where)} \u00b7 ${esc(it.when)}</div></div>`
      + `<span class="pi-cost">${inr(it.cost_inr)}</span>`;
    return li;
  }));

  const note = $('prep-note');
  if (d.planner_note) {
    note.hidden = false;
    note.innerHTML = `<div class="ro-head">The planner\u2019s read <span class="tag-modelled">agent</span></div>`
      + `<p>${esc(d.planner_note)}</p>`
      + `<p class="muted tiny">Phrasing only. Every figure is from the list above; this adds no measurement.</p>`;
  } else {
    note.hidden = true;
  }

  const inf = $('prep-infeasible');
  if (d.infeasible && d.infeasible.length) {
    inf.hidden = false;
    $('prep-infeasible-list').replaceChildren(...d.infeasible.map((x) => {
      const li = document.createElement('li');
      li.innerHTML = `<b>${esc(x.measure)}</b><div class="meta">${esc(x.reason)}</div>`;
      return li;
    }));
  } else {
    inf.hidden = true;
  }

  $('prep-source').innerHTML =
    (d.source ? `Budget basis: ${esc(d.source)}. ` : '')
    + 'Preparation and planning only \u2014 never dispatch, evacuation or a live response plan.';
}

async function loadRoadOutlook(res) {
  const my = ++seq.outlook;
  const el = $('road-outlook');
  el.hidden = true;
  if (!state.agents || !state.lastBbox) return;
  try {
    const d = await fetch('/api/road-outlook', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        bbox: state.lastBbox, region: state.region.name,
        rainfall_mm: state.tl.total, duration_h: state.tl.dur,
      }),
    }).then((r) => { if (!r.ok) throw new Error('no outlook'); return r.json(); });
    if (my !== seq.outlook || !d.outlook) return;
    el.hidden = false;
    el.innerHTML = `<div class="ro-head">What this might mean here <span class="tag-modelled">agent &middot; grounded</span></div>`
      + `<p>${esc(d.outlook)}</p>`
      + (d.sources && d.sources.length
        ? `<p class="muted tiny">Read from: ${d.sources.map((s) => esc(s)).join(', ')}</p>` : '')
      + `<p class="muted tiny">Qualitative context from recent reporting. No number here is a measurement; the figures and roads are the engine's, above.</p>`;
  } catch { /* the playback is unaffected */ }
}

/* ---- who is nearby (F7) --------------------------------------------------------------------
   One modelled line, with the method folded away. By area, never per home. */
function renderPeople(ex) {
  const el = $('people');
  if (!ex || !ex.available || !ex.people_in_worst_spots) { el.hidden = true; return; }
  el.hidden = false;
  el.innerHTML = `
    <div class="card-title">Who lives in the worst spots that fill</div>
    <p class="peoplenum"><b>~${ex.people_in_worst_spots.toLocaleString()}</b> people<span class="muted">, out of
      about ${ex.people_in_box.toLocaleString()} living in this box</span></p>
    <details class="fine"><summary>How is this estimated?</summary>
      <ul>${ex.caveats.map((c) => `<li>${esc(c)}</li>`).join('')}</ul></details>`;
}

/* ---- in the news now (A13) -----------------------------------------------------------------
   Its own tab. Context only: it changes no number anywhere. The news index is slow (tens of seconds
   on a cold hit), so the server answers from a cache and, when it has nothing yet, says "pending";
   this asks again every few seconds and says plainly what it is doing, rather than sit on a spinner. */
function resetNews(region) {
  state.newsFor = null;
  $('news-body').innerHTML = '<p class="muted small">Opens when you visit this tab.</p>';
  if (state.tab === 'news') loadNews();
}

async function loadNews(attempt = 0) {
  const region = state.region;
  if (!region) return;
  if (attempt === 0) {
    if (state.newsFor === region.name) return;
    state.newsFor = region.name;
    $('news-body').innerHTML = '<p class="muted small">checking the news\u2026</p>';
  }
  const stillHere = () => state.region === region && state.newsFor === region.name;
  try {
    const res = await fetch(`/api/news?region=${encodeURIComponent(region.name)}`);
    if (!stillHere()) return;                       // they switched city while this was loading
    const d = await res.json();
    if (d.pending) {
      $('news-body').innerHTML = '<p class="muted small">The news index is slow today \u2014 still '
        + 'looking. This can take up to a minute the first time.</p>';
      if (attempt < 25) setTimeout(() => { if (stillHere()) loadNews(attempt + 1); }, 3000);
      else throw new Error('timed out');
      return;
    }
    if (!res.ok) throw new Error(d.detail || 'news unavailable');
    renderNews(d);
  } catch {
    if (!stillHere()) return;
    state.newsFor = null;                           // let a later visit retry
    $('news-body').innerHTML = '<p class="muted small">No live news right now \u2014 the news index is '
      + 'busy or not answering. Visit this tab again in a minute.</p>';
  }
}

function renderNews(d) {
  const arts = (d.articles || []).slice(0, 8).map((a) =>
    `<li><a href="${esc(a.url)}" target="_blank" rel="noopener">${esc(a.title)}</a>
      <span class="src">${esc(a.domain)}${a.seen ? ' \u00b7 ' + esc(a.seen.slice(0, 10)) : ''}</span></li>`).join('');
  const age = d.age_s != null && d.age_s >= 90 ? ` Updated ${Math.round(d.age_s / 60)} min ago.` : '';
  $('news-body').innerHTML = `
    ${d.digest ? `<p class="digest">${esc(d.digest)}</p>` : ''}
    <ul class="newslist">${arts || '<li class="muted small">No recent reports found.</li>'}</ul>
    <p class="muted tiny">${esc(d.note)}${age}</p>`;
}

/* ---- checked against a real storm ----------------------------------------------------------
   A negative result shown as plainly as a positive one. The point of the replay is not a headline
   number; it is that a flag covers most of the box at an extreme rainfall, so a high hit-rate
   means nothing. Leading with that is the honest thing, and it is the whole product in miniature. */
async function loadReplay(region) {
  const el = $('replay');
  const ids = region.replays || [];
  if (!ids.length) { el.hidden = true; return; }
  try {
    const data = await fetch(`/api/replay?id=${encodeURIComponent(ids[0])}`).then((r) => r.json());
    renderReplay(data);
    el.hidden = false;
  } catch {
    el.hidden = true;
  }
}

function renderReplay(data) {
  const p = data.pooled;
  const ev = data.event;
  const rain = data.rainfall;
  const verdictClass = p.discriminating ? 'ok' : 'warn';
  const boxes = data.boxes.map((b) => {
    const rows = b.per_locality.map((l) => {
      const nf = l.nearest_filling_spot;
      const near = nf ? `${nf.distance_m} m away, rank ${nf.pooling_rank}` : 'no filling spot near';
      const cls = !l.flooded ? 'held' : (l.detected ? 'hit' : 'miss');
      const tag = !l.flooded ? 'held out' : (l.detected ? 'flagged' : 'not flagged');
      return `<li class="${cls}"><b>${esc(l.name)}</b> <span class="tag">${tag}</span>
        <div class="meta">${esc(l.report || '')} <i>(nearest pooling spot ${near})</i></div></li>`;
    }).join('');
    return `<div class="rbox"><div class="rhd">${esc(b.box)} box · a flag covers
      <b>${Math.round(b.baseline * 100)}%</b> of it</div><ul class="rlist">${rows}</ul></div>`;
  }).join('');

  $('replay-body').innerHTML = `
    <p class="muted small">${esc(ev.title)} — ${esc(ev.what_happened)}</p>
    <div class="rverdict ${verdictClass}">
      <div class="big">${p.detected}/${p.positives} reported localities flagged,
        but a flag covers <b>${Math.round(p.baseline * 100)}%</b> of the area</div>
      <div class="small">Enrichment ${p.enrichment}× over chance —
        ${p.discriminating ? 'the places that flooded are flagged more than chance.'
          : 'at ' + rain.mm + ' mm in ' + rain.duration_h + ' h the model floods almost everything, '
            + 'so being near a flag separates nothing. This is the regime the relative ranking is '
            + 'not built for.'}</div>
    </div>
    ${boxes}
    <details class="rmore">
      <summary>How this was scored, and what it does and doesn't show</summary>
      <p class="small">${esc(data.method)}</p>
      <ul class="small">${data.honest.map((h) => `<li>${esc(h)}</li>`).join('')}</ul>
      <p class="small muted">Rainfall basis: ${esc(rain.basis)}</p>
    </details>`;
}

/* ---- asking ---------------------------------------------------------------------------------

   The answer takes about ten seconds and most of that is two model calls, so the steps arrive as
   they finish rather than all at the end. That is not a loading animation: the Verifier sending a
   draft back, or a claim being removed, is the part of this product worth watching. */

const esc = (s) => String(s ?? '').replace(/[&<>"]/g,
  (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

/* ---- panel: tabs ---------------------------------------------------------------------------- */

function setTab(name) {
  state.tab = name;
  for (const t of document.querySelectorAll('.tabs [role="tab"]')) {
    const on = t.dataset.tab === name;
    t.setAttribute('aria-selected', String(on));
    t.tabIndex = on ? 0 : -1;
    $(`pane-${t.dataset.tab}`).hidden = !on;
  }
  if (name === 'details') $('tab-details').querySelector('.dot').hidden = true;
  if (name === 'agent') toBottom();
  if (name === 'news') loadNews();                 // lazy: the news index is only asked when wanted
}

function wireTabs() {
  const tabs = [...document.querySelectorAll('.tabs [role="tab"]')];
  tabs.forEach((t, i) => {
    t.addEventListener('click', () => setTab(t.dataset.tab));
    t.addEventListener('keydown', (e) => {
      if (e.key !== 'ArrowRight' && e.key !== 'ArrowLeft') return;
      const next = tabs[(i + (e.key === 'ArrowRight' ? 1 : tabs.length - 1)) % tabs.length];
      next.focus();
      setTab(next.dataset.tab);
      e.preventDefault();
    });
  });
}

/* New results land in Details while you are reading the chat. A dot says so without stealing focus. */
function notifyDetails() {
  if (state.tab !== 'details') $('tab-details').querySelector('.dot').hidden = false;
}

/* ---- Rain tab: what is forecast, in plain words ----------------------------------------------
   One headline, one picture, everything else folded away. The words for how much rain it is follow
   the India Meteorological Department's daily classes, and are labelled as such. */

function rainClass(mm24) {
  if (mm24 < 2.5) return 'Little or no rain';
  if (mm24 < 15.6) return 'Light rain';
  if (mm24 < 64.5) return 'Moderate rain';
  if (mm24 < 115.6) return 'Heavy rain';
  if (mm24 < 204.5) return 'Very heavy rain';
  return 'Extremely heavy rain';
}

function renderRainChart(f) {
  const el = $('rainchart');
  const hrs = f.hours || [];
  if (!hrs.length) { el.hidden = true; return; }
  const tz = state.region && state.region.timezone;
  const fmt = (t, o) => {
    try { return new Intl.DateTimeFormat([], { timeZone: tz || undefined, ...o }).format(new Date(t)); }
    catch { return new Intl.DateTimeFormat([], o).format(new Date(t)); }
  };
  const max = Math.max(2, ...hrs.map((h) => h.mm));
  const peak = hrs.reduce((a, b) => (b.mm > a.mm ? b : a));
  const bars = hrs.map((h) => {
    const pct = h.mm > 0 ? Math.max(4, (h.mm / max) * 100) : 1;
    return `<span class="bar" style="height:${pct}%" title="${fmt(h.t, { weekday: 'short', hour: 'numeric', minute: '2-digit' })} · ${h.mm} mm"></span>`;
  }).join('');
  const day = { weekday: 'short', hour: 'numeric' };
  el.innerHTML =
    `<div class="rc-head"><b>When it rains, hour by hour</b>`
    + `<span class="muted tiny">scale 0–${+max.toFixed(1)} mm per hour</span></div>`
    + `<div class="bars">${bars}</div>`
    + `<div class="rc-axis"><span>${fmt(hrs[0].t, { hour: 'numeric' })}</span>`
    + `<span>${fmt(hrs[Math.floor(hrs.length / 2)].t, day)}</span>`
    + `<span>${fmt(hrs[hrs.length - 1].t, day)}</span></div>`
    + `<div class="rc-foot muted tiny">${peak.mm > 0
      ? `Wettest hour: ${peak.mm} mm, ${fmt(peak.t, day)}.` : 'No measurable rain in this window.'}`
    + `${tz ? ` Times in ${esc(tz.split('/').pop().replace('_', ' '))}.` : ''}</div>`;
  el.hidden = false;
}

function resetRain() {
  state.forecast = null;
  state.rainEffect = null;
  $('rain-summary').innerHTML = '<p class="muted small">Pick an area and its rain forecast appears here.</p>';
  $('forecast').innerHTML = '';
  $('rainchart').hidden = true;
  $('rainnow').hidden = true;
}

/* How many low spots fill at the rainfall being looked at. Counted by spot, not by map polygon: one
   spot can be drawn as several polygons, and the totals everywhere else count spots. */
const spotsThatFill = (res) => new Set(res.geojson.features
  .filter((f) => f.properties.fills_at_rainfall).map((f) => f.properties.id)).size;

/* What that rainfall does to this area, from the engine's own result, shown under the headline. */
function updateRainEffect(res, driver) {
  const fills = spotsThatFill(res);
  const total = res.screening.depressions.length;
  const real = Boolean(driver && driver.profile);
  const mm = driver ? driver.total : Number($('rain').value);
  state.rainEffect = `${real ? 'At that rainfall' : `In the what-if (${fmtMm(mm)} mm)`}, `
    + `<b>${fills}</b> of the <b>${total}</b> low spots in this area fill.`;
  applyRainEffect();
}

function applyRainEffect() {
  const el = document.getElementById('rain-effect');
  if (el && state.rainEffect) el.innerHTML = state.rainEffect;
}

/* An erased or replaced area makes any in-flight answer stale, so stop it. */
function clearedArea(hadArea) {
  if (chat.abort) chat.abort.abort();
  resetRain();
  if (hadArea) chatNote('Area cleared — draw a new box, or name a place, to continue.');
}

/* ---- the agent: a chat -----------------------------------------------------------------------
   A conversation, not a form. A question becomes a user bubble and an agent bubble. The agent
   bubble shows the agents working (the trace), folds that to one line when they finish, and shows
   the answer. Nothing about how answers are made changed: the engine computes, the models only
   phrase, and a Verifier checks every figure. The trace stays one click away because it is the
   proof of that.

   The chat remembers: each answer is noted in `chat.history`, which is sent with the next message
   so "and the roads?" or "same storm for Vyttila" can be understood. The history only helps the
   agent work out what a message means; every figure still comes from the engine. A message can name
   a place instead of using the drawn box, or ask what something means, so neither needs a box. */

const SUGGESTIONS = [
  { q: 'Which spots fill first here?' },
  { q: 'Which roads are hit first?' },
  { q: 'Roughly how many people are exposed?', needs: 'people' },
  { q: 'How does this compare with the official map?', needs: 'official' },
];

const ASK_ME = ['What does pooling rank mean?', 'How far can I trust this?'];

const ICON = {
  drop: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 2c-5.33 4.55-8 8.48-8 11.8 0 4.98 3.8 8.2 8 8.2s8-3.22 8-8.2C20 10.48 17.33 6.55 12 2z"/></svg>',
  send: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M2.01 21 23 12 2.01 3 2 10l15 2-15 2z"/></svg>',
  stop: '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="6.5" y="6.5" width="11" height="11" rx="2"/></svg>',
  copy: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M16 1H4c-1.1 0-2 .9-2 2v14h2V3h12V1zm3 4H8c-1.1 0-2 .9-2 2v14c0 1.1.9 2 2 2h11c1.1 0 2-.9 2-2V7c0-1.1-.9-2-2-2zm0 16H8V7h11v14z"/></svg>',
  redo: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M17.65 6.35A7.96 7.96 0 0 0 12 4a8 8 0 1 0 7.73 10h-2.08A6 6 0 1 1 12 6c1.66 0 3.14.69 4.22 1.78L13 11h7V4l-2.35 2.35z"/></svg>',
};

// What each step hands over to, so the working line says what is happening now.
const NEXT_STEP = {
  'A2 Scenario': 'Pulling the live forecast…',
  'A3 Forecast': 'Running the terrain engine…',
  'A4 Engine (no model)': 'Writing the briefing…',
  'A6 Narrator': 'Checking every figure…',
  'A7 Verifier': 'Finishing…',
};

const chat = { busy: false, abort: null, run: 0, history: [] };
const MAX_HISTORY = 6;

const mk = (tag, cls, html) => {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (html != null) n.innerHTML = html;
  return n;
};
const fmtMm = (n) => String(+Number(n).toFixed(1));
const boxKm2 = ([w, s, e, n]) =>
  Math.abs(e - w) * Math.abs(n - s) * 111.32 ** 2 * Math.cos((((s + n) / 2) * Math.PI) / 180);

const nearBottom = () => {
  const c = $('chat');
  return c.scrollHeight - c.scrollTop - c.clientHeight < 140;
};
const toBottom = () => { const c = $('chat'); c.scrollTop = c.scrollHeight; };

/* Append, and keep the newest message in view -- unless the reader has scrolled up to re-read. */
function push(node) {
  const stick = nearBottom();
  $('chat').appendChild(node);
  if (stick) toBottom();
  return node;
}

function agentBubble() {
  const m = mk('div', 'msg msg-agent',
    `<div class="avatar" aria-hidden="true">${ICON.drop}</div><div class="bubble"></div>`);
  return { m, b: m.querySelector('.bubble') };
}

function addUser(text) {
  const m = mk('div', 'msg msg-user', '<div class="bubble"></div>');
  m.querySelector('.bubble').textContent = text;       // never innerHTML: it is the user's text
  push(m);
}

function chatNote(text) { push(mk('div', 'chat-note', `<span>${esc(text)}</span>`)); }

/* What the agent is allowed to know of the conversation. Short, and only what a message might refer
   to: the question, the area, the storm that was used, and the headline that came back. */
function remember(entry) {
  chat.history.push(entry);
  if (chat.history.length > MAX_HISTORY) chat.history.shift();
}

function chooseArea(p) {
  setTool('pan');
  predictBox(p.bbox, p.label);
}

function areaChips(region) {
  const wrap = mk('div', 'chips');
  for (const p of (region && region.presets) || []) {
    const b = mk('button', 'chip');
    b.type = 'button';
    b.textContent = p.label;
    b.onclick = () => chooseArea(p);
    wrap.appendChild(b);
  }
  return wrap;
}

function askChips(questions) {
  const wrap = mk('div', 'chips');
  for (const q of questions) {
    const b = mk('button', 'chip chip-alt');
    b.type = 'button';
    b.textContent = q;
    b.onclick = () => sendQuestion(q);
    wrap.appendChild(b);
  }
  return wrap;
}

function chatReset() {
  if (chat.abort) chat.abort.abort();
  chat.history = [];
  $('chat').replaceChildren();
  const region = state.region;
  const { m, b } = agentBubble();
  if (state.agents) {
    b.innerHTML =
      `<p><b>Hi — I'm the flood-screening agent${region ? ' for ' + esc(region.title) : ''}.</b></p>`
      + '<p>Pick an area below, draw a box on the map, or just <b>name a place</b> — “will '
      + 'Vyttila flood tonight?”. I predict from the live rainfall forecast, and you can ask me '
      + 'what anything means.</p>';
    b.appendChild(mk('div', 'chat-label', 'Start with an area'));
    b.appendChild(areaChips(region));
    b.appendChild(mk('div', 'chat-label', 'Or ask'));
    b.appendChild(askChips(ASK_ME));
    b.appendChild(mk('p', 'muted tiny chat-fine',
      'I never write a number myself: every figure comes from the engine and is checked before you see it.'));
  } else {
    b.innerHTML =
      '<p><b>The agent is not switched on for this deployment.</b></p>'
      + '<p class="muted small">The screening itself still works: pick an area and read the result in '
      + 'the Details tab.</p>';
    b.appendChild(areaChips(region));
  }
  push(m);
}

/* ---- finding a place the user named ----------------------------------------------------------
   The agent says which place the message names; the browser finds it on the map. Presets first
   (exact and instant), then Google's geocoder. The box is a fixed size around the point, the same
   scale as the preset areas, and the place must fall inside a city that has data. */

const normName = (s) => String(s).toLowerCase().replace(/[^a-z0-9 ]+/g, ' ').replace(/\s+/g, ' ').trim();

function regionAt(lat, lng) {
  const inside = (r) => {
    const [w, s, e, n] = r.bbox_wsen;
    return lng >= w && lng <= e && lat >= s && lat <= n;
  };
  return (state.region && inside(state.region) ? state.region : state.regions.find(inside)) || null;
}

async function resolvePlace(name) {
  const want = normName(name);
  for (const r of state.regions) {
    for (const p of r.presets || []) {
      const have = normName(p.label);
      if (want && have && (want === have || want.includes(have) || have.includes(want))) {
        return { bbox: p.bbox, label: p.label, region: r.name };
      }
    }
  }
  if (!(window.google && google.maps && google.maps.Geocoder)) return null;
  const geocoder = new google.maps.Geocoder();
  const [w, s, e, n] = state.region.bbox_wsen;
  const tries = [
    { address: `${name}, ${state.region.title}`, bounds: { west: w, south: s, east: e, north: n } },
    { address: name },
  ];
  for (const req of tries) {
    try {
      const { results } = await geocoder.geocode(req);
      const loc = results && results[0] && results[0].geometry.location;
      if (!loc) continue;
      const lat = loc.lat();
      const lng = loc.lng();
      const r = regionAt(lat, lng);
      const d = 0.014;                                    // about 1.5 km each way
      if (r) return { bbox: [lng - d, lat - d, lng + d, lat + d], label: name, region: r.name };
    } catch { /* no result, or a quota error: try the next form */ }
  }
  return null;
}

/* Select an area: remember it, show it, and load its forecast. */
function setArea(bbox, label) {
  state.lastBbox = bbox;
  state.areaLabel = label;
  state.whatif = false;
  setTool('pan');
  showRect(bbox);
  chatNote(`${label} · ${boxKm2(bbox).toFixed(1)} km²`);
  loadForecast(bbox);      // the Rain tab, and it seeds the what-if slider
  loadRainNow(bbox);       // live gauges, where a city has an open feed
}

/* ---- one agent turn -------------------------------------------------------------------------- */

function startTurn(question, opts) {
  // Only the newest answer offers follow-ups, or the chat fills with stale buttons.
  document.querySelectorAll('#chat .followups, #chat .whatif-inline').forEach((n) => n.remove());
  const { m, b } = agentBubble();
  b.innerHTML =
    '<details class="work" open><summary><span class="spin"></span>'
    + '<span class="work-label">Reading your question…</span></summary><ol class="trace"></ol></details>'
    + '<div class="answer" hidden></div>';
  if (opts.replace && opts.replace.isConnected) opts.replace.replaceWith(m);
  else push(m);
  return {
    m, question, auto: Boolean(opts.auto), finished: false, steps: 0, ms: 0, text: '',
    work: b.querySelector('.work'), label: b.querySelector('.work-label'),
    trace: b.querySelector('.trace'), answer: b.querySelector('.answer'),
  };
}

function addTraceRow(turn, step) {
  turn.steps += 1;
  turn.ms += step.ms || 0;
  const li = document.createElement('li');
  li.className = `step ${String(step.status).replace(/\s+/g, '-')}`;
  const ms = step.ms ? `${(step.ms / 1000).toFixed(1)} s` : '';
  li.innerHTML = `<div class="head"><strong>${esc(step.agent)}</strong>
      <span class="pill">${esc(step.status)}</span><span class="ms">${ms}</span></div>
    ${step.note ? `<div class="note">${esc(step.note)}</div>` : ''}`;
  turn.trace.appendChild(li);
  if (step.status === 'sent back') turn.label.textContent = 'The checker sent a claim back — rewriting…';
  else if (NEXT_STEP[step.agent] && !['failed', 'withheld', 'rejected'].includes(step.status)) {
    turn.label.textContent = NEXT_STEP[step.agent];
  }
  if (nearBottom()) toBottom();
}

function settle(turn, label, cls) {
  turn.finished = true;
  turn.work.classList.add('done');
  if (cls) turn.work.classList.add(cls);
  turn.label.textContent = label;
}

function retryButton(turn) {
  const b = mk('button', 'linkbtn', 'Try again');
  b.type = 'button';
  b.onclick = () => sendQuestion(turn.question, { auto: turn.auto, replace: turn.m });
  return b;
}

function errorTurn(turn, message) {
  if (turn.finished) return;
  settle(turn, 'Something went wrong', 'failed');
  turn.work.open = true;
  turn.answer.hidden = false;
  turn.answer.replaceChildren(mk('p', 'a-error', esc(message || 'The agent could not answer that.')), retryButton(turn));
}

function stopTurn(turn) {
  if (turn.finished) return;
  settle(turn, 'Stopped', 'stopped');
  turn.answer.hidden = false;
  turn.answer.replaceChildren(mk('p', 'muted small', 'Stopped before an answer was written.'), retryButton(turn));
}

/* Facts shown as chips come straight from the engine's output, not from anything a model wrote. */
function factChips(ev) {
  const sc = ev.scenario || {};
  const r = ev.result || {};
  const out = [];
  if (sc.rainfall_mm != null) {
    out.push(`<b>${sc.mode === 'forecast' ? 'Live forecast' : 'What-if'}</b> · ${fmtMm(sc.rainfall_mm)} mm`
      + (sc.duration_h ? ` over ${Math.round(sc.duration_h)} h` : ''));
  }
  const n = r.screening && r.screening.depressions ? r.screening.depressions.length : null;
  if (n != null) out.push(`<b>${n.toLocaleString()}</b> ${n === 1 ? 'place collects' : 'places collect'} water`);
  const ex = r.exposure;
  if (ex && ex.available && ex.people_in_worst_spots) {
    out.push(`~<b>${ex.people_in_worst_spots.toLocaleString()}</b> people in the worst spots that fill`);
  }
  return out;
}

function copyButton(getText) {
  const c = mk('button', 'icon-btn', ICON.copy);
  c.type = 'button';
  c.title = 'Copy';
  c.setAttribute('aria-label', 'Copy the answer');
  c.onclick = () => {
    navigator.clipboard.writeText(getText()).then(() => {
      c.title = 'Copied';
      setTimeout(() => { c.title = 'Copy'; }, 1500);
    }).catch(() => {});
  };
  return c;
}

function actionsRow(turn, { details }) {
  const acts = mk('div', 'a-actions');
  if (turn.text) acts.appendChild(copyButton(() => turn.text));
  const r = mk('button', 'icon-btn', ICON.redo);
  r.type = 'button'; r.title = 'Ask again'; r.setAttribute('aria-label', 'Ask again');
  r.onclick = () => sendQuestion(turn.question, { auto: turn.auto, replace: turn.m });
  acts.appendChild(r);
  if (details) {
    const d = mk('button', 'linkbtn', 'Open details →');
    d.type = 'button';
    d.onclick = () => setTab('details');
    acts.appendChild(d);
  }
  return acts;
}

function renderAnswer(turn, ev) {
  const a = turn.answer;
  a.hidden = false;
  a.replaceChildren();
  const b = ev.briefing;
  if (b) {
    a.insertAdjacentHTML('beforeend',
      `<p class="a-head">${esc(b.headline)}</p>`
      + (b.bullets && b.bullets.length
        ? `<ul class="a-bullets">${b.bullets.map((x) => `<li>${esc(x)}</li>`).join('')}</ul>` : '')
      + (b.caveat ? `<p class="a-caveat">${esc(b.caveat)}</p>` : ''));
    turn.text = [b.headline, ...(b.bullets || []).map((x) => `• ${x}`), b.caveat].filter(Boolean).join('\n');
  } else {
    a.insertAdjacentHTML('beforeend',
      `<p class="a-withheld">${esc(ev.withheld || 'No briefing was produced.')}</p>`
      + '<p class="muted small">The screening itself is unaffected — it is computed, not written. '
      + 'See the map and the Details tab.</p>');
  }
  if ((ev.removed || []).length) {
    a.insertAdjacentHTML('beforeend',
      `<details class="a-removed"><summary>${ev.removed.length} claim(s) removed by the checking agent</summary><ul>`
      + ev.removed.map((r) => `<li><span class="rule">${esc(r.rule || 'unsupported')}</span> ${esc(r.problem)}
          <div class="cut">&ldquo;${esc(r.text)}&rdquo;</div></li>`).join('')
      + '</ul></details>');
  }
  const facts = factChips(ev);
  if (facts.length) a.appendChild(mk('div', 'facts', facts.map((f) => `<span class="fact">${f}</span>`).join('')));
  a.appendChild(actionsRow(turn, { details: true }));
}

function addFollowups(turn, ev) {
  const sc = ev.scenario || {};
  const hasOfficial = Boolean(state.region && state.region.official
    && (state.region.official.return_periods || []).length);
  const hasPeople = Boolean(ev.result && ev.result.exposure && ev.result.exposure.available);
  const box = mk('div', 'followups');
  SUGGESTIONS
    .filter((s) => s.q !== turn.question && (s.needs !== 'official' || hasOfficial)
      && (s.needs !== 'people' || hasPeople))
    .slice(0, 3)
    .forEach((s) => {
      const c = mk('button', 'chip');
      c.type = 'button';
      c.textContent = s.q;
      c.onclick = () => sendQuestion(s.q);
      box.appendChild(c);
    });
  const w = mk('button', 'chip chip-alt', 'What if it rains more?');
  w.type = 'button';
  w.onclick = () => toggleWhatIf(turn, sc);
  box.appendChild(w);
  turn.answer.appendChild(box);
}

/* The agent offers the slider: a hypothesis, run through the same agents, so the answer to "what if
   it is worse than forecast" is a written, checked briefing and not just a recoloured map. */
function toggleWhatIf(turn, sc) {
  const existing = turn.answer.querySelector('.whatif-inline');
  if (existing) { existing.remove(); return; }
  // How long the rain takes to fall is the user's call; it starts at the window of the last answer.
  const lengths = [3, 6, 12, 24];
  const want = sc.duration_h || 3;
  const near = lengths.reduce((a, b) => (Math.abs(b - want) < Math.abs(a - want) ? b : a));
  const w = mk('div', 'whatif-inline',
    '<label>A heavier storm <output>100 mm</output></label>'
    + '<input type="range" min="10" max="300" step="10" value="100" aria-label="Rainfall in millimetres">'
    + '<div class="wi-row"><label class="wi-len">falling over '
    + '<select aria-label="How long the rain falls">'
    + lengths.map((h) => `<option value="${h}"${h === near ? ' selected' : ''}>${h} h</option>`).join('')
    + '</select></label><button type="button" class="btn-sm">Run</button></div>'
    + '<div class="muted tiny">Asks the agent for a checked briefing. The rain is assumed to fall evenly.</div>');
  const range = w.querySelector('input');
  range.oninput = () => { w.querySelector('output').textContent = `${range.value} mm`; };
  w.querySelector('button').onclick = () =>
    sendQuestion(`What if ${range.value} mm of rain falls in ${w.querySelector('select').value} hours here?`);
  turn.answer.appendChild(w);
  if (nearBottom()) toBottom();
}

function finishTurn(turn, ev) {
  const removed = (ev.removed || []).length;
  const sc = ev.scenario || {};
  settle(turn, ev.briefing
    ? `Checked by the verifier · ${turn.steps} steps · ${(turn.ms / 1000).toFixed(1)} s`
      + (removed ? ` · ${removed} claim${removed > 1 ? 's' : ''} removed` : '')
    : 'Briefing withheld');
  turn.work.open = false;
  const stick = nearBottom();
  renderAnswer(turn, ev);
  addFollowups(turn, ev);
  remember({
    q: turn.auto ? `Forecast for ${state.areaLabel || 'the area'}` : turn.question,
    area: state.areaLabel, mode: sc.mode, rainfall_mm: sc.rainfall_mm, duration_h: sc.duration_h,
    headline: ev.briefing ? ev.briefing.headline : null,
  });
  const c = $('chat');
  if (turn.m.offsetHeight > c.clientHeight) turn.m.scrollIntoView({ block: 'start' });
  else if (stick) toBottom();
}

/* A reply with no analysis in it: an explanation, or a request for somewhere to look. */
function finishText(turn, label, text, { followups, areas, cls }) {
  settle(turn, label, cls);
  turn.work.open = false;
  const stick = nearBottom();
  const a = turn.answer;
  a.hidden = false;
  a.replaceChildren(...String(text).split('\n').filter(Boolean).map((p) => mk('p', null, esc(p))));
  turn.text = String(text);
  if (areas) a.appendChild(areaChips(state.region));
  a.appendChild(actionsRow(turn, { details: false }));
  if (followups) {
    const box = mk('div', 'followups');
    const qs = state.lastBbox ? SUGGESTIONS.filter((s) => !s.needs).map((s) => s.q) : ASK_ME;
    qs.filter((q) => q !== turn.question).slice(0, 3).forEach((q) => {
      const c = mk('button', 'chip');
      c.type = 'button';
      c.textContent = q;
      c.onclick = () => sendQuestion(q);
      box.appendChild(c);
    });
    a.appendChild(box);
  }
  if (stick) toBottom();
}

function finishExplain(turn, ev) {
  finishText(turn, ev.answer_checked
    ? `Answered from the product reference · ${(turn.ms / 1000).toFixed(1)} s` : 'No checked answer',
  ev.answer, { followups: true });
  remember({ q: turn.question, area: state.areaLabel,
             headline: String(ev.answer || '').slice(0, 160) });
}

function finishNeedsArea(turn, ev) {
  finishText(turn, 'I need a place first', ev.answer, { areas: true });
}

function finishCityOverview(turn, ev) {
  const sc = ev.scenario || {};
  settle(turn, `City overview · ${turn.steps} steps · ${(turn.ms / 1000).toFixed(1)} s`);
  turn.work.open = false;
  const stick = nearBottom();
  const a = turn.answer;
  a.hidden = false;

  const presets = ev.presets || [];
  const mm = sc.rainfall_mm;
  const dur = sc.duration_h || 24;
  const isForecast = sc.mode === 'forecast';

  const head = mk('p', 'a-head');
  head.textContent = isForecast
    ? `About ${fmtMm(mm)} mm of rain is expected over the next ${dur} hours.`
    : `At ${fmtMm(mm)} mm over ${dur} hours:`;
  a.appendChild(head);

  if (ev.forecast_meta) {
    const src = mk('div', 'city-src');
    src.textContent = `Live forecast · ${ev.forecast_meta.source || 'Google Weather API'}`;
    a.appendChild(src);
  }

  if (presets.length) {
    const sorted = [...presets].sort((x, y) => (y.fills - x.fills) || (y.top_ranked - x.top_ranked));
    const grid = mk('div', 'preset-grid');
    for (const p of sorted) {
      const card = document.createElement('button');
      card.type = 'button';
      card.className = 'preset-card' + (p.fills ? ' has-fills' : '');
      let inner = `<strong>${esc(p.label)}</strong><div class="preset-stats">`;
      inner += `<span>${p.spots} low spots</span>`;
      if (p.top_ranked) inner += `<span>${p.top_ranked} top-ranked</span>`;
      if (p.fills) inner += `<span class="fill-count">${p.fills} would fill</span>`;
      if (p.road_km) inner += `<span>${p.road_km} km roads</span>`;
      inner += '</div>';
      card.innerHTML = inner;
      card.onclick = () => chooseArea({ label: p.label, bbox: p.bbox });
      grid.appendChild(card);
    }
    a.appendChild(grid);
  }

  const cav = mk('p', 'caveat');
  cav.textContent = 'Terrain screening · stormwater drains are not in the data';
  a.appendChild(cav);
  a.appendChild(actionsRow(turn, { details: false }));

  const hint = mk('p', 'city-hint');
  hint.textContent = 'Tap an area to look closer:';
  a.appendChild(hint);
  a.appendChild(areaChips(state.region));

  turn.text = ev.answer || '';
  remember({
    q: turn.question, area: state.region ? state.region.title : null,
    mode: sc.mode, rainfall_mm: mm, duration_h: dur,
    headline: head.textContent,
  });
  const c = $('chat');
  if (turn.m.offsetHeight > c.clientHeight) turn.m.scrollIntoView({ block: 'start' });
  else if (stick) toBottom();
}

function onAskEvent(turn, ev) {
  if (ev.event === 'step') { addTraceRow(turn, ev); return null; }
  if (ev.event === 'error') { errorTurn(turn, ev.detail); return null; }
  if (ev.event === 'place_needed') return { place: ev.place };
  if (ev.event === 'done') {
    if (ev.kind === 'explain') finishExplain(turn, ev);
    else if (ev.kind === 'needs_area') finishNeedsArea(turn, ev);
    else if (ev.kind === 'city_overview') finishCityOverview(turn, ev);
    else {
      // The agent settled a rainfall and a window (from the live forecast, or the figure in the
      // question). The map, the result list and the road playback follow that, not the slider.
      if (ev.result) {
        const sc = ev.scenario || {};
        const driver = { total: sc.rainfall_mm ?? Number($('rain').value),
                         dur: sc.duration_h || 6,
                         profile: ev.rain_profile || null,           // the forecast's hourly shape
                         profileStart: ev.rain_profile_start || null, // when that window starts
                         forecastMeta: ev.forecast_meta || null };
        try { render(ev.result, driver); notifyDetails(); } catch (err) { setStatus(`Could not draw the result: ${err.message}`, true); }
      }
      finishTurn(turn, ev);
    }
  }
  return null;
}

/* ---- sending --------------------------------------------------------------------------------- */

function setComposerBusy(busy) {
  const btn = $('asksend');
  btn.classList.toggle('stop', busy);
  btn.innerHTML = busy ? ICON.stop : ICON.send;
  btn.title = busy ? 'Stop' : 'Send (Enter)';
  btn.setAttribute('aria-label', busy ? 'Stop' : 'Send');
}

async function readEvents(res, onEvent) {
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buf = '';
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    let cut;
    while ((cut = buf.indexOf('\n')) >= 0) {
      const line = buf.slice(0, cut).trim();
      buf = buf.slice(cut + 1);
      if (line) onEvent(JSON.parse(line));
    }
  }
}

/* One round trip. If the agent says the message names a place, the browser finds it and the same
   turn asks again with the box; `placeResolved` stops that happening more than once. */
async function askLoop(turn, q, placeResolved) {
  const ctl = new AbortController();
  chat.abort = ctl;
  const res = await fetch('/api/ask', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    signal: ctl.signal,
    body: JSON.stringify({
      question: q, bbox: state.lastBbox || null, region: state.region.name,
      rainfall_mm: Number($('rain').value),
      return_period: $('rp').value ? Number($('rp').value) : null,
      history: chat.history, area_label: state.areaLabel || null,
      place_resolved: placeResolved || null,
    }),
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || 'the agent layer could not answer that');
  }
  let place = null;
  await readEvents(res, (ev) => { const r = onAskEvent(turn, ev); if (r && r.place) place = r.place; });
  if (place) { await continueWithPlace(turn, q, place, placeResolved); return; }
  if (!turn.finished) errorTurn(turn, 'The answer ended before it was finished.');
}

async function continueWithPlace(turn, q, place, alreadyResolved) {
  if (alreadyResolved) { errorTurn(turn, `I could not pin down ${place} on the map.`); return; }
  turn.label.textContent = `Finding ${place} on the map…`;
  const hit = await resolvePlace(place);
  if (!hit) {
    const cities = state.regions.map((r) => r.title).join(' and ');
    finishText(turn, 'Could not find that place',
      `I could not find “${place}” inside the areas I have data for (${cities}). `
      + 'Draw a box on the map or pick one of these:', { areas: true });
    return;
  }
  if (hit.region !== state.region.name) selectRegion(hit.region, { keepChat: true });
  setArea(hit.bbox, hit.label);
  await askLoop(turn, q, place);
}

async function sendQuestion(q, opts = {}) {
  q = (q || '').trim();
  if (!q) return;
  if (!state.agents) { chatNote('The agent is not switched on for this deployment.'); return; }
  // A new area replaces whatever was running for the old one; anything else waits its turn.
  if (chat.busy) {
    if (!opts.auto) return;
    if (chat.abort) chat.abort.abort();
  }
  if (!opts.auto && !opts.replace) addUser(q);
  const turn = startTurn(q, opts);
  const run = ++chat.run;
  chat.busy = true;
  setComposerBusy(true);
  try {
    await askLoop(turn, q, null);
  } catch (err) {
    if (err.name === 'AbortError') stopTurn(turn);
    else errorTurn(turn, err.message);
  } finally {
    if (chat.run === run) {          // a newer run owns the composer now
      chat.busy = false;
      chat.abort = null;
      setComposerBusy(false);
    }
  }
}

function growComposer() {
  const t = $('question');
  t.style.height = 'auto';
  t.style.height = `${Math.min(140, t.scrollHeight)}px`;
}

function wireAsk(enabled) {
  state.agents = Boolean(enabled);
  const form = $('composer');
  const box = $('question');
  setComposerBusy(false);
  if (!state.agents) {
    box.disabled = true;
    $('asksend').disabled = true;
    box.placeholder = 'The agent is not switched on for this deployment';
  }
  form.addEventListener('submit', (e) => {
    e.preventDefault();
    if (chat.busy) { if (chat.abort) chat.abort.abort(); return; }   // the send button is Stop while running
    const q = box.value;
    box.value = '';
    growComposer();
    sendQuestion(q);
  });
  box.addEventListener('keydown', (e) => {
    if (e.key !== 'Enter' || e.shiftKey || e.isComposing) return;
    e.preventDefault();
    if (!chat.busy) form.requestSubmit();                   // Enter never stops a running answer
  });
  box.addEventListener('input', growComposer);
  $('chat-new').onclick = () => chatReset();
}

/* The findings in plain sentences, from the engine's own numbers, before any detail. */
function renderSummary(res, driver) {
  const sc = res.screening;
  const n = sc.depressions.length;
  const top = sc.depressions.filter((d) => d.pooling_index >= 4).length;
  const fills = spotsThatFill(res);
  const mm = driver ? driver.total : Number($('rain').value);
  const lines = [
    `In this <b>${res.grid.area_km2} km\u00b2</b> area, <b>${n.toLocaleString()}</b> low ${n === 1 ? 'spot' : 'spots'} `
    + `can collect rain water. The <b>${top.toLocaleString()}</b> worst ${top === 1 ? 'one is' : 'ones are'} shown in the darkest blue.`,
    `At <b>${fmtMm(mm)} mm</b> of rain, <b>${fills.toLocaleString()}</b> of them fill up.`,
  ];
  const ex = res.exposure;
  if (ex && ex.available && ex.people_in_worst_spots) {
    lines.push(`Roughly <b>${ex.people_in_worst_spots.toLocaleString()}</b> people live in the worst spots that fill at this rainfall.`);
  }
  $('summary').innerHTML = lines.map((l) => `<p>${l}</p>`).join('');
}

function render(res, driver) {
  const sc = res.screening;
  if (state.layer) state.layer.setMap(null);
  if (state.officialLayer) { state.officialLayer.setMap(null); state.officialLayer = null; }

  // The official layer goes underneath our screening: it is the backdrop being compared against,
  // and the two are never merged into one number.
  if (state.mapReady && res.official) {
    const off = new google.maps.Data({ map: state.map });
    off.addGeoJson(res.official.geojson);
    off.setStyle((f) => ({
      fillColor: OFFICIAL_FILL[f.getProperty('band')], fillOpacity: 0.5,
      strokeWeight: 0, zIndex: 0, clickable: false,
    }));
    state.officialLayer = off;
  }

  if (state.mapReady) {
    const layer = new google.maps.Data({ map: state.map });
    layer.addGeoJson(res.geojson);
    layer.setStyle((f) => {
      const idx = f.getProperty('pooling_index');
      return {
        fillColor: POOLING_FILL[idx], fillOpacity: f.getProperty('fills_at_rainfall') ? 0.75 : 0.35,
        strokeColor: POOLING_FILL[idx], strokeWeight: 0.6, strokeOpacity: 0.9, zIndex: idx,
      };
    });
    const info = new google.maps.InfoWindow();
    layer.addListener('click', (ev) => {
      if (state.tool === 'box') return;   // do not pop a window mid-draw
      const p = ev.feature.getProperty.bind(ev.feature);
      info.setContent(
        `<b>${p('id')}</b> — pooling rank ${p('pooling_index')} of 5<br>` +
        `${p('contributing_ha')} ha drains into it<br>` +
        `fills ${p('fill_order')}${ordinal(p('fill_order'))} of ${sc.depressions.length}` +
        (p('low_confidence') ? '<br><i>shallower than the terrain error — low confidence</i>' : ''));
      info.setPosition(ev.latLng);
      info.open(state.map);
    });
    state.layer = layer;
  }

  $('results').hidden = false;
  renderSummary(res, driver);
  updateRainEffect(res, driver);
  $('gridline').textContent =
    `${res.grid.area_km2} km² analysed at ${res.grid.res_m} m resolution · ${res.region.title}` +
    (res.grid.despiked_px ? ` · ${res.grid.despiked_px} bad pixels repaired` : '');

  $('spots').replaceChildren(...sc.depressions.slice(0, 5).map((d) => {
    const li = document.createElement('li');
    const rank = document.createElement('span');
    rank.className = `rank s${d.pooling_index}`;
    rank.textContent = d.pooling_index;
    const body = document.createElement('div');
    const where = d.nearest_road
      ? `<b>${esc(d.nearest_road.name)}</b> <span class="muted">· ${d.nearest_road.distance_m} m</span>`
      : `<b>Low spot ${esc(String(d.id).replace(/^d_0*/, ''))}</b> <span class="muted">\u00b7 no named road close by</span>`;
    body.innerHTML = `${where}` +
      `<div class="meta">fills ${d.fill_order}${ordinal(d.fill_order)} of ${sc.depressions.length} \u00b7 `
      + `rain from about ${d.contributing_ha >= 10 ? Math.round(d.contributing_ha) : d.contributing_ha} ha of land runs into it</div>`;
    li.append(rank, body);
    li.onclick = () => zoomTo(d.id);
    return li;
  }));

  renderPeople(res.exposure);
  renderTimeline(res, driver);
  loadPreparations(driver ? Math.round(driver.total) : Number($('rain').value));
  renderOfficial(res);

  const roads = $('roads');
  if (sc.routes_affected.length) {
    roads.replaceChildren(...sc.routes_affected.slice(0, 6).map((r) => {
      const li = document.createElement('li');
      li.innerHTML = `${r.road}<div class="meta">${r.segments} segment${r.segments > 1 ? 's' : ''}` +
        ` · ${(r.length_m / 1000).toFixed(1)} km through top-ranked spots</div>`;
      return li;
    }));
  } else {
    roads.replaceChildren(el('li', 'No road runs through a top-ranked spot in this box.'));
  }

  $('excluded-wrap').hidden = !sc.excluded.length;
  $('excluded').replaceChildren(...sc.excluded.map((x) => el('li', `${x.id} — ${x.reason}`)));

  const caveats = [
    `<b>Ranks, not depths.</b> ${sc.caveat}`,
    '<b>Drainage:</b> no storm-drain network is public anywhere, so pooling is overstated where drains work.',
    `<b>Validated layer:</b> ${sc.confidence.validated_layer === 'not_checked'
      ? 'not loaded yet — nothing here is checked against an official depth map.'
      : sc.confidence.validated_layer}`,
    `<b>Water bodies:</b> ${sc.confidence.water_bodies === 'osm'
      ? 'from OpenStreetMap.' : 'terrain only — no water layer for this box.'}`,
  ];
  $('caveats').replaceChildren(...caveats.map((h) => {
    const li = document.createElement('li');
    li.innerHTML = h;
    return li;
  }));

  const t = res.timings_s;
  $('timings').textContent =
    `${t.total.toFixed(2)} s — terrain ${(t.open_s + t.read_s + t.reproject_s).toFixed(2)} s, ` +
    `layers ${t.osm_s.toFixed(2)} s, water ${t.fill_route_s.toFixed(2)} s, roads ${t.routes_s.toFixed(2)} s`;
}

function renderOfficial(res) {
  const wrap = $('official-wrap');
  const o = res.official;
  if (!o) { wrap.hidden = true; return; }
  wrap.hidden = false;
  const a = res.agreement;
  const bands = o.band_labels
    .map((l, i) => `<span class="ob${i}">${l}</span>`).join('');

  let agreeLine = '';
  if (a && a.checked) {
    agreeLine = a.discriminating
      ? `<div class="claim"><b>${a.enrichment}×</b> — our top-ranked spots fall inside this
           official flood extent ${a.enrichment}× more often than chance would put them there
           (${a.inside} of ${a.checked}, against ${(a.baseline * 100).toFixed(1)}% of the box being
           inundated).<div class="srcline">${a.note}</div></div>`
      : `<div class="claim">${a.inside} of ${a.checked} top-ranked spots fall inside the official
           extent, but ${(a.baseline * 100).toFixed(0)}% of this box is inundated at this return
           period.<div class="srcline">${a.note}</div></div>`;
  }

  wrap.querySelector('#official').innerHTML = `
    ${agreeLine}
    <div class="muted small">${(o.inundated_share * 100).toFixed(1)}% of the box is inundated at a
      1-in-${o.return_period_years}-year event. Deepest ${o.deepest}, median ${o.median_where_wet}
      (${o.units}).</div>
    <div class="bands">${bands}</div>
    <div class="srcline">${o.title} — ${o.source}. About ${o.native_res_m_approx} m,
      shown as published. ${o.caveats[0]}</div>`;
}

function el(tag, text) {
  const n = document.createElement(tag);
  n.textContent = text;
  return n;
}

const ordinal = (n) => {
  const r = n % 100;
  if (r >= 11 && r <= 13) return 'th';
  return ['th', 'st', 'nd', 'rd'][n % 10] || 'th';
};

function zoomTo(id) {
  if (!state.layer) return;
  state.layer.forEach((f) => {
    if (f.getProperty('id') !== id) return;
    const b = new google.maps.LatLngBounds();
    f.getGeometry().forEachLatLng((ll) => b.extend(ll));
    state.map.fitBounds(b, 120);
  });
}

$('rain').addEventListener('input', (e) => {
  state.whatif = true;                             // dragging the slider is a what-if
  $('rainout').textContent = `${e.target.value} mm`;
});
$('rain').addEventListener('change', (e) => {
  state.whatif = true;
  if (!state.lastBbox) return;
  chatNote(`What-if override \u00b7 ${e.target.value} mm \u00b7 map updated`);
  analyse(state.lastBbox);
});
$('rain-reset').addEventListener('click', () => {
  state.whatif = false;
  seedWhatIf();
  if (!state.lastBbox) return;
  chatNote('Back to the live forecast');
  if (state.agents) sendQuestion(DEFAULT_PREDICT_Q, { auto: true });
  else analyse(state.lastBbox);
});

window.addEventListener('error', (ev) => {
  setStatus(`Something broke: ${ev.message}`, true);
});
window.addEventListener('unhandledrejection', (ev) => {
  setStatus(`Something broke: ${ev.reason && ev.reason.message ? ev.reason.message : ev.reason}`, true);
});

boot().catch((e) => setStatus(`Could not start: ${e.message}`, true));
