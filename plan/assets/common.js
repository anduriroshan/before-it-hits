(function (root) {
  'use strict';

  const PAGES = [
    ['index.html', 'Overview'],
    ['features.html', 'Features'],
    ['layers.html', 'Layers & elevation'],
    ['maps.html', 'Maps per feature'],
    ['cities.html', 'Cities & data'],
    ['agents.html', 'AI agents'],
    ['architecture.html', 'Architecture'],
    ['screens.html', 'Product screens'],
  ];

  function el(tag, attrs, kids) {
    const n = document.createElement(tag);
    if (attrs) for (const k in attrs) {
      const v = attrs[k];
      if (v == null || v === false) continue;
      if (k === 'class') n.className = v;
      else if (k === 'html') n.innerHTML = v;
      else if (k === 'text') n.textContent = v;
      else if (k.startsWith('on') && typeof v === 'function') n.addEventListener(k.slice(2), v);
      else n.setAttribute(k, v);
    }
    append(n, kids);
    return n;
  }

  function append(n, kids) {
    if (kids == null) return n;
    (Array.isArray(kids) ? kids : [kids]).forEach((k) => {
      if (k == null || k === false) return;
      n.appendChild(typeof k === 'string' || typeof k === 'number' ? document.createTextNode(String(k)) : k);
    });
    return n;
  }

  const SVGNS = 'http://www.w3.org/2000/svg';
  function svg(tag, attrs, kids) {
    const n = document.createElementNS(SVGNS, tag);
    if (attrs) for (const k in attrs) {
      const v = attrs[k];
      if (v == null || v === false) continue;
      if (k === 'text') n.textContent = v;
      else if (k.startsWith('on') && typeof v === 'function') n.addEventListener(k.slice(2), v);
      else n.setAttribute(k, v);
    }
    if (kids) (Array.isArray(kids) ? kids : [kids]).forEach((k) => k && n.appendChild(k));
    return n;
  }

  function theme() {
    const KEY = 'biht-theme';
    const get = () => document.documentElement.getAttribute('data-theme') === 'dark' ? 'dark' : 'light';
    const set = (t) => {
      document.documentElement.setAttribute('data-theme', t);
      try { localStorage.setItem(KEY, t); } catch (e) { /* ignore (private browsing) */ }
    };
    const btn = el('button', { class: 'theme-toggle', title: 'Toggle dark mode', text: get() === 'dark' ? '☀' : '☾' });
    btn.addEventListener('click', () => {
      const next = get() === 'dark' ? 'light' : 'dark';
      set(next);
      btn.textContent = next === 'dark' ? '☀' : '☾';
    });
    return btn;
  }

  function topbar() {
    const here = (location.pathname.split('/').pop() || 'index.html').toLowerCase();
    const nav = el('nav', { class: 'nav' }, PAGES.map(([href, label]) =>
      el('a', { href, class: href === here ? 'on' : null, text: label })));
    const bar = el('header', { class: 'topbar' }, el('div', { class: 'topbar-inner' }, [
      el('a', { class: 'brand', href: 'index.html', html: 'Before It Hits <small>build plan · mockups</small>' }),
      nav,
      theme(),
    ]));
    document.body.prepend(bar);
    document.body.appendChild(el('div', { class: 'footer', html:
      'Planning mockups, 10 Oct 2026. Nothing here is the product: geometry in screen mockups is schematic and numbers are illustrative. ' +
      'Data-source claims are tagged <span class="q good"></span>verified / <span class="q weak"></span>unverified where it matters.' }));
  }

  let tipEl = null;
  function tip(target, html) {
    if (!tipEl) { tipEl = el('div', { class: 'tip' }); document.body.appendChild(tipEl); }
    target.addEventListener('mousemove', (e) => {
      tipEl.innerHTML = typeof html === 'function' ? html() : html;
      const w = tipEl.offsetWidth, h = tipEl.offsetHeight;
      let x = e.clientX + 14, y = e.clientY + 14;
      if (x + w > innerWidth - 8) x = e.clientX - w - 14;
      if (y + h > innerHeight - 8) y = e.clientY - h - 14;
      tipEl.style.left = x + 'px'; tipEl.style.top = y + 'px';
      tipEl.classList.add('show');
    });
    target.addEventListener('mouseleave', () => tipEl.classList.remove('show'));
  }

  function seg(options, value, onChange) {
    const wrap = el('div', { class: 'seg' });
    const draw = () => {
      wrap.innerHTML = '';
      options.forEach(([v, label]) => wrap.appendChild(el('button', {
        class: v === value ? 'on' : null, text: label,
        onclick: () => { value = v; draw(); onChange(v); },
      })));
    };
    draw();
    return wrap;
  }

  function rng(seed) {
    let a = seed >>> 0;
    return function () {
      a |= 0; a = (a + 0x6D2B79F5) | 0;
      let t = Math.imul(a ^ (a >>> 15), 1 | a);
      t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
  }

  const byId = (list) => Object.fromEntries(list.map((x) => [x.id, x]));
  const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
  const lerp = (a, b, t) => a + (b - a) * t;

  function qdot(q) { return el('span', { class: 'q ' + (q || 'na') }); }
  function badge(text, cls) { return el('span', { class: 'badge ' + (cls || ''), text }); }

  root.UI = { PAGES, el, append, svg, topbar, tip, seg, rng, byId, clamp, lerp, qdot, badge };
  document.addEventListener('DOMContentLoaded', topbar);
})(window);
