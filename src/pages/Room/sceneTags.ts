// The scene library's tags as a vocabulary (owner, 16 Oct: tag choice is generic, never a hard-coded set). Built from
// the catalog's scene presets: every facet (modes, moods, family, technique, dims, ...) with every word in it and how
// many scenes carry it. Numeric facets (energy ranges, weight) aren't words and are left out.
//
// Matching (the room's rule, so the page's count agrees with the director's pool): a plain word matches a scene when
// any facet has it; "facet:word" matches only that facet; a mode's tags match a scene that has any one of them.

import type { Catalog } from "./types";

export interface SceneTagIndex {
  /** Each scene preset's words by facet. */
  scenes: { name: string; words: Record<string, string[]> }[];
  /** facet -> word -> number of scenes carrying it. */
  facets: Record<string, Record<string, number>>;
}

export function buildTagIndex(catalog: Catalog | null): SceneTagIndex {
  const scenes: SceneTagIndex["scenes"] = [];
  const facets: SceneTagIndex["facets"] = {};
  for (const [name, p] of Object.entries(catalog?.presets?.scenes ?? {})) {
    const tags = (p as { tags?: Record<string, unknown> }).tags ?? {};
    const words: Record<string, string[]> = {};
    for (const [facet, v] of Object.entries(tags)) {
      const list = (Array.isArray(v) ? v : [v]).filter((x): x is string => typeof x === "string" && x !== "");
      if (!list.length) continue;
      words[facet] = list;
      const f = (facets[facet] ??= {});
      for (const w of new Set(list)) f[w] = (f[w] ?? 0) + 1;
    }
    scenes.push({ name, words });
  }
  return { scenes, facets };
}

/** Does one tag ("word" or "facet:word") match a scene's words? */
export function tagMatches(words: Record<string, string[]>, tag: string): boolean {
  const i = tag.indexOf(":");
  if (i > 0) return !!words[tag.slice(0, i)]?.includes(tag.slice(i + 1));
  return Object.values(words).some((ws) => ws.includes(tag));
}

/** How many scenes a set of tags lets the director choose from (any one of the tags). */
export function scenesMatching(index: SceneTagIndex, tags: string[]): number {
  if (!tags.length) return index.scenes.length;
  return index.scenes.filter((s) => tags.some((t) => tagMatches(s.words, t))).length;
}

/** How many scenes carry one tag. */
export function tagCount(index: SceneTagIndex, tag: string): number {
  return scenesMatching(index, [tag]);
}
