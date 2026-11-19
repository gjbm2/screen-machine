/* eslint-disable @typescript-eslint/no-explicit-any */
// The Loope's Info panel: what a picture (or clip) is, in plain words, then the detail.
//
// Order: what it is and when it was made; the prompt; how it was made; its movement (a still's clips, or a clip's own
// play info: pace, length, rests; routes/clips.py, routes/clip_ease.py); references; tags; and everything else in
// the file's metadata under "Technical details", folded away.
import React, { useState } from 'react';
import { Check, ChevronDown, Copy, Film, Trash2, Wand2, X } from 'lucide-react';
import { toast } from 'sonner';
import { animateStill, chooseClip, deleteFromBucket } from '@/shared/api/buckets';
import { Button } from '@/components/ui/button';
import { useWorkflows } from '@/data/typedWorkflows';
import { ReferenceImageService } from '@/services/reference-image-service';
import type { ImageItem } from '@/types/image-types';

/** Music groups the tagger scored a good fit (0.7+), best first (docs/ART_TAGS.md). */
function suitedGroups(music: Record<string, number> | undefined): string[] {
  if (!music || typeof music !== 'object') return [];
  return Object.entries(music)
    .filter(([, v]) => typeof v === 'number' && v >= 0.7)
    .sort((a, b) => b[1] - a[1])
    .map(([k]) => k);
}

const CAMERA = /\s*(Static shot from a tripod:[^.]*|The camera is locked off[^.]*;[^.]*)\.?\s*$/i;

