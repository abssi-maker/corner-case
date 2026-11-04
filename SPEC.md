# Corner Case — build spec

> Test the fix before the next crash.

You are building a submission for the **Meta Global AI Developer Hackathon** (Indie track, theme **Push the Frontier**), solo, for a senior developer. Read this whole file before writing code. Work in this repo (`corner-case/`). Submission material (video, Devpost text, gallery) goes in `../submission/`.

**Deadline: Wed Nov 4 2026, 21:00 ET (02:00 UTC Nov 5). Late entries are rejected. Aim to submit by 20:00 ET.**

This project is *inspired by* an earlier entry (FloorGuard, `/Users/mac/local/internal/Nebius/Sightline`): an agent proposes a fix, a deterministic simulator measures it, stress tests break it, and the agent revises. **Do not copy any code from it.** Different vertical, new code, new name, its own repo.

---

## 1. How it will be judged

Stage One is pass/fail: fits the theme, and really uses Meta's models. Stage Two scores each criterion 0–10:

| Criterion | Weight | What the judges ask (verbatim) |
|---|---|---|
| Impact | 30% | "What's the real-world potential here? Who benefits, and how much does it matter? Could this actually become something people use?" |
| Use of Meta AI Tooling | 25% | "How creatively did this team use Meta's AI tooling? Did they go beyond a basic integration? Did they surface capabilities that surprised even us?" |
| Technical Depth & Execution | 20% | "Is the engineering sound and thoughtfully refined? Does this feel like something that was thought through with real craft." |
| Demo | 25% | "Is this a working, impressive demo? Does the product hold up live? Is the demo video genuinely cool to watch?" |

The top 5 per track go to a final round where judges watch the 3-minute videos live. The video may show **only what was built during the hackathon**.

Bonus prize we target: **Best Build on Meta Open Weight Models ($10k)**: Muse Glimmer served on partner inference (Modal).

**Lesson from FloorGuard's mock judges:** all five gave Impact 3/5 because every number came from assumptions with no link to reality. Corner Case must replace assumptions with measurements, and check the simulator against real outcomes.

---

## 2. The product in one paragraph

Pedestrians keep getting hit at the same Manhattan corners. Cities fix a corner after people are hurt, and need years of crash counts to learn whether the fix worked. **Corner Case** lets a city traffic-safety engineer test changes on a real corridor before spending money. It shows where people actually got hurt (NYC crash records), measures how people and cars really behave at the corner (Meta SAM 3.1 + Muse Spark on street video), rebuilds the corridor as a simulator, **proves the simulator ranks dangerous corners the way real crashes did**, then lets Muse Wonder agents (one per intersection) try fixes, break them under stress futures, and coordinate a corridor plan. After a fix is built, a Muse Glimmer watcher counts near-misses on camera so the city learns in weeks, not years.

**User:** a Vision Zero / street-safety engineer at a city transport department (NYC DOT as the example).
**Decision:** which changes to fund on **7th Avenue, W 30th–59th St** this quarter, and in what order.

### Real facts already in the repo (use these; do not invent others)

- `data/crashes/manhattan_ped_2023_2026.json`: NYC Open Data "Motor Vehicle Collisions – Crashes", Manhattan, Jan 2023 – Oct 31 2026, every crash that injured or killed a pedestrian. **6,266 crashes, 77 pedestrians killed, 6,441 injured.** Each row has lat/lon, streets, contributing factor and vehicle type.
- Hotspot analysis (avenue × 10-block segment, pedestrian crashes): **7 Ave 40–49 St: 49 (1 killed)**; 7 Ave 30–39: 46 (1); 8 Ave 30–39: 46; 8 Ave 50–59: 44. Street names are inconsistent (`2 AVE` / `2 AVENUE`, `W` / `WEST`), and about 28% of rows have no street name, so **snap crashes to intersections by lat/lon**, not by name.
- `data/footage/timessquare-2017.webm`: 15 s portrait clip of Times Square at night (7th Ave/Broadway at about 45th St), CC BY-SA 4.0. See `ATTRIBUTION.md`. **More footage is needed.** The user may supply a Pexels API key; if so, pull 3–6 licensed Midtown crosswalk clips (elevated angle, turning cars, people crossing) and record licences in `ATTRIBUTION.md`. NYC DOT live cameras (webcams.nyctmc.org) time out from this network. Do not spoof user agents or bypass bot checks.
- Other NYC Open Data sets worth pulling (same Socrata API; no key needed for small queries): Automated Traffic Volume Counts (`7ym2-wayt`), Bi-Annual Pedestrian Counts, and the DOT signalised-intersection / Leading Pedestrian Interval lists if available. Verify each dataset ID before relying on it.
- A Commons video titled "2026-09-15 Mayor Mamdani Unveils Vision Zero Reimagined to Make Streets Safer for Every New Yorker" exists. Read its description before using it as the "why now" hook; only the title is known.

