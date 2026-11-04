/* Corner Case UI. A fixed 1920x1080 artboard scaled to the window: the visual on the left, a
   rail on the right that reads question -> answer -> action, and the 30-corner strip of 7th
   Avenue along the bottom of every screen, re-drawn for what each step adds to the plan. */
"use strict";

const STEPS = [
  { n: 1, label: "Where people get hurt", q: "Which corners on 7th Ave hurt the most people?" },
  { n: 2, label: "Watch the corner", q: "What actually happens at the corner?" },
  { n: 3, label: "Rebuild it", q: "Can we replay this corridor in a computer?" },
  { n: 4, label: "Can we trust it?", q: "Does the simulator know where crashes really happen?" },
  { n: 5, label: "Agents try fixes", q: "What should we change, corner by corner?" },
  { n: 6, label: "Stress the future", q: "Does the plan still hold when things change?" },
  { n: 7, label: "The plan", q: "What do we fund, and how will we know it worked?" },
];
const PLAY_SECONDS = { 0: 9, 1: 11, 2: 16, 3: 14, 4: 13, 5: 18, 6: 14, 7: 14 };
const NEXT = { 1: "Watch", 2: "Rebuild it", 3: "Can we trust it?", 4: "Choose fixes with it", 5: "Stress the future", 6: "See the plan" };

const S = { run: null, step: 1, corner: "w45", playing: false, timers: [], anim: null,
            trustMode: "with_measured_inputs", agentSel: null, futSel: null, agentP: 1 };

const $ = (sel, el = document) => el.querySelector(sel);
const $$ = (sel, el = document) => [...el.querySelectorAll(sel)];
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmt = (x, d = 0) => (x == null || Number.isNaN(+x) ? "–" : Number(x).toLocaleString("en-US", { minimumFractionDigits: d, maximumFractionDigits: d }));
const money = (x) => "$" + fmt(Math.round(x / 100) * 100);
const kmoney = (x) => (x >= 1000 ? "$" + fmt(x / 1000, x >= 100000 ? 0 : 1) + "k" : money(x));
const sign = (x) => (x >= 0 ? "+" : "−") + fmt(Math.abs(x), 1);
const pct = (x, d = 1) => fmt(100 * x, d) + "%";
const node = (id) => S.run.step1.nodes.find((n) => n.id === id) || {};
const short = (name) => String(name || "").replace("7 Av & ", "");
const nameOf = (id) => short(node(id).name || id);
const colName = (n) => (n.street_num === 59 ? "CPS" : String(n.street_num));
const clock = (t) => `0:${String(Math.floor(t)).padStart(2, "0")}`;
const hit = (id) => node(id).people_hit || 0;

const SOURCES = {
  real: "REAL: police crash records and city open data (street geometry, signals, counts). What actually happened.",
  meas: "MEASURED: measured from street video by Meta SAM 3.1 (one 15 s clip at W 45 St), checked against Muse Spark's notes.",
  sim: "SIMULATED: output of the corridor simulator. Simulated near-misses are not injuries.",
  asm: "ASSUMPTION: a labelled guess, used where no data exists.",
};
function badge(kind, src) {
  const map = { real: ["REAL", "real"], sim: ["SIMULATED", "sim"], meas: ["MEASURED", "meas"], asm: ["ASSUMPTION", "asm"], open: ["REAL", "real"] };
  const [t, c] = map[kind] || map.real;
  return `<span class="badge ${c}" tabindex="0" data-src="${esc(src || SOURCES[c])}">${t}</span>`;
}
const kindBadge = (k, src) => badge(k === "measured" ? "meas" : k === "open-data" ? "open" : "asm", src);
const chip = (model, text) => `<span class="chip"><b>${esc(model)}</b> ${esc(text)}</span>`;
const simv = (a, b) => `<span class="simv" data-src="${esc(SOURCES.sim)}">${fmt(a)} → ${fmt(b)}</span>`;
const realv = (x) => `<span class="realv" data-src="${esc(SOURCES.real)}">${fmt(x)}</span>`;
function describe(ch, n) {
  ch = ch || {};
  const today = n ? node(n) : null, lpiNow = today && S.run.step3.inputs[n]?.find((i) => i.input === "lpi_s")?.v;
  const p = [];
  if (ch.lpi_s != null) p.push(lpiNow ? `Longer walk head start ${fmt(lpiNow)} → ${fmt(ch.lpi_s)} s` : `Walk head start ${fmt(ch.lpi_s)} s`);
  if (ch.turn_ban && ch.turn_ban !== "none") p.push(ch.turn_ban === "both" ? "Ban all turns" : `Ban ${ch.turn_ban} turns`);
  if (ch.hardened_centerline) p.push("Slower turns");
  if (ch.daylighting) p.push("Clear the corner");
  if (ch.split_phase) p.push("Separate turn phase");
  if (ch.cycle_s != null) p.push(`${ch.cycle_s} s cycle`);
  if (ch.ave_share != null) p.push(`${Math.round(ch.ave_share * 100)}% green to 7 Av`);
  return p.join(" + ") || "No change";
}
const clean = (o) => Object.fromEntries(Object.entries(o || {}).filter(([, v]) => v !== null && v !== undefined));
const HAND = `<svg viewBox="0 0 12 16" aria-hidden="true"><path d="M3 7V3.3a.9.9 0 0 1 1.8 0V7V1.9a.9.9 0 0 1 1.8 0V7V2.5a.9.9 0 0 1 1.8 0V7V3.6a.9.9 0 0 1 1.8 0v5.2l.6-1a.9.9 0 0 1 1.6.9L9.9 13c-.7 1.6-2 2.6-3.8 2.6C4 15.6 3 14.1 3 12.2z" fill="#E8590C"/></svg>`;
const WALKER = `<svg viewBox="0 0 10 16" aria-hidden="true"><circle cx="5.6" cy="1.9" r="1.8" fill="white"/><path d="M4.9 4.3 3 8.6l1.3.5 1-2.3.7 2.9-1.6 5.4 1.5.4 1.6-4.9-.6-2.9 1.4 1.6 1.1-.8-2.2-3.1z" fill="white"/></svg>`;
const sig = (kind) => `<span class="sig ${kind}">${kind === "hand" ? HAND : WALKER}</span>`;

/* ---------------------------------------------------------------- artboard scaling */
function fit() {
  const s = Math.min(window.innerWidth / 1920, window.innerHeight / 1080);
  $("#app").style.transform = `translate(-50%, -50%) scale(${s})`;
}
window.addEventListener("resize", fit);

/* ---------------------------------------------------------------- boot */
async function boot() {
  fit();
  bindTips();
  $("#playBtn").onclick = () => (S.playing ? stopPlay() : startPlay());
  $("#liveBtn").onclick = startLive;
  $("#liveClose").onclick = () => $("#liveDlg").close();
  $("#runSel").onchange = (e) => loadRun(e.target.value);
  document.addEventListener("keydown", (e) => {
    if (e.target.matches("input, select, textarea") || $("#liveDlg").open) return;
    if (e.key === "ArrowRight") { stopPlay(); go(Math.min(7, S.step + 1)); }
    if (e.key === "ArrowLeft") { stopPlay(); go(Math.max(0, S.step - 1)); }
    if (e.key === " " && e.target === document.body) { e.preventDefault(); S.playing ? stopPlay() : startPlay(); }
  });
  try {
    const r = await (await fetch("/api/runs")).json();
    $("#liveBtn").disabled = !r.live_available;
    if (!r.live_available) $("#liveBtn").title = "Live mode needs META_API_KEY in .env; replay works without it";
    if (!r.runs.length) return stageMsg("empty", `<h2>No recorded run yet</h2><p>Record one with <code>uv run python -m cornercase.run</code>, or press <b>Run live</b>.</p>`);
    fillRuns(r.runs);
    const want = new URLSearchParams(location.search).get("run") || r.latest || r.runs[0].id;
    $("#runSel").value = want;
    await loadRun(want);
  } catch (e) {
    stageMsg("err", `<b>Could not load runs.</b> ${esc(e.message)}. Start the server with <code>uv run uvicorn cornercase.api:app</code>.`);
  }
}
function fillRuns(runs) {
  $("#runSel").innerHTML = runs.map((x) => `<option value="${x.id}">Run ${x.id}${x.cost_usd != null ? " · $" + x.cost_usd.toFixed(2) : ""}</option>`).join("");
}
function stageMsg(cls, html) { $("#stage").innerHTML = `<div class="${cls}">${html}</div>`; }

async function loadRun(id) {
  stopAnim();
  $("#stage").innerHTML = `<div class="loading"><div class="crosswalk"><i></i><i></i><i></i><i></i><i></i></div><p>Loading run ${esc(id)}…</p></div>`;
  const res = await fetch(`/api/runs/${encodeURIComponent(id)}`);
  if (!res.ok) return stageMsg("err", `Run ${esc(id)} was not found.`);
  S.run = await res.json();
  S.corner = S.run.default_corner || "w45";
  S.agentSel = null; S.futSel = null;
  const st = parseInt(new URLSearchParams(location.search).get("step") || "1", 10);
  go(st >= 0 && st <= 7 ? st : 1);
}

/* ---------------------------------------------------------------- navigation */
function renderStepper() {
  $("#stepper").innerHTML = STEPS.map((s) =>
    `<button class="step ${s.n === S.step ? "on" : s.n < S.step ? "done" : ""}" data-n="${s.n}" type="button" aria-current="${s.n === S.step ? "step" : "false"}"><span class="n">${s.n}</span><span class="l">${s.label}</span>${s.n < S.step ? `<span class="tick" aria-label="done">✓</span>` : ""}</button>`).join("");
  $$("#stepper .step").forEach((b) => (b.onclick = () => { stopPlay(); go(+b.dataset.n); }));
}

