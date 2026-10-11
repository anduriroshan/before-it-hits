/* Draw a box, ask the engine what happens there, draw the answer.
   The map is a basemap and a drawing surface only: nothing is measured or traced from it.

   Dragging is implemented on plain map mouse events rather than the Maps drawing library, so the
   one interaction the whole product depends on has no extra dependency to fail. If the map cannot
   load at all, the preset areas still work: the analysis never needed the map. */

const $ = (id) => document.getElementById(id);
const POOLING_FILL = { 5: '#0b3d91', 4: '#1d6fd0', 3: '#4a9fe0', 2: '#8fc5ec', 1: '#c8e2f5' };

/* Localities named in the 28 May 2024 Kochi cloudburst reporting. */
const PRESETS = [
  { label: 'Edappally', bbox: [76.295, 10.010, 76.325, 10.040] },
  { label: 'Palarivattom', bbox: [76.2968, 9.9967, 76.3168, 10.0167] },
  { label: 'Vyttila', bbox: [76.305, 9.955, 76.330, 9.980] },
  { label: 'Kakkanad', bbox: [76.330, 10.000, 76.360, 10.028] },
];

const state = {
  map: null, rect: null, layer: null, region: null, busy: false,
  dragStart: null, mapReady: false, armed: false, lastBbox: null,
};

function setStatus(text, isError = false) {
  const el = $('status');
  el.hidden = !text;
  el.textContent = text || '';
  el.classList.toggle('error', Boolean(isError));
}

function mapFailed(why) {
  state.mapReady = false;
  $('mapfallback').hidden = false;
  $('mapfallback-why').textContent = why;
  $('legend').hidden = true;
}

/* Maps calls this itself when the key is rejected; without it the failure is a silent grey box. */
window.gm_authFailure = () => mapFailed(
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
    `Basemap © Google — display only.`;
  $('legend-note').textContent = 'No validated depth layer loaded yet, so these are relative ranks.';

  buildPresets();

  if (!cfg.maps_api_key) {
    mapFailed('No Maps API key is configured. Set MAPS_API_KEY in .env.local and restart.');
    return;
  }
  const s = document.createElement('script');
  s.src = `https://maps.googleapis.com/maps/api/js?key=${cfg.maps_api_key}&v=weekly&callback=initMap`;
  s.async = true;
  s.onerror = () => mapFailed('The Maps script could not be fetched. Check the network connection.');
  document.head.appendChild(s);

  setTimeout(() => {
    if (!state.mapReady) mapFailed('The Maps script did not finish loading.');
  }, 12000);
}

function buildPresets() {
  const wrap = $('presets');
  wrap.replaceChildren(...PRESETS.map((p) => {
    const b = document.createElement('button');
    b.className = 'preset';
    b.textContent = p.label;
    b.onclick = () => {
      arm(false);
      setRect(p.bbox);
      analyse(p.bbox);
    };
    return b;
  }));
}

window.initMap = function initMap() {
  try {
    const [w, s, e, n] = state.region.bbox_wsen;
    const bounds = new google.maps.LatLngBounds({ lat: s, lng: w }, { lat: n, lng: e });
    state.map = new google.maps.Map($('map'), {
      center: bounds.getCenter(),
      zoom: 13,
      mapTypeControl: true,
      streetViewControl: false,
      fullscreenControl: false,
      draggableCursor: 'crosshair',
    });

    // The staged region, so it is obvious where the engine can answer.
    new google.maps.Rectangle({
      bounds, map: state.map, clickable: false,
      strokeColor: '#0e7490', strokeOpacity: 0.5, strokeWeight: 1.5, fillOpacity: 0,
    });

    enableDragToDraw();
    state.mapReady = true;
    arm(true);  // the first thing to do on arriving is draw a box
    $('mapfallback').hidden = true;
    $('legend').hidden = false;
    setStatus('');
  } catch (err) {
    mapFailed(`The map failed to start: ${err.message}`);
  }
};

