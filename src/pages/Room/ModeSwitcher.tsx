// The modes (GET /api/v1/modes) as one row of chips that scrolls sideways (icon and name; the one the room is in filled
// teal); tapping one switches the room (PUT /api/v1/mode). Under the row, one full line says what the current mode
// changes in the room (modeWords.modeLine, from describe). Chips never go dead: a tap while a switch runs simply wins
// (room_service: the last request wins), and a tap while the room is out of reach keeps trying for a minute.
//   - Each chip's "⋯" edits, copies, makes it the start mode ("Start here"), moves or deletes that mode (owner, 15 Oct:
//     "modes editable via ⋯"). The pencil at the end of the row opens EditModesSheet: the same, for every mode, and
//     "Add a mode".
//   - A mode's icon is its modes.json "icon" (picked in the editor), else a guess from its name.
//   - Modes with "on_room_page": "more" (a plain per-mode field, set in the editor) sit behind a "More modes" chip at
//     the end of the row, unless the room is in one.
//   - While a switch runs, after a problem, or after Auto switched (with its reason), one plain line says so
//     ("Switching to Disco · glitterball starting"), with the steps behind "Details". A clean switch by hand needs no
//     line: the chip says it.

import { useEffect, useMemo, useRef, useState } from "react";
import {
  Check,
  ChevronLeft,
  ChevronRight,
  CircleAlert,
  CircleCheck,
  CircleDashed,
  CircleHelp,
  Clapperboard,
  Disc3,
  FlaskConical,
  House,
  Image as ImageIcon,
  Lamp,
  LayoutGrid,
  Minus,
  Moon,
  MoreHorizontal,
  Music,
  PartyPopper,
  Pencil,
  Piano,
  Power,
  Sparkles,
  Sun,
  Waves,
  X,
  type LucideIcon,
} from "lucide-react";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { cn } from "@/lib/utils";
import { explainError, RoomApi, RoomApiError } from "./api";
import { Notice, Spinner } from "./bits";
import { sceneChoices, type SceneChoice } from "./catalog";
import { EditModesSheet, type EditModesRow } from "./EditModesSheet";
import { THEME_CLASS } from "./theme";
import { TEXT_CLASSES, TONE_CLASSES, stepTone, type Tone } from "./tones";
import type {
  Catalog,
  ModeDef,
  ModeStatus,
  RawMode,
  RawRoomConfig,
  RoomDescribe,
  RoomStatus,
  TrackedTransition,
  TransitionStep,
} from "./types";
import type { NewLogEntry } from "./useRoomConnection";
import { modeLine, stepSentence, switchingWords, targetName } from "./modeWords";
import { modeDescription, modeLabel, STEP_WORDS, stepFailure, stepGoingLive, stepText } from "./words";

/** How long an interrupted switch stays said. */
const DONE_SHOWN_MS = 15_000;
/** How long "Auto: Now <mode> · <why>" stays after Auto switched (a clean switch by hand shows no line). */
const AUTO_SHOWN_MS = 5 * 60_000;
/** How long a switch with failed steps stays on the main screen (the menu keeps it). */
const PROBLEM_SHOWN_MS = 10 * 60_000;
/** A tap the room doesn't answer is sent again this often, for this long. */
const RETRY_MS = 2_000;
const TRY_FOR_MS = 60_000;
/** How long a delete can be undone. */
const UNDO_MS = 15_000;

type Json = Record<string, unknown>;
const isObj = (v: unknown): v is Json => !!v && typeof v === "object" && !Array.isArray(v);
const clone = <T,>(v: T): T => JSON.parse(JSON.stringify(v)) as T;
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));
const unreachable = (e: unknown) => e instanceof RoomApiError && e.status === 0;

/** A modes.json key for a new mode: its label as a slug, made unique. */
function newModeId(label: string, taken: Record<string, unknown>): string {
  const base = label.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "") || "mode";
  let id = base;
  for (let n = 2; id in taken; n++) id = `${base}-${n}`;
  return id;
}

/** The modes with `copy` inserted right after `after` (tiles follow the file's order). */
function insertAfter(modes: Record<string, RawMode>, after: string, id: string, copy: RawMode): Record<string, RawMode> {
  const out: Record<string, RawMode> = {};
  for (const [k, v] of Object.entries(modes)) {
    out[k] = v;
    if (k === after) out[id] = copy;
  }
  if (!(id in out)) out[id] = copy;
  return out;
}

/** A mode copied for Duplicate and Add, as a full copy (modes are flat): without its comments ("_" keys) and without
 *  the triggers that switch it on by itself (autopilot.enter_when, otherwise_suggest_when), so a copy never switches
 *  the room by itself before someone says so. */
function copyMode(src: RawMode, label: string): { copy: RawMode; hadTrigger: boolean } {
  const copy = clone(src);
  for (const k of Object.keys(copy)) if (k.startsWith("_")) delete copy[k];
  let hadTrigger = false;
  if (isObj(copy.autopilot)) {
    const ap = copy.autopilot;
    hadTrigger = "enter_when" in ap || "otherwise_suggest_when" in ap;
    delete ap.enter_when;
    delete ap.otherwise_suggest_when;
    if (!Object.keys(ap).length) delete copy.autopilot;
  }
  return { copy: { ...copy, label }, hadTrigger };
}

type Row = { verb?: string; slot?: string | null; selector?: string };

