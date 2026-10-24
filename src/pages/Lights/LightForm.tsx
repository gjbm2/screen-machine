// Add a light (one question at a time, five steps) and Edit a DMX light (the same questions on one sheet).
//   1. What kind of light it is (the types the room knows, the plain generic ones first).
//   2. Where it's plugged in: the Art-Net box's address and the universe (1 unless the box says otherwise).
//   3. Its first channel, with the channels it uses and any light it would share them with. Next SAVES the light
//      (POST /show/api/v1/rig/fixtures under a provisional name: its kind and the next number), so it exists and can
//      be driven straight away (docs/LIGHTS.md §8.2).
//   4. Move it (MoveIt.tsx): drive the real light, store its home, limits and positions, point it at places on the Room
//      map; a light that doesn't move shows only what it can do ("Check it lights").
//   5. Its name and where it is in the room (PUT with only what changed).
// Closing after step 3 leaves the light registered and offers "Remove this light". Edit shows the same questions with
// Move it as a section that unrolls ("Move it and set it up"). The server's problems come back in its words, inline.
// After saving a new light the sheet says which modes it follows the music in, and offers Identify and "Put it on the
// Room map".

import { useEffect, useMemo, useRef, useState, type MutableRefObject, type ReactNode } from "react";
import { Check, ChevronLeft, MapPin, Trash2 } from "lucide-react";
import { AppLink } from "@/components/nav/AppLink";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { cn } from "@/lib/utils";
import { Notice, Spinner, Unroll } from "@/pages/Room/bits";
import { ActionSheet } from "@/pages/Room/overlays";
import { TAP_BUTTON_CLASS } from "@/pages/Room/theme";
import { explainLightsError, LightsApiError, type FoundBox, type LightsApi } from "./api";
import { IdentifyButton, useIdentify } from "./Identify";
import { MoveIt } from "./MoveIt";
import {
  changedBody,
  checkDraft,
  draftBody,
  draftFromRow,
  emptyDraft,
  FIELD_STEP,
  footprintWords,
  isGeneric,
  nextFreeChannel,
  overlapsWith,
  placementChoices,
  problemWords,
  provisionalName,
  rowsFromRig,
  andList,
  wholeNumber,
  type ModeUse,
  type Draft,
  type DraftField,
  type LightRow,
  type LightsUi,
  type LightType,
} from "./lib";
import type { TypesState } from "./useRig";

const STEPS = 5;
const MOVE_STEP = 4;
const INPUT = "h-11 rounded-xl bg-white text-base";
const CHIP =
  "inline-flex min-h-11 items-center rounded-full border px-3.5 text-[15px] font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring";
const CHIP_ON = "border-teal-300 bg-teal-50 text-teal-900";
const CHIP_OFF = "border-stone-200 bg-white text-stone-700 hover:bg-stone-50";

/** The sheet: Add when `editing` is null, else Edit that light. Its content (and so the form) resets on every open. */
export function LightSheet({
  open,
  onOpenChange,
  editing,
  api,
  rows,
  types,
  ui,
  onSaved,
  onShowOnMap,
  inModes,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  editing: LightRow | null;
  api: LightsApi;
  rows: LightRow[];
  types: TypesState;
  /** Move it's numbers (show.yaml lights.jog). */
  ui: LightsUi;
  /** Read the rig again. */
  onSaved: () => void;
  /** Open the Room map with this light selected. */
  onShowOnMap?: (id: string) => void;
  /** The modes that set a DMX light (by its id), for the saved panel; absent while the modes aren't known. */
  inModes?: (id: string) => ModeUse[];
}) {
  // Closing the sheet half way through adding asks first (the light is already saved): the form says when.
  const guard = useRef<(() => boolean) | null>(null);
  return (
    <ActionSheet
      open={open}
      onOpenChange={(o) => {
        if (!o && guard.current?.()) return;
        onOpenChange(o);
      }}
      title={editing ? `Edit ${editing.label}` : "Add a light"}
      testId="light-sheet"
    >
      <LightForm
        key={editing?.id ?? "_add"}
        editing={editing}
        api={api}
        rows={rows}
        types={types}
        ui={ui}
        guard={guard}
        onSaved={onSaved}
        inModes={inModes}
        onClose={() => onOpenChange(false)}
        onShowOnMap={
          onShowOnMap
            ? (id) => {
                onOpenChange(false);
                onShowOnMap(id);
              }
            : undefined
        }
      />
    </ActionSheet>
  );
}

