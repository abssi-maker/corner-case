# Corner Case

**Test the fix before the next crash.**

Pedestrians keep getting hit at the same Manhattan corners. Cities usually fix a corner after
people are hurt, then wait years of crash counts to learn whether the fix worked. Corner Case lets a
city street-safety engineer test changes on a real corridor, **7th Avenue from W 30th St to Central
Park South**, before spending money:

1. **Where people get hurt**: every pedestrian crash since 2023 from NYC crash records, located by lat/lon.
2. **Watch the corner**: Meta **SAM 3.1** tracks every person and car in real street video; **Muse Spark 1.3** writes timestamped notes, and SAM cross-checks each one.
3. **Rebuild it**: a deterministic corridor simulator (signals, LPIs, turning cars, walkers) measures near-misses as post-encroachment time.
4. **Can we trust it?**: the simulator, which never sees a crash, is scored against held-out 2025–26 crashes, next to simple baselines.
5. **Agents try fixes**: one **Muse Wonder** agent per intersection proposes changes, runs them in the simulator, and keeps or drops them; a Wonder coordinator rejects fixes that push risk to the next block.
6. **Stress the future**: the coordinator picks futures likely to break the plan (holiday crowds, shows letting out, rain at night, a bus lane, drivers ignoring turn bans); broken fixes go back to their agent for revision.
7. **The plan**: a printable brief: what to fund first, simulated benefit, added car delay, rough costs with sources found by **Muse Spark web search**, and an **after it's built** watcher (**Muse Glimmer**, open weights on Modal) that counts close calls on camera so the city learns in weeks, not years.

Every number on screen carries a **REAL**, **MEASURED**, **OPEN DATA**, **ASSUMPTION** or **SIMULATED**
badge with its source. Simulated near-misses are never presented as injuries.

## 60-second quick start (no API key needed)

```bash
uv sync
uv run uvicorn cornercase.api:app --port 8000
# open http://localhost:8000 and press "Play the story"
```

The recorded run in `runs/` replays all seven steps with no model calls. To print a summary of it:

```bash
uv run python -m cornercase.run --replay runs/$(cat runs/latest.txt).json
```

### Live mode (needs `META_API_KEY` in `.env`)

```bash
uv run python -m cornercase.vision.pipeline   # SAM 3.1 + Muse Spark on the footage (~$0.21)
uv run python -m cornercase.run               # 30 Wonder corner agents + coordinator + Spark costs
uv run python -m cornercase.evidence          # trust check -> evidence/trust_check.json
uv run python -m cornercase.agent_value       # Wonder vs random vs greedy -> evidence/agent_value.json
uv run pytest -q
```

Or press **Run live** in the UI (progress streams over SSE). Model IDs are resolved from
`GET /v1/models` at startup and recorded in every run file.

## Architecture

```
 NYC Open Data / MTA ─┐                         ┌─ data/corridor.json (30 corners, sources)
 (centerline, LPI,    ├─ cornercase/data ───────┤
  counts, ridership)  │                         └─ crashes snapped by lat/lon (step 1, step 4 only)
                      │
 street video ── ffmpeg (6 fps, clock) ─┬─ SAM 3.1 image mode: every person/car/bus/taxi/road on 1 s keyframes
                                        ├─ SAM 3.1 video: box-prompt tracks seeded from them, 16 per request
                                        ├─ one-bit masks → foot points, asphalt-road surface; pan stabilisation
                                        └─ Muse Spark 1.3 (notes + concepts for SAM) ── SAM cross-check
                                                     │
                                        measured inputs (step 2)
                                                     ▼
            cornercase/sim  ── signals · walkers · vehicles · PET conflicts · interventions · futures
                 ▲   deterministic, seeded, common random numbers, coupled by turn-ban diversions
                 │ simulate() tool
   Muse Wonder ×30 corner agents ──► Muse Wonder coordinator ──► stress futures ──► revisions
                                                     │
                     Muse Spark web search (unit costs, citations) ──► brief (step 7)
                     Muse Glimmer on Modal (vLLM) ◄── per-interval SAM track summaries
                                                     │
                       runs/<id>.json  ──► FastAPI + plain JS UI ("Play the story", live SSE)
```

## Evidence

All numbers below come from the committed files in `evidence/` and the recorded run `runs/20261104-115932.json`.

### Trust check: does the simulator rank corners the way real crashes did?

The simulator never sees a crash. It is scored against the **82 pedestrian crashes from 2025-01-01 to 2026-10-31**, snapped to the 30 corners within 40 m. The primary metric (severity-weighted simulated near-misses per hour) was fixed before scoring, and no parameter was tuned on crashes.

| Method | Spearman ρ | 95% CI | one-sided p | top-5 overlap | top-10 overlap |
|---|---|---|---|---|---|
| Simulator: severity-weighted near-misses per hour | +0.30 | -0.07 to +0.59 | 0.0586 | 1 of 5 | 6 of 10 |
| traffic volume alone | +0.42 | +0.01 to +0.75 | 0.013 | 2 of 5 | 8 of 10 |
| walker exposure alone (subway + demand + counts) | +0.27 | -0.07 to +0.56 | 0.0774 | 1 of 5 | 6 of 10 |
| crash history 2023-24 | +0.30 | -0.04 to +0.57 | 0.055 | 1 of 5 | 8 of 10 |

