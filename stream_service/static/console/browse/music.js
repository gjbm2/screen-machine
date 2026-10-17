// Music: how this item follows the music (the per-item mappings, mappings.py: GET / PATCH /api/mappings/{kind}/{plugin},
// POST /api/mappings/eval for previews).  Each setting that moves with the music reads in words (what moves it, when)
// with four bars: quiet, jazz, chorus, peak (the dashed line is the preset's own value).  Tap one to change it; Save
// writes the owner's layer (config/live/mappings.json) for every variation of the item, "Starting point" drops the
// owner's change.  A scene's preview tries an edit at once; saved changes reach screens at the item's next start.
import { h, put, http, fmt, visibleInterval } from "../kit.js";
import * as preview from "./preview.js";
import { button, chip, seg, switchEl, slider, badge } from "../ui/controls.js";
import { icon } from "../ui/icons.js";
import { toast, toastErr } from "../ui/toast.js";
import { errorWords } from "../words.js";

const SIGNALS = [
  ["energy", "energy"], ["drive", "drive"], ["punch", "punch"], ["tempo", "tempo"],
  ["motion", "the lights moving"], ["pan_slow", "where the lights point ↔"], ["tilt_slow", "where the lights point ↕"],
  ["zoom_slow", "the lights' zoom"], ["warmth", "warm light"], ["sat", "colourful light"],
  ["level_slow", "light level"], ["activity_slow", "activity"], ["gobo", "each section's own look"],
  ["beat", "the beat"], ["section", "a new section"],
  // the piano (its MIDI: piano.py)
  ["velocity", "how hard the piano is played"], ["note_rate", "how busy the playing is"], ["register", "high or low on the keyboard"],
  ["spread", "how full the chords are"], ["pedal", "the piano's pedal"], ["tension", "harmonic tension"],
  ["note_on", "each piano note"], ["chord_change", "a chord change"],
  // the room's sound and the music's clock (show_service: music.features / rhythm / clock)
  ["bass", "the bass"], ["mids", "the middle of the sound"], ["highs", "the treble"],
  ["brightness", "how bright the sound is"], ["density", "how busy the rhythm is"], ["groove", "the groove"],
  ["arousal", "how excited the music is"], ["downbeat", "each bar's first beat"],
  ["beat_phase", "through each beat"], ["bar_phase", "through each bar"], ["phrase_phase", "through each phrase"],
];
const SIG = Object.fromEntries(SIGNALS);
const MAIN = new Set(["energy", "drive", "punch", "tempo", "warmth", "gobo", "beat", "velocity", "pedal"]);
const TRIG = new Set(["beat", "pulse", "section", "downbeat", "note_on", "chord_change"]);
const WHEN = { live: "moves with", section: "changes each section with", cue: "is set at the start by" };
const WHEN_SEG = { live: "Moves", section: "Each section", cue: "At the start" };
const STATES = [["ambient", "Quiet"], ["jazz", "Jazz"], ["chorus", "Chorus"], ["peak", "Peak"]];
const FLAG_WORDS = { needs_eyes: "worth a look", gpu_heavy: "heavy on the GPU", flash: "flashes", self_coloured: "keeps its own colours",
                     dead_param: "has unused settings", mislabelled: "settings renamed", setup_reads: "some settings only at the start" };

export const MU = { key: null, it: null, variant: null, rec: null, loading: null, open: null, cand: null, evald: null, problems: null, now: {}, allSigs: false };
const kindOf = (it) => it.kind;
const path = (it) => `/api/mappings/${kindOf(it)}/${encodeURIComponent(it.plugin)}`;
const label = (p) => MU.rec?.params?.[p]?.schema?.["x-ui"]?.label || p;
const clone = (o) => JSON.parse(JSON.stringify(o));
const isV2 = (b) => b && typeof b === "object" && ("in" in b || "colour" in b || "steps" in b);

export function reset(it, variant) {
  Object.assign(MU, { key: it?.key || null, it, variant: variant || null, rec: null, loading: null, open: null, cand: null, evald: null, problems: null, now: {} });
}

async function load(force = false) {
  if (!MU.it) return null;
  if (MU.rec && !force) return MU.rec;
  if (MU.loading && !force) return MU.loading;
  const it = MU.it, key = MU.key;
  MU.loading = (async () => {
    const r = await http("GET", `${path(it)}${MU.variant ? `?preset=${encodeURIComponent(MU.variant)}` : ""}`);
    MU.loading = null;
    if (MU.key !== key) return null;
    if (!r.ok) { MU.rec = { error: errorWords(r, { target: "the renderer" }) }; return MU.rec; }
    MU.rec = r.data;
    return MU.rec;
  })();
  return MU.loading;
}

