// One light's row, the same on the Lights setup page and the Room page's Lights fold (LightShell): its colour, its
// name, a status pill, one line (its kind · where it is), with Identify always at the right, beside the roll-up
// trigger, not in it, so it never unrolls the row. ColourBlock is the brightness and colour presets both pages use for
// a light at a steady colour.
//
// LightItem is the setup page's row. Unrolled: what it is, where it is, what it does now (changed on the Room page),
// which modes set it, and for a DMX light Edit and Remove (with a confirm). Box, universe and channel words stay in Add
// and Edit.

import { useEffect, useRef, useState, type ReactNode } from "react";
import { ChevronDown, Pencil, Sparkles, Trash2 } from "lucide-react";
import { AppLink } from "@/components/nav/AppLink";
import { Button } from "@/components/ui/button";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import { Slider } from "@/components/ui/slider";
import { cn } from "@/lib/utils";
import { Notice, Pill, Spinner } from "@/pages/Room/bits";
import { Modal } from "@/pages/Room/overlays";
import { explainLightsError, LightsApiError, type LightsApi } from "./api";
import { IdentifyButton, useIdentify, type Identify } from "./Identify";
import { COLOUR_PRESETS, typeWords, type LightRow, type LightType, type StatusWords } from "./lib";

const LINK = "inline-flex min-h-11 items-center text-[15px] font-medium text-teal-800 underline-offset-4 hover:underline";

export function Swatch({ hex, dim = 1, className }: { hex: string | null; dim?: number; className?: string }) {
  return (
    <span
      aria-hidden
      className={cn("relative inline-block h-11 w-11 shrink-0 rounded-full border border-stone-300/80 bg-stone-200", className)}
      style={
        hex
          ? { background: hex, opacity: 0.35 + 0.65 * Math.min(1, Math.max(0, dim)), boxShadow: `0 0 14px -2px ${hex}` }
          : undefined
      }
    />
  );
}

/** The row frame every light uses. `focus` (a deep link to this light) unrolls it and scrolls it into view once. */
export function LightShell({
  testId,
  swatch,
  name,
  status,
  summary,
  identify,
  focus = false,
  children,
}: {
  testId?: string;
  swatch: { hex: string | null; dim?: number };
  name: string;
  status?: StatusWords | null;
  /** One line under the name: its kind and where it is. */
  summary?: ReactNode;
  identify: Identify | null;
  focus?: boolean;
  children: ReactNode;
}) {
  const [open, setOpen] = useState(focus);
  const root = useRef<HTMLLIElement>(null);
  useEffect(() => {
    if (!focus) return;
    setOpen(true);
    const reduce = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
    const t = window.setTimeout(() => root.current?.scrollIntoView({ block: "center", behavior: reduce ? "auto" : "smooth" }), 80);
    return () => window.clearTimeout(t);
  }, [focus]);
  return (
    <li ref={root} className={cn("py-1", focus && "rounded-xl ring-2 ring-teal-300/70 ring-offset-2 ring-offset-card")} data-testid={testId}>
      <Collapsible open={open} onOpenChange={setOpen}>
        <div className="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-x-2">
          <CollapsibleTrigger className="flex min-h-11 min-w-0 items-center gap-3 rounded-xl px-1 py-1 text-left hover:bg-stone-100/70 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
            <Swatch hex={swatch.hex} dim={swatch.dim} />
            <span className="min-w-0 flex-1">
              <span className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-0.5">
                <span className="min-w-0 break-words text-[17px] font-semibold leading-snug">{name}</span>
                {status?.text && (
                  <Pill tone={status.tone} pulse={status.pulse}>
                    {status.text}
                  </Pill>
                )}
              </span>
              {summary && <span className="block text-sm font-normal leading-snug text-muted-foreground">{summary}</span>}
            </span>
            <ChevronDown
              aria-hidden
              className={cn("h-4 w-4 shrink-0 text-muted-foreground transition-transform motion-reduce:transition-none", open && "rotate-180")}
            />
          </CollapsibleTrigger>
          {identify && <IdentifyButton state={identify.state} onClick={() => void identify.run()} label={name} />}
        </div>
        {identify?.why && <p className="pb-1 pl-[60px] text-sm text-rose-700">{identify.why}</p>}
        <CollapsibleContent className="pt-2">
          <div className="space-y-4 px-1 pb-3">{children}</div>
        </CollapsibleContent>
      </Collapsible>
    </li>
  );
}

