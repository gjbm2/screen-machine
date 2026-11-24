// The generator's Film mode (docs/FILM_MODE.md, stage 1; owner 23 Nov): a row of slots A, B... A picture comes in by
// being dragged from Generated or a screen's pictures, dropped as a file, or chosen from the slot's menu (the same
// choices as the form's Upload). Between each pair: what happens, and how many seconds. The form's own button makes
// the film (useFilm.ts): one video per pair, from the first picture to the second. Below the strip, suggestion rows
// (as under Image's prompt) add words to the link being written. On a phone the slots are small, each beside the link
// that leaves it, and a picture opens full size in the Loope.
import React, { useRef, useState } from 'react';
import { useDroppable } from '@dnd-kit/core';
import { ArrowRight, Camera, ImagePlus, Palette, Plus, Upload, X } from 'lucide-react';
import { Slider } from '@/components/ui/slider';
import { Textarea } from '@/components/ui/textarea';
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from '@/components/ui/dropdown-menu';
import { ArtworkPicker } from '@/components/artworks/ArtworkPicker';
import { DROP_ZONES } from '@/dnd/dropZones';
import { useIsMobile } from '@/hooks/use-mobile';
import { isActive } from '@/shared/api/jobs';
import { useJobs } from '@/shared/jobs/store';
import { useJobProgress } from '@/shared/jobs/estimate';
import { progressLine, STAGE_WORDS } from '@/shared/jobs/words';
import { findPlaceholderJob } from '@/components/jobs/JobPlaceholder';
import StylePromptsList from '@/components/prompt/StylePromptsList';
import examplePrompts from '@/data/example-prompts.json';
import { useLoopeView } from '@/contexts/LoopeViewContext';
import type { ImageItem } from '@/types/image-types';
import { MAX_SECONDS, MIN_SECONDS, slotLetter, type Slot } from './film';
import type { FilmState } from './useFilm';

const imagesIn = (list: FileList | null | undefined): File[] =>
  Array.from(list || []).filter((f) => f.type.startsWith('image/') || /\.(jpe?g|png|webp|gif|avif|heic|heif)$/i.test(f.name));

type Row = { label: string; prompts: { display: string; prompt: string }[] };
const NUDGES: Row[] = (examplePrompts as { contextPrompts?: Record<string, Row[]> }).contextPrompts?.film || [];

/** Where a slot's picture can come from (the form's Upload menu offers the same). */
type Source = 'files' | 'camera' | 'artworks';

interface SlotBoxProps {
  slot: Slot;
  index: number;
  canRemove: boolean;
  onChoose: (index: number, source: Source) => void;
  onFiles: (index: number, files: File[]) => void;
  onClear: (index: number) => void;
  onRemove: (index: number) => void;
  onAspect: (id: string, aspect: number) => void;
  onOpen: (index: number) => void;
  /** small, on a phone */
  compact?: boolean;
}

const SlotBox: React.FC<SlotBoxProps> = ({ slot, index, canRemove, onChoose, onFiles, onClear, onRemove, onAspect, onOpen, compact }) => {
  const { setNodeRef, isOver } = useDroppable({
    id: `${DROP_ZONES.FILM_SLOT_PREFIX}${slot.id}`,
    data: { type: 'film-slot', accepts: ['image'] },
  });
  const isMobile = useIsMobile();
  const [fileOver, setFileOver] = useState(false);
  const letter = slotLetter(index);
  const src = slot.picture ? (slot.picture.kind === 'url' ? slot.picture.url : slot.picture.preview) : null;
  const corner = 'absolute right-1 top-1 rounded-full bg-background/90 p-1 text-foreground shadow hover:bg-background';

  return (
    <div
      ref={setNodeRef}
      data-film-slot={letter}
      className={`group relative aspect-video shrink-0 overflow-hidden rounded-lg border-2 transition-colors ${compact ? 'w-28' : 'w-44'} ${
        isOver || fileOver ? 'border-primary bg-primary/10' : src ? 'border-border' : 'border-dashed border-border bg-muted/30'
      }`}
      onDragOver={(e) => { if (e.dataTransfer.types.includes('Files')) { e.preventDefault(); setFileOver(true); } }}
      onDragLeave={() => setFileOver(false)}
      onDrop={(e) => {
        const files = imagesIn(e.dataTransfer.files);
        setFileOver(false);
        if (!files.length) return;
        e.preventDefault();
        onFiles(index, files);
      }}
    >
      {src ? (
        <>
          <button type="button" className="h-full w-full" onClick={() => onOpen(index)} aria-label={`Open picture ${letter}`}>
            <img
              src={src}
              alt={`Picture ${letter}`}
              className="h-full w-full bg-black/5 object-contain"
              draggable={false}
              onLoad={(e) => {
                const img = e.currentTarget;
                if (img.naturalWidth && img.naturalHeight) onAspect(slot.id, img.naturalWidth / img.naturalHeight);
              }}
            />
          </button>
          <button type="button" onClick={() => onClear(index)} className={corner} aria-label={`Take the picture out of ${letter}`}>
            <X className="h-3.5 w-3.5" />
          </button>
        </>
      ) : (
        <>
          <DropdownMenu modal={false}>
            <DropdownMenuTrigger asChild>
              <button
                type="button"
                className="flex h-full w-full items-center justify-center text-muted-foreground hover:text-foreground"
                aria-label={`Choose a picture for ${letter}`}
              >
                <ImagePlus className={compact ? 'h-5 w-5' : 'h-6 w-6'} />
              </button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="start" className="w-56 p-1">
              <DropdownMenuItem onClick={() => onChoose(index, 'files')} className="px-3 py-1.5 text-sm">
                <Upload className="mr-2 h-4 w-4" />
                {isMobile ? 'From Gallery' : 'From this computer'}
              </DropdownMenuItem>
              {isMobile && (
                <DropdownMenuItem onClick={() => onChoose(index, 'camera')} className="px-3 py-1.5 text-sm">
                  <Camera className="mr-2 h-4 w-4" />
                  From Camera
                </DropdownMenuItem>
              )}
              <DropdownMenuItem onClick={() => onChoose(index, 'artworks')} className="px-3 py-1.5 text-sm">
                <Palette className="mr-2 h-4 w-4" />
                Famous artworks
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
          {canRemove && (
            <button type="button" onClick={() => onRemove(index)} className={corner} aria-label={`Remove ${letter}`}>
              <X className="h-3.5 w-3.5" />
            </button>
          )}
        </>
      )}
      <span className="pointer-events-none absolute left-1 top-1 rounded bg-background/90 px-1.5 text-xs font-semibold shadow">
        {letter}
      </span>
    </div>
  );
};

