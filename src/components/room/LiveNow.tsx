// A live screen's panel, the one component wherever a live screen is unrolled (the home page's screen strip,
// ScreenLivePanel.tsx; the Room page's screen card, pages/Room/LiveControls.tsx): what was, what is and what is coming,
// scenes and effects, each to rate and comment on (owner, 27 Nov: "the unrolled screen view should show them as what
// was, what is, what is coming so that we can see what was before and score it, give comments"), This is good, the
// scene picker (a cue), and the look controls. Was: kept by this browser as it watches the screen change. Next: the
// renderer's up-next plan (useUpNext). Comments: routes/screen_notes.py.

import { useEffect, useMemo, useRef, useState } from "react";
import { Check, MessageSquare, RotateCw, Shuffle, ThumbsUp, Undo2 } from "lucide-react";
import { cn } from "@/lib/utils";
import { addScreenNote, screenNotes, type ScreenNote } from "@/shared/api/screenNotes";
import { storageGet, storageSet, type RoomApi } from "@/pages/Room/api";
import { LookControls, SceneButton, ScenePicker, type CueChoice } from "@/pages/Room/LivePanel";
import { fxChips, presetLabel, sceneInfo, sceneRatingKey, type LookScope, type SceneInfo } from "@/pages/Room/liveLib";
import { RatingBadge, RatingRow } from "@/pages/Room/Rating";
import { sceneSpecOf } from "./roomLink";
import type { Library, Look, SceneSpec } from "@/pages/Room/types";
import type { LiveLook } from "@/pages/Room/useLiveLook";
import { useUpNext, type UpNextState } from "@/pages/Room/useUpNext";

/** The scene shown before the one on glass now (owner, 27 Nov: "if you miss scoring the previous scene you can't see
 *  what it was anywhere"). */
const PREV_SCENE_KEY = (id: string) => `sm_home_prev_scene:${id}`;

/** A live screen, unrolled. ``cue`` changes the scene on glass (the page's own way: the home page through the look,
 *  the Room page through its screen's controls); ``changing`` while that is on its way. */
