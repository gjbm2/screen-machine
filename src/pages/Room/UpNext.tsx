// Up next on a live screen's card (owner, 16 Oct; the renderer's upnext.py): what plays next, the scenes (each with
// the way it comes in) and the effects, with rough times, and the owner's say over it at any time: play one now, move
// it up, keep it (it won't be swapped), let it pick another, choose a different one, change the way in, take it off,
// or add a scene or an effect. Next scene plays the top scene; Next effect brings in the first effect here that fits;
// Change scene plays at once and leaves the list alone. The renderer re-picks only its own entries; yours stay.
// On a phone it shows the next scene and the next effect, with "Show all" for the rest. No control here is ever
// dead: a change that can't reach the renderer says so in words, with Try again.

import { useEffect, useMemo, useState, type ReactNode } from "react";
import { ArrowUp, ChevronDown, Pin, Play, Plus, Replace, Shuffle, Trash2, type LucideIcon } from "lucide-react";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { cn } from "@/lib/utils";
import { explainError, RoomApi, RoomApiError } from "./api";
import { Notice, Spinner } from "./bits";
import { cap, sceneInfo, useEffectItems, useSceneItems, type SceneInfo } from "./liveLib";
import { Picker } from "./Picker";
import { THEME_CLASS } from "./theme";
import { Thumb } from "./Thumb";
import type { Library, SceneSpec, UpNextEdit, UpNextEffect, UpNextPlan, UpNextScene } from "./types";
import type { UpNextState } from "./useUpNext";
import { friendlyName } from "./words";

const DEFAULT_VIA = "__default";

type Entry = UpNextScene | UpNextEffect;
type Kind = "scene" | "effect";
type PickFor = { kind: Kind; swap?: Entry } | null;

const isScene = (e: Entry): e is UpNextScene => "scene" in e;

/** A picker tile's key as an Up next scene: a preset by name, a plain source as {source}. */
function specOf(key: string): SceneSpec {
  return key.startsWith("source:") ? { source: key.slice(7) } : key.replace(/^preset:/, "");
}

function sceneOf(lib: Library | null, api: RoomApi, e: UpNextScene): SceneInfo {
  const src = e.spec && typeof e.spec === "object" && "source" in e.spec;
  return sceneInfo(lib, api, src ? `source:${e.scene}` : e.scene) ?? { key: `preset:${e.scene}`, label: cap(e.scene), thumb: null };
}

function effectOf(lib: Library | null, api: RoomApi, e: UpNextEffect): SceneInfo {
  const fx = lib?.effects.find((x) => x.name === e.effect);
  return { key: e.effect, label: fx?.label ?? cap(e.effect), thumb: api.thumbUrl(fx?.thumb) };
}

function transitionLabel(lib: Library | null, name: string): string {
  return lib?.transitions.find((t) => t.name === name)?.label ?? cap(name);
}

/** "next", "in ~25 s", "in ~3 min", or "on Next only" (it isn't timed: scene picking or effect picking is off). */
function whenWords(eta: number | [number, number] | null, at: number, now: number, first: boolean): string {
  if (eta === null || eta === undefined) return "on Next only";
  const gone = Math.max(0, now - at) / 1000;
  const s = (Array.isArray(eta) ? (eta[0] + eta[1]) / 2 : eta) - gone;
  if (s < 8) return first ? "next" : "soon";
  if (s < 55) return `in ~${Math.max(10, Math.round(s / 5) * 5)} s`;
  return `in ~${Math.max(1, Math.round(s / 60))} min`;
}

/** Why a change didn't go through, in words; `retry` when it never reached the renderer (the same change can go again). */
function failWords(e: unknown, what: string): { msg: string; retry: boolean } {
  const say = (why: string, retry = false) => ({ msg: `${cap(what)} didn't go through: ${why}.`, retry });
  if (!(e instanceof RoomApiError)) return say(explainError(e));
  if (e.status === 0) return say("the room isn't answering", true);
  if ([502, 503, 504].includes(e.status)) return say("the screen's renderer isn't answering", true);
  if (e.code === "unknown_preset") {
    const thing = /transition/i.test(e.detail) ? "transition" : /effect/i.test(e.detail) ? "effect" : "scene";
    return say(`that ${thing} isn't in the library any more`);
  }
  if (/nothing up next has id/i.test(e.detail)) return say("that one has already played or gone; this is the list as it is now");
  if (e.code === "auto_unavailable") return say("this live screen can't plan ahead");
  if (e.code === "bad_state") return say("the screen is changing over; try again in a moment", true);
  if (e.code === "not_live" || e.status === 404) return say("the screen isn't live any more");
  if (e.status === 400) return say("the renderer turned it down");
  if (e.status === 409) return say("the screen can't do that right now");
  return say(explainError(e));
}

