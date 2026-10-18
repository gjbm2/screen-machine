// One light, rolled up like the Room page's DeviceRow: its colour, its name, a status pill and one line (where it is ·
// what it is), with Identify always at hand. Unrolled: the details in words and, for a DMX light, Edit and Remove
// (with a confirm). Identify sits beside the roll-up trigger, not in it, so it never unrolls the row.

import { useState } from "react";
import { ChevronDown, Pencil, Trash2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import { cn } from "@/lib/utils";
import { Notice, Pill, Spinner } from "@/pages/Room/bits";
import { Modal } from "@/pages/Room/overlays";
import { explainLightsError, LightsApiError, type LightsApi } from "./api";
import { IdentifyButton, useIdentify } from "./Identify";
import {
  doingWords,
  footprintWords,
  placementWords,
  statusOf,
  swatchOf,
  typeWords,
  type LightRow,
  type LightType,
} from "./lib";

export function Swatch({ hex, className }: { hex: string | null; className?: string }) {
  return (
    <span
      aria-hidden
      className={cn("relative inline-block h-11 w-11 shrink-0 rounded-full border border-stone-300/80 bg-stone-200", className)}
      style={hex ? { background: hex, boxShadow: `0 0 14px -2px ${hex}` } : undefined}
    />
  );
}

function boxWords(r: LightRow): string {
  if (r.online === true) return "Answering";
  if (r.online === false) return "Not answering: check it's plugged in and on the house network";
  return "Not checked yet";
}

export function LightItem({
  row,
  api,
  types,
  onEdit,
  onChanged,
}: {
  row: LightRow;
  api: LightsApi;
  types: LightType[];
  onEdit: (row: LightRow) => void;
  /** Read the rig again (after a remove). */
  onChanged: () => void;
}) {
  const [open, setOpen] = useState(false);
  const ident = useIdentify(api, row.id);
  const status = statusOf(row);
  const where = placementWords(row.placement, row.zone);
  const what = typeWords(row, types);
  const summary = [where, what].filter(Boolean).join(" · ");
  const channels = types.find((t) => t.id === row.type)?.channels ?? [];
  const doing = doingWords(row);

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
    <li className="py-1" data-testid={`light-${row.id}`}>
      <Collapsible open={open} onOpenChange={setOpen}>
        {/* A phone has no room for Identify beside the name and its pill: it goes under the words, lined up with
            them; from sm up it sits at the right. */}
        <div className="grid grid-cols-1 items-center gap-x-2 sm:grid-cols-[minmax(0,1fr)_auto]">
          <CollapsibleTrigger className="flex min-h-11 min-w-0 items-center gap-3 rounded-xl px-1 py-1 text-left hover:bg-stone-100/70 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
            <Swatch hex={swatchOf(row)} />
            <span className="min-w-0 flex-1">
              <span className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-0.5">
                <span className="min-w-0 break-words text-[17px] font-semibold leading-snug">{row.label}</span>
                {status && (
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
          <div className="pb-1 pl-[60px] sm:pb-0 sm:pl-0">
            <IdentifyButton state={ident.state} onClick={() => void ident.run()} label={row.label} />
          </div>
        </div>
        {ident.why && <p className="pb-1 pl-[60px] text-sm text-rose-700">{ident.why}</p>}
        <CollapsibleContent className="pt-2">
          <div className="space-y-3 px-1 pb-3">
            <dl className="grid grid-cols-[auto,1fr] gap-x-4 gap-y-1.5 text-[15px]">
              <dt className="text-muted-foreground">What it is</dt>
              <dd className="min-w-0 break-words">
                {what ?? "Not known"}
                {channels.length > 0 && <span className="block text-sm text-muted-foreground">{channels.join(" · ")}</span>}
              </dd>
              <dt className="text-muted-foreground">Where it is</dt>
              <dd className="min-w-0 break-words">{where ?? "Not said yet"}</dd>
              {row.dmx ? (
                <>
                  <dt className="text-muted-foreground">Plugged into</dt>
                  <dd className="min-w-0 break-words">
                    {row.node_ip ? `The box at ${row.node_ip}` : "A box not known yet"}
                    {row.universe !== null && `, universe ${row.universe}`}
                  </dd>
                  <dt className="text-muted-foreground">Channels</dt>
                  <dd className="min-w-0 break-words">
                    {footprintWords(row.address, row.footprint) ?? (row.address !== null ? `From channel ${row.address}` : "Not set")}
                  </dd>
                  <dt className="text-muted-foreground">The box</dt>
                  <dd className="min-w-0 break-words">{boxWords(row)}</dd>
                </>
              ) : row.hue ? (
                <>
                  <dt className="text-muted-foreground">On</dt>
                  <dd className="min-w-0 break-words">The Hue bridge</dd>
                </>
              ) : null}
              <dt className="text-muted-foreground">What it does</dt>
              <dd className="min-w-0 break-words">
                {doing ?? "Set on the Room page"}
              </dd>
            </dl>
            {row.dmx ? (
              <div className="flex flex-wrap gap-2">
                <Button variant="outline" onClick={() => onEdit(row)} className="h-11 rounded-xl bg-white px-4 text-[15px]">
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
            ) : row.hue ? (
              <p className="text-sm text-muted-foreground">Hue lamps come from the Hue bridge, so there's nothing to set up here.</p>
            ) : null}
          </div>
        </CollapsibleContent>
      </Collapsible>

      <Modal
        open={confirm}
        onOpenChange={(o) => !removing && setConfirm(o)}
        title={`Remove ${row.label}?`}
        description="The room stops sending to it. Nothing on the box changes, and you can add the light again any time."
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
    </li>
  );
}
