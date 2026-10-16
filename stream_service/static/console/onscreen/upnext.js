// Up next (the renderer's upnext.py): what the art director will play next, the scenes (each with the transition it
// comes in by) and the effects, with rough times.  Edit it at any time: play one now, move it up, keep it (the
// director then never swaps it), pick another, change the way in, remove it, or add a scene or an effect.  The
// renderer's own API through the pass-through (room has no route for it); in a room it is the leader's plan, so
// either screen shows and edits the same queue.  Re-read on session.upnext events and every 5 s; the times count
// down here between reads.
import { h, put, http } from "../kit.js";
import * as room from "../data/room.js";
import * as catalog from "../data/catalog.js";
import { onEvent } from "../data/renderer.js";
import { lib } from "../data/library.js";
import { labelForSpec } from "../data/query.js";
import { button, select } from "../ui/controls.js";
import { toast, toastErr } from "../ui/toast.js";
import { errorWords, friendly } from "../words.js";

const sessionOf = (id) => room.screenOf(id)?.observed?.session || null;
const itemOf = (kind, name) => lib.pidx.get(`${kind}:${name}`)?.item || null;
const titleOf = (kind, name) => labelForSpec(name, lib.items, lib.pidx, kind) || friendly(name);

function inWords(eta, since) {
  if (eta == null) return null;
  const el = (Date.now() - since) / 1000;
  if (Array.isArray(eta)) {
    const lo = Math.max(0, Math.round(eta[0] - el)), hi = Math.max(0, Math.round(eta[1] - el));
    if (hi <= 0) return "any moment";
    return lo === hi ? `in ${lo} s` : lo <= 0 ? `within ${hi} s` : `in ${lo}–${hi} s`;
  }
  const s = Math.max(0, Math.round(eta - el));
  return s <= 0 ? "any moment" : `in about ${s} s`;
}

