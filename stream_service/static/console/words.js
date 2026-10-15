// Plain words for people (design §6.7): one table, DOM-free, so the tests can run it under node.
// Screen state is ported from the Room page's words.ts summariseScreen, so both pages say the same thing.

export const BUSY_PHASES = new Set(["freezing", "snapshot", "starting", "attaching", "entering", "exiting", "leaving"]);

/** "north-screen" -> "North screen", "devtest" -> "Devtest". */
export function friendly(id) {
  const s = String(id ?? "").replace(/[-_.]+/g, " ").replace(/\s+/g, " ").trim();
  return s ? s[0].toUpperCase() + s.slice(1) : String(id ?? "");
}

export const screenName = (status, id) => status?.screens?.[id]?.name || friendly(id);

/** The live scene of a desired value: a preset name, a {preset|source} spec, or null. */
export function desiredScene(desired) {
  const live = desired && typeof desired === "object" ? desired.live : null;
  return live && typeof live === "object" ? live.scene ?? null : null;
}

/**
 * summariseScreen(screen, label) -> {tone, text, hint, busy, problem, live}
 * label(spec) names a scene spec (the index's label); falls back to friendly names.
 */
export function summariseScreen(screen, label = (s) => specName(s)) {
  const obs = screen?.observed || {};
  const phase = obs.phase ?? null;
  const desired = screen?.desired;
  const desiredLive = desired && typeof desired === "object" && "live" in desired ? desired.live : null;
  const left = Number.isFinite(screen?.desired_expires_in_s) ? screen.desired_expires_in_s : null;
  if (phase === "failed") return { tone: "bad", text: "Problem — tap for details", busy: false, problem: true, live: false, detail: obs.detail || screen?.last_error || "" };
  if (phase && BUSY_PHASES.has(phase)) {
    const text = desiredLive ? "Switching to live…" : desired === "art" ? "Going back to art…" : "Changing…";
    return { tone: "warn", text, busy: true, problem: false, live: false, ...(desiredLive ? retryWords(screen) : {}) };
  }
  if ((phase === "live" || obs.layer === "live") && desired === "art") {
    return { tone: "warn", text: "Going back to art…", busy: true, problem: false, live: false };
  }
  if (phase === "live" || obs.layer === "live") {
    const sc = desiredLive?.scene;
    const name = sc ? label(sc) : null;
    return { tone: "live", text: (name ? `Live · ${name}` : "Live") + (left != null ? ` · ${mmssW(left)} left` : ""), busy: false, problem: false, live: true, scene: name };
  }
  if (desiredLive) {
    const hint = obs.page_connected === false ? "Can't go live yet: the TV page isn't connected" : undefined;
    return { tone: "warn", text: "Waiting to go live…", hint, busy: true, problem: false, live: false, ...retryWords(screen) };
  }
  return { tone: "mute", text: "Showing art", hint: screen?.last_error ? "Last try had a problem" : undefined, busy: false, problem: false, live: false };
}
/** Room's reason a go-live attempt fell back, in words: "fallback: page could not join: play-failed" -> "the TV page
 *  couldn't play the live stream".  Unknown reasons pass through without room's "fallback:" prefix. */
export function roomErrorWords(err) {
  const s = String(err || "").replace(/^\s*fallback:\s*/i, "").trim();
  if (!s) return "";
  if (/play[-_ ]?failed/i.test(s)) return "the TV page couldn't play the live stream";
  if (/could not join|join.*(timeout|failed)/i.test(s)) return "the TV page couldn't join the live stream";
  if (/ack.*(timeout|frozen)|frozen.*timeout|timeout/i.test(s)) return "the TV page didn't answer in time";
  if (/not connected|no page|page.*(gone|closed|disconnected)/i.test(s)) return "the TV page isn't connected";
  if (/stream.*(unreachable|refused|down)|renderer/i.test(s)) return "the renderer didn't answer the room";
  return s;
}
/** While a screen is trying to go live: room's last reason and its retry ({why, retryIn, tries}), once a try failed. */
function retryWords(screen) {
  const obs = screen?.observed || {};
  const tries = Number(obs.fallbacks_in_window) || 0;
  const why = tries > 0 || /retry|fallback/i.test(String(obs.detail || "")) ? roomErrorWords(screen?.last_error) : "";
  return why ? { why, tries, retryIn: Number.isFinite(obs.retry_in_s) ? obs.retry_in_s : null } : {};
}
/** What to try when going live is stuck. */
export function stuckAdvice(screen, name = "the screen") {
  if (screen?.observed?.page_connected === false) return `Check the TV page is open on ${name} (reload it), then try again, or go back to art.`;
  return `Reload the TV page on ${name}, then try again, or go back to art.`;
}
const mmssW = (s) => { s = Math.max(0, Math.round(s)); return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`; };

/** A spec's bare name: "fluid-ink-neon", {preset}, {source} -> name. */
export function specName(spec) {
  if (spec == null) return "";
  if (typeof spec === "string") return spec;
  return spec.preset || spec.source || spec.effect || spec.transition || "";
}

// ---------------------------------------------------------------- errors
export function errorWords(res, { what = "", screen = "", target = "the renderer" } = {}) {
  if (!res) return `${what || "That"} didn't work`;
  const code = res.error || "", detail = res.detail || "";
  const S = screen || "The screen";
  if (res.status === 0) return `Can't reach ${target}`;
  switch (code) {
    case "not_live": return `${S} isn't live`;
    case "live_disabled": return `Live is turned off for ${S}`;
    case "transition_running": return "The room is changing mode; try again in a moment";
    case "enforced": return `The current mode keeps ${S} as it is`;
    case "device_busy": return `${S} is busy with another change`;
    case "too_fast": return "One change a second (retried once by itself)";
    case "stream_unreachable": return "The renderer isn't answering the room";
    case "fx_conflict": return "Someone else changed the effects; showing theirs";
    case "artnet_live": return "The real lighting desk is sending, so test music is off";
    case "dmx_disabled": return "The room has no lighting input configured";
    case "too_many_sessions": return "The renderer is full right now";
    case "bad_request": case "ParamError": case "bad_params": case "bad_desired":
      return `The renderer refused a setting: ${detail || code}`;
    case "unknown_screen": return `There's no screen called ${detail || S}`;
    default: break;
  }
  if (res.status === 404) return `${what ? what + ": " : ""}not available here`;
  return `${what ? what + ": " : ""}${detail || code || "HTTP " + res.status}`;
}

