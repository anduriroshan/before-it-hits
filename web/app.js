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

const state = {
  map: null, rect: null, layer: null, region: null,
  busy: false, mapReady: false, tool: 'pan', drag: null, lastBbox: null, officialLayer: null,
  regions: [], region: null, regionRect: null, forecast: null, rainMode: 'whatif',
  agents: false, asking: false, newsFor: null,
};

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
  buildRegionPicker();
  selectRegion(state.regions[0].name);
  wireToolbar();
  $('news').addEventListener('toggle', () => { if ($('news').open) loadNews(); });
  wireAsk(cfg.agents);

  if (!cfg.maps_api_key) {
    mapFailed('No Maps API key is configured. Set MAPS_API_KEY in .env.local and restart.');
    return;
  }
  const s = document.createElement('script');
  s.src = `https://maps.googleapis.com/maps/api/js?key=${cfg.maps_api_key}&v=weekly&callback=initMap`;
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
function selectRegion(name) {
  const region = state.regions.find((r) => r.name === name);
  if (!region) return;
  state.region = region;
  $('region').value = name;

  const counts = region.vectors.counts || {};
  $('regionline').textContent =
    `staged ${region.staged} · ${(counts.roads || 0).toLocaleString()} roads` +
    (counts.buildings ? ` · ${counts.buildings.toLocaleString()} buildings` : '');
  $('regionnote').textContent = region.note || '';
  $('credits').innerHTML =
    `Terrain: ${(region.terrain && region.terrain.source) || 'DeltaDTM v1.1'}. ` +
    `Roads and water: ${region.vectors.licence}. Basemap © Google — display only.`;

  buildPresets(region);
  buildReturnPeriods(region);
  loadReplay(region);
  resetNews(region);
  clearAll();
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
      if (state.lastBbox) analyse(state.lastBbox);
    });
  }
}

function buildPresets(region) {
  $('presets').replaceChildren(...(region.presets || []).map((p) => {
    const b = document.createElement('button');
    b.className = 'preset';
    b.textContent = p.label;
    b.onclick = () => { setTool('pan'); showRect(p.bbox); analyse(p.bbox); };
    return b;
  }));
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
  // While the box tool is active the map must not pan, or a drag would do both at once.
  if (state.mapReady) state.map.setOptions({ draggable: tool !== 'box', draggableCursor: tool === 'box' ? 'crosshair' : null });
}