/** The video being made for a link: its stage and the time left, or why it failed. */
const SegmentStatus: React.FC<{ placeholderId: string; batchId: string }> = ({ placeholderId, batchId }) => {
  const { jobs, clockOffset } = useJobs();
  const job = findPlaceholderJob(jobs, { placeholderId, batchId }, clockOffset);
  const progress = useJobProgress(job, clockOffset, !!job && isActive(job));
  if (!job) return <span>{STAGE_WORDS.preparing}</span>;
  if (job.status === 'failed') return <span className="text-destructive">{job.error || STAGE_WORDS.failed}</span>;
  return <span>{progressLine(job, progress)}</span>;
};

interface LinkEditorProps {
  state: FilmState;
  index: number;
  active: boolean;
  onFocus: (index: number) => void;
}

/** What happens from one slot to the next, and how many seconds. */
const LinkEditor: React.FC<LinkEditorProps> = ({ state, index: i, active, onFocus }) => {
  const { film, asked, linkKey } = state;
  const from = slotLetter(i);
  const to = slotLetter(i + 1);
  return (
    <div className="flex min-w-0 flex-1 flex-col gap-1.5">
      <Textarea
        value={film.links[i].text}
        onChange={(e) => state.setLink(i, { text: e.target.value })}
        onFocus={() => onFocus(i)}
        placeholder="What happens?"
        aria-label={`What happens from ${from} to ${to}`}
        rows={2}
        className={`min-h-[56px] resize-none text-sm ${active ? 'ring-1 ring-primary/40' : ''}`}
      />
      <div className="flex items-center gap-2">
        <Slider
          value={[film.links[i].seconds]}
          min={MIN_SECONDS}
          max={MAX_SECONDS}
          step={1}
          onValueChange={([v]) => state.setLink(i, { seconds: v })}
          aria-label={`Seconds from ${from} to ${to}`}
        />
        <span className="w-8 shrink-0 text-right text-xs tabular-nums text-muted-foreground" data-film-seconds>
          {film.links[i].seconds} s
        </span>
      </div>
      {asked[linkKey(i)] && (
        <div className="text-xs text-muted-foreground" data-film-status>
          <SegmentStatus {...asked[linkKey(i)]} />
        </div>
      )}
    </div>
  );
};

