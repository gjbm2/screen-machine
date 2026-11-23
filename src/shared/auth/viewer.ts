// Who is using the app (docs/ACCOUNTS.md): /api/auth/me, read once per page with useViewer(), plus Google sign-in and
// sign-out. While the deployment has no sign-in set up (`accounts` false) everyone is an admin, as before.
import { useSyncExternalStore } from 'react';
import { jsonInit, requestJson, send } from '@/shared/http/client';

export type Role = 'guest' | 'approved' | 'admin';

export interface Viewer {
  /** Sign-in and roles are in force. */
  accounts: boolean;
  google_client_id: string | null;
  signed_in: boolean;
  email: string | null;
  name: string | null;
  role: Role;
  /** The hidden buckets (routes/auth/lock.py): this viewer may unlock them; a PIN is chosen; open until (epoch s). */
  can_unlock?: boolean;
  pin_set?: boolean;
  unlocked_until?: number | null;
}

const ROLES: Role[] = ['guest', 'approved', 'admin'];

/** Until /auth/me answers (or if it fails), the page acts as it did before accounts. */
const UNKNOWN: Viewer = { accounts: false, google_client_id: null, signed_in: false, email: null, name: null, role: 'admin' };

let state: Viewer = UNKNOWN;
let loaded = false;
let loading: Promise<void> | null = null;
const listeners = new Set<() => void>();

let relock: ReturnType<typeof setTimeout> | null = null;

function set(next: Viewer) {
  state = next;
  loaded = true;
  // the hidden buckets close by themselves: read again when the unlock runs out, so every list drops them
  if (relock) clearTimeout(relock);
  relock = null;
  if (next.unlocked_until) {
    relock = setTimeout(() => void refreshViewer(), Math.max(0, next.unlocked_until * 1000 - Date.now()) + 1000);
  }
  listeners.forEach((l) => l());
}

export function refreshViewer(): Promise<void> {
  loading = requestJson<Viewer>('/auth/me', undefined, 'Could not read who is signed in')
    .then(set)
    .catch(() => set(UNKNOWN));
  return loading;
}

function subscribe(listener: () => void) {
  listeners.add(listener);
  if (!loaded && !loading) void refreshViewer();
  return () => listeners.delete(listener);
}

export function useViewer(): Viewer {
  return useSyncExternalStore(subscribe, () => state);
}

/** Whether the viewer has at least `role` (always, while accounts are off). */
export function atLeast(viewer: Viewer, role: Role | undefined): boolean {
  if (!viewer.accounts) return true;
  return ROLES.indexOf(viewer.role) >= ROLES.indexOf(role ?? 'admin');
}

/** A destination is usable by roles from its `access` (publish-destinations.json; default admin). */
export function canUseDestination(viewer: Viewer, dest: { access?: Role }): boolean {
  return atLeast(viewer, dest.access);
}

/** Google sign-in: the ID token from Google's button, checked by Flask, which sets the session cookie. */
export async function signInWithGoogle(credential: string): Promise<Viewer> {
  const me = await requestJson<Viewer>('/auth/google', jsonInit('POST', { credential }), 'Sign-in failed');
  set(me);
  return me;
}

/** Shows the hidden buckets to this browser for 30 minutes (admins; the first PIN typed becomes the PIN). Rejects
 * with the server's reason (e.g. "wrong PIN"). */
export async function unlockHidden(pin: string): Promise<Viewer> {
  const r = await send('/auth/unlock', jsonInit('POST', { pin }));
  const body = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(body.error || 'Could not unlock');
  set(body as Viewer);
  return body as Viewer;
}

export async function lockHidden(): Promise<void> {
  const me = await requestJson<Viewer>('/auth/lock', { method: 'POST' }, 'Could not lock');
  set(me);
}

export async function signOut(): Promise<void> {
  await requestJson('/auth/signout', { method: 'POST' }, 'Sign-out failed');
  await refreshViewer();
}

/** Someone listed in, or seen by, the deployment's accounts (Settings › People). Times are epoch seconds. */
export interface Person {
  email: string;
  name: string | null;
  role: Role;
  first_seen: number | null;
  last_seen: number | null;
}

export interface PeopleList {
  people: Person[];
  roles: Role[];
  /** The admin looking. */
  me: string | null;
}

export function listPeople(): Promise<PeopleList> {
  return requestJson<PeopleList>('/auth/people', undefined, 'Could not read the people');
}

/** Gives someone a role; guest takes them off the list. Rejects with the server's reason (e.g. the last admin). */
export async function setPersonRole(email: string, role: Role): Promise<PeopleList> {
  const r = await send('/auth/people', jsonInit('PUT', { email, role }));
  const body = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(body.error || 'Could not change the role');
  return body as PeopleList;
}
