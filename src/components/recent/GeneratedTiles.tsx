// Generated as tiles (owner, 20 Nov: "reason through the way that containers are expressed and images grouped and
// managed on this class of page afresh -- esp. given how different the real estate needs are for portrait vs.
// landscape"). Two layouts beside the classic batch panels, chosen at the top of Generated:
//
// - Batches, for making pictures: each batch is a caption (its prompt, what it was made from, Another, its menu) over
//   its pictures, each in its own shape, packed into rows of even height. The newest batch is drawn larger, so what
//   was just made can be judged without opening anything. A picture still being made is a tile with its progress.
// - Grid, for looking back through what was made and choosing older pictures: a day's pictures packed small, with no
//   captions, many to a screen.
//
// One way in for looking closely: a tile opens the full-screen viewer (the Loope), which fits portrait and landscape
// alike and has the picture's Info, its clips and every action. Every tile can be dragged onto the prompt or a screen's
// tab, as before. On a phone the rows are lower and the menu button is always shown (there is no hover).
import React, { useEffect, useLayoutEffect, useReducer, useRef, useState } from 'react';
import { useDraggable } from '@dnd-kit/core';
import { ChevronDown, MoreVertical, Play, RefreshCw, Trash2, Wand2 } from 'lucide-react';
import {
  DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuSeparator, DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import { ImageMenuItems } from '@/components/common/ImageMenuItems';
import { RetryImg } from '@/components/common/ImageCard';
import { ExpandableContainer } from '@/components/common';
import { JobPlaceholder, placeholderKeyOf } from '@/components/jobs/JobPlaceholder';
import { openNextWithInfo, useLoopeView } from '@/contexts/LoopeViewContext';
import { ReferenceImageService } from '@/services/reference-image-service';
import { isVideoFile } from '@/utils/image-utils';
import type { ImageItem } from '@/types/image-types';
import { startAgain } from './startAgain';

export type GeneratedLayout = 'classic' | 'batches' | 'grid';

type Batch = { batchId: string; images: ImageItem[] };
type Section = { id: string; label: string; batches: Batch[]; imageCount: number };
type Destination = { id: string; name: string; headless: boolean };

interface Props {
  layout: 'batches' | 'grid';
  sections: Section[];
  isSectionCollapsed: (id: string) => boolean;
  onToggleSection: (id: string) => void;
  onDeleteSection: (id: string) => void;
  onDeleteImage: (img: ImageItem) => void;
  onDeleteBatch: (batchId: string) => void;
  onCopyTo: (img: ImageItem, destId: string) => void;
  onMoveTo: (img: ImageItem, destId: string) => void;
  onPublish: (img: ImageItem, destId: string) => void;
  onUseAsPrompt: (img: ImageItem) => void;
  onGenerateAgain: (batchId: string) => void;
  destinations: Destination[];
}

// Row heights (px) the packing aims for: the newest batch, other batches, the grid; lower on a phone
const HEIGHTS = {
  wide: { hero: 340, batch: 200, grid: 132 },
  narrow: { hero: 220, batch: 128, grid: 92 },
};
const GAP = 4;
const GUESS = 16 / 9;

const isPlaceholder = (img: ImageItem) => img.metadata?.placeholder === true;
const isVideo = (img: ImageItem) => img.mediaType === 'video' || isVideoFile(img.urlFull || img.id);
const thumbOf = (img: ImageItem) => img.urlThumb || img.urlFull;

// ---------------------------------------------------------------------------------------------------- shapes

/** Each picture's shape (width / height), from its thumbnail, kept for the page's life: the rows are packed from it. */
const ratios = new Map<string, number>();
const probing = new Set<string>();

function useShapes(images: ImageItem[]): (img: ImageItem) => number {
  const [, bump] = useReducer((n: number) => n + 1, 0);
  // one redraw for the shapes that arrive together, and none once the page has gone (not tied to the effect: the
  // list is a new array on every render, so its cleanup would have dropped every redraw)
  const mounted = useRef(true);
  const queued = useRef(false);
  useEffect(() => () => {
    mounted.current = false;
  }, []);
  useEffect(() => {
    const later = () => {
      if (queued.current) return;
      queued.current = true;
      requestAnimationFrame(() => {
        queued.current = false;
        if (mounted.current) bump();
      });
    };
    images.forEach((img) => {
      const url = thumbOf(img);
      if (!url || isPlaceholder(img) || ratios.has(url) || probing.has(url)) return;
      probing.add(url);
      const probe = new Image();
      probe.onload = () => {
        probing.delete(url);
        if (probe.naturalWidth && probe.naturalHeight) ratios.set(url, probe.naturalWidth / probe.naturalHeight);
        later();
      };
      probe.onerror = () => probing.delete(url);
      probe.src = url;
    });
  }, [images]);
  return (img) => {
    if (isPlaceholder(img)) return typeof img.metadata?.aspectRatio === 'number' ? img.metadata.aspectRatio : GUESS;
    return ratios.get(thumbOf(img)) ?? (typeof img.metadata?.aspectRatio === 'number' ? img.metadata.aspectRatio : GUESS);
  };
}

/** Pack pictures of the given shapes into rows `width` wide, each row as near `target` high as the shapes allow. The
 *  last row keeps the target height rather than stretching. */
export function packRows(shapes: number[], width: number, target: number): { h: number; items: number[] }[] {
  const rows: { h: number; items: number[] }[] = [];
  let row: number[] = [];
  let sum = 0;
  const fit = (s: number, n: number) => (width - GAP * (n - 1)) / s;
  shapes.forEach((r, i) => {
    row.push(i);
    sum += r;
    if (sum * target + GAP * (row.length - 1) < width) return;
    const withIt = fit(sum, row.length);
    const without = row.length > 1 ? fit(sum - r, row.length - 1) : Infinity;
    if (row.length > 1 && Math.abs(without - target) < Math.abs(withIt - target) && without < target * 1.5) {
      rows.push({ h: without, items: row.slice(0, -1) });
      row = [i];
      sum = r;
    } else {
      rows.push({ h: withIt, items: row });
      row = [];
      sum = 0;
    }
  });
  if (row.length) rows.push({ h: Math.min(target, fit(sum, row.length)), items: row });
  return rows;
}

function useWidth(): [React.RefObject<HTMLDivElement>, number] {
  const ref = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(0);
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    setWidth(el.clientWidth);
    const ro = new ResizeObserver(() => setWidth(el.clientWidth));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  return [ref, width];
}

// ---------------------------------------------------------------------------------------------------- a tile

interface TileProps {
  img: ImageItem;
  w: number;
  h: number;
  /** The menu button shows without hover (a phone, or a big tile). */
  menuAlways: boolean;
  onOpen: (img: ImageItem, opts?: { info?: boolean; play?: boolean }) => void;
  menu: Omit<Props, 'layout' | 'sections' | 'isSectionCollapsed' | 'onToggleSection' | 'onDeleteSection' | 'onDeleteBatch' | 'onGenerateAgain'>;
}

function Tile({ img, w, h, menuAlways, onOpen, menu }: TileProps) {
  // Dragged onto the prompt or a screen's tab, as everywhere else: the same data as SortableImageGrid's pictures
  const { attributes, listeners, setNodeRef, isDragging } = useDraggable({
    id: img.id,
    disabled: isPlaceholder(img),
    data: { raw_url: img.raw_url || img.urlFull, thumbnail_url: img.urlThumb, image: img, bucketId: img.bucketId || '_recent' },
  });
  const video = isVideo(img);
  const secs = Number(img.metadata?.clip?.play_s) || 0;
  const style: React.CSSProperties = { width: w, height: h, opacity: isDragging ? 0.35 : 1 };
  if (isPlaceholder(img)) {
    return (
      <div style={style} className="overflow-hidden rounded-md">
        {/* a big tile shows the job's progress and a Cancel button; a small one its ring and a × */}
        <JobPlaceholder placeholder={placeholderKeyOf(img)} aspectRatio={w / h} large={w >= 280 && h >= 180} />
      </div>
    );
  }
  return (
    <div
      ref={setNodeRef}
      {...attributes}
      {...listeners}
      style={style}
      title={img.promptKey || undefined}
      className="group relative cursor-pointer overflow-hidden rounded-md bg-muted"
      onClick={() => onOpen(img, { play: video })}
      onContextMenu={(e) => {
        if (window.matchMedia('(pointer: coarse)').matches) e.preventDefault();
      }}
    >
      <RetryImg src={thumbOf(img)} alt="" draggable={false} className="h-full w-full select-none object-cover" />
      {video && (
        <span className="pointer-events-none absolute bottom-1.5 left-1.5 inline-flex items-center gap-1 rounded-full bg-black/65 px-2 py-0.5 text-[11px] font-medium text-white">
          <Play className="h-3 w-3 fill-white" />
          {secs ? `${Math.round(secs)}s` : 'Video'}
        </span>
      )}
      {Array.isArray(img.metadata?.clips) && img.metadata.clips.length > 0 && (
        <span className="pointer-events-none absolute bottom-1.5 left-1.5 inline-flex items-center gap-1 rounded-full bg-black/65 px-2 py-0.5 text-[11px] font-medium text-white">
          <Play className="h-3 w-3 fill-white" />
          {img.metadata.clips.length > 1 ? `${img.metadata.clips.length} clips` : 'Clip'}
        </span>
      )}
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <button
            type="button"
            aria-label="Picture menu"
            className={`absolute right-1.5 top-1.5 flex h-7 w-7 items-center justify-center rounded-full bg-black/45 text-white hover:bg-black/70 ${menuAlways ? '' : 'opacity-0 group-hover:opacity-100 focus:opacity-100'}`}
            onClick={(e) => e.stopPropagation()}
            onPointerDown={(e) => e.stopPropagation()}
          >
            <MoreVertical className="h-4 w-4" />
          </button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end" onClick={(e) => e.stopPropagation()}>
          <ImageMenuItems
            image={img}
            bucket={img.bucketId || '_recent'}
            onInfo={(i) => onOpen(i, { info: true })}
            onUseAsPrompt={menu.onUseAsPrompt}
            onStartAgain={startAgain}
            onCopyTo={menu.onCopyTo}
            onMoveTo={menu.onMoveTo}
            onPublish={menu.onPublish}
            publishDestinations={menu.destinations}
            onDelete={menu.onDeleteImage}
          />
        </DropdownMenuContent>
      </DropdownMenu>
    </div>
  );
}

// ---------------------------------------------------------------------------------------------------- rows

function Rows({ images, target, menuAlways, onOpen, menu, shapeOf }: {
  images: ImageItem[]; target: number; menuAlways: boolean; shapeOf: (img: ImageItem) => number;
  onOpen: TileProps['onOpen']; menu: TileProps['menu'];
}) {
  const [ref, width] = useWidth();   // its own width: a section's padding makes it narrower than the page
  const rows = width ? packRows(images.map(shapeOf), width, target) : [];
  return (
    <div ref={ref} className="flex w-full flex-col" style={{ gap: GAP, minHeight: width ? undefined : target }}>
      {rows.map((row, r) => (
        <div key={r} className="flex" style={{ gap: GAP }}>
          {row.items.map((i) => (
            <Tile
              key={images[i].uniqueKey || images[i].id}
              img={images[i]}
              w={Math.floor(shapeOf(images[i]) * row.h)}
              h={Math.floor(row.h)}
              menuAlways={menuAlways}
              onOpen={onOpen}
              menu={menu}
            />
          ))}
        </div>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------------------------------- a batch

function BatchCaption({ batch, onGenerateAgain, onDeleteBatch, onOpenRefs }: {
  batch: Batch; onGenerateAgain: (id: string) => void; onDeleteBatch: (id: string) => void;
  onOpenRefs: (items: ImageItem[], i: number) => void;
}) {
  const [open, setOpen] = useState(false);
  const first = batch.images.find((img) => !isPlaceholder(img)) || batch.images[0];
  // the words the person wrote (the refined prompt the model was given is in the picture's Info)
  const prompt = first?.metadata?.original_prompt || first?.promptKey || first?.metadata?.prompt || '';
  const refs: any[] = first?.reference_images || first?.metadata?.reference_images || [];
  const bucket = first?.bucketId || '_recent';
  const made = batch.images.filter((img) => !isPlaceholder(img)).length;
  const making = batch.images.length - made;
  const newest = Math.max(0, ...batch.images.map((img) => new Date(img.createdAt).getTime() || 0));
  const refItems: ImageItem[] = refs.map((ref, i) => ({
    id: ref.stored_path || `ref-${i}`,
    urlFull: ReferenceImageService.getReferenceImageUrls(bucket, [ref])[0],
    urlThumb: ReferenceImageService.getReferenceImageThumbnailUrls(bucket, [ref])[0],
    promptKey: ref.original_filename || `Reference ${i + 1}`,
    seed: 0, createdAt: '', isFavourite: false, disableFavorite: true, mediaType: 'image' as const,
  }));
  return (
    <div className="mb-1.5 flex items-start gap-2 text-sm">
      {/* what it was made from: its reference pictures, which open in the viewer */}
      {refItems.length > 0 && (
        <div className="flex shrink-0 gap-1 pt-0.5">
          {refItems.slice(0, 3).map((r, i) => (
            <button key={r.id} type="button" title={`Made from ${r.promptKey}`} onClick={() => onOpenRefs(refItems, i)}
              className="h-7 w-7 overflow-hidden rounded border border-border">
              <img src={r.urlThumb || r.urlFull} alt="" className="h-full w-full object-cover" />
            </button>
          ))}
        </div>
      )}
      <button
        type="button"
        className={`min-w-0 flex-1 pt-1 text-left text-muted-foreground hover:text-foreground ${open ? 'whitespace-pre-wrap break-words' : 'truncate'}`}
        style={{ width: 0 }}
        title={open ? undefined : prompt}
        onClick={() => setOpen(!open)}
      >
        {prompt || 'No prompt'}
      </button>
      <span className="shrink-0 pt-1 text-xs tabular-nums text-muted-foreground">
        {making > 0 ? `${made} + ${making} making` : made}
        <span className="hidden sm:inline">
          {newest ? ` · ${new Date(newest).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}` : ''}
        </span>
      </span>
      <button
        type="button"
        onClick={() => onGenerateAgain(batch.batchId)}
        className="inline-flex shrink-0 items-center gap-1 rounded-full border px-2.5 py-1 text-xs hover:bg-muted"
        title="Make another with the same prompt and settings"
        aria-label="Another"
      >
        <RefreshCw className="h-3.5 w-3.5" /> <span className="hidden sm:inline">Another</span>
      </button>
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <button type="button" aria-label="Batch menu" className="shrink-0 rounded-full p-1.5 hover:bg-muted">
            <MoreVertical className="h-4 w-4" />
          </button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end">
          {first && !isPlaceholder(first) && (
            <DropdownMenuItem onClick={() => startAgain(first)}>
              <Wand2 className="mr-2 h-4 w-4" /> Start again from this prompt
            </DropdownMenuItem>
          )}
          <DropdownMenuItem onClick={() => setOpen(!open)}>
            <ChevronDown className="mr-2 h-4 w-4" /> {open ? 'Shorten the prompt' : 'Show the whole prompt'}
          </DropdownMenuItem>
          <DropdownMenuSeparator />
          <DropdownMenuItem
            className="text-destructive"
            onClick={() => {
              if (confirm(`Delete this batch (${made} picture${made === 1 ? '' : 's'})?`)) onDeleteBatch(batch.batchId);
            }}
          >
            <Trash2 className="mr-2 h-4 w-4" /> Delete batch
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
    </div>
  );
}

// ---------------------------------------------------------------------------------------------------- the page

export function GeneratedTiles(props: Props) {
  const { layout, sections, isSectionCollapsed, onToggleSection, onDeleteSection, onDeleteBatch, onGenerateAgain } = props;
  const { open: openLoope } = useLoopeView();
  const [ref, width] = useWidth();
  const narrow = width > 0 && width < 640;
  const heights = narrow ? HEIGHTS.narrow : HEIGHTS.wide;
  const coarse = typeof window !== 'undefined' && window.matchMedia?.('(pointer: coarse)').matches;
  const all = sections.flatMap((s) => s.batches.flatMap((b) => b.images));
  const shapeOf = useShapes(all);

  // Batches newest first (a batch goes by its newest picture, so Another on an old batch brings it up)
  const newestOf = (b: Batch) => Math.max(0, ...b.images.map((img) => new Date(img.createdAt).getTime() || 0));
  const ordered = sections.map((s) => ({ ...s, batches: [...s.batches].sort((a, b) => newestOf(b) - newestOf(a)) }));
  const heroId = ordered[0]?.batches[0]?.batchId;

  const menu: TileProps['menu'] = {
    onDeleteImage: props.onDeleteImage, onCopyTo: props.onCopyTo, onMoveTo: props.onMoveTo, onPublish: props.onPublish,
    onUseAsPrompt: props.onUseAsPrompt, destinations: props.destinations,
  };

  // The viewer: over the batch (Batches: its alternatives side by side) or the day (Grid: looking back through it)
  const openIn = (list: ImageItem[], title: string) => (img: ImageItem, opts?: { info?: boolean; play?: boolean }) => {
    const items = list.filter((i) => !isPlaceholder(i));
    const at = Math.max(0, items.findIndex((i) => i.id === img.id));
    if (opts?.info) openNextWithInfo();
    openLoope(items, at, title, { loop: true, allowFavorites: false, ...(opts?.play ? { playId: img.id } : {}) });
  };

  return (
    // width 0, at least the whole width: the page scrolls inside a ScrollArea whose content box grows to what its
    // content needs, and rows sized from the width they were given (and a long prompt) would otherwise hold it wide
    <div ref={ref} style={{ width: 0, minWidth: '100%' }}>
      {ordered.map((section) => (
        <ExpandableContainer
          key={`tiles-${section.id}`}
          id={section.id}
          label={`${section.label} (${section.imageCount})`}
          variant="section"
          iconPos="left"
          collapsed={isSectionCollapsed(section.id)}
          onToggle={onToggleSection}
          className="mb-2"
          showContextMenu={section.imageCount > 0}
          contextMenuItems={[{
            label: 'Delete all', onClick: () => onDeleteSection(section.id), icon: <Trash2 className="h-4 w-4" />,
            variant: 'destructive' as const,
          }]}
        >
          {layout === 'grid' ? (
            (() => {
              const images = section.batches.flatMap((b) => b.images);
              return (
                <Rows images={images} target={heights.grid} menuAlways={false} shapeOf={shapeOf}
                  onOpen={openIn(images, `Generated – ${section.label}`)} menu={menu} />
              );
            })()
          ) : (
            <div className="flex flex-col gap-5 pb-2">
              {section.batches.map((batch) => (
                <div key={batch.batchId}>
                  <BatchCaption
                    batch={batch}
                    onGenerateAgain={onGenerateAgain}
                    onDeleteBatch={onDeleteBatch}
                    onOpenRefs={(items, i) => openLoope(items, i, 'Reference pictures')}
                  />
                  <Rows
                    images={batch.images}
                    target={batch.batchId === heroId ? heights.hero : heights.batch}
                    menuAlways={!!coarse}
                    shapeOf={shapeOf}
                    onOpen={openIn(batch.images, batch.images[0]?.promptKey || 'Generated')}
                    menu={menu}
                  />
                </div>
              ))}
            </div>
          )}
        </ExpandableContainer>
      ))}
    </div>
  );
}