---

## 3. The flow (this is the product; get it right first)

A judge who knows nothing about the project must understand **what it does, for whom, and what to do next** within 10 seconds of any screen. The story is seven steps, always visible as a stepper at the top, each a plain-English question:

| # | Step label | The question this screen answers | What the user sees and does | Real or simulated |
|---|---|---|---|---|
| 1 | **Where people get hurt** | "Which corners on 7th Ave hurt the most people?" | A map of the corridor (W 30th–59th) with every real pedestrian crash as a dot and each intersection sized by its toll. One headline: "185 people hit on these 40 blocks since 2023." Primary action: **Pick a corner** (Times Square is preselected). | REAL |
| 2 | **Watch the corner** | "What actually happens there?" | The real video with SAM 3.1 masks and tracks on every car and person, a scrubbable timeline, and Spark's timestamped notes ("0:07 three people start crossing on a flashing DON'T WALK"). Output: **measured numbers** that replace assumptions (crossing against the signal %, turning-car speed, crowd density). | REAL (measured from video) |
| 3 | **Rebuild it** | "Can we replay this corner in a computer?" | The simulator plays today's corridor top-down: signals cycling, turning cars, people crossing; every near-miss flashes a red ring. A side panel shows which inputs were measured (step 2) and which are assumptions, each with its source. | SIMULATED |
| 4 | **Can we trust it?** | "Does the simulator know where crashes really happen?" | Two ranked lists side by side: the simulator's riskiest intersections vs the real crashes it never saw (held-out 2025–26). A single verdict line with the actual metric, e.g. "Simulator's top 5 contains 4 of the real top 5", plus the baseline that uses traffic volume alone. **Show the true result, even if it is weak.** | REAL vs SIMULATED |
| 5 | **Agents try fixes** | "What should we change, corner by corner?" | One card per intersection agent (Muse Wonder sub-agents) proposing a change (walk head start / leading pedestrian interval, turn ban, slower turns via hardened centreline, clearing parked cars from the corner, signal retiming), running it, and keeping or dropping it. A corridor coordinator rejects fixes that push risk to the next block; show at least one such rejection. | SIMULATED |
| 6 | **Stress the future** | "Does the plan still hold when things change?" | The plan under futures the agents choose: holiday crowds, a Broadway show letting out, rain at night, a new bus lane, 30% of drivers ignoring the turn ban. One plan visibly breaks, gets revised, then holds. | SIMULATED |
| 7 | **The plan** | "What do we fund, and how will we know it worked?" | A one-page brief: changes per corner in priority order; simulated near-misses before/after; added car delay (the honest cost); rough cost with cited sources (Spark web search); and **"After it's built"**: the Glimmer watcher counts near-misses on camera weekly, so success is visible in weeks. Printable. | SIMULATED + cited |

### UX rules (judged as UX, not decoration)

