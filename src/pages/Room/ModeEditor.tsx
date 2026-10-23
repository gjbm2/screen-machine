// Edit one mode (the "⋯" on its tile): its name, its description, and what it sets, as plain controls built from each
// target's value schema (GET /api/v1/room/describe). No JSON. The owner asked for this on 15 Oct, which supersedes
// CLAUDE.md rule 7 ("no config on the Room page") for modes. Saves the whole modes.json through
// PUT /api/v1/room/config with the revision it was read at: the server validates it and keeps history.
//
// What a mode sets is ModeTargets: every device, by room and kind, each switched in or out of the mode. This file draws
// each value's control (ValueControl, SchemaControls.tsx: drawn from the target's schema alone) for it.
//
// The draft is checked as it is edited (POST /api/v1/room/config/validate, read-only) and its problems are shown in
// words under their rows. A draft is never lost: offline, Save says so and keeps it; if someone else saved meanwhile,
// a save over another mode's change rebases silently, and a change to this mode asks "Keep mine" or "Use theirs".
// Editing the mode the room is in offers "Save and use now" (save, then PUT /api/v1/mode with the same mode).

import { useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { cn } from "@/lib/utils";
import { RoomApiError, type RoomApi } from "./api";
import { AutoTriggers } from "./AutoTriggers";
import { Notice, Spinner } from "./bits";
import { kindWords, ModeTargets, targetsOf } from "./ModeTargets";
import { editable, inWords, jsonKey, ValueControl, type Schema } from "./SchemaControls";
import type { SceneTagIndex } from "./sceneTags";
import type { RawMode, RawRoomConfig, RoomDescribe, RoomStatus } from "./types";
import type { NewLogEntry } from "./useRoomConnection";

const NOT_ANSWERING = "The room isn't answering. Your changes are kept; tap Save to try again.";

function friendly(s: string): string {
  const t = s.replace(/[-_:.]+/g, " ").trim();
  return t.charAt(0).toUpperCase() + t.slice(1);
}

function clone<T>(v: T): T {
  return JSON.parse(JSON.stringify(v)) as T;
}

// ── Saving, checking and conflicts ──

/** One problem the room found, in words, and where: a mode and the targets its rows are. */
interface Issue {
  mode: string | null;
  targets: string[];
  text: string;
}

/** A validate/save problem as the room sends it (aa may add mode, target and plain `text`). */
type RoomProblem = { path?: string; message?: string; code?: string; mode?: string; target?: string; text?: string };

type Check = { ok?: boolean; errors?: RoomProblem[]; warnings?: RoomProblem[] };

type SaveReply = { revision: string; pending_targets?: string[] | null; deferred?: boolean };

type Base = { config: RawRoomConfig; revision: string };

/** POST /api/v1/room/config/validate: a read-only check of a draft. Null when the room can't say. */
async function checkConfig(api: RoomApi, config: RawRoomConfig, signal: AbortSignal): Promise<Check | null> {
  const res = await fetch(`${api.baseUrl}/api/v1/room/config/validate`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ config }),
    signal,
    cache: "no-cache",
  });
  return res.ok ? ((await res.json()) as Check) : null;
}

function withMode(config: RawRoomConfig, name: string, mode: RawMode): RawRoomConfig {
  const c = clone(config);
  c.modes = { ...(c.modes ?? {}), [name]: mode };
  return c;
}

/** A JSON pointer's parts ("/modes/piano/state/drawing-room:tv/restore" -> ["modes", "piano", ...]). */
function pointer(path: string | undefined): string[] {
  return (path ?? "").split("/").slice(1).map((p) => p.replace(/~1/g, "/").replace(/~0/g, "~"));
}