function FieldError({ text }: { text?: string }) {
  return text ? <p className="text-sm text-rose-700">{text}</p> : null;
}

/** "Boxes found": the Art-Net boxes that answered a search of the LAN (GET /show/api/v1/rig/nodes/find), as chips
 *  that fill in the address; typing it stays the way when none answer. Searched once when the step opens, and again
 *  on "Look again". A show_service without the route (or out of reach) just shows nothing here. */
function BoxesFound({ api, chosen, skip, onPick }: { api: LightsApi; chosen: string; skip: string[]; onPick: (ip: string) => void }) {
  const [found, setFound] = useState<FoundBox[] | null>(null);
  const [words, setWords] = useState<string | null>(null);
  const [looking, setLooking] = useState(false);
  const [gone, setGone] = useState(false);
  const [round, setRound] = useState(0);

  useEffect(() => {
    const ac = new AbortController();
    setLooking(true);
    api
      .findBoxes(ac.signal)
      .then((r) => {
        setFound(Array.isArray(r?.nodes) ? r.nodes.filter((n) => n && typeof n.ip === "string") : []);
        setWords(typeof r?.words === "string" ? r.words : null);
        setGone(false);
      })
      .catch((e) => {
        if (e instanceof DOMException && e.name === "AbortError") return;
        setGone(true);
      })
      .finally(() => {
        if (!ac.signal.aborted) setLooking(false);
      });
    return () => ac.abort();
  }, [api, round]);

  if (gone) return null;
  const shownBoxes = (found ?? []).filter((b) => !skip.includes(b.ip));
  return (
    <div className="space-y-1.5 pt-1" data-testid="boxes-found">
      <p className="flex items-center gap-2 text-sm text-muted-foreground">
        {looking ? (
          <>
            <Spinner /> Looking for boxes on the network…
          </>
        ) : shownBoxes.length > 0 ? (
          "Boxes found:"
        ) : (
          (found?.length ? "No other boxes answered." : words) ?? "No Art-Net box answered. Type the box's address instead."
        )}
        {!looking && (
          <button type="button" onClick={() => setRound((n) => n + 1)} className="min-h-9 font-medium text-teal-800 underline-offset-4 hover:underline">
            Look again
          </button>
        )}
      </p>
      {shownBoxes.length > 0 && (
        <div className="flex flex-wrap gap-2">
          {shownBoxes.map((b) => (
            <button
              key={b.ip}
              type="button"
              onClick={() => onPick(b.ip)}
              className={cn(CHIP, chosen === b.ip ? CHIP_ON : CHIP_OFF)}
              title={b.heard === "earlier" ? `${b.label}: answered a few minutes ago` : b.label}
            >
              <span className="max-w-[10rem] truncate">{b.name}</span>
              <span className="ml-1.5 font-mono font-normal text-muted-foreground">{b.ip}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

function StepHead({ n, title, sub, adding }: { n: number; title: string; sub?: ReactNode; adding: boolean }) {
  return (
    <div className="mb-3">
      {adding && <p className="text-sm font-medium text-muted-foreground">Step {n} of {STEPS}</p>}
      <h3 className="text-[17px] font-semibold leading-snug">{adding ? title : `${n}. ${title}`}</h3>
      {sub && <p className="mt-0.5 text-[15px] leading-snug text-muted-foreground">{sub}</p>}
    </div>
  );
}

/** One kind of light, as a tap-to-pick tile. */
function TypeTile({ t, on, onPick }: { t: LightType; on: boolean; onPick: () => void }) {
  return (
    <button
      type="button"
      role="radio"
      aria-checked={on}
      onClick={onPick}
      data-testid={`type-${t.id}`}
      className={cn(
        "flex min-h-14 w-full items-center gap-3 rounded-xl border px-3 py-2 text-left transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
        on ? CHIP_ON : CHIP_OFF,
      )}
    >
      <span className="min-w-0 flex-1">
        <span className="block text-base font-semibold leading-snug">{t.label}</span>
        {t.channels.length > 0 && (
          <span className="block truncate text-sm text-muted-foreground">{t.channels.join(" · ")}</span>
        )}
      </span>
      {on && <Check className="h-5 w-5 shrink-0 text-teal-700" aria-hidden />}
    </button>
  );
}

interface Saved {
  id: string | null;
  label: string;
  words: string | null;
  problems: string[];
}

function LightForm({
  editing,
  api,
  rows,
  types: typesState,
  ui,
  guard,
  onSaved,
  onClose,
  onShowOnMap,
  inModes,
}: {
  editing: LightRow | null;
  api: LightsApi;
  rows: LightRow[];
  types: TypesState;
  ui: LightsUi;
  guard: MutableRefObject<(() => boolean) | null>;
  onSaved: () => void;
  onClose: () => void;
  onShowOnMap?: (id: string) => void;
  inModes?: (id: string) => ModeUse[];
}) {
  const adding = editing === null;
  // Adding: the light as saved at the end of step 3 (under its provisional name), so Move it can drive it.
  const [created, setCreated] = useState<LightRow | null>(null);
  const [leaving, setLeaving] = useState(false);
  const [draft, setDraft] = useState<Draft>(() => (editing ? draftFromRow(editing) : emptyDraft()));
  const [step, setStep] = useState(1);
  // The steps (or, editing, the whole form) the person has tried to leave: their problems show from then on.
  const [tried, setTried] = useState<Set<number>>(() => new Set());
  const [saving, setSaving] = useState(false);
  const [failure, setFailure] = useState<{ text: string; problems: string[] } | null>(null);
  const [saved, setSaved] = useState<Saved | null>(null);
  const [freeText, setFreeText] = useState(false);

  const types = typesState.types ?? [];
  const type = types.find((t) => t.id === draft.type) ?? null;
  const footprint = type?.footprint ?? editing?.footprint ?? null;
  const problems = checkDraft(draft, type);
  const shown = (f: DraftField) => (tried.has(adding ? FIELD_STEP[f] : 0) ? problems[f] : undefined);
  const set = (patch: Partial<Draft>) => {
    setDraft((d) => ({ ...d, ...patch }));
    setFailure(null);
  };

  const others = useMemo(() => rows.filter((r) => r.id !== (editing?.id ?? created?.id)), [rows, editing?.id, created?.id]);
  const universe = wholeNumber(draft.universe);
  const address = wholeNumber(draft.address);
  const nextFree = nextFreeChannel(others, draft.node_ip, universe);
  const overlaps = overlapsWith(others, draft.node_ip, universe, address, footprint);
  const boxes = useMemo(() => {
    const seen = new Map<string, string[]>();
    for (const r of others) if (r.node_ip) seen.set(r.node_ip, [...(seen.get(r.node_ip) ?? []), r.label]);
    return [...seen.entries()];
  }, [others]);
  const places = useMemo(() => placementChoices(rows), [rows]);
  const placeKey = draft.placement ? `${draft.placement}\u0000${draft.zone ?? ""}` : null;
  const placeIsChoice = !!placeKey && places.some((p) => p.key === placeKey);
  const typing = freeText || (!!draft.placement && !placeIsChoice);

  const stepOk = (n: number) => (Object.keys(problems) as DraftField[]).every((f) => FIELD_STEP[f] !== n);

  useEffect(() => {
    guard.current = () => {
      if (!created || saved || leaving) return false;
      setLeaving(true);
      return true;
    };
    return () => {
      guard.current = null;
    };
  });

  /** Step 3's Next: save the light (POST under a provisional name; once saved, PUT any change to steps 1-3). */
  const saveWiring = async () => {
    setTried((t) => new Set(t).add(1).add(2).add(3));
    const bad = [1, 2, 3].find((n) => !stepOk(n));
    if (bad !== undefined) {
      setStep(bad);
      return;
    }
    setSaving(true);
    setFailure(null);
    try {
      let row: LightRow | null = created;
      let notes: string[] = [];
      if (!created) {
        const label = draft.label.trim() || provisionalName(type, rows);
        const res = await api.add(draftBody({ ...draft, label }));
        row = rowsFromRig({ fixtures: res?.fixture ? [res.fixture] : [] })[0] ?? null;
        notes = problemWords(res?.problems);
        if (!row) {
          setFailure({ text: "That wasn't saved:", problems: notes });
          return;
        }
        setDraft((d) => ({ ...d, label: row?.label ?? label }));
      } else {
        const body = changedBody({ ...draft, label: created.label }, created);
        if (Object.keys(body).length) {
          const res = await api.update(created.id, body);
          row = rowsFromRig({ fixtures: res?.fixture ? [res.fixture] : [] })[0] ?? created;
          notes = problemWords(res?.problems);
        }
      }
      setCreated(row);
      onSaved();
      if (notes.length) setFailure({ text: "Saved. Worth a look:", problems: notes });
      setStep(MOVE_STEP);
    } catch (e) {
      setFailure({ text: explainLightsError(e), problems: e instanceof LightsApiError ? e.problems : [] });
    } finally {
      setSaving(false);
    }
  };

  const removeCreated = async () => {
    if (!created) return;
    setSaving(true);
    try {
      await api.remove(created.id);
      onSaved();
      onClose();
    } catch (e) {
      setFailure({ text: explainLightsError(e), problems: e instanceof LightsApiError ? e.problems : [] });
      setLeaving(false);
    } finally {
      setSaving(false);
    }
  };

  const save = async () => {
    setTried(new Set([0, 1, 2, 3, 4, 5]));
    if (Object.keys(problems).length) {
      if (adding) setStep(Math.min(...(Object.keys(problems) as DraftField[]).map((f) => FIELD_STEP[f])));
      return;
    }
    const target = editing ?? created;
    const body = target ? changedBody(draft, target) : draftBody(draft);
    if (target && Object.keys(body).length === 0) {
      if (editing) {
        onClose();
        return;
      }
      setSaved({ id: target.id, label: target.label, words: footprintWords(target.address, target.footprint), problems: [] });
      return;
    }
    setSaving(true);
    setFailure(null);
    try {
      const res = target ? await api.update(target.id, body) : await api.add(body);
      const row = rowsFromRig({ fixtures: res?.fixture ? [res.fixture] : [] })[0] ?? null;
      const notes = problemWords(res?.problems);
      if (!res?.fixture && notes.length) {
        // Nothing was saved: the problems say why.
        setFailure({ text: "That wasn't saved:", problems: notes });
        return;
      }
      onSaved();
      const words = footprintWords(row?.address ?? address, row?.footprint ?? footprint);
      if (editing && notes.length === 0) {
        onClose();
        return;
      }
      setSaved({ id: row?.id ?? target?.id ?? null, label: row?.label ?? draft.label.trim(), words, problems: notes });
    } catch (e) {
      setFailure({ text: explainLightsError(e), problems: e instanceof LightsApiError ? e.problems : [] });
    } finally {
      setSaving(false);
    }
  };

  if (saved) {
    return (
      <SavedPanel
        saved={saved}
        inModes={inModes}
        api={api}
        adding={adding}
        onAnother={() => {
          setSaved(null);
          setCreated(null);
          setDraft((d) => ({ ...emptyDraft(), type: d.type, node_ip: d.node_ip, universe: d.universe }));
          setTried(new Set());
          setFreeText(false);
          setStep(3);
        }}
        onClose={onClose}
        onShowOnMap={onShowOnMap}
      />
    );
  }

  if (leaving && created) {
    return (
      <div className="space-y-4 pb-1" data-testid="light-leaving">
        <p className="text-[17px] font-semibold">{created.label} is already saved</p>
        <p className="text-[15px] text-muted-foreground">The show can drive it now. Keep it as it is (you can name it later with Edit), or remove it.</p>
        <div className="flex flex-wrap justify-end gap-2">
          <Button variant="ghost" onClick={() => void removeCreated()} disabled={saving} className={cn(TAP_BUTTON_CLASS, "text-rose-700 hover:bg-rose-50")}>
            {saving ? <Spinner /> : <Trash2 aria-hidden />} Remove this light
          </Button>
          <Button onClick={onClose} className={TAP_BUTTON_CLASS} data-testid="light-keep">
            Keep it
          </Button>
        </div>
      </div>
    );
  }

  // -- the four questions -----------------------------------------------------------------------------------------------

  const typeList = (
    <TypeList
      typesState={typesState}
      current={draft.type}
      onPick={(id) => {
        set({ type: id });
        if (adding) setStep(2);
      }}
    />
  );

  const q1 = (
    <section data-testid="step-kind">
      <StepHead n={1} title="What kind of light is it?" sub="Pick the one with the same channels as the light's manual." adding={adding} />
      <div role="radiogroup" aria-label="What kind of light">
        {typeList}
      </div>
      <FieldError text={shown("type")} />
    </section>
  );

  const q2 = (
    <section data-testid="step-box" className="space-y-3">
      <StepHead n={2} title="Where is it plugged in?" sub="The Art-Net box its DMX cable goes to." adding={adding} />
      <div className="space-y-1.5">
        <Label htmlFor="light-ip" className="text-[15px]">
          The box's address
        </Label>
        <Input
          id="light-ip"
          value={draft.node_ip}
          onChange={(e) => set({ node_ip: e.target.value })}
          inputMode="decimal"
          autoCapitalize="off"
          autoCorrect="off"
          spellCheck={false}
          autoComplete="off"
          className={cn(INPUT, "font-mono")}
          aria-invalid={!!shown("node_ip")}
          data-testid="field-ip"
        />
        {boxes.length > 0 && (
          <div className="flex flex-wrap gap-2 pt-1">
            {boxes.map(([ip, labels]) => (
              <button
                key={ip}
                type="button"
                onClick={() => set({ node_ip: ip })}
                className={cn(CHIP, draft.node_ip.trim() === ip ? CHIP_ON : CHIP_OFF)}
                title={`The box ${labels.join(", ")} ${labels.length === 1 ? "is" : "are"} on`}
              >
                <span className="font-mono">{ip}</span>
                <span className="ml-1.5 max-w-[10rem] truncate font-normal text-muted-foreground">with {labels[0]}</span>
              </button>
            ))}
          </div>
        )}
        <BoxesFound api={api} chosen={draft.node_ip.trim()} skip={boxes.map(([ip]) => ip)} onPick={(ip) => set({ node_ip: ip })} />
        <FieldError text={shown("node_ip")} />
      </div>
      <div className="space-y-1.5">
        <Label htmlFor="light-universe" className="text-[15px]">
          Universe
        </Label>
        <Input
          id="light-universe"
          value={draft.universe}
          onChange={(e) => set({ universe: e.target.value })}
          inputMode="numeric"
          pattern="[0-9]*"
          autoComplete="off"
          className={cn(INPUT, "w-28 font-mono")}
          aria-invalid={!!shown("universe")}
          data-testid="field-universe"
        />
        <p className="text-sm text-muted-foreground">1 unless the box has more than one DMX socket and it's in another.</p>
        <FieldError text={shown("universe")} />
      </div>
    </section>
  );

  const fpWords = footprintWords(address, footprint);
  const q3 = (
    <section data-testid="step-channel" className="space-y-3">
      <StepHead n={3} title="Its first channel" sub="The DMX address set on the light itself (1 to 512)." adding={adding} />
      <div className="flex flex-wrap items-center gap-2">
        <Input
          id="light-address"
          aria-label="First channel"
          value={draft.address}
          onChange={(e) => set({ address: e.target.value })}
          inputMode="numeric"
          pattern="[0-9]*"
          autoComplete="off"
          className={cn(INPUT, "w-28 font-mono")}
          aria-invalid={!!shown("address")}
          data-testid="field-address"
        />
        {nextFree !== null && nextFree !== address && (
          <button type="button" onClick={() => set({ address: String(nextFree) })} className={cn(CHIP, CHIP_OFF)}>
            Next free: {nextFree}
          </button>
        )}
      </div>
      {fpWords && (
        <p className="text-[15px]" data-testid="footprint">
          {fpWords}.
        </p>
      )}
      {!fpWords && footprint && <p className="text-[15px] text-muted-foreground">It uses {footprint} channels.</p>}
      <FieldError text={shown("address")} />
      {overlaps.length > 0 && !problems.address && (
        <Notice tone="warn" className="text-[15px]">
          {overlaps.map((o) => (
            <p key={o.id}>
              Shares channels with {o.label} (channels {o.from === o.to ? o.from : `${o.from}–${o.to}`}). Both will show
              the same thing unless one moves.
            </p>
          ))}
        </Notice>
      )}
    </section>
  );

  const q4 = (
    <section data-testid="step-name" className="space-y-3">
      <StepHead n={adding ? STEPS : 4} title="Its name and where it is" adding={adding} />
      <div className="space-y-1.5">
        <Label htmlFor="light-name" className="text-[15px]">
          Name
        </Label>
        <Input
          id="light-name"
          value={draft.label}
          onChange={(e) => set({ label: e.target.value })}
          autoComplete="off"
          className={INPUT}
          aria-invalid={!!shown("label")}
          data-testid="field-name"
        />
        <FieldError text={shown("label")} />
      </div>
      <div className="space-y-1.5">
        <p className="text-[15px] font-medium">Where it is in the room</p>
        <div role="radiogroup" aria-label="Where it is in the room" className="flex flex-wrap gap-2">
          {places.map((p) => {
            const on = !typing && placeKey === p.key;
            return (
              <button
                key={p.key}
                type="button"
                role="radio"
                aria-checked={on}
                onClick={() => {
                  setFreeText(false);
                  set({ placement: p.placement, zone: p.zone });
                }}
                className={cn(CHIP, on ? CHIP_ON : CHIP_OFF)}
              >
                {p.words}
              </button>
            );
          })}
          <button
            type="button"
            role="radio"
            aria-checked={typing}
            onClick={() => {
              setFreeText(true);
              if (placeIsChoice) set({ placement: null, zone: null });
            }}
            className={cn(CHIP, typing ? CHIP_ON : CHIP_OFF)}
          >
            Somewhere else…
          </button>
          <button
            type="button"
            role="radio"
            aria-checked={!typing && !draft.placement}
            onClick={() => {
              setFreeText(false);
              set({ placement: null, zone: null });
            }}
            className={cn(CHIP, !typing && !draft.placement ? CHIP_ON : CHIP_OFF)}
          >
            Not saying
          </button>
        </div>
        {typing && (
          <Input
            aria-label="Where it is, in your words"
            value={draft.placement ?? ""}
            onChange={(e) => set({ placement: e.target.value || null, zone: null })}
            autoComplete="off"
            className={INPUT}
            data-testid="field-placement"
            autoFocus
          />
        )}
      </div>
    </section>
  );

  const failureNote = failure && (
    <Notice tone="bad" className="text-[15px]">
      <p>{failure.text}</p>
      {failure.problems.length > 0 && (
        <ul className="mt-1 list-disc space-y-0.5 pl-5">
          {failure.problems.map((p) => (
            <li key={p}>{p}</li>
          ))}
        </ul>
      )}
    </Notice>
  );

  // -- edit: everything on one sheet --------------------------------------------------------------------------------------
  if (!adding) {
    return (
      <div className="space-y-6 pb-1">
        {q1}
        {q2}
        {q3}
        {editing.address !== null && (
          <Unroll title={<span className="text-[17px] font-semibold">Move it and set it up</span>} testId="edit-move-it" revealOnOpen>
            <MoveIt api={api} row={editing} ui={ui} onChanged={onSaved} />
          </Unroll>
        )}
        {q4}
        {failureNote}
        <div className="sticky bottom-0 -mx-1 flex flex-wrap justify-end gap-2 bg-background px-1 pb-1 pt-2">
          <Button variant="outline" onClick={onClose} className={cn(TAP_BUTTON_CLASS, "bg-white")}>
            Cancel
          </Button>
          <Button onClick={() => void save()} disabled={saving} className={TAP_BUTTON_CLASS} data-testid="light-save">
            {saving && <Spinner />} Save changes
          </Button>
        </div>
      </div>
    );
  }

  // -- add: one question at a time ----------------------------------------------------------------------------------------
  const next = () => {
    if (step === 3) {
      void saveWiring();
      return;
    }
    setTried((t) => new Set(t).add(step));
    if (stepOk(step)) setStep((s) => Math.min(STEPS, s + 1));
  };
  const moveStep = created && (
    <section data-testid="step-move" className="space-y-3">
      <StepHead
        n={MOVE_STEP}
        title={created.caps?.moves ? "Move it" : "Check it lights"}
        sub={created.caps?.moves ? "It's saved as “" + created.label + "”: move the real light, and store where it rests and where it may go." : "It's saved as “" + created.label + "”: try what it can do."}
        adding
      />
      <MoveIt api={api} row={created} ui={ui} onChanged={onSaved} />
    </section>
  );
  return (
    <div className="space-y-4 pb-1">
      {step === 1 && q1}
      {step === 2 && q2}
      {step === 3 && q3}
      {step === MOVE_STEP && moveStep}
      {step === STEPS && q4}
      {step >= 3 && failureNote}
      {step > 1 && (
        <p className="text-sm text-muted-foreground" data-testid="add-so-far">
          {[created?.label, type?.label, draft.node_ip.trim() && `box ${draft.node_ip.trim()}`, universe !== null && `universe ${universe}`, step > 3 && fpWords]
            .filter(Boolean)
            .join(" · ")}
        </p>
      )}
      <div className="sticky bottom-0 -mx-1 flex items-center gap-2 bg-background px-1 pb-1 pt-2">
        {step > 1 && (
          <Button variant="ghost" onClick={() => setStep((s) => s - 1)} className={cn(TAP_BUTTON_CLASS, "px-3")}>
            <ChevronLeft aria-hidden /> Back
          </Button>
        )}
        {created && step > 3 && (
          <Button variant="ghost" onClick={() => setLeaving(true)} disabled={saving} className={cn(TAP_BUTTON_CLASS, "px-3 text-rose-700 hover:bg-rose-50")}>
            Remove this light
          </Button>
        )}
        <span className="flex-1" />
        {step < STEPS ? (
          (step > 1 || !!draft.type) && (
            <Button onClick={next} disabled={saving} className={TAP_BUTTON_CLASS} data-testid="light-next">
              {saving && <Spinner />} {step === 3 && !created ? "Save and move it" : "Next"}
            </Button>
          )
        ) : (
          <Button onClick={() => void save()} disabled={saving} className={TAP_BUTTON_CLASS} data-testid="light-save">
            {saving && <Spinner />} Save
          </Button>
        )}
      </div>
    </div>
  );
}

function TypeList({
  typesState,
  current,
  onPick,
}: {
  typesState: TypesState;
  current: string | null;
  onPick: (id: string) => void;
}) {
  const { types, error, reload } = typesState;
  if (!types) {
    return error ? (
      <Notice tone="warn" className="space-y-2 text-[15px]">
        <p>{error}</p>
        <Button variant="outline" onClick={reload} className="h-11 rounded-xl bg-white text-[15px]">
          Try again
        </Button>
      </Notice>
    ) : (
      <p className="flex items-center gap-2 py-2 text-base text-muted-foreground">
        <Spinner /> Reading the kinds of light…
      </p>
    );
  }
  if (types.length === 0) {
    return <p className="py-2 text-base text-muted-foreground">The room doesn't know any kinds of DMX light yet.</p>;
  }
  const generic = types.filter(isGeneric);
  const more = types.filter((t) => !isGeneric(t));
  // A kind picked from the longer list keeps it open, so the pick stays in view.
  const moreOpen = !!current && more.some((t) => t.id === current);
  return (
    <div className="space-y-2">
      {(generic.length ? generic : more).map((t) => (
        <TypeTile key={t.id} t={t} on={t.id === current} onPick={() => onPick(t.id)} />
      ))}
      {generic.length > 0 && more.length > 0 && (
        <Unroll title="More kinds" hint={`${more.length}`} defaultOpen={moreOpen}>
          <div className="space-y-2">
            {more.map((t) => (
              <TypeTile key={t.id} t={t} on={t.id === current} onPick={() => onPick(t.id)} />
            ))}
          </div>
        </Unroll>
      )}
    </div>
  );
}

function SavedPanel({
  saved,
  inModes,
  api,
  adding,
  onAnother,
  onClose,
  onShowOnMap,
}: {
  saved: Saved;
  inModes?: (id: string) => ModeUse[];
  api: LightsApi;
  adding: boolean;
  onAnother: () => void;
  onClose: () => void;
  onShowOnMap?: (id: string) => void;
}) {
  const ident = useIdentify(api, saved.id);
  return (
    <div className="space-y-4 pb-1" data-testid="light-saved">
      <p className="flex items-center gap-2 text-[17px] font-semibold">
        <Check className="h-5 w-5 text-teal-700" aria-hidden /> {saved.label} is saved
      </p>
      {saved.words && <p className="text-[15px] text-muted-foreground">{saved.words}.</p>}
      {saved.id && inModes && <FollowsIn uses={inModes(saved.id)} />}
      {saved.problems.length > 0 && (
        <Notice tone="warn" className="text-[15px]">
          <p>Worth a look:</p>
          <ul className="mt-1 list-disc space-y-0.5 pl-5">
            {saved.problems.map((p) => (
              <li key={p}>{p}</li>
            ))}
          </ul>
        </Notice>
      )}
      {saved.id && (
        <div className="space-y-1.5">
          <p className="text-[15px]">Find it: it flashes for a few seconds, whatever it was doing.</p>
          <IdentifyButton state={ident.state} onClick={() => void ident.run()} label={saved.label} wide className="h-12 px-4 text-base" />
          {ident.why && <p className="text-sm text-rose-700">{ident.why}</p>}
        </div>
      )}
      <div className="flex flex-wrap justify-end gap-2 pt-1">
        {saved.id && onShowOnMap && (
          <Button variant="outline" onClick={() => onShowOnMap(saved.id as string)} className={cn(TAP_BUTTON_CLASS, "bg-white")}>
            <MapPin aria-hidden /> Put it on the Room map
          </Button>
        )}
        {adding && (
          <Button variant="outline" onClick={onAnother} className={cn(TAP_BUTTON_CLASS, "bg-white")}>
            Add another
          </Button>
        )}
        <Button onClick={onClose} className={TAP_BUTTON_CLASS} data-testid="light-done">
          Done
        </Button>
      </div>
    </div>
  );
}

/** "It follows the music in: Party, Disco and Ambient", from the modes as the room has them now. */
function FollowsIn({ uses }: { uses: ModeUse[] }) {
  const follows = uses.filter((u) => u.follows).map((u) => u.label);
  return (
    <p className="text-[15px]" data-testid="saved-modes">
      {follows.length ? `It follows the music in: ${andList(follows)}.` : "No mode makes it follow the music yet."}{" "}
      <AppLink href="/room#modes" className="font-medium text-teal-800 underline-offset-4 hover:underline">
        Change a mode ›
      </AppLink>
    </p>
  );
}