function go(n) {
  stopAnim();
  S.step = n;
  $("#app").classList.toggle("title", n === 0);
  renderStepper();
  const v = SCREENS[n]();
  if (n === 0) {
    $("#stage").innerHTML = v.full;
  } else {
    const s = STEPS[n - 1];
    $("#stage").innerHTML = `
    <section class="visual" aria-label="${esc(s.label)}">${v.visual}</section>
    <aside class="rail">
      <div class="eyebrow">Step ${n} of 7 · ${s.label}</div>
      <h1>${s.q}</h1>
      ${v.answer}
      ${v.cap ? `<p class="cap">${v.cap}</p>` : ""}
      ${v.why ? `<p class="why">${v.why}</p>` : ""}
      ${v.rail || ""}
      <div class="spacer"></div>
      ${v.source ? `<p class="src">Source: ${esc(v.source)}</p>` : ""}
      <div class="action">${v.extra || ""}${n < 7 ? `<button class="btn primary" id="nextBtn" type="button">${v.action || NEXT[n]} →</button>` : `<button class="btn primary noprint" id="printBtn" type="button">Print the brief</button>`}</div>
    </aside>`;
  }
  renderStrip(n);
  if ($("#nextBtn")) $("#nextBtn").onclick = () => { stopPlay(); go(n + 1); };
  if ($("#printBtn")) $("#printBtn").onclick = () => window.print();
  v.mount && v.mount();
  const u = new URL(location); u.searchParams.set("step", n); u.searchParams.set("run", S.run.id); history.replaceState(null, "", u);
}
function confidence() {
  const sim = S.run.step4.with_measured_inputs.methods.find((m) => m.id === "simulator");
  return `<div class="conf">Confidence: <b>weak</b> (step 4: ${sim.top5} of 5, ρ ${sim.spearman >= 0 ? "+" : ""}${sim.spearman.toFixed(2)}). Pilot, then measure.</div>`;
}
function stopAnim() { if (S.anim) { S.anim.stop(); S.anim = null; } }

/* ---------------------------------------------------------------- the corridor strip */
function planState() {
  const r = S.run, rv = r.step5.coordinator.review;
  const accepted = new Set(rv.accepted), rejected = new Set(rv.rejected.map((x) => x.id));
  const final = new Set(r.step7.items.map((i) => i.id));
  const revised = new Set(r.step6.stress.flatMap((s) => s.revisions.map((x) => x.node)));
  const waits = new Set(r.step6.stress.flatMap((s) => (s.budget_drops || []).map((x) => x.node)));
  const added = new Set((r.step6.backfill || []).map((x) => x.node));
  return { accepted, rejected, final, revised, waits, added };
}
function renderStrip(n) {
  const r = S.run, nodes = r.step1.nodes;
  const ps = planState();
  let t2 = "", t3 = "", col;
  const maxHit = Math.max(...nodes.map((x) => x.people_hit), 1);
  const top6 = new Set([...nodes].sort((a, b) => b.people_hit - a.people_hit).slice(0, 6).map((x) => x.id));
  if (n <= 1) {
    t2 = `${n === 0 ? fmt(r.step1.headline.people_hit) + " people hit walking since 2023" : "Showing: people hit since 2023"}`;
    t3 = badge("real", r.step1.source);
    col = (x) => `<div class="bar real" style="height:${(92 * x.people_hit) / maxHit}%">${top6.has(x.id) && x.id !== S.corner ? `<span class="v" style="top:0">${x.people_hit}</span>` : ""}</div>`;
  } else if (n === 2) {
    const sam = r.step2.measurements.sam;
    t2 = "Showing: what street video measured"; t3 = "Other corners: city counts and labelled assumptions";
    col = (x) => (x.id === (r.default_corner || "w45") ? `<div class="blk meas">${fmt(sam.people_tracked)}<br>people</div>` : `<div class="tickdash"></div>`);
  } else if (n === 3) {
    const t = r.step3.today.nodes, mx = Math.max(...nodes.map((x) => t[x.id].near_miss_ph));
    t2 = "Showing: simulated near-misses an hour"; t3 = badge("sim");
    col = (x) => `<div class="bar sim" style="height:${(92 * t[x.id].near_miss_ph) / mx}%"></div>`;
  } else if (n === 4) {
    const tc = r.step4[S.trustMode], real = Object.fromEntries(tc.lists.real.map((x) => [x.id, x.crashes]));
    const sim = Object.fromEntries(tc.lists.simulator.map((x) => [x.id, x.value]));
    const mr = Math.max(...Object.values(real), 1), ms = Math.max(...Object.values(sim), 1);
    const rt = new Set(tc.lists.real.slice(0, 5).map((x) => x.id)), st = new Set(tc.lists.simulator.slice(0, 5).map((x) => x.id));
    t2 = "Showing: where the simulator agrees with reality";
    t3 = `<span>${badge("real")} held-out crashes</span><span>${badge("sim")} simulator</span><span><b>✓</b> in both top 5s</span>`;
    col = (x) => `<div class="bar real half" style="height:${(88 * (real[x.id] || 0)) / mr}%"></div><div class="bar sim half" style="height:${(88 * (sim[x.id] || 0)) / ms}%"></div>${rt.has(x.id) && st.has(x.id) ? `<span class="revmark" style="color:var(--ink);font-size:15px">✓</span>` : ""}`;
  } else if (n === 5) {
    const live = S.agentP < 1 ? agentStates(S.agentP) : null;
    t2 = "Showing: the fixes chosen";
    t3 = `<span class="tag sel">selected</span><span class="tag rej">rejected</span><span class="tag drop">dropped</span>`;
    col = (x) => {
      if (live) { const s = live[x.id]?.state || "idle"; return `<div class="blk ${["kept", "dropped", "rejected"].includes(s) ? { kept: "sel", dropped: "drop", rejected: "rej" }[s] : s === "idle" ? "drop" : "busy"}"></div>`; }
      return `<div class="blk ${ps.accepted.has(x.id) ? "sel" : ps.rejected.has(x.id) ? "rej" : "drop"}"></div>`;
    };
  } else if (n === 6) {
    t2 = "Showing: does each fix hold";
    t3 = ps.waits.size ? `<span class="tag sel">✓</span> holds <span class="tag drop">waits</span> for next quarter, to stay in budget` : `<span class="tag sel">✓</span> holds in every future`;
    col = (x) => (ps.final.has(x.id) ? `<div class="blk sel">✓</div>${ps.revised.has(x.id) ? `<span class="revmark">revised</span>` : ps.added.has(x.id) ? `<span class="revmark" style="color:var(--ink)">added</span>` : ""}`
      : ps.waits.has(x.id) ? `<div class="blk drop"></div><span class="revmark">waits</span>` : "");
  } else {
    const items = r.step7.items;
    t2 = "Showing: the funded plan";
    t3 = `<span class="tag sel">1 2 3</span> build first <span class="tag drop" style="border-style:solid;color:var(--ink)">then</span> after camera counts`;
    col = (x) => { const it = items.find((i) => i.id === x.id); if (!it) return ""; const w = it.wave ?? (it.priority <= 3 ? 1 : 2);
      return w === 1 ? `<div class="blk sel">${it.priority}</div>` : `<div class="blk then">then</div>`; };
  }
  $("#strip").innerHTML = `<div class="lab"><div class="t1">7th Avenue · ${nodes.length} corners</div><div class="t2">${t2}</div><div class="t3">${t3}</div></div>
    <div class="cols">${nodes.map((x) => `<button class="col ${x.id === S.corner ? "cur" : ""}" type="button" data-id="${x.id}" data-src="${esc(x.name)}: ${x.people_hit} people hit since 2023"><span class="top2">${x.id === S.corner ? `<span class="plate">${esc(short(x.name))}</span>` : ""}</span><span class="body">${col(x)}</span><span class="nm">${colName(x)}</span></button>`).join("")}</div>`;
  $$("#strip .col").forEach((b) => (b.onclick = () => { stopPlay(); S.corner = b.dataset.id; if (S.step === 5) S.agentSel = b.dataset.id; go(Math.max(1, S.step)); }));
}

/* ================================================================ 0 · title card (Play mode) */
function timeline(compact) {
  const dots = Array.from({ length: compact ? 7 : 20 }, (_, i) => `<span class="dot" style="left:${(compact ? 120 : 170) + i * (compact ? 62 : 60)}px"></span>`).join("");
  const W = compact ? 540 : 1500;
  return `<div class="tl"><div class="who">${compact ? "Today" : "TODAY"}</div><div class="row">
      <span class="seg build" style="left:0;width:${compact ? 70 : 100}px">Build</span><span class="line" style="left:${compact ? 70 : 100}px;width:${W - (compact ? 70 : 100)}px"></span>${dots}<span class="end" style="left:${W}px"></span>
      ${compact ? `<span class="note1" style="left:${W + 14}px"><b>about 3 years</b> of new crashes</span>` : `<span class="note1" style="left:${W - 260}px">know if it worked · <b>about 3 years</b></span><span class="note1" style="left:120px">new crashes while we wait</span>`}</div>
    <div class="who">${compact ? "Corner Case" : "CORNER CASE"}</div><div class="row">
      <span class="seg test" style="left:0;width:${compact ? 110 : 150}px">Test first</span><span class="seg build" style="left:${compact ? 110 : 150}px;width:${compact ? 70 : 100}px">Build</span><span class="seg cam" style="left:${compact ? 180 : 250}px;width:${compact ? 150 : 190}px">camera counts</span><span class="end" style="left:${compact ? 330 : 440}px"></span>
      <span class="note1" style="left:${compact ? 344 : 300}px${compact ? ";top:12px" : ""}">${compact ? "<b>weeks</b>, then fund the rest" : "know · <b>weeks</b>"}</span></div></div>`;
}
function step0() {
  return {
    full: `<div class="titlecard">
      <div class="eyebrow">7th Avenue · W 30th St to Central Park South · Manhattan</div>
      <p class="lede">Today a fix at a dangerous corner is judged by waiting for new crashes.</p>
      <h1>3 years of crashes →<br>weeks of camera counts.</h1>
      ${timeline(false)}
      <div class="credits"><span>SAM 3.1 measures the street from ordinary video</span><span>Muse Wonder agents test every fix on every corner</span><span>Muse Glimmer, open weights, watches every changed corner</span></div>
      <div class="action" style="margin-top:22px"><button class="btn primary" id="startBtn" type="button">Step 1: Where people get hurt →</button></div>
    </div>`,
    mount() { $("#startBtn").onclick = () => { if (!S.playing) go(1); }; },
  };
}