export function LiveNow({
  screenId,
  lv,
  lib,
  api,
  scope,
  nowKey,
  cue,
  changing = false,
}: {
  screenId: string;
  lv: LiveLook;
  lib: Library | null;
  api: RoomApi;
  /** Its room's live card, when the room has one (the room-wide controls live there). */
  scope?: LookScope | null;
  /** The scene asked for, while the renderer hasn't said what is on glass. */
  nowKey?: string | null;
  cue: (key: string, how: CueChoice) => void;
  changing?: boolean;
}) {
  const [picking, setPicking] = useState(false);
  const un = useUpNext({ api, sessionId: lv.session, subscribe: lv.subscribe });
  const nowInfo = sceneInfo(lib, api, lv.look?.current_scene || nowKey);
  return (
    <div className="space-y-3">
      <WasNowNext
        id={screenId}
        look={lv.look}
        plan={un}
        lib={lib}
        api={api}
        session={scope?.hosted ? null : lv.session}
        nowKey={nowKey}
        busy={!!lv.busy}
        changing={changing}
        onChange={() => setPicking(true)}
        onBack={(key) => cue(key, { at: "now" })}
      />
      {lv.available !== false && <LookControls screenId={screenId} lv={lv} lib={lib} api={api} scope={scope} />}
      <ScenePicker
        open={picking}
        onOpenChange={setPicking}
        lib={lib}
        api={api}
        mode="cue"
        currentKey={nowInfo?.key ?? null}
        onPick={(key, how) => cue(key, how)}
        testId={`scenepicker-${screenId}`}
      />
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
  /** The effect preset ("This is good" credits it); none for a scene's own effects. */
  preset?: string | null;
}
/** After the last touch inside, what shows stays put this long (a finger has no hover). */
const HOLD_MS = 6000;
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

/** The scenes and effects that were, are and come next on a screen or a room (``id``: its prefs and comments), from
 *  its look and its up-next plan; each to rate and comment on. */
export function WasNowNext({
  id,
  look,
  plan: un,
  lib,
  api,
  session,
  nowKey,
  now,
  busy = false,
  changing = false,
  onChange,
  onBack,
}: {
  id: string;
  look: Look | null | undefined;
  plan: UpNextState;
  lib: Library | null;
  api: RoomApi;
  /** The live session "This is good" goes to (a room's leader); none: no thumbs. */
  session?: string | null;
  /** The scene asked for, while the renderer hasn't said what is on glass. */
  nowKey?: string | null;
  /** What shows as Now in place of the look's scene (a room whose screens each show their own). */
  now?: SceneInfo | null;
  busy?: boolean;
  changing?: boolean;
  /** Change the scene on glass (the picker). */
  onChange: () => void;
  /** Back to the scene before. */
  onBack: (key: string) => void;
}) {
  const screenId = id;
  const nowInfo: SceneInfo | null = now !== undefined ? now : sceneInfo(lib, api, look?.current_scene || nowKey);
  // The scene before it: when what is on glass changes, the one it replaced (kept by this browser).
  const [prevKey, setPrevKey] = useState<string | null>(() => storageGet(PREV_SCENE_KEY(screenId)));
  const seenScene = useRef<string | null>(null);
  const curKey = lib ? (nowInfo?.key ?? null) : null;
  useEffect(() => {
    if (!curKey) return;
    if (seenScene.current && seenScene.current !== curKey) {
      storageSet(PREV_SCENE_KEY(screenId), seenScene.current);
      setPrevKey(seenScene.current);
    }
    seenScene.current = curKey;
  }, [screenId, curKey]);
  const prevInfo: SceneInfo | null = prevKey && prevKey !== curKey ? sceneInfo(lib, api, prevKey) : null;
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
  const nowFx: FxItem[] = useMemo(() => fxChips(look, lib).map((c) => ({ label: c.label, effect: c.effect, preset: c.kind === "scene" ? null : c.preset })), [look, lib]);
  const [prevFx, setPrevFx] = useState<FxItem[] | null>(() => readPrevFx(screenId));
  const seenFx = useRef<FxItem[] | null>(null);
  const sig = look ? fxSig(nowFx) : null;
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
    ? [{ label: presetLabel(nextFxPlan.effect, lib), effect: lib?.effects.find((x) => x.name === nextFxPlan.effect)?.effect ?? nextFxPlan.effect, preset: nextFxPlan.effect }]
    : [];
  const eta = (s: number | null | undefined) => (s == null ? null : Math.max(0, Math.round(s - since)));

  // Nothing moves under the pointer (owner, 27 Nov: "you advance the scene before you get to the score ... and end up
  // rating the wrong thing"): while the pointer is over this, and for HOLD_MS after a touch, it shows what it showed
  // when you came to it; ↻ shows when the screen has moved on since, and brings it up to date.
  const liveView = {
    nowInfo,
    prevInfo,
    nextScene,
    nextInfo,
    nowFx,
    prevFx,
    nextFx,
    nextFxId: nextFxPlan?.id ?? null,
    sceneEta: eta(nextScene?.eta_s?.[0]),
    fxEta: eta(nextFxPlan?.eta_s),
  };
  const [frozen, setFrozen] = useState<typeof liveView | null>(null);
  const inside = useRef(false);
  const touched = useRef(0);
  const hold = () => setFrozen((f) => f ?? liveView);
  const release = () => {
    touched.current = 0;
    setFrozen(null);
  };
  useEffect(() => {
    if (frozen && !inside.current && Date.now() - touched.current > HOLD_MS) setFrozen(null);
  }, [clock, frozen]);
  const v = frozen ?? liveView;
  const stale =
    !!frozen &&
    (frozen.nowInfo?.key !== liveView.nowInfo?.key || frozen.nextScene?.id !== liveView.nextScene?.id || fxSig(frozen.nowFx) !== fxSig(liveView.nowFx));
  const again = (planId: string) => {
    release();
    void un.edit({ op: "reroll", id: planId }).catch(() => undefined);
  };
  const sceneEta = frozen ? null : v.sceneEta;
  const fxEta = frozen ? null : v.fxEta;

  const lastNote = (key: string | null) => (key ? notes.find((n) => n.key === key) : undefined);
  const keep = async (n: Omit<ScreenNote, "at" | "song" | "screen">) => {
    const saved = await addScreenNote({ ...n, screen: screenId });
    setNotes((xs) => [saved, ...xs]);
  };

  const playNext = () => {
    const n = v.nextScene;
    if (!n) return;
    release();
    void un.edit({ op: "play", id: n.id }).catch(() => undefined);
  };

  // One card per moment, its scene and its effects together: Was and Next quiet, Now the one that stands out.
  const bucket = (when: When, info: SceneInfo | null, fx: FxItem[], caption: string, onClick: () => void) => {
    const isNow = when === "now";
    const isNext = when === "next";
    const rate = info?.key ? sceneRatingKey(lib, info.key) : null;
    const eta = isNext ? (sceneEta ?? fxEta) : null;
    const sel = fxSel?.when === when ? fxSel : null;
    const selKey = sel ? `effect/${sel.item.effect}` : null;
    return (
      <section
        key={when}
        data-testid={`${when}-${screenId}`}
        className={cn(
          "space-y-1.5 rounded-xl border p-2",
          isNow ? "border-teal-500 bg-white shadow-sm ring-1 ring-teal-500/30" : "border-stone-200 bg-stone-50/70",
        )}
      >
        <div className="flex min-h-6 items-center gap-2 px-0.5">
          <span
            className={cn(
              "text-xs font-semibold uppercase tracking-wider",
              isNow ? "text-teal-700" : "text-stone-500",
            )}
          >
            {WHEN_WORDS[when]}
          </span>
          {eta ? <span className="text-xs tabular-nums text-stone-500">in {eta} s</span> : null}
        </div>
        {info && (
          <div className={cn(!isNow && "opacity-90")}>
            <SceneButton info={info} caption={caption} onClick={onClick} disabled={!lib || busy} testId={isNow ? `scene-${screenId}` : undefined} />
          </div>
        )}
        {info && !isNext && (
          <div className="flex items-center gap-1">
            <RatingRow bare api={api} ratingKey={rate} what="scene" className="min-w-0" testId={isNow ? `rate-${screenId}` : undefined} />
            {session && info.key && (
              <GoodButton
                key={`${info.key}|${fxSig(fx)}`}
                api={api}
                session={session}
                scene={sceneSpecOf(info.key)}
                effects={fx.map((f) => f.preset).filter((x): x is string => !!x)}
              />
            )}
            {rate && <CommentButton onKeep={(words) => keep({ kind: "scene", key: rate, label: info.label, when, words })} />}
          </div>
        )}
        {info && !isNext && <NoteLine note={lastNote(rate)} />}
        {isNext && v.nextScene && <PickAgain onClick={() => again(v.nextScene!.id)} testId={`again-scene-${screenId}`} />}
        {fx.length > 0 && (
          <div className="flex flex-wrap items-center gap-1.5 px-0.5">
            <span className="text-xs text-stone-500">Effects</span>
            {fx.map((it) => {
              if (isNext)
                return (
                  <span key={`${it.effect}:${it.label}`} className="inline-flex h-8 items-center whitespace-nowrap rounded-full border border-dashed border-stone-300 bg-white px-3 text-sm">
                    {shortLabel(it.label)}
                  </span>
                );
              const on = !!sel && sel.item.effect === it.effect && sel.item.label === it.label;
              return (
                <button
                  key={`${it.effect}:${it.label}`}
                  type="button"
                  aria-pressed={on}
                  onClick={() => setFxSel(on ? null : { when, item: it })}
                  className={cn(
                    "inline-flex h-8 items-center gap-1.5 whitespace-nowrap rounded-full border px-3 text-sm",
                    on ? "border-stone-800 bg-stone-800 text-white" : "border-stone-300 bg-white hover:bg-stone-50",
                  )}
                >
                  {shortLabel(it.label)}
                  <RatingBadge api={api} ratingKey={`effect/${it.effect}`} />
                </button>
              );
            })}
            {isNext && v.nextFxId && <PickAgain icon onClick={() => again(v.nextFxId!)} testId={`again-fx-${screenId}`} />}
          </div>
        )}
        {sel && selKey && (
          <div className="space-y-0.5 rounded-lg bg-stone-100/80 px-2 py-1">
            <div className="flex items-center gap-1">
              <span className="min-w-0 shrink truncate text-sm font-medium">{shortLabel(sel.item.label)}</span>
              <RatingRow bare api={api} ratingKey={selKey} what="effect" className="min-w-0" />
              <CommentButton onKeep={(words) => keep({ kind: "effect", key: selKey, label: sel.item.label, when, words })} />
            </div>
            <NoteLine note={lastNote(selKey)} />
          </div>
        )}
      </section>
    );
  };

  return (
    <div
      className="relative space-y-2"
      data-testid={`live-now-${screenId}`}
      onPointerEnter={() => {
        inside.current = true;
        hold();
      }}
      onPointerLeave={() => {
        inside.current = false;
      }}
      onPointerDown={() => {
        touched.current = Date.now();
        hold();
      }}
    >
      {stale && (
        <button
          type="button"
          onClick={release}
          title="Up to date"
          aria-label="Up to date"
          className="absolute right-2 top-1.5 z-10 grid h-7 w-7 place-items-center rounded-full border border-teal-300 bg-teal-50 text-teal-800 shadow-sm"
        >
          <RotateCw className="h-3.5 w-3.5" aria-hidden />
        </button>
      )}
      {(v.prevInfo || (v.prevFx?.length ?? 0) > 0) &&
        bucket("was", v.prevInfo, v.prevFx ?? [], "Go back", () => v.prevInfo && (release(), onBack(v.prevInfo.key)))}
      {bucket("now", v.nowInfo, v.nowFx, changing ? "Changing…" : "Change", onChange)}
      {(v.nextInfo || v.nextFx.length > 0) && bucket("next", v.nextInfo, v.nextFx, "Play now", playNext)}
    </div>
  );
}

/** "This is good" for one row: credits that scene and those effects (not whatever is on screen by the time it lands);
 *  pressed, it stays lit with an undo beside it. */
function GoodButton({ api, session, scene, effects }: { api: RoomApi; session: string; scene: SceneSpec; effects: string[] }) {
  const [state, setState] = useState<"idle" | "busy" | "done" | "failed">("idle");
  const send = (undo: boolean) => {
    setState("busy");
    api
      .good(session, { scene, effects, ...(undo ? { undo: true } : {}) })
      .then(() => setState(undo ? "idle" : "done"))
      .catch(() => setState("failed"));
  };
  return (
    <span className="inline-flex shrink-0 items-center">
      <button
        type="button"
        aria-label="This is good"
        aria-pressed={state === "done"}
        title="This is good"
        disabled={state === "busy" || state === "done"}
        onClick={() => send(false)}
        className={cn(
          "grid h-11 w-9 place-items-center rounded-lg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
          state === "done" ? "text-teal-700" : state === "failed" ? "text-rose-600" : "text-stone-500 hover:text-stone-900",
        )}
      >
        <ThumbsUp className={cn("h-4 w-4", state === "done" && "fill-teal-200")} aria-hidden />
      </button>
      {state === "done" && (
        <button
          type="button"
          aria-label="Undo"
          title="Undo"
          onClick={() => send(true)}
          className="grid h-11 w-8 place-items-center rounded-lg text-stone-500 hover:text-stone-900 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          <Undo2 className="h-4 w-4" aria-hidden />
        </button>
      )}
    </span>
  );
}

function PickAgain({ onClick, testId, icon = false }: { onClick: () => void; testId?: string; icon?: boolean }) {
  return (
    <button
      type="button"
      onClick={onClick}
      data-testid={testId}
      aria-label="Pick again"
      title="Pick again"
      className="inline-flex h-9 shrink-0 items-center gap-1.5 rounded-lg px-2 text-sm text-stone-600 hover:bg-stone-100 hover:text-stone-900 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
    >
      <Shuffle className="h-4 w-4" aria-hidden />
      {!icon && "Pick again"}
    </button>
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
