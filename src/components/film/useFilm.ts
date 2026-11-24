// The film being put together in the generator's Film mode, and the requests that make it (docs/FILM_MODE.md, stage 1).
// The form holds it (PromptForm): the strip draws the slots, the toolbar's button makes the film.
import { useEffect, useState } from 'react';
import { toast } from 'sonner';
import { nanoid } from '@/lib/utils';
import { generationApi, publishApi } from '@/shared/api';
import { remainingLabel } from '@/shared/jobs/words';
import {
  FILM_WORKFLOW, addPicture, addSlot, emptyFilm, estimateSeconds, filmAspect, filmOf, fromStored, placePictures, removeSlot,
  requestPictures, segments, setAspect, setLink, setPicture, slotLetter, toStored,
  type Film, type Link, type SlotPicture,
} from './film';

const STORE_KEY = 'generator.film';
/** A picture for the film, not for one slot ("Use as prompt" while Film is open): detail { url }. */
export const FILM_ADD_EVENT = 'film:add-picture';
/** A picture dropped on a slot from the app's own pictures (Index's drag end): detail { slotId, url }. */
export const FILM_SLOT_EVENT = 'film:set-slot';

/** The prompt helper: the film's own ("auto"), or none (the person's words as they are). */
export type FilmRefiner = 'auto' | 'none';

interface Kept {
  film: Film;
  params: Record<string, unknown>;
  refiner: FilmRefiner;
  /** where the finished film is published: screens' ids, comma separated, or 'none' */
  publish: string;
}

function load(): Kept {
  try {
    const stored = JSON.parse(localStorage.getItem(STORE_KEY) || 'null');
    return {
      film: fromStored(stored) ?? emptyFilm(),
      params: stored && typeof stored.params === 'object' && stored.params ? stored.params : {},
      refiner: stored?.refiner === 'none' ? 'none' : 'auto',
      publish: typeof stored?.publish === 'string' && stored.publish ? stored.publish : 'none',
    };
  } catch {
    return { film: emptyFilm(), params: {}, refiner: 'auto', publish: 'none' };
  }
}

const pictureOf = (picture: File | string): SlotPicture =>
  typeof picture === 'string'
    ? { kind: 'url', url: picture }
    : { kind: 'file', file: picture, preview: URL.createObjectURL(picture) };

/** This browser's record of its placeholders (the Generated tab brings them back from it after a reload). */
function rememberPlaceholders(ids: string[], batchId: string) {
  try {
    const stored = JSON.parse(localStorage.getItem('activePlaceholders') || '[]');
    localStorage.setItem('activePlaceholders',
      JSON.stringify([...stored, ...ids.map((id) => ({ id, batchId, timestamp: Date.now() }))]));
  } catch { /* storage off: a reload just won't bring them back */ }
}

function forgetPlaceholder(id: string) {
  try {
    const stored = JSON.parse(localStorage.getItem('activePlaceholders') || '[]');
    localStorage.setItem('activePlaceholders', JSON.stringify(stored.filter((p: { id: string }) => p.id !== id)));
  } catch { /* nothing to forget */ }
}

