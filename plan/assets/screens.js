/* Product-screen mockup for Kochi.
   The terrain is generated, but the hydrology is real: depressions are filled by relaxation,
   flow is accumulated downhill, and pooling spots are ranked by volume against inflow.
   Place names are real; every number is illustrative. */
(function () {
  'use strict';
  const { el, svg, seg, rng, clamp, tip, qdot, badge } = UI;
  const C = CITIES;
  const KOCHI = C.cities[0];

  const W = 760, H = 560, CS = 10, GW = Math.floor(W / CS), GH = Math.floor(H / CS), N = GW * GH;
  const idx = (x, y) => y * GW + x;

  /* ---------------- terrain ---------------- */
  function valueNoise(freq, seed) {
    const g = rng(seed), gw = Math.ceil(GW / freq) + 2, gh = Math.ceil(GH / freq) + 2;
    const grid = new Float32Array(gw * gh);
    for (let i = 0; i < grid.length; i++) grid[i] = g();
    const sm = (t) => t * t * (3 - 2 * t);
    return (x, y) => {
      const fx = x / freq, fy = y / freq, x0 = Math.floor(fx), y0 = Math.floor(fy);
      const tx = sm(fx - x0), ty = sm(fy - y0);
      const a = grid[y0 * gw + x0], b = grid[y0 * gw + x0 + 1];
      const c = grid[(y0 + 1) * gw + x0], d = grid[(y0 + 1) * gw + x0 + 1];
      return (a * (1 - tx) + b * tx) * (1 - ty) + (c * (1 - tx) + d * tx) * ty;
    };
  }
  const n1 = valueNoise(22, 7), n2 = valueNoise(9, 19), n3 = valueNoise(4, 33);

  // canal: drains to the south-west, towards the backwaters
  const CANAL = [[72, 6], [58, 16], [46, 22], [34, 31], [22, 38], [10, 47], [2, 53]];
  function distToPolyline(x, y, pts) {
    let best = 1e9;
    for (let i = 0; i < pts.length - 1; i++) {
      const [ax, ay] = pts[i], [bx, by] = pts[i + 1];
      const dx = bx - ax, dy = by - ay;
      const t = clamp(((x - ax) * dx + (y - ay) * dy) / (dx * dx + dy * dy), 0, 1);
      best = Math.min(best, Math.hypot(x - ax - t * dx, y - ay - t * dy));
    }
    return best;
  }

  // A few basins, so the terrain has believable catchments rather than only noise.
  const BASINS = [
    { x: 16, y: 12, r: 4.0, d: 1.2 }, { x: 41, y: 9, r: 3.6, d: 1.0 },
    { x: 29, y: 23, r: 4.4, d: 1.4 }, { x: 57, y: 18, r: 3.8, d: 1.1 },
    { x: 22, y: 41, r: 4.2, d: 1.3 }, { x: 49, y: 34, r: 4.6, d: 1.4 },
    { x: 65, y: 45, r: 3.6, d: 1.0 }, { x: 11, y: 30, r: 3.4, d: 0.9 },
    { x: 36, y: 48, r: 3.8, d: 1.1 }, { x: 62, y: 30, r: 3.4, d: 0.9 },
  ];
  const raw = new Float32Array(N);
  for (let y = 0; y < GH; y++) for (let x = 0; x < GW; x++) {
    const slope = 0.030 * x + 0.020 * (GH - y);              // high NE, low SW
    const texture = 1.2 * n1(x, y) + 0.6 * n2(x, y) + 0.25 * n3(x, y);
    const canal = 3.0 * Math.exp(-Math.pow(distToPolyline(x, y, CANAL) / 2.0, 2));
    let basin = 0;
    BASINS.forEach((b) => { basin += b.d * Math.exp(-(Math.pow(x - b.x, 2) + Math.pow(y - b.y, 2)) / (2 * b.r * b.r)); });
    raw[idx(x, y)] = slope + texture - canal - basin;
  }
  // roads sit slightly low, so water runs along them
  const ROADS = [
    { name: 'Seaport-Airport Rd', pts: [[4, 12], [24, 15], [44, 19], [64, 24], [75, 27]] },
    { name: 'NH-66 Bypass', pts: [[14, 2], [19, 16], [24, 30], [29, 44], [33, 55]] },
    { name: 'Edappally-Pukkattupady Rd', pts: [[2, 34], [18, 33], [36, 30], [54, 28], [72, 25]] },
    { name: 'Container Rd', pts: [[48, 2], [50, 14], [52, 27], [55, 40], [58, 54]] },
    { name: 'Thrikkakara Rd', pts: [[6, 48], [22, 47], [40, 45], [58, 44], [74, 42]] },
  ];
  ROADS.forEach((rd) => {
    for (let y = 0; y < GH; y++) for (let x = 0; x < GW; x++) {
      const d = distToPolyline(x, y, rd.pts);
      if (d < 1.4) raw[idx(x, y)] -= 0.42 * (1 - d / 1.4);
    }
  });

  /* ---------------- hydrology (real) ---------------- */
  const filled = new Float32Array(N);
  (function fillDepressions() {
    const BIG = 1e6;
    for (let i = 0; i < N; i++) filled[i] = BIG;
    for (let x = 0; x < GW; x++) { filled[idx(x, 0)] = raw[idx(x, 0)]; filled[idx(x, GH - 1)] = raw[idx(x, GH - 1)]; }
    for (let y = 0; y < GH; y++) { filled[idx(0, y)] = raw[idx(0, y)]; filled[idx(GW - 1, y)] = raw[idx(GW - 1, y)]; }
    const EPS = 1e-4;
    for (let pass = 0; pass < 220; pass++) {
      let changed = false;
      for (let y = 1; y < GH - 1; y++) for (let x = 1; x < GW - 1; x++) {
        const i = idx(x, y);
        let m = 1e9;
        for (let dy = -1; dy <= 1; dy++) for (let dx = -1; dx <= 1; dx++) {
          if (!dx && !dy) continue;
          const f = filled[idx(x + dx, y + dy)];
          if (f < m) m = f;
        }
        const v = Math.max(raw[i], m + EPS);
        if (v < filled[i] - 1e-6) { filled[i] = v; changed = true; }
      }
      if (!changed) break;
    }
  })();

  const depth = new Float32Array(N);
  for (let i = 0; i < N; i++) depth[i] = Math.max(0, filled[i] - raw[i]);

  const accum = new Float32Array(N).fill(1);
  (function flowAccumulate() {
    const order = Array.from({ length: N }, (_, i) => i).sort((a, b) => filled[b] - filled[a]);
    for (const i of order) {
      const x = i % GW, y = (i / GW) | 0;
      if (x === 0 || y === 0 || x === GW - 1 || y === GH - 1) continue;
      let best = -1, bestDrop = 0;
      for (let dy = -1; dy <= 1; dy++) for (let dx = -1; dx <= 1; dx++) {
        if (!dx && !dy) continue;
        const j = idx(x + dx, y + dy);
        const drop = (filled[i] - filled[j]) / Math.hypot(dx, dy);
        if (drop > bestDrop) { bestDrop = drop; best = j; }
      }
      if (best >= 0) accum[best] += accum[i];
    }
  })();

  /* ---------------- pooling spots ---------------- */
  const MIN_DEPTH = 0.12;
  const HA_PER_CELL = 0.9; // one 10 px cell is about 95 m across, so roughly 0.9 ha
  const ha = (cells) => cells * HA_PER_CELL;

  const spotOf = new Int16Array(N).fill(-1);
  const spots = [];
  (function findSpots() {
    for (let i = 0; i < N; i++) {
      if (depth[i] < MIN_DEPTH || spotOf[i] >= 0) continue;
      const id = spots.length, cells = [], queue = [i];
      spotOf[i] = id;
      while (queue.length) {
        const c = queue.pop(); cells.push(c);
        const x = c % GW, y = (c / GW) | 0;
        for (let dy = -1; dy <= 1; dy++) for (let dx = -1; dx <= 1; dx++) {
          const nx = x + dx, ny = y + dy;
          if (nx < 0 || ny < 0 || nx >= GW || ny >= GH) continue;
          const j = idx(nx, ny);
          if (spotOf[j] < 0 && depth[j] >= MIN_DEPTH) { spotOf[j] = id; queue.push(j); }
        }
      }
      if (cells.length < 3) { cells.forEach((c) => (spotOf[c] = -1)); continue; }
      let vol = 0, maxD = 0, inflow = 0, sx = 0, sy = 0, lowest = 1e9;
      cells.forEach((c) => {
        vol += depth[c]; maxD = Math.max(maxD, depth[c]); inflow = Math.max(inflow, accum[c]);
        sx += c % GW; sy += (c / GW) | 0; lowest = Math.min(lowest, raw[c]);
      });
      spots.push({ id, cells, vol, maxD, inflow, lowest, cx: sx / cells.length, cy: sy / cells.length, area: cells.length });
      spots[id].cells.forEach((c) => (spotOf[c] = id));
    }
    // Fill time is volume needed against water arriving: a small dip with a big catchment fills first.
    spots.forEach((s) => { s.fillTime = s.vol / Math.max(1, s.inflow); });
    const times = spots.map((s) => s.fillTime).sort((a, b) => a - b);
    const tMax = times[Math.floor(times.length * 0.92)] || 1;
    spots.forEach((s) => { s.fillNorm = clamp(s.fillTime / tMax, 0.02, 1.4); });
    // Score blends how much land drains in with how deep the dip is.
    const scores = spots.map((s) => Math.log(1 + s.inflow) * Math.sqrt(s.maxD));
    const sMax = Math.max(...scores);
    spots.forEach((s, i) => { s.score = scores[i] / sMax; });
    // Index is a rank within this terrain, not an absolute scale, which is what
    // "relative ranking" actually means and keeps the 1-5 bands meaningful.
    const ranked = spots.slice().sort((a, b) => b.score - a.score);
    ranked.forEach((s, i) => { s.index = clamp(5 - Math.floor((i / ranked.length) * 5), 1, 5); });
    spots.sort((a, b) => a.fillTime - b.fillTime);
  })();

  /* ---------------- places ---------------- */
  const WARDS = [
    { name: 'Edappally', x0: 2, y0: 2, x1: 38, y1: 26, pop: 5200 },
    { name: 'Palarivattom', x0: 38, y0: 2, x1: 75, y1: 26, pop: 4100 },
    { name: 'Kakkanad', x0: 38, y0: 26, x1: 75, y1: 55, pop: 6400 },
    { name: 'Vyttila', x0: 2, y0: 26, x1: 38, y1: 55, pop: 4800 },
  ];
  const wardAt = (x, y) => WARDS.find((w) => x >= w.x0 && x < w.x1 && y >= w.y0 && y < w.y1) || WARDS[0];
  const nearestRoad = (x, y) => {
    let best = null, bd = 1e9;
    ROADS.forEach((rd) => { const d = distToPolyline(x, y, rd.pts); if (d < bd) { bd = d; best = rd; } });
    return best.name;
  };
  spots.forEach((s) => { s.ward = wardAt(s.cx, s.cy).name; s.road = nearestRoad(s.cx, s.cy); });

  /* ---------------- state ---------------- */
  const S = {
    box: { x0: 6, y0: 6, x1: 56, y1: 44 },
    rain: 92, seq: 100, view: '2d', hazard: 'flood', official: false,
    tab: 'spots', selSpot: null, drawing: null, busy: false,
  };
  let playTimer = null;

  const inBox = (s) => s.cx >= S.box.x0 && s.cx <= S.box.x1 && s.cy >= S.box.y0 && s.cy <= S.box.y1;
  // Numbered within the drawn box, so "spot 1" always means first to collect here.
  function boxSpots() {
    const list = spots.filter(inBox).sort((a, b) => a.fillTime - b.fillTime);
    list.forEach((s, i) => { s.order = i + 1; });
    return list;
  }
  const boxAreaKm2 = () => {
    const w = (S.box.x1 - S.box.x0) * 0.095, h = (S.box.y1 - S.box.y0) * 0.095;
    return Math.max(0.2, w * h);
  };
  const fillFrac = (s) => clamp((S.seq / 100) / s.fillNorm, 0, 1);
  const activeSpots = () => boxSpots().filter((s) => fillFrac(s) > 0.12);

  /* ---------------- derived reporting ---------------- */
  function exposure() {
    const byWard = {};
    WARDS.forEach((w) => (byWard[w.name] = 0));
    const act = activeSpots();
    act.forEach((s) => {
      byWard[wardAt(s.cx, s.cy).name] += Math.round(s.area * fillFrac(s) * (s.index >= 4 ? 10 : 5));
    });
    const total = Object.values(byWard).reduce((a, b) => a + b, 0);
    return { byWard, total, spots: act.length };
  }

  const COST = { drain: 180000, pump: 420000, barricade: 60000 };
  const BUDGET = 50000000; // the 5 crore preparedness line
  function preparations() {
    const top = boxSpots().filter((s) => s.index >= 3).sort((a, b) => b.score - a.score).slice(0, 5);
    const out = []; let spent = 0;
    top.forEach((s, i) => {
      const type = i === 0 ? 'drain' : s.index >= 4 ? 'pump' : 'barricade';
      const cost = COST[type];
      if (spent + cost > BUDGET) return;
      spent += cost;
      out.push({
        type, spot: s, cost,
        what: type === 'drain' ? `Clear the outlet drain serving spot ${s.order}`
          : type === 'pump' ? `Pre-position a pump near spot ${s.order}`
            : `Barricade the low stretch at spot ${s.order}`,
        where: `${s.road}, ${s.ward}`,
        when: type === 'drain' ? 'before the season' : 'ahead of forecast rain',
      });
    });
    return { items: out, spent };
  }

  /* ---------------- rendering ---------------- */
  const DEPTH_COLORS = ['#cfe0fb', '#9dc1f5', '#6098ea', '#2f68cc', '#1a3f8f'];
  const INDEX_COLORS = { 1: '#9dc1f5', 2: '#6098ea', 3: '#2f68cc', 4: '#1a3f8f', 5: '#0f2557' };
  const px = (gx) => gx * CS, py = (gy) => gy * CS;

  let eMin = Infinity, eMax = -Infinity;
  for (let i = 0; i < N; i++) { eMin = Math.min(eMin, raw[i]); eMax = Math.max(eMax, raw[i]); }

  // Hillshade with light from the north-west, so slope and basins read as terrain.
  const shade = new Float32Array(N);
  (function hillshade() {
    const LX = -0.55, LY = -0.68, LZ = 0.49, Z = 2.2;
    for (let y = 0; y < GH; y++) for (let x = 0; x < GW; x++) {
      const xm = Math.max(0, x - 1), xp = Math.min(GW - 1, x + 1);
      const ym = Math.max(0, y - 1), yp = Math.min(GH - 1, y + 1);
      const dzdx = (raw[idx(xp, y)] - raw[idx(xm, y)]) * Z;
      const dzdy = (raw[idx(x, yp)] - raw[idx(x, ym)]) * Z;
      const len = Math.sqrt(dzdx * dzdx + dzdy * dzdy + 1);
      shade[idx(x, y)] = clamp((-dzdx * LX - dzdy * LY + LZ) / len, 0, 1);
    }
  })();

  function terrainShade(i) {
    const t = clamp((raw[i] - eMin) / (eMax - eMin), 0, 1);
    const lit = 0.64 + 0.44 * shade[i];
    const rr = clamp((214 - t * 36) * lit, 0, 255);
    const gg = clamp((216 - t * 30) * lit, 0, 255);
    const bb = clamp((208 - t * 44) * lit, 0, 255);
    return `rgb(${rr | 0},${gg | 0},${bb | 0})`;
  }

  function project(gx, gy, lift) {
    if (S.view === '2d') return [px(gx), py(gy)];
    const cy = H / 2;
    return [px(gx), cy + (py(gy) - cy) * 0.70 + 40 - (lift || 0)];
  }

  function drawMap() {
    const s = document.getElementById('map');
    s.innerHTML = '';
    const gTerrain = svg('g'), gWater = svg('g'), gLines = svg('g'), gMark = svg('g'), gBox = svg('g');
    s.append(gTerrain, gWater, gLines, gMark, gBox);
    const cellH = S.view === '2d' ? CS : CS * 0.72;

    for (let y = 0; y < GH; y++) for (let x = 0; x < GW; x++) {
      const i = idx(x, y);
      const [sx, sy] = project(x, y, 0);
      gTerrain.appendChild(svg('rect', { x: sx.toFixed(1), y: sy.toFixed(1), width: CS, height: cellH,
        fill: terrainShade(i), 'shape-rendering': 'crispEdges' }));
    }

    const poly = (pts, stroke, w) => {
      let d = '';
      pts.forEach(([gx, gy], i) => { const [sx, sy] = project(gx, gy, 0); d += `${i ? 'L' : 'M'}${sx + CS / 2} ${sy + CS / 2} `; });
      gLines.appendChild(svg('path', { d, fill: 'none', stroke, 'stroke-width': w, 'stroke-linecap': 'round', opacity: 0.95 }));
    };
    poly(CANAL, '#3f74cc', 5);
    ROADS.forEach((rd) => poly(rd.pts, '#f4f6f8', 2.6));

    if (S.official) {
      for (let y = 0; y < GH; y++) for (let x = 0; x < GW; x++) {
        const i = idx(x, y);
        const low = clamp(1 - (raw[i] - eMin) / (eMax - eMin) * 1.6, 0, 1);
        if (low > 0.55) {
          const [sx, sy] = project(x, y, 0);
          gWater.appendChild(svg('rect', { x: sx, y: sy, width: CS, height: cellH,
            fill: '#0d9466', opacity: (low - 0.55) * 0.9, 'shape-rendering': 'crispEdges' }));
        }
      }
    }

    const frac = new Map();
    boxSpots().forEach((s2) => frac.set(s2.id, fillFrac(s2)));
    for (let y = 0; y < GH; y++) for (let x = 0; x < GW; x++) {
      const i = idx(x, y), sid = spotOf[i];
      if (sid < 0) continue;
      const f = frac.get(sid);
      if (!f || f <= 0.02) continue;
      const d = depth[i] * f;
      if (d < 0.03) continue;
      const band = clamp(Math.floor(d / 0.34), 0, 4);
      const lift = S.view === '3d' ? d * 24 : 0;
      const [sx, sy] = project(x, y, lift);
      if (S.view === '3d' && lift > 1) {
        gWater.appendChild(svg('rect', { x: sx, y: sy + cellH, width: CS, height: Math.min(lift, 38),
          fill: DEPTH_COLORS[band], opacity: 0.4, 'shape-rendering': 'crispEdges' }));
      }
      gWater.appendChild(svg('rect', { x: sx, y: sy, width: CS, height: cellH,
        fill: DEPTH_COLORS[band], opacity: 0.92, 'shape-rendering': 'crispEdges' }));
    }

    boxSpots().forEach((s2) => {
      if (fillFrac(s2) <= 0.12) return;
      const lift = S.view === '3d' ? s2.maxD * 24 : 0;
      const [sx, sy] = project(s2.cx, s2.cy, lift);
      const g = svg('g', { style: 'cursor:pointer' });
      g.appendChild(svg('circle', { cx: sx + CS / 2, cy: sy + CS / 2, r: s2.id === S.selSpot ? 13 : 10,
        fill: INDEX_COLORS[s2.index], stroke: '#fff', 'stroke-width': 2 }));
      g.appendChild(svg('text', { x: sx + CS / 2, y: sy + CS / 2 + 4, 'text-anchor': 'middle',
        'font-size': 11, 'font-weight': 700, fill: '#fff', text: String(s2.order) }));
      g.addEventListener('click', () => { S.selSpot = S.selSpot === s2.id ? null : s2.id; S.tab = 'spots'; render(); });
      tip(g, () => `<b>Spot ${s2.order} &mdash; ${s2.ward}</b><br>${s2.road}<br>pooling index ${s2.index} of 5<br>` +
        `${ha(s2.area).toFixed(1)} ha collects water<br>${Math.round(ha(s2.inflow))} ha drains into it<br>` +
        `<span style="opacity:.75">relative ranking, not a depth</span>`);
      gMark.appendChild(g);
    });

    WARDS.forEach((w) => {
      const [sx, sy] = project((w.x0 + w.x1) / 2, w.y0 + 2, 0);
      gMark.appendChild(svg('text', { x: sx, y: sy, 'text-anchor': 'middle', 'font-size': 11, fill: '#3f4852',
        stroke: '#eef1f5', 'stroke-width': 3, 'paint-order': 'stroke', text: w.name }));
    });

    const [bx0, by0] = project(S.box.x0, S.box.y0, 0);
    const [bx1, by1] = project(S.box.x1, S.box.y1, 0);
    gBox.appendChild(svg('path', {
      d: `M0 0 H${W} V${H} H0 Z M${bx0} ${by0} H${bx1} V${by1} H${bx0} Z`,
      'fill-rule': 'evenodd', fill: 'rgba(16,20,26,.42)',
    }));
    gBox.appendChild(svg('rect', { x: bx0, y: by0, width: bx1 - bx0, height: by1 - by0,
      fill: 'none', stroke: '#fff', 'stroke-width': 2, 'stroke-dasharray': '7 4' }));
    [[bx0, by0], [bx1, by0], [bx0, by1], [bx1, by1]].forEach(([hx, hy]) =>
      gBox.appendChild(svg('rect', { x: hx - 4, y: hy - 4, width: 8, height: 8, fill: '#fff', stroke: '#1b1f24' })));
    gBox.appendChild(svg('text', { x: bx0 + 6, y: by0 - 7, 'font-size': 11.5, 'font-weight': 700, fill: '#fff',
      text: `${boxAreaKm2().toFixed(1)} km2 . ${S.view === '2d' ? 'drag to redraw' : 'switch to 2D to redraw'}` }));

    if (S.drawing) {
      const d = S.drawing;
      gBox.appendChild(svg('rect', { x: Math.min(d.x0, d.x1) * CS, y: Math.min(d.y0, d.y1) * CS,
        width: Math.abs(d.x1 - d.x0) * CS, height: Math.abs(d.y1 - d.y0) * CS,
        fill: 'rgba(96,152,234,.25)', stroke: '#2563eb', 'stroke-width': 2 }));
    }
  }

  /* drag to draw a new box */
  function wireDrawing() {
    const s = document.getElementById('map');
    const toGrid = (ev) => {
      const r2 = s.getBoundingClientRect();
      return {
        x: clamp(((ev.clientX - r2.left) / r2.width) * GW, 0, GW - 1),
        y: clamp(((ev.clientY - r2.top) / r2.height) * GH, 0, GH - 1),
      };
    };
    s.addEventListener('mousedown', (ev) => {
      if (S.view === '3d') return;
      const p = toGrid(ev);
      S.drawing = { x0: p.x, y0: p.y, x1: p.x, y1: p.y };
    });
    s.addEventListener('mousemove', (ev) => {
      if (!S.drawing) return;
      const p = toGrid(ev);
      S.drawing.x1 = p.x; S.drawing.y1 = p.y;
      drawMap();
    });
    window.addEventListener('mouseup', () => {
      if (!S.drawing) return;
      const d = S.drawing; S.drawing = null;
      if (Math.abs(d.x1 - d.x0) > 5 && Math.abs(d.y1 - d.y0) > 5) {
        S.box = { x0: Math.min(d.x0, d.x1), y0: Math.min(d.y0, d.y1), x1: Math.max(d.x0, d.x1), y1: Math.max(d.y0, d.y1) };
        S.selSpot = null;
        S.busy = true; render();
        setTimeout(() => { S.busy = false; render(); }, 600);
      } else render();
    });
  }

  /* ---------------- panels ---------------- */
  function chrome() {
    const c = document.getElementById('chrome');
    c.innerHTML = '';
    const qs = Object.values(KOCHI.layers).map((x) => x.q);
    const cnt = (q) => qs.filter((x) => x === q).length;
    c.append(
      el('span', { class: 'logo', text: 'Before It Hits' }),
      badge('Kochi, Kerala', 'core'),
      seg([['flood', 'Flood'], ['heat', 'Heat']], S.hazard, (v) => { S.hazard = v; render(); }),
      seg([['2d', '2D'], ['3d', '3D']], S.view, (v) => { S.view = v; render(); }),
      el('span', { class: 'spacer' }),
      el('span', { class: 'conf' }, ['Data:', qdot('good'), cnt('good'), qdot('ok'), cnt('ok'), qdot('weak'), cnt('weak'), qdot('gap'), cnt('gap')]),
    );
  }

  function mapHead() {
    const h = document.getElementById('maphead');
    h.innerHTML = '';
    h.append(
      el('b', { text: S.busy ? 'Analysing the box...' : `${boxSpots().length} pooling spots in this box` }),
      el('span', { class: 'muted', text: `${boxAreaKm2().toFixed(1)} km2 · DeltaDTM 30 m to a 10 m working grid` }),
      el('span', { style: 'flex:1' }),
      el('label', { class: 'small', style: 'display:flex;gap:5px;align-items:center;cursor:pointer' }, [
        el('input', { type: 'checkbox', checked: S.official ? '' : null, onchange: (e) => { S.official = e.target.checked; render(); } }),
        'Official KSDMA layer',
      ]),
    );
    document.getElementById('seqVal').textContent = S.seq + '%';
    document.getElementById('seq').value = S.seq;
    document.getElementById('seqLab').textContent =
      `Fill sequence — ${S.seq}% of the rain delivered · ${activeSpots().length} of ${boxSpots().length} spots collecting. ` +
      'This is an order, not a clock: arrival times would need drainage data that is not public.';
  }

  function legend() {
    const lg = document.getElementById('legend');
    lg.innerHTML = '';
    const item = (c, t) => el('span', null, [el('i', { class: 'sw', style: `background:${c}` }), t]);
    DEPTH_COLORS.forEach((c, i) => lg.append(item(c, i === 0 ? 'collects less' : i === 4 ? 'collects more' : '')));
    lg.append(el('span', { class: 'tiny muted', text: 'relative pooling, not measured depth' }), item('#3f74cc', 'canal'));
    if (S.official) lg.append(item('#0d9466', 'official hazard layer'));
  }

  function conversation() {
    const L = document.getElementById('left');
    L.innerHTML = '';
    const list = boxSpots();
    const prep = preparations();
    L.appendChild(el('div', { class: 'tiny muted', text: 'Ward officer · Kochi Corporation · 14:05' }));
    L.appendChild(el('div', { class: 'bubble' }, [
      'ഈ ഭാഗത്ത് ആദ്യം വെള്ളം കെട്ടിനിൽക്കുന്നത് എവിടെയാണ്?',
      el('span', { class: 'tr', text: 'Which parts of this area collect water first?' }),
    ]));
    L.appendChild(el('div', { class: 'steps-mini' }, [
      ['Area', `${boxAreaKm2().toFixed(1)} km2 · DeltaDTM · coastal lowland`],
      ['Rainfall', `${S.rain} mm in 2 h, about a 1-in-25-year event`],
      ['Terrain', `filled depressions, routed flow, ranked ${list.length} spots`],
      ['Official', 'KSDMA 1-in-25-year layer available to compare'],
      ['Planner', `${prep.items.length} preparation${prep.items.length === 1 ? '' : 's'} within the 5 crore line`],
      ['Verifier', 'all claims traced · no depth claimed from our model'],
    ].map(([a, b]) => el('div', null, [el('span', { text: '✓' }), el('span', null, [el('b', { text: a + ' ' }), el('span', { class: 'muted', text: b })])]))));
    if (list.length) {
      const first = list[0];
      const high = list.filter((s) => s.index >= 4).length;
      L.appendChild(el('div', { class: 'answer' }, [
        `Water collects first at spot ${first.order}, on ${first.road} in ${first.ward} — about `,
        el('b', { text: `${Math.round(ha(first.inflow))} hectares` }),
        ' drain into it, and it sits lower than everything around it. ',
        high ? `${high} of the ${list.length} spots here rank 4 or 5 out of 5. ` : `None of the ${list.length} spots here reach the top two ranks. `,
        'The highest-value preparation is clearing the outlet drain at the worst of them. ',
        el('span', { class: 'muted', text: 'Depths come only from the official KSDMA layer. Our own model ranks places; it does not measure them.' }),
      ]));
    } else {
      L.appendChild(el('div', { class: 'answer' }, 'No pooling spots in this box. The ground here drains to the canal without trapping water.'));
    }
    L.appendChild(el('div', { class: 'inputbox', text: 'Ask a follow-up... "what if it is twice the rain?", "only the part near the canal"' }));
  }

  function panels() {
    const R = document.getElementById('right');
    R.innerHTML = '';
    const tabs = [['spots', 'Pooling'], ['who', 'Who'], ['prepare', 'Prepare'], ['news', 'Reporting'], ['gaps', 'Gaps'], ['brief', 'Brief']];
    R.appendChild(el('div', { class: 'tabs' }, tabs.map(([k, t]) => el('button', { class: S.tab === k ? 'on' : null, text: t, onclick: () => { S.tab = k; panels(); } }))));
    R.appendChild(el('div', { class: 'caveat', html:
      '<b>30 m terrain screening, not a flood forecast.</b> Shows which places collect rainwater <i>relative to their surroundings</i>. No storm drains, canals or tides are modelled.' }));

    if (S.hazard === 'heat') {
      R.appendChild(el('div', { class: 'note warn', text: 'Heat is reported by area only: which wards run hotter than the city median, from satellite surface temperature. Indoor temperature per home was dropped, because it needs roof, floor and ventilation data that does not exist here.' }));
      const base = [4.1, 2.8, 3.6, 1.9];
      R.appendChild(el('h4', { style: 'margin-top:12px', text: 'Hotter than the city median' }));
      WARDS.forEach((w, i) => R.appendChild(el('div', { class: 'hbar' }, [
        el('span', { text: w.name }),
        el('div', { class: 'tr' }, el('div', { class: 'fl', style: `width:${(base[i] / 4.5) * 100}%;background:var(--heat)` })),
        el('span', { class: 'mono', text: '+' + base[i].toFixed(1) + '°' }),
      ])));
      return;
    }

    if (S.tab === 'spots') {
      const list = boxSpots();
      R.appendChild(el('div', { class: 'small muted', style: 'margin-bottom:8px', text: `${list.length} spots, in the order they start collecting.` }));
      list.forEach((s2) => R.appendChild(el('div', { class: 'spot' + (s2.id === S.selSpot ? ' sel' : ''), onclick: () => { S.selSpot = S.selSpot === s2.id ? null : s2.id; render(); } }, [
        el('div', { class: 'rank', style: `background:${INDEX_COLORS[s2.index]}`, text: String(s2.order) }),
        el('div', null, [
          el('b', { text: `${s2.ward} — ${s2.road}` }),
          el('div', { class: 'meta', text: `${Math.round(ha(s2.inflow))} ha drains in · ${ha(s2.area).toFixed(1)} ha collects · fills ${s2.order === 1 ? 'first' : s2.order <= 3 ? 'early' : 'later'}` }),
        ]),
        el('span', { class: 'mono small', text: s2.index + '/5' }),
      ])));
    }

    if (S.tab === 'who') {
      const ex = exposure();
      R.appendChild(el('div', { class: 'kpis' }, [
        el('div', { class: 'kpi' }, [el('div', { class: 'v', text: String(ex.total) }), el('div', { class: 'l', text: 'people in affected areas (modelled)' })]),
        el('div', { class: 'kpi' }, [el('div', { class: 'v', text: String(ex.spots) }), el('div', { class: 'l', text: 'spots currently collecting' })]),
      ]));
      R.appendChild(el('h4', { style: 'margin-top:14px', text: 'By ward' }));
      const mx = Math.max(1, ...Object.values(ex.byWard));
      WARDS.forEach((w) => R.appendChild(el('div', { class: 'hbar' }, [
        el('span', { text: w.name }),
        el('div', { class: 'tr' }, el('div', { class: 'fl', style: `width:${(ex.byWard[w.name] / mx) * 100}%;background:var(--water)` })),
        el('span', { class: 'mono', text: String(ex.byWard[w.name]) }),
      ])));
      R.appendChild(el('div', { class: 'note warn small', style: 'margin-top:12px', text: 'A modelled distribution fitted to published ward totals, not a household register. Reported by ward, never by building.' }));
      R.appendChild(el('h4', { style: 'margin-top:14px', text: 'Routes crossing pooling spots' }));
      const roads = [...new Set(activeSpots().filter((s2) => s2.index >= 3).map((s2) => s2.road))];
      R.appendChild(roads.length
        ? el('ul', { class: 'small', style: 'margin:0;padding-left:18px' }, roads.map((n) => el('li', { text: n })))
        : el('p', { class: 'small muted', text: 'None at this point in the fill sequence.' }));
    }

    if (S.tab === 'prepare') {
      const prep = preparations();
      R.appendChild(el('h4', { text: 'What Kochi has to work with' }));
      R.appendChild(el('ul', { class: 'small', style: 'margin:0 0 10px;padding-left:18px' }, KOCHI.inventory.slice(0, 3).map((x) => el('li', { text: x }))));
      R.appendChild(el('div', { class: 'small', text: `Budget: ${(prep.spent / 100000).toFixed(1)} lakh of the 5 crore preparedness line` }));
      R.appendChild(el('div', { class: 'budget' }, el('div', { style: `width:${(prep.spent / BUDGET) * 100}%` })));
      R.appendChild(el('div', { class: 'tiny muted', style: 'margin:4px 0 10px', text: 'Preparation only, never dispatch or evacuation routing.' }));
      prep.items.forEach((p, i) => R.appendChild(el('div', { class: 'prep' }, [
        el('span', { class: 'n', text: String(i + 1).padStart(2, '0') }),
        el('div', null, [el('b', { text: p.what }), el('div', { class: 'meta', text: `${p.where} · ${p.when}` })]),
        el('span', { class: 'c', text: (p.cost / 1000) + 'k' }),
      ])));
      R.appendChild(el('div', { class: 'prep bad' }, [
        el('span', { class: 'n', text: '✕' }),
        el('div', null, [el('b', { text: 'Raise the road at the worst spot' }), el('div', { class: 'meta', text: 'Capital work, outside the preparedness budget line and needing a separate sanction' })]),
        el('span'),
      ]));
    }

    if (S.tab === 'news') {
      R.appendChild(el('div', { class: 'grounded' }, [
        el('b', { text: 'Live — what is being reported now' }),
        el('div', { style: 'margin-top:5px', text: 'Heavy rain warning for Ernakulam over the next 48 hours; waterlogging reported at Edappally junction this morning.' }),
        el('div', { class: 'tiny', style: 'margin-top:6px;opacity:.8', text: 'Grounded search result, displayed only and never stored or scored. Sources shown inline in the product.' }),
      ]));
      R.appendChild(el('h4', { style: 'margin-top:14px', text: 'Stored corpus — 3 clusters from 11 articles' }));
      [
        ['Waterlogging at Edappally and Palarivattom junctions', '4 distinct sources · first seen 6 h ago'],
        ['Corporation begins pre-monsoon canal desilting', '3 distinct sources · first seen 2 days ago'],
        ['IMD orange alert for Ernakulam district', '2 distinct sources · first seen 11 h ago'],
      ].forEach(([h, s2]) => R.appendChild(el('div', { class: 'story' }, [el('b', { text: h }), el('div', { class: 'src', text: s2 })])));
      R.appendChild(el('div', { class: 'story rejected' }, [
        el('b', { text: 'Rejected — "Kochi submerged" photo set' }),
        el('div', { class: 'src', text: 'First seen August 2018 and resurfacing now. Recycled old footage is the documented failure mode in Indian flood coverage, so anything first seen before the event window is dropped automatically.' }),
      ]));
    }

    if (S.tab === 'gaps') {
      R.appendChild(el('p', { class: 'small', text: 'What we do not know well here, shown on screen rather than buried in an appendix.' }));
      C.layerRows.forEach(([id, label]) => {
        const x = KOCHI.layers[id];
        if (!x || x.q === 'good') return;
        R.appendChild(el('div', { class: 'gap' }, [qdot(x.q), el('div', null, [
          el('b', { text: label }), el('div', { class: 's', text: x.src }),
          el('div', { class: 's', style: 'color:var(--ink)', text: '→ ' + x.proc }),
        ])]));
      });
    }

    if (S.tab === 'brief') {
      const prep = preparations(), ex = exposure(), list = boxSpots();
      const top = list.slice().sort((a, b) => b.score - a.score)[0];
      const worst = WARDS.map((w) => [w.name, ex.byWard[w.name]]).sort((a, b) => b[1] - a[1])[0][0];
      const lines = [
        `PREPAREDNESS BRIEF — ${boxAreaKm2().toFixed(1)} km2 around Edappally, Kochi`,
        `Rainfall considered: ${S.rain} mm in 2 h (about a 1-in-25-year event).`,
        '',
        `${list.length} pooling spots identified; ${list.filter((s2) => s2.index >= 4).length} rank 4 or 5 out of 5.`,
        top ? `First to collect: ${top.ward}, near ${top.road}, with roughly ${Math.round(ha(top.inflow))} ha draining into it.` : '',
        `Modelled people in affected areas: about ${ex.total}, concentrated in ${worst}.`,
        '',
        `Preparations within the 5 crore line (${(prep.spent / 100000).toFixed(1)} lakh used):`,
        ...prep.items.map((p, i) => `  ${i + 1}. ${p.what} — ${p.where}, ${p.when}`),
        '',
        'Not possible: raising the road at the worst spot. Capital work, outside this budget line.',
        '',
        'Basis and limits: 30 m terrain screening. A relative ranking of where water collects, not a forecast and not a depth.',
        'Storm drains are not modelled because no public data exists, so pooling is overstated where drains work.',
        'Official depth figures, where quoted, come from the KSDMA hazard layer and not from this model.',
      ].filter(Boolean);
      R.appendChild(el('div', { class: 'chips', style: 'margin-bottom:8px' }, [badge('all claims traced', 'act'), badge('draft only, not sent')]));
      R.appendChild(el('div', { class: 'msg' }, [
        el('div', { class: 'head' }, [el('span', { text: 'Officer brief' }), el('span', { text: 'English' })]),
        el('pre', { style: 'white-space:pre-wrap;font:12px/1.5 var(--mono);margin:0', text: lines.join('\n') }),
      ]));
      R.appendChild(el('div', { class: 'msg' }, [
        el('div', { class: 'head' }, [el('span', { text: 'Area message, draft for WhatsApp' }), el('span', { text: 'Malayalam' })]),
        el('div', { class: 'local', text: 'നിങ്ങളുടെ പ്രദേശത്ത് മഴവെള്ളം ആദ്യം കെട്ടിനിൽക്കുന്ന സ്ഥലങ്ങളിൽ ഒന്നാണിത്.' }),
        el('div', { class: 'en', text: 'This is one of the first places rainwater collects in your area. The Corporation is clearing the drain ahead of heavy rain.' }),
        el('div', { class: 'tiny muted', style: 'margin-top:6px', text: 'Mockup text, needs a native-speaker check. In the product the Narrator writes it and the Verifier checks every claim.' }),
      ]));
    }
  }

  function extras() {
    const t = document.getElementById('ondemand');
    t.innerHTML = '';
    t.appendChild(el('tr', null, ['Step', 'What happens', 'Budget'].map((h) => el('th', { text: h }))));
    [
      ['Read the elevation', 'Windowed read of just this box from pre-staged cloud-optimised tiles, never whole files', '2-4 s'],
      ['Resample', `30 m source onto a 10 m working grid, about ${Math.round(boxAreaKm2() * 100)} x 100 cells`, '< 0.3 s'],
      ['Burn in buildings and roads', 'Buildings become walls; roads become shallow trenches, because water runs along them', '< 0.2 s'],
      ['Fill depressions', 'The physics: where the ground traps water. Published benchmark fills a million cells in under a second', '< 1 s'],
      ['Rank by what arrives', 'Weight each spot by the catchment draining into it, so a dip nothing feeds ranks low', '< 0.2 s'],
    ].forEach(([a, b, c]) => t.appendChild(el('tr', null, [
      el('td', { class: 'rowhead', text: a }), el('td', { class: 'small muted', text: b }), el('td', { class: 'mono small', text: c }),
    ])));

    const sc = document.getElementById('scores');
    sc.innerHTML = '';
    sc.appendChild(el('div', { class: 'scorerow' }, [el('b', { text: 'Run' }), el('b', { text: 'Detected' }), el('b', { text: 'False alarms' }), el('b', { text: 'Score' })]));
    [
      ['Real replay, 28 May 2024', '7 of 9 localities', '2', '0.64', true],
      ['Synthetic, 20 seeded storms', 'median 78%', 'median 21%', '0.65', false],
    ].forEach(([a, b, c, d, real]) => sc.appendChild(el('div', { class: 'scorerow' }, [
      el('span', null, [a + ' ', real ? badge('real', 'act') : badge('simulated')]),
      el('span', { class: 'mono', text: b }), el('span', { class: 'mono', text: c }), el('span', { class: 'mono', text: d }),
    ])));
  }

  /* ---------------- loop ---------------- */
  function render() { chrome(); drawMap(); mapHead(); legend(); conversation(); panels(); extras(); }

  document.getElementById('seq').oninput = (e) => { S.seq = +e.target.value; drawMap(); mapHead(); panels(); };
  document.getElementById('playBtn').onclick = () => {
    const btn = document.getElementById('playBtn');
    if (playTimer) { clearInterval(playTimer); playTimer = null; btn.textContent = '▶'; return; }
    btn.textContent = '❚❚';
    if (S.seq >= 100) S.seq = 0;
    playTimer = setInterval(() => {
      S.seq = Math.min(100, S.seq + 4);
      drawMap(); mapHead(); panels();
      if (S.seq >= 100) { clearInterval(playTimer); playTimer = null; btn.textContent = '▶'; }
    }, 120);
  };

  render();
  wireDrawing();
  window.__mock = { spots, raw, depth, accum, S };
})();