// ---------------------------------------------------------------- words
function inputsWords(b) {
  if (!b) return "";
  if (b.colour) return `blends toward the room's light colour (${Math.round((b.mix ?? 0) * 100)}%)`;
  if (b.bind) return `follows ${SIG[b.bind] || b.bind} (set by the preset)`;
  const ins = (b.in || []).map((t) => `${t.inv ? "less " : ""}${SIG[t.sig] || t.sig}`);
  const list = ins.length > 1 ? `${ins.slice(0, -1).join(", ")} and ${ins[ins.length - 1]}` : ins[0] || "";
  const kick = b.kick ? `, kicks on ${{ section: "each new section", downbeat: "each bar's first beat", note_on: "each piano note", chord_change: "each chord change" }[b.kick.sig] || "the beat"}` : "";
  return `${WHEN[b.at] || "follows"} ${list}${kick}`;
}
function num(v) {
  if (typeof v !== "number") return String(v ?? "–");
  const a = Math.abs(v);
  return a >= 100 ? v.toFixed(0) : a >= 10 ? v.toFixed(1) : a >= 1 ? v.toFixed(2) : v.toFixed(3);
}
function bars(p, pv, base) {
  const s = MU.rec?.params?.[p]?.schema || {};
  const vals = STATES.map(([k]) => pv?.[k]).filter((v) => typeof v === "number");
  if (!vals.length) return null;
  let lo = s.minimum, hi = s.maximum;
  if (typeof lo !== "number" || typeof hi !== "number" || !(hi > lo)) { lo = Math.min(...vals, base ?? Infinity); hi = Math.max(...vals, base ?? -Infinity); }
  const pad = (hi - lo) || 1;
  const frac = (v) => Math.max(0.06, Math.min(1, (v - lo) / pad));
  const el = h("span", { class: "mbars", title: STATES.map(([k, w]) => `${w} ${num(pv?.[k])}`).join(" · ") + (typeof base === "number" ? ` · preset ${num(base)}` : "") },
    STATES.map(([k]) => { const i = h("i"); i.style.height = `${Math.round(frac(pv?.[k] ?? lo) * 100)}%`; return i; }));
  if (typeof base === "number") { const b = h("b"); b.style.bottom = `${Math.round(frac(base) * 100)}%`; el.append(b); }
  return el;
}

// ---------------------------------------------------------------- the editor's candidate
function amountOf(b) {
  const m = b?.map || {};
  if (m.around) return Math.max(-m.around[0], m.around[1]);
  if (m.scale) return Math.max(1 - m.scale[0], m.scale[1] - 1) / 2;       // 0..0.5 of the base
  return null;
}
const r3 = (v) => +Number(v).toFixed(3);
function withAmount(b, x) {
  const m = b.map || {};
  if (m.around) {
    const [a, c] = m.around, top = Math.max(-a, c);
    return { ...b, map: { around: top > 0 ? [r3(a * x / top), r3(c * x / top)] : [r3(-x), r3(x)] } };
  }
  if (m.scale) {
    const [f0, f1] = m.scale, top = Math.max(1 - f0, f1 - 1), s = x * 2;
    const lo = top > 0 ? 1 - (1 - f0) * s / top : 1 - s, hi = top > 0 ? 1 + (f1 - 1) * s / top : 1 + s;
    return { ...b, map: { scale: [r3(Math.max(0.4, lo)), r3(Math.min(2, hi))] } };
  }
  return b;
}
// A preset's own one-signal binding ({bind, range, rest}) as an editable mapping that does the same.
function fromV1(b) {
  if (!b || !b.bind || !Array.isArray(b.range)) return null;
  return { at: "live", in: [{ sig: b.bind, w: 1 }], map: { range: [...b.range], ...(b.rest != null ? { rest: b.rest } : {}) } };
}
function newBinding(p) {
  const modes = MU.rec?.params?.[p]?.modes || [];
  const at = modes.includes("section") ? "section" : modes.includes("live") ? "live" : "cue";
  return { at, in: [{ sig: "energy", w: 1 }], map: { around: [-0.1, 0.1] } };
}