/* ================================================================ 1 · where people get hurt */
function step1() {
  const d = S.run.step1, h = d.headline;
  const top = [...d.nodes].sort((a, b) => b.people_hit - a.people_hit || b.killed - a.killed).slice(0, 5);
  const sel = node(S.corner);
  const hasVideo = S.corner === (S.run.default_corner || "w45");
  return {
    visual: `<div class="mapbox"><div style="display:flex;flex-direction:column;min-height:0">${mapSVG(d)}
      <div class="legend"><span><i style="background:var(--risk)"></i>crash that hurt someone walking</span><span><i style="background:var(--ink)"></i>a person was killed</span></div></div>
      <div class="worst"><div class="h3">Most people hit ${badge("real", d.source)}</div>${top.map((n) => `<div class="wl" data-id="${n.id}"><span>${n.id === S.corner ? `<span class="plate">${esc(short(n.name))}</span>` : esc(short(n.name))}</span><b>${n.people_hit}${n.killed ? ` · ${n.killed} killed` : ""}</b></div>`).join("")}</div></div>`,
    answer: `<div class="answer">${fmt(h.people_hit)}</div>`,
    cap: `people hit walking since 2023, ${fmt(h.killed)} killed ${badge("real", d.source)}`,
    why: `Every dot is a police crash record. ${hasVideo ? `<b>${esc(short(sel.name))}</b> is selected because it is the corner we have street video for (${sel.people_hit} people hit).` : `<b>${esc(short(sel.name))}</b>: ${sel.people_hit} people hit.`}`,
    action: `Watch ${esc(short(node(S.run.default_corner || "w45").name))}`,
    source: "NYC Open Data · Motor Vehicle Collisions (h9gi-nx95), Jan 2023 – Oct 2026, located by latitude/longitude",
    mount() {
      $$(".worst .wl, .map .hit").forEach((el) => (el.onclick = () => { S.corner = el.dataset.id; go(1); }));
      if (S.playing) animateDots();
    },
  };
}

/* the real street geometry: latitude/longitude projected flat, north up; 6th and 8th Avenues
   drawn faintly ~280 m either side so it reads as the Manhattan grid */
function mapSVG(d) {
  const W = 800, H = 690, pad = 40, AVE = 280;
  const lat0 = d.nodes.reduce((s, n) => s + n.lat, 0) / d.nodes.length, kx = Math.cos((lat0 * Math.PI) / 180) * 111320, ky = 110540;
  const lons = d.nodes.map((n) => n.lon), lats = d.nodes.map((n) => n.lat);
  const mx = (lon) => (lon - Math.min(...lons)) * kx, my = (lat) => (Math.max(...lats) - lat) * ky;
  const spanX = mx(Math.max(...lons)), spanY = my(Math.min(...lats));
  const sc = Math.min((W - 2 * pad) / (spanX + 2 * AVE), (H - 2 * pad) / spanY);
  const ox = (W - spanX * sc) / 2, oy = (H - spanY * sc) / 2;
  const P = (lat, lon) => [ox + mx(lon) * sc, oy + my(lat) * sc];
  const ns = [...d.nodes].sort((a, b) => a.street_num - b.street_num);
  const [ax, ay] = P(ns[0].lat, ns[0].lon), [bx, by] = P(ns[ns.length - 1].lat, ns[ns.length - 1].lon);
  const L = Math.hypot(bx - ax, by - ay), ux = (bx - ax) / L, uy = (by - ay) / L, px = -uy, py = ux; // p points east of the avenue
  const off = (x, y, m) => [(x + px * m * sc).toFixed(1), (y + py * m * sc).toFixed(1)];
  const halo = 'paint-order="stroke" stroke="var(--paper)" stroke-width="6" stroke-linejoin="round"';
  let s = `<svg class="map" viewBox="0 0 ${W} ${H}" preserveAspectRatio="xMidYMid meet" role="img" aria-label="Map of 7th Avenue with every pedestrian crash since 2023">`;
  for (const n of ns) { const [x, y] = P(n.lat, n.lon), [x1, y1] = off(x, y, -AVE - 30), [x2, y2] = off(x, y, AVE + 30); s += `<line class="ctx" x1="${x1}" y1="${y1}" x2="${x2}" y2="${y2}"/>`; }
  for (const m of [-AVE, AVE]) { const [x1, y1] = off(ax, ay, m), [x2, y2] = off(bx, by, m); s += `<line class="ctx" style="stroke-width:5" x1="${x1}" y1="${y1}" x2="${x2}" y2="${y2}"/>`; }
  const ts = ns.filter((n) => n.street_num >= 43 && n.street_num <= 47);
  if (ts.length) {
    const a = P(ts[0].lat, ts[0].lon), b = P(ts[ts.length - 1].lat, ts[ts.length - 1].lon), w = 70 * sc;
    s += `<polygon class="plaza" points="${[[a[0] + px * w, a[1] + py * w], [b[0] + px * w, b[1] + py * w], [b[0] - px * w, b[1] - py * w], [a[0] - px * w, a[1] - py * w]].map((q) => q.map((v) => v.toFixed(1)).join(",")).join(" ")}"/>`;
    const m = P((ts[0].lat + ts[ts.length - 1].lat) / 2, (ts[0].lon + ts[ts.length - 1].lon) / 2), [x, y] = off(m[0], m[1], -150);
    s += `<text class="lbl p" x="${x}" y="${y}" text-anchor="middle" ${halo}>Times Square</text>`;
  }
  const penn = ns.find((n) => n.street_num === 32);
  if (penn) { const [x0, y0] = P(penn.lat, penn.lon), [x, y] = off(x0, y0, -150); s += `<text class="lbl p" x="${x}" y="${y}" text-anchor="middle" ${halo}>Penn Station</text>`; }
  s += `<line class="ave" x1="${ax}" y1="${ay}" x2="${bx}" y2="${by}"/>`;
  s += `<text class="lbl" x="${(ax - ux * 30).toFixed(1)}" y="${(ay - uy * 30 + 6).toFixed(1)}" text-anchor="middle" ${halo}>7th Ave</text>`;
  for (const n of ns) {
    const [x, y] = P(n.lat, n.lon);
    if ([30, 34, 42, 53, 59].includes(n.street_num) && n.id !== S.corner) {
      const [lx, ly] = off(x, y, 45);
      s += `<text class="lbl" x="${lx}" y="${(+ly + 6).toFixed(1)}" ${halo}>${n.street_num === 59 ? "Central Park South" : `W ${n.street_num} St`}</text>`;
    }
    s += `<circle class="hit" data-id="${n.id}" cx="${x.toFixed(1)}" cy="${y.toFixed(1)}" r="16"><title>${esc(n.name)}: ${n.people_hit} people hit, ${n.killed} killed</title></circle>`;
  }
  d.crashes.forEach((c, i) => {
    const [x, y] = P(c.lat, c.lon);
    s += `<circle class="crash ${c.killed ? "k" : ""}" data-i="${i}" cx="${x.toFixed(1)}" cy="${y.toFixed(1)}" r="${c.killed ? 8 : 5}"><title>${esc(c.date)}: ${c.injured} injured${c.killed ? `, ${c.killed} killed` : ""}. ${esc(c.factor || "")}${c.vehicle ? " (" + esc(c.vehicle) + ")" : ""}</title></circle>`;
  });
  const sel = ns.find((n) => n.id === S.corner);
  if (sel) {
    const [x, y] = P(sel.lat, sel.lon), [tx, ty] = off(x, y, 45).map(Number), t = short(sel.name), w = 16 + t.length * 10.5;
    s += `<g><rect x="${tx.toFixed(1)}" y="${(ty - 15).toFixed(1)}" width="${w}" height="30" rx="4" fill="var(--sign)"/><rect x="${(tx + 2).toFixed(1)}" y="${(ty - 13).toFixed(1)}" width="${w - 4}" height="26" rx="3" fill="none" stroke="#fff" stroke-width="1"/><text x="${(tx + w / 2).toFixed(1)}" y="${(ty + 6).toFixed(1)}" text-anchor="middle" style="font:700 17px var(--font);fill:#fff">${esc(t)}</text></g>`;
  }
  return s + `</svg>`;
}
function animateDots() {
  const dots = $$(".map .crash");
  dots.sort((a, b) => S.run.step1.crashes[a.dataset.i].date.localeCompare(S.run.step1.crashes[b.dataset.i].date));
  dots.forEach((el) => (el.style.opacity = 0));
  dots.forEach((el, k) => S.timers.push(setTimeout(() => (el.style.opacity = 1), 400 + (k * 6000) / dots.length)));
}

