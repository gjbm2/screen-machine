// node --test stream_service/static/console/tests/  (the pure plumbing only: query.js and words.js)
import { test } from "node:test";
import assert from "node:assert/strict";
import { normalise, applyRating, matches, counts, facetValues, ratingOk, sortOptions, sorted, labelForSpec,
         activeFilters, badgeCount, tokens, variantTitle } from "../data/query.js";
import { cleanTitle, authorOf, sourceLink, summariseScreen, cleanDescription, pluginErrorWords, directorWords, inputsWords,
         roomErrorWords } from "../words.js";

const IDX = { items: [
  { kind: "scene", name: "fluid_ink", plugin: "fluid_ink", label: "Fluid ink", preset: "fluid-ink-neon", spec: "fluid-ink-neon",
    presets: [{ name: "fluid-ink-neon", label: "Neon swirl" }, { name: "fluid-ink-deep", label: "Deep sea" }],
    facets: { family: ["abstract"], moods: ["calm", "cosmic"], dims: ["2d"], origin: ["core"], energy: ["low"] }, energy: [0.1, 0.3] },
  { kind: "scene", name: "st_x", plugin: "st_x", label: "[NV15] 2001 Space_station", preset: "st-x", spec: "st-x",
    presets: [{ name: "st-x", label: "[NV15] 2001 Space_station" }],
    description: "Automatically converted from https://www.shadertoy.com/view/abc by Someone Clever. Nice.",
    facets: { family: ["cosmic"], moods: ["cosmic", "dark"], dims: ["3d"], origin: ["imported"], energy: ["mid", "high"] } },
  { kind: "scene", name: "circuits", plugin: "circuits", label: "(CLEAN) circuits", preset: "circ", spec: "circ",
    presets: [{ name: "circ", label: "Circuits" }], facets: { family: ["fractal"], dims: ["2d"], origin: ["core"] } },
  { kind: "transition", name: "burn", plugin: "burn", label: "Burn", preset: "burn-2s", spec: "burn-2s", duration_s: 2,
    presets: [{ name: "burn-2s", label: "Burn" }], facets: { family: ["organic"], style: ["wild"], origin: ["core"] } },
  { kind: "effect", name: "bloom", plugin: "bloom", label: "Bloom", preset: "bloom-haze", spec: "bloom-haze", stage: "light",
    presets: [{ name: "bloom-haze", label: "Concert haze" }], facets: { stage: ["light"], moods: ["warm"], origin: ["core"] } },
] };

const base = () => ({ q: "", stars: { n: null, op: "ge" }, rejects: "hide", facets: { scene: {}, transition: {}, effect: {} } });

test("counts equal the number of matches for every kind", () => {
  const items = normalise(IDX);
  applyRating(items[0], { stars: 3 });
  applyRating(items[1], { stars: 1, reject: true });
  const sts = [base(), { ...base(), q: "c" }, { ...base(), stars: { n: 1, op: "ge" } },
    { ...base(), facets: { scene: { look: ["cosmic"] }, transition: {}, effect: {} } }, { ...base(), rejects: "only" }];
  for (const st of sts) {
    const c = counts(items, st);
    for (const k of ["scene", "transition", "effect"]) {
      assert.equal(c[k], items.filter((i) => matches(i, { ...st, qt: tokens(st.q) }, k)).length, `${k} ${JSON.stringify(st)}`);
    }
  }
});