/** "Hue lamp · Above the north screen". */
export function summaryLine(kind: string | null, where: string | null): string | null {
  return [kind, where].filter(Boolean).join(" · ") || null;
}

/** Brightness and colour for a light at a steady colour: a slider (sent on release) and the quick colours. */
export function ColourBlock({
  label,
  hex,
  bri,
  disabled,
  onBrightness,
  onColour,
}: {
  /** The light's name, for screen readers. */
  label: string;
  hex: string | null;
  bri: number;
  disabled: boolean;
  onBrightness: (bri: number) => void;
  onColour: (hex: string, bri: number) => void;
}) {
  const [b, setB] = useState(bri);
  const dragging = useRef(false);
  useEffect(() => {
    if (!dragging.current) setB(bri);
  }, [bri]);
  const pickTimer = useRef<number>();
  useEffect(() => () => window.clearTimeout(pickTimer.current), []);
  const level = Math.max(b, 1);
  return (
    <div className="space-y-3">
      <div className="flex min-h-11 items-center gap-3">
        <span className="shrink-0 text-base font-medium">Brightness</span>
        <Slider
          aria-label={`${label} brightness`}
          className="flex-1 py-3 [&_[role=slider]]:h-6 [&_[role=slider]]:w-6 [&_[role=slider]]:shadow-sm"
          min={1}
          max={100}
          step={1}
          value={[b]}
          disabled={disabled}
          onValueChange={([v]) => {
            dragging.current = true;
            setB(v);
          }}
          onValueCommit={([v]) => {
            dragging.current = false;
            onBrightness(v);
          }}
        />
        <span className="w-11 shrink-0 text-right font-mono text-[15px] tabular-nums text-muted-foreground">{b}%</span>
      </div>
      <div>
        <span className="text-base font-medium">Colour</span>
        <div className="mt-2 flex flex-wrap items-center gap-2">
          {COLOUR_PRESETS.map((p) => (
            <button
              key={p.name}
              type="button"
              title={p.name}
              aria-label={p.name}
              aria-pressed={!!hex && hex.toLowerCase() === p.hex}
              disabled={disabled}
              onClick={() => onColour(p.hex, level)}
              className={cn(
                "h-10 w-10 rounded-full border border-stone-300 shadow-sm transition-transform hover:scale-105 disabled:opacity-50",
                !!hex && hex.toLowerCase() === p.hex && "ring-2 ring-teal-600 ring-offset-2",
              )}
              style={{ background: p.hex }}
            />
          ))}
          <label className="relative h-10 w-10 cursor-pointer overflow-hidden rounded-full border border-dashed border-stone-400" title="Any colour">
            <span className="sr-only">Any colour</span>
            <input
              type="color"
              disabled={disabled}
              defaultValue={hex ?? COLOUR_PRESETS[0].hex}
              onChange={(e) => {
                const v = e.currentTarget.value;
                window.clearTimeout(pickTimer.current);
                pickTimer.current = window.setTimeout(() => onColour(v, level), 350);
              }}
              className="absolute inset-0 h-full w-full cursor-pointer opacity-0"
            />
            <Sparkles className="absolute inset-0 m-auto h-4 w-4 text-stone-500" aria-hidden />
          </label>
        </div>
      </div>
    </div>
  );
}

/** What the setup page knows about a light beyond the rig's row: from the Room map, rooms.json, room_service. */
export interface LightView {
  name: string;
  kind: string | null;
  where: string | null;
  status: StatusWords | null;
  swatch: { hex: string | null; dim?: number };
  /** What it does now, in words. */
  doing: string | null;
  /** The modes that set it, grouped by what they do; null while the modes aren't known. */
  modes: [string, string[]][] | null;
}

function connectionWords(r: LightRow): string {
  if (r.online === true) return "Answering";
  if (r.online === false) return "Not answering: check it's plugged in and on the house network";
  return "Not checked yet";
}