const FilmStrip: React.FC<{ film: FilmState }> = ({ film: state }) => {
  const { film } = state;
  const isMobile = useIsMobile();
  const { open: openLoope } = useLoopeView();
  // the link the suggestion rows add to: the one last written in (the first, to begin with)
  const [activeLink, setActiveLink] = useState(0);
  const active = Math.min(activeLink, film.links.length - 1);
  const fileInput = useRef<HTMLInputElement>(null);
  const cameraInput = useRef<HTMLInputElement>(null);
  // the slot a picture is being chosen for (one file picker and one artwork picker serve every slot)
  const choosingFor = useRef(0);
  const [artOpen, setArtOpen] = useState(false);

  const choose = (index: number, source: Source) => {
    choosingFor.current = index;
    // after the menu has closed, so it doesn't take the focus back
    if (source === 'artworks') setTimeout(() => setArtOpen(true), 50);
    else setTimeout(() => (source === 'camera' ? cameraInput : fileInput).current?.click(), 50);
  };
  // a slot's picture, full size, among the film's other pictures
  const openSlot = (index: number) => {
    const filled = film.slots
      .map((s, i) => ({ s, i }))
      .filter(({ s }) => s.picture);
    const items: ImageItem[] = filled.map(({ s, i }) => {
      const url = s.picture!.kind === 'url' ? s.picture!.url : s.picture!.preview;
      return { id: `film-slot-${s.id}`, urlFull: url, urlThumb: url, promptKey: `Picture ${slotLetter(i)}`, seed: 0,
               createdAt: '', isFavourite: false, disableFavorite: true, mediaType: 'image' };
    });
    openLoope(items, Math.max(0, filled.findIndex(({ i }) => i === index)), 'Film');
  };
  const nudge = (words: string) => {
    const now = film.links[active]?.text.trim() || '';
    state.setLink(active, { text: now ? `${now.replace(/[,.]$/, '')}, ${words}` : words });
  };
  const slotBox = (slot: Slot, i: number) => (
    <SlotBox
      slot={slot}
      index={i}
      canRemove={film.slots.length > 2}
      onChoose={choose}
      onFiles={state.placeFiles}
      onClear={state.clearSlot}
      onRemove={state.removeSlot}
      onAspect={state.setAspect}
      onOpen={openSlot}
      compact={isMobile}
    />
  );
  const addButton = (
    <button
      type="button"
      onClick={state.addSlot}
      className={`flex shrink-0 items-center justify-center rounded-lg border-2 border-dashed border-border text-muted-foreground hover:text-foreground ${
        isMobile ? 'aspect-video w-28' : 'h-[99px] w-12'
      }`}
      aria-label="Add a picture"
      data-film-add
    >
      <Plus className="h-5 w-5" />
    </button>
  );

  const chosen = (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = imagesIn(e.target.files);
    e.target.value = '';
    if (files.length) state.placeFiles(choosingFor.current, files);
  };

  return (
    // as wide as the form and no wider, however many slots: the row scrolls sideways inside it (the page's scroll area
    // would otherwise grow to fit the row)
    <div data-film-strip style={{ width: 0, minWidth: '100%' }}>
      <input ref={fileInput} type="file" accept="image/*" multiple className="hidden" onChange={chosen} />
      <input ref={cameraInput} type="file" accept="image/*" capture="environment" className="hidden" onChange={chosen} />
      <ArtworkPicker open={artOpen} onOpenChange={setArtOpen} onPick={(files) => state.placeFiles(choosingFor.current, files)} />

      {isMobile ? (
        // a phone: one row per slot, the slot small beside the link that leaves it
        <div className="flex flex-col gap-2" data-film-row>
          {film.slots.map((slot, i) => (
            <div key={slot.id} className="flex items-start gap-2" data-film-link={i < film.links.length ? `${slotLetter(i)}${slotLetter(i + 1)}` : undefined}>
              {slotBox(slot, i)}
              {i < film.links.length && <LinkEditor state={state} index={i} active={active === i && film.links.length > 1} onFocus={setActiveLink} />}
            </div>
          ))}
          {addButton}
        </div>
      ) : (
        <div
          className="flex flex-row items-start gap-3 overflow-x-auto pb-3 [scrollbar-color:hsl(var(--muted-foreground)/0.5)_transparent] [scrollbar-width:thin]"
          data-film-row
        >
          {film.slots.map((slot, i) => (
            <React.Fragment key={slot.id}>
              {slotBox(slot, i)}
              {i < film.links.length && (
                <div className="flex w-60 shrink-0 items-stretch gap-2" data-film-link={`${slotLetter(i)}${slotLetter(i + 1)}`}>
                  <div className="flex items-center text-muted-foreground"><ArrowRight className="h-4 w-4" /></div>
                  <LinkEditor state={state} index={i} active={active === i && film.links.length > 1} onFocus={setActiveLink} />
                  <div className="flex items-center text-muted-foreground"><ArrowRight className="h-4 w-4" /></div>
                </div>
              )}
            </React.Fragment>
          ))}
          {addButton}
        </div>
      )}

      {/* words to add to the link being written, as under Image's prompt; on a phone each row is one line that
          scrolls sideways (owner, 24 Nov: wrapped rows were ugly) */}
      <div className="space-y-1.5 px-1 pt-3" data-film-nudges>
        {NUDGES.map((row) => isMobile ? (
          <div key={row.label} className="flex items-center gap-1 overflow-x-auto whitespace-nowrap no-scrollbar">
            <span className="shrink-0 text-xs text-muted-foreground">{row.label}:</span>
            {row.prompts.map((p) => (
              <button
                key={p.display}
                type="button"
                onClick={() => nudge(p.prompt)}
                className="shrink-0 rounded-full border border-amber-200 bg-amber-50 px-2 py-0.5 text-sm text-amber-700 hover:bg-amber-100"
              >
                {p.display}
              </button>
            ))}
          </div>
        ) : (
          <StylePromptsList
            key={row.label}
            label={row.label}
            styles={row.prompts}
            initialCount={7}
            onStyleClick={(p) => nudge(p.prompt)}
          />
        ))}
      </div>
    </div>
  );
};

export default FilmStrip;
