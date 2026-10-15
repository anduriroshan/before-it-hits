# 06. UI design language

The bar the user holds this UI to: **"what would a weather app do with this?"** (they named Ventusky), and
**"what would it say on a day when the answer is nothing?"** A panel that looks like machine output loses on
polish and on honesty at once. Values below are the real ones from `web/style.css` and `web/app.js`.

## Principles

1. **A map layer is a field, not a set of labelled dots.** Printing "85%" on nine circles implies a reading at
   each dot and says nothing about everywhere else. Draw a smooth interpolated colour field on canvas, with a
   colour scale beside it. Figures belong in the side panel, with their source.
2. **One clock.** Everything time-shaped on the map follows a single thin bar. A scrubber that only fills is a
   loading bar, not weather.
3. **The map is the subject; chrome is thin.** Floating controls are translucent and small. No cards over the
   picture. (The time bar is one line, no card.)
4. **Say which patch of ground, every time.** Box or whole city, printed unprompted.
5. **Say what a thing is not, in the same place you show it.** Typical season, not today. Surface, not air.
   Modelled, not measured. Best case, not a forecast.
6. **Honest empty states.** Nothing is faked: null is transparent, "not available for this city" is a sentence,
   and a day that needs no spending says so and spends ₹0.
7. **Agent first.** The headline flow is "draw a box, ask". Controls exist for testing and for people who want
   them; they are not the route to the answer.
8. **Calm, plain, rounded hard.** Short sentences, everyday words, "about" and "roughly" for modelled figures.

## Layout

```
┌──────────────────────────────────────────────┬──────────────┐
│ MAP (grid column 1fr)                         │ PANEL 440 px │
│ ┌toolbar┐ ┌layers panel┐      [map type ▾]    │ brand · city │
│ │Move   │ │ checkbox   │      ┌legend┐        │ tabs: Agent  │
│ │Box    │ │ + opacity  │      └──────┘        │ Rain Details │
│ │Erase  │ │ + note     │             ┌scale┐   │ Heat Air News│
│ │Clean  │ └────────────┘             │ bar │   │              │
│ │Layers │                            └─────┘   │ (chat or     │
│ └───────┘     ◖▶ ━━━━●━━━━━━  Wed 3:30 PM ◗    │  scrolling   │
│                                  [zoom ± ]     │  pane)       │
└──────────────────────────────────────────────┴──────────────┘
```

- `body` is a two-column grid: `minmax(0, 1fr) 440px`. Under **900 px** it stacks: map `46vh`, panel below.
- Floating map controls, all `position: absolute` inside `#mapwrap` (siblings of `#map`, never children):

| Control | Position | z-index |
|---|---|---|
| `#toolbar` (Move / Box / Erase / labels / Layers) | top 12, left 12 | 6 |
| `.layers-panel` | top 12, left 82 | 6 |
| `#legend` (pooling rank scale) | top 64, right 12 | 5 |
| `#fieldscale` (one colour bar per active field) | right 12, bottom 64 | 6 |
| `#tl-float` (the time bar) | bottom 16, centred, 560 px max | 6 |
| `.maphint` ("Drag on the map to draw a box") | top 14, centred | 6 |
| Google map-type dropdown / zoom | top-right / right-centre (set in `initMap`) | Google's |

- The panel is a flex column: header (brand, city select), tabs, then one `tabpane`. The Agent pane is chat +
  composer pinned at the bottom; the others scroll (`.tabpane.scroll`, content in `.pane-pad`, padding
  `14px 18px 30px`).

## Tokens

```css
--ink:    #15191e;   /* text */            --accent: #0e7490;  /* interactive, agent, "this is ours" */
--muted:  #5d6671;   /* secondary text */  --water:  #2563eb;  /* water, past-rain bars */
--faint:  #8b939d;   /* captions, labels */--warn:   #b45309;  /* caution, modelled, removed, caveat rule */
--line:   #e2e6ea;   /* hairlines */       --card:   #fff;     --bg: #f6f7f9;
--p5: #0b3d91; --p4: #1d6fd0; --p3: #4a9fe0; --p2: #8fc5ec; --p1: #c8e2f5;   /* pooling rank 5 to 1 */
--mono: ui-monospace, "SF Mono", "Cascadia Mono", Menlo, monospace;
```

