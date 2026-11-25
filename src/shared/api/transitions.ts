// A screen's transitions (routes/transitions.py) for the review page: what was made, how it was briefed, its score.
import { jsonInit, requestJson } from '@/shared/http/client';

export interface Transition {
  name: string;
  screen: string;
  url: string;
  from?: string;
  to?: string;
  from_url?: string;
  to_url?: string;
  from_thumb?: string;
  to_thumb?: string;
  connection?: 'link' | 'device' | null;
  device?: string | null;
  idea?: string | null;
  prompt?: string;
  writer?: { link_a?: string; link_b?: string; motion?: string; rest?: string };
  at?: number;
  made_s?: number;
  cost_usd?: number | null;
  score?: number | null;
  note?: string;
}

export interface TransitionFailure {
  at: number;
  error: string;
  made_s?: number;
}

export async function getTransitions(screen: string): Promise<{ transitions: Transition[]; failed: TransitionFailure[] }> {
  return requestJson(`/buckets/${screen}/transitions`, undefined, (r) => `Could not read the transitions: ${r.statusText}`);
}

export async function scoreTransition(screen: string, name: string, score: number | null, note?: string): Promise<Transition> {
  return requestJson(
    `/buckets/${screen}/transitions/${encodeURIComponent(name)}/score`,
    jsonInit('POST', { score, ...(note !== undefined ? { note } : {}) }),
    (r) => `Could not save the score: ${r.statusText}`,
  );
}
