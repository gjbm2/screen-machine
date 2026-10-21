// The renderer's breadth on a live screen's card, in plain words (room_service live_api.py):
//   - SceneButton + ScenePicker: the scene on glass as a thumbnail; a picker to change it (or, on an
//     art screen, to choose what to go live with);
//   - PickingRows: Scenes and Effects, each picked by itself (the renderer's picking) or by you, with Next scene /
//     Next effect, "How many" effects and an energy choice. On a room's live card (RoomLive.tsx) these act on every
//     screen in the room; on a screen's own card only where the room has no live card (owner, 17 Oct: a control acts
//     where it sits);
//   - LookControls (inline on a screen's card): the rows above when they belong there, Up next (UpNext.tsx), Colours,
//     and the screen's effects: chips (a tap opens a sheet: Showing, Strength, Choose another, Take it off),
//     Change effect (a picker with "No effects" and "Add on top"), and one strength slider;
//   - LookSettings (under "More"): what picking picks from and how often, the feel (speed, movement, moods, music,
//     transitions), fade times, and the music it hears.
// The renderer's per-screen picking is never called "Auto" on the page: that word is the room-level Auto (it picks
// the mode). Nothing here is ever greyed out: a change that can't get through says so in words.

import { useEffect, useMemo, useState, type CSSProperties, type ReactNode } from "react";
import { Ban, Check, Plus, Shuffle, SkipForward, SlidersHorizontal, Sparkles } from "lucide-react";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Slider } from "@/components/ui/slider";
import { Switch } from "@/components/ui/switch";
import { cn } from "@/lib/utils";
import { RoomApi, storageGet, storageSet } from "./api";
import { Notice, SectionTitle, Spinner } from "./bits";
import {
  bothWords,
  cap,
  droppedWords,
  fxChips,
  fxLabel,
  fxRange,
  useEffectItems,
  useSceneItems,
  type FxChip,
  type LookScope,
  type SceneInfo,
} from "./liveLib";
import { ActionSheet } from "./overlays";
import { Chip, Picker } from "./Picker";
import { clearEffects, effectSlots, isPicked, pickEffect, type LiveMember } from "./roomFx";
import { THEME_CLASS } from "./theme";
import { Thumb } from "./Thumb";
import type { AutoBlock, Direction, Library, Look } from "./types";
import { UpNext } from "./UpNext";
import { mergeDirection, type LiveLook, type LookCtl } from "./useLiveLook";
import { useUpNext, type UpNextRead } from "./useUpNext";
import { friendlyName } from "./words";

const CUE_VIA_KEY = "sm_room_cue_via";
const CUE_BEAT_KEY = "sm_room_cue_beat";
const FX_ON_TOP_KEY = "sm_room_fx_add";
const DEFAULT_VIA = "__default";

const BUTTON =
  "inline-flex h-11 items-center gap-1.5 rounded-xl border border-stone-300 bg-white px-3.5 text-base hover:bg-stone-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring";
const ADD_BUTTON =
  "inline-flex h-11 items-center gap-1.5 rounded-full border border-dashed border-stone-400 bg-white px-4 text-base hover:bg-stone-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring";

export function SceneButton({
  info,
  caption,
  onClick,
  disabled,
  busy,
  testId,
}: {
  info: SceneInfo | null;
  caption: string;
  onClick: () => void;
  disabled?: boolean;
  busy?: boolean;
  testId?: string;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      data-testid={testId}
      className="flex min-h-[3.75rem] w-full min-w-0 items-center gap-3 rounded-xl border border-stone-300 bg-white p-1.5 pr-3 text-left hover:bg-stone-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50"
    >
      <Thumb src={info?.thumb} className="aspect-video w-[5.5rem] shrink-0 rounded-lg" />
      <span className="min-w-0 flex-1">
        <span className="block truncate text-base font-semibold leading-snug">{info?.label ?? "Choose a scene"}</span>
        <span className="flex items-center gap-1.5 truncate whitespace-nowrap text-[15px] leading-snug text-muted-foreground">
          {busy && <Spinner className="h-3.5 w-3.5" />}
          {caption}
        </span>
      </span>
    </button>
  );
}

export interface CueChoice {
  transition?: string;
  at?: "now" | "beat";
}

