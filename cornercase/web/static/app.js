/* Corner Case UI. A fixed 1920x1080 artboard scaled to the window: the visual on the left,
   and a rail on the right that always reads question -> evidence -> action. */
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
const PLAY_SECONDS = { 1: 11, 2: 17, 3: 15, 4: 13, 5: 20, 6: 15, 7: 13 };
const NEXT = { 1: "Watch the corner", 2: "Rebuild it", 3: "Can we trust it?", 4: "Let agents try fixes", 5: "Stress the future", 6: "See the plan" };

const S = { run: null, step: 1, corner: "w45", playing: false, timers: [], anim: null,
            trustMode: "with_measured_inputs", agentSel: null, futSel: null, agentP: 1 };

const $ = (sel, el = document) => el.querySelector(sel);
const $$ = (sel, el = document) => [...el.querySelectorAll(sel)];
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmt = (x, d = 0) => (x == null || Number.isNaN(+x) ? "–" : Number(x).toLocaleString("en-US", { minimumFractionDigits: d, maximumFractionDigits: d }));
const money = (x) => "$" + fmt(Math.round(x / 100) * 100);
const kmoney = (x) => (x >= 1000 ? "$" + fmt(x / 1000, x >= 100000 ? 0 : 1) + "k" : money(x));
const sign = (x) => (x >= 0 ? "+" : "−") + fmt(Math.abs(x), 1);
const node = (id) => S.run.step1.nodes.find((n) => n.id === id) || {};
const short = (name) => String(name || "").replace("7 Av & ", "");
const nameOf = (id) => short(node(id).name || id);
const clock = (t) => `0:${String(Math.floor(t)).padStart(2, "0")}`;

function badge(kind, src) {
  const map = { real: ["REAL", "real"], sim: ["SIMULATED", "sim"], meas: ["MEASURED", "meas"], asm: ["ASSUMPTION", "asm"], open: ["OPEN DATA", "real"] };
  const [t, c] = map[kind] || map.real;
  return `<span class="badge ${c}" tabindex="0" data-src="${esc(src || "")}">${t}</span>`;
}
const kindBadge = (k, src) => badge(k === "measured" ? "meas" : k === "open-data" ? "open" : "asm", src);
const chip = (model, text) => `<span class="chip"><span class="dot"></span><b>${esc(model)}</b> ${esc(text)}</span>`;
function describe(ch) {
  ch = ch || {};
  const p = [];
  if (ch.lpi_s != null) p.push(`Walk head start ${ch.lpi_s} s`);
  if (ch.turn_ban && ch.turn_ban !== "none") p.push(ch.turn_ban === "both" ? "Ban all turns" : `Ban ${ch.turn_ban} turns`);
  if (ch.hardened_centerline) p.push("Slower turns");
  if (ch.daylighting) p.push("Clear the corner");
  if (ch.split_phase) p.push("Separate turn phase");
  if (ch.cycle_s != null) p.push(`${ch.cycle_s} s cycle`);
  if (ch.ave_share != null) p.push(`${Math.round(ch.ave_share * 100)}% green to 7 Av`);
  return p.join(" + ") || "No change";
}
const clean = (o) => Object.fromEntries(Object.entries(o || {}).filter(([, v]) => v !== null && v !== undefined));

/* ---------------------------------------------------------------- artboard scaling */
function fit() {
  const s = Math.min(window.innerWidth / 1920, window.innerHeight / 1080);
  $("#app").style.transform = `scale(${s})`;
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
    if (e.key === "ArrowLeft") { stopPlay(); go(Math.max(1, S.step - 1)); }
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
  go(st >= 1 && st <= 7 ? st : 1);
}

/* ---------------------------------------------------------------- navigation */
function renderStepper() {
  $("#stepper").innerHTML = STEPS.map((s) =>
    `<button class="step ${s.n === S.step ? "on" : s.n < S.step ? "done" : ""}" data-n="${s.n}" type="button" aria-current="${s.n === S.step ? "step" : "false"}"><span class="n">${s.n}</span><span class="l">${s.label}</span></button>`).join("");
  $$("#stepper .step").forEach((b) => (b.onclick = () => { stopPlay(); go(+b.dataset.n); }));
}

function go(n) {
  stopAnim();
  S.step = n;
  renderStepper();
  const s = STEPS[n - 1];
  const v = SCREENS[n]();
  $("#stage").innerHTML = `
    <section class="visual" aria-label="${esc(s.label)}">${v.visual}</section>
    <aside class="rail">
      <div class="eyebrow">Step ${n} of 7 · ${s.label}</div>
      <h1>${s.q}</h1>
      ${v.sub ? `<p class="sub">${v.sub}</p>` : ""}
      ${v.rail}
      <div class="spacer"></div>
      <div class="action">${v.extra || ""}${n < 7 ? `<button class="btn primary" id="nextBtn" type="button">${v.action || NEXT[n]} →</button>` : `<button class="btn primary noprint" id="printBtn" type="button">Print the brief</button>`}</div>
    </aside>`;
  $("#capText").textContent = v.caption || "";
  $("#capSrc").textContent = v.source || "";
  if ($("#nextBtn")) $("#nextBtn").onclick = () => { stopPlay(); go(n + 1); };
  if ($("#printBtn")) $("#printBtn").onclick = () => window.print();
  v.mount && v.mount();
  const u = new URL(location); u.searchParams.set("step", n); u.searchParams.set("run", S.run.id); history.replaceState(null, "", u);
}
function stopAnim() { if (S.anim) { S.anim.stop(); S.anim = null; } }

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

