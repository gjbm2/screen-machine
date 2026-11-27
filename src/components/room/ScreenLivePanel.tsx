// A screen tab's live parts on the home page, for its screen strip (ScreenStrip.tsx): is this screen live, going
// live with the scene it last showed (else the room's default), back to art, the scene picker, and the Room page's own
// look controls (Auto/Next, energy, colours, effects), so the two pages never drift.
//
// Live shows only on the LAN (room_service answers) and only for screens room_service lets go live; elsewhere
// `shown` is false and the strip leaves the live parts out. It reads the room link through selectors (one value
// each), so a room status frame re-renders the strip only when something it shows changes.

import { useEffect, useMemo, useRef, useState } from "react";
import { Check, MessageSquare } from "lucide-react";
import { cn } from "@/lib/utils";
import { addScreenNote, screenNotes, type ScreenNote } from "@/shared/api/screenNotes";
import { explainError, storageGet, storageSet } from "@/pages/Room/api";
import { LookControls, SceneButton, ScenePicker } from "@/pages/Room/LivePanel";
import { fxChips, presetLabel, sceneInfo, sceneRatingKey, type SceneInfo } from "@/pages/Room/liveLib";
import { RatingBadge, RatingRow } from "@/pages/Room/Rating";
import { useLiveLook } from "@/pages/Room/useLiveLook";
import { useUpNext } from "@/pages/Room/useUpNext";
import {
  isLiveCapable,
  liveSceneKey,
  liveStateOf,
  roomApi,
  sceneSpecOf,
  subscribeStream,
  useRoomLibrary,
  useRoomLinkSelect,
  wantsLive,
  type LiveState,
} from "./roomLink";

/** The room's default live scene (the deployment's room/modes.json, the live modes). */
const DEFAULT_SCENE_KEY = "preset:fractal-zoom";
const LAST_SCENE_KEY = (id: string) => `sm_home_live_scene:${id}`;
/** The scene shown before the one on glass now (owner, 27 Nov: "if you miss scoring the previous scene you can't see
 *  what it was anywhere"): kept by this browser as it watches the screen change. */
const PREV_SCENE_KEY = (id: string) => `sm_home_prev_scene:${id}`;
const noop = () => {};

export function useScreenLive(screenId: string) {
  const onLan = useRoomLinkSelect((s) => s.onLan === true);
  const canControl = useRoomLinkSelect((s) => s.canControl);
  const connected = useRoomLinkSelect((s) => s.connected);
  const inRoom = useRoomLinkSelect((s) => !!s.status?.screens?.[screenId]);
  const capable = useRoomLinkSelect((s) => isLiveCapable(s.status?.screens?.[screenId]));
  const ls: LiveState = useRoomLinkSelect((s) => liveStateOf(s.status?.screens?.[screenId]));
  const want = useRoomLinkSelect((s) => wantsLive(s.status?.screens?.[screenId]));
  const sceneKey = useRoomLinkSelect((s) => liveSceneKey(s.status?.screens?.[screenId]));
  const session = useRoomLinkSelect((s) => s.status?.screens?.[screenId]?.observed?.session ?? null);
  const pageMissing = useRoomLinkSelect((s) => s.status?.screens?.[screenId]?.observed?.page_connected === false);
  const detail = useRoomLinkSelect((s) => s.status?.screens?.[screenId]?.observed?.detail ?? null);
  const lastError = useRoomLinkSelect((s) => s.status?.screens?.[screenId]?.last_error ?? null);

  const shown = onLan && canControl && capable;
  const api = roomApi();
  const lib = useRoomLibrary(shown);
  const liveNow = ls === "live";
  const lv = useLiveLook({
    api,
    screenId,
    sessionId: shown && liveNow ? (session as string | null) : null,
    enabled: !!lib,
    subscribe: subscribeStream,
    addLog: noop,
    reportError: noop,
  });
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [picker, setPicker] = useState<"choose" | "cue" | null>(null);

  // Remember the scene this screen shows, so Go live brings it back next time.
  useEffect(() => {
    if (sceneKey) storageSet(LAST_SCENE_KEY(screenId), sceneKey);
  }, [screenId, sceneKey]);

  const lastKey = storageGet(LAST_SCENE_KEY(screenId)) ?? DEFAULT_SCENE_KEY;
  const nextInfo: SceneInfo | null = sceneInfo(lib, api, lastKey);
  // The scene on glass, as the renderer says (Auto changes it by itself), else the one asked for.
  const nowInfo: SceneInfo | null = sceneInfo(lib, api, (liveNow && lv.look?.current_scene) || sceneKey);
  // The one before it: when what is on glass changes, the one it replaced.
  const [prevKey, setPrevKey] = useState<string | null>(() => storageGet(PREV_SCENE_KEY(screenId)));
  const seen = useRef<string | null>(null);
  const curKey = lib ? (nowInfo?.key ?? null) : null;
  useEffect(() => {
    if (!curKey) return;
    if (seen.current && seen.current !== curKey) {
      storageSet(PREV_SCENE_KEY(screenId), seen.current);
      setPrevKey(seen.current);
    }
    seen.current = curKey;
  }, [screenId, curKey]);
  const prevInfo: SceneInfo | null = prevKey && prevKey !== curKey ? sceneInfo(lib, api, prevKey) : null;

  const run = async (label: string, fn: () => Promise<unknown>) => {
    setBusy(label);
    setError(null);
    try {
      await fn();
    } catch (e) {
      setError(explainError(e));
    } finally {
      setBusy(null);
    }
  };
  const goLive = (key: string) => void run("Going live", () => api.setDesired(screenId, { live: { scene: sceneSpecOf(key) } }));
  const backToArt = () => void run("Going back to art", () => api.setDesired(screenId, "art"));

  return {
    screenId,
    onLan,
    connected,
    inRoom,
    shown,
    api,
    lib,
    lv,
    ls,
    liveNow,
    /** Live is asked for (going live, live, or failing while it retries). */
    want,
    sceneKey,
    lastKey,
    nextInfo,
    nowInfo,
    prevInfo,
    pageMissing,
    detail,
    lastError,
    busy,
    error,
    picker,
    setPicker,
    goLive,
    backToArt,
  };
}