/* ================================================================ 2 · watch the corner */
function step2() {
  const v = S.run.step2, m = v.measurements, sam = m.sam, sp = m.spark;
  const evs = sp.events || [], used = m.sim_inputs || {};
  const conf = evs.filter((e) => e.sam_agrees).length;
  const dur = m.clip.duration_s;
  const calls = sam.close_call_times || [];
  const c = node(S.run.default_corner || "w45");
  return {
    visual: `<div class="watch">
      <div class="vid"><video id="vid" src="/static/${v.video}" muted playsinline preload="auto"></video><canvas id="ov" width="932" height="1656"></canvas>
        <div class="hud"><span>SAM 3.1 · <b id="hudN">0</b> people in view</span><span id="hudRoad" hidden>walker on the road</span></div>
        <div class="vctl"><button id="vplay" type="button">Play</button><div class="scrub" id="scrub"><div class="rail2"></div>${evs.map((e) => `<div class="mk" style="left:${Math.min(100, (100 * e.t_s) / dur)}%"></div>`).join("")}${calls.map((t) => `<div class="cc" style="left:${(100 * t) / dur}%"></div>`).join("")}<div class="head" id="head"></div></div><span class="vtime" id="vt">0:00</span></div>
      </div>
      <div class="wcol">
        <div><div class="h2">What the video measured</div>
        <ul class="mrows">
          <li>${badge("meas", "SAM 3.1: a walker and a moving vehicle box within a car length")}<span>Close calls with moving cars</span><b>${fmt(sam.close_calls)}</b></li>
          <li>${badge("meas", "foot point from each SAM mask inside SAM's 'asphalt road' mask")}<span>Walkers standing on the road</span><b>${fmt(sam.people_in_roadway)}</b></li>
          <li>${badge("meas", used.walk_speed_mps?.source || "SAM 3.1 tracks scaled by body height")}<span>Walking speed (crowded)</span><b>${sam.walk_speed_mps_median ? fmt(sam.walk_speed_mps_median, 2) + " m/s" : "–"}</b></li>
          <li>${badge("meas", "SAM 3.1 tracks over 15 s; the camera pans; 16 faces on billboards excluded")}<span>People tracked · vehicles</span><b>${fmt(sam.people_tracked)} · ${fmt(sam.vehicles_tracked)}</b></li>
        </ul></div>
        <div style="min-height:0;overflow:hidden"><div class="h2">Muse Spark's notes, checked by SAM</div>
        <ul class="notes" id="notes">${evs.filter((e) => e.t_s <= dur).slice(0, 4).map((e) => `<li data-t="${e.t_s}"><span class="ts">${clock(e.t_s)}</span><span>${esc(e.note)}<div class="agree ${e.sam_agrees ? "yes" : "no"}">${e.sam_agrees ? `SAM sees it too: ${e.sam_people} people, ${e.sam_vehicles} vehicles in view` : "SAM cannot confirm"}</div></span></li>`).join("")}</ul></div>
        <div class="chips">${chip("SAM 3.1", "masks and tracks")}${chip("Muse Spark 1.3", `${evs.length} notes, ${conf} confirmed by SAM`)}</div>
      </div></div>`,
    answer: `<div class="answer">${fmt(sam.close_calls)} <small>close calls</small></div>`,
    cap: `in 15 seconds at ${esc(short(c.name))}, with ${fmt(sam.people_in_roadway)} people standing on the road ${badge("meas")}`,
    why: `A close call: a walker and a moving car within a car length. Measured from ordinary street video; the walking speed replaces a guess in the simulator.`,
    source: "Footage: “TimesSquare-NewYork-may25-2017”, Nesnad, Wikimedia Commons, CC BY-SA 4.0",
    mount: mountVideo,
  };
}

/* ================================================================ 3 · rebuild it */
const PRETTY = {
  ped_vph: ["Walkers crossing", (v) => fmt(v) + " an hour"], ave_vph: ["7 Av cars", (v) => fmt(v) + " an hour"],
  walk_speed_mps: ["Walking speed", (v) => fmt(v, 2) + " m/s"], lpi_s: ["Walk head start today", (v) => fmt(v) + " s"],
  cycle_s: ["Signal cycle", (v) => fmt(v) + " s"], noncompliance: ["Cross against DON'T WALK", (v) => fmt(v * 100) + "%"],
  detect_turn: ["Turning driver sees walker", (v) => fmt(v * 100) + "%"], turn_speed_mps: ["Turning speed", (v) => fmt(v, 1) + " m/s"],
};
function step3() {
  const st = S.run.step3, today = st.today, nodes = S.run.step1.nodes;
  const n = node(S.corner), r = today.nodes[n.id];
  const order = { measured: 0, "open-data": 1, assumption: 2 };
  const all = st.inputs[n.id] || [];
  const rows = all.filter((i) => PRETTY[i.input]).sort((a, b) => order[a.kind] - order[b.kind]);
  const cnt = (kk) => all.filter((i) => i.kind === kk).length;
  return {
    visual: `<div class="simgrid">
      <canvas class="simc" id="simc" width="1656" height="1656" aria-label="Top-down simulation of ${esc(n.name)}"></canvas>
      <div style="min-height:0"><div class="h2">What the simulator was given for ${esc(short(n.name))}</div>
        <p class="muted" style="margin:0 0 10px;font-size:16px">${cnt("measured")} measured from video · ${cnt("open-data")} from city data · ${cnt("assumption")} labelled assumptions · hover a badge for its source</p>
        <ul class="inputs">${rows.slice(0, 8).map((i) => `<li><span>${PRETTY[i.input][0]}</span><b>${PRETTY[i.input][1](i.v)}</b>${kindBadge(i.kind, i.source)}</li>`).join("")}</ul>
        <div class="slegend"><span><i style="background:var(--ink);border-radius:50%"></i>walker</span><span><i style="background:#fff;border:1.5px solid var(--ink)"></i>car</span><span><i style="border:2.5px solid var(--risk);border-radius:50%"></i>near-miss</span><span><i style="background:#fff;border:1px solid var(--ink)"></i>WALK</span><span><i style="background:var(--risk)"></i>DON'T WALK</span></div>
      </div></div>`,
    answer: `<div class="answer">${fmt(today.totals.near_miss_ph)}</div>`,
    cap: `simulated near-misses an hour on the corridor, weekday 5–6 pm ${badge("sim", "corridor simulator: 1 hour × 3 seeds, common random numbers")}`,
    why: `A near-miss is a walker and a car at the same spot less than 1.5 seconds apart: the simulator's version of a close call. Not an injury.`,
    rail: `<ul class="facts"><li><span>${esc(short(n.name))}</span><b>${fmt(r.near_miss_ph)} an hour ${badge("sim")}</b></li></ul>`,
    source: "Inputs: NYC DOT Centerline, signal and LPI lists, counts; MTA ridership; SAM 3.1 measurements.",
    mount() { S.anim = simAnim($("#simc"), st.traces[n.id], n); },
  };
}

/* ================================================================ 4 · can we trust it? */
function step4() {
  const tc = S.run.step4[S.trustMode];
  const sim = tc.lists.simulator.slice(0, 5), real = tc.lists.real.slice(0, 5);
  const rowH = 56;
  let lines = "";
  sim.forEach((s, i) => { const j = real.findIndex((r) => r.id === s.id); if (j >= 0) lines += `<line x1="0" y1="${i * rowH + 28}" x2="210" y2="${j * rowH + 28}" stroke="#111" stroke-width="4" vector-effect="non-scaling-stroke"/>`; });
  const X = (r) => `${50 + r * 50}%`;
  const meth = tc.methods, mv = (id) => meth.find((m) => m.id === id);
  const s = mv("simulator");
  const rho = (x) => `${x >= 0 ? "+" : ""}${x.toFixed(2)}`;
  return {
    visual: `<div class="trust">
      <div class="slope">
        <div><h3>Simulator's top 5 ${badge("sim", "severity-weighted simulated near-misses per hour (metric fixed before scoring)")}</h3><ol>${sim.map((x, i) => `<li><span class="rk sim">${i + 1}</span><span>${esc(short(x.name))}</span><span></span></li>`).join("")}</ol></div>
        <div style="padding-top:44px"><svg viewBox="0 0 210 ${rowH * 5}" height="${rowH * 5}" preserveAspectRatio="none" aria-hidden="true">${lines}</svg></div>
        <div><h3>Real crashes it never saw, ${tc.held_out.from.slice(0, 4)}–${tc.held_out.to.slice(2, 4)} ${badge("real", `NYC crashes ${tc.held_out.from} to ${tc.held_out.to}, within ${tc.held_out.snap_m} m of each corner`)}</h3><ol>${real.map((x, i) => `<li><span class="rk real">${i + 1}</span><span>${esc(short(x.name))}</span><span class="num">${x.crashes} crashes</span></li>`).join("")}</ol></div>
      </div>
      <div><div class="h3">How each way of ranking corners does (rank correlation with the held-out crashes, 95% interval)</div>
      <div class="forest">
        <div class="h">Method</div><div class="h" style="text-align:center">95% interval · the line is 0</div><div class="h" style="text-align:right">Top 5 found</div>
        ${meth.map((m) => `<div>${esc(m.label.replace("Baseline: ", ""))} <b>${rho(m.spearman)}</b></div><div><div class="ci"><span class="w ${m.id === "simulator" ? "sim" : ""}" style="left:${X(m.ci95[0])};width:calc(${X(m.ci95[1])} - ${X(m.ci95[0])})"></span><span class="d ${m.id === "simulator" ? "sim" : ""}" style="left:${X(m.spearman)}"></span></div></div><div class="num" style="text-align:right">${m.top5} of 5</div>`).join("")}
      </div></div></div>`,
    answer: `<div class="answer verdict">Weak at ranking corners.</div>`,
    cap: `${s.top5} of the 5 worst real corners found; rank correlation ${rho(s.spearman)} (${rho(s.ci95[0])} to ${rho(s.ci95[1])}) ${badge("sim", "Spearman rank correlation with held-out crashes; bootstrap over corners")}`,
    why: `<b>So the plan ranks corners by people hit, and uses the simulator to choose the fix at each corner</b> and to check what it does to the next block. Past crashes rank no better (${rho(mv("history").spearman)}), but they are facts, not a model.`,
    rail: `<ul class="facts"><li><span>The simulator never saw a crash. It is scored on the ${fmt(tc.held_out.crashes)} crashes it was not given.</span>${badge("real")}</li></ul>`,
    extra: `<div class="toggle noprint" role="group" aria-label="Simulator inputs" data-src="With or without the walking speed measured from video"><button type="button" data-m="with_measured_inputs" class="${S.trustMode === "with_measured_inputs" ? "on" : ""}">With video</button><button type="button" data-m="without_measured_inputs" class="${S.trustMode === "without_measured_inputs" ? "on" : ""}">Without</button></div>`,
    source: `Held-out NYC crashes ${tc.held_out.from} to ${tc.held_out.to}. Spearman ρ; 95% bootstrap intervals over corners.`,
    mount() { $$(".toggle button").forEach((b) => (b.onclick = () => { S.trustMode = b.dataset.m; go(4); })); },
  };
}