/* ================================================================ 1 · where people get hurt */
function step1() {
  const d = S.run.step1, h = d.headline;
  const top = [...d.nodes].sort((a, b) => b.people_hit - a.people_hit || b.killed - a.killed).slice(0, 6);
  const max = top[0]?.people_hit || 1;
  const sel = node(S.corner);
  return {
    sub: "Every dot is a real crash that injured or killed someone walking. Click a corner to study it; Times Square is preselected.",
    visual: `<div class="mapwrap card" style="padding:18px 20px">${mapSVG(d)}
      <div class="legend"><span><i style="background:var(--risk);opacity:.6;border-radius:50%"></i>crash that hurt a pedestrian</span><span><i style="background:var(--ink);border-radius:50%"></i>a pedestrian was killed</span><span><i style="border:2px solid var(--ink);border-radius:50%"></i>corner, sized by people hit</span><span class="muted">Across-street distance stretched 3×</span></div></div>`,
    rail: `<div class="big">${fmt(h.people_hit)}</div>
      <p class="bigcap">people hit walking on these ${h.blocks} blocks since 2023, ${fmt(h.killed)} killed ${badge("real", d.source)}</p>
      <ul class="rank">${top.map((n, i) => `<li data-id="${n.id}" class="${n.id === S.corner ? "sel" : ""}"><span class="i">${i + 1}</span><b>${esc(short(n.name))}</b><span class="num">${n.people_hit} hit${n.killed ? ` · ${n.killed} killed` : ""}</span><span class="bar"><i style="width:${(100 * n.people_hit) / max}%"></i></span></li>`).join("")}</ul>
      <ul class="facts">
        <li><span class="k">Selected: <b style="font-size:17px">${esc(short(sel.name))}</b></span><b>${sel.people_hit} hit</b></li>
        <li><span class="k">Walk head start today ${badge("open", "NYC DOT VZV Leading Pedestrian Interval list (xc4v-ntf4)")}</span><b>${sel.lpi_installed?.length ? "since " + sel.lpi_installed[0].slice(0, 4) : sel.exclusive_ped_phase ? "walk-only phase" : "none"}</b></li>
      </ul>`,
    action: `Watch ${esc(short(sel.name))}`,
    caption: `${fmt(h.people_hit)} people were hit walking on 7th Avenue between W 30th St and Central Park South since January 2023.`,
    source: "NYC Open Data: Motor Vehicle Collisions (h9gi-nx95), Jan 2023 – Oct 2026, located by latitude/longitude",
    mount() {
      const pick = (id) => { S.corner = id; go(1); };
      $$(".rank li, .map .node").forEach((el) => (el.onclick = () => pick(el.dataset.id)));
      $$(".map .node").forEach((el) => (el.onkeydown = (e) => { if (e.key === "Enter") pick(el.dataset.id); }));
      if (S.playing) animateDots();
    },
  };
}