test("rating ops and rejects", () => {
  const items = normalise(IDX);
  applyRating(items[0], { stars: 3 });
  applyRating(items[2], { stars: 5 });
  applyRating(items[1], { reject: true });
  const st = (n, op, rejects = "hide") => ({ stars: { n, op }, rejects });
  assert.equal(ratingOk(items[0], st(3, "ge")), true);
  assert.equal(ratingOk(items[0], st(3, "eq")), true);
  assert.equal(ratingOk(items[2], st(3, "le")), false);
  assert.equal(ratingOk(items[3], st(3, "le")), true);            // unrated counts as 0
  assert.equal(ratingOk(items[3], st(0, "eq")), true);            // "Unrated"
  assert.equal(ratingOk(items[0], st(0, "eq")), false);
  assert.equal(ratingOk(items[1], st(null, "ge")), false);        // rejects hidden by default
  assert.equal(ratingOk(items[1], st(null, "ge", "show")), true);
  assert.equal(ratingOk(items[0], st(null, "ge", "only")), false);
});

test("Look merges family and moods, once each", () => {
  const items = normalise(IDX);
  assert.deepEqual(items[1].ff.look, ["cosmic", "dark"]);
  const vals = facetValues(items, base(), "scene", "look").map(([v]) => v);
  assert.equal(vals.filter((v) => v === "cosmic").length, 1);
});

test("facet counts exclude the facet's own selection", () => {
  const items = normalise(IDX);
  const st = { ...base(), facets: { scene: { look: ["fractal"] }, transition: {}, effect: {} } };
  const m = new Map(facetValues(items, st, "scene", "look"));
  assert.equal(m.get("cosmic"), 2);      // fluid_ink + st_x, ignoring the fractal pick
  const d = new Map(facetValues(items, st, "scene", "dims"));
  assert.equal(d.get("2d"), 1);          // only circuits is fractal
});

test("Fastest is hidden without bench data", () => {
  const items = normalise(IDX);
  assert.ok(!sortOptions(items).some(([k]) => k === "fastest"));
  items[0].bench_ms_4k = 3;
  assert.ok(sortOptions(items).some(([k]) => k === "fastest"));
});

test("A-Z sorts by the cleaned title", () => {
  const items = normalise(IDX).filter((i) => i.kind === "scene");
  assert.deepEqual(sorted(items, "az").map((i) => i.title), ["2001 Space station", "Circuits", "Fluid ink"]);
});

test("cleanTitle, authorOf, sourceLink, cleanDescription", () => {
  assert.equal(cleanTitle("[NV15] 2001 Space Station"), "2001 Space Station");
  assert.equal(cleanTitle("(CLEAN) first shader/sdf"), "First shader/sdf");
  assert.equal(cleanTitle("@Party Concert Visuals 2020"), "Party Concert Visuals 2020");
  assert.equal(cleanTitle("[290 min char] Hyper swirls"), "Hyper swirls");
  assert.equal(cleanTitle("[x]", "st_glow_worm"), "Glow worm");
  assert.equal(cleanTitle("my_cool_thing"), "My cool thing");
  const d = "Automatically converted from https://www.shadertoy.com/view/MtcGD7 by CaliCoastReplay. A big thank you.";
  assert.equal(authorOf(d), "CaliCoastReplay");
  assert.equal(sourceLink(d), "https://www.shadertoy.com/view/MtcGD7");
  assert.equal(cleanDescription(d), "A big thank you.");
  assert.equal(sourceLink("FBM noise, converted from <https://www.shadertoy.com/view/wttXz8>"), "https://www.shadertoy.com/view/wttXz8");
});

test("labelForSpec names presets, plug-ins and live art", () => {
  const items = normalise(IDX);
  assert.equal(labelForSpec("fluid-ink-deep", items), "Fluid ink · Deep sea");
  assert.equal(labelForSpec("circ", items), "Circuits");
  assert.equal(labelForSpec({ source: "fluid_ink" }, items), "Fluid ink");
  assert.equal(labelForSpec({ source: "live_art" }, items), "Live art");
  assert.equal(labelForSpec("unknown-thing", items), "Unknown thing");
});

test("active filters and the badge agree", () => {
  const st = { ...base(), stars: { n: 3, op: "ge" }, rejects: "show", facets: { scene: { look: ["calm", "cosmic"] }, transition: {}, effect: {} } };
  assert.equal(activeFilters(st, "scene").length, 4);
  assert.equal(badgeCount(st, "scene"), 4);
  assert.equal(badgeCount(base(), "scene"), 0);
});