let evalT = null;
function evalSoon(render) {
  clearTimeout(evalT);
  evalT = setTimeout(async () => {
    const p = MU.open, b = MU.cand;
    if (!p || !b || !MU.it) return;
    const r = await http("POST", "/api/mappings/eval", { key: MU.key, preset: MU.variant || undefined, bindings: { [p]: b } });
    if (MU.open !== p) return;
    if (r.ok) { MU.evald = r.data.effective?.[p] || null; MU.problems = null; }
    else { MU.evald = null; MU.problems = (r.data?.problems || []).map((x) => x.detail || x.code).join("; ") || errorWords(r); }
    render();
  }, 250);
}

async function save(p, b, render) {
  const it = MU.it;
  const r = await http("PATCH", path(it), { base_rev: MU.rec?.rev ?? 0, bindings: { [p]: b } });
  if (r.status === 409) { toast("Someone changed this item's music meanwhile: reloaded"); await load(true); render(); return false; }
  if (!r.ok) { toastErr(`Music: ${r.data?.problems?.[0]?.detail || errorWords(r)}`); return false; }
  MU.rec = r.data;
  toast(b === "default" ? `${label(p)}: back to the starting point` : b === null ? `${label(p)} no longer follows the music` : `Saved: ${label(p)}`);
  if (preview.sessionId()) preview.show(it, MU.variant || it.spec, { immediate: true });     // the preview restarts with it
  return true;
}
async function tryOnPreview(p, b) {
  const sid = preview.sessionId();
  if (!sid || MU.it?.kind !== "scene") return;
  const r = await http("PATCH", `/api/sessions/${sid}`, { params: { [p]: b } });
  if (!r.ok) toastErr(`Try: ${r.data?.problems?.[0]?.detail || errorWords(r)}`);
}

// ---------------------------------------------------------------- the panel
export function renderPanel(host, it, variant) {
  if (MU.key !== it.key || MU.variant !== (variant || null)) reset(it, variant);
  const render = () => renderPanel(host, it, variant);
  if (!MU.rec) {
    put(host, h("div", { class: "music", tid: "music-panel" }, h("p", { class: "hint" }, "Loading how it follows the music…")));
    load().then(() => { if (MU.key === it.key) render(); });
    return;
  }
  const rec = MU.rec;
  if (rec.error) { put(host, h("p", { class: "hint" }, `Couldn't load the music settings: ${rec.error}`)); return; }
  const user = rec.user || {}, gen = rec.generated || {};
  const follow = (user.follow ?? gen.follow ?? true) !== false;
  const followSw = switchEl("Follows the music", follow, async (on) => {
    const r = await http("PATCH", path(it), { base_rev: rec.rev ?? 0, follow: on });
    if (r.ok) { MU.rec = r.data; toast(on ? `${it.title} follows the music again` : `${it.title} ignores the music now`); render(); }
    else { toastErr(errorWords(r)); render(); }
  }, { tid: "music-follow", sub: follow ? "" : "Its settings stay as the preset sets them" });
  const rv = gen.review || {};
  const flags = (rv.flags || []).filter((f) => FLAG_WORDS[f]);
  const nUser = Object.keys(user.bindings || {}).length;
  const who = h("p", { class: "hint" }, rv.by === "llm" ? "Starting point: reviewed against the plug-in's code" : "Starting point: the general rules",
    nUser ? ` · your changes: ${nUser}` : "", rec.stale ? " · the plug-in changed since: some may not apply" : "");
  const eff = rec.effective || {};
  const names = Object.keys(eff).filter((p) => p in (rec.params || {}));
  const colourP = names.find((p) => eff[p].binding?.colour);
  const rows = h("div", { class: "mrows" });
  for (const p of names.filter((x) => x !== colourP).sort((a, b) => label(a).localeCompare(label(b)))) rows.append(mrow(p, eff[p], render));
  if (MU.open && !eff[MU.open]) rows.prepend(mrow(MU.open, { binding: MU.cand, base: null, preview: null }, render));
  let colourRow = null;
  if (colourP) {
    const b = eff[colourP].binding;
    const sl = slider("Room's light colour", { tid: "music-colour", min: 0, max: 1, step: 0.05, value: b.mix ?? 0.5, fmt: (v) => `${Math.round(v * 100)}%`,
      title: "How far the colours blend toward the lighting desk's colour", onCommit: (v) => save(colourP, { ...b, mix: +v.toFixed(2) }, render).then(render) });
    colourRow = h("div", {}, sl);
  }
  const addable = Object.entries(rec.params || {}).filter(([p, x]) => !eff[p] && (x.modes || []).some((m) => m !== "cue" || it.kind === "transition")
    && ["number", "integer"].includes(x.schema?.type) && !x.schema?.enum).map(([p]) => p).sort((a, b) => label(a).localeCompare(label(b)));
  const addSel = h("select", { class: "select sm", "aria-label": "Add a setting that follows the music", tid: "music-add" },
    h("option", { value: "" }, "Add a setting…"), addable.map((p) => h("option", { value: p }, label(p))));
  addSel.addEventListener("change", () => {
    const p = addSel.value;
    if (!p) return;
    MU.open = p; MU.cand = newBinding(p); MU.evald = null; MU.problems = null;
    evalSoon(render); render();
  });
  put(host, h("div", { class: "music", tid: "music-panel" },
    followSw, who,
    flags.length ? h("div", { class: "chips" }, flags.map((f) => badge(FLAG_WORDS[f], f === "needs_eyes" ? "warn" : "mute"))) : null,
    follow ? [colourRow, names.length ? rows : h("p", { class: "hint" }, "Nothing here moves with the music yet."),
              addable.length ? h("div", { class: "row", style: { marginTop: "8px" } }, addSel) : null,
              h("p", { class: "hint" }, it.kind === "transition" ? "Set each time the transition runs." : "Saved changes apply to every variation, from its next start.")] : null));
}