function mapSVG(d) {
  const W = 1200, H = 740, pad = 46, cy = 360, stretch = 4.2;
  const ys = d.nodes.map((n) => n.gy);
  const y0 = Math.min(...ys) - 40, y1 = Math.max(...ys) + 25;
  const X = (gy) => pad + ((gy - y0) / (y1 - y0)) * (W - 2 * pad);
  const Y = (gx) => cy - (gx - d.gx_ave) * stretch * 0.9;
  const maxHit = Math.max(...d.nodes.map((n) => n.people_hit), 1);
  const ts = d.nodes.filter((n) => n.plaza === "Times Square Plaza" && n.broadway);
  let s = `<svg class="map" viewBox="0 0 ${W} ${H}" preserveAspectRatio="xMidYMid meet" role="img" aria-label="Map of 7th Avenue with every pedestrian crash since 2023">`;
  if (ts.length) {
    const a = X(Math.min(...ts.map((n) => n.gy)) - 50), b = X(Math.max(...ts.map((n) => n.gy)) + 30);
    s += `<rect class="plaza" x="${a}" y="${cy - 250}" width="${b - a}" height="500"/><text class="lbl b" x="${(a + b) / 2}" y="${cy - 262}" text-anchor="middle">Times Square</text>`;
  }
  const penn = d.nodes.find((n) => n.street_num === 32);
  if (penn) s += `<text class="lbl b" x="${X(penn.gy)}" y="${cy - 262}" text-anchor="middle">Penn Station</text>`;
  for (const n of d.nodes) {
    s += `<line class="street" x1="${X(n.gy)}" y1="${cy - 240}" x2="${X(n.gy)}" y2="${cy + 240}"/>`;
    s += `<text class="lbl" x="${X(n.gy)}" y="${cy + 266}" text-anchor="middle">${n.street_num === 59 ? "CPS" : n.street_num}</text>`;
  }
  s += `<rect class="ave" x="${pad - 12}" y="${cy - 16}" width="${W - 2 * pad + 24}" height="32"/>`;
  s += `<text class="lbl b" x="${pad - 8}" y="30">7th Avenue · one way, southbound ←</text>`;
  s += `<text class="lbl" x="${pad - 8}" y="${cy + 296}">← downtown · W 30th St</text><text class="lbl" x="${W - pad + 8}" y="${cy + 296}" text-anchor="end">Central Park South · uptown →</text>`;
  for (const n of d.nodes) {
    const r = 7 + 22 * Math.sqrt(n.people_hit / maxHit);
    s += `<circle class="node ${n.id === S.corner ? "sel" : ""}" data-id="${n.id}" tabindex="0" role="button" aria-label="${esc(n.name)}: ${n.people_hit} people hit" cx="${X(n.gy)}" cy="${cy}" r="${r.toFixed(1)}"><title>${esc(n.name)}: ${n.people_hit} people hit, ${n.killed} killed</title></circle>`;
  }
  d.crashes.forEach((c, i) => {
    s += `<circle class="crash ${c.killed ? "k" : ""}" data-i="${i}" cx="${X(c.gy).toFixed(1)}" cy="${Y(c.gx).toFixed(1)}" r="${c.killed ? 6.5 : 4.5}"><title>${esc(c.date)}: ${c.injured} injured${c.killed ? `, ${c.killed} killed` : ""}. ${esc(c.factor || "")}${c.vehicle ? " (" + esc(c.vehicle) + ")" : ""}</title></circle>`;
  });
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
  const v = S.run.step2, m = v.measurements, sam = m.sam, sp = m.spark, meth = m.method || {};
  const evs = sp.events || [], used = m.sim_inputs || {};
  const conf = evs.filter((e) => e.sam_agrees).length;
  const concepts = sp.concepts || [];
  const dur = m.clip.duration_s;
  const calls = sam.close_call_times || [];
  return {
    sub: "Real video of 7th Ave and Broadway at W 45th St. SAM 3.1 finds and tracks every person and car; Muse Spark watches for behaviour; each Spark note is checked against SAM.",
    visual: `<div class="watch">
      <div class="vid"><video id="vid" src="/static/${v.video}" muted playsinline preload="auto"></video><canvas id="ov" width="932" height="1656"></canvas>
        <div class="hud"><span>SAM 3.1 · <b id="hudN">0</b> people in view</span><span id="hudRoad" hidden>walker in the roadway</span></div>
        <div class="vctl"><button id="vplay" type="button">Play</button><div class="scrub" id="scrub"><div class="rail2"></div>${evs.map((e) => `<div class="mk" style="left:${Math.min(100, (100 * e.t_s) / dur)}%"></div>`).join("")}${calls.map((t) => `<div class="cc" style="left:${(100 * t) / dur}%"></div>`).join("")}<div class="head" id="head"></div></div><span class="vtime" id="vt">0:00</span></div>
      </div>
      <div class="wcol">
        <div class="pipe">
          <div><em>1 · Detect</em><b>${fmt(sam.people_peak_in_frame)}</b><span>people in one frame. Image mode returns every instance; video mode stops at 16.</span></div>
          <div><em>2 · Track</em><b>${fmt(sam.people_tracked)}</b><span>people and ${fmt(sam.vehicles_tracked)} vehicles, from ${fmt(meth.seeds)} box-prompt seeds in ${fmt(meth.track_requests)} requests</span></div>
          <div><em>3 · Read masks</em><b>${fmt(sam.people_in_roadway)}</b><span>walkers stood on asphalt road (${fmt(sam.people_in_roadway_seconds, 1)} person-seconds); ${fmt(sam.close_calls)} close calls with moving cars</span></div>
          <div><em>4 · Check Spark</em><b>${conf}/${evs.length}</b><span>Spark notes confirmed by SAM tracks at that moment</span></div>
        </div>
        <div class="card" style="padding:14px 16px">
          <div class="panelh">Muse Spark proposed what else to look for ${badge("meas", "Muse Spark 1.3 named concepts from the video; SAM 3.1 segmented each on 3 keyframes (confidence ≥ 0.5)")}</div>
          <div class="concepts">${concepts.map((c) => `<span class="chip" data-src="${esc(c.why)}"><b>“${esc(c.phrase)}”</b> SAM found ${c.found}</span>`).join("") || `<span class="muted">none</span>`}</div>
        </div>
        <div class="card" style="padding:14px 4px 0 16px;min-height:0;overflow:hidden">
          <div class="panelh">Muse Spark's timestamped notes ${badge("meas", "Muse Spark 1.3, video input with a clock burned into each frame (timestamps ±0.3 s instead of ~1 s early)")}</div>
          <ul class="notes" id="notes">${evs.map((e) => `<li data-t="${e.t_s}"><span class="ts">${clock(e.t_s)}</span><span>${esc(e.note)}<div class="agree ${e.sam_agrees ? "yes" : "no"}">${e.sam_agrees ? `SAM agrees: ${e.sam_people} people, ${e.sam_vehicles} vehicles in view` : e.t_s > dur ? "SAM cannot confirm: this time is after the clip ends" : "SAM cannot confirm"}</div></span></li>`).join("")}</ul>
        </div>
      </div></div>`,
    rail: `<div class="big">${fmt(sam.people_tracked)} <small>people tracked</small></div>
      <p class="bigcap">${chip("SAM 3.1", `${fmt(meth.image_requests)} image + ${fmt(meth.track_requests)} tracking requests`)} ${chip("Muse Spark 1.3", `${evs.length} notes`)}</p>
      <ul class="facts">
        <li><span class="k">Walking speed ${badge("meas", used.walk_speed_mps?.source || "SAM 3.1 tracks scaled by body height")}</span><b>${sam.walk_speed_mps_median ? fmt(sam.walk_speed_mps_median, 2) + " m/s" : "–"}</b></li>
        <li><span class="k">Walkers on the road surface ${badge("meas", "foot point from each SAM mask inside SAM's 'asphalt road' mask")}</span><b>${fmt(sam.people_in_roadway)}</b></li>
        <li><span class="k">Vehicles: taxis / other cars ${badge("meas", "SAM 'car' tracks overlapping SAM 'yellow taxi' detections")}</span><b>${sam.vehicles_by_type.taxi} / ${sam.vehicles_by_type.car}</b></li>
        <li><span class="k">Faces on billboards excluded</span><b>${fmt(sam.billboard_people_excluded)}</b></li>
      </ul>
      <p class="note">Used in the simulator: walking speed at W 45 St${used.noncompliance ? " and crossing against the signal" : ""}. Nobody crossed against a visible signal in this night clip, so that stays a labelled assumption.</p>`,
    caption: `SAM 3.1 tracked ${fmt(sam.people_tracked)} people and ${fmt(sam.vehicles_tracked)} vehicles; Muse Spark wrote ${evs.length} notes and SAM confirmed ${conf}.`,
    source: "Footage: “TimesSquare-NewYork-may25-2017”, Nesnad, Wikimedia Commons, CC BY-SA 4.0",
    mount: mountVideo,
  };
}

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
      ctx.strokeStyle = `rgba(255,212,0,${1 - Math.max(0, age) / 1.2})`; ctx.lineWidth = 3; ctx.stroke();
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