export type ScreenLive = ReturnType<typeof useScreenLive>;

/** While live: what was, what is and what is coming, scenes and effects, each to rate and comment on (owner, 27 Nov),
 *  then the Room page's look controls. */
export function LiveLookPanel({ live }: { live: ScreenLive }) {
  const { screenId, lv, lib, api } = live;
  return (
    <div className="space-y-3">
      <WasNowNext live={live} />
      <LookControls screenId={screenId} lv={lv} lib={lib} api={api} />
    </div>
  );
}

type When = "was" | "now" | "next";
const WHEN_WORDS: Record<When, string> = { was: "Was", now: "Now", next: "Next" };
/** The effects on before the ones on now: kept by this browser as it watches them change. */
const PREV_FX_KEY = (id: string) => `sm_home_prev_fx:${id}`;
interface FxItem {
  label: string;
  /** The effect plug-in: its rating is the plug-in's. */
  effect: string;
}
/** "Rainbow isobars (dense per-line colours over the scene)" -> "Rainbow isobars". */
const shortLabel = (s: string) => s.replace(/\s*\(.*\)\s*$/, "") || s;
const fxSig = (xs: FxItem[]) => xs.map((x) => `${x.effect}:${x.label}`).sort().join("|");

function readPrevFx(id: string): FxItem[] | null {
  try {
    const v = JSON.parse(storageGet(PREV_FX_KEY(id)) ?? "null");
    return Array.isArray(v) ? v : null;
  } catch {
    return null;
  }
}

