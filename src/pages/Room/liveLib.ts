// Helpers for the live controls (kept out of the component files so fast refresh works).

import { createContext, useMemo } from "react";
import type { RoomApi } from "./api";
import type { PickChip, PickFacet, PickItem } from "./Picker";
import { isPicked } from "./roomFx";
import type { FxEntry, Library, LibraryScene, Look } from "./types";

/** The scroll area thumbnails live in (a picker's grid), so they start loading a little before they show. */
export const ThumbRoot = createContext<HTMLElement | null>(null);

export const STAGE_WORDS: Record<string, string> = {
  warp: "Warp",
  feedback: "Trails",
  glitch: "Glitch",
  stylise: "Style",
  colour: "Colour",
  light: "Light",
  projection: "Projection",
  motion: "Motion",
  overlay: "Overlay",
};

export function cap(s: string): string {
  return s ? s[0].toUpperCase() + s.slice(1).replace(/[_-]+/g, " ") : s;
}

// ── Scenes ───────────────────────────────────────────────────────────────────

export interface SceneInfo {
  key: string;
  label: string;
  thumb: string | null;
}

/** A scene's label and thumbnail by preset (or plain source) name. */
export function sceneInfo(lib: Library | null, api: RoomApi, nameOrKey: string | null | undefined): SceneInfo | null {
  if (!nameOrKey) return null;
  const isSource = nameOrKey.startsWith("source:");
  const name = nameOrKey.replace(/^(preset|source):/, "");
  const s = !isSource ? lib?.scenes.find((x) => x.name === name) : undefined;
  if (s) return { key: `preset:${s.name}`, label: s.label, thumb: api.thumbUrl(s.thumb) };
  const src = lib?.sources?.[name];
  if (src) return { key: `source:${name}`, label: src.label ?? name, thumb: api.thumbUrl(src.thumb) };
  return { key: nameOrKey.includes(":") ? nameOrKey : `preset:${name}`, label: cap(name), thumb: null };
}

function sceneSub(s: LibraryScene): string {
  const bits = [...new Set([s.tags?.family, ...(s.tags?.moods ?? [])].filter(Boolean) as string[])].slice(0, 3);
  return bits.map(cap).join(" · ");
}

/** Sort key: the room's own scenes (core) before imported shader ports; letters before punctuation. */
function sortKey(label: string, origin?: string): string {
  return `${origin === "imported" ? "1" : "0"}${label.replace(/^[^A-Za-z0-9]+/, "").toLowerCase()}`;
}

/** A scene's tag words, every facet whose value is a word or a list of words (numbers like energy aren't tags). */
export function sceneWords(s: LibraryScene): string[] {
  const out = new Set<string>();
  for (const v of Object.values(s.tags ?? {})) {
    for (const w of typeof v === "string" ? [v] : Array.isArray(v) ? v : []) if (typeof w === "string" && w) out.add(w);
  }
  return [...out];
}

/** Every tag word in the library with how many scenes carry it: the picker offers them as you type ("Piano · 9 scenes"). */
export interface TagSuggestion extends PickChip {
  count: number;
}

/** The scene picker's filter groups, from each scene's tags (owner, 18 Oct: all of them, grouped, none clipped). */
const SCENE_GROUPS: Array<{ key: string; label: string }> = [
  { key: "modes", label: "Made for" },
  { key: "family", label: "Kind" },
  { key: "moods", label: "Mood" },
  { key: "genre", label: "Music" },
  { key: "style", label: "Pace" },
  { key: "dims", label: "Look" },
];
const TAG_WORDS: Record<string, string> = {
  "2d": "2D", "3d": "3D", rnb: "R&B", "hip-hop": "Hip-hop", "drum-and-bass": "Drum and bass",
  "singer-songwriter": "Singer-songwriter", __plain: "Plain sources",
};
const tagLabel = (v: string) => TAG_WORDS[v] ?? cap(v);

function tagList(v: unknown): string[] {
  return typeof v === "string" && v ? [v] : Array.isArray(v) ? v.filter((w): w is string => typeof w === "string" && !!w) : [];
}