function clearAll() {
  if (state.rect) { state.rect.setMap(null); state.rect = null; }
  if (state.layer) { state.layer.setMap(null); state.layer = null; }
  if (state.officialLayer) { state.officialLayer.setMap(null); state.officialLayer = null; }
  state.lastBbox = null;
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

window.initMap = function initMap() {
  try {
    const [w, s, e, n] = state.region.bbox_wsen;
    const bounds = new google.maps.LatLngBounds({ lat: s, lng: w }, { lat: n, lng: e });
    state.map = new google.maps.Map($('map'), {
      center: bounds.getCenter(), zoom: 12,
      streetViewControl: false, fullscreenControl: false,
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
    analyse(bbox);
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

async function analyse(bbox) {
  if (state.busy) return;
  state.busy = true;
  state.lastBbox = bbox;
  loadForecast(bbox);   // fire-and-forget: a weather outage must not stop the screening
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
    if (state.agents) $('ask').hidden = false;
  } catch (err) {
    setStatus(err.message, true);
    $('results').hidden = true;
  } finally {
    state.busy = false;
  }
}

/* The forecast is live and can fail. It is fetched separately from the analysis so that a
   weather outage costs you the rainfall panel and nothing else. */
async function loadForecast(bbox) {
  const el = $('forecast');
  el.innerHTML = '<span class="muted">checking the forecast…</span>';
  try {
    const r = await fetch('/api/forecast', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ bbox, hours: 48 }),
    });
    const body = await r.json();
    if (!r.ok) throw new Error(body.detail || 'no forecast available');
    state.forecast = body;
    renderForecast();
  } catch (err) {
    state.forecast = null;
    el.innerHTML = `<span class="muted">No live forecast (${err.message}). `
      + 'The rainfall above is a what-if.</span>';
  }
}

function renderForecast() {
  const f = state.forecast;
  if (!f) return;
  const windows = Object.entries(f.peaks)
    .map(([h, p]) => `<button data-mm="${p.mm}" data-h="${h}"
        class="${state.rainMode === `peak${h}` ? 'on' : ''}">${h} h: ${p.mm} mm</button>`)
    .join('');
  $('forecast').innerHTML = `
    <div class="line"><span class="muted">Forecast next ${f.hours_ahead} h:</span>
      <span class="num">${f.total_mm} mm</span></div>
    <div class="windows">${windows}</div>
    <div class="why">Worst rolling window, not the daily total. A point forecast at the box
      centre; no return period inferred.</div>`;

  $('forecast').querySelectorAll('button').forEach((b) => {
    b.onclick = () => {
      state.rainMode = `peak${b.dataset.h}`;
      const mm = Math.round(Number(b.dataset.mm));
      $('rain').value = String(mm);
      $('rainout').textContent = `${mm} mm · forecast worst ${b.dataset.h} h`;
      renderForecast();
      if (state.lastBbox) analyse(state.lastBbox);
    };
  });
}

/* ---- who is nearby (F7) --------------------------------------------------------------------
   One modelled line, with the method folded away. By area, never per home. */
function renderPeople(ex) {
  const el = $('people');
  if (!ex || !ex.available || !ex.people_in_worst_spots) { el.hidden = true; return; }
  el.hidden = false;
  el.innerHTML = `
    <h2>Who is nearby</h2>
    <p class="peoplenum"><b>~${ex.people_in_worst_spots.toLocaleString()}</b> people in the worst
      spots<span class="muted">, of ~${ex.people_in_box.toLocaleString()} modelled in this box</span></p>
    <details class="fine"><summary>How this is modelled</summary>
      <ul>${ex.caveats.map((c) => `<li>${esc(c)}</li>`).join('')}</ul></details>`;
}

/* ---- in the news now (A13) -----------------------------------------------------------------
   Live, display-only, and loaded only when the panel is opened — GDELT rate-limits hard and the
   screening must never wait on it. Never stored, never scored, changes no number on the page. */
function resetNews(region) {
  state.newsFor = null;
  $('news').open = false;
  $('news-body').innerHTML = '<p class="muted small">Open to load live reports for this city.</p>';
}

async function loadNews() {
  const region = state.region;
  if (!region || state.newsFor === region.name) return;
  state.newsFor = region.name;
  const body = $('news-body');
  body.innerHTML = '<p class="muted small">checking the news…</p>';
  try {
    const d = await fetch(`/api/news?region=${encodeURIComponent(region.name)}`)
      .then((r) => { if (!r.ok) throw new Error('news unavailable'); return r.json(); });
    renderNews(d);
  } catch {
    state.newsFor = null;        // let a later open retry
    body.innerHTML = '<p class="muted small">No live news right now (the index may be busy).</p>';
  }
}

function renderNews(d) {
  const arts = (d.articles || []).slice(0, 6).map((a) =>
    `<li><a href="${esc(a.url)}" target="_blank" rel="noopener">${esc(a.title)}</a>
      <span class="src">${esc(a.domain)}${a.seen ? ' · ' + esc(a.seen.slice(0, 10)) : ''}</span></li>`).join('');
  $('news-body').innerHTML = `
    ${d.digest ? `<p class="digest">${esc(d.digest)}</p>` : ''}
    <ul class="newslist">${arts || '<li class="muted small">No recent reports found.</li>'}</ul>
    <p class="muted tiny">${esc(d.note)}</p>`;
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

const CHIPS = [
  'Which spots fill first here?',
  'What if 90 mm falls in an hour?',
  'How does this compare with the official map?',
];

function wireAsk(enabled) {
  state.agents = Boolean(enabled);
  if (!state.agents) return;
  $('askchips').innerHTML = CHIPS.map((c) => `<button type="button">${esc(c)}</button>`).join('');
  $('askchips').querySelectorAll('button').forEach((b) => {
    b.onclick = () => { $('question').value = b.textContent; askQuestion(); };
  });
  $('asksend').onclick = () => askQuestion();
  $('question').addEventListener('keydown', (e) => { if (e.key === 'Enter') askQuestion(); });
}

async function askQuestion() {
  const q = $('question').value.trim();
  if (!q || state.asking || state.busy || !state.lastBbox) return;
  state.asking = true;
  $('asksend').disabled = true;
  $('trace').hidden = false;
  $('trace').innerHTML = '';
  $('briefing').hidden = true;
  try {
    const res = await fetch('/api/ask', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        question: q, bbox: state.lastBbox, region: state.region.name,
        rainfall_mm: Number($('rain').value),
        return_period: $('rp').value ? Number($('rp').value) : null,
      }),
    });
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      throw new Error(body.detail || 'the agent layer could not answer that');
    }
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
        if (line) onAskEvent(JSON.parse(line));
      }
    }
  } catch (err) {
    addTraceRow({ agent: 'Coordinator', status: 'failed', ms: 0, note: err.message });
  } finally {
    state.asking = false;
    $('asksend').disabled = false;
  }
}