/** A legacy store/retrieve pair ({"store": slot, "set": v} here, {"retrieve": slot} elsewhere) must never be left half:
 *  a retrieve of what only the deleted mode saved would have nothing to put back, ever, and the room refuses the file.
 *  With nothing saved, a retrieve holds its "else" or leaves the device be, so that's what the row becomes. */
function unpair(
  modes: Record<string, RawMode>,
  name: string,
  describe: RoomDescribe | null | undefined,
  label: (m: string) => string,
  nameOf: (t: string) => string,
): string[] {
  if (!describe) return [];
  const rows = (m: string) => Object.entries((describe.modes[m]?.entries ?? {}) as Record<string, Row>);
  const stores = (m: string) =>
    rows(m)
      .filter(([, e]) => e.slot && (e.verb === "store" || e.verb === "store+set"))
      .map(([t, e]) => `${e.slot}\n${t}`);
  const mine = new Set(stores(name));
  if (!mine.size) return [];
  const others = new Set(Object.keys(describe.modes).filter((m) => m !== name).flatMap(stores));
  const out: string[] = [];
  for (const m of Object.keys(modes)) {
    const state = modes[m].state;
    if (!isObj(state)) continue;
    const lost = rows(m).filter(([t, e]) => e.verb === "retrieve" && !!e.slot && mine.has(`${e.slot}\n${t}`) && !others.has(`${e.slot}\n${t}`));
    if (!lost.length) continue;
    const next: Json = { ...state };
    for (const sel of new Set(lost.map(([, e]) => e.selector).filter((s): s is string => !!s))) {
      const r = isObj(state[sel]) ? (state[sel] as Json) : {};
      const held = "else" in r && r.else !== "leave" ? { set: r.else, policy: r.policy ?? "ignore", ...(r.opts ? { opts: r.opts } : {}) } : null;
      const covered = rows(m).filter(([, e]) => e.selector === sel).length;
      const here = lost.filter(([, e]) => e.selector === sel).map(([t]) => t);
      if (here.length === covered) {
        if (held) next[sel] = held;
        else delete next[sel];
      } else for (const t of here) next[t] = held;   // null: this one is left be
    }
    modes[m] = { ...modes[m], state: next };
    out.push(`${label(m)} no longer puts back what ${label(name)} saved (${lost.map(([t]) => nameOf(t)).join(", ")}).`);
  }
  return out;
}

/**
 * Delete mode `name` from `cfg`, with everything that names it, so the room never refuses the save: another start mode
 * (`startIn`) if it was the room's default, its name out of other modes' Auto entries (autopilot.from, leave_to) and
 * out of the switching-order rules, and no half store/retrieve pair (unpair). Returns what else changed, in words.
 */
function removeMode(
  cfg: RawRoomConfig,
  name: string,
  startIn: string | null,
  describe: RoomDescribe | null | undefined,
  label: (m: string) => string,
  nameOf: (t: string) => string,
): string[] {
  const out: string[] = [];
  const gone = label(name);
  const modes = { ...(cfg.modes ?? {}) };
  delete modes[name];
  if (startIn && startIn in modes) {
    cfg.default = startIn;
    out.push(`The room starts in ${label(startIn)} now.`);
  }
  for (const [m, def] of Object.entries(modes)) {
    if (!isObj(def.autopilot)) continue;
    const ap: Json = { ...def.autopilot };
    let changed = false;
    if (ap.from === name || (Array.isArray(ap.from) && ap.from.includes(name))) {
      const rest = Array.isArray(ap.from) ? (ap.from as unknown[]).filter((x) => x !== name) : [];
      if (rest.length) {
        ap.from = rest;
        out.push(`${label(m)} no longer switches on by itself from ${gone}.`);
      } else {
        // It could only take over from the deleted mode. An empty or missing "from" means "from any mode" (show_service),
        // so it would start from anywhere: stop it starting by itself instead, and say so. (Its way back stays.)
        delete ap.from;
        delete ap.enter_when;
        delete ap.otherwise_suggest_when;
        out.push(`${label(m)} won't switch on by itself any more: it could only take over from ${gone}.`);
      }
      changed = true;
    }
    if (ap.leave_to === name) {
      ap.leave_to = "previous";
      out.push(`When ${label(m)} ends by itself, the room goes back to the mode it was in before.`);
      changed = true;
    }
    if (changed) {
      const next = { ...def, autopilot: ap };
      if (!Object.keys(ap).length) delete (next as Json).autopilot;
      modes[m] = next;
    }
  }
  if (Array.isArray(cfg.transitions)) {
    const names = (x: unknown) => x === name || x === `@${name}`;
    let touched = false;
    const rules = (cfg.transitions as unknown[]).flatMap((r) => {
      if (!isObj(r)) return [r];
      const rule: Json = { ...r };
      for (const k of ["from", "to"]) {
        const v = rule[k];
        if (typeof v === "string" && names(v)) {
          touched = true;
          return [];
        }
        if (Array.isArray(v) && v.some(names)) {
          touched = true;
          const rest = v.filter((x) => !names(x));
          if (!rest.length) return [];
          rule[k] = rest;
        }
      }
      return [rule];
    });
    if (touched) {
      cfg.transitions = rules;
      out.push(`The room's own order for switching to or from ${gone} goes too.`);
    }
  }
  out.push(...unpair(modes, name, describe, label, nameOf));
  cfg.modes = modes;
  return out;
}