- **Type:** `system-ui, -apple-system, "Segoe UI", sans-serif`, 15 px / 1.5 base. `h1` 20 px; section `h2`
  13 px uppercase `letter-spacing .07em` in `--faint`. Body copy in panels 13 to 13.5 px; captions 11 to 12.5 px.
  **Numbers use `font-variant-numeric: tabular-nums`**; ids, timings and rules use `--mono`.
- **Radius:** controls 8 px; cards and floating panels 10 to 12 px; pills 999 px; chat bubbles 16 px with one
  4 px corner toward the speaker.
- **Shadow:** floating panels `0 2px 12px rgba(0,0,0,.12)`; the time bar has none (it is a tint).
- **Red** `#b91c1c` / `#7f1d1d` is reserved for failures and withheld briefings. **Green** `#0a7d34` on
  `#e6f6ec` is reserved for "live". Do not use either decoratively.

## Colour language (semantic: the same meaning everywhere)

| Meaning | Colour |
|---|---|
| Interactive, selected, the agent, "ours" | `--accent` teal `#0e7490`; selected tool/tab fill or underline |
| Water and pooling rank | blue ramp `--p1`..`--p5`; filled spots opacity `.75`, unfilled `.35`; outlined green `#0a7d34` in the "with the plan" view |
| Official (KSDMA) depth | blues `#cfe8ff, #86c0f0, #3d8fd1, #1b5fa8, #0b3566` under the screening, never merged |
| Roads going under | yellow to red `#f2c94c, #f59f00, #f76707, #e03131, #b3122b` (no near-black: many dark strokes smear). Stroke by class (motorway 3.4 px to tertiary 2.2), scaled with zoom |
| Caution, modelled, "sent back", caveat | `--warn` orange; `.tag-modelled` `#b45309` on `#fdf2e3`; caveats get a 2 px orange left rule |
| Live | `.tag-live` green pill |
| Failure, withheld | red text/border |
| Heat, temperature, surface heat | blue → green → yellow → orange → red: `#3b6fb5, #56a8d4, #8fd3a8, #f0e05e, #f4a63a, #e2512a, #8b1414`. Red always means hot, on forecast and satellite alike; the scale's label says which |
| Humidity | cream → teal → deep blue `#f7f3e4 … #14306b` |
| Rain | `#eaf6ff, #86cff0, #2f84d4, #2a3da0, #6b2a9c` |
| Wind | `#f2f6f5, #9ad4c2, #3f9f8c, #2a6f8f, #1b3566` |
| People per km² | `#fff8e8, #ffd481, #fb9242, #df472a, #75102c` |

Ramps are defined once in `RAMPS` (app.js). The scale beside the map and the painted field are built from the
same stops, so **the legend cannot drift from the picture**.

## Components

- **Tool palette.** Modal: exactly one tool active (`aria-pressed`), 58 px wide buttons with a 19 px glyph and a
  10.5 px label; active = teal fill, white text. Keys `V` `B` `E` `L` `Esc`. While Box is armed the map is a
  crosshair drawing surface.
- **Tabs.** Underline tabs, 13.5 px semi-bold; selected = teal with a 2 px underline; a 7 px orange dot on the
  **Details** tab marks a new result you have not looked at yet (`notifyDetails`).
- **Chat.** User bubble teal, right, white text. Agent bubble `--bg` with a hairline border and a 26 px circular
  avatar. A **work block** (`<details class="work">`) is open and spinning while agents run, then folds to one
  line ("Checked by the verifier · 5 steps · 23 s"), still one click from the full trace: *a checked briefing and
  an unchecked one look identical, so this is the only way to tell them apart.* Trace steps have a coloured left
  rule: teal ok/revised, orange sent back/trimmed/guarded, red failed/rejected/withheld/unavailable, each with an
  uppercase pill and a mono timing.