/** The plan with one entry changed in place (an optimistic paint). */
function mapEntry(p: UpNextPlan, id: string, f: (e: Entry) => Entry | null): UpNextPlan {
  const go = <T extends Entry>(xs: T[]) => xs.map((x) => (x.id === id ? (f(x) as T | null) : x)).filter((x): x is T => x !== null);
  return { ...p, scenes: go(p.scenes), effects: go(p.effects) };
}

function moveUp(p: UpNextPlan, id: string): UpNextPlan {
  const up = <T extends Entry>(xs: T[]) => {
    const i = xs.findIndex((x) => x.id === id);
    if (i <= 0) return xs;
    const out = [...xs];
    [out[i - 1], out[i]] = [out[i], out[i - 1]];
    return out;
  };
  return { ...p, scenes: up(p.scenes), effects: up(p.effects) };
}

export function UpNext({
  screenId,
  un,
  lib,
  api,
  autoOn,
  fxAutoOn,
  sharedLine = true,
}: {
  screenId: string;
  un: UpNextState;
  lib: Library | null;
  api: RoomApi;
  /** Scene picking is on (the director fills the list and times it). */
  autoOn: boolean;
  /** Effect picking is on ("Pick effects by itself"). */
  fxAutoOn: boolean;
  /** Its own "Shared with …" line (off where the card or the room's live card already says so). */
  sharedLine?: boolean;
}) {
  const [now, setNow] = useState(() => Date.now());
  const [openId, setOpenId] = useState<string | null>(null);
  const [all, setAll] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [fail, setFail] = useState<{ msg: string; again?: () => void } | null>(null);
  /** A change that went through, but not quite as asked ("X didn't fit; it brought in Y"). */
  const [note, setNote] = useState<string | null>(null);
  const [pick, setPick] = useState<PickFor>(null);
  const { items: sceneItems, chips: sceneChips, tags: sceneTags, facets: sceneFacets } = useSceneItems(lib, api);
  const { items: fxItems, chips: fxChips } = useEffectItems(lib, api);

  const read = un.read;
  const timed = !!read && [...read.plan.scenes, ...read.plan.effects].some((e) => e.eta_s !== null && e.eta_s !== undefined);
  // The times count down here between reads.
  useEffect(() => {
    if (!timed) return;
    const t = setInterval(() => {
      if (document.visibilityState === "visible") setNow(Date.now());
    }, 1000);
    return () => clearInterval(t);
  }, [timed]);
  // A refusal (the list shows how it is now) goes after a while; one with Try again stays until it's dealt with.
  useEffect(() => {
    if (!fail || fail.again) return;
    const t = setTimeout(() => setFail(null), 8000);
    return () => clearTimeout(t);
  }, [fail]);
  useEffect(() => {
    if (!note) return;
    const t = setTimeout(() => setNote(null), 8000);
    return () => clearTimeout(t);
  }, [note]);

  const send = async (what: string, key: string, body: UpNextEdit, optimistic?: (p: UpNextPlan) => UpNextPlan) => {
    setFail(null);
    setNote(null);
    setBusy(key);
    try {
      return await un.edit(body, optimistic);
    } catch (e) {
      const f = failWords(e, what);
      setFail({ msg: f.msg, again: f.retry ? () => void send(what, key, body, optimistic) : undefined });
      if (!f.retry) void un.refresh();
      return null;
    } finally {
      setBusy((b) => (b === key ? null : b));
    }
  };

  const pickedItems = pick?.kind === "effect" ? fxItems : sceneItems;
  const pickSelected = useMemo(() => {
    const s = pick?.swap;
    if (!s) return new Set<string>();
    return new Set([isScene(s) ? sceneOf(lib, api, s).key : s.effect]);
  }, [pick, lib, api]);

  if (!un.session || un.available === false) return null;

  if (!read) {
    return (
      <div className="border-t border-stone-200/80 pt-3" data-testid={`upnext-${screenId}`}>
        <div className="text-base font-medium">Up next</div>
        {un.readFailed ? (
          <Notice tone="warn" className="mt-1">
            Can't read what's up next right now.{" "}
            <button type="button" className="font-medium underline underline-offset-2" onClick={() => void un.refresh()}>
              Try again
            </button>
          </Notice>
        ) : (
          <p className="mt-1 flex items-center gap-2 text-[15px] text-muted-foreground">
            <Spinner /> Reading what's up next…
          </p>
        )}
      </div>
    );
  }

  const { scenes, effects, planned_by } = read.plan;
  const anyAuto = [...scenes, ...effects].some((e) => e.origin !== "you");
  const hidden = Math.max(0, scenes.length - 1) + Math.max(0, effects.length - 1);
  const shared = sharedLine && planned_by && planned_by !== screenId ? friendlyName(planned_by) : null;
  const phoneHide = !all && "hidden sm:block";

  const row = (e: Entry, i: number) => {
    const scene = isScene(e);
    const kind: Kind = scene ? "scene" : "effect";
    const info = scene ? sceneOf(lib, api, e) : effectOf(lib, api, e);
    const label = info.label;
    const open = openId === e.id;
    const tl = scene && e.transition ? transitionLabel(lib, e.transition) : null;
    const meta = [tl ? `via ${tl}` : null, whenWords(e.eta_s, read.at, now, i === 0), e.origin === "you" ? "yours" : null]
      .filter(Boolean)
      .join(" · ");
    const k = (op: string) => `${op}-${e.id}`;
    return (
      <li
        key={e.id}
        data-testid={`upn-${kind}-${screenId}-${i}`}
        className={cn("rounded-xl border bg-white", e.origin === "you" ? "border-teal-200" : "border-stone-200", i > 0 && phoneHide)}
      >
        <div className="flex items-center gap-1 p-1">
          <button
            type="button"
            aria-expanded={open}
            onClick={() => setOpenId(open ? null : e.id)}
            className="flex min-h-11 min-w-0 flex-1 items-center gap-2.5 rounded-lg p-0.5 text-left hover:bg-stone-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            <Thumb src={info.thumb} className="aspect-video w-16 shrink-0 rounded-md" />
            <span className="min-w-0 flex-1">
              <span className="block truncate text-base font-medium leading-snug">{label}</span>
              <span className="line-clamp-2 text-[15px] leading-snug text-muted-foreground">{meta}</span>
            </span>
            <ChevronDown
              aria-hidden
              className={cn("h-4 w-4 shrink-0 text-muted-foreground transition-transform motion-reduce:transition-none", open && "rotate-180")}
            />
          </button>
          <button
            type="button"
            aria-label={`Play ${label} now`}
            title="Play now"
            data-testid={`upn-play-${screenId}-${kind}-${i}`}
            onClick={() => void send(`playing ${label}`, k("play"), { op: "play", id: e.id })}
            className="grid h-11 w-11 shrink-0 place-items-center rounded-xl border border-teal-200 bg-teal-50 text-teal-800 hover:bg-teal-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            {busy === k("play") ? <Spinner /> : <Play className="h-4 w-4" aria-hidden />}
          </button>
        </div>
        {open && (
          <div className="space-y-2 border-t border-stone-200/80 p-2">
            {e.why_words && <p className="px-0.5 text-[15px] leading-snug text-stone-700">{e.why_words}</p>}
            {scene && (
              <div className="flex items-center gap-2">
                <Label htmlFor={`${screenId}-upn-${e.id}-via`} className="shrink-0 text-[15px] font-normal text-muted-foreground">
                  Comes in by
                </Label>
                <Select
                  value={e.transition_origin === "you" && e.transition ? e.transition : DEFAULT_VIA}
                  onValueChange={(v) => {
                    const t = v === DEFAULT_VIA ? null : v;
                    void send(`changing how ${label} comes in`, k("via"), { op: "set", id: e.id, transition: t }, (p) =>
                      mapEntry(p, e.id, (x) => ({ ...x, transition: t, transition_origin: t ? "you" : null, origin: "you" })),
                    );
                  }}
                >
                  <SelectTrigger id={`${screenId}-upn-${e.id}-via`} className="h-11 min-w-0 flex-1 rounded-xl bg-white text-base">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent className={cn(THEME_CLASS, "max-h-[50vh] rounded-xl")}>
                    <SelectItem value={DEFAULT_VIA} className="min-h-11 rounded-lg text-base">
                      {tl && e.transition_origin !== "you" ? `Picked by itself (${tl})` : "Picked by itself"}
                    </SelectItem>
                    {e.transition_origin === "you" && e.transition && !lib?.transitions.some((t) => t.name === e.transition) && (
                      <SelectItem value={e.transition} className="min-h-11 rounded-lg text-base">
                        {tl}
                      </SelectItem>
                    )}
                    {(lib?.transitions ?? []).map((t) => (
                      <SelectItem key={t.name} value={t.name} className="min-h-11 rounded-lg text-base">
                        {t.label}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                {busy === k("via") && <Spinner />}
              </div>
            )}
            <div className="flex flex-wrap gap-2">
              {i > 0 && (
                <Act icon={ArrowUp} busy={busy === k("move")} onClick={() => void send(`moving ${label} up`, k("move"), { op: "move", id: e.id, to: i - 1 }, (p) => moveUp(p, e.id))}>
                  Move up
                </Act>
              )}
              {e.origin !== "you" && (
                <Act
                  icon={Pin}
                  busy={busy === k("keep")}
                  title="It won't swap this one"
                  onClick={() =>
                    void send(`keeping ${label}`, k("keep"), { op: "keep", id: e.id }, (p) =>
                      mapEntry(p, e.id, (x) => ({
                        ...x,
                        origin: "you",
                        ...(isScene(x) && x.transition_origin === "auto" ? { transition_origin: "you" as const } : {}),
                      })),
                    )
                  }
                >
                  Keep
                </Act>
              )}
              <Act icon={Shuffle} busy={busy === k("reroll")} title="It picks a different one" onClick={() => void send(`picking another for ${label}`, k("reroll"), { op: "reroll", id: e.id })}>
                Pick another
              </Act>
              <Act icon={Replace} busy={busy === `swap-${e.id}`} title={`Choose a different ${kind} yourself`} onClick={() => setPick({ kind, swap: e })}>
                Choose…
              </Act>
              <Act
                icon={Trash2}
                busy={busy === k("remove")}
                onClick={() => void send(`taking ${label} off`, k("remove"), { op: "remove", id: e.id }, (p) => mapEntry(p, e.id, () => null))}
              >
                Remove
              </Act>
            </div>
            <p className="text-[15px] leading-snug text-muted-foreground">
              {e.origin === "you"
                ? "Yours: it stays until it plays or you remove it."
                : "Picked by itself: it may change if the feel changes. Keep holds it."}
              {i === 0 && (scene ? " Next scene plays this one." : " Next effect brings this in if it fits.")}
            </p>
          </div>
        )}
      </li>
    );
  };

  const group = (kind: Kind, list: Entry[], empty: string) => (
    <div>
      <div className="flex min-h-11 items-center gap-2">
        <span className="min-w-0 flex-1 text-[15px] text-muted-foreground">{kind === "scene" ? "Scenes" : "Effects"}</span>
        <button
          type="button"
          onClick={() => setPick({ kind })}
          data-testid={`upn-add-${kind}-${screenId}`}
          className="inline-flex h-11 items-center gap-1.5 rounded-full border border-dashed border-stone-400 bg-white px-4 text-base hover:bg-stone-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          {busy === `add-${kind}` ? <Spinner /> : <Plus className="h-4 w-4" aria-hidden />} {kind === "scene" ? "Scene" : "Effect"}
        </button>
      </div>
      {list.length ? (
        <ol className="space-y-1.5" aria-label={kind === "scene" ? "Scenes up next" : "Effects up next"}>
          {list.map((e, i) => row(e, i))}
        </ol>
      ) : (
        <p className="text-[15px] leading-snug text-muted-foreground">{empty}</p>
      )}
    </div>
  );

  const onPick = (key: string, itemLabel: string) => {
    const p = pick;
    setPick(null);
    if (!p) return;
    if (p.swap) {
      const was = isScene(p.swap) ? sceneOf(lib, api, p.swap).label : effectOf(lib, api, p.swap).label;
      const body: UpNextEdit = p.kind === "scene" ? { op: "set", id: p.swap.id, scene: specOf(key) } : { op: "set", id: p.swap.id, effect: key };
      void send(`swapping ${was} for ${itemLabel}`, `swap-${p.swap.id}`, body);
    } else {
      const body: UpNextEdit = p.kind === "scene" ? { op: "add", kind: "scene", scene: specOf(key), at: 0 } : { op: "add", kind: "effect", effect: key, at: 0 };
      void send(`adding ${itemLabel}`, `add-${p.kind}`, body);
    }
  };

  const swapLabel = pick?.swap ? (isScene(pick.swap) ? sceneOf(lib, api, pick.swap).label : effectOf(lib, api, pick.swap).label) : null;
  const pickTitle = pick?.swap ? `Instead of ${swapLabel}` : pick?.kind === "effect" ? "Add an effect up next" : "Add a scene up next";
  const pickWords = pick?.swap
    ? "It takes this one's place in the list and stays yours."
    : pick?.kind === "effect"
      ? fxAutoOn
        ? "It goes to the top of the effects up next and comes in at the next effect change, or on Next effect."
        : "It goes to the top of the effects up next and comes in on Next effect."
      : autoOn
        ? "It goes to the top of Up next and plays when this scene's time is up, or on Next scene."
        : "It goes to the top of Up next and plays on Next scene.";

  return (
    <div className="border-t border-stone-200/80 pt-3" data-testid={`upnext-${screenId}`}>
      <div className="flex min-h-11 items-center gap-2">
        <span className="min-w-0 flex-1 text-base font-medium">Up next</span>
        {anyAuto && (
          <button
            type="button"
            title="It picks its own again; yours stay"
            data-testid={`upn-repick-${screenId}`}
            onClick={async () => {
              if (await send("picking again", "repick", { op: "reroll", kind: "scene" })) await send("picking again", "repick", { op: "reroll", kind: "effect" });
            }}
            className="-my-1 inline-flex h-11 items-center gap-1.5 rounded-xl px-3 text-[15px] text-muted-foreground hover:bg-stone-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            {busy === "repick" ? <Spinner /> : <Shuffle className="h-4 w-4" aria-hidden />} Pick again
          </button>
        )}
      </div>
      {shared && <p className="text-[15px] leading-snug text-muted-foreground">Shared with {shared}: a change here changes both.</p>}
      {un.readFailed && <p className="text-[15px] leading-snug text-muted-foreground">Can't reach the renderer just now; this is the last list it sent.</p>}

      <div className="space-y-1">
        {group("scene", scenes, autoOn ? "Choosing the next ones…" : "Nothing queued. Add one, or turn on picking scenes by itself.")}
        {group("effect", effects, fxAutoOn ? "Choosing the next ones…" : "None queued. Add one, or turn on picking effects by itself.")}
      </div>

      {note && (
        <Notice tone="muted" className="mt-2">
          {note}
        </Notice>
      )}
      {fail && (
        <Notice tone="bad" className="mt-2">
          {fail.msg}{" "}
          {fail.again && (
            <button type="button" className="font-medium underline underline-offset-2" onClick={fail.again}>
              Try again
            </button>
          )}
        </Notice>
      )}

      <p className={cn("mt-2 text-[15px] leading-snug text-muted-foreground", phoneHide)}>
        Next scene plays the top scene; Next effect brings in the first effect here that fits. Change scene plays at once and
        leaves this list alone. It re-picks only its own; yours stay.
      </p>
      {hidden > 0 && (
        <button
          type="button"
          onClick={() => setAll((a) => !a)}
          aria-expanded={all}
          data-testid={`upn-all-${screenId}`}
          className="mt-1 inline-flex h-11 items-center gap-1.5 rounded-xl px-1 text-[15px] font-medium text-teal-800 hover:bg-stone-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring sm:hidden"
        >
          {all ? "Show less" : `Show all · ${hidden} more`}
          <ChevronDown aria-hidden className={cn("h-4 w-4 transition-transform motion-reduce:transition-none", all && "rotate-180")} />
        </button>
      )}

      <Picker
        open={!!pick}
        onOpenChange={(o) => !o && setPick(null)}
        title={pickTitle}
        description={pickWords}
        items={pickedItems}
        chips={pick?.kind === "effect" ? fxChips : sceneChips}
        facets={pick?.kind === "effect" ? undefined : sceneFacets}
        tags={pick?.kind === "effect" ? undefined : sceneTags}
        selected={pickSelected}
        onPick={(it) => onPick(it.key, it.label)}
        testId={`upn-picker-${screenId}`}
      />
    </div>
  );
}

function Act({
  icon: Icon,
  busy,
  title,
  onClick,
  children,
}: {
  icon: LucideIcon;
  busy?: boolean;
  title?: string;
  onClick: () => void;
  children: ReactNode;
}) {
  return (
    <button
      type="button"
      title={title}
      onClick={onClick}
      className="inline-flex h-11 items-center gap-1.5 rounded-full border border-stone-300 bg-white px-3.5 text-base hover:bg-stone-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
    >
      {busy ? <Spinner /> : <Icon className="h-4 w-4" aria-hidden />}
      {children}
    </button>
  );
}
