/* Kochi specifics. Researched 10–11 Oct 2026.
   q: good | ok | weak | gap | na.  "[u]" = not confirmed at source. */
window.CITIES = (function () {
  'use strict';

  const layerRows = [
    ['L1', 'Terrain (DTM)'], ['L2', 'Drainage & water'], ['L18', 'Official hazard maps'],
    ['L4', 'Buildings'], ['L7', 'Roads'], ['L8', 'Land cover'], ['L9', 'Surface temperature'],
    ['L10', 'Admin areas & census'], ['L11', 'Population grid'], ['L13', 'Facilities'],
    ['L14', 'City capabilities'], ['L15', 'Rainfall forecast'], ['L16', 'Observations & reporting'],
  ];

  const cities = [
    {
      id: 'koc', name: 'Kochi', country: 'Kerala, India', depth: 'deep',
      area: 'Kochi Corporation and the Edappally–Kakkanad corridor',
      languages: ['Malayalam', 'English'], app: 'WhatsApp',
      profiles: ['coastal-low', 'validated', 'heat-area'], profilesMaybe: [],
      character:
        'Low and flat — most of the city sits between 0 and 7 m above sea level, laced with canals that drain to the backwaters. ' +
        'Rain that cannot get into a canal sits in whatever local dip it lands in. That is why terrain screening is the right tool here, ' +
        'and also why timing is not: without the drainage network, we can say where water gathers but not when it clears.',
      heat:
        'Humid tropical. Area-level only, from hot-season satellite surface temperature expressed relative to the city median. ' +
        'Cloud cover limits the number of clear scenes, so the composite uses a longer window.',
      inventory: [
        '₹5 crore per city corporation for flood preparedness (2026 SDRF rules)',
        '₹50,000 per corporation ward for debris clearance',
        'Operation Breakthrough — the named canal desilting programme, under Kerala High Court supervision',
        'Perandoor, Thevara–Perandoor and Mullassery canals are the repeatedly named problem assets',
        'Wards went 74 → 76 in the 2025 delimitation',
      ],
      replay: [
        '28 May 2024 cloudburst — about 98–100 mm in 1–1.5 h at the CUSAT gauge; Kalamassery ~150 mm/3 h. Named: Edappally, Kakkanad, Infopark, Palarivattom, Kaloor, Vyttila, Kadavanthra, Kalamassery',
        'November 2025 evening storm — water into shops on MG Road; Jos Junction, Woodlands, Thammanam',
      ],
      forcing: {
        flood: 'Weather API for the forecast; published gauge readings for replays. On-site S-band Doppler radar (one of only two in Kerala)',
        heat: 'Weather API + Landsat thermal composites',
      },
      layers: {
        L1: { src: 'DeltaDTM 30 m — bare-earth, CC-BY 4.0, MAE ~0.45 m, covers land below 10 m above sea level. Kochi is 0–7 m, so it is fully inside coverage.', q: 'ok', proc: 'Pre-staged in our own storage; windowed read per box; resampled to a 10 m working grid. Relative ranking only — the ~0.45 m error is the same size as the depths we would otherwise report.' },
        L2: { src: 'OSM canals and backwaters. Storm-drain network not public anywhere.', q: 'gap', proc: 'Canals burned into the terrain. Missing drains mean we overstate pooling in well-drained areas — stated on screen.' },
        L18: { src: 'KSDMA flood hazard rasters — depth in cm by return period (10/25/50/100/200/500-year, historical and RCP8.5), produced with UNEP/GRID-Geneva', q: 'good', proc: 'Displayed as published, clipped to the box. Never resampled or reinterpreted. Resolution and licence still to be confirmed. [u]' },
        L4: { src: 'OpenStreetMap; Google Open Buildings', q: 'good', proc: 'Burned in as walls; spreads population within a ward; extruded in the optional 3D view.' },
        L7: { src: 'OpenStreetMap', q: 'good', proc: 'Burned in as shallow trenches; used to flag routes crossing pooling spots.' },
        L8: { src: 'ESA WorldCover 10 m; Sentinel-2', q: 'good', proc: 'Paving share feeds the runoff coefficient.' },
        L9: { src: 'Landsat 8/9 thermal, hot-season composite', q: 'weak', proc: 'Humid climate means few clear scenes — longer compositing window, area level only.' },
        L10: { src: 'Census 2011 ward tables (access-restricted holdings); Kerala KSMART ward GIS from Nov 2025, public availability unconfirmed [u]', q: 'weak', proc: 'Totals as the control; age structure from WorldPop. Needs a 74→76 ward crosswalk.' },
        L11: { src: 'WorldPop R2025A 100 m age/sex, CC-BY', q: 'ok', proc: 'Spreads ward totals within the ward.' },
        L13: { src: 'OSM + Places API', q: 'ok', proc: 'Hospitals, schools, community halls. Places coordinates refreshed monthly per the terms.' },
        L14: { src: 'Kerala SDRF 2026 rules (budget lines); Operation Breakthrough orders; Kochi Corporation + Ernakulam DDMA', q: 'ok', proc: 'Document agent extracts the budget lines; preparations are capped by them. No official flood-prone point list exists. [u]' },
        L15: { src: 'Maps Platform Weather API. IMD runs an official gateway but it needs JWT plus IP whitelisting — real friction for a short build.', q: 'ok', proc: 'Forecast rainfall mapped to a return period, which selects the KSDMA layer.' },
        L16: { src: 'GDELT 2.0 corpus (Translingual covers Malayalam); KSDMA event maps for 2018–2020 including a 2018 crowd-sourced inundation-depth map', q: 'ok', proc: 'Named localities extracted for replay scoring. Rainfall for 28 May 2024 is single-gauge, not a network — stated.' },
      },
      sources: [
        ['KSDMA hazard maps', 'https://sdma.kerala.gov.in/hazard-maps/'],
        ['KSDMA event-specific maps', 'https://sdma.kerala.gov.in/event-specific-maps/'],
        ['28 May 2024 Kochi cloudburst', 'https://www.onmanorama.com/news/kerala/2024/05/28/kochi-rain-waterlogging-traffic.html'],
        ['DeltaDTM (4TU)', 'https://data.4tu.nl/datasets/1da2e70f-6c4d-4b03-86bd-b53e789cc629'],
        ['WorldPop R2025A', 'https://hub.worldpop.org/geodata/listing?id=138'],
        ['GDELT Translingual', 'https://blog.gdeltproject.org/gdelt-translingual-translating-the-planet/'],
      ],
    },
  ];

  const v = (q, data, proc) => ({ q, data, proc });
  const variants = {
    F1: { koc: v('ok', 'DeltaDTM covers the whole corporation area (0–7 m elevation)', 'Coastal-lowland profile. Boxes extending inland past the 10 m contour fall back to a surface model and are flagged.') },
    F3: { koc: v('ok', 'Weather API; published gauge readings for replays; on-site Doppler radar', 'Rainfall mapped to a KSDMA return period, which selects the validated layer.') },
    F4: { koc: v('ok', 'DeltaDTM + OSM canals, buildings and roads', 'Depressions filled, flow accumulated, pooling ranked 1–5. Missing storm drains mean pooling is overstated in drained areas.') },
    F5: { koc: v('good', 'KSDMA depth rasters by return period, produced with UNEP', 'Displayed as published. This is the one place our screening can be checked against somebody else’s validated data.') },
    F6: { koc: v('weak', 'Landsat thermal, hot-season composite', 'Few clear scenes in a humid climate — longer window, area level only.') },
    F7: { koc: v('weak', 'Census 2011 ward totals + WorldPop', 'Needs a 74→76 ward crosswalk. Reported by area, never by building.') },
    F9: { koc: v('ok', '₹5 crore preparedness line; ₹50,000/ward debris clearance; Operation Breakthrough canals', 'Preparations capped by the real budget lines. Canal desilting is the highest-value lever here.') },
    F11: { koc: v('ok', 'Malayalam + English; WhatsApp', 'Officer briefing in English, area messages in Malayalam. Drafts only — we never send.') },
    F13: { koc: v('ok', 'GDELT Translingual covers Malayalam; grounded panel for live reporting', 'Stored corpus is scored; grounded panel is display-only.') },
    F15: { koc: v('good', '28 May 2024 cloudburst; KSDMA rasters as independent ground truth', 'Score our top-ranked pooling spots against named flooded localities and against the official raster.') },
  };

  const findings = [
    { h: 'Kochi was chosen for one reason: official data to check ourselves against.', p: 'KSDMA publishes flood hazard rasters with depth in centimetres by return period. No other candidate city had anything comparable. It converts "trust our model" into "here is how our model compares to the authority’s".' },
    { h: 'No open terrain model anywhere is good enough for absolute street depths.', p: 'Published work shows accuracy collapsing beyond about 7 m resolution, and at 30 m the water-level error exceeds the depth being reported. So we rank places against each other instead, which survives the error because it is spatially correlated over distances larger than a block.' },
    { h: 'India has no photorealistic 3D coverage.', p: 'Google’s coverage page lists no Indian city. 3D terrain is global and our own geometry renders fine, so 3D stays as an optional view built from our own extruded OSM buildings — not as the main claim.' },
    { h: 'Storm drains are not public — anywhere.', p: 'This is the single largest known bias: in areas that drain well, we will overstate pooling. It is disclosed on every run rather than buried.' },
    { h: 'Rainfall ground truth is thinner than it looks.', p: 'The 28 May 2024 figures come from a single gauge, not a city network. Good enough to replay an event; not good enough to claim spatial rainfall accuracy.' },
    { h: 'Boundaries moved.', p: 'Kochi’s wards went 74 → 76 in the 2025 delimitation, so 2011 census ward data needs a crosswalk before it means anything.' },
    { h: 'The real-time data path is awkward.', p: 'IMD’s official API needs JWT and IP whitelisting. For a short build we use the forecast API plus published gauge readings, and say so.' },
  ];

  return { cities, layerRows, variants, findings };
})();
