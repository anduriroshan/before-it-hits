# Before It Hits — demo narration script

Spoken text is identical to `narration.srt`; this copy adds the on-screen action each cue needs,
for recording and editing.


**Runtime at ~165 wpm: 00:02:28,324** (19 cues, 397 words). Target was 2:30.


| # | Time | Narration | On screen |
|---|------|-----------|-----------|
| 1 | 00:00 | A city gets one weather warning for the whole region. But a storm hits streets, not the whole city, one block at a time. | Title card: Before It Hits. Then the live site, map only, no box drawn yet. |
| 2 | 00:08 | Agencies already forecast well at the city scale, and their official flood maps are the ground truth here. | Hold on the map, slow drift. |
| 3 | 00:15 | Before It Hits adds the missing layer. It turns that same public data into an answer for your own street, for anyone to ask. | Back to the live site, panel visible. |
| 4 | 00:24 | Let's ask about Edappally, in Kochi. The moment you draw a box, it pulls the live forecast, runs a deterministic terrain engine, and writes a checked answer, not a guess. | Select Kochi, click the Edappally preset; loading state, then the answer renders in the Agent tab. |
| 5 | 00:35 | These blue shapes are the low spots where rainwater collects, ranked one to five. | Zoom on the blue pooling polygons; legend visible. |
| 6 | 00:41 | Watch the time bar. As the forecast rain builds hour by hour, the low spots fill in the same order the terrain would, and the roads through them go under too. | HERO SHOT: press play on the forecast time bar; low spots ink in, roads light up. |
| 7 | 00:52 | Every number, the people, the roads, the rain each spot needs, comes from the engine. The model only puts it in plain words. | Pan the Details tab: worst spots, roads, people. |
| 8 | 01:01 | Now ask it something it can't honestly answer. | Focus the chat composer. |
| 9 | 01:04 | How deep will the water get? It refuses. The terrain's own elevation error is as large as the depth would be, so it ranks risk instead of inventing a number. | Type "How deep will the water get?"; show the refusal + the expanded verifier trace. |
| 10 | 01:15 | How would this affect traffic? It says plainly it holds no traffic data, then gives what published studies found, instead of inventing a jam. | Type "How would this affect traffic?"; show the cited-study answer. |
| 11 | 01:24 | A verifier agent checks every reply. If a number can't be traced back to the engine, the claim is removed. | Expand the "Checked by the verifier" trace line. |
| 12 | 01:31 | The same box answers for heat, wind, earthquake shaking, and air quality. Five hazards, one place to ask. | Layers panel: toggle Surface heat, Wind, Air quality quickly. |
| 13 | 01:38 | It doesn't wait to be asked either. A Watcher sweeps every area on a schedule, and publishes a preparation alert to Pub/Sub when a storm would fill the worst spots. | Preparation alerts banner; click Check now; alerts populate. |
| 14 | 01:50 | Where a government publishes an official flood map, like Kochi's, we show their depths unchanged, and report how often we agree. | Details -> Compare with the government's flood map; official overlay + agreement stat. |
| 15 | 01:57 | It runs on Cloud Run, where Gemini only reads the question and writes the explanation. It never computes a figure. | Cut to the architecture diagram: client, Cloud Run, engine, verifier. |
| 16 | 02:05 | The same verified answers are open to other software, through an MCP server and an A2A agent. | Architecture diagram, highlight the MCP + A2A box. |
| 17 | 02:12 | Six hundred ten automated checks, and ninety eight adversarial questions that come back clean. | Terminal running the test suite, or the benchmarking slide. |
| 18 | 02:17 | Every figure computed. Every figure checked. Every limit stated out loud. | Hold on the live map, worst spots visible. |
| 19 | 02:21 | Before It Hits. Built for the AI Builder Cup, running live, with the code and the link below. | End card: title, live URL, GitHub link, AI Builder Cup mark. |

**Pacing note:** written for a brisk ~165 wpm read. A slower TTS voice (~150 wpm) lands around 2:32, still inside 2:30-ish. If you need it shorter, cut cue 15 or 16 (the architecture and MCP lines) first: they are the only beats not tied to a specific piece of live footage.