/** A plug-in failure in words: "too slow for this GPU (254 ms a frame, needs under 33 ms)". */
export function pluginErrorWords(ev) {
  if (!ev) return "failed";
  if (ev.reason === "slow" || /GpuTooSlow/.test(String(ev.detail || ""))) {
    const ms = Number(ev.gpu_ms) || Number(String(ev.detail || "").match(/([\d.]+)\s*ms/)?.[1]);
    const budget = Number(ev.budget_ms) || 33;
    return `too slow for this GPU${ms ? ` (${Math.round(ms)} ms a frame, needs under ${Math.round(budget)} ms)` : ""}`;
  }
  if (ev.action === "rejected" || ev.phase === "prepare") return "failed to start (a code error in the plug-in)";
  return `failed (${String(ev.detail || ev.action || "error").split("\n")[0].slice(0, 120)})`;
}

export function slowWords(sl) {
  if (!sl) return "";
  if (sl.source === "calibration") return sl.status === "broken" ? "broken on this GPU (calibration)" : `too slow for GPU ${sl.gpu} (calibration${sl.ms != null ? `, ${Math.round(sl.ms)} ms a frame` : ""})`;
  return `too slow for this GPU (${Math.round(sl.ms)} ms a frame, needs under ${Math.round(sl.budget_ms || 33)} ms)`;
}

// ---------------------------------------------------------------- the director, in words
const CAUSE = {
  max_dwell: "it had been on long enough",
  "dmx effect": "the lighting desk changed effect",
  "dmx variant": "the lighting desk changed variant",
  dmx_effect: "the lighting desk changed effect",
  dmx_variant: "the lighting desk changed variant",
  plugin_error: "the previous scene failed",
  music: "the music changed",
  next: "someone asked for the next scene",
};
export function directorWords(ev, label = (s) => friendly(s), plugin = (n) => friendly(n)) {
  if (!ev) return null;
  if (ev.type === "session.transition" && ev.phase === "started") {
    if (ev.kind === "out" || ev.cause === "exit") return "Went back to art";
    const name = ev.scene ? label(ev.scene) : "a new scene";
    if (ev.cause === "go") return `Went live with ${name}`;
    if (ev.cause === "auto") {
      const why = CAUSE[ev.reason] || CAUSE[String(ev.reason || "").replace(/^dmx[ _]/, "dmx ")] || (ev.reason ? String(ev.reason).replace(/_/g, " ") : "it was time for a change");
      return `Changed to ${name} — ${why}`;
    }
    if (ev.cause === "panel" || ev.cause === "cue" || ev.cause === "manual") return `Changed to ${name} — you cued it`;
    if (ev.cause === "patch") return `Changed to ${name} — set by hand`;
    return `Changed to ${name}`;
  }
  if (ev.type === "session.palette") return `Colours: ${ev.palette ? friendly(ev.palette) : "each scene's own"}`;
  if (ev.type === "session.plugin_error") return `${plugin(ev.name)} ${pluginErrorWords(ev)}; the show kept going`;
  if (ev.type === "wander") return ev.text;
  return null;
}