- **One question per screen, one primary action.** The header of each screen is its question in plain words. No jargon without a one-line explanation on first use ("leading pedestrian interval = walkers get a few seconds' head start before cars").
- **Every number carries a REAL or SIMULATED badge**, and its source on hover/tap. Simulated near-misses are never presented as predicted injuries or deaths.
- **Show the agents' work, not chat.** Agent activity is cards with state (proposing → testing → kept / dropped, and why), not transcripts.
- **Meta model attribution is small and specific**: a chip on the element that model produced ("SAM 3.1 tracked 214 people"), not a logo wall.
- **"Play the story" mode** auto-advances through the seven steps on a recorded run with no API calls, for judges and for the video. A live mode re-runs with real calls.
- Loading, empty and error states are designed, not default spinners. Long model steps show live progress.
- The first screen works without explanation: map, headline, one button.
- Desktop first (1440 px), legible on a projector; must not break at laptop widths.

### Visual direction

The user rejects both dull SaaS dashboards and neon/glow "mission control". **No gradients, no glow, no glassmorphism, no stat-card walls, no emoji.** Aim for an editorial, map-first tool that feels like NYC streets: off-white paper background, ink-black text, crosswalk-white and pedestrian-signal orange (the "DON'T WALK" hand) as the risk colour, a calm green for "holds". Type: a confident grotesk (Helvetica-like, as in NYC signage) with tabular numbers. Restraint beats ornament. The designer may propose something better if it reads more clearly.

---

## 4. Mockups first: GPT Image via Codex, then a UX test loop

Before building the UI, design it. Use **Codex's built-in image generation (GPT Image)** to make high-fidelity mockups, then test them on fresh eyes and iterate until the **flow** is clear, not just the look.

**Setup**
1. Write `design/style.md`: product summary, tagline, palette (hex), type, logo idea, top bar and stepper, the REAL/SIMULATED badge, and the real numbers from §2. Every mockup prompt says "Read `style.md` first and follow it exactly."
2. One prompt file per screen: `design/prompts/0N-<step>.md`, 1536×1024 landscape, exact text to render, layout read in order (question → evidence → action). Use real numbers only; where a number does not exist yet, use an obviously placeholder value and mark it in the prompt file.
3. Generate each screen with Codex (attach a real frame from the footage or a map render as reference where useful):

   ```bash
   cd design/mockups
   codex exec --skip-git-repo-check -s workspace-write -C "$PWD" \
     -i ../refs/<reference>.jpg \
     -o v1/0N-<step>.codex.md - < ../prompts/0N-<step>.md > v1/0N-<step>.log 2>&1
   ```

   The prompt must say: "Use your built-in image generation tool (GPT Image) to create ONE high-fidelity UI mockup image. Do NOT write HTML or code. Save it as `v1/0N-<step>.png` (copy it from wherever the tool saves images). Create or modify no other file. Reply with the saved path and a 3-line note on anything the image got wrong." Run screens in parallel.
4. Look at every image yourself. Reject misspelled text, invented brands, wrong numbers or clutter, and regenerate.

**UX test loop (repeat until it passes; at least 2 rounds)**
1. Give the full sequence of mockups, in order, with **no other context**, to two fresh reviewers: a fresh read-only Codex session (`codex exec --skip-git-repo-check --ephemeral -s read-only -i ...`) and a fresh Claude subagent. Ask each, per screen: What is this product? Who is it for? What question does this screen answer? What would you click next, and why? What is real and what is simulated? What confused you?
2. Score against the intended answers in the §3 table. **Pass = both reviewers answer the product, the user, each screen's question and the next action correctly on every screen, and name the REAL/SIMULATED split correctly.**
3. Fix the flow first (order, labels, what each screen asks), then the visuals. Regenerate as `v2/`, `v3/`… Keep each round's reviewer answers in `design/ux-rounds/roundN.md` with a short table of what changed.
4. Copy the final set to `../submission/design/` and use it as the spec for the real UI.

Cap: about 90 minutes total for mockups and UX rounds. The build matters more than perfect images.

---

## 5. Meta models: each does the job only it can do

Base URL `https://api.meta.ai/v1` (OpenAI- and Anthropic-compatible). Key: `META_API_KEY` in `.env`. **List models with `GET /v1/models` at startup and resolve each role to a real ID; record actual IDs in every run file.**