function onAskEvent(ev) {
  if (ev.event === 'step') addTraceRow(ev);
  else if (ev.event === 'error') addTraceRow({ agent: 'Coordinator', status: 'failed', ms: 0, note: ev.detail });
  else if (ev.event === 'done') {
    if (ev.result) render(ev.result);          // the map and the result list follow the question
    renderBriefing(ev);
  }
}

function addTraceRow(step) {
  const li = document.createElement('li');
  li.className = `step ${String(step.status).replace(/\s+/g, '-')}`;
  const ms = step.ms ? `${(step.ms / 1000).toFixed(1)} s` : '';
  li.innerHTML = `<div class="head"><strong>${esc(step.agent)}</strong>
      <span class="pill">${esc(step.status)}</span><span class="ms">${ms}</span></div>
    ${step.note ? `<div class="note">${esc(step.note)}</div>` : ''}`;
  $('trace').appendChild(li);
}

function renderBriefing(ev) {
  const el = $('briefing');
  el.hidden = false;
  if (!ev.briefing) {
    el.innerHTML = `<p class="withheld">${esc(ev.withheld || 'No briefing was produced.')}</p>
      <p class="muted small">The screening below is unaffected: it is computed, not written.</p>`;
    return;
  }
  const b = ev.briefing;
  const removed = (ev.removed || []).map((r) =>
    `<li><span class="rule">${esc(r.rule || 'unsupported')}</span> ${esc(r.problem)}
       <div class="cut">&ldquo;${esc(r.text)}&rdquo;</div></li>`).join('');
  el.innerHTML = `
    <p class="headline">${esc(b.headline)}</p>
    <ul class="bullets">${b.bullets.map((x) => `<li>${esc(x)}</li>`).join('')}</ul>
    <p class="caveat">${esc(b.caveat)}</p>
    ${removed ? `<details class="removed" open><summary>${ev.removed.length} claim(s) removed by
      the checking agent</summary><ul>${removed}</ul></details>` : ''}
    <p class="muted small">Every figure above was matched against the engine's own output before
      this was shown. Nothing here was written by the engine, and no figure here was written by a
      model.</p>`;
}

function render(res) {
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
  $('pitcount').textContent = sc.depressions.length.toLocaleString();
  $('pitword').textContent = sc.depressions.length === 1 ? 'place collects water' : 'places collect water';
  $('gridline').textContent =
    `${res.grid.area_km2} km² at ${res.grid.res_m} m · ${res.region.title}` +
    (res.grid.despiked_px ? ` · ${res.grid.despiked_px} bad pixels repaired` : '');

  $('spots').replaceChildren(...sc.depressions.slice(0, 8).map((d) => {
    const li = document.createElement('li');
    const rank = document.createElement('span');
    rank.className = `rank s${d.pooling_index}`;
    rank.textContent = d.pooling_index;
    const body = document.createElement('div');
    const where = d.nearest_road
      ? `<b>${esc(d.nearest_road.name)}</b> <span class="muted">· ${d.nearest_road.distance_m} m</span>`
      : `<b>${esc(d.id)}</b>`;
    body.innerHTML = `${where} — ${d.contributing_ha} ha drains in` +
      `<div class="meta">fills ${d.fill_order}${ordinal(d.fill_order)}</div>`;
    li.append(rank, body);
    li.onclick = () => zoomTo(d.id);
    return li;
  }));

  renderPeople(res.exposure);
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
  state.rainMode = 'whatif';                       // dragging the slider is a what-if again
  $('rainout').textContent = `${e.target.value} mm`;
  renderForecast();
});
$('rain').addEventListener('change', () => { if (state.lastBbox) analyse(state.lastBbox); });

window.addEventListener('error', (ev) => {
  setStatus(`Something broke: ${ev.message}`, true);
});
window.addEventListener('unhandledrejection', (ev) => {
  setStatus(`Something broke: ${ev.reason && ev.reason.message ? ev.reason.message : ev.reason}`, true);
});

boot().catch((e) => setStatus(`Could not start: ${e.message}`, true));
