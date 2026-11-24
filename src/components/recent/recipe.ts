// Start again (owner, 24 Nov): what a generated picture or clip was made from, read back from what is kept with it, so
// the generator can be set up to make it again. Every result keeps its request in its sidecar (routes/generate_utils.py):
// the words as written (`original_prompt`), the workflow, the settings sent (`original_params`), the prompt helper asked
// for (`original_refiner`) and its pictures, in order (`reference_images`, stored beside it as <file>.ref.N). Pure, so it
// is tested without a browser:
//   node --experimental-strip-types --test src/components/recent/recipe.test.ts

export const FILM_WORKFLOW = 'h3-film.json';

/** Settings the app works out or sets for itself: never put back in the form. */
const OWN_PARAMS = ['publish_destination', 'poll_interval', 'keep_with_picture', 'video_aspect', 'video_seconds', 'film_link'];

export interface CreateRecipe {
  kind: 'create';
  prompt: string;
  /** the workflow it ran on ('auto' when unknown) */
  workflow: string;
  params: Record<string, unknown>;
  /** the prompt helper asked for ('auto' when it was left to the app) */
  refiner: string;
  /** links to its pictures, in the order they were given */
  pictures: string[];
}

export interface FilmRecipe {
  kind: 'film';
  /** links to the slots' pictures; null where it isn't known */
  pictures: Array<string | null>;
  links: Array<{ text: string; seconds: number }>;
  params: Record<string, unknown>;
  refiner: 'auto' | 'none';
}

export type Recipe = CreateRecipe | FilmRecipe;

/** Just what is read of a Generated item (an ImageItem). */
type Ref = { index?: number; stored_path?: string };

/** What is kept in a result's sidecar, as far as it is read here. */
interface Kept {
  workflow?: string;
  batch_id?: string;
  original_prompt?: string;
  prompt?: string;
  original_refiner?: string | null;
  original_params?: Record<string, unknown> & { film_link?: unknown; video_seconds?: unknown };
  /** the film's segments joined into one clip (routes/film.py) */
  joined_film?: boolean;
  reference_images?: Ref[];
}

export interface Made {
  promptKey?: string;
  bucketId?: string;
  reference_images?: Ref[];
  metadata?: Record<string, unknown>;
  createdAt?: string;
}

const meta = (item: Made): Kept => (item.metadata || {}) as Kept;

/** Links to an item's pictures, in the order they were given. */
export function pictureUrls(item: Made): string[] {
  const refs = [...(item.reference_images?.length ? item.reference_images : meta(item).reference_images || [])];
  refs.sort((a, b) => (a.index ?? 0) - (b.index ?? 0));
  const bucket = item.bucketId || '_recent';
  return refs
    .map((r) => String(r.stored_path || ''))
    .filter(Boolean)
    .map((p) => (p.startsWith('http') || p.startsWith('/') ? p : `/output/${bucket}/${p}`));
}

function ownSettings(params: unknown): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  if (params && typeof params === 'object') {
    for (const [k, v] of Object.entries(params as Record<string, unknown>)) if (!OWN_PARAMS.includes(k)) out[k] = v;
  }
  return out;
}

const wordsOf = (item: Made): string => {
  const m = meta(item);
  return String(m.original_prompt ?? item.promptKey ?? m.prompt ?? '');
};

export const isFilmSegment = (item: Made): boolean => meta(item).workflow === FILM_WORKFLOW;

/** Where a segment sits in its film: its link's index (sent with it since 24 Nov), else unknown. */
const linkOf = (item: Made): number | null => {
  const n = Number(meta(item).original_params?.film_link);
  return Number.isInteger(n) && n >= 0 ? n : null;
};

function filmRecipe(item: Made, batch: Made[]): FilmRecipe {
  const m = meta(item);
  // the film's other segments: the batch's, each at its link (the newest where a link was made again); a segment that
  // doesn't say where it goes stands alone
  const own = linkOf(item);
  const segments = new Map<number, Made>();
  if (own !== null || m.joined_film) {
    for (const other of batch) {
      const at = linkOf(other);
      if (!isFilmSegment(other) || at === null || meta(other).batch_id !== m.batch_id) continue;
      const have = segments.get(at);
      if (!have || String(other.createdAt || '') > String(have.createdAt || '')) segments.set(at, other);
    }
  }
  if (!segments.size) segments.set(0, item);
  const last = Math.max(...segments.keys());
  const pictures: Array<string | null> = Array(last + 2).fill(null);
  const links: Array<{ text: string; seconds: number }> = [];
  for (let i = 0; i <= last; i++) {
    const seg = segments.get(i);
    const [from, to] = seg ? pictureUrls(seg) : [];
    pictures[i] = pictures[i] ?? from ?? null;
    pictures[i + 1] = to ?? null;
    links.push({ text: seg ? wordsOf(seg) : '', seconds: Number(seg ? meta(seg).original_params?.video_seconds : NaN) || 5 });
  }
  // the film's settings: a segment's (a joined film keeps none of its own)
  const settings = m.joined_film ? meta(segments.get(Math.min(...segments.keys())) as Made) : m;
  return {
    kind: 'film',
    pictures,
    links,
    params: ownSettings(settings.original_params),
    refiner: settings.original_refiner === 'none' ? 'none' : 'auto',
  };
}

/** What `item` was made from. `batch`: the items made with it, if known (a film is rebuilt from all its segments). */
export function recipeOf(item: Made, batch: Made[] = []): Recipe {
  if (isFilmSegment(item)) return filmRecipe(item, batch);
  const m = meta(item);
  return {
    kind: 'create',
    prompt: wordsOf(item),
    workflow: typeof m.workflow === 'string' && m.workflow ? m.workflow : 'auto',
    params: ownSettings(m.original_params),
    refiner: typeof m.original_refiner === 'string' && m.original_refiner ? m.original_refiner : 'auto',
    pictures: pictureUrls(item),
  };
}