/* ================================================================ 3 · rebuild it */
const STREAM_LABEL = {
  ave_left: "Left-turning cars × east crosswalk", ave_right: "Right-turning cars × west crosswalk",
  cross_turn: "Cars turning onto 7 Av × south crosswalk", ave_through_n: "7 Av traffic × walkers against signal (N)",
  ave_through_s: "7 Av traffic × walkers against signal (S)", cross_through_e: "Cross traffic × walkers against signal (E)",
  cross_through_w: "Cross traffic × walkers against signal (W)",
};
const PRETTY = {
  ped_vph: ["Walkers crossing / h", (v) => fmt(v)], ave_vph: ["7 Av vehicles / h", (v) => fmt(v)],
  "cross_vph.EB": ["Cross street eastbound / h", (v) => fmt(v)], "cross_vph.WB": ["Cross street westbound / h", (v) => fmt(v)],
  walk_speed_mps: ["Walking speed", (v) => fmt(v, 2) + " m/s"], lpi_s: ["Walk head start", (v) => fmt(v) + " s"],
  excl_ped_s: ["Walk-only phase", (v) => fmt(v) + " s"], ave_turn_share: ["7 Av cars turning, each way", (v) => fmt(v * 100) + "%"],
  turn_speed_mps: ["Turning speed", (v) => fmt(v, 1) + " m/s"], noncompliance: ["Cross against DON'T WALK", (v) => fmt(v * 100) + "%"],
  detect_turn: ["Turning driver sees walker", (v) => fmt(v * 100) + "%"], cycle_s: ["Signal cycle", (v) => fmt(v) + " s"],
};
function step3() {
  const st = S.run.step3, today = st.today, nodes = S.run.step1.nodes;
  const n = node(S.corner), r = today.nodes[n.id];
  const maxSev = Math.max(...nodes.map((x) => today.nodes[x.id].sev_ph));
  const order = { measured: 0, "open-data": 1, assumption: 2 };
  const rows = (st.inputs[n.id] || []).filter((i) => PRETTY[i.input]).sort((a, b) => order[a.kind] - order[b.kind]);
  const all = st.inputs[n.id] || [];
  const cnt = (kk) => all.filter((i) => i.kind === kk).length;
  const streams = Object.entries(r.by_stream).sort((a, b) => b[1].sev - a[1].sev);
  const smax = Math.max(...streams.map(([, x]) => x.sev), 1);
  return {
    sub: `Weekday 5–6 pm on all ${nodes.length} corners: signals cycling, cars turning, people crossing. A near-miss is less than 1.5 s between a walker and a car at the same spot; each one flashes a ring.`,
    visual: `<div class="simgrid">
      <canvas class="simc" id="simc" width="1656" height="1656" aria-label="Top-down simulation of ${esc(n.name)}"></canvas>
      <div class="scol">
        <div class="card" style="padding:14px 16px">
          <div class="panelh">Simulated risk on every corner ${badge("sim", "severity-weighted near-misses per hour; click a bar to watch that corner")}</div>
          <div class="strip" id="strip">${nodes.map((x) => `<button class="${x.id === n.id ? "sel" : ""}" data-id="${x.id}" style="height:${(100 * today.nodes[x.id].sev_ph) / maxSev}%" data-src="${esc(x.name)}: risk ${fmt(today.nodes[x.id].sev_ph, 1)}"></button>`).join("")}</div>
          <div class="striplbl">${nodes.map((x, i) => `<span>${i % 3 === 0 ? (x.street_num === 59 ? "CPS" : x.street_num) : ""}</span>`).join("")}</div>
        </div>
        <div class="card" style="padding:14px 16px">
          <div class="panelh">Where the near-misses come from</div>
          <ul class="streams">${streams.map(([k2, x]) => `<li><span>${STREAM_LABEL[k2] || k2}</span><span class="track"><i style="width:${(100 * x.sev) / smax}%"></i></span><b class="num">${fmt(x.near_miss)}</b></li>`).join("")}</ul>
          <p class="note">Near-misses per hour, one seed. Risk weights each by the car's speed squared.</p>
          <div class="legend" style="margin-top:12px;font-size:14px"><span><i style="background:var(--ink);border-radius:50%"></i>walker</span><span><i style="background:#fff;border:1.5px solid var(--ink)"></i>car</span><span><i style="border:2px solid var(--risk);border-radius:50%"></i>near-miss</span><span><i style="background:#fff;border:1px solid var(--ink)"></i>WALK</span><span><i style="background:var(--risk)"></i>DON'T WALK</span></div>
        </div>
      </div></div>`,
    rail: `<div class="big">${fmt(today.totals.near_miss_ph)}</div>
      <p class="bigcap">simulated near-misses an hour on the corridor ${badge("sim", "corridor simulator: 1 hour × 3 seeds, common random numbers")}. At ${esc(short(n.name))}: ${fmt(r.near_miss_ph)} an hour, ${fmt(r.serious_ph)} serious.</p>
      <div class="panelh" style="margin-top:20px">What the simulator knows about ${esc(short(n.name))}</div>
      <p class="muted" style="margin:0 0 4px;font-size:14px">${cnt("measured")} measured from video · ${cnt("open-data")} from city data · ${cnt("assumption")} labelled assumptions</p>
      <ul class="inputs">${rows.slice(0, 10).map((i) => `<li><span>${PRETTY[i.input][0]}</span><b>${PRETTY[i.input][1](i.v)}</b>${kindBadge(i.kind, i.source)}</li>`).join("")}</ul>`,
    caption: `The simulator replays weekday 5–6 pm on all ${nodes.length} corners: ${fmt(today.totals.near_miss_ph)} simulated near-misses an hour. Simulated near-misses are not injuries.`,
    source: "Inputs: NYC DOT Centerline, LPI list, counts; MTA ridership; SAM 3.1 measurements. Hover any badge for its source.",
    mount() {
      $$("#strip button").forEach((b) => (b.onclick = () => { S.corner = b.dataset.id; go(3); }));
      S.anim = simAnim($("#simc"), st.traces[n.id], n);
    },
  };
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
    ctx.font = F(14); ctx.fillStyle = "#E8590C"; ctx.fillText(`${count} near-misses so far · ×${speed} speed`, 40, 104);
    raf = requestAnimationFrame(frame);
  }
  raf = requestAnimationFrame(frame);
  return { stop: () => cancelAnimationFrame(raf) };
}

