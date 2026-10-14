// FX bus panel for the dev console (docs/EFFECTS_LAYER_DESIGN.md §I.3, API §D.2-D.3).
// GET / PUT /api/sessions/{id}/fx (whole stack, if_rev, retry once on 409), session.fx events, Auto FX (auto.fx +
// direction.fx).  With ?mock=1 the bus lives in memory (MockFx) so the panel works without the backend.
import { h, put, http, ctx, debounce, clamp01, fmt, toast, explain, store, STAGES, FX_STATES } from "./libkit.js";

const AUTO_KEY = "sm.fx.auto";            // per-viewer: the Auto FX descriptors survive a reload
const POLL_MS = 500;                      // 2 Hz while visible and a session can hold a bus
const MAX_SLOTS = 6;
const stageIdx = (s) => { const i = STAGES.indexOf(s); return i < 0 ? 99 : i; };

// ---------------------------------------------------------------- stack items (GET slot -> PUT item)
function itemOf(s) {
  const o = { id: s.id };
  if (s.preset) o.preset = s.preset;
  else {
    o.effect = s.effect;
    const b = s.bindings && typeof s.bindings === "object" ? s.bindings : {};
    const p = { ...(s.params || {}) };
    for (const k of Object.keys(b)) delete p[k];          // the same key in params and bindings is a 400
    if (Object.keys(p).length) o.params = p;
    if (Object.keys(b).length) o.bindings = b;
  }
  if (s.level != null) o.level = s.level;
  if (s.bypass) o.bypass = true;
  return o;
}

function specItem(spec) {
  if (typeof spec === "string") return { preset: spec };
  if (spec && typeof spec === "object") return { ...spec };
  return null;
}

function sameSpec(slot, spec) {
  if (typeof spec === "string") return slot.preset === spec;
  return !slot.preset && spec && slot.effect === spec.effect;
}

function masterOut(m) {
  const pick = (v, d) => (v == null ? d : typeof v === "object" ? { bind: v.bind, range: v.range, ...(v.rest != null ? { rest: v.rest } : {}) } : v);
  return { amount: pick(m?.amount, 1), pulse: pick(m?.pulse, 1) };
}