export function useSceneItems(
  lib: Library | null,
  api: RoomApi,
): { items: PickItem[]; chips: PickChip[]; tags: TagSuggestion[]; facets: PickFacet[] } {
  return useMemo(() => {
    if (!lib) return { items: [], chips: [], tags: [], facets: [] };
    const scenes = [...lib.scenes].sort((a, b) => (sortKey(a.label, a.origin) < sortKey(b.label, b.origin) ? -1 : 1));
    const items: PickItem[] = scenes.map((s) => ({
      key: `preset:${s.name}`,
      label: s.label,
      sub: sceneSub(s),
      thumb: api.thumbUrl(s.thumb),
      tags: sceneWords(s),
      facets: Object.fromEntries(SCENE_GROUPS.map((g) => [g.key, tagList((s.tags as Record<string, unknown> | undefined)?.[g.key])])),
      search: [s.label, s.name, s.source, ...sceneWords(s)].join(" ").toLowerCase(),
    }));
    const counts = new Map<string, number>();
    for (const s of scenes) for (const w of sceneWords(s)) counts.set(w, (counts.get(w) ?? 0) + 1);
    const tags: TagSuggestion[] = [...counts.entries()]
      .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
      .map(([w, n]) => ({ value: w, label: cap(w), count: n }));
    for (const [name, src] of Object.entries(lib.sources ?? {})) {
      items.push({
        key: `source:${name}`,
        label: src.label ?? name,
        sub: "Plain, default settings",
        thumb: api.thumbUrl(src.thumb),
        tags: ["__plain"],
        facets: { family: ["__plain"] },
        search: `${src.label ?? ""} ${name} plain`.toLowerCase(),
      });
    }
    const facets: PickFacet[] = SCENE_GROUPS.map((g) => {
      const vals = new Set<string>();
      for (const it of items) for (const v of it.facets?.[g.key] ?? []) vals.add(v);
      return { key: g.key, label: g.label, values: [...vals].map((v) => ({ value: v, label: tagLabel(v) })) };
    }).filter((g) => g.values.length > 0);
    const fam = (lib.facets?.scenes?.family ?? []).slice(0, 10).map((f) => ({ value: f.value, label: cap(f.value) }));
    const moods = (lib.facets?.scenes?.moods ?? []).slice(0, 10).map((f) => ({ value: f.value, label: cap(f.value) }));
    const seen = new Set(fam.map((f) => f.value));
    return {
      items,
      chips: [...fam, ...moods.filter((m) => !seen.has(m.value)), { value: "__plain", label: "Plain sources" }],
      tags,
      facets,
    };
  }, [lib, api]);
}


// ── Scope (owner, 17 Oct: a control acts where it sits) ──────────────────────

/** The renderer can apply a look PATCH to one screen of a room (scope "screen"); without it a look change is the room's. */
export const LOOK_SCOPE_FEATURE = "look_scope";

export function hasFeature(lib: Library | null | undefined, feature: string): boolean {
  return !!lib?.features?.includes(feature);
}

/** A screen card's place in its room, from the room's live card (RoomLive.tsx). */
export interface LookScope {
  /** The room's live card carries the room-wide controls (picking, Next, energy, how many, Up next); the screen's card
   *  keeps only this screen's. */
  hosted: boolean;
  /** The other live screens in the room (their names): what a room-wide change on this card also changes. */
  shared: string[];
  /** What this screen has set for itself (the session's `own`: direction, palette, beat_follow, fx); the rest is the
   *  room's. Empty on a renderer without look_scope. */
  own: string[];
  /** The room's own ways to take one of its picked effects off, or swap it (they reach every screen in the room). */
  takeOffPicked: (preset: string) => Promise<void>;
  swapPicked: (preset: string) => Promise<void>;
}

const OWN_WORDS: Record<string, string> = { palette: "colours", direction: "feel", fx: "effects", beat_follow: "beat" };

/** "colours and feel": a screen's own parts in words (unknown parts are left out). */
export function ownWords(own: string[] | null | undefined): string | null {
  const w = [...new Set((own ?? []).map((p) => OWN_WORDS[p]).filter(Boolean))];
  return w.length ? (w.length === 1 ? w[0] : `${w.slice(0, -1).join(", ")} and ${w[w.length - 1]}`) : null;
}

