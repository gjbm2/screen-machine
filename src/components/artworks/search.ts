// Search over the famous-artworks library (output/_artworks/library.json, made by tools/artworks/fetch.py from
// config/artworks/catalogue.yaml). Pure, so it is tested without a browser:
//   node --experimental-strip-types --test src/components/artworks/search.test.ts

export interface Artwork {
  id: string;
  title: string;
  artist: string;
  year: number;
  movement: string;
  period: string;
  genre: string;
  medium: string;
  people: 'none' | 'one' | 'two' | 'group';
  tags: string[];
  aka: string[];
  width: number;
  height: number;
  image: string;
  thumb: string;
  source: { article: string; file: string; license: string | null };
}

export interface Library {
  periods: string[];
  works: Artwork[];
}

export interface Filters {
  period?: string | null;
  genre?: string | null;
  /** only pictures with people in them (to put someone in) */
  people?: boolean;
}

/** Lower case, accents off ("Dürer" -> "durer", "Hokusai" stays), punctuation to spaces. */
export function fold(text: string): string {
  return text
    .normalize('NFD')
    .replace(/[̀-ͯ]/g, '')
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, ' ')
    .trim();
}

/** "1665" -> 17th century words, so "17th century" or "1600s" find it. */
function eraWords(year: number): string {
  if (year < 0) return 'ancient bc';
  const century = Math.floor(year / 100) + 1;
  const suffix = century % 10 === 1 && century !== 11 ? 'st' : century % 10 === 2 && century !== 12 ? 'nd' : century % 10 === 3 && century !== 13 ? 'rd' : 'th';
  return `${year} ${Math.floor(year / 100) * 100}s ${Math.floor(year / 10) * 10}s ${century}${suffix} century`;
}

interface Indexed {
  work: Artwork;
  /** where a hit counts most: title, artist, other names */
  name: string;
  /** everything else that describes it */
  rest: string;
}

export function indexLibrary(works: Artwork[]): Indexed[] {
  return works.map((work) => ({
    work,
    name: fold([work.title, work.artist, ...work.aka].join(' ')),
    rest: fold([work.movement, work.period, work.genre, work.medium, ...work.tags, eraWords(work.year)].join(' ')),
  }));
}

const wordStarts = (haystack: string, word: string) => haystack.startsWith(word) || haystack.includes(` ${word}`);

/** The works matching the query and filters, best first: every word must match somewhere; a word at the start of a
 *  name word beats one inside it, and a name hit beats a tag hit. With no words, by year. */
export function searchArtworks(index: Indexed[], query: string, filters: Filters = {}): Artwork[] {
  const words = fold(query).split(' ').filter(Boolean);
  const scored: { work: Artwork; score: number }[] = [];
  for (const entry of index) {
    const w = entry.work;
    if (filters.period && w.period !== filters.period) continue;
    if (filters.genre && w.genre !== filters.genre && !(filters.genre === 'portrait' && w.genre === 'self-portrait')) continue;
    if (filters.people && w.people === 'none') continue;
    let score = 0;
    let all = true;
    for (const word of words) {
      if (wordStarts(entry.name, word)) score += 3;
      else if (entry.name.includes(word)) score += 2;
      else if (wordStarts(entry.rest, word)) score += 1;
      else if (word.length >= 3 && entry.rest.includes(word)) score += 0.5;
      else { all = false; break; }
    }
    if (all) scored.push({ work: w, score });
  }
  scored.sort((a, b) => b.score - a.score || a.work.year - b.work.year);
  return scored.map((s) => s.work);
}

export interface Suggestion {
  /** what the search box becomes */
  text: string;
  kind: 'artist' | 'title' | 'tag';
}

/** Type-ahead for the last word being typed: artists, titles and tags that start with it (up to `limit`). */
export function suggest(works: Artwork[], query: string, limit = 6): Suggestion[] {
  const q = fold(query);
  if (q.length < 2) return [];
  const seen = new Set<string>();
  const out: Suggestion[] = [];
  const add = (text: string, kind: Suggestion['kind']) => {
    const key = fold(text);
    if (seen.has(key) || key === q) return;
    if (!wordStarts(key, q)) return;
    seen.add(key);
    out.push({ text, kind });
  };
  for (const w of works) add(w.artist, 'artist');
  for (const w of works) { add(w.title, 'title'); w.aka.forEach((a) => add(a, 'title')); }
  for (const w of works) w.tags.forEach((t) => add(t, 'tag'));
  const rank = { artist: 0, title: 1, tag: 2 };
  return out.sort((a, b) => rank[a.kind] - rank[b.kind]).slice(0, limit);
}
