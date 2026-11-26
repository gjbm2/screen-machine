// The haze as the eye meets it: one number, extinction sigma (per metre), from show_service's modelled haze in the air
// (GET /api/v1/haze estimate_ml_m3). Haze scatters (it barely absorbs), so light from a surface d metres away arrives
// as exp(-sigma d) of itself and the air between makes up the rest with the light it is lit by. Visibility (how far
// you can make things out) is about 3 / sigma.
//
// The levels mean what you see (owner, 26 Nov: "in a thick haze room, things become fuzzy and semi-opaque. it's a
// fog"): light, the room looks clear but beams show (about 60 m); medium, a soft room (about 20 m); thick, a fog (about
// 5 m: the far end of the room is gone). show_service's levels say how many ml/m3 each is; between them sigma is
// interpolated, past thick it carries on at thick's rate.

export const LEVEL_VISIBILITY_M: Record<string, number> = { light: 60, medium: 20, thick: 5 };
const ORDER = ["light", "medium", "thick"];

export type HazeLevels = Record<string, number>;   // {word: ml/m3}, show_service's (GET /api/v1/haze levels)

function table(levels: HazeLevels | null | undefined): [number, number][] {
  const pts: [number, number][] = [[0, 0]];
  for (const w of ORDER) {
    const ml = levels?.[w];
    if (typeof ml === "number" && ml > pts[pts.length - 1][0]) pts.push([ml, 3 / LEVEL_VISIBILITY_M[w]]);
  }
  return pts.length > 1 ? pts : [[0, 0], [0.02, 3 / LEVEL_VISIBILITY_M.thick]];
}

/** Extinction (per metre) for ``ml`` ml/m3 of haze. */
export function sigmaOf(ml: number | null | undefined, levels?: HazeLevels | null): number {
  if (!ml || ml <= 0) return 0;
  const p = table(levels);
  for (let i = 1; i < p.length; i++) {
    if (ml <= p[i][0]) return p[i - 1][1] + ((ml - p[i - 1][0]) / (p[i][0] - p[i - 1][0])) * (p[i][1] - p[i - 1][1]);
  }
  const [a, b] = [p[p.length - 2], p[p.length - 1]];
  return b[1] + ((ml - b[0]) * (b[1] - a[1])) / (b[0] - a[0]);
}

/** The ml/m3 that gives extinction ``sigma`` (sigmaOf's inverse). */
export function mlOf(sigma: number, levels?: HazeLevels | null): number {
  if (sigma <= 0) return 0;
  const p = table(levels);
  for (let i = 1; i < p.length; i++) {
    if (sigma <= p[i][1]) return p[i - 1][0] + ((sigma - p[i - 1][1]) / (p[i][1] - p[i - 1][1])) * (p[i][0] - p[i - 1][0]);
  }
  const [a, b] = [p[p.length - 2], p[p.length - 1]];
  return b[0] + ((sigma - b[1]) * (b[0] - a[0])) / (b[1] - a[1]);
}

// The owner's control: one slider from clear to pea soup. Its position is sqrt(sigma / SIGMA_MAX), so the light end
// has room: light sits at about 0.22, medium 0.39, thick 0.77.
export const SIGMA_MAX = 1.0;                       // visibility 3 m
export const sliderOf = (sigma: number) => Math.sqrt(Math.max(0, Math.min(1, sigma / SIGMA_MAX)));
export const sigmaOfSlider = (p: number) => SIGMA_MAX * p * p;

/** How visible a beam is in the air, 0-1: light caught over a beam's few metres of path. */
export const BEAM_PATH_M = 6;
export const beamVisibility = (sigma: number) => (sigma > 0 ? 1 - Math.exp(-sigma * BEAM_PATH_M) : 0);

/** In words: "clear", or how far you can see. */
export function hazeWords(sigma: number): string {
  if (sigma < 0.003) return "clear";
  const v = 3 / sigma;
  return v >= 100 ? "barely any" : `see ~${v >= 20 ? Math.round(v / 5) * 5 : v.toFixed(v >= 5 ? 0 : 1)} m`;
}