/** The scene picker. `cue` mode (a live screen) has the transition chooser and "wait for the beat". */
export function ScenePicker({
  open,
  onOpenChange,
  lib,
  api,
  currentKey,
  mode,
  onPick,
  testId,
  description,
}: {
  open: boolean;
  onOpenChange: (o: boolean) => void;
  lib: Library | null;
  api: RoomApi;
  currentKey: string | null;
  mode: "cue" | "choose";
  onPick: (key: string, choice: CueChoice) => void;
  testId?: string;
  /** Overrides the cue picker's line (a room's: "…on both screens"). */
  description?: string;
}) {
  const { items: all, chips, tags: sceneTags, facets } = useSceneItems(lib, api);
  // The scene on now comes first, so the picker opens on it.
  const items = useMemo(() => {
    const i = currentKey ? all.findIndex((x) => x.key === currentKey) : -1;
    return i > 0 ? [all[i], ...all.slice(0, i), ...all.slice(i + 1)] : all;
  }, [all, currentKey]);
  const [via, setVia] = useState(() => storageGet(CUE_VIA_KEY) ?? DEFAULT_VIA);
  const [beat, setBeat] = useState(() => storageGet(CUE_BEAT_KEY) === "1");
  const transitions = lib?.transitions ?? [];
  const viaKnown = via === DEFAULT_VIA || transitions.some((t) => t.name === via);
  const selected = useMemo(() => new Set(currentKey ? [currentKey] : []), [currentKey]);
  const footer =
    mode === "cue" ? (
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
        <div className="flex min-w-0 flex-1 basis-full items-center gap-2 sm:basis-auto">
          <Label htmlFor={`${testId}-via`} className="shrink-0 text-base font-normal">
            Transition
          </Label>
          <Select
            value={viaKnown ? via : DEFAULT_VIA}
            onValueChange={(v) => {
              setVia(v);
              storageSet(CUE_VIA_KEY, v);
            }}
          >
            <SelectTrigger id={`${testId}-via`} data-testid={`${testId}-via`} className="h-11 min-w-0 flex-1 rounded-xl bg-white text-base sm:max-w-xs">
              <SelectValue />
            </SelectTrigger>
            <SelectContent className={cn(THEME_CLASS, "max-h-[50vh] rounded-xl")}>
              <SelectItem value={DEFAULT_VIA} className="min-h-11 rounded-lg text-base">
                The room's choice
              </SelectItem>
              {transitions.map((t) => (
                <SelectItem key={t.name} value={t.name} className="min-h-11 rounded-lg text-base">
                  {t.label}
                  {typeof t.duration_s === "number" && t.name !== "cut" ? ` · ${t.duration_s} s` : ""}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <label className="flex min-h-11 cursor-pointer items-center gap-2.5 text-base">
          <Switch
            checked={beat}
            onCheckedChange={(c) => {
              setBeat(c);
              storageSet(CUE_BEAT_KEY, c ? "1" : "0");
            }}
          />
          On the beat
        </label>
      </div>
    ) : null;
  return (
    <Picker
      open={open}
      onOpenChange={onOpenChange}
      title={mode === "cue" ? "Change scene" : "Choose a scene"}
      description={mode === "cue" ? description ?? "Tap one to switch to it now." : "The screen goes live with the scene you choose."}
      items={items}
      chips={chips}
      facets={facets}
      tags={sceneTags}
      selected={selected}
      onPick={(it) => {
        onPick(it.key, mode === "cue" ? { transition: viaKnown && via !== DEFAULT_VIA ? via : undefined, at: beat ? "beat" : "now" } : {});
        onOpenChange(false);
      }}
      footer={footer}
      testId={testId}
    />
  );
}

// ── Picking: Scenes and Effects, side by side ─────────────────────────────────

const ENERGY: Array<{ label: string; energy: number; style: string | null }> = [
  { label: "Calm", energy: 0.2, style: "gentle" },
  { label: "Steady", energy: 0.5, style: null },
  { label: "Lively", energy: 0.75, style: "lively" },
  { label: "Wild", energy: 0.95, style: "wild" },
];

const HOW_MANY: Array<{ label: string; n: number }> = [
  { label: "One", n: 1 },
  { label: "Two", n: 2 },
  { label: "Three", n: 3 },
];

function Row({ label, hint, children, className }: { label: ReactNode; hint?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <div className={cn("flex min-h-11 items-center gap-3", className)}>
      <div className="min-w-0 flex-1">
        <div className="text-base font-medium leading-snug">{label}</div>
        {hint && <div className="line-clamp-2 text-[15px] leading-snug text-muted-foreground">{hint}</div>}
      </div>
      {children}
    </div>
  );
}

function autoHint(auto: AutoBlock | null | undefined): string {
  if (!auto?.enabled) return "You pick";
  const d = auto.dwell_s_effective;
  if (d && d.length === 2) {
    const fmt = (s: number) => (s >= 90 ? `${Math.round(s / 60)} min` : `${Math.round(s)} s`);
    return `Picked by itself · a new one every ${fmt(d[0])}–${fmt(d[1])}`;
  }
  return "Picked by itself";
}

function gradient(swatch: string[]): string {
  return swatch.length ? `linear-gradient(135deg, ${swatch.join(", ")})` : "#e7e5e4";
}

/** Seconds until the next effect change, from Up next (counted down from when it was read), or null. */
function fxEta(plan: UpNextRead | null | undefined, now: number): number | null {
  const e = plan?.plan.effects[0]?.eta_s;
  if (typeof e !== "number") return null;
  return Math.max(0, e - Math.max(0, now - plan.at) / 1000);
}

/** "Picked by itself · next change in ~40 s", "You pick · these stay on", …: what the Effects switch means right now. */
function FxStatus({
  look,
  plan,
  picks,
  anyOn,
  testId,
}: {
  look: Look;
  plan: UpNextRead | null | undefined;
  picks: number;
  anyOn: boolean;
  testId?: string;
}) {
  const [now, setNow] = useState(() => Date.now());
  const on = !!look.auto?.fx?.enabled;
  const timed = on && typeof plan?.plan.effects[0]?.eta_s === "number";
  useEffect(() => {
    if (!timed) return;
    setNow(Date.now());
    const t = setInterval(() => {
      if (document.visibilityState === "visible") setNow(Date.now());
    }, 5000);
    return () => clearInterval(t);
  }, [timed, plan]);
  const [lo, hi] = fxRange(look.auto?.fx?.max, look.direction?.fx?.density);
  const parts: string[] = [];
  if (on) {
    parts.push("Picked by itself");
    const eta = fxEta(plan, now);
    if (picks === 0 && lo >= 1 && !plan?.plan.effects.length) parts.push("none on yet; one comes in at its next change");
    else if (eta !== null)
      parts.push(
        eta < 5 ? "next change soon" : eta < 90 ? `next change in ~${Math.max(5, Math.round(eta / 5) * 5)} s` : `next change in ~${Math.round(eta / 60)} min`,
      );
    if (lo !== hi) parts.push(`${lo} or ${hi} at a time`);
  } else {
    parts.push("You pick", anyOn ? "these stay on" : "none on");
  }
  if (look.current_scene_effects?.length) parts.push("the scene has its own as well");
  return (
    <p className="text-[15px] leading-snug text-muted-foreground" data-testid={testId}>
      {parts.join(" · ")}
    </p>
  );
}

function SwitchSlot({ label, checked, onChange, testId }: { label: string; checked: boolean; onChange: (c: boolean) => void; testId?: string }) {
  return (
    <span className="grid h-11 w-14 shrink-0 place-items-center">
      <Switch aria-label={label} data-testid={testId} checked={checked} onCheckedChange={onChange} />
    </span>
  );
}

/**
 * Scenes and Effects, each with Next and a "Pick … by itself" switch (off: it stops changing; what's on stays), then
 * How many (while effects are picked) and the energy chips (while either is). `children` go under the Effects line
 * (the effect chips and the strength slider). The same rows serve a screen's card and a room's live card.
 */
export function PickingRows({
  id,
  look,
  lib,
  plan,
  picks,
  anyOn,
  where,
  children,
  onNextScene,
  onNextEffect,
  onScenePicking,
  onFxPicking,
  onDirection,
  onHowMany,
}: {
  id: string;
  look: Look;
  lib: Library | null;
  plan: UpNextRead | null | undefined;
  /** How many of the room's picked effects are on now. */
  picks: number;
  /** Any effect on at all. */
  anyOn: boolean;
  /** Words for where a change goes when it isn't just here ("both screens"), for the buttons' titles. */
  where?: string | null;
  children?: ReactNode;
  onNextScene: () => void;
  onNextEffect: () => void;
  onScenePicking: (on: boolean) => void;
  onFxPicking: (on: boolean) => void;
  onDirection: (label: string, d: Partial<Direction>) => void;
  onHowMany: (n: number) => void;
}) {
  const auto = look.auto ?? null;
  const sceneOn = !!auto?.enabled;
  const fxOn = !!auto?.fx?.enabled;
  const dir = look.direction ?? {};
  const energyNow = typeof dir.energy === "number" ? dir.energy : 0.5;
  const nearest = ENERGY.reduce((a, b) => (Math.abs(b.energy - energyNow) < Math.abs(a.energy - energyNow) ? b : a));
  const [lo, hi] = fxRange(auto?.fx?.max, dir.fx?.density);
  const styles = lib?.vocab?.transition_style ?? [];
  const on = where ? `, on ${where}` : "";
  return (
    <>
      <Row label="Scenes" hint={autoHint(auto)}>
        <button
          type="button"
          onClick={onNextScene}
          data-testid={`next-${id}`}
          title={(sceneOn ? "Picks the next one now" : "Picks one now, then it stays") + on}
          className={BUTTON}
        >
          <SkipForward className="h-4 w-4" aria-hidden /> Next scene
        </button>
        <SwitchSlot label="Pick scenes by itself" testId={`auto-${id}`} checked={sceneOn} onChange={onScenePicking} />
      </Row>

      <div>
        <Row label="Effects">
          <button
            type="button"
            onClick={onNextEffect}
            data-testid={`next-fx-${id}`}
            title={(picks ? "Swaps the one it picked for another now; yours stay" : "Brings one in now") + on}
            className={BUTTON}
          >
            <SkipForward className="h-4 w-4" aria-hidden /> Next effect
          </button>
          <SwitchSlot label="Pick effects by itself" testId={`autofx-${id}`} checked={fxOn} onChange={onFxPicking} />
        </Row>
        <FxStatus look={look} plan={plan} picks={picks} anyOn={anyOn} testId={`fxstatus-${id}`} />
        {children}
      </div>

      {fxOn && (
        <div className="flex items-center gap-3">
          <span className="w-[4.75rem] shrink-0 text-[15px] text-muted-foreground">How many</span>
          <div className="grid flex-1 grid-cols-3 gap-2" role="group" aria-label="How many effects">
            {HOW_MANY.map((h) => (
              <Chip
                key={h.n}
                active={lo === hi && lo === h.n}
                testId={`howmany-${id}-${h.label.toLowerCase()}`}
                className="w-full justify-center px-2"
                onClick={() => onHowMany(h.n)}
              >
                {h.label}
              </Chip>
            ))}
          </div>
        </div>
      )}

      {(sceneOn || fxOn) && (
        <div className="grid grid-cols-4 gap-2" role="group" aria-label="Energy">
          {ENERGY.map((e) => (
            <Chip
              key={e.label}
              active={nearest === e && (dir.source === "panel" || Math.abs(e.energy - energyNow) < 0.08)}
              testId={`energy-${id}-${e.label.toLowerCase()}`}
              className="w-full justify-center px-2"
              onClick={() =>
                onDirection(`energy ${e.label}`, {
                  energy: e.energy,
                  transition_style: e.style && styles.includes(e.style) ? e.style : null,
                  source: "panel",
                })
              }
            >
              {e.label}
            </Chip>
          ))}
        </div>
      )}
    </>
  );
}

// ── Effect chips, their sheet, and the effect picker ──────────────────────────

export function EffectChips({
  id,
  chips,
  where,
  onOpen,
  trailing,
}: {
  id: string;
  chips: FxChip[];
  /** "both screens" when the room's picks are on more than this one. */
  where?: string | null;
  onOpen: (c: FxChip) => void;
  trailing?: ReactNode;
}) {
  return (
    <div className="mt-2 flex flex-wrap gap-2">
      {chips.map((c) => {
        const paused = c.kind === "yours" && !!c.entry?.bypass;
        const title =
          c.kind === "scene"
            ? "Comes with the scene"
            : c.kind === "picked"
              ? `${where ? `On ${where}` : "Picked by itself"} · it changes this one when it picks`
              : "Yours · this screen";
        return (
          <button
            key={c.key}
            type="button"
            onClick={() => onOpen(c)}
            title={title}
            data-testid={`fxchip-${id}`}
            className={cn(
              "inline-flex h-11 max-w-full items-center gap-1.5 rounded-full border pl-4 pr-3 text-base focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
              c.kind === "scene"
                ? "border-stone-300 bg-stone-100 text-stone-700 hover:bg-stone-200/70"
                : paused
                  ? "border-stone-300 bg-stone-100 text-stone-500 hover:bg-stone-200/70"
                  : c.kind === "picked"
                    ? "border-dashed border-teal-400 bg-teal-50 text-teal-900 hover:bg-teal-100"
                    : "border-teal-300 bg-teal-50 text-teal-900 hover:bg-teal-100",
            )}
          >
            <span className="truncate">
              {c.label}
              {c.kind === "scene" ? " · with the scene" : paused ? " · paused" : ""}
            </span>
            <SlidersHorizontal className="h-4 w-4 shrink-0 opacity-70" aria-hidden />
          </button>
        );
      })}
      {trailing}
    </div>
  );
}

/** One effect's sheet: yours (Showing, Strength, Choose another, Take it off), the room's pick (Swap it, Take it
 *  off) or the scene's own (Next scene). Every button closes it; the card says how it went. */
export function EffectSheet({
  chip,
  onClose,
  fxOn,
  where,
  pickedLine,
  testId,
  onShowing,
  onStrength,
  onChooseAnother,
  onTakeOff,
  onSwap,
  onNextScene,
}: {
  chip: FxChip | null;
  onClose: () => void;
  /** Pick effects by itself is on. */
  fxOn: boolean;
  /** "both screens" when the room's picks (and Next scene) reach more than this screen. */
  where?: string | null;
  /** The room's pick's line when its buttons act on this screen only (the renderer's scope "screen"). */
  pickedLine?: string;
  testId?: string;
  onShowing?: (c: FxChip, on: boolean) => void;
  onStrength?: (c: FxChip, v: number) => void;
  onChooseAnother?: (c: FxChip) => void;
  onTakeOff: (c: FxChip) => void;
  onSwap?: (c: FxChip) => void;
  onNextScene?: () => void;
}) {
  const [draft, setDraft] = useState<number | null>(null);
  useEffect(() => setDraft(null), [chip?.key]);
  const level = draft ?? (typeof chip?.entry?.level === "number" ? chip.entry.level : 1);
  const showing = !chip?.entry?.bypass;
  const go = (fn: () => void) => () => {
    onClose();
    fn();
  };
  const line =
    chip?.kind === "scene"
      ? "Comes with the scene. It changes when the scene changes."
      : chip?.kind === "picked"
        ? pickedLine ??
          `${where ? `On ${where}.` : "Picked by itself."} ${fxOn ? "It moves on at its next change while Pick effects by itself is on." : "It stays while Pick effects by itself is off."}`
        : where
          ? "Yours, on this screen only. It stays until you take it off."
          : "Yours. It stays until you take it off.";
  return (
    <ActionSheet open={!!chip} onOpenChange={(o) => !o && onClose()} title={chip?.label ?? ""} description={line} testId={testId}>
      {chip?.kind === "yours" && (
        <div className="space-y-3">
          <label className="flex min-h-11 cursor-pointer items-center gap-3">
            <span className="min-w-0 flex-1">
              <span className="block text-base">Showing</span>
              <span className="block text-[15px] leading-snug text-muted-foreground">{showing ? "On the picture" : "Paused · kept in its place"}</span>
            </span>
            <Switch checked={showing} onCheckedChange={(c) => onShowing?.(chip, c)} />
          </label>
          <div className="flex min-h-11 items-center gap-3">
            <span className="shrink-0 text-base">Strength</span>
            <Slider
              aria-label={`${chip.label} strength`}
              className="flex-1 py-3 [&_[role=slider]]:h-6 [&_[role=slider]]:w-6 [&_[role=slider]]:shadow-sm"
              min={0}
              max={1}
              step={0.01}
              value={[level]}
              onValueChange={([v]) => setDraft(v)}
              onValueCommit={([v]) => onStrength?.(chip, v)}
            />
            <span className="w-11 shrink-0 text-right font-mono text-[15px] tabular-nums text-muted-foreground">{Math.round(level * 100)}%</span>
          </div>
          <div className="flex flex-wrap gap-2 pt-1">
            <button type="button" className={BUTTON} onClick={go(() => onChooseAnother?.(chip))}>
              <Sparkles className="h-4 w-4" aria-hidden /> Choose another…
            </button>
            <button type="button" className={BUTTON} onClick={go(() => onTakeOff(chip))}>
              Take it off
            </button>
          </div>
        </div>
      )}
      {chip?.kind === "picked" && (
        <div className="space-y-2">
          <div className="flex flex-wrap gap-2">
            <button
              type="button"
              className={BUTTON}
              title={`It picks another${pickedLine ? ", on this screen" : where ? `, on ${where}` : ""}`}
              onClick={go(() => onSwap?.(chip))}
            >
              <Shuffle className="h-4 w-4" aria-hidden /> Swap it
            </button>
            <button type="button" className={BUTTON} onClick={go(() => onTakeOff(chip))}>
              Take it off
            </button>
          </div>
          {fxOn && (
            <p className="text-[15px] leading-snug text-muted-foreground">
              Taking it off brings another in at its next change. To keep them off, turn Pick effects by itself off.
            </p>
          )}
        </div>
      )}
      {chip?.kind === "scene" && onNextScene && (
        <button type="button" className={BUTTON} onClick={go(onNextScene)}>
          <SkipForward className="h-4 w-4" aria-hidden /> Next scene{where ? ` · ${where}` : ""}
        </button>
      )}
    </ActionSheet>
  );
}

/**
 * The effect picker. "change": tap one to put it on now in place of the room's pick (or, with Add on top, on top as
 * yours), with a first "No effects" tile. "add": on top, as yours, only. "instead": another in one of yours' place.
 * Tapping one that's on takes it off. Nothing is ever disabled (at the limit your oldest comes off).
 */
export function EffectPicker({
  open,
  onOpenChange,
  lib,
  api,
  mode,
  title,
  description,
  selected,
  onPick,
  onNone,
  noneSub,
  onTopHint,
  testId,
}: {
  open: boolean;
  onOpenChange: (o: boolean) => void;
  lib: Library | null;
  api: RoomApi;
  mode: "change" | "add" | "instead";
  title: string;
  description: string;
  selected: ReadonlySet<string>;
  onPick: (key: string, label: string, onTop: boolean) => void;
  onNone?: () => void;
  noneSub?: string;
  onTopHint?: string;
  testId?: string;
}) {
  const { items, chips } = useEffectItems(lib, api);
  const [onTop, setOnTop] = useState(() => storageGet(FX_ON_TOP_KEY) === "1");
  const footer =
    mode === "change" ? (
      <label className="flex min-h-11 cursor-pointer items-center gap-3">
        <span className="min-w-0 flex-1">
          <span className="block text-base">Add on top</span>
          <span className="block text-[15px] leading-snug text-muted-foreground">{onTopHint ?? "Keeps what's on and adds this one."}</span>
        </span>
        <Switch
          checked={onTop}
          data-testid={testId ? `${testId}-ontop` : undefined}
          onCheckedChange={(c) => {
            setOnTop(c);
            storageSet(FX_ON_TOP_KEY, c ? "1" : "0");
          }}
        />
      </label>
    ) : null;
  return (
    <Picker
      open={open}
      onOpenChange={onOpenChange}
      title={title}
      description={description}
      items={items}
      chips={chips}
      selected={selected}
      lead={
        mode === "change" && onNone
          ? {
              label: "No effects",
              sub: noneSub,
              icon: <Ban className="h-7 w-7" aria-hidden />,
              testId: testId ? `${testId}-none` : undefined,
              onPick: () => {
                onOpenChange(false);
                onNone();
              },
            }
          : undefined
      }
      onPick={(it) => {
        onOpenChange(false);
        onPick(it.key, it.label, mode === "add" || (mode === "change" && onTop));
      }}
      footer={footer}
      testId={testId}
    />
  );
}

function StrengthRow({ id, amount, hint, onChange }: { id: string; amount: number; hint?: string | null; onChange: (v: number) => void }) {
  return (
    <div className="mt-1 flex min-h-11 items-center gap-3">
      <span className="shrink-0 leading-snug">
        <span className="block text-base">Strength</span>
        {hint && <span className="block text-[15px] text-muted-foreground">{hint}</span>}
      </span>
      <Slider
        aria-label="Effects strength"
        data-testid={`fxmaster-${id}`}
        className="flex-1 py-3 [&_[role=slider]]:h-6 [&_[role=slider]]:w-6 [&_[role=slider]]:shadow-sm"
        min={0}
        max={1}
        step={0.01}
        value={[amount]}
        onValueChange={([v]) => onChange(v)}
      />
      <span className="w-11 shrink-0 text-right font-mono text-[15px] tabular-nums text-muted-foreground">{Math.round(amount * 100)}%</span>
    </div>
  );
}

/** The session palettes as swatches, plus the scene's own. */
export function ColoursRow({
  id,
  look,
  lib,
  hint,
  onPick,
}: {
  id: string;
  look: Look;
  lib: Library | null;
  /** Where a change goes ("This screen", "Both screens"), next to the name of the colours on now. */
  hint?: string | null;
  onPick: (name: string | null, label: string) => void;
}) {
  const palettes = lib?.palettes ?? [];
  if (!palettes.length) return null;
  const palNow = palettes.find((p) => p.name === look.palette);
  return (
    <div>
      <div className="mb-1 flex items-baseline justify-between gap-2">
        <span className="text-base font-medium">Colours</span>
        <span className="truncate text-[15px] text-muted-foreground">
          {palNow ? palNow.label : "The scene's own"}
          {hint ? ` · ${hint}` : ""}
        </span>
      </div>
      <div className="-mx-4 flex gap-2 overflow-x-auto px-4 pb-1 [scrollbar-width:none]" role="group" aria-label="Colours">
        <Swatch label="The scene's own colours" active={!look.palette} onClick={() => onPick(null, "scene colours")} testId={`palette-${id}-own`}>
          <Ban className="h-5 w-5 text-stone-400" aria-hidden />
        </Swatch>
        {palettes.map((p) => (
          <Swatch
            key={p.name}
            label={p.label}
            active={look.palette === p.name}
            style={{ background: gradient(p.swatch) }}
            onClick={() => onPick(p.name, `colours ${p.label}`)}
            testId={`palette-${id}-${p.name}`}
          />
        ))}
      </div>
    </div>
  );
}

function Swatch({
  label,
  active,
  onClick,
  style,
  children,
  testId,
}: {
  label: string;
  active: boolean;
  onClick: () => void;
  style?: CSSProperties;
  children?: ReactNode;
  testId?: string;
}) {
  return (
    <button
      type="button"
      aria-label={label}
      aria-pressed={active}
      title={label}
      onClick={onClick}
      data-testid={testId}
      style={style}
      className={cn(
        "relative grid h-11 w-11 shrink-0 place-items-center rounded-full border border-stone-300 bg-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
        active && "ring-2 ring-teal-700 ring-offset-2",
      )}
    >
      {children}
      {active && style && (
        <span className="absolute grid h-5 w-5 place-items-center rounded-full bg-white/90 text-teal-800">
          <Check className="h-3.5 w-3.5" aria-hidden />
        </span>
      )}
    </button>
  );
}

// ── Inline on a screen's card ─────────────────────────────────────────────────

type PickFor = { mode: "change" | "add" } | { mode: "instead"; chip: FxChip } | null;

export function LookControls({
  screenId,
  lv,
  lib,
  api,
  scope,
}: {
  screenId: string;
  lv: LiveLook;
  lib: Library | null;
  api: RoomApi;
  /** Kept for callers; nothing here is greyed out (a tap that can't get through says so). */
  disabled?: boolean;
  /** Its room's live card, when the room has one (index.tsx): the room-wide controls live there. */
  scope?: LookScope | null;
}) {
  const look = lv.look;
  const hosted = !!scope?.hosted;
  const [pick, setPick] = useState<PickFor>(null);
  const [sheetKey, setSheetKey] = useState<string | null>(null);
  // Up next is the room's plan: on a room's live card when it has one, else here.
  const un = useUpNext({ api, sessionId: hosted ? null : lv.session, subscribe: lv.subscribe });
  const chips = useMemo(() => fxChips(look, lib), [look, lib]);
  if (!look) {
    return lv.available === false ? null : (
      <p className="flex items-center gap-2 text-[15px] text-muted-foreground">
        <Spinner /> Reading the screen's live settings…
      </p>
    );
  }
  const planner = un.read?.plan.planned_by;
  const shared = scope ? scope.shared : planner && planner !== screenId ? [friendlyName(planner)] : [];
  const both = bothWords(shared);
  const auto = look.auto ?? null;
  const fxOn = !!auto?.fx?.enabled;
  const stack = look.fx?.stack ?? [];
  const max = effectSlots(lib);
  const picks = stack.filter(isPicked).length;
  const amount = lv.masterDraft ?? (typeof look.fx?.master?.amount === "number" ? (look.fx.master.amount as number) : 1);
  const me: LiveMember | null = lv.session ? { id: screenId, name: friendlyName(screenId), session: lv.session } : null;
  const sheetChip = chips.find((c) => c.key === sheetKey) ?? null;
  // A look change from this card: this screen's only with the renderer's look_scope, else the room's.
  const own = scope?.own ?? [];
  const lookWhere = hosted && both ? (lv.scoped ? (own.includes("palette") ? "This screen's own" : "This screen") : cap(both)) : null;
  // The room's picks from this card: this screen only when the renderer can (its effects become its own).
  const fxHere = hosted && !!both && lv.fxScoped;

  const fxPicking = (on: boolean) =>
    void lv.patch(on ? "effect picking on" : "effect picking off", { auto: { fx: { enabled: on } } }, (l) => ({
      ...l,
      auto: { ...(l.auto ?? {}), fx: { ...(l.auto?.fx ?? {}), enabled: on } },
    }));

  const takeOffPicked = (c: FxChip) => {
    const what = c.preset ?? c.key;
    if (fxHere) void lv.takeOffHere(c.key, c.label);
    else if (scope) void lv.act(`taking ${c.label} off`, () => scope.takeOffPicked(what));
    else if (me) void lv.act(`taking ${c.label} off`, () => clearEffects(api, [me], [me], what, { lib }));
  };
  const takeOff = (c: FxChip) => (c.kind === "picked" ? takeOffPicked(c) : c.entry && void lv.removeEffect(c.entry.id, c.label));
  const swap = (c: FxChip) => {
    if (!fxHere && scope && c.preset) void lv.act(`swapping ${c.label}`, () => scope.swapPicked(c.preset as string));
    else void lv.swapEffect(c.key, c.label);
  };
  const noEffects = async () => {
    await lv.patch("effect picking off", { auto: { fx: { enabled: false } } }, (l) => ({
      ...l,
      auto: { ...(l.auto ?? {}), fx: { ...(l.auto?.fx ?? {}), enabled: false } },
    }));
    if (me) await lv.act("taking every effect off", () => clearEffects(api, [me], [me], undefined, { lib }));
  };
  const onPicked = (key: string, label: string, onTop: boolean) => {
    const p = pick;
    setPick(null);
    if (p?.mode === "instead") {
      void lv.setEffect(p.chip.key, `${p.chip.label} for ${label}`, { preset: key });
      return;
    }
    const on = chips.find((c) => c.kind !== "scene" && c.preset === key);
    if (on) return takeOff(on);
    if (onTop) void lv.addEffect(key, label, max, (d) => droppedWords(max, fxLabel(d, lib)));
    else if (me) void lv.act(`putting ${label} on`, () => pickEffect(api, me.session, key, { lib }));
  };

  const selected = new Set(chips.filter((c) => c.kind !== "scene" && c.preset).map((c) => c.preset as string));
  const effectChips = (
    <EffectChips
      id={screenId}
      chips={chips}
      where={both}
      onOpen={(c) => setSheetKey(c.key)}
      trailing={
        <button type="button" onClick={() => setPick({ mode: hosted ? "add" : "change" })} data-testid={`addfx-${screenId}`} className={ADD_BUTTON}>
          {hosted ? <Plus className="h-4 w-4" aria-hidden /> : <Sparkles className="h-4 w-4" aria-hidden />}
          {hosted ? "Add an effect" : "Change effect"}
        </button>
      }
    />
  );
  const strength = stack.length > 0 && <StrengthRow id={screenId} amount={amount} hint={both ? "This screen" : null} onChange={lv.setMaster} />;
  const pickerWords =
    pick?.mode === "instead"
      ? { title: `Instead of ${pick.chip.label}`, description: "It takes this one's place, on this screen, and stays yours." }
      : pick?.mode === "add"
        ? { title: "Add an effect", description: `It goes on over what's on${both ? ", on this screen only," : ""} and stays until you take it off.` }
        : {
            title: "Change effect",
            description:
              `Tap one to put it on now, in place of the one it picked${both ? `, on ${both}` : ""}.` +
              (fxOn ? " It moves on at its next change. Turn Pick effects by itself off to keep it." : ""),
          };

  return (
    <div className="space-y-3 border-t border-stone-200/80 pt-3" data-testid={`look-${screenId}`}>
      {!hosted && both && (
        <p className="text-[15px] leading-snug text-muted-foreground">
          Shared with {shared.join(" and ")}: a change here changes {both}. Strength and Add on top are this screen's only.
        </p>
      )}

      {hosted ? (
        <div>
          <div className="flex min-h-11 items-center">
            <span className="text-base font-medium">{both ? "Effects on this screen" : "Effects"}</span>
          </div>
          {effectChips}
          {strength}
        </div>
      ) : (
        <PickingRows
          id={screenId}
          look={look}
          lib={lib}
          plan={un.read}
          picks={picks}
          anyOn={stack.length > 0}
          where={both}
          onNextScene={() => void lv.cue("next scene", { to: "auto" })}
          onNextEffect={() => void lv.nextEffect()}
          onScenePicking={(c) =>
            void lv.patch(c ? "scene picking on" : "scene picking off", { auto: { enabled: c } }, (l) => ({
              ...l,
              auto: { ...(l.auto ?? {}), enabled: c },
            }))
          }
          onFxPicking={fxPicking}
          onDirection={(label, d) => void lv.setDirection(label, d)}
          onHowMany={(n) =>
            void lv.patch(`${n} ${n === 1 ? "effect" : "effects"} at a time`, { auto: { fx: { max: n } }, direction: { fx: { density: 1 } } }, (l) => ({
              ...l,
              auto: { ...(l.auto ?? {}), fx: { ...(l.auto?.fx ?? {}), max: n } },
              direction: mergeDirection(l.direction, { fx: { density: 1 } }),
            }))
          }
        >
          {effectChips}
          {strength}
        </PickingRows>
      )}

      {/* Up next: the scenes and effects it plays next; change them at any time */}
      {!hosted && <UpNext screenId={screenId} un={un} lib={lib} api={api} autoOn={!!auto?.enabled} fxAutoOn={fxOn} sharedLine={!both} />}

      <ColoursRow
        id={screenId}
        look={look}
        lib={lib}
        hint={lookWhere}
        onPick={(name, label) => void lv.patch(label, { palette: name }, (l) => ({ ...l, palette: name }))}
      />

      {lv.busy && (
        <p className="flex items-center gap-2 text-[15px] text-muted-foreground">
          <Spinner /> {cap(lv.busy)}…
        </p>
      )}
      {lv.error && <Notice tone="bad">{lv.error}</Notice>}
      {lv.info && <Notice tone="muted">{lv.info}</Notice>}

      <EffectPicker
        open={!!pick}
        onOpenChange={(o) => !o && setPick(null)}
        lib={lib}
        api={api}
        mode={pick?.mode ?? "change"}
        title={pickerWords.title}
        description={pickerWords.description}
        selected={pick?.mode === "instead" ? new Set(pick.chip.preset ? [pick.chip.preset] : []) : selected}
        onPick={onPicked}
        onNone={() => void noEffects()}
        noneSub={both ? `On ${both}` : "On this screen"}
        onTopHint={`Keeps what's on and adds this one${both ? ", on this screen only" : ""}.`}
        testId={`fxpicker-${screenId}`}
      />
      <EffectSheet
        chip={sheetChip}
        onClose={() => setSheetKey(null)}
        fxOn={fxOn}
        where={both}
        pickedLine={fxHere ? `The room's pick, on ${both}. Swap it or take it off here and it changes on this screen only; this screen then keeps its own effects.` : undefined}
        testId={`fxsheet-${screenId}`}
        onShowing={(c, on) => void lv.setEffect(c.key, on ? `showing ${c.label}` : `pausing ${c.label}`, { bypass: !on })}
        onStrength={(c, v) => void lv.setEffect(c.key, `${c.label} strength`, { level: v })}
        onChooseAnother={(c) => setPick({ mode: "instead", chip: c })}
        onTakeOff={takeOff}
        onSwap={swap}
        onNextScene={() => void lv.cue("next scene", { to: "auto" })}
      />
    </div>
  );
}

// ── Under "More": the rest of the look ────────────────────────────────────────

const DWELL: Array<{ label: string; value: [number, number] | null }> = [
  { label: "Follow the feel", value: null },
  { label: "About 20 s", value: [15, 30] },
  { label: "About 1 min", value: [40, 90] },
  { label: "About 3 min", value: [120, 240] },
];

const FADES = [0, 1, 2, 5, 10];
const PALETTE_FADES = [1, 4, 10];
/** direction.fx.follow while "Change with the music" is on (the renderer's own default). */
const FX_FOLLOW_ON = 0.8;

function sameDwell(a: unknown, b: [number, number] | null): boolean {
  if (b === null) return a === null || a === undefined;
  return Array.isArray(a) && a.length === 2 && Math.abs(a[0] - b[0]) < 0.5 && Math.abs(a[1] - b[1]) < 0.5;
}

function poolFamily(pool: unknown): string | null {
  if (pool && typeof pool === "object" && !Array.isArray(pool) && "tags" in pool) {
    const f = (pool as { tags: Record<string, unknown> }).tags.family;
    return typeof f === "string" ? f : null;
  }
  return null;
}

const oftenWords = (v: number) => (v < 0.34 ? "Rarely" : v < 0.67 ? "Sometimes" : "Often");

/**
 * The rest of a look. `parts`: "picking" (what scenes it picks from, how long they stay, how often effects change) for
 * a room's live card; "feel" (the feel and fades) for a screen's card in such a room; "all" otherwise. `words` says
 * where a change goes ("Changes here go to both screens.").
 */
export function LookSettings({
  screenId,
  lv,
  lib,
  api,
  parts = "all",
  words,
}: {
  screenId: string;
  lv: LookCtl;
  lib: Library | null;
  api: RoomApi;
  /** Kept for callers; nothing here is greyed out. */
  disabled?: boolean;
  parts?: "all" | "picking" | "feel";
  words?: string | null;
}) {
  const look = lv.look;
  const [music, setMusic] = useState<string | null>(null);
  // Re-read every 5 s while this is on screen and the page is visible.
  useEffect(() => {
    let alive = true;
    const read = () => {
      if (document.visibilityState === "hidden") return;
      api
        .inputs(screenId)
        .then((r) => {
          if (!alive) return;
          const d = (r.derived ?? {}) as Record<string, unknown>;
          const bpm = typeof d.bpm === "number" ? Math.round(d.bpm) : null;
          setMusic(
            r.online === false
              ? "No music signal: on-the-beat changes happen straight away."
              : bpm
                ? `Hearing ${bpm} beats a minute${d.beat_locked ? ", in time" : ", finding the beat"}.`
                : "Listening for the beat…",
          );
        })
        .catch(() => alive && setMusic(null));
    };
    read();
    const id = window.setInterval(read, 5_000);
    return () => {
      alive = false;
      window.clearInterval(id);
    };
  }, [api, screenId, look?.session]);

  if (!look) return null;
  const auto = look.auto ?? null;
  const dir: Direction = look.direction ?? {};
  const vocab = lib?.vocab ?? {};
  const families = (lib?.facets?.scenes?.family ?? []).slice(0, 8);
  const fam = poolFamily(auto?.pool);
  const fxFade = look.fx?.fade_s ?? 2;
  const picking = parts !== "feel";
  const feel = parts !== "picking";

  const setAuto = (label: string, a: Partial<AutoBlock>) =>
    void lv.patch(label, { auto: a }, (l) => ({ ...l, auto: { ...(l.auto ?? {}), ...a } }));

  return (
    <div className="space-y-5" data-testid={`looksettings-${screenId}`}>
      {words && <p className="text-[15px] leading-snug text-muted-foreground">{words}</p>}

      {picking && (
        <div>
          <SectionTitle>Scene picking</SectionTitle>
          {!auto?.enabled && <p className="mb-2 text-[15px] text-muted-foreground">Used while Pick scenes by itself is on.</p>}
          <div className="space-y-3">
            <ChipGroup label="Scenes it picks from">
              <Chip active={auto?.pool === "all"} onClick={() => setAuto("picking from every scene", { pool: "all" })}>
                <Shuffle className="h-4 w-4" aria-hidden /> Any
              </Chip>
              {families.map((f) => (
                <Chip key={f.value} active={fam === f.value} onClick={() => setAuto(`picking ${f.value} scenes`, { pool: { tags: { family: f.value } } })}>
                  {cap(f.value)}
                </Chip>
              ))}
            </ChipGroup>
            {Array.isArray(auto?.pool) && (
              <p className="text-[15px] text-muted-foreground">
                This mode picks from its own {auto.pool.length} scenes; Any widens it.
              </p>
            )}
            <ChipGroup label="Each scene stays">
              {DWELL.map((d) => (
                <Chip key={d.label} active={sameDwell(auto?.dwell_s, d.value)} onClick={() => setAuto(`scenes stay: ${d.label.toLowerCase()}`, { dwell_s: d.value })}>
                  {d.label}
                </Chip>
              ))}
            </ChipGroup>
            {sameDwell(auto?.dwell_s, null) && (
              <UnitSlider
                label="How often it changes scene"
                value={dir.change_rate ?? 0.5}
                show={oftenWords}
                onCommit={(v) => void lv.setDirection("change rate", { change_rate: v, source: "panel" })}
              />
            )}
            <SwitchRow
              label="Change on the beat"
              hint="Waits for the music's next beat"
              checked={(auto?.snap ?? "beat") === "beat"}
              onChange={(c) => setAuto(c ? "changing on the beat" : "changing straight away", { snap: c ? "beat" : "now" })}
            />
          </div>
        </div>
      )}

      {picking && (
        <div>
          <SectionTitle>Effect picking</SectionTitle>
          {!auto?.fx?.enabled && <p className="mb-2 text-[15px] text-muted-foreground">Used while Pick effects by itself is on.</p>}
          <div className="space-y-3">
            <UnitSlider
              label="How often they change"
              value={dir.fx?.rate ?? 0.5}
              show={oftenWords}
              onCommit={(v) => void lv.setDirection("how often effects change", { fx: { rate: v } })}
            />
            <SwitchRow
              label="Change with the music"
              hint="A drop or a new track brings a change"
              checked={(dir.fx?.follow ?? FX_FOLLOW_ON) > 0}
              onChange={(c) => void lv.setDirection(c ? "effects change with the music" : "effects keep their own time", { fx: { follow: c ? FX_FOLLOW_ON : 0 } })}
            />
          </div>
        </div>
      )}

      {feel && (
        <div>
          <SectionTitle
            right={
              <button
                type="button"
                className="-my-1 h-11 rounded-xl px-3 text-[15px] text-muted-foreground hover:bg-stone-100"
                onClick={() => void lv.setDirection("feel back to neutral", { ...(lib?.direction?.defaults ?? {}), source: "panel" })}
              >
                Back to neutral
              </button>
            }
          >
            The feel
          </SectionTitle>
          <div className="space-y-3">
            <UnitSlider
              label="Speed"
              value={dir.pace ?? 0.5}
              show={(v) => `${(2 ** (2 * v - 1)).toFixed(2).replace(/0$/, "")}×`}
              onCommit={(v) => void lv.setDirection("speed", { pace: v, source: "panel" })}
            />
            <UnitSlider
              label="Movement with the music"
              value={dir.intensity ?? 1}
              show={(v) => `${Math.round(v * 100)}%`}
              onCommit={(v) => void lv.setDirection("movement", { intensity: v, source: "panel" })}
            />
            {!!vocab.moods?.length && (
              <ChipGroup label="Moods">
                {vocab.moods.map((m) => {
                  const on = (dir.moods ?? []).includes(m);
                  return (
                    <Chip
                      key={m}
                      active={on}
                      onClick={() =>
                        void lv.setDirection(`mood ${m}`, {
                          moods: on ? (dir.moods ?? []).filter((x) => x !== m) : [...(dir.moods ?? []), m],
                          source: "panel",
                        })
                      }
                    >
                      {cap(m)}
                    </Chip>
                  );
                })}
              </ChipGroup>
            )}
            {(
              [
                ["Music", "genre", vocab.genre],
                ["Transitions", "transition_style", vocab.transition_style],
                ["Colour family", "palette_family", vocab.palette_family],
              ] as const
            ).map(([label, key, w]) =>
              w?.length ? (
                <ChipGroup key={key} label={label}>
                  <Chip active={!dir[key]} onClick={() => void lv.setDirection(`${label}: any`, { [key]: null, source: "panel" })}>
                    Any
                  </Chip>
                  {w.map((x) => (
                    <Chip key={x} active={dir[key] === x} onClick={() => void lv.setDirection(`${label}: ${x}`, { [key]: x, source: "panel" })}>
                      {cap(x)}
                    </Chip>
                  ))}
                </ChipGroup>
              ) : null,
            )}
          </div>
        </div>
      )}

      {feel && (
        <div>
          <SectionTitle>Fades</SectionTitle>
          <div className="space-y-3">
            <ChipGroup label="Colour changes blend over">
              {PALETTE_FADES.map((s) => (
                <Chip
                  key={s}
                  active={Math.abs((look.palette_transition_s ?? 4) - s) < 0.01}
                  onClick={() => void lv.patch(`colour fade ${s} s`, { palette_transition_s: s }, (l) => ({ ...l, palette_transition_s: s }))}
                >
                  {s} s
                </Chip>
              ))}
            </ChipGroup>
            <ChipGroup label="Effects fade in and out over">
              {FADES.map((s) => (
                <Chip key={s} active={Math.abs(fxFade - s) < 0.01} onClick={() => void lv.setFxFade(s)}>
                  {s === 0 ? "Instant" : `${s} s`}
                </Chip>
              ))}
            </ChipGroup>
          </div>
        </div>
      )}

      {music && <p className="text-[15px] text-muted-foreground">{music}</p>}
    </div>
  );
}

function ChipGroup({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div role="group" aria-label={label}>
      <div className="mb-1 text-[15px] text-muted-foreground">{label}</div>
      <div className="flex flex-wrap gap-2">{children}</div>
    </div>
  );
}

function SwitchRow({ label, hint, checked, onChange }: { label: string; hint?: string; checked: boolean; onChange: (c: boolean) => void }) {
  return (
    <label className="flex min-h-11 cursor-pointer items-center gap-3">
      <span className="min-w-0 flex-1">
        <span className="block text-base">{label}</span>
        {hint && <span className="block text-[15px] leading-snug text-muted-foreground">{hint}</span>}
      </span>
      <Switch checked={checked} onCheckedChange={onChange} />
    </label>
  );
}

/** A 0–1 slider that sends on release (each change is a round trip to the renderer). */
function UnitSlider({ label, value, show, onCommit }: { label: string; value: number; show: (v: number) => string; onCommit: (v: number) => void }) {
  const [draft, setDraft] = useState<number | null>(null);
  const v = draft ?? value;
  return (
    <div>
      <div className="flex items-baseline justify-between gap-3">
        <span className="min-w-0 text-base">{label}</span>
        <span className="shrink-0 text-[15px] tabular-nums text-muted-foreground">{show(v)}</span>
      </div>
      <Slider
        aria-label={label}
        className="py-3 [&_[role=slider]]:h-6 [&_[role=slider]]:w-6 [&_[role=slider]]:shadow-sm"
        min={0}
        max={1}
        step={0.01}
        value={[v]}
        onValueChange={([x]) => setDraft(x)}
        onValueCommit={([x]) => {
          setDraft(null);
          onCommit(x);
        }}
      />
    </div>
  );
}
