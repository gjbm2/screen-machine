// Sound (docs/SOUND.md): Flask's /api/sound/*, same-origin. Where sound plays (the app's one list of outputs,
// smsound.outputs), how late each is heard (the one latency store, smsound.latency), and measuring it with the room mic
// (routes/sound/calibrate.py). Karaoke, the piano and Settings all read these rows, so every picker shows the same
// places in the same words, each with its delay.

import { sendRelative } from '@/shared/http/client';

export type OutputKind = 'screen' | 'pc22' | 'sonos' | 'device' | 'none';

/** An output's delay: + ms = heard that much after it is scheduled. `effective_ms` = measured + trim, else the kind's
 *  default, else null (the player uses its own figure: a browser's reported output latency). */
export interface OutputLatency {
  effective_ms: number | null;
  delay_ms: number | null;
  trim_ms: number;
  method: string | null;
  measured_at: string | null;
  /** measured | manual | default | browser (not measured: the browser's own figure) | live (the piano measures it as it
   *  plays) | device (the phone measures itself) | none */
  source: 'measured' | 'manual' | 'default' | 'browser' | 'live' | 'device' | 'none';
  spread_ms?: number;
  agree?: number;
  of?: number;
}

export interface SoundOutput {
  id: string;
  kind: OutputKind;
  kind_label: string;
  label: string;
  room: string | null;
  hint: string | null;
  /** How its delay is measured: chirp (here, with the room mic) | live | device | null */
  calibrate: 'chirp' | 'live' | 'device' | null;
  latency?: OutputLatency;
  /** For a consumer (?for=): whether it can play there, and if not why. */
  available?: boolean;
  why?: string;
}

export interface CalibrationRun {
  id?: string;
  state: 'idle' | 'running' | 'done' | 'failed';
  output?: string;
  label?: string;
  phase?: 'mic' | 'sonos' | 'listening' | null;
  message?: string;
  /** Server times (s) of the chirps; with `chirps`, what the screen being measured plays. */
  clicks?: number[];
  chirps?: { f0: number; f1: number; dur_s: number }[];
  delay_ms?: number;
  spread_ms?: number;
  at?: number;
  /** The server's clock when it answered (s). */
  now?: number;
}

export interface LatencyRow {
  key: string;
  label: string;
  value_ms: number | null;
  trim_ms?: number;
  measured_at?: string | null;
  method?: string | null;
  measures: string;
  how: string;
  used_by: string;
}

export interface LatencyOverview {
  file: string;
  rule: string;
  sound: (SoundOutput & { latency: OutputLatency })[];
  screens: LatencyRow[];
  lights: LatencyRow[];
  sources: LatencyRow[];
}

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const r = await sendRelative(`/sound${path}`, init);
  const body = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error((body as { error?: string }).error || `Sound: ${r.status}`);
  return body as T;
}

const send = (method: string, body: unknown): RequestInit => ({
  method,
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(body),
});

export const sound = {
  outputs: (consumer?: 'karaoke' | 'music') =>
    call<{ outputs: SoundOutput[] }>(`/outputs${consumer ? `?for=${consumer}` : ''}`),
  latency: () => call<LatencyOverview>('/latency'),
  setLatency: (id: string, body: { trim_ms?: number; delay_ms?: number | null; forget?: boolean }) =>
    call<{ id: string; latency: OutputLatency }>(`/outputs/${encodeURIComponent(id)}/latency`, send('PUT', body)),
  calibrate: (output: string) => call<CalibrationRun>('/calibrate', send('POST', { output })),
  calibration: () => call<CalibrationRun>('/calibrate'),
};

/** "134 ms", "about 0.5 s"; null: "not measured". */
export function msText(ms: number | null | undefined): string {
  if (ms == null) return 'not measured';
  return Math.abs(ms) >= 1000 ? `${(ms / 1000).toFixed(2)} s` : `${Math.round(ms)} ms`;
}

/** One line on how an output's delay is known. */
export function latencyText(l: OutputLatency | undefined): string {
  if (!l) return '';
  const trim = l.trim_ms ? ` (${l.trim_ms > 0 ? '+' : ''}${Math.round(l.trim_ms)} ms trim)` : '';
  const when = l.measured_at ? `, ${new Date(l.measured_at).toLocaleDateString(undefined, { day: 'numeric', month: 'short' })}` : '';
  switch (l.source) {
    case 'measured':
      return `${msText(l.effective_ms)} measured${when}${trim}`;
    case 'manual':
      return `${msText(l.effective_ms)} set by hand${trim}`;
    case 'default':
      return `${msText(l.effective_ms)} (not measured: the usual figure)${trim}`;
    case 'browser':
      return l.trim_ms ? `the browser's own figure${trim}` : "not measured: the browser's own figure";
    case 'live':
      return 'measured by the piano as it plays';
    case 'device':
      return 'the phone measures its own';
    default:
      return '';
  }
}