- **Answer.** A chat reply, not a report: the opening answer a touch larger but not bold, then ordinary paragraphs
  (no bullets), then a caveat with an orange left rule, then Copy / Ask again / "Open details →", then **three**
  follow-up chips (dashed rule above) and one quiet "More about this area ▾" fold that holds the rest (the other
  hazards, who lives here, what is reported). There are no fact chips: the figures are in the sentences. The
  options row stays open while the first prediction runs, because then it is the thing to do.
  Removed claims are listed with the rule that removed them, in orange.
- **Chips.** 999 px pills, 12.5 px. Default white; `chip-alt` teal-tinted (e.g. "What if it rains more?");
  `chip-static` solid tint for labels. Fact chips are 11.5 px, muted, value in bold.
- **"More about this area" row** (`.followups.area-options`). A small uppercase label then teal chips, one per
  hazard in `hazards.py` plus "Who lives here" and a plain "Ask about this area". It appears in the agent bubble
  the moment a box is drawn, while the rain prediction is still running, and moves into the answer when it lands.
  After any answer it offers the other things, never the one just answered. Rain is never behind it: it has
  already run. Choosing heat, wind or air also turns on that layer.
- **Wind on the map.** Colour for speed, a grid of white arrows with a dark halo (about 62 px apart, longer where
  stronger) and animated white streaks with fading tails, for direction. Arrows point where the air is GOING;
  words say where it blows FROM. Streak pace has a floor and means "moving", not "fast": colour and arrow length
  carry the speed, and the layer note says so.
- **Cards.** `<details class="card">`, 1 px border, radius 12, a chevron drawn with borders, summary hover `--bg`,
  body padding `2px 14px 14px`. Heavy detail folds into cards; the primary answer stays open.
- **Scope line.** `.scopeline.box` pale blue `#eef7fb` with a teal left rule; `.scopeline.city` pale amber
  `#faf7ee` with a `#c9a227` rule. Bold "For the box you drew · 19.5 km²" then a one-line note. At the top of
  Heat, Air and the satellite section.
- **Tags.** Small uppercase pills: `modelled` (orange), `live` (green), `satellite` (re-uses the modelled style).
- **Headline block.** `--bg` rounded 10, big tabular number (26 px) with a muted line beneath.
- **Status bar.** A pinned strip with a teal left rule; red variant for errors. Used for "reading the terrain…".
- **Layers panel.** One row per layer: checkbox + label on the left, a 56 px opacity slider on the right (hidden
  until the layer is on), and a **one-line note** underneath (10.5 px, muted, orange when it is a warning). A
  turned-on row gets a faint teal wash. The list scrolls past 62 vh.
- **Colour scale.** `.fs-item` card per active field: bold label, unit in muted, a 12×86 px gradient bar built
  from the ramp, three tick values. Stacked, `pointer-events: none`.
- **The time bar.** One line, nothing else. A pill tinted `rgba(14,20,28,.34)` with `backdrop-filter: blur(3px)`,
  white marks with a faint text shadow so it reads over a pale basemap and over any field colour: a 26 px
  circular play/pause (inline SVG, not emoji glyphs), a 3 px line filled to the current moment with a 13 px white
  thumb, and the time ("Wed 3:30 PM", tabular, right-aligned, 92 px). The line moves smoothly while playing
  (step `0.02`); arrow keys move one hour; Space toggles play. **It shows only time.** Everything it used to show
  (sources, hourly rain strip, rainfall readout, storm length) lives in the Details → "Roads that go under
  first" card.
- **Hottest ground list.** A row per place: a coloured temperature pill (red-orange `#c2381f` for hot, blue
  `#3b6fb5` for cool), "beside **Road** (about 70 m)" with a muted "· 4.4°C above the box's typical", and a *Show*
  link that turns the layer on, pans and zooms to 16, and draws a 110 m white ring for 7 s.
- **News feed.** Folds to a 44 px title bar with the headline count; the fold is remembered
  (`localStorage` key `bih.newsfold`) and is **never** applied automatically, because that would hide the sources
  an answer is built from. The label-density choice is `bih.labels`.

## Map drawing rules