/* ================================================================ 5 · agents try fixes */
function agentStates(p) {
  const tl = (S.run.timeline || []).filter((e) => e.type === "agent" || e.type === "coordinator");
  if (!tl.length) return null;
  const t0 = tl[0].at, t1 = tl[tl.length - 1].at, cut = t0 + (t1 - t0) * p;
  const st = {};
  for (const e of tl) {
    if (e.at > cut) break;
    if (e.type === "agent") st[e.node] = { ...(st[e.node] || {}), state: e.state, last: e };
    if (e.type === "coordinator" && e.state === "rejected") st[e.node] = { ...(st[e.node] || {}), state: "rejected" };
  }
  return st;
}
function keptTest(id) {
  const ag = S.run.step5.agents[id], d = ag?.decision;
  if (!d || d.decision !== "keep") return null;
  const want = JSON.stringify(clean(d.changes));
  return (ag.tests || []).find((t) => JSON.stringify(t.changes) === want && (t.result.future || "today") === "today")?.result || null;
}
function nmPair(id) {
  const r = keptTest(id), t = S.run.step3.today.nodes[id];
  if (r && r.near_misses_ph != null && r.near_misses_before != null) return [r.near_misses_before, r.near_misses_ph];
  if (r && r.near_miss_ph != null) return [t.near_miss_ph, r.near_miss_ph];
  const it = S.run.step7.items.find((i) => i.id === id);
  return it ? [it.near_misses_before, it.near_misses_after] : [t.near_miss_ph, t.near_miss_ph];
}
function rejectWhy(r, left) {
  const t = String(r.reason || ""), cost = S.run.step5.coordinator.costs_mid?.[r.id];
  const pushes = t.match(/pushes risk onto (?!no )([^,.;]+)/i);
  if (pushes) return `pushes risk onto ${pushes[1]}`;
  if (/budget/i.test(t) && cost != null) return `${kmoney(cost)} does not fit the ${kmoney(Math.max(0, left))} left`;
  return shortReason(t);
}
const shortReason = (s) => { s = String(s || ""); const i = s.indexOf(". "); s = i > 30 ? s.slice(0, i + 1) : s; return s.length > 130 ? s.slice(0, s.lastIndexOf(" ", 130)) + "…" : s; };
function step5() {
  const a = S.run.step5, co = a.coordinator, rv = co.review, tri = a.glimmer_triage;
  const acc = [...rv.accepted].sort((x, y) => hit(y) - hit(x) || nmPair(y)[0] - nmPair(x)[0]);
  const rej = [...rv.rejected].sort((x, y) => hit(y.id) - hit(x.id));
  const dropped = Object.keys(a.agents).filter((id) => !rv.accepted.includes(id) && !rv.rejected.some((r) => r.id === id));
  const budget = co.budget_usd || 0, spent = co.accepted_cost_usd || 0, comb = a.combined;
  const runs = S.run.sim_budget?.corridor_evaluations;
  const sel = S.agentSel && a.agents[S.agentSel];
  const SHOW = 8;
  const triChip = tri && tri.status === "live" ? chip("Muse Glimmer corner-triage", `screened ${tri.score_vs_simulator.corners} corners in ${fmt(tri.wall_s)} s; first guess = simulator's best fix at ${tri.score_vs_simulator.best_change_matches}`) : "";
  const railSel = sel ? (() => {
    const g = tri?.corners?.[S.agentSel];
    return `<div class="facts" style="border:0"></div><div class="h3">${esc(short(sel.name))}: ${rv.accepted.includes(S.agentSel) ? `<span class="tag sel">selected</span>` : rv.rejected.some((r) => r.id === S.agentSel) ? `<span class="tag rej">rejected</span>` : `<span class="tag drop">dropped</span>`}</div>
      ${g?.glimmer ? `<p class="why" style="margin-top:4px">Glimmer's first guess: ${esc(g.first_guess)}.</p>` : ""}
      <ul class="tests" style="margin-top:8px">${(sel.tests || []).slice(0, 6).map((t) => `<li><span>${esc(describe(t.changes, S.agentSel))}${t.result.future && t.result.future !== "today" ? ` (${esc(t.result.future)})` : ""}</span><span class="num">risk ${fmt(t.result.risk_before, 0)} → ${fmt(t.result.risk, 0)}</span></li>`).join("")}</ul>
      ${sel.decision ? `<p class="why" style="font-size:15px">${esc(shortReason(sel.decision.reason))}</p>` : ""}
      <p style="margin:10px 0 0"><button class="btn ghost" id="backCo" type="button" style="font-size:14px;padding:6px 12px">← The coordinator's view</button></p>`;
  })() : "";
  return {
    visual: `<div class="decide">
      <div class="hd"><div class="h2" style="margin:0">The coordinator's decision</div>${chip("Muse Wonder", `${Object.keys(a.agents).length} corner agents + 1 coordinator${runs ? ` · ${runs} simulator runs` : ""}`)}${triChip}</div>
      <div class="budget"><div class="bb"><i style="width:${Math.min(100, (100 * spent) / Math.max(budget, 1))}%"></i></div><b>${kmoney(spent)} of ${kmoney(budget)}</b>${badge("asm", "quarterly capital for this corridor is an assumption (set with --budget); costs are Spark-cited unit costs, midpoint")}</div>
      <div class="two">
        <div style="min-height:0"><h3>Selected for the plan · ${acc.length}</h3>
          <ul class="rows sel"><li class="muted" style="cursor:default;font-size:13px;letter-spacing:.05em;text-transform:uppercase"><span>Corner</span><span>Hit</span><span>Fix</span><span>Near-misses / h</span></li>
          ${acc.slice(0, SHOW).map((id) => { const [b, f] = nmPair(id); return `<li data-id="${id}" class="${id === S.agentSel ? "on" : ""}"><span class="cn">${esc(nameOf(id))}</span>${realv(hit(id))}<span>${esc(describe(a.plan_before_stress[id] || clean(a.agents[id].decision?.changes), id))}</span>${simv(b, f)}</li>`; }).join("")}
          ${acc.length > SHOW ? `<li class="more">and ${acc.length - SHOW} more</li>` : ""}</ul></div>
        <div style="min-height:0"><h3>Rejected by the coordinator · ${rej.length}</h3>
          <ul class="rows rej">${rej.slice(0, 6).map((r) => `<li data-id="${r.id}"><span class="tag rej">REJECTED</span><span><b>${esc(nameOf(r.id))}</b> (${hit(r.id)} hit) · ${esc(rejectWhy(r, budget - spent))}</span></li>`).join("") || `<li class="muted">None.</li>`}
          ${rej.length > 6 ? `<li class="more">and ${rej.length - 6} more${dropped.length ? `; ${dropped.length} dropped by their own agent` : ""}</li>` : dropped.length ? `<li class="more">${dropped.length} dropped by their own agent</li>` : ""}</ul></div>
      </div>
      <div class="defs">Walk head start = walkers get a few seconds before cars (LPI) · Clear the corner = no parking at the kerb (daylighting) · Slower turns = hardened centre line · Separate turn phase = turns never share the walk</div>
    </div>`,
    answer: sel ? "" : `<div class="answer">${acc.length} <small>fixes</small></div>`,
    cap: sel ? "" : `fit the ${kmoney(budget)} quarterly budget ${badge("asm", "quarterly capital for this corridor is an assumption")}`,
    why: sel ? "" : `<b>${fmt(comb.near_misses_before)} → ${fmt(comb.near_misses_ph)}</b> simulated near-misses an hour, +${pct(comb.added_car_delay_share)} car delay, before the stress tests ${badge("sim")}. Corners go in order of people hit; each agent tests fixes in the simulator; the coordinator funds down the list and rejects a fix that pushes risk onto another corner or buys too little.`,
    rail: sel ? railSel : confidence(),
    extra: `<button class="btn ghost noprint" id="replayAgents" type="button">↻ Replay</button>`,
    source: "Muse Wonder (Responses API, function tools); each test is the corridor simulator, 1 hour × 3 seeds. Costs: Muse Spark web search with citations.",
    mount() {
      $$(".rows li[data-id]").forEach((li) => (li.onclick = () => { S.agentSel = S.agentSel === li.dataset.id ? null : li.dataset.id; S.corner = li.dataset.id; go(5); }));
      if ($("#backCo")) $("#backCo").onclick = () => { S.agentSel = null; go(5); };
      $("#replayAgents").onclick = () => replayAgents(12000);
      if (S.playing) replayAgents((PLAY_SECONDS[5] - 5) * 1000);
    },
  };
}
function replayAgents(ms) {
  const t0 = performance.now();
  const tick = () => {
    if (S.step !== 5) return;
    S.agentP = Math.min(1, (performance.now() - t0) / ms);
    renderStrip(5);
    if (S.agentP < 1) S.timers.push(setTimeout(tick, 120));
  };
  S.agentP = 0; tick();
}