test("Room phase words", () => {
  const art = { desired: "art", observed: { phase: "idle", layer: "art" } };
  assert.equal(summariseScreen(art).text, "Showing art");
  assert.equal(summariseScreen({ desired: { live: { scene: "x" } }, observed: { phase: "entering" } }).text, "Switching to live…");
  assert.equal(summariseScreen({ desired: "art", observed: { phase: "exiting" } }).text, "Going back to art…");
  assert.equal(summariseScreen({ desired: { live: { scene: "x" } }, observed: { phase: "live", layer: "live" } }, () => "Fluid ink").text, "Live · Fluid ink");
  assert.equal(summariseScreen({ desired: { live: { scene: "x" } }, observed: { phase: "failed" } }).tone, "bad");
  assert.equal(summariseScreen({ desired: { live: { scene: "x" } }, observed: { phase: "idle", page_connected: false } }).text, "Waiting to go live…");
  assert.equal(summariseScreen({ desired: { live: { scene: "x" } }, desired_expires_in_s: 102, observed: { phase: "live" } }, () => "C").text, "Live · C · 1:42 left");
});

test("plug-in errors and director lines in words", () => {
  assert.match(pluginErrorWords({ reason: "slow", gpu_ms: 254, budget_ms: 33.3 }), /too slow for this GPU \(254 ms a frame, needs under 33 ms\)/);
  assert.match(pluginErrorWords({ detail: "GpuTooSlow: st_circuits: 254.0 ms" }), /254 ms/);
  const l = directorWords({ type: "session.transition", phase: "started", kind: "scene", scene: "dune", cause: "auto", reason: "max_dwell" });
  assert.equal(l, "Changed to Dune — it had been on long enough");
  assert.ok(!l.includes("{"));
});

test("a variation's label doesn't repeat its title", () => {
  assert.equal(variantTitle("Crossfade", "Crossfade 3 s"), "Crossfade 3 s");
  assert.equal(variantTitle("Fluid ink", "Deep sea"), "Fluid ink · Deep sea");
  assert.equal(variantTitle("Burn", "Burnished"), "Burn · Burnished");
  assert.equal(variantTitle("Melt", "Melt"), "Melt");
});

test("the lighting desk in words", () => {
  assert.equal(inputsWords({ online: false, stale: "input", age_ms: 48, bus: { connected: true } }), "Lighting desk: connected, but nothing is playing");
  assert.equal(inputsWords({ online: false, stale: "link", age_ms: 12400, bus: { connected: true } }), "Lighting desk: silent for 12 s");
  assert.equal(inputsWords({ online: false, stale: "link", age_ms: null, bus: { connected: false, configured: true } }), "Lighting desk: not connected");
  assert.doesNotMatch(inputsWords({ online: false, age_ms: 50 }), /for 0 s/);
});

test("room's go-live fallbacks in words", () => {
  assert.equal(roomErrorWords("fallback: page could not join: play-failed"), "the TV page couldn't play the live stream");
  assert.equal(roomErrorWords("entry ack frozen: timeout after 5 s"), "the TV page didn't answer in time");
  const sc = { desired: { live: { scene: "x" } }, last_error: "fallback: page could not join: play-failed", observed: { phase: "idle", fallbacks_in_window: 2, retry_in_s: 2 } };
  const sum = summariseScreen(sc);
  assert.equal(sum.text, "Waiting to go live…");
  assert.equal(sum.why, "the TV page couldn't play the live stream");
  assert.equal(sum.tries, 2);
  // a stale error from an earlier try isn't blamed on this one
  assert.equal(summariseScreen({ ...sc, observed: { phase: "idle", fallbacks_in_window: 0 } }).why, undefined);
});