function when(value: unknown): string | null {
  const n = typeof value === 'number' ? value : typeof value === 'string' && value ? Date.parse(value) / 1000 : NaN;
  if (!Number.isFinite(n) || n <= 0) return null;
  const d = new Date(n * 1000);
  return d.toLocaleString(undefined, { weekday: 'short', day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' });
}

function duration(s: unknown): string | null {
  const n = Number(s);
  if (!Number.isFinite(n) || n <= 0) return null;
  if (n < 60) return `${Math.round(n)} s`;
  const m = Math.floor(n / 60);
  const r = Math.round(n - m * 60);
  return r ? `${m} min ${r} s` : `${m} min`;
}

function pounds(v: unknown): string | null {
  const n = Number(v);
  return Number.isFinite(n) && n > 0 ? `£${n < 0.1 ? n.toFixed(3) : n.toFixed(2)}` : null;
}

function rests(r: unknown): string | null {
  if (!Array.isArray(r) || r.length !== 2) return null;
  const [a, b] = r.map(Number);
  if (b >= 120) return `${Math.round(a / 60 * 2) / 2}–${Math.round(b / 60 * 2) / 2} min`;
  return `${a}–${b} s`;
}

function movementSize(m: unknown): string | null {
  const n = Number(m);
  if (!Number.isFinite(n)) return null;
  return n < 4 ? 'Slight' : n < 8 ? 'Moderate' : 'Lively';
}

const PACE: Record<string, string> = { slow: 'Slow', gentle: 'Gentle', natural: 'Natural speed' };

function Rows({ rows }: { rows: Array<[string, React.ReactNode | null | undefined]> }) {
  const shown = rows.filter(([, v]) => v !== null && v !== undefined && v !== '');
  if (!shown.length) return null;
  return (
    <dl className="grid grid-cols-[max-content_1fr] gap-x-4 gap-y-1.5">
      {shown.map(([k, v]) => (
        <React.Fragment key={k}>
          <dt className="text-neutral-400">{k}</dt>
          <dd className="text-white break-words min-w-0">{v}</dd>
        </React.Fragment>
      ))}
    </dl>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="space-y-2">
      <h4 className="text-[11px] font-semibold uppercase tracking-wide text-neutral-400">{title}</h4>
      {children}
    </section>
  );
}

function CopyButton({ text }: { text: string }) {
  const [done, setDone] = useState(false);
  return (
    <button
      type="button"
      className="inline-flex items-center gap-1 text-neutral-400 hover:text-white"
      onClick={() => {
        void navigator.clipboard?.writeText(text).then(() => {
          setDone(true);
          setTimeout(() => setDone(false), 1500);
        });
      }}
    >
      {done ? <Check className="h-3.5 w-3.5" /> : <Copy className="h-3.5 w-3.5" />}
      {done ? 'Copied' : 'Copy'}
    </button>
  );
}

/** A clip's play info (its `clip` block, carried in the MP4 too) and how it was made. */
function ClipRows({ meta }: { meta: Record<string, any> }) {
  const c = (meta.clip || {}) as Record<string, any>;
  const what = typeof meta.prompt === 'string' ? meta.prompt.replace(CAMERA, '').trim() : '';
  return (
    <div className="space-y-2">
      <Rows
        rows={[
          ['Pace', c.eased ? PACE[String(c.pace || 'slow')] || String(c.pace) : 'Older clip (slowed on the screen)'],
          ['Plays for', c.play_s ? duration(c.play_s) : null],
          ['Movement', movementSize(c.motion)],
          ['Rests', rests(c.rest_s) ? `${rests(c.rest_s)} between plays` : null],
          ['Made', when(meta.timestamp)],
          ['Took', duration(meta.generation_time_seconds)],
          ['Cost', pounds(meta.generation_cost_gbp)],
        ]}
      />
      {what && <p className="rounded-md bg-neutral-800/80 p-2 text-white whitespace-pre-wrap break-words">{what}</p>}
    </div>
  );
}

const SHOWN = new Set([
  'prompt', 'timestamp', 'workflow', 'refiner', 'seed', 'generation_time_seconds', 'generation_cost_gbp',
  'tags', 'tagging', 'clips', 'clip', 'still', 'favorite', 'reference_images', 'space',
]);

function techValue(v: unknown): React.ReactNode {
  if (typeof v === 'object') return <pre className="whitespace-pre-wrap break-all text-[11px] text-neutral-200">{JSON.stringify(v, null, 1)}</pre>;
  return String(v);
}

/** Make a (another) clip of the still: what moves (optional) and the pace (optional): the triage picks the rest. */
function MakeClip({ bucket, filename }: { bucket: string; filename: string }) {
  const [what, setWhat] = useState('');
  const [pace, setPace] = useState<'' | 'slow' | 'gentle' | 'natural'>('');
  const [busy, setBusy] = useState(false);
  return (
    <div className="space-y-2 rounded-md border border-neutral-800 p-3">
      <textarea
        value={what}
        onChange={(e) => setWhat(e.target.value)}
        rows={2}
        placeholder="What should move? Leave empty and it chooses."
        className="w-full resize-y rounded-md bg-neutral-800 p-2 text-white placeholder:text-neutral-500"
      />
      <div className="flex flex-wrap items-center gap-2">
        <select
          value={pace}
          onChange={(e) => setPace(e.target.value as typeof pace)}
          className="rounded-md bg-neutral-800 px-2 py-1.5 text-white"
          aria-label="Pace"
        >
          <option value="">Pace: it chooses</option>
          <option value="slow">Slow (about 30 s)</option>
          <option value="gentle">Gentle (about 12 s)</option>
          <option value="natural">Natural speed</option>
        </select>
        <Button
          size="sm"
          className="gap-1"
          disabled={busy}
          onClick={() => {
            setBusy(true);
            animateStill(bucket, filename, { prompt: what.trim() || undefined, pace: pace || undefined })
              .then(
                () => {
                  toast.success('Making a clip: it appears here in a few minutes');
                  setWhat('');
                },
                (err) => toast.error(String(err?.message || err)),
              )
              .finally(() => setBusy(false));
          }}
        >
          <Wand2 className="h-4 w-4" /> Make a clip
        </Button>
      </div>
    </div>
  );
}

interface Props {
  image: ImageItem;
  /** The bucket the picture is in (for its clips' actions). */
  bucket?: string | null;
  /** The screen or collection the picture is in, by name. */
  placeName?: string | null;
  onClose: () => void;
  /** Show the still's newest clip playing (the Loope's Moving view). */
  onWatch?: () => void;
}

export function ImageInfo({ image, bucket, placeName, onClose, onWatch }: Props) {
  const workflows = useWorkflows();
  const meta = (image.metadata || {}) as Record<string, any>;
  const isClip = image.mediaType === 'video';
  // this panel's own copy, so choosing or deleting a clip shows at once (the grid catches up when it next loads)
  const [clips, setClips] = useState<Array<{ filename: string; metadata?: Record<string, any> }>>(
    Array.isArray(meta.clips) ? meta.clips : [],
  );
  const workflow = workflows.find((w) => w.id === meta.workflow);
  const prompt = (typeof meta.prompt === 'string' && meta.prompt) || image.promptKey || '';
  const refs = (image.reference_images || meta.reference_images || []) as any[];
  const ai = meta.tagging?.ai as Record<string, any> | undefined;
  const [tech, setTech] = useState(false);

  const extra = Object.entries(meta)
    .filter(([k, v]) => !SHOWN.has(k) && v !== null && v !== undefined && v !== '' && !(Array.isArray(v) && !v.length))
    .sort(([a], [b]) => a.localeCompare(b));

  return (
    <div className="absolute inset-0 z-[60] mx-auto flex max-w-2xl flex-col overflow-hidden border-neutral-700 bg-neutral-900 text-sm shadow-xl sm:inset-x-2 sm:top-10 sm:bottom-16 sm:rounded-lg sm:border">
      <div className="flex items-center justify-between border-b border-neutral-700 px-4 py-3">
        <h3 className="font-medium">{isClip ? 'About this clip' : 'About this picture'}</h3>
        <Button variant="ghost" size="sm" className="h-8 w-8 p-0" onClick={onClose} aria-label="Close">
          <X className="h-4 w-4" />
        </Button>
      </div>

      <div className="flex-1 space-y-6 overflow-y-auto px-4 py-4">
        <Rows
          rows={[
            ['Made', when(meta.timestamp) || (image.createdAt ? new Date(image.createdAt).toLocaleString() : null)],
            ['In', placeName || image.bucketId || null],
            ['Moves', !isClip && clips.length ? `Yes: ${clips.length === 1 ? 'a clip' : `${clips.length} clips`}` : null],
          ]}
        />

        {prompt && (
          <Section title="Prompt">
            <p className="rounded-md bg-neutral-800/80 p-3 text-white whitespace-pre-wrap break-words">{isClip ? prompt.replace(CAMERA, '').trim() : prompt}</p>
            <CopyButton text={prompt} />
          </Section>
        )}

        <Section title="How it was made">
          <Rows
            rows={[
              ['Workflow', workflow ? workflow.name : meta.workflow || null],
              ['Refiner', meta.refiner || null],
              ['Took', duration(meta.generation_time_seconds)],
              ['Cost', pounds(meta.generation_cost_gbp)],
              ['Seed', meta.seed ?? null],
            ]}
          />
        </Section>

        {isClip && meta.clip && (
          <Section title="Movement">
            <ClipRows meta={{ ...meta, prompt: '' }} />
          </Section>
        )}

        {!isClip && (
          <Section title={clips.length > 1 ? `Movement (${clips.length} clips)` : 'Movement'}>
            {clips.length > 0 && onWatch && (
              <Button variant="secondary" size="sm" className="gap-1" onClick={onWatch}>
                <Film className="h-4 w-4" /> Watch it move
              </Button>
            )}
            {clips.length === 0 && <p className="text-neutral-400">This picture is still.</p>}
            <div className="space-y-4">
              {clips.map((c, i) => (
                <div key={c.filename} className={i ? 'border-t border-neutral-800 pt-3' : ''}>
                  {clips.length > 1 && (
                    <p className="mb-1.5 text-xs font-medium text-neutral-300">{i === 0 ? 'Plays on the screen' : `Clip ${i + 1}`}</p>
                  )}
                  <ClipRows meta={c.metadata || {}} />
                  {bucket && (
                    <div className="mt-2 flex flex-wrap gap-2">
                      {i > 0 && (
                        <Button
                          variant="secondary"
                          size="sm"
                          onClick={() =>
                            chooseClip(bucket, c.filename).then(
                              () => setClips([c, ...clips.filter((x) => x !== c)]),
                              (err) => toast.error(String(err?.message || err)),
                            )
                          }
                        >
                          Play this one
                        </Button>
                      )}
                      <Button
                        variant="ghost"
                        size="sm"
                        className="gap-1 text-neutral-300 hover:text-white"
                        onClick={() => {
                          if (!window.confirm('Delete this clip? The picture stays.')) return;
                          deleteFromBucket(bucket, c.filename).then(
                            () => setClips(clips.filter((x) => x !== c)),
                            (err) => toast.error(String(err?.message || err)),
                          );
                        }}
                      >
                        <Trash2 className="h-4 w-4" /> Delete clip
                      </Button>
                    </div>
                  )}
                </div>
              ))}
            </div>
            {bucket && <MakeClip bucket={bucket} filename={image.id} />}
          </Section>
        )}

        {refs.length > 0 && (
          <Section title={`Reference images (${refs.length})`}>
            <div className="flex flex-wrap gap-2">
              {refs.map((ref: any, idx: number) => {
                const bucketId = image.bucketId || '_recent';
                const full = ReferenceImageService.getReferenceImageUrls(bucketId, [ref])[0];
                const thumb = ReferenceImageService.getReferenceImageThumbnailUrls(bucketId, [ref])[0];
                return (
                  <button
                    key={ref.stored_path || idx}
                    type="button"
                    onClick={() => window.open(full, '_blank', 'noopener,noreferrer')}
                    title={ref.original_filename ? `Open ${ref.original_filename}` : 'Open'}
                  >
                    <img src={thumb} alt={`Reference ${idx + 1}`} className="h-16 w-16 rounded border border-neutral-600 object-cover hover:border-neutral-400" />
                  </button>
                );
              })}
            </div>
          </Section>
        )}

        {(ai || meta.tagging?.status === 'failed') && (
          <Section title="Tags">
            {ai ? (
              <div className="space-y-1.5">
                {ai.look && <p className="text-white">{String(ai.look)}</p>}
                <Rows
                  rows={[
                    ['Palette', ai.palette ? String(ai.palette) : null],
                    ['Suits', suitedGroups(ai.music).join(', ') || null],
                    ['Quality', typeof ai.quality === 'number' ? ai.quality.toFixed(2) : null],
                  ]}
                />
              </div>
            ) : (
              <p className="text-neutral-400">Not tagged{meta.tagging?.error ? `: ${String(meta.tagging.error)}` : ''}</p>
            )}
          </Section>
        )}

        <section>
          <button
            type="button"
            className="flex w-full items-center justify-between text-[11px] font-semibold uppercase tracking-wide text-neutral-400 hover:text-neutral-200"
            onClick={() => setTech(!tech)}
          >
            Technical details
            <ChevronDown className={`h-4 w-4 transition-transform ${tech ? 'rotate-180' : ''}`} />
          </button>
          {tech && (
            <div className="mt-2 space-y-1.5 text-xs">
              <Rows
                rows={[
                  ['File', image.id],
                  ['Collection', image.bucketId || null],
                  ...(meta.still ? [['Made from', meta.still] as [string, React.ReactNode]] : []),
                  ...clips.map((c, i) => [`Clip ${i + 1}`, c.filename] as [string, React.ReactNode]),
                  ...extra.map(([k, v]) => [k, techValue(v)] as [string, React.ReactNode]),
                ]}
              />
            </div>
          )}
        </section>
      </div>
    </div>
  );
}