| Model | Role in Corner Case | Notes measured on this account (Nov 3) |
|---|---|---|
| **SAM 3.1** | Step 2: text-prompted detect + track of "car", "taxi", "bus", "person" across the clip in one call. Tracks → image-space trajectories → metres via a 4-point homography the user confirms on a frame (crosswalk corners). Produces measured speeds, counts, conflicts. | Phrase prompts work; generic words can miss (a flat "button" returned 0, a descriptive phrase worked), so test a few phrasings. Point prompts stream full masks every frame: very slow; avoid. Use one-bit masks or boxes. Billed per source frame: downsample fps. |
| **Muse Spark 1.3** | Step 2: watches the same clip (Files API → `input_video`, `text.format` json_schema) for behaviour tracking can't see: crossing against the signal, looking at phones, cars turning into a busy crosswalk; timestamped. Step 7: **web search with citations** for unit costs of each change (prefer NYC DOT / FHWA sources). | Timestamps come back ~1 s early unless a clock is burned into frames (then ±0.3 s): burn a clock overlay with ffmpeg before upload. Low frame sampling (~2.7k tokens for 10 s). It hallucinated an action in a test, so cross-check its claims against SAM tracks and show agreement. |
| **Muse Wonder** | Steps 5–6: the planner. A lead agent spawns **one sub-agent per intersection** plus a corridor coordinator; each calls the simulator as a tool, reads measured outcomes, revises; the coordinator picks stress futures and rejects risk-shifting fixes. Long-running with error recovery. | Responses API. $4 in / $20 out per M tokens; keep prompts compact and cache shared context. Validate every structured output (Pydantic); one repair retry; keep repairs in the trace. |
| **Muse Glimmer** (open weights) | Step 7 "After it's built": served on **Modal** (vLLM, scale to zero) as an always-on watcher that reads per-minute track summaries and writes the weekly near-miss log and alerts. Optional small LoRA (§7). Qualifies for the $10k open-weights prize. | ~30B class (Meta compares it to Gemma4-31B / Qwen3.6-27B). The user's Mac has 8 GB RAM: **never run it locally**. Budget: **$30 Modal credit**; check modal.com/pricing first. Verify weights are downloadable and vLLM supports the architecture before committing. |
| Muse Image (optional) | Step 7: one "after" picture of the corner with the fix built, for the brief. Only if time remains. | $0.01/image. |

Do not use a model just to tick a box. If one is cut, cut it cleanly and do not mention it in the video.

---

## 6. Simulator and evidence (the technical depth)

**Corridor model** (Python + NumPy, deterministic, seeded, common random numbers across options so comparisons are fair):
- 7th Ave from W 30th to W 59th: about 30 signalised intersections, one-way southbound avenue, one-way cross streets alternating direction (verify per street). Signal cycle (NYC typical about 90 s; mark as assumption), avenue/street phases, pedestrian walk / flashing / steady DON'T WALK.
- Agents: through and turning vehicles (turning vehicles cross the parallel crosswalk during WALK, the main conflict), pedestrians with arrival rates, walking speeds and signal compliance.
- Conflict metric: **post-encroachment time** (PET < 1.5 s = near-miss) and time-to-collision, weighted by vehicle speed as a severity proxy. Report near-misses per hour per intersection.
- Interventions as parameters: leading pedestrian interval (seconds), turn ban by hour, hardened centreline (lower turn speed), daylighting (better sightline → driver reaction), signal timing changes, a split phase where turns don't share the walk.
- Inputs per intersection: **measured** from video where we have it (step 2); otherwise NYC Open Data volume and pedestrian counts; otherwise labelled assumptions. Every input records its source.

**Trust check (step 4), the evidence judges remember:**
- Calibrate on nothing from the target period: use 2023–2024 crashes at most for tuning (prefer no tuning), and score against **held-out 2025-01-01 to 2026-10-31** crashes snapped to intersections.
- Metrics: Spearman rank correlation between simulated near-miss rate and real pedestrian crashes; top-5 / top-10 overlap. Baseline: rank by traffic volume alone (and by crash history 2023–24). Report all honestly in the UI and README, with confidence intervals (bootstrap).