- **Canvas fields.** A small raster painted through a 256-step LUT and scaled up by the browser. Anything with no
  value is fully transparent. Default opacities: heat `.65`, surface `.7`, temperature `.65`, humidity `.6`, rain
  `.7`, wind `.55`, population `.6`, air quality tiles `.55`, traffic `.8`.
- **Stable scales.** Weather fields use fixed bounds (heat 24 to 44 °C). Surface heat uses the city's own p2 to
  p98. Population is cut at p95.
- **Honest about resolution.** Do not upsample and sharpen. Soft edges are the truth about how coarse the data is.
- **Clean label style by default** (no shops, restaurants, transit icons or road shields) so the layers read.
  Google adds a local-language second line under district names in India; styling cannot remove it.

## Copy and tone

- **Plain words.** "Low spots where water collects", not "pooling spots". If a rank is shown, say its scale
  ("rank 5 of 5"). Explain a technical word in a few words or leave it out.
- **Round hard, qualify modelled figures:** "roughly", "about", "~". Never false precision on a modelled number.
- **Name the source and what it is not,** at the point of use: "Landsat satellite, a typical mid-morning (January
  to April): not a forecast, not today, and the ground's surface rather than the air."
- **Speak to what a person would do with it, never what they must do.** Preparation and planning only. No
  "evacuate", no "dispatch", no "will flood at 7".
- **No hype, no emoji, no exclamation.** No "AI-powered". Sentence case. Numbers carry their unit (`41.6°C`,
  `45 mm`, `₹22,80,000`).
- **Empty and error states say what is true:** "Not available for this city." "The data arrived but this panel
  could not be drawn. That is a fault in this page, not in the reading." Never "not available right now" about
  something that actually arrived.
- **Quiet day:** "An ordinary amount of rain, so nothing to buy for it. The India Meteorological Department
  would call this light rain or less…" It explains, and still reports how many spots fill anyway.
- **Layer notes are one or two sentences,** stating the source, the area (box or city) and the limit.

## The honesty patterns, as UI

| Pattern | Where |
|---|---|
| Scope line (box vs city, with km²) | Heat, Air, surface-heat section |
| "What this is and is not" fold | Heat satellite section, Details caveats, the guide |
| `modelled` / `live` / `satellite` tags | card titles |
| What-if is labelled "What-if, not a forecast", with no rain bars (the even spread is an assumption) | time bar source, Rain tab |
| Best-case label on before/after | preparations |
| "Inferred" units on the official layer | Details |
| Removed claims shown with their rule | Agent answers |
| Trace shows failures and sent-back drafts | work block |
| Null cells transparent; a missing hour never becomes 0 | all fields |
| Colour scale is the city's, not the box's | surface heat |
| Resolution stated ("patches about 100 m across") | layer note, Heat tab |

## Responsive and accessibility: what exists, and what is not verified

Exists: tabs use `role="tab"` and `aria-selected`; tools use `aria-pressed`; the chat and news log use
`aria-live`; controls have `aria-label`s; the time line has a visible `:focus-visible` ring and keyboard stepping;
the stacked layout under 900 px; the panel layout was checked at 1500×900, 1280×680 and 820 wide on 12 Oct.
**Not verified:** the time bar, field scale and layers panel at narrow widths; keyboard order through the layers
panel; `prefers-reduced-motion` (the clock autoplay is user-initiated, but this was not audited); colour contrast
of the white-on-tint time bar over every basemap and field colour.

## Do and don't for new UI

- Do reuse the tokens, the scope line, the card, the chip and the layer-row patterns before inventing new ones.
- Do put detail in the Details tab or a fold and keep the map and the headline answer clear.
- Do state the box or the city, the source, and the limit, in that component.
- Don't print numbers on the map. Don't make a card over the map. Don't add a control where the agent could just
  understand the question. Don't use red or green decoratively. Don't claim resolution you do not have.
- Don't rely on the `hidden` attribute against an id-level `display` rule; the global
  `[hidden] { display: none !important; }` is there for that.
- Don't put anything that floats over the map *inside* `#map`: Google Maps empties that element.
