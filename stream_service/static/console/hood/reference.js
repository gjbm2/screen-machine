// The Reference session: a raw stream_service session for testing the renderer itself (POST /api/sessions), with
// its tools: Go, Renew, auto-renew, Delete, Live settings (PATCH params), Change scene (raw PATCH), Exit to a colour or
// PNG.  The ids are kept for the headless playback test (#screen #gpu #profile #anchorKind #anchorColor #anchorFile
// #scene #sceneParams #tIn #tOut #ttl #create #stage #art).  Room-owned sessions are read-only here: room runs them.
import { h, put, http, blobToB64 } from "../kit.js";
import { S } from "../store.js";
import * as catalog from "../data/catalog.js";
import * as room from "../data/room.js";
import { onEvent } from "../data/renderer.js";
import { button, switchEl, select, statusRow } from "../ui/controls.js";
import { paramForm } from "../ui/paramform.js";
import { toast, toastErr } from "../ui/toast.js";
import { errorWords, sessionWords } from "../words.js";

const HEVC_TYPE = 'video/mp4; codecs="hvc1.1.6.L153.B0"';
const HEVC_OK = !!(window.MediaSource && MediaSource.isTypeSupported && MediaSource.isTypeSupported(HEVC_TYPE));
const DEFAULT_PROFILE = HEVC_OK ? "hevc-2160p60" : "h264-720p30";

function specFrom(value, overrides) {
  const [kind, name] = String(value).split(":");
  const params = overrides && Object.keys(overrides).length ? overrides : undefined;
  if (kind === "preset") return params ? { preset: name, params } : name;
  return { [kind]: name, ...(params ? { params } : {}) };
}
async function stillSpec(kind, colorEl, fileEl) {
  if (kind === "png") {
    const f = fileEl.files[0];
    if (!f) throw new Error("choose a PNG first");
    return { spec: { png_b64: await blobToB64(f) }, css: `url(${URL.createObjectURL(f)})` };
  }
  const hex = colorEl.value;
  return { spec: { rgb: [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16)) }, css: hex };
}

