// The screen strip's state and actions (ScreenStrip.tsx), one implementation for both pages that show it: the
// Generator's screen tabs (BucketGridView, which keeps its own bucket read for the grid and passes that in) and the
// Room page's screen cards (owner, 28 Oct: "under room when I view 'screen', I should be able to do the same sort of
// image control stuff as I can on Generator page").
//
// useScreenStripActions: what both pages share: the light mask (read when the screen changes, then set), the picture
//   before / after among the favourites (the scheduler's "display" action, POST /api/publish/<id>/display), the
//   bucket's re-index and JSON extract, and every screen's published picture in the loupe. `onChanged` hears about
//   each change so the page can re-read what it shows.
// useScreenStripState: those, plus what a page with no bucket view of its own needs: whether the screen has a bucket
//   (the publish destinations), its schedule (the shared all-schedules read, useScreenSchedules.ts, re-read after a
//   change), and the Upload and Maintenance dialogs' open state. The favourites count stays unknown (null): the only
//   read that has it is the whole bucket (a megabyte or more), so Previous / Next stay on and the server says when
//   there is nothing to step to.

import { useCallback, useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import { bucketsApi, lightApi, publishApi } from "@/shared/api";
import { jsonInit, readErrorBody, send } from "@/shared/http/client";
import { useLoopeView } from "@/contexts/LoopeViewContext";
import type { ImageItem } from "@/types/image-types";
import type { NextAction } from "@/components/image-display/SchedulerPanel";
import { refreshScreenSchedules, useScreenSchedule } from "./useScreenSchedules";

/** What just changed: a new picture on the screen, or the bucket re-indexed / its JSON extracted. */
export type StripChange = "picture" | "reindex" | "extract";

/** The loupe's opener; a no-op outside its provider (the app has one around every page). */
function useLoopeOpen(): ReturnType<typeof useLoopeView>["open"] {
  try {
    return useLoopeView().open;
  } catch {
    return () => console.warn("The loupe isn't available here");
  }
}

export function useScreenStripActions(screenId: string, onChanged?: (what: StripChange) => void) {
  const changed = useRef(onChanged);
  changed.current = onChanged;
  const openLoope = useLoopeOpen();
  const [maskOn, setMaskOn] = useState(true);
  const [stepping, setStepping] = useState(false);
  const [loadingAllPictures, setLoadingAllPictures] = useState(false);

  // The light mask's state: on until read (as before), and on when it can't be read.
  const readMask = useCallback(async () => {
    try {
      const result = await lightApi.getMaskState(screenId);
      setMaskOn(result.enabled);
    } catch (error) {
      console.error("Error fetching mask state:", error);
      setMaskOn(true);
    }
  }, [screenId]);
  useEffect(() => {
    void readMask();
  }, [readMask]);

  const setMask = useCallback(
    async (on: boolean) => {
      try {
        if (on) await lightApi.enableMask(screenId);
        else await lightApi.disableMask(screenId);
        setMaskOn(on);
      } catch (error) {
        console.error("Error toggling mask:", error);
        toast.error("Failed to toggle mask");
      }
    },
    [screenId],
  );

  // Previous / Next: the favourite before / after the one on the screen.
  const stepPicture = useCallback(
    async (mode: "Previous" | "Next") => {
      if (stepping) return;
      setStepping(true);
      try {
        const res = await send(`/publish/${encodeURIComponent(screenId)}/display`, jsonInit("POST", { mode }));
        if (!res.ok) {
          const body = await readErrorBody(res, `Couldn't show the ${mode.toLowerCase()} picture`);
          toast.error(body.error || `Couldn't show the ${mode.toLowerCase()} picture`);
          return;
        }
        changed.current?.("picture");
      } catch (error) {
        console.error(`Error showing the ${mode} picture:`, error);
        toast.error(`Couldn't show the ${mode.toLowerCase()} picture`);
      } finally {
        setStepping(false);
      }
    },
    [screenId, stepping],
  );

  const reindex = useCallback(async () => {
    try {
      const success = await bucketsApi.performBucketMaintenance(screenId, "reindex");
      if (success) {
        toast.success("Bucket reindexed successfully");
        changed.current?.("reindex");
      }
    } catch (error) {
      console.error("Error reindexing bucket:", error);
      toast.error("Failed to reindex bucket");
    }
  }, [screenId]);

  const extract = useCallback(async () => {
    try {
      const success = await bucketsApi.performBucketMaintenance(screenId, "extract");
      if (success) {
        toast.success("JSON extracted successfully");
        changed.current?.("extract");
      } else {
        toast.error("Failed to extract JSON");
      }
    } catch (error) {
      console.error("Error extracting JSON:", error);
      toast.error("Failed to extract JSON");
    }
  }, [screenId]);

  // Every screen's published picture in the loupe, starting at this one (headless destinations have none).
  const openAllPictures = useCallback(async () => {
    if (loadingAllPictures) return;
    setLoadingAllPictures(true);
    try {
      const all = await publishApi.getPublishDestinations();
      const pictures: ImageItem[] = [];
      for (const dest of all.filter((d) => !d.headless)) {
        try {
          const response = await publishApi.getPublishedContentForDestination(dest.id);
          if (!response.published) continue;
          pictures.push({
            id: `${dest.id}:${response.published}`,
            urlFull: response.raw_url || "",
            urlThumb: response.thumbnail_url || response.raw_url || "",
            promptKey: response.meta?.prompt || "",
            seed: response.meta?.seed || 0,
            createdAt: response.publishedAt || new Date().toISOString(),
            isFavourite: false,
            mediaType: (response.raw_url || "").toLowerCase().match(/\.mp4|\.webm/) ? "video" : "image",
            bucketId: dest.id,
            destinationName: dest.name || dest.id,
            metadata: response.meta || {},
            isPublished: true,
            disableFavorite: true,
          });
        } catch (error) {
          console.error(`Error fetching published content for ${dest.id}:`, error);
        }
      }
      if (!pictures.length) {
        toast.error("No published images found");
        return;
      }
      const at = Math.max(0, pictures.findIndex((img) => img.bucketId === screenId));
      const title = (img: ImageItem, idx: number, total: number) => `Currently published - ${img.destinationName} (${idx + 1}/${total})`;
      openLoope(pictures, at, title(pictures[at], at, pictures.length), { loop: true });
    } catch (error) {
      console.error("Error fetching published images:", error);
      toast.error("Failed to load published images");
    } finally {
      setLoadingAllPictures(false);
    }
  }, [loadingAllPictures, openLoope, screenId]);

  return { maskOn, setMask, readMask, stepping, stepPicture, reindex, extract, openAllPictures, loadingAllPictures };
}

export type ScreenStripActions = ReturnType<typeof useScreenStripActions>;

/** Everything the strip needs for one screen, read here (the Room page's screen cards). */
export function useScreenStripState(screenId: string) {
  const actions = useScreenStripActions(screenId);
  const schedule = useScreenSchedule(screenId);
  const [dest, setDest] = useState<{ hasBucket: boolean; headless: boolean } | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [uploadOpen, setUploadOpen] = useState(false);
  const [maintenanceOpen, setMaintenanceOpen] = useState(false);

  const readDest = useCallback(async () => {
    try {
      const all = await publishApi.getPublishDestinations();
      const d = all.find((x) => x.id === screenId);
      setDest({ hasBucket: d?.has_bucket !== false, headless: !!d?.headless });
    } catch (error) {
      console.error("Error fetching destinations:", error);
    }
  }, [screenId]);
  useEffect(() => {
    void readDest();
  }, [readDest]);

  const { readMask } = actions;
  const refresh = useCallback(async () => {
    setRefreshing(true);
    try {
      await Promise.all([readDest(), readMask(), refreshScreenSchedules()]);
    } finally {
      setRefreshing(false);
    }
  }, [readDest, readMask]);

  return {
    actions,
    hasBucket: dest?.hasBucket ?? false,
    headless: dest?.headless ?? false,
    scheduler: {
      isRunning: schedule?.is_running,
      isPaused: schedule?.is_paused,
      nextAction: (schedule?.next_action ?? null) as NextAction | null,
    },
    refreshScheduler: () => void refreshScreenSchedules(),
    refresh,
    refreshing,
    uploadOpen,
    setUploadOpen,
    maintenanceOpen,
    setMaintenanceOpen,
  };
}