export function useFilm(generationOff = false) {
  const [kept] = useState(load);
  const [film, setFilm] = useState<Film>(kept.film);
  // Advanced: the workflow's own settings (shape, how another shape is fitted, smoothness, seed)
  const [params, setParams] = useState<Record<string, unknown>>(kept.params);
  const [refiner, setRefiner] = useState<FilmRefiner>(kept.refiner);
  const [publish, setPublish] = useState<string>(kept.publish);
  // the video asked for on each link, by the two slots it runs between
  const [asked, setAsked] = useState<Record<string, { placeholderId: string; batchId: string }>>({});

  useEffect(() => {
    try {
      localStorage.setItem(STORE_KEY, JSON.stringify({ ...toStored(film), params, refiner, publish }));
    } catch { /* storage off: the film just isn't kept */ }
  }, [film, params, refiner, publish]);

  useEffect(() => {
    const onAdd = (e: Event) => {
      const url = (e as CustomEvent<{ url?: string }>).detail?.url;
      if (url) setFilm((f) => addPicture(f, { kind: 'url', url }));
    };
    const onSlot = (e: Event) => {
      const { slotId, url } = (e as CustomEvent<{ slotId?: string; url?: string }>).detail || {};
      if (!slotId || !url) return;
      setFilm((f) => {
        const i = f.slots.findIndex((s) => s.id === slotId);
        return i < 0 ? f : setPicture(f, i, { kind: 'url', url });
      });
    };
    window.addEventListener(FILM_ADD_EVENT, onAdd);
    window.addEventListener(FILM_SLOT_EVENT, onSlot);
    return () => {
      window.removeEventListener(FILM_ADD_EVENT, onAdd);
      window.removeEventListener(FILM_SLOT_EVENT, onSlot);
    };
  }, []);

  const segs = segments(film);
  const linkKey = (i: number) => `${film.slots[i]?.id}>${film.slots[i + 1]?.id}`;
  const seconds = estimateSeconds(film);

  const make = () => {
    if (generationOff || segs.length === 0) return;
    const batchId = nanoid();
    // the film's shape: the one chosen in Advanced, else its first picture's
    const shape = typeof params.video_shape === 'string' && params.video_shape !== 'auto';
    const aspect = shape ? undefined : filmAspect(film);
    const jobs = segs.map((seg, i) => ({ seg, placeholderId: nanoid(), batchIndex: i }));

    rememberPlaceholders(jobs.map((j) => j.placeholderId), batchId);
    // the Generated tab shows the batch at once, a placeholder per video, in slot order
    window.dispatchEvent(new CustomEvent('recent:batch-placeholders', {
      detail: {
        batchId,
        count: jobs.length,
        prompt: segs.find((s) => s.text)?.text || 'Film',
        placeholders: jobs.map((j) => ({ placeholderId: j.placeholderId, batchIndex: j.batchIndex })),
        workflow: FILM_WORKFLOW,
        params: {},
        globalParams: { batch_size: 1 },
        collapsed: false,
      },
    }));
    setAsked((prev) => ({
      ...prev,
      ...Object.fromEntries(jobs.map((j) => [linkKey(j.seg.index), { placeholderId: j.placeholderId, batchId }])),
    }));

    // once every video has landed: one film (two or more joined into one clip, kept in Generated with them), then
    // published where chosen. A segment that failed leaves the others in Generated, unjoined.
    const screens = publish === 'none' ? [] : publish.split(',').filter(Boolean);
    const made = jobs.map(({ seg, placeholderId, batchIndex }) => {
      const { files, urls, order } = requestPictures(seg);
      return generationApi.generateImage({
        prompt: seg.text,
        workflow: FILM_WORKFLOW,
        params: {
          ...params,
          film_link: seg.index,        // where it goes in the film, kept with it (Start again rebuilds the film)
          video_seconds: seg.seconds,
          keep_with_picture: 'no',     // a segment belongs to the film, not to either of its pictures
          ...(aspect ? { video_aspect: aspect } : {}),
        },
        global_params: { batch_size: 1 },
        imageFiles: files,
        referenceUrls: urls,
        image_order: order,
        batch_id: batchId,
        placeholders: [{ batch_index: batchIndex, placeholder_id: placeholderId }],
        ...(refiner === 'none' ? { refiner: 'none' } : {}),
      })
        .then((result) => {
          forgetPlaceholder(placeholderId);
          return (result?.recent_files?.[0] as string | undefined) ?? null;
        })
        .catch((error: unknown) => {
          forgetPlaceholder(placeholderId);
          window.dispatchEvent(new CustomEvent('recent:placeholder-gone', { detail: { batchId, placeholderId } }));
          const from = slotLetter(seg.index);
          const to = slotLetter(seg.index + 1);
          toast.error(`${from} to ${to}: ${error instanceof Error && error.message ? error.message : "couldn't make the video"}`);
          return null;
        });
    });

    void Promise.all(made).then(async (files) => {
      if (files.some((f) => !f)) {
        if (jobs.length > 1) toast.error("Not every video was made, so the film wasn't joined");
        return;
      }
      let film = files[0] as string;
      if (files.length > 1) {
        try {
          const joined = await generationApi.joinFilm(batchId, files as string[], segs.map((s) => s.text));
          film = joined.file;
          window.dispatchEvent(new CustomEvent('recent:add', { detail: {
            batchId, files: [film], imagesWithMetadata: [{ fileName: film, metadata: joined.metadata }], placeholderIds: [],
          } }));
          toast.success('The film is in Generated');
        } catch (error) {
          toast.error(error instanceof Error ? error.message : "Couldn't join the film");
          return;
        }
      }
      for (const screen of screens) {
        try {
          await publishApi.publishBucketFile(screen, '_recent', film);
        } catch (error) {
          toast.error(error instanceof Error ? error.message : `Couldn't publish to ${screen}`);
        }
      }
    });
  };

  return {
    film,
    asked,
    linkKey,
    params,
    refiner,
    publish,
    setPublish,
    canMake: !generationOff && segs.length > 0,
    /** "About 2 minutes", or nothing while there is no video to make. */
    estimate: seconds > 0 ? remainingLabel(seconds) : undefined,
    isEmpty: !film.slots.some((s) => s.picture) && !film.links.some((l) => l.text),
    make,
    /** Pictures for the film, not for one slot (the toolbar's Upload): each goes in the next empty slot. */
    addPictures: (pictures: Array<File | string>) =>
      setFilm((f) => pictures.reduce((next, p) => addPicture(next, pictureOf(p)), f)),
    /** Pictures chosen for a slot: they fill it and the slots after it. */
    placeFiles: (at: number, files: File[]) => setFilm((f) => placePictures(f, at, files.map(pictureOf))),
    clearSlot: (at: number) => setFilm((f) => setPicture(f, at, null)),
    removeSlot: (at: number) => setFilm((f) => removeSlot(f, at)),
    addSlot: () => setFilm((f) => addSlot(f)),
    setLink: (i: number, change: Partial<Link>) => setFilm((f) => setLink(f, i, change)),
    setAspect: (id: string, aspect: number) =>
      setFilm((f) => (f.slots.find((s) => s.id === id)?.aspect === aspect ? f : setAspect(f, id, aspect))),
    setParam: (id: string, value: unknown) => setParams((p) => ({ ...p, [id]: value })),
    resetParams: () => setParams({}),
    setRefiner: (id: string) => setRefiner(id === 'none' ? 'none' : 'auto'),
    startAgain: () => {
      setFilm(emptyFilm());
      setAsked({});
    },
    /** A film made before, back in the slots (Start again: components/recent/recipe.ts). */
    load: (pictures: Array<string | null>, links: Array<Partial<Link>>, settings: Record<string, unknown>, helper: FilmRefiner) => {
      setFilm(filmOf(pictures, links));
      setParams(settings);
      setRefiner(helper);
      setAsked({});
    },
  };
}

export type FilmState = ReturnType<typeof useFilm>;