/* ================================================================ 6 · stress the future */
function step6() {
  const s6 = S.run.step6, list = s6.stress;
  if (!list.length) return { visual: `<div class="empty">The coordinator chose no stress futures in this run.</div>`, answer: "" };
  if (S.futSel == null) { const ib = list.findIndex((x) => x.breaks.length); S.futSel = ib >= 0 ? ib : 0; }
  const f = list[Math.min(S.futSel, list.length - 1)];
  const fin = s6.final_checks, holds = fin.filter((c) => c.holds).length, broke = list.filter((x) => x.breaks.length);
  const end = (x) => x.after || x.first;
  const mx = Math.max(...list.map((x) => end(x).near_misses_before || 1));
  const verdict = (x) => (x.breaks.length ? (x.revisions.length && x.holds ? `<span style="color:var(--risk)">BROKE</span>, REVISED, HOLDS` : `<span style="color:var(--risk)">BREAKS</span>`) : "HOLDS");
  const ticks = [0, 1000, 2000, 3000, 4000].filter((t) => t <= mx * 1.05);
  const rev = f.revisions[0], drops = f.budget_drops || [], back = s6.backfill || [];
  const story = f.breaks.length ? `<div class="storyrow">${sig("hand")}<p><b>1 · Breaks:</b> ${esc(f.breaks[0].problem.replace(/^the fix at 7 Av & /, "at "))}</p><span class="arr">→</span>
      <p><b>2 · ${rev ? `The ${esc(nameOf(rev.node))} agent revises:` : "No revision:"}</b> ${rev ? `${esc(describe(rev.from, rev.node))} → <b>${esc(describe(rev.to, rev.node))}</b>` : ""}${drops.length ? `. That costs more: to stay in budget, ${drops.map((d) => `${esc(nameOf(d.node))} (${d.people_hit} hit)`).join(", ")} waits for next quarter${back.length ? `, and the money left funds ${back.map((b) => `${esc(nameOf(b.node))} (${b.people_hit} hit)`).join(", ")}` : ""}.` : ""}</p><span class="arr">→</span>
      ${f.holds ? sig("walk") : sig("hand")}<p><b>3 · Tested again:</b> ${f.holds ? "the plan holds" : "still breaks"}</p></div>` : "";
  return {
    visual: `<div class="stress">
      ${list.map((x, i) => `<div class="frow ${i === S.futSel ? "on" : ""}" data-i="${i}"><div><div class="fl">${esc(x.label)}</div><div class="fw">${esc(FUT_WHY[x.future] || x.why)}${x.picked === false ? " · not picked by the coordinator, tested anyway" : ""}</div></div>
        <div class="pairs"><div><span>no plan</span><span class="tr"><i class="base" style="width:${(100 * end(x).near_misses_before) / mx}%"></i></span></div><div><span>plan</span><span class="tr"><i class="plan" style="width:${(100 * end(x).near_misses_ph) / mx}%"></i></span></div></div>
        <div class="vd">${verdict(x)}<span class="n">${fmt(end(x).near_misses_before)} → ${fmt(end(x).near_misses_ph)} an hour</span></div></div>${i === S.futSel ? story : ""}`).join("")}
      <div class="axis"><span></span><span class="ticks">${ticks.map((t) => `<span style="left:${(100 * t) / mx}%">${fmt(t)}</span>`).join("")}</span><span style="text-align:right">simulated near-misses an hour</span></div>
    </div>`,
    answer: `<div class="answer words">Holds in ${holds} of ${fin.length} futures</div>`,
    cap: `${broke.length ? `after ${broke.length === 1 ? "one revision" : broke.length + " revisions"}: the first plan broke in ${broke.map((x) => x.label.toLowerCase()).join(" and ")}` : "the first plan held in every future"} ${badge("sim", "each future re-simulated with the final plan vs no plan")}`,
    why: `The coordinator names the futures it expects to break this plan; the system tests every future anyway. A fix breaks if it leaves any corner worse than doing nothing, keeps less than 30% of its benefit, or blows the car-delay limit.`,
    source: "Futures: holiday crowds ×1.7 walkers; shows letting out ×2.6 at W 43–50; rain at night −12 pts detection; bus lane; 30% ignore the new rules.",
    mount() { $$(".frow").forEach((b) => (b.onclick = () => { S.futSel = +b.dataset.i; go(6); })); },
  };
}
const FUT_WHY = { holiday: "1.7 times the walkers, December peak", show_out: "a crowd surge at W 43–50 St around 10:30 pm",
  rain_night: "drivers see walkers later; walkers hurry", bus_lane: "one lane becomes a bus lane; buses block sightlines",
  ban_ignored: "drivers still turn where banned and park in cleared corners" };

