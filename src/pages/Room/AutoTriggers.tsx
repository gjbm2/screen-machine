// "Starts by itself": what makes Auto switch the room to this mode, and back, all from config: the mode's own
// `autopilot` block in modes.json (docs/SHOW_ENGINE.md §8.9). The owner asked for it on 17 Oct. It sits at the end of the
// mode editor's "For the whole mode" and is saved with the rest of the mode (PUT /api/v1/room/config).
//
// Every word comes from show_service's vocabulary (GET /show/api/v1/situations through room_service's pass-through, or
// describe's x-ui.situations when show_service isn't answering), every mode from modes.json, every device from describe:
// no situation, mode or device is named here.
//
// Conditions are show_service's rule grammar (situations.py Conditions): a word {"<id>": {params…, for_s?}}, or
// {"all": [...]}, {"any": [...]}, {"not": node}. One all/any list of words, each optionally negated, is drawn as a list;
// anything deeper is shown in words and kept exactly as it is (it can be removed, and "Edit as a list" is offered only
// where flattening means the same: an all inside an all, an any inside an any, several words inside an all).
//
// Live preview: GET /show/api/v1/autopilot/rules (the saved block, as Auto runs it) and POST /show/api/v1/autopilot/check
// (the draft, debounced; never saves) give each node's truth now, its sentence and its problems, by path
// ("enter_when.any[1]", "leave_when.all[0].not", "from"). Until they answer, the page writes its own sentences from the
// vocabulary and takes "true now" from the vocabulary's readings for words left at their usual settings.
//
// Empty is written as nothing: no enter_when = only when someone picks it, no from = from any mode, no leave_when = Auto
// never leaves it, no leave_to = back to the mode it came from; once everything is cleared the `autopilot` key goes too.
// show_service reads null and [] the same as absent (autopilot.py rule_of, from_of, leave_to_of), and so does this page.

import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { ChevronDown, X } from "lucide-react";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";
import type { RoomApi } from "./api";
import { Dot, Notice } from "./bits";
import { kindWords, setTargets, targetsOf } from "./ModeTargets";
import { CANT_SHOW, inWords, jsonKey, ValueControl, type Schema, type XUi } from "./SchemaControls";
import type { RawMode, RawRoomConfig, RoomDescribe, RoomStatus } from "./types";

type Json = Record<string, unknown>;
type CondKey = "enter_when" | "otherwise_suggest_when" | "leave_when";
type Combine = "all" | "any";
type ParamUi = XUi & { required?: boolean };
/** A word's param: a JSON schema with x-ui ("nullable": null is allowed and means its unset_label). */
type Param = Omit<Schema, "x-ui" | "items"> & { nullable?: boolean; format?: string; items?: Param; "x-ui"?: ParamUi };

/** One word of the vocabulary (GET /show/api/v1/situations). */
interface Word {
  id: string;
  label?: string;
  description?: string;
  available?: boolean;
  params?: Record<string, Param>;
}

type Problem = { path: string; text: string; level: "error" | "warn" };

/** What show_service says about a block: problems, truth now and sentences, by node path. */
interface Preview {
  /** The block it was worked out for (jsonKey), so an answer for an older draft never lands on the wrong row. */
  of: string;
  problems: Problem[];
  live: Record<string, boolean | null>;
  nodes: Record<string, string>;
  leaveTo?: string;
}

/** A condition drawn as a list: an all/any's children, or the one node itself. */
interface ListView {
  combine: Combine | null;
  rows: unknown[];
  /** The all/any's own "for at least" (kept, shown in words). */
  groupFor?: unknown;
  isList: boolean;
}

/** One word, optionally negated: the rows this page edits. */
interface RowView {
  word: string;
  params: Json;
  negated: boolean;
  /** The not's own "for at least". */
  notFor?: unknown;
}