/* ================================================================ 4 · can we trust it? */
function step4() {
  const tc = S.run.step4[S.trustMode];
  const sim = tc.lists.simulator.slice(0, 10), real = tc.lists.real.slice(0, 10);
  const realTop = new Set(real.slice(0, 5).map((r) => r.id)), simTop = new Set(sim.slice(0, 5).map((r) => r.id));
  const rowH = 46;
  let lines = "";
  sim.forEach((s, i) => { const j = real.findIndex((r) => r.id === s.id); if (j >= 0) lines += `<line x1="0" y1="${i * rowH + 23}" x2="200" y2="${j * rowH + 23}" stroke="${i < 5 && j < 5 ? "#2F7D4F" : "#B9B2A4"}" stroke-width="${i < 5 && j < 5 ? 4 : 2}"/>`; });
  const X = (r) => `${50 + r * 50}%`;
  const meth = tc.methods, mv = (id) => meth.find((m) => m.id === id);
  const s = mv("simulator");
  const rho = (x) => `${x >= 0 ? "+" : ""}${x.toFixed(2)}`;
  return {
    sub: `The simulator never saw a crash. Its riskiest corners are compared with the ${fmt(tc.held_out.crashes)} pedestrian crashes of ${tc.held_out.from.slice(0, 4)}–${tc.held_out.to.slice(0, 4)} it was not given, next to simpler ways to rank corners.`,
    visual: `<div class="trust">
      <div class="card" style="padding:18px 20px"><div class="slope">
        <div><h3>Simulator's riskiest corners ${badge("sim", "severity-weighted simulated near-misses per hour (metric fixed before scoring)")}</h3><ol>${sim.map((x, i) => `<li class="${realTop.has(x.id) && i < 5 ? "match" : ""}"><span class="rk">${i + 1}</span><span>${esc(short(x.name))}</span><span class="num muted">${fmt(x.value, 1)}</span></li>`).join("")}</ol></div>
        <div style="padding-top:37px"><svg viewBox="0 0 200 ${rowH * 10}" height="${rowH * 10}" aria-hidden="true">${lines}</svg></div>
        <div><h3>Real crashes it never saw ${badge("real", `NYC crashes ${tc.held_out.from} to ${tc.held_out.to}, within ${tc.held_out.snap_m} m of each corner`)}</h3><ol>${real.map((x, i) => `<li class="${simTop.has(x.id) && i < 5 ? "match" : ""}"><span class="rk">${i + 1}</span><span>${esc(short(x.name))}</span><span class="num muted">${x.crashes} crashes</span></li>`).join("")}</ol></div>
      </div></div>
      <div class="card" style="padding:16px 20px"><div class="forest">
        <div class="h">How each method ranks the ${tc.held_out.corners} corners</div><div class="h" style="text-align:right"><span style="text-transform:none">ρ</span></div><div class="h">95% interval, −1 to +1 (line = 0)</div><div class="h" style="text-align:right">Top 5</div>
        ${meth.map((m) => `<div>${esc(m.label.replace("Baseline: ", ""))} ${m.id === "simulator" ? badge("sim", "Spearman rank correlation with held-out crashes; bootstrap over corners") : badge("real", m.label)}</div><div class="num" style="text-align:right"><b>${rho(m.spearman)}</b></div><div><div class="ci"><span class="w" style="left:${X(m.ci95[0])};width:calc(${X(m.ci95[1])} - ${X(m.ci95[0])})"></span><span class="d ${m.id === "simulator" ? "sim" : ""}" style="left:${X(m.spearman)}"></span></div></div><div class="num" style="text-align:right">${m.top5} of 5</div>`).join("")}
      </div></div></div>`,
    rail: `<p style="font-size:30px;font-weight:700;line-height:1.2;margin:0;letter-spacing:-0.01em">${esc(tc.verdict)}</p>
      <ul class="facts">
        <li><span class="k">Traffic volume alone</span><b>ρ ${rho(mv("volume").spearman)}</b></li>
        <li><span class="k">Crash history 2023–24</span><b>ρ ${rho(mv("history").spearman)}</b></li>
        <li><span class="k">One-sided p, simulator</span><b>${s.p_one_sided}</b></li>
      </ul>
      <p class="note warn">Shown as measured: the simulator ranks corners about as well as the previous two years of crashes, and less well than traffic volume. About 3 held-out crashes per corner, so every ranking is noisy.</p>
      <p class="note">No crash data went into the simulator's inputs or parameters.</p>`,
    extra: `<div class="toggle noprint" role="group" aria-label="Simulator inputs"><button type="button" data-m="with_measured_inputs" class="${S.trustMode === "with_measured_inputs" ? "on" : ""}">With video</button><button type="button" data-m="without_measured_inputs" class="${S.trustMode === "without_measured_inputs" ? "on" : ""}">Without</button></div>`,
    caption: tc.verdict,
    source: `Held-out: NYC crashes ${tc.held_out.from} to ${tc.held_out.to}. Spearman ρ; 95% bootstrap intervals over corners (2,000 resamples).`,
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
function tileHTML(id) {
  const a = S.run.step5, ag = a.agents[id], d = ag.decision, rv = a.coordinator.review;
  const accepted = new Set(rv.accepted), rejected = Object.fromEntries(rv.rejected.map((r) => [r.id, r.reason]));
  let state = !d ? "error" : accepted.has(id) ? "funded" : rejected[id] ? "rejected" : d.decision === "keep" ? "kept" : "dropped";
  let what = d ? (d.decision === "keep" ? describe(clean(d.changes)) : "No change worth its cost") : "Agent failed";
  const live = S.agentP < 1 ? agentStates(S.agentP) : null;
  if (live) {
    const ls = live[id]?.state || "idle";
    state = { proposing: "busy", testing: "busy", measured: "busy", verifying: "busy", repair: "busy", retrying: "busy", revising: "busy" }[ls] || ls;
    if (state === "busy") what = live[id]?.last?.changes?.length ? "Testing " + live[id].last.changes.join(" + ") : "Reading its brief";
    if (state === "idle") what = "Waiting for its brief";
  }
  const label = { funded: "funded", kept: "kept", dropped: "dropped", rejected: "rejected", error: "error", busy: "testing", idle: "waiting" }[state] || state;
  const cls = { testing: "busy", waiting: "idle" }[label] || label;
  const tests = ag.tests || [];
  const keptT = d && d.decision === "keep" ? tests.find((t) => JSON.stringify(t.changes) === JSON.stringify(clean(d.changes))) : null;
  const r = keptT?.result;
  const show = r && ["funded", "kept", "rejected"].includes(state);
  return `<button class="tile ${S.agentSel === id ? "sel" : ""}" data-id="${id}" type="button">
    <div class="hd"><span class="nm">${esc(short(ag.name))}</span><span class="st ${cls}">${label}</span></div>
    <div class="pr">${esc(ag.brief?.priority || "")} priority · ${tests.length} test${tests.length === 1 ? "" : "s"}</div>
    <div class="what">${esc(what)}</div>
    ${show ? `<div class="res">risk ${fmt(r.risk_before, 0)} → <span class="${r.risk < r.risk_before ? "down" : "up"}">${fmt(r.risk, 0)}</span> · ${sign(r.car_delay_s_per_veh - r.car_delay_before)} s</div>` : `<div class="res muted">&nbsp;</div>`}
  </button>`;
}
function step5() {
  const a = S.run.step5, today = S.run.step3.today.nodes, co = a.coordinator, rv = co.review;
  const ids = Object.keys(a.agents).sort((x, y) => today[y].sev_ph - today[x].sev_ph);
  const tests = ids.reduce((s, i) => s + (a.agents[i].tests || []).length, 0);
  const budget = co.budget_usd || 0, spent = co.accepted_cost_usd || 0;
  const comb = a.combined;
  const sel = S.agentSel && a.agents[S.agentSel];
  const funded = sel && rv.accepted.includes(S.agentSel);
  const railDefault = `<div class="big">${rv.accepted.length} <small>fixes funded</small></div>
      <p class="bigcap">${kmoney(spent)} of the ${kmoney(budget)} quarterly budget ${badge("asm", "quarterly capital for this corridor is an assumption (set with --budget); costs are Spark-cited unit costs, midpoint")} · ${rv.rejected.length} rejected</p>
      <ul class="rej">${rv.rejected.slice(0, 5).map((r) => `<li><span class="st rejected">rejected</span> <b>${esc(nameOf(r.id))}</b>: ${esc(r.reason)}</li>`).join("") || `<li class="muted">No proposal was rejected.</li>`}</ul>
      <p class="note">Every number on a tile is a simulator run the agent asked for: ${tests} runs in total. Click a tile to see them.</p>`;
  const railSel = sel ? `<div class="panelh" style="font-size:20px">${esc(sel.name)} <span class="st ${funded ? "funded" : "dropped"}">${funded ? "funded" : "not funded"}</span></div>
      ${sel.brief ? `<p class="note" style="margin-top:0">Coordinator's brief: ${esc(sel.brief.objective)}. Focus: ${esc(sel.brief.focus)}.</p>` : ""}
      <ul class="tests" style="margin-top:12px">${(sel.tests || []).map((t) => `<li><span>${esc(describe(t.changes))}${t.result.future !== "today" ? ` <span class="chip">${esc(t.result.future)}</span>` : ""}${t.by === "system" ? ` <span class="chip">verified</span>` : ""}</span><span class="num">${fmt(t.result.risk_before, 0)} → <span class="${t.result.risk < t.result.risk_before ? "down" : "up"}">${fmt(t.result.risk, 0)}</span> · ${sign(t.result.car_delay_s_per_veh - t.result.car_delay_before)} s</span></li>`).join("")}</ul>
      ${sel.decision ? `<p class="note">${esc(sel.decision.reason)}</p>` : ""}
      <p style="margin:12px 0 0"><button class="btn ghost" id="backCo" type="button" style="font-size:14px;padding:6px 12px">← Coordinator's view</button></p>` : "";
  return {
    sub: "A Muse Wonder coordinator briefs one Wonder agent per corner. Each agent tests changes in the simulator and keeps or drops them; the coordinator funds the best set within budget and rejects fixes that push risk to the next block.",
    visual: `<div class="agents">
      <div class="card coord"><div><h3>Coordinator ${chip("Muse Wonder", `${ids.length} corner agents`)}</h3><p>${esc(co.plan_note || rv.note || "")}</p>
        <p style="margin-top:6px">Combined plan, simulated: risk ${fmt(comb.risk_before, 0)} → <b>${fmt(comb.risk, 0)}</b> ${badge("sim", "severity-weighted near-misses per hour, all 30 corners")}, added car delay ${fmt(comb.added_car_delay_share * 100, 1)}%</p></div>
        <div class="budget"><div class="lab"><span>Budget used</span><b>${kmoney(spent)} / ${kmoney(budget)}</b></div><div class="bar"><i style="width:${Math.min(100, (100 * spent) / Math.max(budget, 1))}%"></i></div></div></div>
      <div class="tiles" id="tiles">${ids.map(tileHTML).join("")}</div></div>`,
    rail: sel ? railSel : railDefault,
    extra: `<button class="btn ghost noprint" id="replayAgents" type="button">↻ Replay</button>`,
    caption: `The coordinator briefed ${ids.length} Wonder agents; they ran ${tests} simulations; ${rv.accepted.length} fixes funded for ${kmoney(spent)}, ${rv.rejected.length} rejected.`,
    source: "Muse Wonder (Responses API, function tools). Each test: corridor simulator, 1 hour × 3 seeds. Costs: Muse Spark web search with citations.",
    mount() {
      bindTiles();
      if ($("#backCo")) $("#backCo").onclick = () => { S.agentSel = null; go(5); };
      $("#replayAgents").onclick = () => replayAgents(15000);
      if (S.playing) replayAgents((PLAY_SECONDS[5] - 4) * 1000);
    },
  };
}
function bindTiles() { $$(".tile").forEach((c) => (c.onclick = () => { S.agentSel = S.agentSel === c.dataset.id ? null : c.dataset.id; S.agentP = 1; go(5); })); }
function replayAgents(ms) {
  const t0 = performance.now();
  const ids = Object.keys(S.run.step5.agents).sort((x, y) => S.run.step3.today.nodes[y].sev_ph - S.run.step3.today.nodes[x].sev_ph);
  const tick = () => {
    if (S.step !== 5 || !$("#tiles")) return;
    S.agentP = Math.min(1, (performance.now() - t0) / ms);
    $("#tiles").innerHTML = ids.map(tileHTML).join("");
    bindTiles();
    if (S.agentP < 1) S.timers.push(setTimeout(tick, 120));
  };
  S.agentP = 0; tick();
}

/* ================================================================ 6 · stress the future */
function step6() {
  const s6 = S.run.step6, list = s6.stress;
  if (!list.length) return { visual: `<div class="empty">The coordinator chose no stress futures in this run.</div>`, rail: "" };
  if (S.futSel == null) { const ib = list.findIndex((x) => x.breaks.length); S.futSel = ib >= 0 ? ib : 0; }
  const f = list[Math.min(S.futSel, list.length - 1)];
  const status = (x) => (x.breaks.length ? (x.revisions.length && x.holds ? "fixed" : "breaks") : "holds");
  const stText = { holds: "holds", breaks: "breaks", fixed: "broke → fixed" };
  const fin = s6.final_checks, holds = fin.filter((c) => c.holds).length;
  const mb = (x) => { const fr = x.after || x.first; const mx = Math.max(fr.risk_before, fr.risk, 1);
    return `<div class="minibars"><div><span>no plan</span><span class="tr"><i style="width:${(100 * fr.risk_before) / mx}%;background:#B9B2A4"></i></span><span class="num">${fmt(fr.risk_before, 0)}</span></div><div><span>plan</span><span class="tr"><i style="width:${(100 * fr.risk) / mx}%;background:${status(x) === "breaks" ? "var(--risk)" : "var(--hold)"}"></i></span><span class="num">${fmt(fr.risk, 0)}</span></div></div>`; };
  return {
    sub: "The coordinator picks the futures most likely to break the plan. A fix breaks if it leaves any corner worse than doing nothing, loses most of its benefit, or blows the car-delay limit. Broken fixes go back to their agent.",
    visual: `<div class="stress">
      <div class="futs">${list.map((x, i) => `<button class="fut ${i === S.futSel ? "sel" : ""}" data-i="${i}" type="button"><div class="lab">${esc(x.label)}</div><div class="why">${esc(x.why)}</div>${mb(x)}<span class="st ${status(x)}" style="align-self:flex-start">${stText[status(x)]}</span></button>`).join("")}</div>
      <div class="story">
        <div class="beat ${f.breaks.length ? "b" : "h"}"><h3>1 · Test the plan under “${esc(f.label)}” ${badge("sim", "corridor simulator with this future's multipliers")}</h3>
          <p>Corridor risk ${fmt(f.first.risk_before, 0)} without the plan, <b>${fmt(f.first.risk, 0)}</b> with it. Added car delay ${fmt(f.first.added_car_delay_share * 100, 1)}%.</p>
          ${f.breaks.length ? f.breaks.slice(0, 3).map((b) => `<p><span class="st breaks">breaks</span> ${esc(b.problem)}</p>`).join("") + (f.breaks.length > 3 ? `<p class="muted">and ${f.breaks.length - 3} more</p>` : "") : `<p><span class="st holds">holds</span> Every fix still helps and no corner ends up worse than doing nothing.</p>`}</div>
        <div class="beat r"><h3>2 · The agent revises</h3>
          ${f.revisions.length ? f.revisions.map((r) => `<p><b>${esc(nameOf(r.node))}</b>: ${esc(describe(r.from))} → <b>${esc(describe(r.to))}</b></p><p class="muted" style="font-size:14px">${esc(r.reason)}</p>`).join("") : `<p class="muted">Nothing to revise under this future.</p>`}</div>
        <div class="beat ${f.revisions.length ? (f.holds ? "h" : "b") : "h"}"><h3>3 · Test again</h3>
          ${f.after ? `<p>Corridor risk ${fmt(f.after.risk_before, 0)} without, <b>${fmt(f.after.risk, 0)}</b> with the revised plan.</p><p><span class="st ${f.holds ? "holds" : "breaks"}">${f.holds ? "holds" : "still breaks"}</span></p>${(f.breaks_after || []).slice(0, 2).map((b) => `<p class="muted" style="font-size:14px">${esc(b.problem)}</p>`).join("")}` : `<p class="muted">No retest needed.</p>`}</div>
      </div></div>`,
    rail: `<div class="big">${holds} <small>of ${fin.length} futures hold</small></div>
      <p class="bigcap">with the final plan ${badge("sim", "each future re-simulated with the final plan vs no plan")}</p>
      <ul class="facts">${fin.map((c) => `<li><span class="k">${esc(c.label)}</span><span class="st ${c.holds ? "holds" : "breaks"}">${c.holds ? "holds" : "breaks"}</span></li>`).join("")}</ul>`,
    caption: `${list.filter((x) => x.breaks.length).length} of ${list.length} futures broke the first plan; after revision the final plan holds under ${holds} of ${fin.length}.`,
    source: "Futures: holiday crowds ×1.7 walkers; shows letting out ×2.6 at W 43–50; rain at night −12 pts detection; bus lane; 30% ignore turn bans.",
    mount() { $$(".fut").forEach((b) => (b.onclick = () => { S.futSel = +b.dataset.i; go(6); })); },
  };
}

/* ================================================================ 7 · the plan */
function step7() {
  const p = S.run.step7, t = p.totals, w = p.watcher, co = S.run.step5.coordinator;
  const kinds = [...new Set(p.items.flatMap((i) => i.cost_kinds))].filter((k) => p.costs[k]);
  const idx = Object.fromEntries(kinds.map((k, i) => [k, i + 1]));
  const shown = p.items.slice(0, 9);
  const date = (S.run.created || "").slice(0, 10);
  return {
    sub: "A one-page brief for the engineer: what to fund first, the simulated benefit, the honest cost in car delay, and prices with sources.",
    visual: `<article class="brief">
      <h2>7th Avenue safety plan: W 30th St to Central Park South</h2>
      <div class="meta">Corner Case run ${esc(S.run.id)} · ${esc(date)} · simulated weekday 5–6 pm · ${esc(Object.values(S.run.models).filter((m) => !m.includes("image")).join(", "))}</div>
      <table class="t"><thead><tr><th>#</th><th>Corner</th><th>Change</th><th class="r">Near-misses / h ${badge("sim", "simulated, today vs plan")}</th><th class="r">Serious / h</th><th class="r">Car delay</th><th class="r">Rough cost</th></tr></thead><tbody>
      ${shown.map((i) => `<tr><td class="num">${i.priority}</td><td><b>${esc(short(i.name))}</b></td><td>${esc(i.changes.join(" + "))}</td><td class="r">${fmt(i.near_misses_before)} → <b>${fmt(i.near_misses_after)}</b></td><td class="r">${fmt(i.serious_before)} → ${fmt(i.serious_after)}</td><td class="r">${sign(i.car_delay_added_s)} s</td><td class="r">${money(i.cost_low)}–${money(i.cost_high)} <sup>${i.cost_kinds.map((k) => idx[k]).filter(Boolean).join(",")}</sup></td></tr>`).join("")}
      ${p.items.length > shown.length ? `<tr><td></td><td colspan="6" class="muted">and ${p.items.length - shown.length} more corners in the printed brief</td></tr>` : ""}
      <tr class="total"><td></td><td>Corridor</td><td>${p.items.length} corners</td><td class="r">${fmt(t.near_misses_before)} → ${fmt(t.near_misses_after)}</td><td class="r">${fmt(t.serious_before)} → ${fmt(t.serious_after)}</td><td class="r">+${fmt(t.added_car_delay_share * 100, 1)}%</td><td class="r">${money(t.cost_low)}–${money(t.cost_high)}</td></tr>
      </tbody></table>
      <p class="note">Order: simulated risk cut per dollar. Near-misses are simulated, not injuries. Within the ${kmoney(co.budget_usd || 0)} quarterly budget (assumption).</p>
      <div class="spacer"></div>
      <h3>Cost sources, found by Muse Spark web search</h3>
      <ol class="sources">${kinds.map((k) => { const c = p.costs[k]; return `<li><b>${esc(c.item)}</b>: ${money(c.low_usd)}–${money(c.high_usd)} ${esc(c.unit)}. ${c.source_urls.slice(0, 2).map((u) => `<a href="${esc(u)}" target="_blank" rel="noopener">${esc(u.replace(/^https?:\/\/(www\.)?/, "").slice(0, 60))}</a>`).join(" · ")}</li>`; }).join("")}</ol>
    </article>`,
    rail: `<div class="big">${fmt(t.near_misses_before)} → ${fmt(t.near_misses_after)}</div>
      <p class="bigcap">simulated near-misses an hour, for ${money(t.cost_low)}–${money(t.cost_high)} and +${fmt(t.added_car_delay_share * 100, 1)}% car delay ${badge("sim", "corridor simulator; costs Spark-cited")}</p>
      <div class="panelh" style="margin-top:22px;font-size:19px">After it's built: know in weeks, not years</div>
      <p style="font-size:16px;line-height:1.4;margin:0 0 10px;color:var(--ink-2)">A camera at each changed corner feeds SAM 3.1 tracks to an always-on <b>Muse Glimmer</b> watcher (open weights, on Modal). It logs close calls between walkers and moving cars every week.</p>
      <table class="t"><thead><tr><th>W 45 St clip</th><th class="r">Walkers</th><th class="r">Vehicles</th><th class="r">Close calls</th></tr></thead><tbody>
        ${w.summaries.map((s) => `<tr><td>${esc(s.interval)}</td><td class="r">${s.walkers}</td><td class="r">${s.vehicles}</td><td class="r">${s.close_calls}</td></tr>`).join("")}</tbody></table>
      <p class="note">${w.status === "live" ? `Glimmer (${esc(w.model)}): ${esc(w.result?.trend_note || "")}` : `Watcher status in this run: <b>${w.status === "not_deployed" ? "not deployed" : esc(w.status)}</b>; counts above come from SAM tracks.`}</p>`,
    caption: `Fund ${p.items.length} changes: simulated near-misses ${fmt(t.near_misses_before)} → ${fmt(t.near_misses_after)} an hour, +${fmt(t.added_car_delay_share * 100, 1)}% car delay, ${money(t.cost_low)}–${money(t.cost_high)}.`,
    source: "Costs: Muse Spark 1.3 web search with citations (FHWA, NYC DOT). Watcher: Muse Glimmer on Modal (vLLM).",
  };
}

const SCREENS = { 1: step1, 2: step2, 3: step3, 4: step4, 5: step5, 6: step6, 7: step7 };

/* ---------------------------------------------------------------- play the story */
function startPlay() {
  stopPlay();
  S.playing = true; S.agentP = 1; S.agentSel = null; S.futSel = null;
  $("#playBtn").textContent = "■ Stop";
  $("#playbar").hidden = false;
  const total = Object.values(PLAY_SECONDS).reduce((a, b) => a + b, 0);
  let acc = 0;
  for (let n = 1; n <= 7; n++) { const at = acc; S.timers.push(setTimeout(() => go(n), at * 1000)); acc += PLAY_SECONDS[n]; }
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