function arm(on) {
  state.armed = on;
  $('draw').classList.toggle('active', on);
  if (state.mapReady) state.map.setOptions({ draggableCursor: on ? 'crosshair' : null });
  setStatus(on ? 'Drag a rectangle on the map.' : '');
}

/* Drag to rubber-band a rectangle, but only once armed: otherwise a drag pans the map as usual. */
function enableDragToDraw() {
  const map = state.map;

  map.addListener('mousedown', (ev) => {
    if (!state.armed || !ev.latLng) return;
    state.dragStart = ev.latLng;
    map.setOptions({ draggable: false });
    if (state.rect) { state.rect.setMap(null); state.rect = null; }
  });

  map.addListener('mousemove', (ev) => {
    if (!state.dragStart || !ev.latLng) return;
    drawBand(state.dragStart, ev.latLng);
  });

  const finish = (latLng) => {
    if (!state.dragStart) return;
    const start = state.dragStart;
    state.dragStart = null;
    map.setOptions({ draggable: true });
    if (!latLng) return;
    const bbox = toBbox(start, latLng);
    // A stray click is not a box. Below ~150 m a side there is nothing to analyse.
    if (Math.abs(bbox[2] - bbox[0]) < 0.0015 || Math.abs(bbox[3] - bbox[1]) < 0.0015) {
      if (state.rect) { state.rect.setMap(null); state.rect = null; }
      setStatus('That box was too small — drag a larger rectangle.');
      return;
    }
    arm(false);
    analyse(bbox);
  };

  map.addListener('mouseup', (ev) => finish(ev.latLng));
  // Releasing outside the map must not leave the map stuck undraggable.
  window.addEventListener('mouseup', () => {
    if (state.dragStart) { state.dragStart = null; map.setOptions({ draggable: true }); }
  });
}

const toBbox = (a, b) => [
  Math.min(a.lng(), b.lng()), Math.min(a.lat(), b.lat()),
  Math.max(a.lng(), b.lng()), Math.max(a.lat(), b.lat()),
];

function drawBand(a, b) {
  const [w, s, e, n] = toBbox(a, b);
  const bounds = { south: s, west: w, north: n, east: e };
  if (!state.rect) {
    state.rect = new google.maps.Rectangle({
      bounds, map: state.map, clickable: false,
      strokeColor: '#b45309', strokeWeight: 2, fillColor: '#b45309', fillOpacity: 0.06,
    });
  } else {
    state.rect.setBounds(bounds);
  }
}

function setRect(bbox) {
  if (!state.mapReady) return;
  const [w, s, e, n] = bbox;
  const bounds = { south: s, west: w, north: n, east: e };
  if (state.rect) state.rect.setMap(null);
  state.rect = new google.maps.Rectangle({
    bounds, map: state.map, clickable: false,
    strokeColor: '#b45309', strokeWeight: 2, fillColor: '#b45309', fillOpacity: 0.06,
  });
  state.map.fitBounds(bounds, 60);
}

async function analyse(bbox) {
  if (state.busy) return;
  state.busy = true;
  state.lastBbox = bbox;
  setStatus('Reading the terrain and working out where water goes…');
  try {
    const res = await fetch('/api/analyse', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ bbox, rainfall_mm: Number($('rain').value) }),
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

$('draw').addEventListener('click', () => {
  if (!state.mapReady) {
    setStatus('The map is not available — use one of the preset areas instead.', true);
    return;
  }
  arm(!state.armed);
});

$('clear').addEventListener('click', () => {
  if (state.rect) { state.rect.setMap(null); state.rect = null; }
  if (state.layer) { state.layer.setMap(null); state.layer = null; }
  state.lastBbox = null;
  $('results').hidden = true;
  arm(false);
});

boot().catch((e) => setStatus(`Could not start: ${e.message}`, true));