/* ================================================================ 7 · the plan */
function step7() {
  const p = S.run.step7, t = p.totals, w = p.watcher, co = S.run.step5.coordinator, tri = S.run.step5.glimmer_triage;
  const items = p.items.map((i) => ({ ...i, people: i.people_hit ?? hit(i.id), wave: i.wave ?? (i.priority <= 3 ? 1 : 2) }));
  const first = items.filter((i) => i.wave === 1), rest = items.filter((i) => i.wave !== 1);
  const kinds = [...new Set(items.flatMap((i) => i.cost_kinds))].filter((k) => p.costs[k]);
  const mid = t.cost_mid ?? items.reduce((s, i) => s + (i.cost_low + i.cost_high) / 2, 0);
  const w1 = t.wave1_cost_mid ?? first.reduce((s, i) => s + (i.cost_low + i.cost_high) / 2, 0);
  const budget = t.budget_usd ?? co.budget_usd ?? 0;
  const rule = (i) => i.success_rule || { expand_if_camera_cut_pct_at_least: Math.round(50 * (1 - i.near_misses_after / i.near_misses_before)), within_weeks: 6 };
  const names = first.map((i) => esc(short(i.name)));
  const ft = tri && tri.status === "live" ? tri.score_vs_simulator : null;
  return {
    visual: `<article class="brief">
      <h2>7th Avenue safety plan: W 30th St to Central Park South</h2>
      <div class="meta">Corner Case run ${esc(S.run.id)} · simulated weekday 5–6 pm · corners in order of people hit (real), fixes chosen in the simulator</div>
      <div class="sec">1 · Build first, this quarter</div>
      <table class="t"><thead><tr><th>Corner</th><th>Change</th><th class="r">People hit since 2023</th><th class="r">Near-misses / h</th><th class="r">Expand if camera close calls fall by</th><th class="r">Rough cost</th></tr></thead><tbody>
      ${first.map((i) => `<tr><td class="nw"><b>${esc(short(i.name))}</b></td><td>${esc(describe(i.changes_raw, i.id))}</td><td class="r">${realv(i.people)}</td><td class="r">${simv(i.near_misses_before, i.near_misses_after)}</td><td class="r"><b>${fmt(rule(i).expand_if_camera_cut_pct_at_least)}%</b> in ${rule(i).within_weeks} weeks</td><td class="r nw">${kmoney(i.cost_low)}–${kmoney(i.cost_high)}</td></tr>`).join("")}</tbody></table>
      <div class="sec">2 · Measure close calls on camera, week by week &nbsp;·&nbsp; 3 · Then fund the rest: ${rest.length} corners</div>
      <div style="font-size:17px">${rest.map((i) => esc(short(i.name))).join(", ")}</div>
      <div class="after"><div class="h3">After it's built: know in weeks, not 3 years</div>${timeline(true)}
        <div class="defs" style="margin-top:4px">■ <b>Muse Glimmer</b> (open weights, on Modal) watches every changed corner: base Glimmer writes the weekly close-call log${w.status === "live" ? "" : " (not live in this run)"}; a corner-triage LoRA we fine-tuned re-reads all 30 corners in seconds${ft ? ` (in this run its first guess matched the simulator's best fix at ${ft.best_change_matches} of ${ft.corners} corners; risk band ${ft.risk_band_matches} of ${ft.corners})` : ""}.</div></div>
      <div class="sources">Costs, found by Muse Spark web search: ${kinds.map((k) => { const c = p.costs[k]; const u = c.source_urls[0] || ""; return `${esc(COST_LABEL[k] || k)} ${money(c.low_usd)}–${money(c.high_usd)} <a href="${esc(u)}" target="_blank" rel="noopener">${esc(u.replace(/^https?:\/\/(www\.)?/, "").split("/")[0])}</a>`; }).join(" · ")}</div>
    </article>`,
    answer: `<div class="answer verdict" style="font-size:38px">Build ${names.slice(0, -1).join(", ")} and ${names.slice(-1)} first. Measure. Then fund the rest.</div>`,
    cap: `${simv(t.near_misses_before, t.near_misses_after)} simulated near-misses an hour, whole plan, +${pct(t.added_car_delay_share)} car delay ${badge("sim")}`,
    why: `First wave ${kmoney(w1)}; whole plan ${kmoney(mid)} estimated of the ${kmoney(budget)} budget (range ${kmoney(t.cost_low)}–${kmoney(t.cost_high)}). A corner is expanded only if its camera close calls fall by at least half its simulated cut.`,
    rail: confidence(),
    source: "Costs: Muse Spark 1.3 web search with citations (FHWA, NYC DOT). Watcher: Muse Glimmer on Modal (vLLM).",
  };
}

const COST_LABEL = { lpi: "Walk head start", turn_ban: "Turn ban", hardened_centerline: "Slower turns",
  daylighting: "Clear the corner", split_phase: "Separate turn phase", retime: "Signal retiming" };

const SCREENS = { 0: step0, 1: step1, 2: step2, 3: step3, 4: step4, 5: step5, 6: step6, 7: step7 };

/* ---------------------------------------------------------------- play the story */
function startPlay() {
  stopPlay();
  S.playing = true; S.agentP = 1; S.agentSel = null; S.futSel = null; S.corner = S.run.default_corner || "w45";
  $("#playBtn").textContent = "■ Stop";
  $("#playbar").hidden = false;
  const total = Object.values(PLAY_SECONDS).reduce((a, b) => a + b, 0);
  let acc = 0;
  for (let n = 0; n <= 7; n++) { const at = acc; S.timers.push(setTimeout(() => go(n), at * 1000)); acc += PLAY_SECONDS[n]; }
  S.timers.push(setTimeout(stopPlay, total * 1000));
  const t0 = performance.now();
  const bar = () => { if (!S.playing) return; $("#playfill").style.width = `${Math.min(100, ((performance.now() - t0) / 1000 / total) * 100)}%`; requestAnimationFrame(bar); };
  bar();
}
function stopPlay() {
  S.playing = false;
  S.timers.forEach(clearTimeout); S.timers = [];
  $("#playBtn").textContent = "▶ Play the story";
  $("#playbar").hidden = true;
  S.agentP = 1;
}

/* ---------------------------------------------------------------- tooltips */
function bindTips() {
  const tip = $("#tip");
  const show = (el) => {
    const t = el.dataset.src; if (!t) return;
    tip.textContent = t; tip.hidden = false;
    const r = el.getBoundingClientRect();
    tip.style.left = Math.min(window.innerWidth - 390, Math.max(8, r.left)) + "px";
    const below = r.bottom + 8;
    tip.style.top = (below + tip.offsetHeight > window.innerHeight ? r.top - tip.offsetHeight - 8 : below) + "px";
  };
  document.addEventListener("mouseover", (e) => { const el = e.target.closest("[data-src]"); if (el) show(el); });
  document.addEventListener("mouseout", (e) => { if (e.target.closest("[data-src]")) tip.hidden = true; });
  document.addEventListener("focusin", (e) => { const el = e.target.closest("[data-src]"); if (el) show(el); });
  document.addEventListener("focusout", () => (tip.hidden = true));
}

/* ---------------------------------------------------------------- the street video with SAM 3.1 masks */
function mountVideo() {
  const v = $("#vid"), cv = $("#ov"), ctx = cv.getContext("2d"), tr = S.run.step2.tracks;
  const k = cv.width / tr.width, dt = 1 / tr.fps, dur = S.run.step2.measurements.clip.duration_s;
  const paths = new Map();
  const P = (s) => { if (!s) return null; if (!paths.has(s)) paths.set(s, new Path2D(s)); return paths.get(s); };
  const roadKeys = Object.keys(tr.road || {}).map(Number).sort((a, b) => a - b);
  const roadAt = (key) => tr.road[String(key)] || tr.road[key.toFixed(1)] || [];
  const calls = tr.close_calls || [];
  const draw = () => {
    const t = v.currentTime;
    ctx.clearRect(0, 0, cv.width, cv.height);
    ctx.save(); ctx.scale(k, k);
    if (roadKeys.length) {
      const rk = roadKeys.reduce((b, x) => (Math.abs(x - t) < Math.abs(b - t) ? x : b), roadKeys[0]);
      if (Math.abs(rk - t) <= 0.6) for (const r of roadAt(rk)) {
        const p = P(r.mask); if (!p) continue;
        ctx.save(); ctx.translate(r.box[0], r.box[1]); ctx.fillStyle = "rgba(53,85,127,.32)"; ctx.fill(p); ctx.restore();
      }
    }
    let n = 0, inRoad = false;
    for (const o of tr.tracks) {
      let j = -1;
      for (let i = 0; i < o.t.length; i++) if (Math.abs(o.t[i] - t) <= dt * 0.6) { j = i; break; }
      if (j < 0) continue;
      const b = o.box[j], p = P(o.mask && o.mask[j]);
      const person = o.label === "person";
      if (person) n++;
      const road = person && o.in_road;
      if (road) inRoad = true;
      ctx.save(); ctx.translate(b[0], b[1]);
      if (p) {
        ctx.fillStyle = person ? (road ? "rgba(232,89,12,.55)" : "rgba(255,255,255,.38)") : "rgba(232,89,12,.28)";
        ctx.strokeStyle = person ? (road ? "#E8590C" : "rgba(255,255,255,.9)") : "#E8590C";
        ctx.lineWidth = person ? 1 : 1.6;
        ctx.fill(p); ctx.stroke(p);
      } else {
        ctx.strokeStyle = person ? "#fff" : "#E8590C"; ctx.strokeRect(0, 0, b[2] - b[0], b[3] - b[1]);
      }
      ctx.restore();
    }
    for (const c of calls) {
      const age = t - c.t; if (age < -0.2 || age > 1.2) continue;
      const o = tr.tracks.find((x) => x.id === c.person); if (!o) continue;
      let j = 0; for (let i = 0; i < o.t.length; i++) if (Math.abs(o.t[i] - c.t) < Math.abs(o.t[j] - c.t)) j = i;
      const b = o.box[j];
      ctx.beginPath(); ctx.arc((b[0] + b[2]) / 2, b[3], 14 + Math.max(0, age) * 22, 0, 7);
      ctx.strokeStyle = `rgba(232,89,12,${1 - Math.max(0, age) / 1.2})`; ctx.lineWidth = 3; ctx.stroke();
    }
    ctx.restore();
    $("#hudN").textContent = n;
    $("#hudRoad").hidden = !inRoad;
    $("#head").style.left = `${Math.min(100, (100 * t) / dur)}%`;
    $("#vt").textContent = clock(t);
    $$("#notes li").forEach((li) => li.classList.toggle("on", Math.abs(+li.dataset.t - t) < 1.2));
  };
  let raf;
  const loop = () => { draw(); raf = requestAnimationFrame(loop); };
  loop();
  S.anim = { stop: () => { cancelAnimationFrame(raf); v.pause(); } };
  $("#vplay").onclick = () => (v.paused ? v.play() : v.pause());
  v.onplay = () => ($("#vplay").textContent = "Pause");
  v.onpause = () => ($("#vplay").textContent = "Play");
  $("#scrub").onclick = (e) => { const r = $("#scrub").getBoundingClientRect(); v.currentTime = Math.max(0, Math.min(1, (e.clientX - r.left) / r.width)) * (v.duration || dur); };
  $$("#notes li").forEach((li) => (li.onclick = () => { v.currentTime = Math.min(+li.dataset.t, (v.duration || dur) - 0.1); }));
  if (S.playing) v.play().catch(() => {});
}

/* top-down animation of one corner from a recorded trace */
function simAnim(cv, tr, nd, opts = {}) {
  if (!tr) return { stop() {} };
  const ctx = cv.getContext("2d"), Wpx = cv.width, view = 34, k = Wpx / (2 * view);
  const Wa = tr.crossing_m.N, Wc = tr.crossing_m.E, cw = 3.2;
  const P = (x, y) => [Wpx / 2 + x * k, Wpx / 2 - y * k];
  const dirs = nd.cross_dirs;
  const yE = dirs.length === 2 ? -Wc / 4 : 0, yW = dirs.length === 2 ? Wc / 4 : 0;
  const band = { N: Wc / 2 + 1 + cw / 2, S: -(Wc / 2 + 1 + cw / 2), E: Wa / 2 + 1 + cw / 2, W: -(Wa / 2 + 1 + cw / 2) };
  const bez = (p0, c, p1, n = 24) => Array.from({ length: n + 1 }, (_, i) => { const t = i / n; return [(1 - t) ** 2 * p0[0] + 2 * (1 - t) * t * c[0] + t * t * p1[0], (1 - t) ** 2 * p0[1] + 2 * (1 - t) * t * c[1] + t * t * p1[1]]; });
  function path(stream, lane, i) {
    const far = 60, xl = -Wa / 2 + Wa * lane;
    if (stream === "ave_through_n") return [[xl, far], [xl, -far]];
    if (stream === "ave_left") { const x0 = Wa / 2 - 2; return [[x0, far], ...bez([x0, Wc / 2 + 4], [x0, yE], [Wa / 2 + 4, yE]), [far, yE]]; }
    if (stream === "ave_right") { const x0 = -Wa / 2 + 2; return [[x0, far], ...bez([x0, Wc / 2 + 4], [x0, yW], [-Wa / 2 - 4, yW]), [-far, yW]]; }
    const eb = dirs.length === 2 ? i % 2 === 0 : dirs[0] === "EB";
    if (stream === "cross_turn") return eb ? [[-far, yE], ...bez([-Wa / 2 - 4, yE], [-Wa / 2 + 2, yE], [-Wa / 2 + 2, -Wc / 2 - 4]), [-Wa / 2 + 2, -far]]
      : [[far, yW], ...bez([Wa / 2 + 4, yW], [Wa / 2 - 2, yW], [Wa / 2 - 2, -Wc / 2 - 4]), [Wa / 2 - 2, -far]];
    if (stream === "cross_through_e") return eb ? [[-far, yE], [far, yE]] : [[far, yW], [-far, yW]];
    return null;
  }
  function densify(pts) {
    const out = [pts[0]], cum = [0];
    for (let i = 1; i < pts.length; i++) {
      const [ax, ay] = pts[i - 1], [bx, by] = pts[i], L = Math.hypot(bx - ax, by - ay), n = Math.max(1, Math.ceil(L / 0.5));
      for (let j = 1; j <= n; j++) { out.push([ax + ((bx - ax) * j) / n, ay + ((by - ay) * j) / n]); cum.push(cum[cum.length - 1] + L / n); }
    }
    return { pts: out, cum };
  }
  function crossS(d, leg) {
    for (let i = 1; i < d.pts.length; i++) {
      const [ax, ay] = d.pts[i - 1], [bx, by] = d.pts[i];
      if (leg === "N" || leg === "S") { const y = band[leg]; if ((ay - y) * (by - y) <= 0 && Math.abs(ax) < Wa / 2 + 2) return d.cum[i]; }
      else { const x = band[leg]; if ((ax - x) * (bx - x) <= 0 && Math.abs(ay) < Wc / 2 + 2) return d.cum[i]; }
    }
    return d.cum[Math.floor(d.cum.length / 2)];
  }
  const veh = tr.vehicles.filter((v) => !["ave_through_s", "cross_through_w"].includes(v.stream)).map((v, i) => {
    const p = path(v.stream, v.lane, i); if (!p) return null;
    const d = densify(p), sz = crossS(d, v.stream === "ave_through_n" ? "N" : v.leg);
    return { ...v, d, sz, sStop: Math.max(0, sz - 6) };
  }).filter(Boolean);
  function posAt(v, t) {
    let s;
    if (t <= v.release) s = v.sStop - (v.release - t) * v.speed;
    else if (t < v.t_zone - 2 / v.speed) s = Math.min(v.sStop + (t - v.release) * v.speed, v.sz - 2);
    else s = v.sz + (t - v.t_zone) * v.speed;
    if (s < 0 || s > v.d.cum[v.d.cum.length - 1]) return null;
    let lo = 0, hi = v.d.cum.length - 1;
    while (hi - lo > 1) { const m = (lo + hi) >> 1; if (v.d.cum[m] < s) lo = m; else hi = m; }
    const a = v.d.pts[lo], b = v.d.pts[hi];
    return { x: b[0], y: b[1], ang: Math.atan2(b[1] - a[1], b[0] - a[0]) };
  }
  const sig = tr.signal;
  const cyc = (t) => ((t % sig.cycle_s) + sig.cycle_s) % sig.cycle_s;
  const pedState = (leg, t) => { const [ws, we, fe] = sig.walk[leg]; const c = cyc(t); return c >= ws && c < we ? 0 : c >= we && c < fe ? 1 : 2; };
  const vehGreen = (ph, t) => { const c = cyc(t); return ph === "A" ? c >= sig.lpi_s && c < sig.ga : c >= sig.b0 + sig.lpi_s && c < sig.b0 + sig.gb; };
  const F = (px) => `${px * 2}px Helvetica Neue, Helvetica, Arial`;
  function drawStatic() {
    ctx.fillStyle = "#DCD6CA"; ctx.fillRect(0, 0, Wpx, Wpx);
    ctx.fillStyle = "#A39C90";
    let [ax, ay] = P(-Wa / 2, view); ctx.fillRect(ax, ay, Wa * k, 2 * view * k);
    [ax, ay] = P(-view, Wc / 2); ctx.fillRect(ax, ay, 2 * view * k, Wc * k);
    ctx.fillStyle = "#fff";
    const stripes = (x0, y0, x1, y1, horiz) => {
      const n = Math.floor((horiz ? x1 - x0 : y1 - y0) / 1.0);
      for (let i = 0; i < n; i += 2) {
        if (horiz) { const [px, py] = P(x0 + i, y1); ctx.fillRect(px, py, 0.6 * k, (y1 - y0) * k); }
        else { const [px, py] = P(x0, y0 + i + 0.6); ctx.fillRect(px, py, (x1 - x0) * k, 0.6 * k); }
      }
    };
    stripes(-Wa / 2, band.N - cw / 2, Wa / 2, band.N + cw / 2, true);
    stripes(-Wa / 2, band.S - cw / 2, Wa / 2, band.S + cw / 2, true);
    stripes(band.E - cw / 2, -Wc / 2, band.E + cw / 2, Wc / 2, false);
    stripes(band.W - cw / 2, -Wc / 2, band.W + cw / 2, Wc / 2, false);
    ctx.fillStyle = "#45423C"; ctx.font = "bold " + F(16);
    ctx.textAlign = "right"; let [lx, ly] = P(-Wa / 2 - 1.5, Wc / 2 + 14); ctx.fillText("7th Ave ↓", lx, ly); [lx, ly] = P(-Wa / 2 - 1.5, Wc / 2 + 11.5); ctx.fillText("one way", lx, ly);
    const street = nd.street_num === 59 ? "Central Park S" : `W ${nd.street_num} St`;
    ctx.textAlign = "left"; [lx, ly] = P(Wa / 2 + 6, Wc / 2 + 1.4); ctx.fillText(`${street} ${dirs.length === 2 ? "⇄" : dirs[0] === "EB" ? "→" : "←"}`, lx, ly);
  }
  let t = opts.start || 0, last = performance.now(), raf;
  const speed = opts.speed || 3;
  function frame(now) {
    t += ((now - last) / 1000) * speed; last = now;
    if (t > tr.seconds) t = 0;
    drawStatic();
    for (const leg of ["N", "S", "E", "W"]) {
      const s = pedState(leg, t);
      const on = s === 0 ? "#ffffff" : s === 1 ? (Math.floor(t * 2) % 2 ? "#E8590C" : "#F9DCC8") : "#E8590C";
      const pts = leg === "N" || leg === "S" ? [[-Wa / 2 - 1.8, band[leg]], [Wa / 2 + 1.8, band[leg]]] : [[band[leg], -Wc / 2 - 1.8], [band[leg], Wc / 2 + 1.8]];
      for (const [x, y] of pts) { const [px, py] = P(x, y); ctx.fillStyle = on; ctx.strokeStyle = "#141414"; ctx.lineWidth = 2; ctx.fillRect(px - 11, py - 11, 22, 22); ctx.strokeRect(px - 11, py - 11, 22, 22); }
    }
    for (const [ph, x, y] of [["A", -Wa / 2 - 3, Wc / 2 + 6], ["B", -Wa / 2 - 6, -Wc / 2 - 3], ["B", Wa / 2 + 6, Wc / 2 + 3]]) {
      const [px, py] = P(x, y); ctx.beginPath(); ctx.arc(px, py, 11, 0, 7); ctx.fillStyle = vehGreen(ph, t) ? "#2F7D4F" : "#B3261E"; ctx.fill();
    }
    for (const p of tr.peds) {
      if (t < p.start) continue;
      const d = p.speed * (t - p.start); if (d > p.len) continue;
      let x, y;
      const off = ((p.start * 7) % 2 - 1) * 0.8;
      if (p.leg === "N" || p.leg === "S") { x = p.side === 0 ? -Wa / 2 + d : Wa / 2 - d; y = band[p.leg] + off; }
      else { y = p.side === 0 ? -Wc / 2 + d : Wc / 2 - d; x = band[p.leg] + off; }
      const [px, py] = P(x, y); ctx.beginPath(); ctx.arc(px, py, 7, 0, 7); ctx.fillStyle = "#141414"; ctx.fill();
      if (p.against) { ctx.strokeStyle = "#E8590C"; ctx.lineWidth = 3; ctx.stroke(); }
    }
    for (const v of veh) {
      const q = posAt(v, t); if (!q) continue;
      const [px, py] = P(q.x, q.y);
      ctx.save(); ctx.translate(px, py); ctx.rotate(-q.ang);
      ctx.fillStyle = "#FFFDF8"; ctx.strokeStyle = "#141414"; ctx.lineWidth = 2.5;
      ctx.fillRect(-2.3 * k, -0.95 * k, 4.6 * k, 1.9 * k); ctx.strokeRect(-2.3 * k, -0.95 * k, 4.6 * k, 1.9 * k);
      ctx.restore();
    }
    let count = 0;
    for (const e of tr.events) {
      if (e.t <= t) count++;
      const age = t - e.t; if (age < 0 || age > 2.2) continue;
      let x, y;
      if (e.leg === "N" || e.leg === "S") { x = -Wa / 2 + Wa * e.lane; y = band[e.leg]; } else { y = -Wc / 2 + Wc * e.lane; x = band[e.leg]; }
      const [px, py] = P(x, y);
      ctx.beginPath(); ctx.arc(px, py, 18 + age * 30, 0, 7); ctx.strokeStyle = `rgba(232,89,12,${1 - age / 2.2})`; ctx.lineWidth = 6; ctx.stroke();
    }
    ctx.fillStyle = "rgba(255,253,248,.94)"; ctx.fillRect(20, 20, 560, 104);
    ctx.fillStyle = "#141414"; ctx.textAlign = "left"; ctx.font = "bold " + F(17);
    const tt = Math.floor(t); ctx.fillText(`5:${String(Math.floor(tt / 60)).padStart(2, "0")}:${String(tt % 60).padStart(2, "0")} pm · simulated`, 40, 64);
    ctx.font = F(14); ctx.fillStyle = "#E8590C"; ctx.fillText(`${count} near-misses so far · playing ×${speed}`, 40, 104);
    raf = requestAnimationFrame(frame);
  }
  raf = requestAnimationFrame(frame);
  return { stop: () => cancelAnimationFrame(raf) };
}

/* ---------------------------------------------------------------- live mode */
async function startLive() {
  const dlg = $("#liveDlg"), log = $("#liveLog");
  log.innerHTML = "";
  dlg.showModal();
  const add = (txt, cls = "") => { const li = document.createElement("li"); li.className = cls; li.textContent = txt; log.appendChild(li); log.scrollTop = log.scrollHeight; };
  const res = await fetch("/api/live", { method: "POST" });
  const body = await res.json();
  if (!res.ok) { add(body.detail || "Could not start a live run.", "bad"); return; }
  add("Started. Steps 1–4 use local data and the simulator; step 5 calls Muse Wonder.");
  const es = new EventSource(`/api/live/${body.job}/events`);
  es.onmessage = async (m) => {
    const e = JSON.parse(m.data);
    if (e.type === "step") add(`Step ${e.step}: ${e.msg}`, e.status === "done" ? "done" : "");
    else if (e.type === "agent" && ["kept", "dropped", "error"].includes(e.state)) add(`${nameOf(e.node)}: ${e.state}${e.headline ? " · " + e.headline : ""}`, e.state === "error" ? "bad" : "");
    else if (e.type === "coordinator" && ["rejected", "briefed"].includes(e.state)) add(e.state === "briefed" ? "Coordinator wrote briefs for every corner" : `Coordinator rejected ${nameOf(e.node)}: ${e.reason}`, e.state === "rejected" ? "bad" : "");
    else if (e.type === "stress") add(`Stress future ${e.future}: ${e.breaks ? e.breaks + " break(s)" : "holds"}`);
    else if (e.type === "error") add(`Error: ${e.msg}`, "bad");
    else if (e.type === "end") {
      es.close();
      if (e.status === "done") {
        add(`Done: run ${e.run_id}. Loading it…`, "done");
        fillRuns((await (await fetch("/api/runs")).json()).runs);
        $("#runSel").value = e.run_id;
        await loadRun(e.run_id);
      } else add(`The run stopped: ${e.error || "unknown error"}. The recorded run is still available.`, "bad");
    }
  };
  es.onerror = () => add("Lost the connection to the server; the run continues in the background.", "bad");
}

boot();