/** "both screens", "all 3 screens", or null when nothing else shares it. */
export function bothWords(shared: string[]): string | null {
  if (!shared.length) return null;
  return shared.length === 1 ? "both screens" : `all ${shared.length + 1} screens`;
}

// ── Effects ──────────────────────────────────────────────────────────────────

/** How many effects the picker runs at a time, from auto.fx.max and direction.fx.density: [lo, hi] (equal: exact). */
export function fxRange(max: number | undefined, density: number | undefined): [number, number] {
  const mx = typeof max === "number" ? max : 2;
  const x = (typeof density === "number" ? density : 0.5) * mx;
  const lo = Math.max(0, Math.min(mx, Math.floor(x + 1e-9)));
  const exact = Math.abs(x - Math.round(x)) < 1e-6;
  return [lo, exact ? lo : Math.min(mx, lo + 1)];
}

/** The effect presets as picker tiles, with one chip per kind of effect (warp, trails, colour…). */
export function useEffectItems(lib: Library | null, api: RoomApi): { items: PickItem[]; chips: PickChip[] } {
  return useMemo(
    () => ({
      items: (lib?.effects ?? []).map((e) => ({
        key: e.name,
        label: e.label,
        sub: [e.stage ? STAGE_WORDS[e.stage] ?? cap(e.stage) : null, e.tags?.moods?.[0] ? cap(e.tags.moods[0]) : null]
          .filter(Boolean)
          .join(" · "),
        thumb: api.thumbUrl(e.thumb),
        tags: [e.stage ?? ""],
        search: [e.label, e.name, e.effect, e.stage, e.tags?.family, ...(e.tags?.moods ?? [])].join(" ").toLowerCase(),
      })),
      chips: (lib?.facets?.effects?.stage ?? []).map((f) => ({ value: f.value, label: STAGE_WORDS[f.value] ?? cap(f.value) })),
    }),
    [lib, api],
  );
}

export interface FxChip {
  /** The slot id on the bus, or "scene:<n>" for one that comes with the scene. */
  key: string;
  label: string;
  /** yours: put on this screen by hand · picked: the room's pick · scene: comes with the scene. */
  kind: "yours" | "picked" | "scene";
  entry?: FxEntry;
  preset?: string | null;
}

export function fxLabel(e: FxEntry, lib: Library | null): string {
  return (e.preset && lib?.effects.find((x) => x.name === e.preset)?.label) || e.label || cap(e.effect);
}

export function presetLabel(name: string, lib: Library | null): string {
  return lib?.effects.find((x) => x.name === name)?.label ?? cap(name);
}

/** A look's effects as chips: the bus (yours and the room's picks, filtered by `kinds`), then the scene's own. */
export function fxChips(look: Look | null | undefined, lib: Library | null, kinds: { yours: boolean; picked: boolean } = { yours: true, picked: true }): FxChip[] {
  const out: FxChip[] = [];
  for (const e of look?.fx?.stack ?? []) {
    const picked = isPicked(e);
    if (picked ? !kinds.picked : !kinds.yours) continue;
    out.push({ key: e.id, label: fxLabel(e, lib), kind: picked ? "picked" : "yours", entry: e, preset: e.preset ?? null });
  }
  (look?.current_scene_effects ?? []).forEach((s, i) =>
    out.push({ key: `scene:${i}`, label: s.preset ? presetLabel(s.preset, lib) : cap(s.effect), kind: "scene", preset: s.preset ?? null }),
  );
  return out;
}

/** "That's 6: <name> came off." */
export const droppedWords = (max: number, label: string) => `That's ${max}: ${label} came off.`;

export function mismatchWords(asked: string, got: string | null, lib: Library | null): string | null {
  if (got === asked) return null;
  const label = presetLabel(asked, lib);
  return got ? `${label} doesn't fit with this scene just now; it put ${presetLabel(got, lib)} on.` : `${label} doesn't fit with this scene just now.`;
}