**Agent value check:** at the same simulation budget, compare Wonder against random search and greedy one-change-at-a-time on the full corridor (the search space is large and coupled across intersections, which is the point). Record results in `evidence/`.

**Run records:** each run writes `runs/<id>.json` with seed, inputs and sources, model IDs, every model call (tokens, latency, retries, cost estimate), proposals, measured outcomes, stress tests and the final plan. "Play the story" replays a run file with no API calls.

**Tests:** determinism, geometry and homography, PET computation, signal phase logic, intervention effects pointing the right way, crash snapping.

---

## 7. Small fine-tune (optional, decide at hour 8)

Only if the core flow, trust check and agents work by about 13:00 ET. Idea: the simulator generates thousands of labelled corner scenarios (inputs → near-miss rate and the best single change); a small LoRA on Glimmer learns to triage corners quickly, and the watcher uses it. Compare against base Glimmer on held-out simulated scenarios and report the result. Hard cap: about $15 of Modal credit and 2 hours. If skipped, say nothing about fine-tuning anywhere.

---

## 8. Stack and layout

- Python 3.12 with `uv`; FastAPI serving the web UI and streaming progress (SSE); Pydantic; NumPy; ffmpeg for frames, clock overlay and clips. Frontend: plain HTML/CSS/JS + SVG/Canvas (map and sim view); no heavy framework unless it clearly saves time. Map: OpenStreetMap tiles or a static SVG of the street grid built from OSM data (attribute OSM).
- Suggested layout:
  ```
  cornercase/  data/ (loaders, crash snapping)  vision/ (sam.py, spark.py, homography.py)
               sim/ (corridor.py, signals.py, agents.py, conflicts.py, interventions.py)
               agents/ (wonder.py, coordinator.py, glimmer_watch.py)  api.py  web/
  data/  evidence/  runs/  design/  tests/  modal/ (glimmer serve + optional lora)
  ```
- `README.md` for judges: what it is, a 60-second quick start, a replay command that works with no key, architecture diagram, evidence tables, limitations, licences and attributions (Commons CC BY-SA, OSM ODbL, NYC Open Data terms).
- Public GitHub repo (required). Never commit `.env`. MIT licence.

---

## 9. Plan (now about 05:00 ET; 16 hours)

| By (ET) | Done |
|---|---|
| 06:00 | Model list resolved; SAM and Spark smoke tests on the clip; crash snapping to intersections; more footage if a Pexels key arrives |
| 07:30 | Mockups v1 + UX round 1 (in parallel with the simulator) |
| 09:30 | Corridor simulator with conflicts and interventions; tests green; mockups pass the UX test |
| 11:00 | Trust check against held-out crashes, with the real result |
| 13:00 | Wonder sub-agents + coordinator + stress futures; one full recorded run. **Fine-tune go/no-go** |
| 15:30 | UI for all seven steps + "Play the story"; Glimmer on Modal |
| 18:30 | Demo video (hyperframes skills; VO with the user's Gemini TTS voice `voice_iomvf9o7zyz2`, "Night Narrator 2", see `/Users/mac/local/internal/Nebius/FloorGuard-submission/demo/tools/tts.py`; test one line early: that key may be out of credit) |
| 20:00 | Devpost text, gallery, public repo, submitted |

**Cut list, in order, if late:** Muse Image → fine-tune → stress futures beyond three → live mode (keep replay) → extra footage → Glimmer (last; it carries the $10k bonus).

## 10. Honesty rules

- Simulated near-misses are not injuries or deaths. Never claim lives saved; say "simulated near-misses per hour" and show the real crash numbers separately.
- Show the trust-check result as measured, even if weak, with the baseline beside it.
- Every cost cites its source; every assumption is labelled.
- Credit footage, OSM and NYC Open Data. State in the submission that the project was built during the hackathon, from scratch, inspired by the author's earlier simulation-agent method (no code reused).