/** The room's mode icons by their modes.json name (room_service's ICONS; the editor's picker draws these). */
export const MODE_ICONS: Record<string, LucideIcon> = {
  home: House,
  music: Music,
  piano: Piano,
  party: PartyPopper,
  disco: Disc3,
  film: Clapperboard,
  moon: Moon,
  sun: Sun,
  sparkles: Sparkles,
  power: Power,
  lamp: Lamp,
  image: ImageIcon,
  waves: Waves,
  grid: LayoutGrid,
  flask: FlaskConical,
};

/** A mode's icon: its own ("icon" in modes.json), else a fitting one guessed from its name and label. */
export function modeIcon(name: string, def?: { label?: string; icon?: unknown } | null): LucideIcon {
  const own = typeof def?.icon === "string" ? MODE_ICONS[def.icon] : undefined;
  return own ?? guessIcon(name, def?.label);
}

/** The guess, from words in the mode's name and label, else a neutral one. */
function guessIcon(name: string, label?: string): LucideIcon {
  const s = `${name} ${label ?? ""}`.toLowerCase().replace(/[-_]/g, " ");
  if (/\b(disco|party|dance|club)\b/.test(s)) return PartyPopper;
  if (/\b(music|sonos|piano|tracks?|songs?|listen|radio)\b/.test(s)) return Music;
  if (/\b(night|sleep|off|rest|quiet|bed)\b/.test(s)) return Moon;
  if (/\b(movies?|films?|cinema|watch)\b/.test(s)) return Clapperboard;
  if (/\b(gallery|art|frames?|pictures?|photos?)\b/.test(s)) return ImageIcon;
  if (/\b(live|show|perform|set|visuals?)\b/.test(s)) return Sparkles;
  return LayoutGrid;
}

/** A save the room turned down, in words (the server's own list goes to the activity log). */
function saveError(e: unknown): string {
  if (unreachable(e)) return "The room isn't answering. Try again in a moment.";
  if (e instanceof RoomApiError && e.status === 409) return "Someone else changed the modes just now. Try again.";
  if (e instanceof RoomApiError && e.errors?.length) return "The room's checks found a problem with that, so nothing changed.";
  return explainError(e);
}

interface Toast {
  id: number;
  text: string;
  lines?: string[];
  ms: number;
  undo?: () => void;
}