export function mountUpNext(host, target, { openPicker }) {
  let data = null, at = 0, timer = null, loading = false, sig = "";
  let times = [];               // [{el, e, mine}]: the countdowns, updated in place each second
  const name = room.nameOf(target).replace(/ screen$/i, "");
  const sid = () => sessionOf(target);

  const load = async () => {
    const s = sid();
    if (!s || loading) return;
    loading = true;
    const r = await http("GET", `/api/sessions/${s}/upnext`);
    loading = false;
    if (r.ok) { data = r.data; at = Date.now(); }
    draw();
  };
  const send = async (body, what) => {
    const s = sid();
    if (!s) return null;
    const r = await http("POST", `/api/sessions/${s}/upnext`, body);
    if (!r.ok) { toastErr(errorWords(r, { screen: name, what, target: "the renderer" })); load(); return null; }
    data = r.data.upnext; at = Date.now(); draw();
    return r.data;
  };

  const transitions = () => {
    const T = catalog.get()?.presets?.transitions || {};
    return Object.keys(T).sort().map((n) => [n, titleOf("transition", n)]);
  };

  const row = (kind, e, i, n) => {
    const it = itemOf(kind, e.scene || e.effect);
    const title = titleOf(kind, e.scene || e.effect);
    const thumb = h("div", { class: "upn-thumb" });
    if (it?.thumb) thumb.append(h("img", { src: it.thumb, alt: "", loading: "lazy" }));
    const bits = [];
    if (kind === "scene") {
      const via = select([["", "Director's choice"], ...transitions()], e.transition_origin === "you" ? e.transition || "" : "",
        (v) => send({ op: "set", id: e.id, transition: v || null }, "changing the transition"),
        { aria: `How ${title} comes in`, cls: "sm" });
      if (e.transition && e.transition_origin !== "you") {
        via.querySelector('option[value=""]').textContent = `via ${titleOf("transition", e.transition)}`;
      }
      bits.push(via);
    }
    const metaEl = h("span", { class: "meta" });
    times.push({ el: metaEl, e });
    const acts = h("div", { class: "upn-acts" },
      button(null, { icon: "play", aria: `Play ${title} now`, title: "Play now", tid: `upn-play-${e.id}`,
        onClick: async () => { if (await send({ op: "play", id: e.id }, `playing ${title}`)) toast(`${title} → ${name}`); } }),
      i > 0 ? button(null, { icon: "up", aria: `Move ${title} up`, title: "Move up", tid: `upn-up-${e.id}`,
        onClick: () => send({ op: "move", id: e.id, to: i - 1 }, "moving it") }) : null,
      e.origin === "you" ? null : button(null, { icon: "check", aria: `Keep ${title}`, title: "Keep (the director won't swap it)",
        tid: `upn-keep-${e.id}`, onClick: () => send({ op: "keep", id: e.id }, "keeping it") }),
      button(null, { icon: "shuffle", aria: `Pick another instead of ${title}`, title: "Pick another", tid: `upn-reroll-${e.id}`,
        onClick: () => send({ op: "reroll", id: e.id }, "picking another") }),
      button(null, { icon: "x", aria: `Remove ${title}`, title: "Remove", tid: `upn-remove-${e.id}`,
        onClick: () => send({ op: "remove", id: e.id }, "removing it") }));
    return h("li", { class: `upn-row${e.origin === "you" ? " mine" : ""}`, tid: `upn-${kind}-${i}` },
      h("span", { class: "upn-n" }, String(i + 1)), thumb,
      h("div", { class: "upn-main" }, h("div", { class: "upn-title" }, title),
        h("div", { class: "upn-sub" }, ...bits, metaEl)),
      acts);
  };

  const updateTimes = () => {
    for (const { el, e } of times) el.textContent = [inWords(e.eta_s, at), e.origin === "you" ? "yours" : null].filter(Boolean).join(" · ");
  };
  const draw = () => {
    const sc = data?.scenes || [], ef = data?.effects || [];
    const nsig = JSON.stringify([data?.rev, sc.map((e) => [e.id, e.scene, e.transition, e.origin]), ef.map((e) => [e.id, e.effect, e.origin]), !!data]);
    if (nsig === sig && host.firstChild) { updateTimes(); return; }      // same plan: only the times move
    sig = nsig;
    times = [];
    const followNote = data?.planned_by && data.planned_by !== room.screenOf(target)?.id && data.planned_by !== target
      ? h("p", { class: "hint" }, "Shared with the room's other screen.") : null;
    const addScene = button("Add a scene", { kind: "outline", icon: "plus", cls: "btn-sm", tid: "upn-add-scene",
      onClick: () => openPicker("scene", { title: `Play next on ${name}`, onPick: async (it2, preset) => {
        if (await send({ op: "add", kind: "scene", scene: preset || it2.spec, at: 0 }, "adding it")) toast(`${it2.title} plays next`);
      } }) });
    const addFx = button("Add an effect", { kind: "outline", icon: "plus", cls: "btn-sm", tid: "upn-add-effect",
      onClick: () => openPicker("effect", { title: `Next effect on ${name}`, onPick: async (it2, preset) => {
        const p = preset || it2.preset || it2.presets?.[0]?.name;
        if (await send({ op: "add", kind: "effect", effect: p, at: 0 }, "adding it")) toast(`${it2.title} comes in next`);
      } }) });
    const shuffle = button("Shuffle", { kind: "quiet", icon: "shuffle", cls: "btn-sm", tid: "upn-shuffle",
      title: "The director picks its own entries again (yours stay)",
      onClick: async () => { await send({ op: "reroll", kind: "scene" }, "shuffling"); await send({ op: "reroll", kind: "effect" }, "shuffling"); } });
    put(host, h("div", { class: "os-section upn", tid: "upnext" },
      h("div", { class: "row" }, h("span", { class: "lab grow" }, "Up next"), shuffle),
      followNote,
      sc.length ? h("ol", { class: "upn-list" }, sc.map((e, i) => row("scene", e, i, sc.length)))
        : h("p", { class: "hint" }, data ? "Nothing queued: the director isn't picking scenes. Add one, or turn Auto on." : "Loading…"),
      h("div", { class: "row" }, addScene),
      h("span", { class: "lab", style: { marginTop: "8px" } }, "Effects up next"),
      ef.length ? h("ol", { class: "upn-list" }, ef.map((e, i) => row("effect", e, i, ef.length)))
        : h("p", { class: "hint" }, "None queued: auto effects are off, or nothing else fits."),
      h("div", { class: "row" }, addFx)));
    updateTimes();
  };

  const off = onEvent("session.upnext", (e) => {
    const s = sid();
    if (!s || e.session !== s) return;
    data = e; at = Date.now(); draw();
  });
  const tick = () => {
    if (!host.isConnected) return;
    if (document.visibilityState === "visible") {
      if (Date.now() - at > 5000) load(); else updateTimes();
    }
  };
  timer = setInterval(tick, 1000);
  load();
  return { destroy() { clearInterval(timer); off?.(); }, refresh: load };
}
