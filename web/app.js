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

/* Localities named in the 28 May 2024 Kochi cloudburst reporting. */
const PRESETS = [
  { label: 'Edappally', bbox: [76.295, 10.010, 76.325, 10.040] },
  { label: 'Palarivattom', bbox: [76.2968, 9.9967, 76.3168, 10.0167] },
  { label: 'Vyttila', bbox: [76.305, 9.955, 76.330, 9.980] },
  { label: 'Kakkanad', bbox: [76.330, 10.000, 76.360, 10.028] },
];

const state = {
  map: null, rect: null, layer: null, region: null,
  busy: false, mapReady: false, tool: 'pan', drag: null, lastBbox: null, officialLayer: null,
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
  let cfg, region;
  try {
    [cfg, region] = await Promise.all([
      fetch('/api/config').then((r) => r.json()),
      fetch('/api/region').then((r) => r.json()),
    ]);
  } catch (err) {
    setStatus(`Could not reach the engine: ${err.message}`, true);
    return;
  }
  state.region = region;
  $('regionline').textContent =
    `${region.title} · staged ${region.staged} · ${region.vectors.counts.roads.toLocaleString()} roads`;
  $('credits').innerHTML =
    `Terrain: DeltaDTM v1.1 (CC BY 4.0). Roads, water and buildings: ${region.vectors.licence}. ` +
    'Basemap © Google — display only.';
  $('legend-note').textContent = 'No validated depth layer loaded yet, so these are relative ranks.';

  buildPresets();
  buildReturnPeriods(region);
  wireToolbar();

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

function buildReturnPeriods(region) {
  const sel = $('rp');
  const periods = (region.official && region.official.return_periods) || [];
  if (!periods.length) {
    sel.disabled = true;
    $('rpout').textContent = 'none published here';
    return;
  }
  for (const rp of periods) {
    const o = document.createElement('option');
    o.value = String(rp);
    o.textContent = `1-in-${rp}-year`;
    sel.appendChild(o);
  }
  sel.value = '50';
  sel.addEventListener('change', () => {
    $('rpout').textContent = sel.value ? `1-in-${sel.value}-year` : 'off';
    if (state.lastBbox) analyse(state.lastBbox);
  });
}

function buildPresets() {
  $('presets').replaceChildren(...PRESETS.map((p) => {
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

function wireToolbar() {
  $('toolbar').addEventListener('click', (ev) => {
    const btn = ev.target.closest('button');
    if (!btn) return;
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
      center: bounds.getCenter(), zoom: 13,
      mapTypeControl: true, streetViewControl: false, fullscreenControl: false,
      zoomControlOptions: { position: google.maps.ControlPosition.RIGHT_TOP },
    });

    // The staged region, so it is obvious where the engine can answer.
    new google.maps.Rectangle({
      bounds, map: state.map, clickable: false,
      strokeColor: '#0e7490', strokeOpacity: 0.5, strokeWeight: 1.5, fillOpacity: 0,
    });

    // Everything below is set up synchronously. An earlier version waited for the map's 'idle'
    // event, which meant one unlucky async error left the toolbar hidden with nothing on screen
    // to say why. UI chrome should never depend on a map event firing.
    enableBoxDrawing();
    state.mapReady = true;
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
  setStatus('Reading the terrain and working out where water goes…');
  try {
    const res = await fetch('/api/analyse', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        bbox, rainfall_mm: Number($('rain').value),
        return_period: $('rp').value ? Number($('rp').value) : null,
      }),
    });
    const body = await res.json();
    if (!res.ok) throw new Error(body.detail || 'the engine could not analyse that box');
    render(body);
    setStatus('');
  } catch (err) {
    setStatus(err.message, true);
    $('results').hidden = true;
  } finally {
    state.busy = false;
  }
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
    body.innerHTML = `<b>${d.id}</b> — ${d.contributing_ha} ha drains in` +
      `<div class="meta">fills ${d.fill_order}${ordinal(d.fill_order)}</div>`;
    li.append(rank, body);
    li.onclick = () => zoomTo(d.id);
    return li;
  }));

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

$('rain').addEventListener('input', (e) => { $('rainout').textContent = `${e.target.value} mm`; });
$('rain').addEventListener('change', () => { if (state.lastBbox) analyse(state.lastBbox); });

window.addEventListener('error', (ev) => {
  setStatus(`Something broke: ${ev.message}`, true);
});
window.addEventListener('unhandledrejection', (ev) => {
  setStatus(`Something broke: ${ev.reason && ev.reason.message ? ev.reason.message : ev.reason}`, true);
});

boot().catch((e) => setStatus(`Could not start: ${e.message}`, true));
