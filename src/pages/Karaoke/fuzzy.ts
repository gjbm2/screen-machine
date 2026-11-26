// The karaoke panel's library match (owner, 25 Nov: the search "should cope better with misspellings, partial words"):
// every word typed must be close to the start of some word of the song's title or artist. "dancng que" finds
// Dancing Queen, "farell" finds Pharrell Williams, "brightsid" finds Mr. Brightside.

export function normalise(s: string): string {
  return s
    .normalize("NFD")
    .replace(/[̀-ͯ]/g, "")
    .toLowerCase()
    .replace(/&/g, " and ")
    .replace(/[^a-z0-9]+/g, " ")
    .trim();
}

/** Optimal string alignment distance (Levenshtein plus adjacent swaps), stopping early past `max`. */
function distance(a: string, b: string, max: number): number {
  if (Math.abs(a.length - b.length) > max) return max + 1;
  const n = b.length;
  let prev2: number[] = [];
  let prev = Array.from({ length: n + 1 }, (_, j) => j);
  for (let i = 1; i <= a.length; i++) {
    const cur = [i];
    let best = i;
    for (let j = 1; j <= n; j++) {
      const cost = a[i - 1] === b[j - 1] ? 0 : 1;
      let v = Math.min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost);
      if (i > 1 && j > 1 && a[i - 1] === b[j - 2] && a[i - 2] === b[j - 1]) v = Math.min(v, prev2[j - 2] + 1);
      cur.push(v);
      if (v < best) best = v;
    }
    if (best > max) return max + 1;
    prev2 = prev;
    prev = cur;
  }
  return prev[n];
}

/** Roughly how a word sounds, for spelling by ear: "farell" and "pharrell" are both "farel". */
function sound(w: string): string {
  return w
    .replace(/ph/g, "f")
    .replace(/ck/g, "k")
    .replace(/q/g, "k")
    .replace(/c(?=[eiy])/g, "s")
    .replace(/c/g, "k")
    .replace(/z/g, "s")
    .replace(/y/g, "i")
    .replace(/(.)\1+/g, "$1");
}

/** How well one typed word fits one word of the song (0..1): a prefix is 1, else spelling slips forgiven by length,
 *  as written or as it sounds. */
function wordFit(q: string, w: string): number {
  const a = spelt(q, w);
  if (a === 1 || q.length <= 2) return a;
  return Math.max(a, 0.95 * spelt(sound(q), sound(w)));
}

function spelt(q: string, w: string): number {
  if (w.startsWith(q)) return 1;
  if (q.length >= 3 && w.includes(q)) return 0.9;
  if (q.length <= 2) return 0;
  const max = q.length <= 4 ? 1 : q.length <= 7 ? 2 : 3;
  // Against the word's start (a word still being typed) and the whole word (a finished one, misspelt).
  let d = distance(q, w, max);
  for (const k of [q.length - 1, q.length, q.length + 1]) {
    if (k > 0 && k < w.length) d = Math.min(d, distance(q, w.slice(0, k), max));
  }
  return d > max ? 0 : 1 - d / (q.length + 1);
}

/** 0 (no match) .. 1 (every word typed starts a word of the song). */
export function fuzzyScore(query: string, haystack: string): number {
  const qs = normalise(query).split(" ").filter(Boolean);
  if (!qs.length) return 0;
  const ws = normalise(haystack).split(" ").filter(Boolean);
  // Words run together ("mrbrightside") or apart ("bright side") still meet: the whole haystack as one word too.
  ws.push(ws.join(""));
  let sum = 0;
  for (const q of qs) {
    let best = 0;
    for (const w of ws) {
      best = Math.max(best, wordFit(q, w));
      if (best === 1) break;
    }
    if (best < 0.5) return 0;
    sum += best;
  }
  return sum / qs.length;
}

export function fuzzyFilter<T>(items: T[], query: string, text: (t: T) => string, min = 0.7): T[] {
  return items
    .map((it) => ({ it, s: fuzzyScore(query, text(it)) }))
    .filter((x) => x.s >= min)
    .sort((a, b) => b.s - a.s)
    .map((x) => x.it);
}