/** What a problem means, in the page's words (never the room's own text: it names slots, keys and pointers). */
function problemWords(p: RoomProblem, part: string | undefined, describe: RoomDescribe | null, target?: string): string {
  if (p.text) return p.text;
  const m = p.message ?? "";
  if (part === "restore") return "Put back afterwards needs a value to set first.";
  if (part === "max_age_s") return "\"Only if it was changed within\" needs put back afterwards.";
  if (part === "store_if") {
    // The target's own store_if words when it has exactly one choice ("Only if music was playing").
    const own = target
      ? (describe as { targets?: Record<string, { entry_schema?: Schema }> } | null)?.targets?.[target]?.entry_schema
      : undefined;
    const labels = own?.properties?.store_if?.["x-ui"]?.enum_labels;
    return `"${labels?.length === 1 ? labels[0] : "Only if it was in use"}" needs put back afterwards.`;
  }
  if (part === "policy") return "\"Keep it there\" has a setting this page doesn't know.";
  if (part === "retrieve" || /no mode stores/.test(m)) return "It puts back something no mode remembers first.";
  if (/can't be combined/.test(m)) return "It can't set a value and put one back at the same time.";
  if (/equally specific/.test(m)) return "Two settings in this mode disagree about it.";
  if (/matches no target/.test(m)) return "It names something the room doesn't have.";
  if (/needs one of set/.test(m)) return "Pick what it should be set to.";
  if (part === "set" || part === "else" || /^[\w.-]+: /.test(m)) return "That value can't be used here.";
  return "This setting can't be used as it is.";
}

function issuesOf(problems: RoomProblem[] | undefined, describe: RoomDescribe | null): Issue[] {
  const out: Issue[] = [];
  for (const p of problems ?? []) {
    const ps = pointer(p.path);
    const mode = p.mode ?? (ps[0] === "modes" ? (ps[1] ?? null) : null);
    const sel = ps[0] === "modes" && ps[2] === "state" ? ps[3] : undefined;
    const targets = p.target ? [p.target] : sel && describe ? targetsOf(describe, sel) : [];
    let text = problemWords(p, sel ? ps[4] : undefined, describe, p.target ?? sel);
    if (!sel && mode && !p.text) {
      text =
        ps[2] === "label"
          ? "The name can't be used."
          : ps[2] === "autopilot"
            ? "Something under \"Starts by itself\" can't be used as it is."
            : "Something in this mode can't be used as it is.";
    }
    if (!mode && !p.text) text = "Something else in the room's setup needs fixing first.";
    if (!out.some((x) => x.mode === mode && x.text === text && jsonKey(x.targets) === jsonKey(targets))) out.push({ mode, targets, text });
  }
  return out;
}

/** The room's setup notes (describe/validate warnings), in words. */
function warningWords(ws: RoomProblem[] | undefined, describe: RoomDescribe | null): string {
  const out = new Set<string>();
  for (const w of ws ?? []) {
    if (w.text) {
      out.add(w.text);
      continue;
    }
    if (w.code === "guard_inactive" && describe) {
      const named = (w.message ?? "").split(":")[0].split(", ").filter((t) => describe.targets[t]);
      const kinds = [...new Set(named.map((t) => kindWords(describe, t.split(".")[0])))];
      const list = kinds.map((k) => (/^[A-Z][a-z]/.test(k) ? k.toLowerCase() : k));
      out.add(`The ${list.length ? list.join(" and ") : "devices"} aren't wired up yet, so a switch doesn't wait for them.`);
    } else if (w.code === "unknown_subsystem" || w.code === "placeholder_values_unchecked") {
      out.add("Some things in the room aren't connected yet, so nothing is sent to them.");
    } else if (w.code === "rooms_json") {
      out.add("The list of rooms couldn't be read, so the last good one is used.");
    } else {
      out.add("Something in the room's setup needs a look.");
    }
  }
  return [...out].join(" ");
}

export function ModeEditor({
  name,
  onClose,
  api,
  describe,
  tagIndex,
  onSaved,
  addLog,
  names,
  status: statusIn,
}: {
  /** The mode being edited; null when closed. */
  name: string | null;
  /** Display names that beat a target's raw label (the screens' names from status), by target. */
  names?: Record<string, string>;
  onClose: () => void;
  api: RoomApi;
  describe: RoomDescribe | null;
  /** Mode tags in the scene library ({tag: number of scenes}), for the Art Director's steer. */
  tagIndex: SceneTagIndex;
  /** Not needed: Save works offline too (it keeps the draft and says the room isn't answering). */
  connected?: boolean;
  onSaved: () => void;
  addLog: (e: NewLogEntry) => void;
  /** The room's status (its rooms, the current mode, what each device is doing now); read once on open if not given. */
  status?: RoomStatus | null;
}) {
  const [base, setBase] = useState<Base | null>(null);
  const [draft, setDraft] = useState<RawMode | null>(null);
  const [loadFailed, setLoadFailed] = useState(false);
  const [reload, setReload] = useState(0);
  const [read, setRead] = useState<RoomStatus | null>(null);
  const [busy, setBusy] = useState<"save" | "use" | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [issues, setIssues] = useState<Issue[] | null>(null);
  const [warnings, setWarnings] = useState<RoomProblem[] | null>(null);
  const [conflict, setConflict] = useState<{ base: Base; andUse: boolean } | null>(null);
  const [saved, setSaved] = useState<{ pending: number | null } | null>(null);

  useEffect(() => {
    if (!name) return;
    const ac = new AbortController();
    setBase(null);
    setDraft(null);
    setLoadFailed(false);
    setProblem(null);
    setIssues(null);
    setWarnings(null);
    setConflict(null);
    setSaved(null);
    api
      .roomConfig(ac.signal)
      .then((r) => {
        setBase({ config: r.config, revision: r.revision });
        setDraft(clone(r.config.modes?.[name] ?? {}));
      })
      .catch((e) => {
        if (!(e instanceof DOMException && e.name === "AbortError")) setLoadFailed(true);
      });
    return () => ac.abort();
  }, [api, name, reload]);

  // The room's status, when the caller doesn't pass it: rooms, the current mode, what each device is doing now.
  useEffect(() => {
    if (!name || statusIn !== undefined) return;
    const ac = new AbortController();
    api
      .status(ac.signal)
      .then(setRead)
      .catch(() => {
        /* the editor works without it: one list, schema defaults */
      });
    return () => ac.abort();
  }, [api, name, statusIn]);

  const baseMode = name ? base?.config.modes?.[name] : undefined;
  const dirty = !!draft && !!base && jsonKey(draft) !== jsonKey(baseMode ?? {});

  // Check the draft as it is edited (debounced, read-only): its problems show in words under their rows.
  useEffect(() => {
    if (!name || !base || !draft || !dirty) {
      setIssues(null);
      setWarnings(null);
      return;
    }
    const ac = new AbortController();
    const id = setTimeout(() => {
      checkConfig(api, withMode(base.config, name, draft), ac.signal)
        .then((r) => {
          if (!r) return;
          setIssues(issuesOf(r.errors, describe));
          setWarnings(r.warnings ?? null);
        })
        .catch(() => {
          /* offline: Save says so */
        });
    }, 500);
    return () => {
      clearTimeout(id);
      ac.abort();
    };
  }, [api, name, base, draft, dirty, describe]);

  if (!name) return null;
  const status = statusIn === undefined ? read : statusIn;
  const modeLabel = draft?.label || describe?.modes[name]?.label || friendly(name);
  const labelOf = (m: string) => (m === name ? modeLabel : describe?.modes[m]?.label || friendly(m));
  const current = !!status?.mode && (status.mode.target ? status.mode.target === name : status.mode.current === name);

  const update = (fn: (d: RawMode) => void) =>
    setDraft((d) => {
      if (!d) return d;
      const n = clone(d);
      fn(n);
      const was = baseMode ?? {};
      return jsonKey(n) === jsonKey(was) ? clone(was) : n;   // back as saved: exactly as saved, key order and all
    });

  const plain = (e: unknown): string =>
    e instanceof RoomApiError && (e.status === 0 || e.status >= 500)
      ? NOT_ANSWERING
      : "That didn't save. Your changes are kept; tap Save to try again.";

  /** PUT /api/v1/mode with this mode: switches to it, or (the current one) applies it again. */
  const switchTo = async (again: string) => {
    setBusy("use");
    setProblem(null);
    try {
      await api.setMode(name, "mode-editor");
      addLog({ type: "ui", summary: `mode ${name} used from the editor` });
      onClose();
    } catch (e) {
      const away = e instanceof RoomApiError && (e.status === 0 || e.status >= 500);
      setProblem(`${away ? "The room isn't answering" : "The room didn't switch"}. Tap ${again} to try again.`);
    } finally {
      setBusy(null);
    }
  };

  const persist = async (andUse: boolean, over?: Base) => {
    let b = over ?? base;
    if (!b || !draft || busy) return;
    setBusy(andUse ? "use" : "save");
    setProblem(null);
    setConflict(null);
    setSaved(null);
    try {
      let r: SaveReply | undefined;
      for (let attempt = 0; !r; attempt++) {
        try {
          r = (await api.saveRoomConfig(withMode(b.config, name, draft), b.revision, `${modeLabel} edited on the Room page`)) as SaveReply;
        } catch (e) {
          if (!(e instanceof RoomApiError && e.status === 409) || attempt >= 2) throw e;
          const fresh = await api.roomConfig();
          const theirs: Base = { config: fresh.config, revision: fresh.revision };
          if (jsonKey(fresh.config.modes?.[name] ?? null) !== jsonKey(b.config.modes?.[name] ?? null)) {
            setConflict({ base: theirs, andUse });
            return;
          }
          b = theirs;                                   // someone changed another mode: save this one over theirs
        }
      }
      setBase({ config: withMode(b.config, name, draft), revision: r.revision });
      setIssues(null);
      addLog({ type: "ui", summary: `mode ${name} saved` });
      onSaved();
      if (andUse) {
        await switchTo("Use now");                          // saved: the buttons now read "No changes" / "Use now"
        return;
      }
      if (current && (r.pending_targets?.length || r.deferred)) setSaved({ pending: r.pending_targets?.length ?? null });
      else onClose();
    } catch (e) {
      if (e instanceof RoomApiError && e.errors?.length) setIssues(issuesOf(e.errors, describe));
      else setProblem(plain(e));
    } finally {
      setBusy(null);
    }
  };

  const onSave = () => (dirty ? void persist(false) : onClose());
  const onSaveAndUse = () => (busy ? undefined : dirty ? void persist(true) : void switchTo("Use now"));

  const mine = (issues ?? []).filter((i) => i.mode === name);
  const elsewhere = (issues ?? []).filter((i) => i.mode !== name);
  const byTarget: Record<string, string[]> = {};
  for (const i of mine) for (const t of i.targets) byTarget[t] = [...(byTarget[t] ?? []), i.text];
  const general = [...mine.filter((i) => !i.targets.length).map((i) => i.text), ...elsewhere.map((i) => (i.mode ? `${labelOf(i.mode)}: ${i.text}` : i.text))];
  const count = (issues ?? []).length;
  const notes = warningWords(warnings ?? (describe as (RoomDescribe & { warnings?: RoomProblem[] }) | null)?.warnings, describe);

  return (
    <Sheet open={!!name} onOpenChange={(o) => !o && onClose()}>
      <SheetContent side="right" className="w-full overflow-y-auto bg-background sm:max-w-lg">
        <SheetHeader className="text-left">
          <SheetTitle>Edit {modeLabel}</SheetTitle>
          <SheetDescription>What the room does in this mode.</SheetDescription>
        </SheetHeader>

        {loadFailed && (
          <Notice tone="warn" className="mt-4">
            <p>The room isn't answering.</p>
            <button type="button" onClick={() => setReload((n) => n + 1)} className="mt-1 min-h-9 font-semibold underline underline-offset-2">
              Try again
            </button>
          </Notice>
        )}
        {!draft || !describe ? (
          !loadFailed && (
            <p className="mt-6 flex items-center gap-2 text-base text-muted-foreground">
              <Spinner /> Loading…
            </p>
          )
        ) : (
          <div className="mt-4 space-y-5 pb-40">
            {notes && <Notice tone="warn">{notes}</Notice>}
            {count > 0 && (
              <Notice tone="warn">
                <p className="font-semibold">
                  {modeLabel} can't be saved yet: {count} {count === 1 ? "thing" : "things"} to fix
                </p>
                {general.length > 0 && (
                  <ul className="mt-1 list-disc pl-5">
                    {general.map((g) => (
                      <li key={g}>{g}</li>
                    ))}
                  </ul>
                )}
              </Notice>
            )}

            <div className="space-y-3">
              <label className="block">
                <span className="text-sm font-medium text-stone-700">Name</span>
                <Input
                  className="mt-1 h-11 text-base"
                  value={draft.label ?? ""}
                  placeholder={friendly(name)}
                  onChange={(e) => {
                    const v = e.currentTarget.value;
                    update((d) => {
                      d.label = v;
                    });
                  }}
                />
              </label>
              <label className="block">
                <span className="text-sm font-medium text-stone-700">Description (optional)</span>
                <Input
                  className="mt-1 h-11 text-base"
                  value={typeof draft.description === "string" ? draft.description : ""}
                  onChange={(e) => {
                    const v = e.currentTarget.value;
                    update((d) => {
                      if (v) d.description = v;
                      else delete d.description;
                    });
                  }}
                />
              </label>
              <label className="block">
                <span className="text-sm font-medium text-stone-700">Voice name (optional)</span>
                <Input
                  className="mt-1 h-11 text-base"
                  value={typeof draft.voice === "string" ? draft.voice : ""}
                  maxLength={40}
                  placeholder="e.g. party"
                  onChange={(e) => {
                    const v = e.currentTarget.value;
                    update((d) => {
                      if (v.trim()) d.voice = v;
                      else delete d.voice;
                    });
                  }}
                />
                <span className="mt-1 block text-xs text-stone-500">
                  For Alexa: “computer, use AI to switch to {(typeof draft.voice === "string" && draft.voice.trim()) || "party"} mode”.
                  Keep it short and unlike the others. Without one, this mode can’t be chosen by voice.
                </span>
              </label>
            </div>

            <div>
              <h3 className="mb-2 text-base font-semibold text-stone-800">In this mode</h3>
              <ModeTargets
                name={name}
                draft={draft}
                baseMode={baseMode}
                update={update}
                describe={describe}
                status={status}
                names={names}
                issues={byTarget}
                renderValue={(t, value, onChange) => {
                  const schema = describe.targets[t]?.schema as Schema | undefined;
                  return schema && editable(schema) ? (
                    <ValueControl schema={schema} value={value} tagIndex={tagIndex} onChange={onChange} />
                  ) : null;
                }}
                words={(t, v) => inWords(describe.targets[t]?.schema as Schema | undefined, v)}
                wholeMode={
                  base && (
                    <AutoTriggers
                      name={name}
                      draft={draft}
                      baseMode={baseMode}
                      update={update}
                      describe={describe}
                      api={api}
                      config={base.config}
                      status={status}
                      names={names}
                    />
                  )
                }
              />
            </div>
          </div>
        )}

        <div className="fixed inset-x-0 bottom-0 space-y-2 border-t border-stone-200 bg-background/95 px-4 py-3 backdrop-blur sm:absolute">
          {conflict && (
            <div className="rounded-xl border border-amber-200 bg-amber-50 p-3 text-sm text-amber-900">
              <p className="font-semibold">Someone else changed {modeLabel} too.</p>
              <div className="mt-2 grid grid-cols-2 gap-2">
                <Button
                  variant="outline"
                  className="h-11 rounded-xl bg-white text-[15px]"
                  onClick={() => {
                    const c = conflict;
                    setBase(c.base);
                    void persist(c.andUse, c.base);
                  }}
                >
                  Keep mine
                </Button>
                <Button
                  variant="outline"
                  className="h-11 rounded-xl bg-white text-[15px]"
                  onClick={() => {
                    setBase(conflict.base);
                    setDraft(clone(conflict.base.config.modes?.[name] ?? {}));
                    setConflict(null);
                  }}
                >
                  Use theirs
                </Button>
              </div>
            </div>
          )}
          {saved && (
            <p className="flex flex-wrap items-center gap-x-1.5 text-sm text-stone-700">
              <span>
                Saved ·{" "}
                {saved.pending === null
                  ? "it waits for the switch under way"
                  : `${saved.pending} ${saved.pending === 1 ? "change waits" : "changes wait"} for the next switch`}{" "}
                ·
              </span>
              <button
                type="button"
                onClick={() => !busy && void switchTo("Apply now")}
                className="min-h-9 font-semibold text-teal-800 underline underline-offset-2"
              >
                {busy === "use" ? <Spinner className="mr-1 inline" /> : null}Apply now
              </button>
            </p>
          )}
          {problem && <p className="text-sm font-medium text-amber-800">{problem}</p>}
          <div className="grid grid-cols-[auto_1fr_1.5fr] gap-2">
            <Button variant="outline" className="h-11 rounded-xl bg-white px-4 text-[15px]" onClick={onClose}>
              {saved ? "Close" : "Cancel"}
            </Button>
            <Button
              variant={current ? "outline" : "default"}
              className={cn("h-11 rounded-xl px-2 text-[15px]", current && "bg-white")}
              onClick={onSave}
            >
              {busy === "save" ? <Spinner className="mr-1.5" /> : null}
              {dirty ? "Save" : "No changes"}
            </Button>
            <Button
              variant={current ? "default" : "outline"}
              className={cn("h-11 whitespace-nowrap rounded-xl px-2 text-[15px]", !current && "bg-white")}
              onClick={onSaveAndUse}
            >
              {busy === "use" ? <Spinner className="mr-1.5" /> : null}
              {dirty ? "Save and use now" : "Use now"}
            </Button>
          </div>
        </div>
      </SheetContent>
    </Sheet>
  );
}