function mrow(p, e, render) {
  const b = e.binding;
  const open = MU.open === p;
  const mine = !!MU.rec?.user?.bindings?.[p];
  const pv = open && MU.evald ? MU.evald.preview : e.preview;
  const now = MU.now[p];
  const head = h("div", { class: "mrow", role: "button", tabindex: "0", tid: "music-row", dataset: { param: p }, "aria-expanded": String(open) },
    h("span", { class: "mname" }, label(p), mine ? h("span", { class: "mine", title: "Your change" }, " · yours") : null),
    h("span", { class: "row" }, now != null ? h("span", { class: "now", title: "On the preview now" }, num(now)) : null, bars(p, pv, e.base)),
    h("span", { class: "mwords" }, inputsWords(open ? MU.cand : b)));
  const toggle = () => {
    if (open) { MU.open = null; MU.cand = null; }
    else { MU.open = p; MU.cand = isV2(b) && !b.colour ? clone(b) : fromV1(b) || newBinding(p); MU.evald = null; MU.problems = null; evalSoon(render); }
    render();
  };
  head.addEventListener("click", toggle);
  head.addEventListener("keydown", (ev) => { if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); toggle(); } });
  return open ? h("div", {}, head, editor(p, e, render)) : head;
}

function editor(p, e, render) {
  const c = MU.cand;
  const modes = (MU.rec?.params?.[p]?.modes || []).filter((m) => WHEN_SEG[m]);
  const changed = () => { MU.evald = null; evalSoon(render); render(); };
  const ins = c.in || [];
  const has = (s) => ins.find((t) => t.sig === s);
  const shown = SIGNALS.filter(([s]) => !(s === "gobo" && c.at === "live") && (MU.allSigs || MAIN.has(s) || has(s)));
  const pick = h("div", { class: "chips" }, shown.map(([s, w]) => chip(w, { pressed: !!has(s), tid: `music-sig-${s}`,
    onClick: () => {
      if (has(s)) { if (ins.length > 1) c.in = ins.filter((t) => t.sig !== s); }
      else if (ins.length < 4) {
        const trig = TRIG.has(s);
        c.in = trig ? [{ sig: s, w: 1 }] : [...ins.filter((t) => !TRIG.has(t.sig)), { sig: s, w: 1 }];
        c.op = "mean";
      }
      changed();
    } })), MU.allSigs ? null : chip("More…", { tid: "music-sig-more", onClick: () => { MU.allSigs = true; render(); } }));
  const weights = ins.length ? h("div", { class: "stack" }, ins.filter((t) => !TRIG.has(t.sig)).map((t) => h("div", { class: "row" },
    h("div", { class: "grow" }, ins.length > 1 ? slider(SIG[t.sig] || t.sig, { min: 0.1, max: 1, step: 0.05, value: Math.min(1, t.w ?? 1), fmt: (v) => `${Math.round(v * 100)}%`,
      title: "How much this one counts in the blend", onCommit: (v) => { t.w = +v.toFixed(2); changed(); } }) : h("span", {}, SIG[t.sig] || t.sig)),
    switchEl("Opposite", !!t.inv, (on) => { if (on) t.inv = true; else delete t.inv; changed(); }, { word: false, tid: `music-inv-${t.sig}` })))) : null;
  const when = modes.length > 1 ? seg(modes.map((m) => [m, WHEN_SEG[m]]), c.at, (m) => {
    c.at = m;
    if (m !== "live") delete c.kick;
    if (m === "live") { c.in = (c.in || []).filter((t) => t.sig !== "gobo"); if (!c.in.length) c.in = [{ sig: "energy", w: 1 }]; }
    if (m === "section" && c.ease_s == null) c.ease_s = 4;
    changed();
  }, { role: "group", aria: "When it changes", tid: "music-when" }) : h("span", { class: "hint" }, WHEN_SEG[c.at] || c.at);
  const amt = amountOf(c);
  const farMax = c.map?.scale ? 0.5 : 0.35;
  const far = amt != null ? slider("How far", { min: 0, max: farMax, step: 0.01, value: Math.min(amt, farMax), tid: "music-far", fmt: (v) => `${Math.round(v * 100)}%`,
    title: "How far the music can move it, as a share of its whole range", onCommit: (v) => { MU.cand = withAmount(MU.cand, v); changed(); } }) : null;
  const kickOK = c.at === "live" && !TRIG.has((c.in || [])[0]?.sig);
  const kick = kickOK ? switchEl("Kick on the beat", !!c.kick, (on) => { if (on) c.kick = { sig: "beat", amt: 0.08, by: "punch" }; else delete c.kick; changed(); },
    { tid: "music-kick", sub: "A push on each beat, harder when the hits are strong" }) : null;
  const prv = MU.evald?.preview || e.preview;
  const nums = prv ? h("p", { class: "meta" }, STATES.map(([k, w]) => `${w} ${num(prv[k])}`).join(" · "),
    typeof e.base === "number" ? ` · preset ${num(e.base)}` : "") : null;
  const isMine = !!MU.rec?.user?.bindings?.[p];
  const acts = h("div", { class: "row" },
    button("Save", { kind: "primary", tid: "music-save", disabled: !!MU.problems, onClick: async () => { if (await save(p, MU.cand, render)) { MU.open = null; MU.cand = null; } render(); } }),
    MU.it?.kind === "scene" ? button("Try on the preview", { kind: "outline", tid: "music-try", disabled: !!MU.problems, onClick: () => tryOnPreview(p, MU.cand) }) : null,
    e.binding ? button("Stop following", { kind: "quiet", tid: "music-remove", onClick: async () => { if (await save(p, null, render)) { MU.open = null; MU.cand = null; } render(); } }) : null,
    isMine ? button("Starting point", { kind: "quiet", tid: "music-default", title: "Forget your change to this setting", onClick: async () => { if (await save(p, "default", render)) { MU.open = null; MU.cand = null; } render(); } }) : null,
    button("Cancel", { kind: "ghost", onClick: () => { MU.open = null; MU.cand = null; render(); } }));
  return h("div", { class: "medit stack", tid: "music-editor" },
    h("div", {}, h("span", { class: "lab" }, "Follows"), pick), weights,
    h("div", { class: "row" }, h("span", { class: "grow" }, "When"), when), far, kick, nums,
    MU.problems ? h("p", { class: "bad-ink", role: "alert" }, MU.problems) : null, acts);
}

// ---------------------------------------------------------------- "now" values from the preview (1.5 s while open)
let stopNow = null;
export function watchNow(isOpen, redraw) {
  stopNow?.();
  stopNow = visibleInterval(async () => {
    const sid = preview.sessionId();
    if (!sid || !MU.rec || MU.open) return;
    const r = await http("GET", `/api/sessions/${sid}/inputs`);
    if (!r.ok) return;
    const now = {};
    for (const b of r.data.bindings || []) if (typeof b.value === "number") now[b.param] = b.value;
    for (const b of r.data.fx_bindings || []) if (typeof b.value === "number" && MU.it?.kind === "effect") now[b.param] = b.value;
    MU.now = now;
    redraw();
  }, 1500, isOpen);
  return stopNow;
}