// ---------------------------------------------------------------- show bus / inputs
export function inputsWords(i) {
  if (!i) return "Lighting desk: no reading";
  if (!i.online) {
    // frames still arriving (the bus is up) but the desk's input isn't live: nothing is playing, not "silent for 0 s"
    if (i.stale === "input" || (Number.isFinite(i.age_ms) && i.age_ms < 1000 && i.bus?.connected !== false))
      return "Lighting desk: connected, but nothing is playing";
    if (i.bus && (i.bus.connected === false || i.bus.configured === false)) return i.bus.configured === false ? "Lighting desk: not set up" : "Lighting desk: not connected";
    const age = Number.isFinite(i.age_ms) ? Math.round(i.age_ms / 1000) : null;
    return `Lighting desk: silent${age != null && age >= 1 ? ` for ${age < 120 ? `${age} s` : `${Math.round(age / 60)} min`}` : ""}`;
  }
  const src = i.bus?.source === "fake" || i.bus?.source === "room_service" ? (i.bus?.source === "fake" ? "test show" : "room") : i.bus?.source || "show bus";
  return `Lighting desk: receiving (${src})${Number.isFinite(i.age_ms) ? ` · ${Math.round(i.age_ms)} ms old` : ""}`;
}
export function beatWords(d) {
  if (d?.beat_locked && d?.bpm) return `Beat locked · ${Number(d.bpm).toFixed(1)} bpm`;
  return "Listening for the beat";
}

// ---------------------------------------------------------------- misc tables
export const ORIGIN_WORDS = { core: "Built-in", imported: "Imported", lab: "Lab" };
export const originWords = (o) => (!o || o === "core" ? "Built-in" : String(o).startsWith("lab") ? "Lab" : String(o).startsWith("imported") ? "Imported" : friendly(String(o).split(":")[0]));
export const ENERGY_WORDS = { low: "Calm", mid: "Medium", high: "Lively" };
export const SESSION_WORDS = { holding: "Ready to start", held: "Holding the picture", ended: "Stopped", failed: "Failed", live: "Live",
  entering: "Going live", exiting: "Going back", starting: "Starting" };
export const sessionWords = (s) => SESSION_WORDS[s] || friendly(s || "–");
export const STAGE_WORDS = { warp: "Warp", feedback: "Feedback", glitch: "Glitch", stylise: "Stylise", colour: "Colour", light: "Light",
  projection: "Projection", motion: "Motion", overlay: "Overlay" };

export function diagWords(level, verdict, sustainedTv) {
  if (level === "ok" || !level) return { tone: "ok", text: "Running smoothly" };
  if (level === "bad" && sustainedTv) return { tone: "bad", text: `Dropping frames: ${verdict}` };
  return { tone: "warn", text: `Brief slow frames: ${verdict}` };
}

// ---------------------------------------------------------------- titles and descriptions
const LEAD = /^\s*(?:\[[^\]]*\]|\([^)]*\)|\{[^}]*\}|@)\s*/;
export function humanise(name) {
  const s = String(name || "").replace(/^(st|isf|glt)_/, "").replace(/[_-]+/g, " ").trim();
  return s ? s[0].toUpperCase() + s.slice(1) : String(name || "");
}
export function cleanTitle(s, plugin = "") {
  let t = String(s ?? "");
  for (let i = 0; i < 6; i++) { const n = t.replace(LEAD, ""); if (n === t) break; t = n; }
  t = t.replace(/_/g, " ").replace(/\s+/g, " ").trim();
  if (t.length < 3) t = humanise(plugin || s);
  return t ? t[0].toUpperCase() + t.slice(1) : t;
}
export function authorOf(desc) {
  const m = String(desc || "").match(/converted from \S+ by ([^.,\n]+)/i);
  return m ? m[1].trim() : null;
}
export function sourceLink(desc) {
  const m = String(desc || "").match(/converted from\s+<?(https?:\/\/[^\s>]+)>?/i);
  return m ? m[1].replace(/[.,)]+$/, "") : null;
}
export function sourceSite(url) {
  try { const h = new URL(url).hostname.replace(/^www\./, ""); return /shadertoy/.test(h) ? "Shadertoy" : /glsl/.test(h) ? "GLSL Sandbox" : h; } catch (_) { return "source"; }
}
/** The description without its "(automatically) converted from <url> by X." clause. */
export function cleanDescription(desc) {
  let d = String(desc || "");
  d = d.replace(/(?:automatically\s+)?converted from\s+<?https?:\/\/[^\s>]+>?(?:\s+by\s+[^.\n]+)?\.?/i, "").replace(/\s*,\s*$/, "").trim();
  d = d.replace(/^[.,;:\s]+/, "");
  return d ? d[0].toUpperCase() + d.slice(1) : "";
}

const fmtS = (s) => `${Number(s).toFixed(1).replace(/\.0$/, "")} s`;

/** A tile's subtitle: "Fractal · 3D", "Warp · 1.5 s", "Feedback effect"; + variations and author. */
export function subtitle(it) {
  const parts = [];
  const fam = it.facets?.family?.[0];
  const cap = (x) => (x ? friendly(x) : "");
  if (it.kind === "scene") {
    if (fam) parts.push(cap(fam));
    const d = it.facets?.dims?.[0];
    if (d) parts.push(String(d).toUpperCase());
  } else if (it.kind === "transition") {
    if (fam) parts.push(cap(fam));
    if (it.duration_s != null) parts.push(fmtS(it.duration_s));
  } else {
    parts.push(`${cap(it.stage) || "An"} effect`);
  }
  if (it.heavy === true) parts.push("Heavy");
  const nPre = it.presets?.length || 0;
  if (nPre > 1) parts.push(`${nPre} variations`);
  if (it._author) parts.push(`by ${it._author}`);
  return parts.join(" · ");
}
