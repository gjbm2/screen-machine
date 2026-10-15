// The Player: the reference MSE player (static/live-player.js) on any media URL, with its stats line (4 Hz while
// open), Attach / Detach, and "Show the art cuts" (the anchor / target overlay cut at the first frame / hold_pts),
// offered only for a session with an anchor or target.  ?attach=<ws>&target_ms= plays at once and sets
// window.__player (the headless playback test relies on it; previews never set it).
import { h, put, visibleInterval } from "../kit.js";
import { LivePlayer } from "../../live-player.js";
import { onEvent } from "../data/renderer.js";
import { button, switchEl } from "../ui/controls.js";

export function mountPlayer(host, ctx) {
  const art = h("div", { id: "art", class: "hidden" });
  const stage = h("div", { id: "stage", class: "player-stage" }, art);
  const stats = h("div", { class: "statsline", tid: "player-stats" }, "–");
  const url = h("input", { class: "input", tid: "attach-url", placeholder: "ws://…/media/s-…?k=…", "aria-label": "Media URL", autocomplete: "off" });
  const attachBtn = button("Attach", { kind: "outline", tid: "attach-btn", onClick: () => { if (url.value.trim()) attach(url.value.trim(), 300, null); } });
  const detachBtn = button("Detach", { kind: "quiet", tid: "detach-btn", onClick: () => detach() });
  const cuts = switchEl("Show the art cuts", true, () => {}, { tid: "artcuts-switch", sub: "The anchor until the first frame, the target from the hold" });
  cuts.hidden = true;
  const msg = h("p", { class: "hint" });
  put(host, h("div", { class: "stack" }, stage, stats, msg, cuts, h("div", { class: "row" }, h("div", { class: "grow" }, url), attachBtn, detachBtn)));
  let player = null, cur = null;    // cur: {url, sid, artCss, targetCss}

  function showArt(css) {
    if (!css) return;
    if (css.startsWith("url(")) { art.style.backgroundImage = css; art.style.backgroundColor = "#000"; }
    else { art.style.backgroundImage = "none"; art.style.backgroundColor = css; }
    art.classList.remove("hidden");
  }
  function attach(u, targetMs = 300, artCss = null, meta = {}) {
    detach();
    cur = { url: u, sid: meta.sid || (String(u).match(/\/media\/([^?/]+)/) || [])[1] || null, artCss, targetCss: meta.targetCss || null };
    cuts.hidden = !(artCss || meta.targetCss);
    if (artCss && cuts.sw.get()) showArt(artCss); else art.classList.add("hidden");
    const codecMsg = (name, d) => { if (name === "type-unsupported") msg.textContent = "This browser can't play this stream (its codec); the TV shows it."; };
    player = new LivePlayer({ url: u, targetBufferMs: targetMs, mount: stage, onEvent: (name, d) => {
      codecMsg(name, d);
      if (["config", "first-frame", "eos", "stall", "ws-close", "teardown"].includes(name) || d?.fatal) window.smDev?.log?.(`player ${name} ${JSON.stringify(d)}`, d?.fatal ? "err" : "");
    } });
    player.start();
    player.onFirstFrame((mt) => { art.classList.add("hidden"); window.smDev?.log?.(`entry cut at mediaTime ${Number(mt).toFixed(3)}`); });
    window.__player = player;
    msg.textContent = "";
    if (url.value !== u) url.value = u;
    return player;
  }
  function detach() {
    if (player) { player.stop(); player = null; window.__player = null; }
    stats.textContent = "–";
  }
  onEvent("session.held", (ev) => {
    if (!player || !cur || ev.session !== cur.sid || !cur.targetCss || !cuts.sw.get()) return;
    player.watchHold(ev.hold_pts, (mt) => { showArt(cur.targetCss); window.smDev?.log?.(`exit cut at mediaTime ${Number(mt).toFixed(3)} (hold_pts ${ev.hold_pts})`); });
  });
  visibleInterval(() => {
    if (!player) { stats.textContent = "–"; return; }
    const s = player.stats();
    stats.textContent = `${s.state} · ${s.codec ?? "–"} · ${s.fps ?? "–"} fps\npresented ${s.presented} · dropped ${s.dropped ?? "–"} · buffered ${s.buffered_ms ?? "–"} ms\nedge jumps ${s.edge_jumps} · gap skips ${s.gap_skips} · stalls ${s.stalls} · mediaTime ${s.media_time?.toFixed?.(3) ?? "–"} · rx ${(s.bytes_rx / 1e6).toFixed(1)} MB`;
  }, 250, () => true);
  return { attach, detach, current: () => cur, setTarget: (css) => { if (cur) cur.targetCss = css; cuts.hidden = !(cur?.artCss || css); }, player: () => player };
}