export function LightItem({
  row,
  view,
  api,
  types,
  focus = false,
  onEdit,
  onChanged,
}: {
  row: LightRow;
  view: LightView;
  api: LightsApi;
  types: LightType[];
  /** Opened from a link to this light (/lights?light=<id>). */
  focus?: boolean;
  onEdit: (row: LightRow) => void;
  /** Read the rig again (after a remove). */
  onChanged: () => void;
}) {
  const ident = useIdentify(api, row.id, row.hue);
  const what = typeWords(row, types);

  const [confirm, setConfirm] = useState(false);
  const [removing, setRemoving] = useState(false);
  const [removeErr, setRemoveErr] = useState<{ text: string; problems: string[] } | null>(null);
  const remove = async () => {
    setRemoving(true);
    setRemoveErr(null);
    try {
      await api.remove(row.id);
      setConfirm(false);
      onChanged();
    } catch (e) {
      setRemoveErr({ text: explainLightsError(e), problems: e instanceof LightsApiError ? e.problems : [] });
    } finally {
      setRemoving(false);
    }
  };

  return (
    <>
      <LightShell
        testId={`light-${row.id}`}
        swatch={view.swatch}
        name={view.name}
        status={view.status}
        summary={summaryLine(view.kind, view.where)}
        identify={ident}
        focus={focus}
      >
        <dl className="grid grid-cols-[auto,1fr] gap-x-4 gap-y-1.5 text-[15px]">
          <dt className="text-muted-foreground">What it is</dt>
          <dd className="min-w-0 break-words">{what ?? view.kind ?? "Not known"}</dd>
          <dt className="text-muted-foreground">Where it is</dt>
          <dd className="min-w-0 break-words">{view.where ?? "Not said yet"}</dd>
          {row.dmx && (
            <>
              <dt className="text-muted-foreground">Connection</dt>
              <dd className="min-w-0 break-words">{connectionWords(row)}</dd>
            </>
          )}
          <dt className="text-muted-foreground">What it does</dt>
          <dd className="min-w-0 break-words">
            <span className="block">{view.doing ?? "Not known yet"}</span>
            <AppLink href="/room#lights" className={cn(LINK, "min-h-0")} data-testid="change-on-room">
              Change on the Room page ›
            </AppLink>
          </dd>
          <dt className="text-muted-foreground">In modes</dt>
          <dd className="min-w-0 break-words" data-testid="light-modes">
            {view.modes === null ? (
              "Not known yet"
            ) : view.modes.length === 0 ? (
              "No mode changes it"
            ) : (
              <ul className="space-y-0.5">
                {view.modes.map(([words, modes]) => (
                  <li key={words}>
                    <span className="font-medium">{words}:</span> {modes.join(", ")}
                  </li>
                ))}
              </ul>
            )}
            <AppLink href="/room#modes" className={cn(LINK, "min-h-0")}>
              Change a mode ›
            </AppLink>
          </dd>
        </dl>
        {row.dmx && (
          <div className="flex flex-wrap gap-2">
            <Button variant="outline" onClick={() => onEdit(row)} className="h-11 rounded-xl bg-white px-4 text-[15px]" data-testid="edit-light">
              <Pencil aria-hidden /> Edit
            </Button>
            <Button
              variant="ghost"
              onClick={() => {
                setRemoveErr(null);
                setConfirm(true);
              }}
              className="h-11 rounded-xl px-4 text-[15px] text-rose-700 hover:bg-rose-50 hover:text-rose-800"
            >
              <Trash2 aria-hidden /> Remove…
            </Button>
          </div>
        )}
      </LightShell>

      <Modal
        open={confirm}
        onOpenChange={(o) => !removing && setConfirm(o)}
        title={`Remove ${view.name}?`}
        description="The room stops sending to it. Nothing on the light itself changes, and you can add it again any time."
      >
        {removeErr && (
          <Notice tone="bad" className="mb-3">
            <p>{removeErr.text}</p>
            {removeErr.problems.length > 0 && (
              <ul className="mt-1 list-disc space-y-0.5 pl-5">
                {removeErr.problems.map((p) => (
                  <li key={p}>{p}</li>
                ))}
              </ul>
            )}
          </Notice>
        )}
        <div className="flex flex-wrap justify-end gap-2">
          <Button variant="outline" onClick={() => setConfirm(false)} disabled={removing} className="h-11 rounded-xl bg-white px-5 text-base">
            Keep it
          </Button>
          <Button
            variant="destructive"
            onClick={() => void remove()}
            disabled={removing}
            className="h-11 rounded-xl px-5 text-base"
            data-testid="confirm-remove"
          >
            {removing && <Spinner />} Remove
          </Button>
        </div>
      </Modal>
    </>
  );
}