export function mountReference(host, ctx) {
  const opt = (v, t) => h("option", { value: v }, t);
  const screen = h("input", { id: "screen", class: "input", value: "console-ref", "aria-label": "Screen id" });
  const gpu = h("select", { id: "gpu", class: "select", "aria-label": "GPU" }, opt("0", "GPU 0"), opt("1", "GPU 1"));
  gpu.value = "1";
  const profile = h("select", { id: "profile", class: "select", "aria-label": "Profile" });
  const anchorKind = h("select", { id: "anchorKind", class: "select", "aria-label": "Anchor" }, opt("rgb", "A colour"), opt("png", "A PNG"));
  const anchorColor = h("input", { id: "anchorColor", type: "color", class: "color", value: "#203040", "aria-label": "Anchor colour" });
  const anchorFile = h("input", { id: "anchorFile", type: "file", accept: "image/png", "aria-label": "Anchor PNG" });
  const scene = h("select", { id: "scene", class: "select", "aria-label": "Scene" });
  const sceneParams = h("div", { id: "sceneParams", class: "pform" });
  const tIn = h("select", { id: "tIn", class: "select", "aria-label": "Transition in" });
  const tOut = h("select", { id: "tOut", class: "select", "aria-label": "Transition out" });
  const ttl = h("input", { id: "ttl", type: "number", class: "input", value: "120", min: "5", max: "3600", "aria-label": "Time to live (s)" });
  const create = h("button", { id: "create", type: "button", class: "btn btn-primary" }, h("span", { class: "label" }, "Create and attach"));
  const warn = h("div", {});
  const f = (l, c) => h("label", { class: "field" }, h("span", { class: "lab" }, l), c);
  const form = h("div", { class: "stack" },
    h("p", { class: "hint" }, "A raw renderer session for testing the renderer itself. Room screens are driven from On screen."),
    h("div", { class: "fields" }, f("Screen id", screen), f("GPU", gpu), f("Profile", profile)), warn,
    h("div", { class: "row" }, f("Anchor", anchorKind), anchorColor, anchorFile),
    f("Scene", scene), sceneParams,
    h("div", { class: "fields" }, f("In", tIn), f("Out", tOut), f("Time to live (s)", ttl)), h("div", {}, create));

  // tools for the session we look at
  const head = h("div", { class: "row" });
  const guard = h("div", { tid: "ref-guard" });
  const go = button("Go", { kind: "outline", tid: "ref-go" });
  const renew = button("Renew", { kind: "outline", tid: "ref-renew" });
  const autoRenew = switchEl("Auto-renew", true, () => {}, { tid: "ref-autorenew", word: false });
  const del = button("Delete", { kind: "danger", tid: "ref-delete" });
  const params = h("div", { class: "pform", tid: "ref-params" });
  const scene2 = h("select", { class: "select", tid: "ref-change-scene", "aria-label": "Scene" });
  const tScene = h("select", { class: "select", tid: "ref-change-transition", "aria-label": "Transition" });
  const changeBtn = button("Change scene (raw PATCH; the director may move on at once)", { kind: "outline", tid: "ref-change-btn" });
  const targetKind = h("select", { class: "select", "aria-label": "Exit to" }, opt("rgb", "A colour"), opt("png", "A PNG"));
  const targetColor = h("input", { type: "color", class: "color", value: "#402020", tid: "ref-exit-color", "aria-label": "Exit colour" });
  const targetFile = h("input", { type: "file", accept: "image/png", "aria-label": "Exit PNG" });
  const tExit = h("select", { class: "select", "aria-label": "Exit transition" });
  const exitBtn = button("Exit", { kind: "outline", tid: "ref-exit-btn" });
  const tools = h("div", { class: "stack" }, head, guard, h("div", { class: "ref-tools" }, go, renew, autoRenew, del),
    h("h3", { class: "group-h" }, "Live settings"), params,
    h("h3", { class: "group-h" }, "Change scene"), h("div", { class: "fields" }, scene2, tScene), h("div", {}, changeBtn),
    h("h3", { class: "group-h" }, "Exit to"), h("div", { class: "row" }, targetKind, targetColor, targetFile), tExit, h("div", {}, exitBtn));
  put(host, h("div", { class: "stack" }, form, h("hr", { style: { border: 0, borderTop: "1px solid var(--line)", width: "100%" } }), tools));

  let overrides = {}, liveSess = null;
  const sceneForm = () => {
    const c = catalog.get();
    if (!c) return;
    const [kind, name] = scene.value.split(":");
    const src = kind === "preset" ? c.presets.scenes[name]?.source : name;
    const props = catalog.propsFor("scene", src, c);
    const values = kind === "preset" ? { ...(c.presets.scenes[name]?.params || {}) } : {};
    overrides = {};
    paramForm(sceneParams, props, values, (k, v) => { overrides[k] = v; });
  };
  scene.addEventListener("change", sceneForm);
  catalog.ready().then((c) => {
    if (!c) return;
    put(profile, Object.entries(c.profiles || {}).map(([n, p]) => opt(n, `${n} (${p.w}×${p.h} @ ${p.fps})`)));
    profile.value = c.profiles?.[DEFAULT_PROFILE] ? DEFAULT_PROFILE : Object.keys(c.profiles || {})[0];
    const sceneOpts = () => [...Object.entries(c.presets.scenes).map(([n, p]) => opt(`preset:${n}`, `${p.label} (${n})`)),
      ...(c.sources || []).filter((s) => !s.internal).map((s) => opt(`source:${s.name}`, `${s.label} (plug-in)`))];
    put(scene, sceneOpts()); put(scene2, sceneOpts());
    const trOpts = () => [...Object.entries(c.presets.transitions).map(([n, p]) => opt(`preset:${n}`, p.label)), ...(c.transitions || []).map((t) => opt(`transition:${t.name}`, `${t.label} (plug-in)`))];
    for (const s of [tIn, tOut, tScene]) { put(s, trOpts()); s.value = "preset:crossfade-3s"; }
    put(tExit, opt("", "The session's own out"), trOpts()); tExit.value = "";
    sceneForm();
  });
  screen.addEventListener("input", () => {
    const isRoom = !!S.get().roomStatus?.screens?.[screen.value.trim()];
    put(warn, isRoom ? statusRow("warn", "That's a room screen; use On screen.") : null);
    create.querySelector(".label").textContent = isRoom ? "Create anyway" : "Create and attach";
  });
  create.addEventListener("click", async () => {
    try {
      const anchor = await stillSpec(anchorKind.value, anchorColor, anchorFile);
      const body = { screen: screen.value.trim() || "console-ref", gpu: parseInt(gpu.value, 10), profile: profile.value, anchor: anchor.spec,
                     scene: specFrom(scene.value, overrides), transitions: { in: specFrom(tIn.value), out: specFrom(tOut.value) },
                     ttl_s: parseFloat(ttl.value), idempotency_key: `console-${Date.now()}` };
      create.disabled = true;
      const r = await http("POST", "/api/sessions", body);
      create.disabled = false;
      if (!r.ok) { toastErr(errorWords(r)); return; }
      ctx.H.refSid = r.data.session;
      ctx.H.refArt = anchor.css;
      window.smDev?.log?.(`created ${r.data.session} (${r.data.attach?.codec} ${r.data.attach?.w}×${r.data.attach?.h} @ ${r.data.attach?.fps})`);
      ctx.setLookingAt(r.data.session);
      ctx.player.attach(r.data.attach.url, r.data.attach.target_buffer_ms ?? 300, anchor.css, { sid: r.data.session });
      ctx.sec.player.open = true;
      toast(`Reference session ${r.data.session} created`);
    } catch (e) { create.disabled = false; toastErr(String(e.message || e)); }
  });

  // ---- the tools follow "Looking at"
  const sid = () => ctx.lookingAt();
  const isRoomSess = () => !!ctx.roomScreenOf(sid());
  const call = async (method, path, body, what) => {
    const r = await http(method, path, body);
    if (!r.ok) toastErr(errorWords(r, { what }));
    return r;
  };
  go.addEventListener("click", () => call("POST", `/api/sessions/${sid()}/go`, {}, "Go"));
  renew.addEventListener("click", () => call("POST", `/api/sessions/${sid()}/renew`, {}, "Renew"));
  del.addEventListener("click", async () => {
    const s = sid();
    await call("DELETE", `/api/sessions/${s}`, undefined, "Delete");
    if (ctx.player.current()?.sid === s) setTimeout(() => ctx.player.detach(), 500);
    if (ctx.H.refSid === s) ctx.H.refSid = null;
    ctx.setLookingAt(null, false);
  });
  changeBtn.addEventListener("click", async () => {
    const r = await call("PATCH", `/api/sessions/${sid()}`, { scene: specFrom(scene2.value), transition: specFrom(tScene.value) }, "Change scene");
    if (r.ok) draw(true);
  });
  exitBtn.addEventListener("click", async () => {
    try {
      const t = await stillSpec(targetKind.value, targetColor, targetFile);
      ctx.player.setTarget(t.css);
      const body = { target: t.spec };
      if (tExit.value) body.transition = specFrom(tExit.value);
      await call("POST", `/api/sessions/${sid()}/exit`, body, "Exit");
    } catch (e) { toastErr(String(e.message || e)); }
  });

  let drawnFor = null;
  async function draw(force = false) {
    const s = sid();
    const roomOwned = isRoomSess();
    const mine = s && s === ctx.H.refSid;
    for (const b of [go, renew, del, changeBtn, exitBtn]) b.disabled = !s || roomOwned;
    autoRenew.sw.disable(!s || roomOwned);
    put(guard, roomOwned ? statusRow("info", `Room runs this one: use On screen → Back to art (${room.nameOf(ctx.roomScreenOf(s))}).`) : !s ? h("p", { class: "hint" }, "Nothing to act on: create a reference session above, or pick one under Looking at.") : !mine ? h("p", { class: "hint" }, "Not yours: careful, another page may be using it.") : null);
    if (!s) { put(head); put(params); drawnFor = null; return; }
    if (drawnFor === s && !force) return;
    drawnFor = s;
    const r = await http("GET", `/api/sessions/${s}`);
    if (!r.ok) { put(head, h("span", { class: "muted" }, `${s}: ${errorWords(r)}`)); return; }
    liveSess = r.data;
    put(head, h("strong", {}, s), h("span", { class: "badge tone-mute", tid: "ref-state" }, sessionWords(r.data.state)), h("span", { class: "muted" }, r.data.current_scene || r.data.scene || ""),
      h("span", { class: "meta" }, `${r.data.screen} · ${r.data.profile} · GPU ${r.data.gpu}`));
    const c = await catalog.ready();
    const cur = r.data.current_scene || r.data.scene;
    const src = c?.presets?.scenes?.[cur]?.source || cur;
    const props = catalog.propsFor("scene", src, c);
    let pend = {}, t = null;
    paramForm(params, props, r.data.scene_params || {}, (k, v) => {
      if (roomOwned) return;
      pend[k] = v; clearTimeout(t);
      t = setTimeout(() => { const p = pend; pend = {}; http("PATCH", `/api/sessions/${s}`, { params: p }).then((x) => { if (!x.ok) toastErr(errorWords(x)); }); }, 120);
    }, { bound: new Set(Object.keys(r.data.scene_bindings || {})) });
    if (roomOwned) params.querySelectorAll("input,select").forEach((e) => { e.disabled = true; });
  }
  ctx.onHood(() => draw());
  onEvent("session.transition", (ev) => { if (ev.session === sid() && ev.phase === "started" && ev.kind === "scene") draw(true); });
  onEvent("session.state", (ev) => { if (ev.session === sid()) { const b = host.querySelector("[data-testid=ref-state]"); if (b) b.textContent = sessionWords(ev.state); } });
  // auto-renew: reference sessions only (never room's)
  setInterval(() => {
    const s = ctx.H.refSid;
    if (!s || !autoRenew.sw.get() || isRoomSess()) return;
    http("POST", `/api/sessions/${s}/renew`, {}).then((r) => { if (r.status === 404 || r.status === 409) { /* ended */ } });
  }, 10000);
  draw();
}
