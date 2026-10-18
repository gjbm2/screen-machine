// Add a light (one question at a time, four steps) and Edit a DMX light (the same four questions on one sheet).
//   1. What kind of light it is (the types the room knows, the plain generic ones first).
//   2. Where it's plugged in: the Art-Net box's address and the universe (1 unless the box says otherwise).
//   3. Its first channel, with the channels it uses and any light it would share them with.
//   4. Its name and where it is in the room (the placements the other lights have, or anything typed).
// Save sends POST /show/api/v1/rig/fixtures (Edit: PUT with only what changed). The server's problems come back in
// its words, inline. After saving a new light the sheet offers Identify, so it can be found at once.

import { useMemo, useState, type ReactNode } from "react";
import { Check, ChevronLeft } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { cn } from "@/lib/utils";
import { Notice, Spinner, Unroll } from "@/pages/Room/bits";
import { ActionSheet } from "@/pages/Room/overlays";
import { TAP_BUTTON_CLASS } from "@/pages/Room/theme";
import { explainLightsError, LightsApiError, type LightsApi } from "./api";
import { IdentifyButton, useIdentify } from "./Identify";
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
  rowsFromRig,
  wholeNumber,
  type Draft,
  type DraftField,
  type LightRow,
  type LightType,
} from "./lib";
import type { TypesState } from "./useRig";

const STEPS = 4;
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
  onSaved,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  editing: LightRow | null;
  api: LightsApi;
  rows: LightRow[];
  types: TypesState;
  /** Read the rig again. */
  onSaved: () => void;
}) {
  return (
    <ActionSheet
      open={open}
      onOpenChange={onOpenChange}
      title={editing ? `Edit ${editing.label}` : "Add a light"}
      testId="light-sheet"
    >
      <LightForm
        key={editing?.id ?? "_add"}
        editing={editing}
        api={api}
        rows={rows}
        types={types}
        onSaved={onSaved}
        onClose={() => onOpenChange(false)}
      />
    </ActionSheet>
  );
}

function FieldError({ text }: { text?: string }) {
  return text ? <p className="text-sm text-rose-700">{text}</p> : null;
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
  onSaved,
  onClose,
}: {
  editing: LightRow | null;
  api: LightsApi;
  rows: LightRow[];
  types: TypesState;
  onSaved: () => void;
  onClose: () => void;
}) {
  const adding = editing === null;
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

  const others = useMemo(() => rows.filter((r) => r.id !== editing?.id), [rows, editing?.id]);
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

  const save = async () => {
    setTried(new Set([0, 1, 2, 3, 4]));
    if (Object.keys(problems).length) {
      if (adding) setStep(Math.min(...(Object.keys(problems) as DraftField[]).map((f) => FIELD_STEP[f])));
      return;
    }
    let body = draftBody(draft);
    if (editing) {
      body = changedBody(draft, editing);
      if (Object.keys(body).length === 0) {
        onClose();
        return;
      }
    }
    setSaving(true);
    setFailure(null);
    try {
      const res = editing ? await api.update(editing.id, body) : await api.add(body);
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
      setSaved({ id: row?.id ?? editing?.id ?? null, label: row?.label ?? draft.label.trim(), words, problems: notes });
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
        api={api}
        adding={adding}
        onAnother={() => {
          setSaved(null);
          setDraft((d) => ({ ...emptyDraft(), type: d.type, node_ip: d.node_ip, universe: d.universe }));
          setTried(new Set());
          setFreeText(false);
          setStep(3);
        }}
        onClose={onClose}
      />
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
      <StepHead n={4} title="Its name and where it is" adding={adding} />
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
    setTried((t) => new Set(t).add(step));
    if (stepOk(step)) setStep((s) => Math.min(STEPS, s + 1));
  };
  return (
    <div className="space-y-4 pb-1">
      {step === 1 && q1}
      {step === 2 && q2}
      {step === 3 && q3}
      {step === 4 && q4}
      {step === STEPS && failureNote}
      {step > 1 && (
        <p className="text-sm text-muted-foreground" data-testid="add-so-far">
          {[type?.label, draft.node_ip.trim() && `box ${draft.node_ip.trim()}`, universe !== null && `universe ${universe}`, step > 3 && fpWords]
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
        <span className="flex-1" />
        {step < STEPS ? (
          (step > 1 || !!draft.type) && (
            <Button onClick={next} className={TAP_BUTTON_CLASS} data-testid="light-next">
              Next
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
  api,
  adding,
  onAnother,
  onClose,
}: {
  saved: Saved;
  api: LightsApi;
  adding: boolean;
  onAnother: () => void;
  onClose: () => void;
}) {
  const ident = useIdentify(api, saved.id);
  return (
    <div className="space-y-4 pb-1" data-testid="light-saved">
      <p className="flex items-center gap-2 text-[17px] font-semibold">
        <Check className="h-5 w-5 text-teal-700" aria-hidden /> {saved.label} is saved
      </p>
      {saved.words && <p className="text-[15px] text-muted-foreground">{saved.words}.</p>}
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
          <p className="text-[15px]">Find it: it shows full white for a moment, whatever it was doing.</p>
          <IdentifyButton state={ident.state} onClick={() => void ident.run()} label={saved.label} wide className="h-12 px-4 text-base" />
          {ident.why && <p className="text-sm text-rose-700">{ident.why}</p>}
        </div>
      )}
      <div className="flex flex-wrap justify-end gap-2 pt-1">
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