function WasNowNext({ live }: { live: ScreenLive }) {
  const { screenId, lv, lib, api, nowInfo, prevInfo } = live;
  const un = useUpNext({ api, sessionId: lv.session, subscribe: lv.subscribe });
  const [notes, setNotes] = useState<ScreenNote[]>([]);
  const [fxSel, setFxSel] = useState<{ when: When; item: FxItem } | null>(null);
  const [clock, setClock] = useState(() => Date.now());

  useEffect(() => {
    let alive = true;
    void screenNotes(screenId).then((n) => alive && setNotes(n));
    return () => {
      alive = false;
    };
  }, [screenId]);
  useEffect(() => {
    const t = setInterval(() => document.visibilityState === "visible" && setClock(Date.now()), 1000);
    return () => clearInterval(t);
  }, []);

  // Effects now, the set before them, and the next one.
  const nowFx: FxItem[] = useMemo(() => fxChips(lv.look, lib).map((c) => ({ label: c.label, effect: c.effect })), [lv.look, lib]);
  const [prevFx, setPrevFx] = useState<FxItem[] | null>(() => readPrevFx(screenId));
  const seenFx = useRef<FxItem[] | null>(null);
  const sig = lv.look ? fxSig(nowFx) : null;
  useEffect(() => {
    if (sig === null) return;
    if (seenFx.current && fxSig(seenFx.current) !== sig) {
      storageSet(PREV_FX_KEY(screenId), JSON.stringify(seenFx.current));
      setPrevFx(seenFx.current);
    }
    seenFx.current = nowFx;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [screenId, sig]);

  const plan = un.read?.plan;
  const since = un.read ? (clock - un.read.at) / 1000 : 0;
  const nextScene = plan?.scenes[0] ?? null;
  const nextInfo = nextScene ? sceneInfo(lib, api, nextScene.scene) : null;
  const nextFxPlan = plan?.effects[0] ?? null;
  const nextFx: FxItem[] = nextFxPlan
    ? [{ label: presetLabel(nextFxPlan.effect, lib), effect: lib?.effects.find((x) => x.name === nextFxPlan.effect)?.effect ?? nextFxPlan.effect }]
    : [];
  const eta = (s: number | null | undefined) => (s == null ? null : Math.max(0, Math.round(s - since)));
  const sceneEta = eta(nextScene?.eta_s?.[0]);
  const fxEta = eta(nextFxPlan?.eta_s);

  const lastNote = (key: string | null) => (key ? notes.find((n) => n.key === key) : undefined);
  const keep = async (n: Omit<ScreenNote, "at" | "song" | "screen">) => {
    const saved = await addScreenNote({ ...n, screen: screenId });
    setNotes((xs) => [saved, ...xs]);
  };

  const sceneTier = (when: When, info: SceneInfo | null, caption: string, onClick: () => void, rate: string | null) =>
    info && (
      <div className="grid grid-cols-[2.75rem_1fr] items-start gap-x-2" data-testid={`home-${when}-${screenId}`}>
        <span className="pt-4 text-sm font-medium text-muted-foreground">{WHEN_WORDS[when]}</span>
        <div className="min-w-0 space-y-0.5">
          <SceneButton info={info} caption={caption} onClick={onClick} disabled={!lib || !!lv.busy} testId={when === "now" ? `home-scene-${screenId}` : undefined} />
          <div className="flex items-center gap-1">
            <RatingRow bare api={api} ratingKey={rate} what="scene" className="min-w-0" testId={when === "now" ? `home-rate-${screenId}` : undefined} />
            {rate && <CommentButton onKeep={(words) => keep({ kind: "scene", key: rate, label: info.label, when, words })} />}
          </div>
          <NoteLine note={lastNote(rate)} />
        </div>
      </div>
    );

  const fxTier = (when: When, items: FxItem[], extra?: string | null) => (
    <div className="grid grid-cols-[2.75rem_1fr] items-center gap-x-2" key={when}>
      <span className="text-sm font-medium text-muted-foreground">{WHEN_WORDS[when]}</span>
      <div className="flex min-h-9 flex-wrap items-center gap-1.5">
        {items.length === 0 && <span className="text-sm text-muted-foreground">–</span>}
        {items.map((it) => {
          const on = fxSel?.when === when && fxSel.item.effect === it.effect && fxSel.item.label === it.label;
          return (
            <button
              key={`${it.effect}:${it.label}`}
              type="button"
              aria-pressed={on}
              onClick={() => setFxSel(on ? null : { when, item: it })}
              className={cn(
                "inline-flex h-9 items-center gap-1.5 whitespace-nowrap rounded-full border px-3 text-sm",
                on ? "border-stone-800 bg-stone-800 text-white" : "border-stone-300 bg-white hover:bg-stone-50",
              )}
            >
              {shortLabel(it.label)}
              <RatingBadge api={api} ratingKey={`effect/${it.effect}`} />
            </button>
          );
        })}
        {extra && <span className="text-sm tabular-nums text-muted-foreground">{extra}</span>}
      </div>
    </div>
  );
  const selKey = fxSel ? `effect/${fxSel.item.effect}` : null;

  return (
    <div className="space-y-3" data-testid={`home-timeline-${screenId}`}>
      <div className="space-y-2">
        {sceneTier("was", prevInfo, "Go back", () => prevInfo && void lv.cue(`back to ${prevInfo.label}`, { to: sceneSpecOf(prevInfo.key), at: "now" }), prevInfo ? sceneRatingKey(lib, prevInfo.key) : null)}
        {sceneTier("now", nowInfo, "Change", () => live.setPicker("cue"), sceneRatingKey(lib, lv.look?.current_scene))}
        {sceneTier(
          "next",
          nextInfo,
          sceneEta ? `in ${sceneEta} s · Play now` : "Play now",
          () => nextScene && void un.edit({ op: "play", id: nextScene.id }).catch(() => undefined),
          nextScene ? sceneRatingKey(lib, nextScene.scene) : null,
        )}
      </div>
      <div className="space-y-1 border-t border-stone-200/80 pt-2">
        {prevFx && fxTier("was", prevFx)}
        {fxTier("now", nowFx)}
        {(nextFx.length > 0 || plan) && fxTier("next", nextFx, fxEta && nextFx.length ? `in ${fxEta} s` : null)}
        {fxSel && selKey && (
          <div className="ml-[3.25rem] space-y-0.5">
            <div className="flex items-center gap-1">
              <span className="shrink-0 text-sm font-medium">{shortLabel(fxSel.item.label)}</span>
              <RatingRow bare api={api} ratingKey={selKey} what="effect" className="min-w-0" />
              <CommentButton onKeep={(words) => keep({ kind: "effect", key: selKey, label: fxSel.item.label, when: fxSel.when, words })} />
            </div>
            <NoteLine note={lastNote(selKey)} />
          </div>
        )}
      </div>
    </div>
  );
}

function NoteLine({ note }: { note: ScreenNote | undefined }) {
  if (!note) return null;
  return (
    <p className="truncate text-sm italic text-muted-foreground" title={note.words}>
      “{note.words}”
    </p>
  );
}

/** A comment: the icon opens a one-line box; Enter keeps it, Esc closes it. */
function CommentButton({ onKeep }: { onKeep: (words: string) => Promise<void> }) {
  const [open, setOpen] = useState(false);
  const [words, setWords] = useState("");
  const [state, setState] = useState<"idle" | "saving" | "kept" | "failed">("idle");
  const save = async () => {
    const w = words.trim();
    if (!w) return setOpen(false);
    setState("saving");
    try {
      await onKeep(w);
      setWords("");
      setOpen(false);
      setState("kept");
      setTimeout(() => setState("idle"), 1500);
    } catch {
      setState("failed");
    }
  };
  if (!open)
    return (
      <button
        type="button"
        aria-label="Comment"
        title="Comment"
        onClick={() => setOpen(true)}
        className="grid h-11 w-9 shrink-0 place-items-center rounded-lg text-stone-500 hover:text-stone-900 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
      >
        {state === "kept" ? <Check className="h-4 w-4 text-emerald-600" aria-hidden /> : <MessageSquare className="h-4 w-4" aria-hidden />}
      </button>
    );
  return (
    <input
      autoFocus
      value={words}
      onChange={(e) => setWords(e.target.value)}
      onKeyDown={(e) => {
        if (e.key === "Enter") void save();
        if (e.key === "Escape") setOpen(false);
      }}
      onBlur={() => !words.trim() && setOpen(false)}
      disabled={state === "saving"}
      placeholder="Comment"
      aria-invalid={state === "failed"}
      className={cn(
        "h-9 min-w-0 flex-[2] rounded-lg border bg-white px-2 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
        state === "failed" ? "border-rose-500" : "border-stone-300",
      )}
    />
  );
}

/** The scene picker: a scene to go live with (showing art), or a scene to change to (live, as a cue). */
export function LiveScenePicker({ live }: { live: ScreenLive }) {
  const { screenId, lv, lib, api, picker, setPicker, sceneKey, lastKey, goLive } = live;
  return (
    <ScenePicker
      open={picker !== null}
      onOpenChange={(o) => !o && setPicker(null)}
      lib={lib}
      api={api}
      currentKey={picker === "cue" ? sceneKey : lastKey}
      mode={picker ?? "choose"}
      onPick={(key, choice) => {
        if (picker === "cue") {
          const label = sceneInfo(lib, api, key)?.label ?? key;
          void lv.cue(`switch to ${label}`, {
            to: sceneSpecOf(key),
            ...(choice.transition ? { transition: choice.transition } : {}),
            at: choice.at ?? "now",
          });
        } else {
          goLive(key);
        }
      }}
      testId={`home-picker-${screenId}`}
    />
  );
}