// ---------------------------------------------------------------- in-memory bus for /dev?mock=1
class MockFx {
  constructor(lookup, effects) {
    this.lookup = lookup; this.effects = effects;
    this.rev = 0; this.fade_s = 2; this.master = { amount: 1, pulse: 1 }; this.slots = []; this.n = 0; this.seq = 0;
    this.auto = { enabled: false, max: 2, density: 0.5, rate: 0.5, next_at: 0, last: null };
  }
  now() { return performance.now() / 1000; }
  lvl(s, now) { const k = s.fade > 0 ? clamp01((now - s.t0) / s.fade) : 1; return s.from + (s.to - s.from) * k; }
  get() {
    const now = this.now();
    this.tickAuto(now);
    this.slots = this.slots.filter((s) => !(s.removing && this.lvl(s, now) <= 1e-3));
    const stack = this.slots.filter((s) => !s.removing).sort((a, b) => stageIdx(a.stage) - stageIdx(b.stage) || a.seq - b.seq).map((s) => {
      const ln = this.lvl(s, now), base = s.params.amount ?? 0.7;
      const state = s.bypass ? "bypassed" : ln < s.to - 1e-3 ? "fading_in" : ln > s.to + 1e-3 ? "fading_out" : "active";
      return { id: s.id, effect: s.effect, preset: s.preset, label: s.label, stage: s.stage, params: { ...s.params },
               bindings: { pulse: { bind: "beat", range: [0, 1], rest: 0 } }, level: s.level, bypass: s.bypass, origin: s.origin,
               state, level_now: ln, amount_now: ln * base * (0.8 + 0.2 * Math.sin(now * 3 + s.seq)), cost_ms: s.cost };
    });
    const a = this.auto;
    return { session: "mock", rev: this.rev, fade_s: this.fade_s, open: true, master: { ...this.master, level_now: 1 }, stack,
             auto: { enabled: a.enabled, max: a.max, density: a.density, rate: a.rate, armed: a.enabled,
                     next_in_s: a.enabled ? Math.max(0, a.next_at - now) : null, last: a.last }, errors: [] };
  }
  resolve(it) {
    const item = this.lookup(it.preset ?? (it.effect ? { effect: it.effect } : null));
    if (!item) return null;
    return { effect: item.plugin, preset: item.preset, label: item.label, stage: item.stage || item.facets?.stage?.[0] || "stylise",
             params: { amount: 0.7, ...(item.params || {}), ...(it.params || {}) }, cost: item.bench_ms_4k ?? null };
  }
  put(body, origin = "manual") {
    if (body.if_rev != null && body.if_rev !== this.rev) return { ok: false, status: 409, error: "fx_conflict", detail: `the bus is at rev ${this.rev}` };
    const stack = body.stack || [];
    if (stack.length > MAX_SLOTS) return { ok: false, status: 400, error: "too_many_effects", detail: `at most ${MAX_SLOTS}` };
    const resolved = [];
    for (const [i, it] of stack.entries()) {
      const r = this.resolve(it);
      if (!r) return { ok: false, status: 400, error: it.preset ? "unknown_preset" : "unknown_effect", detail: `item ${i}: ${it.preset || it.effect}` };
      resolved.push([it, r]);
    }
    const now = this.now(), fade = Math.max(0, Math.min(30, body.fade_s ?? 2));
    const unmatched = new Set(this.slots.filter((s) => !s.removing));
    const keep = [];
    for (const [it, r] of resolved) {
      let s = it.id ? [...unmatched].find((x) => x.id === it.id) : [...unmatched].find((x) => x.effect === r.effect && x.preset === r.preset);
      const level = it.bypass ? 0 : it.level ?? 1;
      if (s) {
        unmatched.delete(s);
        if (s.level !== (it.level ?? 1) || s.bypass !== !!it.bypass) { if (origin === "manual") s.origin = "manual"; }
        const cur = this.lvl(s, now);
        Object.assign(s, { from: cur, to: level, t0: now, fade, level: it.level ?? 1, bypass: !!it.bypass });
      } else {
        s = { id: it.id || `fx-${++this.n}`, ...r, origin, seq: ++this.seq, from: 0, to: level, t0: now, fade, level: it.level ?? 1, bypass: !!it.bypass };
        this.slots.push(s);
      }
      keep.push(s);
    }
    for (const s of unmatched) Object.assign(s, { removing: true, from: this.lvl(s, now), to: 0, t0: now, fade });
    this.rev += 1; this.fade_s = fade;
    if (body.master) this.master = masterOut(body.master);
    return { ok: true, status: 200, data: this.get() };
  }
  setAuto(a) { Object.assign(this.auto, a); if (a.enabled) this.auto.next_at = this.now() + 4; }
  tickAuto(now) {
    const a = this.auto;
    if (!a.enabled || now < a.next_at) return;
    a.next_at = now + (20 - 14 * a.rate) * (0.75 + Math.random() * 0.5);
    const live = this.slots.filter((s) => !s.removing);
    const autos = live.filter((s) => s.origin === "auto").sort((x, y) => x.seq - y.seq);
    const k = Math.min(a.max, Math.floor(a.density * a.max + Math.random()));
    const used = new Set(live.map((s) => s.stage));
    const pool = this.effects().filter((e) => e.preset && e.auto !== false && !used.has(e.stage || e.facets?.stage?.[0]));
    const pick = pool[Math.floor(Math.random() * pool.length)];
    const items = live.map((s) => ({ id: s.id, ...(s.preset ? { preset: s.preset } : { effect: s.effect }), level: s.level, bypass: s.bypass }));
    let op = null;
    if (autos.length > k) { op = { op: "remove", preset: autos[0].preset || autos[0].effect }; items.splice(items.findIndex((x) => x.id === autos[0].id), 1); }
    else if (autos.length < k && pick && live.length < MAX_SLOTS) { op = { op: "add", preset: pick.preset }; items.push({ preset: pick.preset }); }
    else if (autos.length && pick && Math.random() < 0.6) {
      op = { op: "swap", preset: pick.preset };
      items.splice(items.findIndex((x) => x.id === autos[0].id), 1); items.push({ preset: pick.preset });
    }
    if (!op) return;
    this.put({ stack: items, fade_s: 6 - 4 * a.rate }, "auto");
    a.last = { ...op, why: `${op.preset} (mock pick of ${pool.length})`, at_pts: now };
  }
}