const AWAY = "Auto's side isn't answering; your changes are kept.";
const CANT_SENSE = "can't be sensed right now";
const SOMETHING = "something this page can't name";
const POLL_MS = 5000;
const TIMEOUT_MS = 8000;
/** Words that read as code (a key, a file, a pointer): never shown. */
const CODE = /[A-Za-z0-9]_[A-Za-z0-9]|\.(ya?ml|json|py)\b|[{}[\]]|(^|[\s("“])\/\w|\w\/\w+\//;
const ORDER: CondKey[] = ["enter_when", "otherwise_suggest_when", "leave_when"];

const isObj = (v: unknown): v is Json => !!v && typeof v === "object" && !Array.isArray(v);
const empty = (v: unknown) => v === undefined || v === null || (isObj(v) && !Object.keys(v).length);
const is = (s: { type?: string | string[] } | undefined, t: string) => (Array.isArray(s?.type) ? s.type.includes(t) : s?.type === t);
const ui = (s: Param | undefined): ParamUi => s?.["x-ui"] ?? {};

function clone<T>(v: T): T {
  return v === undefined ? v : (JSON.parse(JSON.stringify(v)) as T);
}

function friendly(s: string): string {
  const t = s.replace(/[-_:.]+/g, " ").trim();
  return t.charAt(0).toUpperCase() + t.slice(1);
}

function upper(s: string): string {
  return s.charAt(0).toUpperCase() + s.slice(1);
}

/** "Blinds" -> "blinds" inside a sentence; "TVs" stays. */
function lower(s: string): string {
  return /^[A-Z][a-z]/.test(s) ? s.charAt(0).toLowerCase() + s.slice(1) : s;
}

function joinWords(parts: string[], conj: string): string {
  return parts.length <= 1 ? (parts[0] ?? "") : `${parts.slice(0, -1).join(", ")} ${conj} ${parts[parts.length - 1]}`;
}

function dur(s: number): string {
  if (s < 60) return `${s} s`;
  if (s % 3600 === 0) return `${s / 3600} h`;
  if (s % 60 === 0) return `${s / 60} min`;
  return s < 600 ? `${s} s` : `${Math.round(s / 60)} min`;
}

const forWords = (v: unknown) => (typeof v === "number" && v > 0 ? ` for ${dur(v)}` : "");

/** A word's description in plain words: asides that name code dropped, then the sentences before any that still do. */
function aboutWords(d: string | undefined): string {
  if (!d) return "";
  const t = d.replace(/\s*\([^)]*\)/g, (m) => (CODE.test(m) ? "" : m));
  const out: string[] = [];
  for (const s of t.match(/[^.!?]+[.!?]*/g) ?? []) {
    if (CODE.test(s)) break;
    out.push(s.trim());
  }
  return out.join(" ");
}

// ── The condition grammar, as lists ──

function listOf(root: unknown): ListView {
  if (empty(root)) return { combine: null, rows: [], isList: false };
  if (isObj(root)) {
    const ks = Object.keys(root);
    const c = ks.find((k): k is Combine => k === "all" || k === "any");
    if (c && Array.isArray(root[c]) && ks.every((k) => k === c || k === "for_s")) {
      return { combine: c, rows: root[c] as unknown[], groupFor: root.for_s, isList: true };
    }
  }
  return { combine: null, rows: [root], isList: false };
}

/** The list written back: nothing, the one node, or an all/any (with its own for_s kept). */
function compose(rows: unknown[], combine: Combine, groupFor: unknown): unknown {
  if (!rows.length) return undefined;
  if (rows.length === 1 && groupFor === undefined) return rows[0];
  return { [combine]: rows, ...(groupFor !== undefined ? { for_s: groupFor } : {}) };
}

function rowOf(node: unknown): RowView | null {
  if (!isObj(node)) return null;
  const ks = Object.keys(node);
  if (ks.length === 1 && !["all", "any", "not", "for_s"].includes(ks[0])) {
    const p = node[ks[0]];
    return p === null || p === true || isObj(p) ? { word: ks[0], params: isObj(p) ? p : {}, negated: false } : null;
  }
  if ("not" in node && ks.every((k) => k === "not" || k === "for_s")) {
    const inner = rowOf(node.not);
    if (inner && !inner.negated) return { ...inner, negated: true, notFor: node.for_s };
  }
  return null;
}

function rowNode(r: RowView): unknown {
  const w = { [r.word]: r.params };
  return r.negated ? { not: w, ...(r.notFor !== undefined ? { for_s: r.notFor } : {}) } : w;
}

/** A node that can be spread into its parent list without changing what it means, or null. */
function flatOf(node: unknown, combine: Combine): unknown[] | null {
  if (!isObj(node)) return null;
  const ks = Object.keys(node);
  if (ks.length === 1 && ks[0] === combine && Array.isArray(node[combine]) && (node[combine] as unknown[]).length) {
    return node[combine] as unknown[];
  }
  if (combine === "all" && ks.length > 1 && ks.every((k) => !["all", "any", "not", "for_s"].includes(k))) {
    return ks.map((k) => ({ [k]: node[k] }));
  }
  return null;
}

/** Is `path` this node's or below it ("enter_when.all[0]" is under "enter_when")? */
const within = (path: string, base: string) => path === base || path.startsWith(`${base}.`) || path.startsWith(`${base}[`);

/** The deepest of `paths` that `path` is under, or -1. */
function deepest(path: string, paths: string[]): number {
  let best = -1;
  paths.forEach((p, i) => {
    if (within(path, p) && (best < 0 || p.length > paths[best].length)) best = i;
  });
  return best;
}

/** One word in words, with what its required params say ("the music is one of these genres (jazz, soul)"). */
function wordWords(w: Word | undefined, p: Json): string {
  if (!w?.label) return SOMETHING;
  const said = Object.entries(w.params ?? {})
    .filter(([k, s]) => ui(s).required && p[k] !== undefined && p[k] !== null)
    .map(([k, s]) => (Array.isArray(p[k]) ? inWords(s as Schema, p[k]) : `${lower(ui(s).label ?? friendly(k))} ${inWords(s as Schema, p[k])}`));
  return `${w.label}${said.length ? ` (${said.join(", ")})` : ""}${forWords(p.for_s)}`;
}

/** A condition in words, from the vocabulary (when show_service has no sentence for it). */
function sayNode(node: unknown, words: Map<string, Word>, ctx: "top" | Combine = "top"): string {
  if (!isObj(node) || !Object.keys(node).length) return SOMETHING;
  const c = (["all", "any"] as Combine[]).find((k) => Array.isArray(node[k]));
  if (c) {
    const body = joinWords((node[c] as unknown[]).map((ch) => sayNode(ch, words, c)), c === "all" ? "and" : "or");
    return forWords(node.for_s) ? `${body}, together${forWords(node.for_s)}` : body;
  }
  if ("not" in node) {
    const inner = sayNode(node.not, words);
    return `${ctx === "all" ? "not while" : "it isn't so that"} ${inner}${forWords(node.for_s) ? `,${forWords(node.for_s)}` : ""}`;
  }
  const parts = Object.entries(node)
    .filter(([k]) => k !== "for_s")
    .map(([id, p]) => wordWords(words.get(id), isObj(p) ? p : {}));
  return `${joinWords(parts, "and")}${forWords(node.for_s)}`;
}

// ── show_service ──

/** One show_service call through room_service's pass-through. Status 0: no answer. Throws only when aborted. */
async function show(api: RoomApi, path: string, body: unknown, signal: AbortSignal): Promise<{ status: number; body?: any }> {
  const ac = new AbortController();
  const stop = () => ac.abort();
  signal.addEventListener("abort", stop);
  const timer = setTimeout(stop, TIMEOUT_MS);
  try {
    const res = await fetch(`${api.baseUrl}/show/api/v1/${path}`, {
      method: body === undefined ? "GET" : "POST",
      headers: body === undefined ? undefined : { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
      signal: ac.signal,
      cache: "no-cache",
    });
    let b: unknown;
    try {
      b = await res.json();
    } catch {
      b = undefined;
    }
    return { status: res.status, body: b };
  } catch {
    if (signal.aborted) throw new DOMException("aborted", "AbortError");
    return { status: 0 };
  } finally {
    clearTimeout(timer);
    signal.removeEventListener("abort", stop);
  }
}

const awayStatus = (s: number) => s === 0 || s >= 500;

/** A problem's words, never code: show_service's own text when it is plain, else the page's. */
function problemText(p: Json): Problem | null {
  const t = typeof p.text === "string" ? p.text : typeof p.message === "string" ? p.message : null;
  if (!t) return null;
  const level = p.level === "warn" || p.level === "warning" ? "warn" : "error";
  const text = CODE.test(t) ? (level === "warn" ? `It ${CANT_SENSE}.` : "This can't be used as it is.") : upper(t);
  return { path: typeof p.path === "string" ? p.path : "", text, level };
}

function previewOf(b: unknown): Omit<Preview, "of"> | null {
  if (!isObj(b)) return null;
  const problems = (Array.isArray(b.problems) ? b.problems : []).flatMap((p) =>
    typeof p === "string" ? (problemText({ text: p }) ?? []) : isObj(p) ? (problemText(p) ?? []) : [],
  );
  const live: Record<string, boolean | null> = {};
  for (const [k, v] of Object.entries(isObj(b.live) ? b.live : {})) {
    const x = isObj(v) && "value" in v ? v.value : v;
    if (typeof x === "boolean" || x === null) live[k] = x as boolean | null;
  }
  const nodes: Record<string, string> = {};
  const said = isObj(b.nodes) ? b.nodes : isObj(b.sentences) ? b.sentences : {};
  for (const [k, v] of Object.entries(said)) {
    const s = typeof v === "string" ? v : isObj(v) && typeof v.text === "string" ? v.text : null;
    if (s && !CODE.test(s)) nodes[k] = s;
  }
  // Each rule's whole sentence also comes as enter / suggest / leave (contract.RuleView).
  for (const [k, root] of [["enter", "enter_when"], ["suggest", "otherwise_suggest_when"], ["leave", "leave_when"]]) {
    const s = b[k];
    if (!(root in nodes) && typeof s === "string" && !CODE.test(s)) nodes[root] = s;
  }
  return { problems, live, nodes, leaveTo: typeof b.leave_to === "string" ? b.leave_to : undefined };
}

/** This mode's entry in GET /autopilot/rules ({modes: {name: …}}, {name: …} or a list of {mode, …}). */
function modeOf(b: unknown, name: string): unknown {
  const pick = (o: unknown) => (isObj(o) && isObj(o[name]) ? o[name] : null);
  const listed = (o: unknown) => (Array.isArray(o) ? (o.find((x) => isObj(x) && (x.mode === name || x.name === name)) ?? null) : null);
  if (!isObj(b)) return listed(b);
  return pick(b.modes) ?? listed(b.modes) ?? pick(b.rules) ?? listed(b.rules) ?? pick(b);
}

/** The saved block as Auto runs it (rules), or the draft checked (check). "away": show_service isn't answering. */
async function readPreview(
  api: RoomApi,
  name: string,
  block: Json,
  saved: boolean,
  modes: string[],
  missing: { rules: boolean; check: boolean },
  signal: AbortSignal,
): Promise<Omit<Preview, "of"> | null | "away"> {
  if (saved && !missing.rules) {
    const r = await show(api, "autopilot/rules", undefined, signal);
    if (awayStatus(r.status)) return "away";
    if (r.status === 404 || r.status === 405) missing.rules = true;   // not built yet: check the saved block instead
    else return r.status < 300 ? previewOf(modeOf(r.body, name)) : null;
  }
  if (missing.check) return null;
  const r = await show(api, "autopilot/check", { mode: name, autopilot: block, modes }, signal);
  if (awayStatus(r.status)) return "away";
  if (r.status === 404 || r.status === 405) {
    missing.check = true;
    return null;
  }
  return previewOf(r.body);
}

/** The vocabulary as room_service's show adapter passes it (x-ui.situations on the mode form or the Auto target). */
function describedWords(describe: RoomDescribe): Word[] | null {
  const d = describe as RoomDescribe & {
    mode_schema?: { properties?: { autopilot?: { properties?: Record<string, { "x-ui"?: { situations?: unknown } }> } } };
  };
  const a = d.mode_schema?.properties?.autopilot?.properties?.enter_when?.["x-ui"]?.situations;
  const b = (d.targets["autopilot.main"]?.schema?.["x-ui"] as { situations?: unknown } | undefined)?.situations;
  const s = Array.isArray(a) ? a : Array.isArray(b) ? b : null;
  return s ? (s.filter((w) => isObj(w) && typeof w.id === "string") as Word[]) : null;
}

// ── Params ──

/** A free-text name (a stream, a target): an id, never shown; only "the usual" or its unset words are offered. */
function idLike(s: Param): boolean {
  const w = ui(s).widget ?? "";
  if (Array.isArray(s.enum) || w === "time" || w.startsWith("vocab:") || s.format) return false;
  if (is(s, "string")) return true;
  return is(s, "array") && is(s.items, "string") && !Array.isArray(s.items?.enum);
}

/** The schema ValueControl draws: a vocabulary's words as choices (x-ui.widget "vocab:<facet>"), a small whole-number
 *  range as chips, and "for at least" reading 0 when unset. */
function controlSchema(s: Param, facets: Record<string, string[]>, isFor: boolean): Schema {
  const u = ui(s);
  let out = { ...s } as Param;
  const facet = u.widget?.startsWith("vocab:") ? facets[u.widget.slice(6)] : undefined;
  if (facet?.length) out = { ...out, items: { type: "string", enum: facet } };
  const it = s.items;
  if (it && is(it, "integer") && !it.enum && typeof it.minimum === "number" && typeof it.maximum === "number" && it.maximum - it.minimum <= 31) {
    out = { ...out, items: { ...it, enum: Array.from({ length: it.maximum - it.minimum + 1 }, (_, i) => it.minimum + i) } };
  }
  if (isFor) out = { ...out, "x-ui": { ...u, unset_label: "0" } };
  return out as Schema;
}

const blank = (v: unknown) => v === undefined || v === "" || (Array.isArray(v) && !v.length);

// ── The block ──

export function AutoTriggers({
  name,
  draft,
  baseMode,
  update,
  describe,
  api,
  config,
  status,
  names,
}: {
  /** The mode being edited. */
  name: string;
  draft: RawMode;
  /** The mode as saved: a choice that means the same as the saved one writes it back exactly. */
  baseMode: RawMode | undefined;
  update: (fn: (d: RawMode) => void) => void;
  describe: RoomDescribe;
  api: RoomApi;
  /** modes.json as read: the modes "Only from" and "Goes back to" offer, in its order. */
  config: RawRoomConfig;
  /** The site's rooms (their names), for the devices this mode sets. */
  status?: RoomStatus | null;
  /** Display names that beat a target's raw label, by target. */
  names?: Record<string, string>;
}) {
  const [open, setOpen] = useState(false);
  const [askOpen, setAskOpen] = useState<boolean | null>(null);
  const [vocab, setVocab] = useState<Word[] | null>(null);
  const [readings, setReadings] = useState<Record<string, boolean | null> | null>(null);
  const [facets, setFacets] = useState<Record<string, string[]>>({});
  const [preview, setPreview] = useState<Preview | null>(null);
  const [awayWords, setAwayWords] = useState(false);
  const [awayPreview, setAwayPreview] = useState(false);
  const [combines, setCombines] = useState<Partial<Record<CondKey, Combine>>>({});
  const [more, setMore] = useState<Record<string, boolean>>({});
  const [note, setNote] = useState<string | null>(null);
  const missing = useRef({ rules: false, check: false });
  const facetsAsked = useRef(false);

  const saved: Json = isObj(baseMode?.autopilot) ? (baseMode.autopilot as Json) : {};
  const ap: Json = isObj(draft.autopilot) ? (draft.autopilot as Json) : {};
  const apKey = jsonKey(ap);
  const savedKey = jsonKey(saved);
  const modeList = Object.entries(config.modes ?? {})
    .filter(([m, d]) => !m.startsWith("_") && !(isObj(d) && d.abstract))
    .map(([m]) => m);
  const modesKey = modeList.join("\n");

  const fallback = useMemo(() => describedWords(describe), [describe]);
  const list = vocab ?? fallback ?? [];
  const words = useMemo(() => new Map(list.map((w) => [w.id, w])), [list]);

  // The vocabulary (and its readings: "true now" until rules/check answer), again every 5 s while open.
  useEffect(() => {
    const ac = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;
    const run = async () => {
      if (!document.hidden) {
        const r = await show(api, "situations", undefined, ac.signal).catch(() => null);
        if (!r) return;                                   // closed meanwhile
        if (isObj(r.body) && Array.isArray(r.body.vocabulary)) {
          setVocab((r.body.vocabulary as unknown[]).filter((w) => isObj(w) && typeof w.id === "string") as Word[]);
          const rd: Record<string, boolean | null> = {};
          for (const [k, v] of Object.entries(isObj(r.body.readings) ? r.body.readings : {})) {
            const x = isObj(v) ? v.value : undefined;
            if (typeof x === "boolean" || x === null) rd[k] = x as boolean | null;
          }
          setReadings(rd);
          setAwayWords(false);
        } else setAwayWords(awayStatus(r.status));
      }
      if (open) timer = setTimeout(run, POLL_MS);
    };
    void run();
    return () => {
      clearTimeout(timer);
      ac.abort();
    };
  }, [api, open]);

  // A word whose param picks from one of show_service's vocabularies (x-ui.widget "vocab:genre"): its words, once.
  const needsFacets = list.some((w) => Object.values(w.params ?? {}).some((p) => (ui(p).widget ?? "").startsWith("vocab:")));
  useEffect(() => {
    if (!needsFacets || !open || facetsAsked.current) return;
    facetsAsked.current = true;
    const ac = new AbortController();
    show(api, "vibes", undefined, ac.signal)
      .then((r) => {
        const v = isObj(r.body) ? r.body.vocab : undefined;
        if (isObj(v)) setFacets(Object.fromEntries(Object.entries(v).filter(([, x]) => Array.isArray(x))) as Record<string, string[]>);
      })
      .catch(() => {
        facetsAsked.current = false;                      // closed meanwhile: ask again next time
      });
    return () => ac.abort();
  }, [api, needsFacets, open]);

  // The live preview: the saved block from rules, a draft from check (debounced); again every 5 s while open.
  useEffect(() => {
    if (!Object.keys(ap).length && apKey === savedKey) {
      setPreview(null);
      setAwayPreview(false);
      return;
    }
    const block = ap;
    const ac = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;
    const run = async () => {
      if (!document.hidden) {
        const p = await readPreview(api, name, block, apKey === savedKey, modesKey.split("\n"), missing.current, ac.signal).catch(() => undefined);
        if (p === undefined) return;                      // the draft moved on, or the editor closed
        setAwayPreview(p === "away");
        setPreview(p && p !== "away" ? { ...p, of: apKey } : null);
      }
      if (open) timer = setTimeout(run, POLL_MS);
    };
    timer = setTimeout(run, apKey === savedKey ? 0 : 500);
    return () => {
      clearTimeout(timer);
      ac.abort();
    };
    // `ap` is read through apKey: the same block, whatever object it is this render.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [api, name, apKey, savedKey, open, modesKey]);

  const away = awayWords || awayPreview;
  const live = preview && preview.of === apKey ? preview : null;
  const problems = live?.problems ?? [];

  // ── Writing ──

  /** Does a key's new value mean the same as the saved one (then the saved form is written back exactly)? */
  const same = (key: string, a: unknown, b: unknown): boolean => {
    if (key === "leave_to") return (a || "previous") === (b || "previous");
    if (key === "from") {
      const norm = (v: unknown) =>
        v === undefined || v === null || (Array.isArray(v) && !v.length) ? null : [...new Set(typeof v === "string" ? [v] : Array.isArray(v) ? v : [v])].map(String).sort();
      return jsonKey(norm(a)) === jsonKey(norm(b));
    }
    if (key === "skip") return jsonKey(Array.isArray(a) && a.length ? a : null) === jsonKey(Array.isArray(b) && b.length ? b : null);
    return (empty(a) && empty(b)) || jsonKey(a) === jsonKey(b);
  };

  const write = (key: string, v: unknown) =>
    update((d) => {
      const next: Json = isObj(d.autopilot) ? { ...(d.autopilot as Json) } : {};
      if (v === undefined) delete next[key];
      else next[key] = v;
      if (same(key, next[key], saved[key])) {
        if (key in saved) next[key] = clone(saved[key]);
        else delete next[key];
      }
      if (Object.keys(next).length) d.autopilot = next;
      else delete d.autopilot;
    });

  // ── Words for modes and devices ──

  const labelOf = (m: string) =>
    (m === name && draft.label) || describe.modes[m]?.label || (config.modes?.[m]?.label as string | undefined) || friendly(m);
  const others = modeList.filter((m) => m !== name);

  type T = { label?: string; category?: string; subsystem?: string; member?: string; schema?: Schema & { readOnly?: boolean } };
  const tinfo = (t: string) => (describe.targets[t] ?? {}) as T;
  const subOf = (t: string) => tinfo(t).subsystem ?? t.split(".")[0];
  const groups = describe.groups ?? {};
  const rooms = status?.rooms?.map((r) => r.id) ?? [...new Set(Object.keys(groups).filter((g) => g.includes(":")).map((g) => g.split(":")[0]))];
  const roomName = (id: string) => status?.rooms?.find((r) => r.id === id)?.name ?? friendly(id);
  const roomOf = (t: string) => {
    const g = Object.keys(groups).find((k) => k.includes(":") && rooms.includes(k.split(":")[0]) && groups[k].includes(t));
    return g ? g.split(":")[0] : "";
  };
  const targetName = (t: string) => {
    const i = tinfo(t);
    const member = i.member ?? t.slice(subOf(t).length + 1);
    if (names?.[t]) return names[t];
    return i.label && i.label !== member && i.label !== t ? i.label : `${kindWords(describe, subOf(t))} ${friendly(member)}`;
  };
  /** What a skip selector names, in words ("Drawing room blinds", "Glitterball"). */
  const selWords = (sel: string): string => {
    const ts = targetsOf(describe, sel);
    if (!ts.length) return "Something the room doesn't have";
    if (ts.length === 1) return targetName(ts[0]);
    const kinds = [...new Set(ts.map((t) => kindWords(describe, subOf(t))))];
    const what = joinWords(kinds.map((k, i) => (i ? lower(k) : k)), "and");
    const rs = [...new Set(ts.map(roomOf))];
    return rooms.length > 1 && rs.length === 1 && rs[0] ? `${roomName(rs[0])} ${lower(what)}` : what;
  };
  const itemWords = (s: unknown): string => {
    if (typeof s === "string") return selWords(s);
    if (!isObj(s)) return CANT_SHOW;
    return joinWords(
      Object.entries(s).map(([sel, c]) => {
        const ts = targetsOf(describe, sel);
        if (!isObj(c) || c.from === undefined) return selWords(sel);
        const sc = tinfo(ts[0] ?? "").schema;
        const v = lower(Array.isArray(c.from) ? joinWords(c.from.map((x) => lower(inWords(sc, x))), "or") : inWords(sc, c.from));
        return `${selWords(sel)}, if ${ts.length > 1 ? "they're" : "it's"} ${v}`;
      }),
      "and",
    );
  };

  // ── Sentences and the live dots ──

  const sentenceAt = (path: string, node: unknown, ctx: "top" | Combine = "top") => live?.nodes[path] ?? sayNode(node, words, ctx);
  const liveAt = (path: string, node?: unknown): boolean | null | undefined => {
    if (live && path in live.live) return live.live[path];
    if (!readings || node === undefined) return undefined;
    const r = rowOf(node);
    if (!r || Object.keys(r.params).some((k) => k !== "for_s") || !(r.word in readings)) return undefined;
    const v = readings[r.word];
    return v === null ? null : r.negated ? !v : v;
  };
  const when = (s: string) => (/^(when|whenever|if|while)\b/i.test(s) ? s : `when ${s}`);
  const toWords = (() => {
    const lt = typeof ap.leave_to === "string" ? ap.leave_to : undefined;
    const said = live?.leaveTo;
    if (said && said !== "previous" && !modeList.includes(said) && !CODE.test(said)) return said;
    return !lt || lt === "previous" ? "the mode it came from" : labelOf(lt);
  })();

  const summary = (() => {
    const s = (k: CondKey) => (empty(ap[k]) ? "" : sentenceAt(k, ap[k]));
    const parts: string[] = [];
    if (s("enter_when")) parts.push(upper(when(s("enter_when"))));
    if (s("otherwise_suggest_when")) parts.push(`${parts.length ? "asks" : "Asks"} first ${when(s("otherwise_suggest_when"))}`);
    if (parts.length && s("leave_when")) parts.push(`back to ${toWords} ${when(s("leave_when"))}`);
    return parts.length ? parts.join(" · ") : "Only when someone picks it";
  })();
  const errs = problems.filter((p) => p.level === "error").length;
  const hasAny = Object.keys(ap).length > 0;

  // ── Drawing ──

  const chip = (pressed: boolean) =>
    cn(
      "min-h-11 rounded-full border px-3 text-[15px] transition-colors",
      pressed ? "border-teal-300 bg-teal-50 font-semibold text-teal-900" : "border-stone-200 bg-white text-stone-700 hover:bg-stone-50",
    );
  const smallChip = (pressed: boolean) =>
    cn(
      "min-h-9 rounded-full border px-3 text-sm transition-colors",
      pressed ? "border-teal-300 bg-teal-50 font-semibold text-teal-900" : "border-stone-200 bg-white text-stone-600 hover:bg-stone-50",
    );
  const seg = (pressed: boolean) =>
    cn(
      "min-h-11 rounded-xl border px-2 text-[15px] transition-colors",
      pressed ? "border-teal-300 bg-teal-50 font-semibold text-teal-900" : "border-stone-200 bg-white hover:bg-stone-50",
    );
  const select = "h-11 w-full rounded-xl border border-stone-200 bg-white px-3 text-base";

  const problemLines = (ps: Problem[]) =>
    ps.map((p, i) => (
      <p key={`${i}:${p.text}`} className={cn("text-sm", p.level === "warn" ? "text-amber-700" : "text-amber-800")}>
        {p.text}
      </p>
    ));

  const liveWords = (v: boolean | null | undefined) =>
    v === undefined ? null : (
      <span className="inline-flex shrink-0 items-center gap-1.5 text-sm text-muted-foreground">
        <Dot tone={v === true ? "ok" : "muted"} className={cn(v === null && "bg-transparent ring-1 ring-inset ring-stone-400")} />
        {v === true ? "true now" : v === false ? "not now" : "can't tell"}
      </span>
    );

  const wordOptions = list.map((w) => (
    <option key={w.id} value={w.id}>
      {upper(w.label ?? SOMETHING)}
      {w.available === false ? ` (${CANT_SENSE})` : ""}
    </option>
  ));

  /** One param: its label and help, "the usual" (left out) and its unset words (null) when it has them, its control. */
  const paramField = (k: string, s: Param, value: unknown, has: boolean, onChange: (v: unknown) => void) => {
    const u = ui(s);
    const isFor = k === "for_s";
    const def = s.default;
    const hasDef = def !== undefined && def !== null;
    const ids = idLike(s);
    const usual = hasDef && !ids ? ` (${inWords(s as Schema, def)})` : !hasDef && s.nullable && u.unset_label ? ` (${u.unset_label})` : "";
    let control: ReactNode = null;
    if (ids) control = has && value !== null ? <p className="text-sm text-stone-700">{CANT_SHOW}</p> : null;
    else if (u.widget === "time") {
      control = (
        <Input
          type="time"
          className="h-11 w-36 rounded-xl text-base"
          value={typeof value === "string" ? value : ""}
          onChange={(e) => onChange(e.currentTarget.value || undefined)}
        />
      );
    } else {
      const shown = value === null ? undefined : has ? value : isFor ? undefined : def;
      control = <ValueControl schema={controlSchema(s, facets, isFor)} value={shown} onChange={(v) => onChange(blank(v) ? undefined : v)} />;
    }
    return (
      <div key={k} className="space-y-1.5">
        <p className="text-sm font-medium text-stone-700">
          {u.label ?? friendly(k)}
          {isFor && typeof value === "number" && value >= 60 && <span className="font-normal text-muted-foreground"> · {dur(value)}</span>}
          {u.help && <span className="block font-normal text-muted-foreground">{u.help}</span>}
        </p>
        {!isFor && !u.required && (
          <div className="flex flex-wrap gap-2">
            <button type="button" aria-pressed={!has} onClick={() => has && onChange(undefined)} className={smallChip(!has)}>
              The usual{usual}
            </button>
            {s.nullable && u.unset_label && hasDef && (
              <button type="button" aria-pressed={has && value === null} onClick={() => onChange(null)} className={smallChip(has && value === null)}>
                {u.unset_label}
              </button>
            )}
          </div>
        )}
        {control}
      </div>
    );
  };

  /** One condition: an editable word (picker, is / isn't, for at least, its params), or a structure shown in words. */
  const row = (key: CondKey, lv: ListView, combine: Combine, node: unknown, i: number, path: string, probs: Problem[], set: (rows: unknown[], c?: Combine) => void) => {
    const r = rowOf(node);
    const w = r ? words.get(r.word) : undefined;
    const replace = (n: unknown) => set(lv.rows.map((x, j) => (j === i ? n : x)));
    const head = (
      <div className="flex items-start gap-2">
        <p className="min-w-0 flex-1 pt-1 text-sm text-muted-foreground">{upper(sentenceAt(path, node, lv.isList ? combine : "top"))}</p>
        <span className="pt-1">{liveWords(liveAt(path, node))}</span>
        <button
          type="button"
          aria-label="Remove this condition"
          onClick={() => set(lv.rows.filter((_, j) => j !== i))}
          className="-mr-1 grid h-9 w-9 shrink-0 place-items-center rounded-lg text-stone-500 hover:bg-stone-100"
        >
          <X className="h-4 w-4" aria-hidden />
        </button>
      </div>
    );
    if (!r || !w) {
      const parent = lv.isList ? combine : "all";
      const flat = flatOf(node, parent);
      return (
        <li key={i} className="space-y-1 rounded-xl border border-stone-200 bg-white p-3">
          {head}
          <p className="text-sm text-stone-600">
            {r ? "This page doesn't know this one, so it's kept as it is." : "This one is set up in a way this page shows in words; it's kept as it is."}
          </p>
          {flat && (
            <button
              type="button"
              onClick={() => {
                setCombines((c) => ({ ...c, [key]: parent }));
                set([...lv.rows.slice(0, i), ...flat, ...lv.rows.slice(i + 1)], parent);
              }}
              className="min-h-9 text-sm font-semibold text-teal-800 underline underline-offset-2"
            >
              Edit as a list
            </button>
          )}
          {problemLines(probs)}
        </li>
      );
    }
    const params = r.params;
    const forV = r.negated ? r.notFor : params.for_s;
    const setRow = (nr: RowView) => replace(rowNode(nr));
    const setParam = (k: string, v: unknown) => {
      if (k === "for_s") {
        const n = typeof v === "number" && v > 0 ? v : undefined;
        if (r.negated) return setRow({ ...r, notFor: n });
        const p = { ...params };
        if (n === undefined) delete p.for_s;
        else p.for_s = n;
        return setRow({ ...r, params: p });
      }
      const p = { ...params };
      if (v === undefined) delete p[k];
      else p[k] = v;
      setRow({ ...r, params: p });
    };
    const pickWord = (id: string) => {
      const nw = words.get(id);
      if (!nw || id === r.word) return;
      const keep = Object.fromEntries(Object.entries(params).filter(([k]) => k === "for_s" || (nw.params && k in nw.params)));
      setRow({ ...r, word: id, params: keep });
    };
    const negate = (on: boolean) => {
      if (on === r.negated) return;
      const p = { ...params };
      if (on) {
        delete p.for_s;                                   // "for at least" moves to the not: how long it hasn't been so
        setRow({ word: r.word, params: p, negated: true, notFor: params.for_s });
      } else {
        if (r.notFor !== undefined && p.for_s === undefined) p.for_s = r.notFor;
        setRow({ word: r.word, params: p, negated: false });
      }
    };
    const ps = Object.entries(w.params ?? {}).filter(([, s]) => !ui(s).hidden);
    const forSchema = w.params?.for_s;
    const front = ps.filter(([k, s]) => k !== "for_s" && ui(s).required);
    const rest = ps.filter(([k, s]) => k !== "for_s" && !ui(s).required);
    const moreKey = `${key}|${i}`;
    const moreOpen = more[moreKey] ?? rest.some(([k]) => k in params);
    const about = aboutWords(w.description);
    const field = ([k, s]: [string, Param]) => paramField(k, s, params[k], k in params, (v) => setParam(k, v));
    return (
      <li key={i} className="space-y-3 rounded-xl border border-stone-200 bg-white p-3">
        {head}
        <div className="space-y-1">
          <select className={select} value={r.word} onChange={(e) => pickWord(e.currentTarget.value)} aria-label="What it listens for">
            {wordOptions}
          </select>
          {about && <p className="text-sm text-muted-foreground">{about}</p>}
          {w.available === false && !probs.some((p) => p.level === "warn") && (
            <p className="text-sm text-amber-700">{upper(CANT_SENSE)}.</p>
          )}
        </div>
        <div className="grid grid-cols-2 gap-2">
          <button type="button" aria-pressed={!r.negated} onClick={() => negate(false)} className={seg(!r.negated)}>
            When it is
          </button>
          <button type="button" aria-pressed={r.negated} onClick={() => negate(true)} className={seg(r.negated)}>
            When it isn't
          </button>
        </div>
        {forSchema && paramField("for_s", forSchema, forV, forV !== undefined, (v) => setParam("for_s", v))}
        {front.map(field)}
        {rest.length > 0 &&
          (moreOpen ? (
            <div className="space-y-3 rounded-xl bg-stone-50 p-3">{rest.map(field)}</div>
          ) : (
            <button
              type="button"
              onClick={() => setMore((m) => ({ ...m, [moreKey]: true }))}
              className="inline-flex min-h-9 items-center gap-1 text-sm text-stone-600 underline underline-offset-2 hover:text-stone-900"
            >
              More <ChevronDown className="h-3.5 w-3.5" aria-hidden />
            </button>
          ))}
        {problemLines(probs)}
      </li>
    );
  };

  /** A list of conditions for one key: combine them, each row, and "Add a condition…". */
  const builder = (key: CondKey, title: string, emptyWords: string, hint?: string) => {
    const lv = listOf(ap[key]);
    const combine = lv.combine ?? combines[key] ?? "all";
    const paths = lv.rows.map((_, i) => (lv.isList ? `${key}.${lv.combine}[${i}]` : key));
    const mine = problems.filter((p) => within(p.path, key));
    const set = (rows: unknown[], c: Combine = combine) => write(key, compose(rows, c, lv.groupFor));
    return (
      <div className="space-y-2">
        <div>
          <p className="text-[15px] font-semibold text-stone-800">{title}</p>
          {hint && <p className="text-sm text-muted-foreground">{hint}</p>}
        </div>
        {!lv.rows.length && <p className="text-sm text-muted-foreground">{emptyWords}</p>}
        {lv.rows.length >= 2 && (
          <div className="space-y-1">
            <div className="grid grid-cols-2 gap-2">
              {(["all", "any"] as Combine[]).map((c) => (
                <button
                  key={c}
                  type="button"
                  aria-pressed={combine === c}
                  onClick={() => {
                    setCombines((x) => ({ ...x, [key]: c }));
                    if (c !== combine) set(lv.rows, c);
                  }}
                  className={seg(combine === c)}
                >
                  {c === "all" ? "All of these" : "Any of these"}
                </button>
              ))}
            </div>
            {(liveAt(key) !== undefined || forWords(lv.groupFor)) && (
              <p className="flex flex-wrap items-center gap-x-2 text-sm text-muted-foreground">
                {forWords(lv.groupFor) && <span>Together,{forWords(lv.groupFor)}</span>}
                {liveWords(liveAt(key))}
              </p>
            )}
          </div>
        )}
        {problemLines(mine.filter((p) => deepest(p.path, paths) < 0))}
        {lv.rows.length > 0 && (
          <ul className="space-y-2">
            {lv.rows.map((node, i) => row(key, lv, combine, node, i, paths[i], mine.filter((p) => deepest(p.path, paths) === i), set))}
          </ul>
        )}
        {list.length ? (
          <select
            className={select}
            value=""
            aria-label="Add a condition"
            onChange={(e) => {
              const id = e.currentTarget.value;
              if (id) set([...lv.rows, { [id]: {} }]);
            }}
          >
            <option value="">{lv.rows.length ? "＋ Add another condition…" : "＋ Add a condition…"}</option>
            {wordOptions}
          </select>
        ) : (
          <p className="text-sm text-muted-foreground">The list of what the room can sense isn't here yet.</p>
        )}
      </div>
    );
  };

  // "Only from…"
  const fromRaw = ap.from;
  const fromList: string[] | null =
    fromRaw === undefined || fromRaw === null || (Array.isArray(fromRaw) && !fromRaw.length)
      ? null
      : typeof fromRaw === "string"
        ? [fromRaw]
        : Array.isArray(fromRaw)
          ? fromRaw.filter((x): x is string => typeof x === "string")
          : [];
  const toggleFrom = (m: string) => {
    const cur = fromList ?? [];
    const next = cur.includes(m) ? cur.filter((x) => x !== m) : [...cur, m];
    write("from", next.length ? next : undefined);
  };
  const lt = typeof ap.leave_to === "string" ? ap.leave_to : "previous";

  // "When it switches by itself, leave alone": this mode's own devices, by room and kind where the room's group is
  // exactly them; other skip entries (conditional ones, things this mode doesn't set) are kept and shown in words.
  const devices = setTargets(describe, draft.state as Json | undefined).filter((t) => {
    const i = tinfo(t);
    const cat = i.category ?? (describe as { subsystems?: Record<string, { category?: string }> }).subsystems?.[subOf(t)]?.category;
    return cat !== "setting" && !i.schema?.readOnly && (i.schema?.["x-ui"] as { widget?: string } | undefined)?.widget !== "readonly";
  });
  const units: { sel: string; ts: string[]; label: string }[] = [];
  for (const t of devices) {
    if (units.some((u) => u.ts.includes(t))) continue;
    const g = Object.keys(groups).find((k) => {
      const [room, kind] = k.split(":");
      return kind === subOf(t) && rooms.includes(room) && groups[k].includes(t);
    });
    if (g && groups[g].every((x) => devices.includes(x))) units.push({ sel: g, ts: [...groups[g]], label: selWords(g) });
    else units.push({ sel: t, ts: [t], label: selWords(t) });
  }
  const skip: unknown[] = Array.isArray(ap.skip) ? ap.skip : [];
  const skipTs = skip.map((s) => (typeof s === "string" ? targetsOf(describe, s) : null));
  const sameSet = (a: string[], b: string[]) => a.length === b.length && a.every((x) => b.includes(x));
  const exactOf = (u: { ts: string[] }) => skip.map((_, i) => i).filter((i) => skipTs[i] && sameSet(skipTs[i] as string[], u.ts));
  const covered = (u: { ts: string[] }) => u.ts.every((t) => skipTs.some((ts) => ts?.includes(t)));
  const owned = new Set(units.flatMap(exactOf));
  const otherSkips = skip.map((s, i) => ({ s, i })).filter(({ i }) => !owned.has(i));
  const toggleUnit = (u: (typeof units)[number]) => {
    setNote(null);
    if (!covered(u)) return write("skip", [...skip, u.sel]);
    const drop = exactOf(u);
    const rest = skip.filter((_, i) => !drop.includes(i));
    const restTs = rest.flatMap((s) => (typeof s === "string" ? targetsOf(describe, s) : []));
    if (u.ts.every((t) => restTs.includes(t))) setNote(`${u.label}: left alone as part of something below; remove that to change it.`);
    write("skip", rest.length ? rest : undefined);
  };

  const at = (base: string) => problems.filter((p) => within(p.path, base));
  const general = problems.filter((p) => ![...ORDER, "from", "leave_to", "skip"].some((k) => within(p.path, k)));
  const askShown = askOpen ?? (!empty(ap.otherwise_suggest_when) || at("otherwise_suggest_when").length > 0);

  return (
    <section className="rounded-2xl border border-stone-200 bg-card">
      <button type="button" aria-expanded={open} onClick={() => setOpen((o) => !o)} className="flex min-h-11 w-full items-start gap-3 p-3 text-left">
        <span className="min-w-0 flex-1">
          <span className="block text-base font-semibold">Starts by itself</span>
          <span className="block text-sm text-muted-foreground">{summary}</span>
          {!open && errs > 0 && <span className="block text-sm text-amber-800">{errs === 1 ? "1 thing to look at" : `${errs} things to look at`}</span>}
          {!open && away && hasAny && <span className="block text-sm text-amber-800">{AWAY}</span>}
        </span>
        <ChevronDown className={cn("mt-1 h-4 w-4 shrink-0 text-muted-foreground transition-transform", !open && "-rotate-90")} aria-hidden />
      </button>
      {open && (
        <div className="space-y-5 border-t border-stone-100 p-3">
          {away && <Notice tone="warn">{AWAY}</Notice>}
          {general.length > 0 && <div className="space-y-1">{problemLines(general)}</div>}

          {builder("enter_when", "Starts by itself when…", "Only when someone picks it.")}

          {hasAny && (
            <div className="space-y-2">
              <p className="text-[15px] font-semibold text-stone-800">Only from…</p>
              <div className="flex flex-wrap gap-2">
                <button type="button" aria-pressed={fromList === null} onClick={() => write("from", undefined)} className={chip(fromList === null)}>
                  From any mode
                </button>
                {others.map((m) => (
                  <button key={m} type="button" aria-pressed={!!fromList?.includes(m)} onClick={() => toggleFrom(m)} className={chip(!!fromList?.includes(m))}>
                    {labelOf(m)}
                  </button>
                ))}
              </div>
              {fromList !== null && !fromList.some((m) => others.includes(m)) && (
                <p className="text-sm text-amber-800">As it is, it can't start by itself from any mode: pick the modes it may start from.</p>
              )}
              {problemLines(at("from"))}
            </div>
          )}

          {hasAny && (
            <>
              {builder("leave_when", "Goes back when…", "It stays until someone picks another mode.", "Only after it started by itself.")}
              <div className="space-y-2">
                <p className="text-[15px] font-semibold text-stone-800">Goes back to</p>
                <div className="flex flex-wrap gap-2">
                  {[["previous", "The mode it came from"], ...others.map((m) => [m, labelOf(m)])].map(([v, w]) => (
                    <button
                      key={v}
                      type="button"
                      aria-pressed={lt === v}
                      onClick={() => write("leave_to", v === "previous" ? undefined : v)}
                      className={chip(lt === v)}
                    >
                      {w}
                    </button>
                  ))}
                </div>
                {problemLines(at("leave_to"))}
              </div>
            </>
          )}

          <div className="space-y-2">
            <button
              type="button"
              aria-expanded={askShown}
              onClick={() => setAskOpen(!askShown)}
              className="flex min-h-11 w-full items-center gap-2 rounded-xl px-1 text-left hover:bg-stone-100/70"
            >
              <ChevronDown className={cn("h-4 w-4 shrink-0 text-muted-foreground transition-transform", !askShown && "-rotate-90")} aria-hidden />
              <span className="min-w-0 flex-1 text-[15px] font-semibold text-stone-700">Ask first instead of switching</span>
              {!askShown && !empty(ap.otherwise_suggest_when) && <span className="shrink-0 text-sm text-muted-foreground">On</span>}
            </button>
            {askShown &&
              builder(
                "otherwise_suggest_when",
                "Asks first when…",
                "It never asks.",
                "Instead of switching, the room offers a one-tap switch to this mode.",
              )}
          </div>

          {hasAny && (
            <div className="space-y-2">
              <p className="text-[15px] font-semibold text-stone-800">When it switches by itself, leave alone:</p>
              <p className="text-sm text-muted-foreground">Picked by hand, the mode still sets everything.</p>
              {units.length > 0 ? (
                <div className="flex flex-wrap gap-2">
                  {units.map((u) => (
                    <button key={u.sel} type="button" aria-pressed={covered(u)} onClick={() => toggleUnit(u)} className={chip(covered(u))}>
                      {u.label}
                    </button>
                  ))}
                </div>
              ) : (
                <p className="text-sm text-muted-foreground">This mode doesn't set any devices.</p>
              )}
              {otherSkips.length > 0 && (
                <div className="space-y-1">
                  <p className="text-sm text-stone-700">Also left alone:</p>
                  <ul className="space-y-1">
                    {otherSkips.map(({ s, i }) => (
                      <li key={`${i}:${jsonKey(s)}`} className="flex min-h-11 items-center gap-1 rounded-xl border border-stone-200 bg-white pl-3 pr-1">
                        <span className="min-w-0 flex-1 text-[15px]">{itemWords(s)}</span>
                        <button
                          type="button"
                          aria-label={`Stop leaving ${itemWords(s)} alone`}
                          onClick={() => {
                            const rest = skip.filter((_, j) => j !== i);
                            write("skip", rest.length ? rest : undefined);
                          }}
                          className="grid h-9 w-9 shrink-0 place-items-center rounded-lg text-stone-500 hover:bg-stone-100"
                        >
                          <X className="h-4 w-4" aria-hidden />
                        </button>
                      </li>
                    ))}
                  </ul>
                </div>
              )}
              {note && <p className="text-sm text-amber-800">{note}</p>}
              {problemLines(at("skip"))}
            </div>
          )}
        </div>
      )}
    </section>
  );
}