export function ModeSwitcher({
  api,
  modes,
  modesError,
  mode,
  transition,
  catalog,
  connected,
  now,
  addLog,
  reportError,
  onEdit,
  describe,
  subsystems,
  names,
  onChanged,
}: {
  api: RoomApi;
  modes: Record<string, ModeDef> | null;
  /** The last modes read failed (the last good list, if any, is still shown). */
  modesError: string | null;
  mode: ModeStatus | undefined;
  transition: TrackedTransition | null;
  catalog: Catalog | null;
  connected: boolean;
  now: number;
  addLog: (e: NewLogEntry) => void;
  reportError: (e: unknown) => void;
  /** Open the editor for a mode (the tile's "⋯"); without it there is no "⋯". */
  onEdit?: (name: string) => void;
  /** GET /room/describe: each tile says what its mode changes in the room. */
  describe?: RoomDescribe | null;
  /** status.subsystems: what the devices are doing now (the progress line names what's still changing). */
  subsystems?: RoomStatus["subsystems"];
  /** Names that beat a target's own label (the screens' names), by target. */
  names?: Record<string, string>;
  /** The modes file was saved here (a mode added, copied, moved or deleted): re-read modes and describe. */
  onChanged?: () => void;
}) {
  const [busy, setBusy] = useState<string | null>(null);
  const [trying, setTrying] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const scenes = useMemo(() => sceneChoices(catalog), [catalog]);
  const tap = useRef(0);
  const alive = useRef(true);
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, []);

  const current = mode?.current ?? null;
  const target = mode?.target ?? null;
  const defaultMode = mode?.default ?? (describe as (RoomDescribe & { default?: string | null }) | null | undefined)?.default ?? null;
  const entries = Object.entries(modes ?? {});
  const wordsOpts = { names, live: subsystems };
  const nameOf = (t: string) => (describe ? targetName(describe, t, names) : t);

  const [toast, setToast] = useState<Toast | null>(null);
  const showToast = (t: Omit<Toast, "id">) => setToast({ ...t, id: Date.now() + Math.random() });
  useEffect(() => {
    if (!toast) return;
    const id = setTimeout(() => setToast((t) => (t?.id === toast.id ? null : t)), toast.ms);
    return () => clearTimeout(id);
  }, [toast]);

  // Modes are the owner's config (16 Oct): every mode is flat and complete, so a new one is a full copy of another.
  // Saved like the editor's saves: the whole file, refused if someone saved meanwhile.
  const editModes = async (
    fn: (cfg: RawRoomConfig) => string[] | void,
    note: string,
  ): Promise<{ previous: RawRoomConfig; revision: string; changes: string[] } | null> => {
    setError(null);
    try {
      const { config, revision } = await api.roomConfig();
      const next = clone(config);
      const changes = fn(next) || [];
      const saved = await api.saveRoomConfig(next, revision, note);
      addLog({ type: "ui", summary: note });
      onChanged?.();
      return { previous: config, revision: saved?.revision ?? "", changes };
    } catch (e) {
      setError(saveError(e));
      addLog({
        type: "ui",
        level: "error",
        summary: `${note}: refused: ${e instanceof RoomApiError && e.errors?.length ? e.errors.map((x) => `${x.path} ${x.message}`).join("; ") : explainError(e)}`,
      });
      return null;
    }
  };

  const duplicate = async (name: string, asNew = false) => {
    let id = "";
    let hadTrigger = false;
    const ok = await editModes((cfg) => {
      const all = cfg.modes ?? {};
      const src = all[name] ?? {};
      const label = asNew ? "New mode" : `${src.label || modeLabel(name, modes)} (copy)`;
      id = newModeId(label, all);
      const c = copyMode(src, label);
      hadTrigger = c.hadTrigger;
      cfg.modes = insertAfter(all, name, id, c.copy);
    }, `mode ${asNew ? "added" : "copied"} on the Room page`);
    if (!ok || !id) return;
    if (hadTrigger) showToast({ text: "Copied without its switch-by-itself trigger", ms: 6_000 });
    onEdit?.(id);
  };

  const move = (name: string, by: -1 | 1) =>
    void editModes((cfg) => {
      const keys = Object.keys(cfg.modes ?? {});
      const shown = keys.filter((k) => !!modes && k in modes);
      const i = shown.indexOf(name);
      const j = i + by;
      if (i < 0 || j < 0 || j >= shown.length) return;
      const a = keys.indexOf(name);
      const b = keys.indexOf(shown[j]);
      [keys[a], keys[b]] = [keys[b], keys[a]];
      const all = cfg.modes ?? {};
      cfg.modes = Object.fromEntries(keys.map((k) => [k, all[k]]));
    }, `mode ${name} moved ${by < 0 ? "earlier" : "later"} on the Room page`);

  // "Start here": the room's start mode (modes.json "default"), the one it comes up in after a restart.
  const startHere = async (name: string) => {
    const ok = await editModes((cfg) => {
      cfg.default = name;
    }, `room starts in ${name} (set on the Room page)`);
    if (ok) showToast({ text: `The room starts in ${modeLabel(name, modes)} now`, ms: 4_000 });
  };

  const addFrom = current && modes?.[current] ? current : (entries[0]?.[0] ?? null);
  const add = () => {
    if (addFrom) void duplicate(addFrom, true);
  };

  // The row: modes marked "more" (on_room_page) wait behind "More modes", unless the room is in or going to one.
  const [editAll, setEditAll] = useState(false);
  const [showMore, setShowMore] = useState(false);
  const isMore = (def: ModeDef) => (def as ModeDef & { on_room_page?: unknown }).on_room_page === "more";
  const pinned = (name: string) => name === current || name === target || name === busy;
  const main = entries.filter(([n, d]) => !isMore(d) || pinned(n));
  const extra = entries.filter(([n, d]) => isMore(d) && !pinned(n));
  const chips = showMore ? [...main, ...extra] : main;
  const shownLine = describe && current ? modeLine(describe, current, wordsOpts) : "";
  const currentDesc = current && modes?.[current] ? modeDescription(modes[current]) : undefined;
  const lineText = shownLine || currentDesc || "";

  // Keep the current chip in view in the sideways row (only the row scrolls, never the page).
  const rowRef = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    const row = rowRef.current;
    const el = row?.querySelector<HTMLElement>('[aria-pressed="true"]')?.parentElement;
    if (!row || !el) return;
    const left = el.offsetLeft - row.offsetLeft;
    if (left < row.scrollLeft || left + el.offsetWidth > row.scrollLeft + row.clientWidth) {
      row.scrollTo({ left: Math.max(0, left - 16), behavior: "smooth" });
    }
  }, [current, chips.length]);

  const sheetRows: EditModesRow[] = entries.map(([name, def]) => ({
    name,
    label: def.label || modeLabel(name, modes),
    icon: modeIcon(name, def as ModeDef & { icon?: unknown }),
    isDefault: name === defaultMode,
    isCurrent: name === current,
    more: isMore(def),
  }));

  // Delete is never refused: the mode the room is in is switched away from first, the start mode is replaced, and
  // whatever names it is tidied in the same save (removeMode). Undo saves the file as it was.
  const [deleting, setDeleting] = useState<string | null>(null);
  const [startIn, setStartIn] = useState("");
  const [preview, setPreview] = useState<string[] | null>(null);
  const asked = useRef(0);
  const labelIn = (cfg: RawRoomConfig) => (m: string) => cfg.modes?.[m]?.label || modeLabel(m, modes);
  const askDelete = (name: string) => {
    const n = ++asked.current;
    setDeleting(name);
    setStartIn(entries.map(([k]) => k).find((k) => k !== name) ?? "");
    setPreview(null);
    api
      .roomConfig()
      .then(({ config }) => {
        const c = clone(config);
        if (n === asked.current) setPreview(removeMode(c, name, null, describe, labelIn(c), nameOf));
      })
      .catch(() => n === asked.current && setPreview([]));
  };
  const dLabel = deleting ? modeLabel(deleting, modes) : "";
  const dIsDefault = !!deleting && deleting === defaultMode;
  const dIsCurrent = !!deleting && (deleting === current || deleting === target);
  const firstOther = entries.map(([k]) => k).find((k) => k !== deleting) ?? null;
  const away = !dIsCurrent ? null : dIsDefault ? startIn || firstOther : defaultMode && defaultMode !== deleting ? defaultMode : firstOther;
  const deleteWords = dIsCurrent
    ? dIsDefault
      ? `Switch to ${modeLabel(away, modes)} and start there, then delete`
      : `Switch to ${modeLabel(away, modes)}, then delete`
    : dIsDefault
      ? `Start in ${modeLabel(startIn, modes)} instead, then delete`
      : "Delete";

  const undoDelete = async (label: string, previous: RawRoomConfig, revision: string) => {
    showToast({ text: `Putting ${label} back…`, ms: UNDO_MS });
    try {
      await api.saveRoomConfig(previous, revision, `mode delete undone on the Room page`);
      addLog({ type: "ui", summary: `${label}: delete undone on the Room page` });
      onChanged?.();
      showToast({ text: `${label} is back`, ms: 4_000 });
    } catch (e) {
      setToast(null);
      setError(
        e instanceof RoomApiError && e.status === 409
          ? `The modes changed since, so ${label} can't be put back from here.`
          : saveError(e),
      );
    }
  };

  const remove = async (name: string, startAt: string | null, switchTo: string | null) => {
    const label = modeLabel(name, modes);
    if (switchTo) {
      try {
        await api.setMode(switchTo, "delete-mode");
        addLog({ type: "ui", summary: `mode → ${switchTo} (before deleting ${name})` });
      } catch (e) {
        setError(`${unreachable(e) ? "The room isn't answering" : explainError(e)}, so ${label} is still there.`);
        return;
      }
    }
    const r = await editModes(
      (cfg) => {
        const wasDefault = cfg.default === name || (cfg.default === undefined && defaultMode === name);
        return removeMode(cfg, name, wasDefault ? startAt : null, describe, labelIn(cfg), nameOf);
      },
      `mode ${name} deleted on the Room page`,
    );
    if (!r) return;
    showToast({ text: `${label} deleted`, lines: r.changes, ms: UNDO_MS, undo: () => void undoDelete(label, r.previous, r.revision) });
  };

  // The newest tap wins: an older tap's answer (or its retries) never overwrites a newer one's. A tap the room doesn't
  // answer is sent again every 2 s for a minute.
  const choose = async (name: string) => {
    const mine = ++tap.current;
    const started = Date.now();
    setBusy(name);
    setError(null);
    setTrying(!connected);
    for (;;) {
      try {
        const r = await api.setMode(name, "mode-tile");
        if (mine !== tap.current) return;
        addLog({ type: "ui", summary: `mode → ${name} accepted${r?.transition_id ? ` (${r.transition_id})` : ""}` });
        break;
      } catch (e) {
        if (mine !== tap.current) return;
        if (unreachable(e) && Date.now() - started < TRY_FOR_MS && alive.current) {
          setTrying(true);
          await sleep(RETRY_MS);
          if (mine !== tap.current || !alive.current) return;
          continue;
        }
        reportError(e);
        const msg = unreachable(e) ? "The room didn't answer. Tap the mode to try again." : explainError(e);
        setError(msg);
        addLog({ type: "ui", level: "error", summary: `mode → ${name} refused: ${explainError(e)}` });
        break;
      }
    }
    setBusy(null);
    setTrying(false);
  };

  return (
    <div className="space-y-3" data-testid="mode-switcher">
      <span className="sr-only" data-testid="mode-current">
        {current ?? "—"}
      </span>

      {modesError && (
        <Notice tone={modes ? "warn" : "bad"}>
          {modes ? "Couldn't refresh the modes; showing the last list." : "The modes couldn't be loaded."}{" "}
          <span className="opacity-80">{modesError}</span>
        </Notice>
      )}

      {modes === null ? (
        !modesError && (
          <p className="flex items-center gap-2 text-base text-muted-foreground">
            <Spinner /> Loading modes…
          </p>
        )
      ) : entries.length === 0 ? (
        <p className="text-base text-muted-foreground">No modes are set up yet.</p>
      ) : (
        <>
          <div className="flex items-center gap-1">
            <div
              ref={rowRef}
              role="group"
              aria-label="Modes"
              data-testid="mode-strip"
              className="flex min-w-0 flex-1 flex-wrap gap-2 px-0.5 py-1"
            >
              {chips.map(([name, def]) => {
                const i = entries.findIndex(([k]) => k === name);
                const isCurrent = name === current;
                const isTarget = name === target && target !== current;
                const label = def.label || modeLabel(name, modes);
                const Icon = modeIcon(name, def as ModeDef & { icon?: unknown });
                const switching = !isCurrent && (isTarget || busy === name);
                return (
                  <div
                    key={name}
                    className={cn(
                      "flex shrink-0 snap-start items-center rounded-full border shadow-sm transition-colors motion-reduce:transition-none",
                      isCurrent
                        ? "border-teal-700 bg-teal-700 text-white"
                        : isTarget
                          ? "border-amber-300 bg-amber-50 text-amber-900"
                          : "border-stone-200/80 bg-card text-stone-800 hover:border-stone-300 hover:bg-stone-50",
                    )}
                  >
                    <button
                      type="button"
                      data-mode={name}
                      aria-pressed={isCurrent}
                      title={isCurrent ? `${label} (on now)` : `Switch to ${label}`}
                      onClick={() => void choose(name)}
                      className={cn(
                        "flex min-h-11 items-center gap-2 whitespace-nowrap rounded-full pl-3.5 text-[15px] font-semibold",
                        onEdit ? "pr-1" : "pr-4",
                        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background",
                      )}
                    >
                      {switching ? <Spinner className="h-[18px] w-[18px]" /> : <Icon className="h-[18px] w-[18px] shrink-0" aria-hidden />}
                      <span>{label}</span>
                      {isCurrent && <Check className="h-4 w-4 shrink-0" aria-label="on now" />}
                    </button>
                    {onEdit && (
                      <DropdownMenu>
                        <DropdownMenuTrigger
                          aria-label={`${label}: edit, copy, start here, move or delete`}
                          title={`Edit, copy, start here, move or delete ${label}`}
                          className={cn(
                            "grid h-11 w-9 shrink-0 place-items-center rounded-full focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                            isCurrent ? "text-white/80 hover:text-white" : "text-stone-500 hover:text-stone-800",
                          )}
                        >
                          <MoreHorizontal className="h-[18px] w-[18px]" aria-hidden />
                        </DropdownMenuTrigger>
                        <DropdownMenuContent align="end" className={cn(THEME_CLASS, "min-w-[11rem]")}>
                          <DropdownMenuItem className="min-h-11 text-base" onSelect={() => onEdit(name)}>
                            Edit
                          </DropdownMenuItem>
                          <DropdownMenuItem className="min-h-11 text-base" onSelect={() => void duplicate(name)}>
                            Duplicate
                          </DropdownMenuItem>
                          {name === defaultMode ? (
                            <DropdownMenuLabel className="flex min-h-11 items-center gap-1.5 text-base font-normal text-muted-foreground">
                              <Check className="h-4 w-4" aria-hidden /> The room starts here
                            </DropdownMenuLabel>
                          ) : (
                            <DropdownMenuItem className="min-h-11 text-base" onSelect={() => void startHere(name)}>
                              Start here
                            </DropdownMenuItem>
                          )}
                          {i > 0 && (
                            <DropdownMenuItem className="min-h-11 text-base" onSelect={() => move(name, -1)}>
                              Move earlier
                            </DropdownMenuItem>
                          )}
                          {i < entries.length - 1 && (
                            <DropdownMenuItem className="min-h-11 text-base" onSelect={() => move(name, 1)}>
                              Move later
                            </DropdownMenuItem>
                          )}
                          <DropdownMenuSeparator />
                          <DropdownMenuItem className="min-h-11 text-base text-rose-700 focus:text-rose-800" onSelect={() => askDelete(name)}>
                            Delete…
                          </DropdownMenuItem>
                        </DropdownMenuContent>
                      </DropdownMenu>
                    )}
                  </div>
                );
              })}
              {extra.length > 0 && (
                <button
                  type="button"
                  aria-expanded={showMore}
                  onClick={() => setShowMore((v) => !v)}
                  className="flex min-h-11 shrink-0 snap-start items-center gap-1 whitespace-nowrap rounded-full border border-dashed border-stone-300 px-3.5 text-[15px] text-stone-700 hover:bg-stone-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                >
                  {showMore ? (
                    <>
                      <ChevronLeft className="h-4 w-4" aria-hidden /> Fewer modes
                    </>
                  ) : (
                    <>
                      More modes <ChevronRight className="h-4 w-4" aria-hidden />
                    </>
                  )}
                </button>
              )}
            </div>
            {onEdit && (
              <button
                type="button"
                onClick={() => setEditAll(true)}
                aria-label="Edit modes"
                title="Edit modes: change, copy, order, delete or add"
                className="grid h-11 w-11 shrink-0 place-items-center rounded-full text-stone-600 hover:bg-stone-200/60 hover:text-stone-900 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              >
                <Pencil className="h-[18px] w-[18px]" aria-hidden />
              </button>
            )}
          </div>
          {lineText && (
            <p className="text-[15px] leading-snug text-stone-600 break-words" data-testid="mode-line">
              {lineText}
            </p>
          )}
        </>
      )}

      {onEdit && (
        <EditModesSheet
          open={editAll}
          onOpenChange={setEditAll}
          rows={sheetRows}
          addFrom={addFrom ? modeLabel(addFrom, modes) : null}
          onEdit={(n) => {
            setEditAll(false);
            onEdit(n);
          }}
          onDuplicate={(n) => {
            setEditAll(false);
            void duplicate(n);
          }}
          onStartHere={(n) => void startHere(n)}
          onMove={move}
          onDelete={askDelete}
          onAdd={() => {
            setEditAll(false);
            add();
          }}
        />
      )}

      <AlertDialog open={!!deleting} onOpenChange={(o) => !o && setDeleting(null)}>
        <AlertDialogContent className={cn(THEME_CLASS, "rounded-2xl")}>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete {dLabel}?</AlertDialogTitle>
            <AlertDialogDescription className="text-base">
              {dLabel} goes for everyone. You can undo for a few seconds.
            </AlertDialogDescription>
          </AlertDialogHeader>
          {dIsCurrent && away && (
            <p className="text-[15px]">
              The room is in {dLabel} now, so it switches to {modeLabel(away, modes)} first.
            </p>
          )}
          {dIsDefault && firstOther && (
            <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-[15px]">
              <span>The room starts in {dLabel}. Start in</span>
              <Select value={startIn} onValueChange={setStartIn}>
                <SelectTrigger aria-label="Start in" className="h-11 w-auto min-w-[9rem] rounded-xl bg-white text-base">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent className={cn(THEME_CLASS, "rounded-xl")}>
                  {entries
                    .filter(([k]) => k !== deleting)
                    .map(([k]) => (
                      <SelectItem key={k} value={k} className="min-h-11 rounded-lg text-base">
                        {modeLabel(k, modes)}
                      </SelectItem>
                    ))}
                </SelectContent>
              </Select>
              <span>instead, then delete.</span>
            </div>
          )}
          {preview && preview.length > 0 && (
            <div className="text-[15px]">
              <p className="font-medium">This also changes:</p>
              <ul className="mt-1 list-disc space-y-0.5 pl-5 text-muted-foreground">
                {preview.map((p) => (
                  <li key={p}>{p}</li>
                ))}
              </ul>
            </div>
          )}
          <AlertDialogFooter>
            <AlertDialogCancel className="h-11 rounded-xl text-base">Keep it</AlertDialogCancel>
            <AlertDialogAction
              className="h-11 rounded-xl bg-rose-700 text-base hover:bg-rose-800"
              onClick={() => {
                const n = deleting;
                setDeleting(null);
                // The start mode goes along whatever status said: the save itself checks whether it was the default.
                if (n) void remove(n, startIn || firstOther, away);
              }}
            >
              {deleteWords}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      {trying && busy && (
        <Notice tone="warn" className="flex items-center gap-2 text-base">
          <Spinner /> The room isn't answering; trying…
        </Notice>
      )}
      {error && <Notice tone="bad">{error}</Notice>}

      <ModeProgress
        transition={transition}
        mode={mode}
        modes={modes}
        now={now}
        scenes={scenes}
        describe={describe}
        subsystems={subsystems}
        names={names}
      />

      {toast && (
        <div
          role="status"
          aria-live="polite"
          data-testid="mode-toast"
          className="fixed inset-x-4 bottom-4 z-[60] mx-auto max-w-md rounded-2xl border border-stone-200 bg-white px-4 py-3 text-foreground shadow-lg"
        >
          <div className="flex items-start gap-2">
            <div className="min-w-0 flex-1 py-1.5">
              <p className="text-base font-semibold leading-snug">{toast.text}</p>
              {toast.lines?.map((l) => (
                <p key={l} className="mt-0.5 text-sm leading-snug text-muted-foreground">
                  {l}
                </p>
              ))}
            </div>
            {toast.undo && (
              <button
                type="button"
                onClick={toast.undo}
                className="h-11 shrink-0 rounded-xl px-3 text-base font-semibold text-teal-800 underline underline-offset-2 hover:bg-teal-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              >
                Undo
              </button>
            )}
            <button
              type="button"
              aria-label="Dismiss"
              onClick={() => setToast(null)}
              className="grid h-11 w-11 shrink-0 place-items-center rounded-full text-stone-500 hover:bg-stone-100 hover:text-stone-800 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            >
              <X className="h-5 w-5" aria-hidden />
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

/** Auto's reason, when it reads as words (never a code or a blob). */
function plainReason(r: unknown): string | null {
  if (typeof r !== "string") return null;
  const s = r.replace(/\s*\([^()\s]+\/[a-z_]+\)\s*$/, "").trim();
  return s && s.length <= 100 && !/[{}[\]<>=_]|https?:/.test(s) ? s : null;
}

/** The one plain line under the modes: switching (and what that's doing to the room), switched with problems, or
 *  interrupted; and after Auto switched, "Auto: Now <mode>" with its reason. A clean switch by hand shows nothing (the
 *  chip says it). */
function ModeProgress({
  transition,
  mode,
  modes,
  now,
  scenes,
  describe,
  subsystems,
  names,
}: {
  transition: TrackedTransition | null;
  mode: ModeStatus | undefined;
  modes: Record<string, ModeDef> | null;
  now: number;
  scenes: SceneChoice[];
  describe?: RoomDescribe | null;
  subsystems?: RoomStatus["subsystems"];
  names?: Record<string, string>;
}) {
  if (!transition) return null;
  const steps = transition.steps ?? [];
  const failed = steps.filter((s) => s.status === "failed").length;
  // a screen still going live past its deadline is in progress, not "not confirmed"
  const goingLive = steps.filter(stepGoingLive).length;
  const unchecked = steps.filter((s) => s.status === "unconfirmed" && !stepGoingLive(s)).length;
  const finished = transition.phase === "done" || transition.phase === "cancelled";
  const age = finished && transition.finishedAt ? now - transition.finishedAt : 0;
  const to = modeLabel(transition.to, modes);
  // The started event doesn't carry who asked; status does, under the same id.
  const st = [mode?.transition, mode?.last_transition].find((t) => t?.id === transition.id);
  const source = transition.source ?? st?.source;
  const auto = source === "autopilot";
  // How the switch ended (ok | partial | failed), from the done event or status; its detail is never shown.
  const outcome = transition.status ?? st?.status;

  let tone: Tone;
  let text: string;
  let sub: string | undefined;
  if (!finished) {
    tone = "warn";
    const doing = switchingWords(describe, transition.to, {
      names,
      live: subsystems,
      steps,
      skip: transition.skip ?? st?.skip,
    });
    text = doing ? `Switching to ${to}` : `Switching to ${to}…`;
    const open = steps.filter((s) => s.status === "pending" || s.status === "running");
    sub =
      doing ||
      (open.some((s) => s.subsystem?.startsWith("screens."))
        ? "screens changing"
        : open.length
          ? "nearly there"
          : steps.length
            ? "finishing"
            : "getting ready");
  } else if (transition.phase === "cancelled") {
    if (age > DONE_SHOWN_MS) return null;
    tone = "muted";
    text = `The switch to ${to} was interrupted`;
  } else if (outcome === "failed") {
    if (age > PROBLEM_SHOWN_MS) return null;
    tone = "bad";
    text = `Couldn't switch to ${to}`;
    sub = `still ${modeLabel(mode?.current, modes)}`;
  } else if (failed) {
    if (age > PROBLEM_SHOWN_MS) return null;
    // Amber when most of it worked; rose only when none of it did.
    const worked = steps.some((s) => s.status === "ok" || s.status === "unconfirmed");
    tone = worked ? "warn" : "bad";
    const first = steps.find((s) => s.status === "failed");
    const why = first ? (stepSentence(describe, first, names)?.failure ?? stepFailure(first)) : null;
    text = `${to} is on, but ${failed === 1 && why ? why : `${failed} things didn't work`}`;
  } else {
    // a clean switch by hand: the chip already says it; only Auto's own switch is told (with its reason)
    if (!auto || age > AUTO_SHOWN_MS) return null;
    tone = "ok";
    text = `Now ${to}`;
    if (unchecked) sub = `${unchecked} not confirmed`;
    if (goingLive) sub = [goingLive === 1 ? "a screen still going live" : `${goingLive} screens still going live`, sub].filter(Boolean).join(" · ");
  }
  if (auto) {
    text = `Auto: ${text}`;
    const why = plainReason(transition.reason ?? st?.reason);
    if (why) sub = sub ? `${why} · ${sub}` : why;
  }

  return (
    <Collapsible
      data-testid="mode-transition"
      data-phase={transition.phase}
      className={cn("rounded-2xl border px-4 py-3", TONE_CLASSES[tone])}
    >
      <div className="flex min-h-8 items-center gap-2.5" role="status" aria-live="polite">
        {!finished ? (
          <Spinner className="h-5 w-5" />
        ) : (failed || outcome === "failed") && transition.phase === "done" ? (
          <CircleAlert className="h-5 w-5 shrink-0" aria-hidden />
        ) : tone === "ok" ? (
          <CircleCheck className="h-5 w-5 shrink-0" aria-hidden />
        ) : (
          <Minus className="h-5 w-5 shrink-0" aria-hidden />
        )}
        <p className="min-w-0 flex-1 text-base leading-snug">
          <span className="font-semibold">{text}</span>
          {sub && <span> · {sub}</span>}
        </p>
        {steps.length > 0 && (
          <CollapsibleTrigger className="-my-2 h-11 shrink-0 rounded-lg px-2 text-sm font-medium underline underline-offset-2 hover:bg-white/50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring data-[state=open]:no-underline">
            Details
          </CollapsibleTrigger>
        )}
      </div>
      <CollapsibleContent>
        <StepList steps={steps} scenes={scenes} describe={describe} names={names} className="mt-3" />
      </CollapsibleContent>
    </Collapsible>
  );
}

function StepIcon({ status }: { status: string }) {
  const cls = cn("mt-0.5 h-4 w-4 shrink-0", TEXT_CLASSES[stepTone(status)]);
  switch (status) {
    case "running":
      return <Spinner className={cls} />;
    case "ok":
      return <CircleCheck className={cls} aria-hidden />;
    case "failed":
      return <X className={cls} aria-hidden />;
    case "unconfirmed":
      return <CircleHelp className={cls} aria-hidden />;
    case "cancelled":
    case "skipped":
      return <Minus className={cls} aria-hidden />;
    default:
      return <CircleDashed className={cls} aria-hidden />;
  }
}

/** A step's status in words; a step the room left alone says why. */
function stepStatusWords(s: TransitionStep): string {
  const reason = (s as TransitionStep & { reason?: string }).reason;
  if (reason === "unavailable") return "The room can't switch it yet";
  if (reason === "unknown_state") return "Not sent: on or off not known";
  if (s.detail === "dry run") return "Not wired up yet";
  if (stepGoingLive(s)) return "Going live…";
  if (reason === "hand") return "Left as you set it";
  if (reason === "left_alone") return "Left alone";
  if (reason === "quiet_before" || s.detail === "re-apply") return "Not needed";
  return STEP_WORDS[s.status] ?? "Other";
}

/** A mode change's steps, in words (device names and values from describe when it has them). */
export function StepList({
  steps,
  scenes,
  describe,
  names,
  className,
}: {
  steps: TransitionStep[];
  scenes: SceneChoice[];
  describe?: RoomDescribe | null;
  names?: Record<string, string>;
  className?: string;
}) {
  if (!steps.length) {
    return <p className={cn("text-sm text-muted-foreground", className)}>Nothing needed changing.</p>;
  }
  return (
    <ol className={cn("space-y-1.5", className)}>
      {steps.map((s, i) => (
        <li
          key={s.id ?? `${s.subsystem}-${s.action}-${i}`}
          data-step-status={s.status}
          className="flex items-start gap-2.5 rounded-xl bg-white/80 px-3 py-2 text-sm text-foreground"
        >
          <StepIcon status={stepGoingLive(s) ? "running" : s.status} />
          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-baseline justify-between gap-x-3">
              <span className={cn("font-medium", (s.status === "cancelled" || s.status === "skipped") && "opacity-60", s.status === "cancelled" && "line-through")}>
                {stepSentence(describe, s, names)?.text ?? stepText(s)}
              </span>
              <span className={cn("text-[13px] font-medium", TEXT_CLASSES[stepTone(stepGoingLive(s) ? "running" : s.status)])}>
                {stepStatusWords(s)}
              </span>
            </div>
            {s.status === "failed" && s.detail && (
              <div className="text-xs text-muted-foreground [overflow-wrap:anywhere]">{s.detail}</div>
            )}
          </div>
        </li>
      ))}
    </ol>
  );
}
