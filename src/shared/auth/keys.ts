// API keys for the generation API (/api/v1; routes/auth/keys.py, docs/API.md): Settings › Apps & API keys lists, makes,
// changes, rolls and revokes them. Admins only (config/auth/gates.yaml). The whole key comes back once, when it is made
// or rolled (`secret`); afterwards only its hint (smk_<id>_…<last 4>).
import { jsonInit, requestWithErrorBody } from '@/shared/http/client';

export type KeyScope = 'generate' | 'publish';

export interface ApiKey {
  id: string;
  name: string;
  hint: string;
  state: 'active' | 'revoked' | 'expired';
  scopes: KeyScope[];
  destinations: string[];
  private: boolean;
  /** Pictures in any 24 hours, generations at once (0 = no limit). */
  per_day: number;
  concurrent: number;
  /** Epoch seconds. */
  created_at: number;
  created_by: string | null;
  expires_at: number | null;
  revoked_at: number | null;
  rolled_at: number | null;
  /** While the old secret still works after a roll. */
  prev_until: number | null;
  last_used_at: number | null;
  last_used_ip: string | null;
  uses: number;
  last_24h: number;
}

export interface KeyList {
  keys: ApiKey[];
  scopes: KeyScope[];
  /** The screens a key may be given. */
  destinations: { id: string; name: string }[];
}

export interface KeyFields {
  name?: string;
  scopes?: KeyScope[];
  destinations?: string[];
  private?: boolean;
  per_day?: number;
  concurrent?: number;
  /** null: never. */
  expires_days?: number | null;
}

async function call<T>(path: string, init: RequestInit | undefined, fallback: string): Promise<T> {
  const r = await requestWithErrorBody(path, init, fallback);
  return r.json();
}

export const listKeys = () => call<KeyList>('/auth/keys', undefined, 'Could not read the keys');

export const makeKey = (fields: KeyFields) =>
  call<KeyList & { key: ApiKey; secret: string }>('/auth/keys', jsonInit('POST', fields), 'Could not make the key');

export const changeKey = (id: string, fields: KeyFields) =>
  call<KeyList & { key: ApiKey }>(`/auth/keys/${encodeURIComponent(id)}`, jsonInit('PUT', fields), 'Could not change the key');

export const rollKey = (id: string, graceHours: number) =>
  call<KeyList & { key: ApiKey; secret: string }>(
    `/auth/keys/${encodeURIComponent(id)}/roll`,
    jsonInit('POST', { grace_hours: graceHours }),
    'Could not roll the key',
  );

export const revokeKey = (id: string) =>
  call<KeyList & { key: ApiKey }>(`/auth/keys/${encodeURIComponent(id)}`, { method: 'DELETE' }, 'Could not revoke the key');