**Reading:** a weak but real signal. The simulator ranks corners about as well as the previous two years of crash history, and less well than traffic volume alone; the confidence interval includes zero. Without the one video-measured input (walking speed at W 45 St) ρ is +0.30 with 2 of 5 in the top 5. Corner Case therefore presents its plan as **what to pilot and measure**, not as a forecast, and the UI carries that caveat from step 4 to the brief.

### Agent value: is the Muse Wonder team better than simple search at the same budget?

Same simulator, seeds and objective (corridor risk, with a steep penalty above the 8% car-delay limit); random search and greedy one-change-at-a-time get exactly as many corridor simulations as the agents used.

| Method (same budget: 111 corridor simulations) | Risk cut | Added car delay | Corners changed | Objective (lower is better) |
|---|---|---|---|---|
| Muse Wonder agents | 12.5% | +4.5% | 13 | 890.34 |
| Random search | 2.3% | +1.2% | 4 | 994.26 |
| Greedy, one change at a time | 10.7% | +8.1% | 9 | 909.23 |

The Wonder plan cuts the most risk while staying inside the delay limit; greedy finds a smaller cut and exceeds the limit; random search barely moves.

### The recorded run

- Coordinator briefed 30 corner agents (priority, test budget, stretch target, focus); agents ran 111 simulations.
- 13 fixes selected for $99,750 of a $100,000 quarterly budget (the budget is a labelled assumption); 15 proposals rejected with reasons.
- Stress futures chosen by the coordinator: 30% ignore turn bans (holds), Broadway shows let out (holds), Rain at night (broke, revised, holds), Holiday crowds (holds).
- Plan: simulated near-misses 2,430 → 2,157 per hour, serious 671 → 516, added car delay +4.5%, cost $34,200–$191,800 (Muse Spark web search, cited per item).
- Run cost $1.82 in 79 Meta model calls; the vision pipeline cost $0.58.

### What SAM 3.1 measured on the W 45 St clip

- Image-mode detection on 1 s keyframes (video mode is capped at 16 objects per frame): up to **43 people in one frame**.
- Box-prompt tracking seeded from those detections in batches of 16: **136 people and 20 vehicles** (16 billboard faces excluded).
- Decoded one-bit masks: ground-contact points and SAM's "asphalt road" surface: 3 walkers on the road, 2 close calls with moving vehicles; walking speed 0.82 m/s from 52 straight-line walkers.
- Muse Spark wrote 7 timestamped notes (burned-in clock); SAM confirmed 6. Spark also proposed concepts for SAM to segment.


## What each Meta model does

| Model | Job | Why this model |
|---|---|---|
| SAM 3.1 | Image mode finds every person, car, bus, taxi and the asphalt road on 1 s keyframes; new detections seed box-prompt video tracks in batches of 16; decoded masks give foot points, "walker on the road" and close calls | Used the way Meta's docs describe: one noun phrase per request, image mode for crowds (video tracks at most 16 objects per frame), box prompts on later frames to catch arrivals, confidences and one-bit masks |
| Muse Spark 1.3 | Watches the clip (burned-in clock for timestamps) and notes behaviour boxes cannot show; every note is checked against SAM tracks. Also finds unit costs by web search with citations | Native video understanding; web search with citations |
| Muse Wonder | A lead coordinator briefs 30 corner agents (priority, test budget scaled to risk, stretch target, focus); agents call the simulator as a function tool in parallel, read results, keep or drop; the coordinator selects the best set within budget, rejects the rest with reasons, picks stress futures and sends broken fixes back (same conversation via `previous_response_id`) | Orchestrator-worker with explicit delegation; every structured output validated (Pydantic + semantic checks, one repair turn); kept claims are re-simulated by the system; checkpoints, timeouts and one fresh retry per agent |
| Muse Glimmer | After a fix is built: reads per-interval close-call summaries from the camera and writes the weekly near-miss log and alerts | Open weights, cheap to keep always on; served with vLLM on Modal (`modal/glimmer_serve.py`) |

## Limitations

- One 15-second night clip of one corner. It shows a plaza crowd and queued taxis, but nobody
  crossing the roadway, so crossing against the signal stays an assumption. Measured walking speed is
  applied at W 45 St only. More footage per corner would replace more assumptions.
- Traffic volume on 7th Ave comes from one DOT count (Times Square, 2022) assumed constant along the
  corridor; cross-street volumes are assumptions from lane counts. Every assumption is labelled in the UI.
- About 3 held-out crashes per corner: every ranking metric is noisy; read the confidence intervals.
- Near-misses are simulated post-encroachment times, not injuries. The plan is for choosing what to
  pilot and measure, not a forecast of injuries prevented.
- The Glimmer watcher needs a Modal deployment (`modal deploy modal/glimmer_serve.py`); until then
  the run file records it as not deployed and the brief shows the deterministic close-call counter.

## Licences and attributions

- Code: MIT (`LICENSE`).
- Footage: "TimesSquare-NewYork-may25-2017" by Nesnad, Wikimedia Commons, CC BY-SA 4.0 (`data/footage/ATTRIBUTION.md`).
- Crashes, street centreline, signals, plazas, counts: NYC Open Data, NYC Open Data Terms of Use (`data/opendata/SOURCES.md`).
- Subway entrances and ridership: MTA, NY State Open Data.
- Unit-cost sources are cited per item in each run file and in the brief.

Built from scratch during the Meta Global AI Developer Hackathon, inspired by the author's earlier
simulation-agent method (an agent proposes, a deterministic simulator measures, stress tests break
it, the agent revises). No code was reused.
