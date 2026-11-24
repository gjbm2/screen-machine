// The generator's Film mode (docs/FILM_MODE.md, stage 1; owner 23 Nov): a row of slots A, B... A picture comes in by
// being dragged from Generated or a screen's pictures, dropped as a file, or chosen from the slot's menu (the same
// choices as the form's Upload). Between each pair: what happens, and how many seconds. The form's own button makes
// the film (useFilm.ts): one video per pair, from the first picture to the second.
import React, { useRef, useState } from 'react';
import { useDroppable } from '@dnd-kit/core';
import { ArrowDown, ArrowRight, Camera, ImagePlus, Palette, Plus, Upload, X } from 'lucide-react';
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
import { MAX_SECONDS, MIN_SECONDS, slotLetter, type Slot } from './film';
import type { FilmState } from './useFilm';

const imagesIn = (list: FileList | null | undefined): File[] =>
  Array.from(list || []).filter((f) => f.type.startsWith('image/') || /\.(jpe?g|png|webp|gif|avif|heic|heif)$/i.test(f.name));

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
}

const SlotBox: React.FC<SlotBoxProps> = ({ slot, index, canRemove, onChoose, onFiles, onClear, onRemove, onAspect }) => {
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
      className={`group relative aspect-video w-full shrink-0 overflow-hidden rounded-lg border-2 transition-colors md:w-44 ${
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
                <ImagePlus className="h-6 w-6" />
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

const FilmStrip: React.FC<{ film: FilmState }> = ({ film: state }) => {
  const { film, asked, linkKey } = state;
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

      <div
        className="flex flex-col gap-3 md:flex-row md:items-start md:overflow-x-auto md:pb-3 [scrollbar-color:hsl(var(--muted-foreground)/0.5)_transparent] [scrollbar-width:thin]"
        data-film-row
      >
        {film.slots.map((slot, i) => (
          <React.Fragment key={slot.id}>
            <SlotBox
              slot={slot}
              index={i}
              canRemove={film.slots.length > 2}
              onChoose={choose}
              onFiles={state.placeFiles}
              onClear={state.clearSlot}
              onRemove={state.removeSlot}
              onAspect={state.setAspect}
            />
            {i < film.links.length && (
              <div className="flex w-full shrink-0 items-stretch gap-2 md:w-60" data-film-link={`${slotLetter(i)}${slotLetter(i + 1)}`}>
                <div className="flex items-center text-muted-foreground">
                  <ArrowDown className="h-4 w-4 md:hidden" />
                  <ArrowRight className="hidden h-4 w-4 md:block" />
                </div>
                <div className="flex min-w-0 flex-1 flex-col gap-1.5">
                  <Textarea
                    value={film.links[i].text}
                    onChange={(e) => state.setLink(i, { text: e.target.value })}
                    placeholder="What happens?"
                    aria-label={`What happens from ${slotLetter(i)} to ${slotLetter(i + 1)}`}
                    rows={2}
                    className="min-h-[56px] resize-none text-sm"
                  />
                  <div className="flex items-center gap-2">
                    <Slider
                      value={[film.links[i].seconds]}
                      min={MIN_SECONDS}
                      max={MAX_SECONDS}
                      step={1}
                      onValueChange={([v]) => state.setLink(i, { seconds: v })}
                      aria-label={`Seconds from ${slotLetter(i)} to ${slotLetter(i + 1)}`}
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
                <div className="hidden items-center text-muted-foreground md:flex">
                  <ArrowRight className="h-4 w-4" />
                </div>
              </div>
            )}
          </React.Fragment>
        ))}
        <button
          type="button"
          onClick={state.addSlot}
          className="flex h-12 w-full shrink-0 items-center justify-center rounded-lg border-2 border-dashed border-border text-muted-foreground hover:text-foreground md:h-[99px] md:w-12"
          aria-label="Add a picture"
          data-film-add
        >
          <Plus className="h-5 w-5" />
        </button>
      </div>
    </div>
  );
};

export default FilmStrip;