// ---------------------------------------------------------------- the panel
export function mountFxBus(root, { lookup, effects } = {}) {
  const listeners = new Set();
  const autoCfg = { enabled: false, max: 2, density: 0.5, rate: 0.5, stages: [], ...store.get(AUTO_KEY, {}) };
  if (!Array.isArray(autoCfg.stages)) autoCfg.stages = [];
  const mock = ctx.mock ? new MockFx(lookup || (() => null), effects || (() => [])) : null;
  let fx = null;               // the last GET /fx body
  let avail = "unknown";       // unknown | ok | missing
  let missingAt = 0;
  let lastSid = null;
  let visible = true;
  let dragging = new Set();    // slot ids whose slider the user is holding
  let inflight = null;

  // dev.html sends direction.fx with every direction PATCH once the backend knows it (unknown fields are a 400)
  const dev = ctx.dev();
  if (dev) dev.fxDirection = () => (ctx.features().has("fx_auto")
    ? { density: autoCfg.density, rate: autoCfg.rate, stages: autoCfg.stages.length ? [...autoCfg.stages] : null, moods: null }
    : undefined);

  // ---- skeleton
  const pill = h("span", { class: "lib-pill" }, "–");
  const rev = h("span", { class: "small lib-sub" });
  const msg = h("div", { class: "fx-empty" });
  const rows = h("div", { class: "fx-rows" });
  const fadeIn = h("input", { type: "number", min: 0, max: 30, step: 0.5, value: 2, title: "fade seconds for changes made here", "aria-label": "fade seconds" });
  const clearBtn = h("button", { onclick: () => clearAll() }, "Clear");
  const masterNow = h("span", { class: "small" });
  const tools = h("div", { class: "fx-tools" }, clearBtn, h("label", { class: "small", style: { margin: 0, display: "flex", gap: "4px", alignItems: "center" } }, "fade", fadeIn, "s"), h("span", { style: { flex: 1 } }), masterNow);

  // Auto FX unroll
  const autoChk = h("input", { type: "checkbox" });
  autoChk.checked = !!autoCfg.enabled;
  const autoPill = h("span", { class: "lib-pill" }, autoCfg.enabled ? "on" : "off");
  const maxSel = h("select", { "aria-label": "max auto effects" }, [0, 1, 2, 3, 4].map((n) => h("option", { value: n }, String(n))));
  maxSel.value = String(autoCfg.max);
  const slider = (label, key, title) => {
    const inp = h("input", { type: "range", min: 0, max: 1, step: 0.01, value: autoCfg[key], "aria-label": label });
    const v = h("span", { class: "v" }, fmt(autoCfg[key]));
    inp.addEventListener("input", () => { autoCfg[key] = parseFloat(inp.value); v.textContent = fmt(autoCfg[key]); saveAuto(); directionSoon(); if (mock) mock.setAuto({ [key]: autoCfg[key] }); });
    return h("div", { class: "slider", title }, h("span", {}, label), inp, v);
  };
  const stageChips = h("div", { class: "chips fx-stagechips" }, STAGES.map((s) => {
    const c = h("button", { class: "lib-fchip", "aria-pressed": String(autoCfg.stages.includes(s)), onclick: () => {
      const on = !autoCfg.stages.includes(s);
      autoCfg.stages = on ? [...autoCfg.stages, s] : autoCfg.stages.filter((x) => x !== s);
      c.setAttribute("aria-pressed", String(on)); saveAuto(); directionSoon();
    } }, s);
    return c;
  }));
  const autoStatus = h("div", { class: "fx-status" }, "–");
  const autoNote = h("div", { class: "lib-note" });
  const autoBox = h("details", { class: "fx-sub" },
    h("summary", {}, "Auto FX ", autoPill),
    h("div", { class: "row", style: { marginTop: "6px" } },
      h("label", { class: "small", style: { margin: 0, flex: "0 0 auto" } }, autoChk, " let the director add, swap and remove effects"),
      h("label", { class: "small", style: { margin: 0, flex: "0 0 auto" } }, "max ", maxSel)),
    slider("Density", "density", "how many bus effects, of max"),
    slider("Rate", "rate", "how often they change: 0 = every 90 s, 1 = every 15 s"),
    h("label", { class: "small" }, "Stages (none = all)"), stageChips, autoStatus, autoNote);

  // Master unroll
  const mAmt = h("input", { type: "range", min: 0, max: 1, step: 0.01, value: 1, "aria-label": "master amount" });
  const mAmtV = h("span", { class: "v" }, "1.00");
  const mAmtF = h("select", { "aria-label": "master amount follows" }, ["", "level_slow", "activity_slow"].map((s) => h("option", { value: s }, s || "fixed")));
  const mPul = h("input", { type: "range", min: 0, max: 1, step: 0.01, value: 1, "aria-label": "master pulse" });
  const mPulV = h("span", { class: "v" }, "1.00");
  const mPulF = h("select", { "aria-label": "master pulse follows" }, ["", "level", "beat", "level_slow"].map((s) => h("option", { value: s }, s || "fixed")));
  const masterBox = h("details", { class: "fx-sub" },
    h("summary", {}, "Master (whole bus; DMX can drive it)"),
    h("div", { class: "slider" }, h("span", {}, "Amount"), mAmt, mAmtV),
    h("div", { class: "row" }, h("span", { class: "small", style: { flex: "0 0 auto" } }, "amount follows"), mAmtF),
    h("div", { class: "slider" }, h("span", {}, "Pulse"), mPul, mPulV),
    h("div", { class: "row" }, h("span", { class: "small", style: { flex: "0 0 auto" } }, "pulse follows"), mPulF),
    h("div", { class: "lib-note" }, "Following a signal: the slider sets the top of the range, bound to 0 at silence."));

  put(root, 
    h("h2", { class: "lib-h2" }, "FX bus ", pill, rev),
    msg, rows, tools, autoBox, masterBox);

  // ---- helpers
  const fadeS = () => { const v = parseFloat(fadeIn.value); return isFinite(v) ? Math.max(0, Math.min(30, v)) : 2; };
  const saveAuto = () => store.set(AUTO_KEY, autoCfg);
  const canEdit = () => !!mock || (avail !== "missing" && !!ctx.sid() && FX_STATES.has(ctx.state()));
  const notify = () => listeners.forEach((fn) => { try { fn(fx); } catch (_) {} });

  async function getFx() {
    if (mock) return { ok: true, status: 200, data: mock.get() };
    const sid = ctx.sid();
    if (!sid) return { ok: false, status: 0, error: "no_session", detail: "no session" };
    const r = await http("GET", `/api/sessions/${sid}/fx`);
    if (r.status === 404 && !/session/.test(`${r.error} ${r.detail}`)) { avail = "missing"; missingAt = Date.now(); }
    else if (r.ok) avail = "ok";
    return r;
  }
  async function putFx(body) {
    if (mock) return mock.put(body);
    return http("PUT", `/api/sessions/${ctx.sid()}/fx`, body);
  }

  // read-modify-write with if_rev; one retry on a conflict
  async function mutate(fn, what) {
    if (!canEdit()) { toast(ctx.sid() ? "The FX bus is not available in this state" : "Start the live demo first: the bus belongs to a session", "err"); return null; }
    if (inflight) await inflight.catch(() => {});
    const run = (async () => {
      for (let attempt = 0; attempt < 2; attempt++) {
        const cur = await getFx();
        if (!cur.ok) { toast(explain(cur, what), "err"); return cur; }
        const body = fn(cur.data);
        if (!body) return null;
        body.if_rev = cur.data.rev;
        if (body.fade_s == null) body.fade_s = fadeS();
        if (body.master === undefined) body.master = masterOut(cur.data.master);
        const res = await putFx(body);
        if (res.ok) {
          fx = res.data; render(); notify();
          for (const rj of res.data?.rejected || []) toast(`${rj.effect || "effect"} rejected: ${rj.error}${rj.detail ? " — " + rj.detail : ""}`, "err", 6000);
          return res;
        }
        if (res.status === 409 && res.error === "fx_conflict" && attempt === 0) continue;
        toast(explain(res, what), "err", 5000);
        ctx.log(`PUT fx -> ${res.status} ${res.error}: ${res.detail}`, "err");
        return res;
      }
      return null;
    })();
    inflight = run;
    try { return await run; } finally { if (inflight === run) inflight = null; }
  }

  function add(item) {
    const spec = item.spec ?? (item.preset || { effect: item.plugin });
    const it = specItem(spec);
    if (!it) return Promise.resolve(null);
    return mutate((cur) => {
      if (cur.stack.some((s) => sameSpec(s, spec))) { toast(`${item.label} is already on the bus`, "info"); return null; }
      if (cur.stack.length >= MAX_SLOTS) { toast(`The bus holds at most ${MAX_SLOTS} effects: remove one first`, "err"); return null; }
      return { stack: [...cur.stack.map(itemOf), it] };
    }, `add ${item.label}`).then((r) => { if (r?.ok) toast(`Added ${item.label} to the FX bus`); return r; });
  }
  function removeSpec(spec) {
    return mutate((cur) => ({ stack: cur.stack.filter((s) => !sameSpec(s, spec)).map(itemOf) }), "remove");
  }
  const remove = (id) => mutate((cur) => ({ stack: cur.stack.filter((s) => s.id !== id).map(itemOf) }), "remove");
  const setBypass = (id, on) => mutate((cur) => ({ stack: cur.stack.map((s) => (s.id === id ? { ...itemOf(s), bypass: on || undefined } : itemOf(s))) }), "bypass");
  const levelSoon = new Map();
  function setLevel(id, v) {
    if (!levelSoon.has(id)) levelSoon.set(id, debounce((x) => mutate((cur) => ({
      stack: cur.stack.map((s) => (s.id === id ? { ...itemOf(s), level: x } : itemOf(s))) }), "level").finally(() => dragging.delete(id)), 300));
    levelSoon.get(id)(v);
  }
  function clearAll() { mutate(() => ({ stack: [] }), "clear"); }

  function masterBody() {
    const val = (inp, sel) => { const v = parseFloat(inp.value); return sel.value ? { bind: sel.value, range: [0, v] } : v; };
    return { amount: val(mAmt, mAmtF), pulse: val(mPul, mPulF) };
  }
  const masterSoon = debounce(() => mutate((cur) => ({ stack: cur.stack.map(itemOf), master: masterBody() }), "master"), 300);
  for (const [inp, v] of [[mAmt, mAmtV], [mPul, mPulV]]) inp.addEventListener("input", () => { v.textContent = fmt(parseFloat(inp.value)); masterSoon(); });
  for (const sel of [mAmtF, mPulF]) sel.addEventListener("change", masterSoon);

  // ---- Auto FX
  async function patchAuto() {
    autoCfg.enabled = autoChk.checked; autoCfg.max = parseInt(maxSel.value, 10); saveAuto();
    autoPill.textContent = autoCfg.enabled ? "on" : "off"; autoPill.className = `lib-pill ${autoCfg.enabled ? "acc" : ""}`;
    if (mock) { mock.setAuto({ enabled: autoCfg.enabled, max: autoCfg.max, density: autoCfg.density, rate: autoCfg.rate }); return; }
    const sid = ctx.sid();
    if (!sid || !ctx.features().has("fx_auto")) return;
    const sceneAuto = document.getElementById("aEnabled");
    const r = await http("PATCH", `/api/sessions/${sid}`, { auto: { ...(sceneAuto ? { enabled: sceneAuto.checked } : {}), fx: { enabled: autoCfg.enabled, max: autoCfg.max, pool: "all" } } });
    if (!r.ok) toast(explain(r, "Auto FX"), "err");
  }
  autoChk.addEventListener("change", patchAuto);
  maxSel.addEventListener("change", patchAuto);
  const directionSoon = debounce(async () => {
    const sid = ctx.sid(), dir = ctx.dev()?.direction?.();
    if (mock || !sid || !dir || !dir.fx || ["ended", "failed"].includes(ctx.state())) return;
    const r = await http("PATCH", `/api/sessions/${sid}`, { direction: dir });
    if (!r.ok) toast(explain(r, "direction"), "err");
  }, 300);

  // ---- render
  const rowEls = new Map();
  function rowFor(s) {
    let r = rowEls.get(s.id);
    if (!r) {
      const lvl = h("input", { type: "range", min: 0, max: 1, step: 0.01, "aria-label": `${s.label || s.effect} level` });
      const lv = h("span", { class: "v" });
      lvl.addEventListener("pointerdown", () => dragging.add(s.id));
      lvl.addEventListener("input", () => { dragging.add(s.id); lv.textContent = fmt(parseFloat(lvl.value)); setLevel(s.id, parseFloat(lvl.value)); });
      const byp = h("button", { class: "lib-iconbtn", title: "bypass (fade to 0, keep the slot)", "aria-pressed": "false" }, "⏻");
      byp.addEventListener("click", () => setBypass(s.id, byp.getAttribute("aria-pressed") !== "true"));
      const rm = h("button", { class: "lib-iconbtn", title: "remove (fades out)", "aria-label": "remove" }, "×");
      rm.addEventListener("click", () => remove(s.id));
      const meter = h("div", { class: "fx-meter", title: "amount now" }, h("div"));
      const r0 = { el: null, lvl, lv, byp, meter, stage: h("span", { class: "stage" }), name: h("div", { class: "name" }), err: h("div", { class: "err" }) };
      r0.el = h("div", { class: "fx-row" }, r0.stage, r0.name, h("div", { class: "btns" }, byp, rm),
        h("div", { class: "lvl" }, meter, lvl, lv), r0.err);
      rowEls.set(s.id, (r = r0));
    }
    const failed = s.state === "failed";
    r.el.className = `fx-row${failed ? " failed" : ""}${s.bypass ? " bypassed" : ""}`;
    r.stage.textContent = s.stage || "–";
    put(r.name, s.label || s.preset || s.effect || s.id,
      s.origin === "auto" ? h("span", { class: "auto", title: "added by Auto FX; touching it makes it manual" }, "auto") : null,
      s.state && s.state !== "active" ? h("span", { class: "st" }, s.state.replace("_", " ")) : null);
    r.name.title = `${s.id} · ${s.effect}${s.preset ? " / " + s.preset : ""}${s.cost_ms != null ? ` · ${fmt(s.cost_ms)} ms` : ""}`;
    if (!dragging.has(s.id) && document.activeElement !== r.lvl) { r.lvl.value = s.level ?? 1; r.lv.textContent = fmt(s.level ?? 1); }
    r.byp.setAttribute("aria-pressed", String(!!s.bypass));
    r.meter.firstChild.style.width = `${Math.round(clamp01(s.amount_now ?? s.level_now ?? 0) * 100)}%`;
    const e = (fx?.errors || []).find((x) => x.slot === s.id);
    r.err.textContent = failed || e ? `failed: ${e?.detail || "render error"}` : "";
    r.err.hidden = !(failed || e);
    return r.el;
  }

  function render() {
    const sid = ctx.sid(), st = ctx.state();
    if (!mock && sid !== lastSid) { lastSid = sid; fx = null; rowEls.clear(); avail = avail === "missing" ? "missing" : "unknown"; if (sid && autoCfg.enabled) patchAuto(); }
    const editable = canEdit();
    clearBtn.disabled = !editable || !(fx?.stack?.length);
    const autoOk = !!mock || ctx.features().has("fx_auto");
    for (const el of autoBox.querySelectorAll("input,select,button")) el.disabled = !autoOk;
    autoNote.textContent = autoOk ? "" : "Auto FX needs a server with the fx_auto feature (not this one yet).";
    for (const el of masterBox.querySelectorAll("input,select")) el.disabled = !editable;

    if (!mock && avail === "missing") {
      pill.textContent = "n/a"; pill.className = "lib-pill";
      put(msg, h("div", { class: "lib-warnbox" }, h("b", {}, "FX bus not available on this server yet. "), "It arrives with the effects backend (PUT /api/sessions/{id}/fx). Try ", h("code", {}, "/dev?mock=1"), " to play with an in-memory bus."));
      put(rows); rev.textContent = ""; return;
    }
    if (!mock && !sid) {
      pill.textContent = "no session"; pill.className = "lib-pill";
      put(msg, "No session. Start the live demo, then add effects from the Library (Effects tab).");
      put(rows); rev.textContent = ""; return;
    }
    const open = !!fx?.open;
    pill.textContent = mock ? "mock" : open ? "open" : st === "exiting" || st === "held" ? "closed" : FX_STATES.has(st) ? "waits for live" : st || "–";
    pill.className = `lib-pill ${mock || open ? "acc" : ""}`;
    rev.textContent = fx ? `rev ${fx.rev}${fx.fade_s != null ? ` · fade ${fmt(fx.fade_s, 1)} s` : ""}` : "";
    const stack = [...(fx?.stack || [])].sort((a, b) => stageIdx(a.stage) - stageIdx(b.stage));
    if (!stack.length) put(msg, "No effects. Add one from the Library (Effects tab).");
    else put(msg);
    msg.hidden = !!stack.length;
    const ids = new Set(stack.map((s) => s.id));
    for (const id of [...rowEls.keys()]) if (!ids.has(id)) rowEls.delete(id);
    put(rows, ...stack.map(rowFor));
    const m = fx?.master;
    if (m) {
      const show = (v) => (v == null ? "1" : typeof v === "object" ? `←${v.bind}` : fmt(v));
      masterNow.textContent = `master ${show(m.amount)} · pulse ${show(m.pulse)}${m.level_now != null ? ` · bus ${fmt(m.level_now)}` : ""}`;
      if (document.activeElement !== mAmt && document.activeElement !== mPul) {
        const sync = (v, inp, sel, out) => {
          if (typeof v === "object" && v) { sel.value = v.bind || ""; inp.value = v.range?.[1] ?? 1; } else { sel.value = ""; inp.value = v ?? 1; }
          out.textContent = fmt(parseFloat(inp.value));
        };
        sync(m.amount, mAmt, mAmtF, mAmtV); sync(m.pulse, mPul, mPulF, mPulV);
      }
    }
    const a = fx?.auto;
    if (a) {
      const last = a.last ? ` · last: ${a.last.op} ${a.last.preset ?? ""}` : "";
      autoStatus.textContent = `${a.enabled ? (a.armed ? "armed" : "disarmed (waits for live)") : "off"}${a.enabled && a.next_in_s != null ? ` · next change in ${Math.round(a.next_in_s)} s` : ""}${last}`;
      autoStatus.title = a.last?.why || "";
    }
  }

  // ---- polling (2 Hz, only while visible and a session can hold a bus)
  try {
    new IntersectionObserver((es) => { visible = es.some((e) => e.isIntersecting); }).observe(root);
  } catch (_) {}
  let polling = false;
  setInterval(async () => {
    if (polling || document.hidden) return;
    const sid = ctx.sid(), st = ctx.state();
    if (!mock) {
      if (!sid || !(FX_STATES.has(st) || st === "exiting")) { render(); return; }
      if (avail === "missing" && Date.now() - missingAt < 10000) { render(); return; }
      if (!visible && fx) return;
    }
    polling = true;
    try {
      const r = await getFx();
      if (r.ok && !inflight) { fx = r.data; notify(); }
      render();
    } finally { polling = false; }
  }, POLL_MS);

  // ---- events
  ctx.onEvent((ev) => {
    if (ev.session !== ctx.sid()) return;
    if (ev.type === "session.fx") {
      if (fx && ev.rev >= fx.rev) {
        const prev = new Map((fx.stack || []).map((s) => [s.id, s]));
        fx = { ...fx, rev: ev.rev, open: ev.open ?? fx.open, fade_s: ev.fade_s ?? fx.fade_s,
               stack: (ev.stack || []).map((s) => ({ ...(prev.get(s.id) || {}), ...s })) };
        render(); notify();
      }
      const parts = [];
      if (ev.added?.length) parts.push(`+${ev.added.join(",")}`);
      if (ev.removed?.length) parts.push(`−${ev.removed.join(",")}`);
      if (ev.changed?.length) parts.push(`~${ev.changed.join(",")}`);
      ctx.decide(`fx ${ev.cause}${ev.open != null ? (ev.open ? " (bus open)" : " (bus closed)") : ""} rev ${ev.rev} ${parts.join(" ")}${ev.detail ? "  " + ev.detail : ""}`, ev.cause === "auto" ? "auto" : ev.cause === "error" ? "err" : ev.cause === "bus" ? "info" : "manual");
    } else if (ev.type === "session.state" && (ev.state === "live" || ev.state === "held")) {
      render();
    }
  });

  render();
  return {
    add, removeSpec,
    has: (spec) => !!(fx?.stack || []).some((s) => sameSpec(s, spec)),
    canEdit, onChange: (fn) => listeners.add(fn),
    stack: () => fx?.stack || [],
  };
}
