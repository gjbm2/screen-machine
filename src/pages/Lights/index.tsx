// /lights: setting up the room's lights (contract D), linked from the Room page's menu ("Lights setup"). Every light
// show_service's rig knows (GET /show/api/v1/rig, polled every 2 s while the page is visible): the DMX lights on
// Art-Net boxes, which can be added, edited and removed here, and the Hue lamps, which come from the Hue bridge. Each
// row has Identify (full white for a moment). Add a light is the page's main action (LightForm.tsx). What a light does
// with the music is chosen on the Room page, not here. Same light, warm skin as /room (its theme and pieces).

import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { ArrowLeft, Menu, Plus } from "lucide-react";
import { AppMenu } from "@/components/nav/AppMenu";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { Dot, Notice, Spinner, Unroll } from "@/pages/Room/bits";
import { DeviceList, DeviceSection } from "@/pages/Room/Devices";
import { RoomThemeStyle, TAP_BUTTON_CLASS, THEME_CLASS } from "@/pages/Room/theme";
import type { Tone } from "@/pages/Room/tones";
import { LightsApi } from "./api";
import { LightSheet } from "./LightForm";
import { LightItem } from "./LightItem";
import type { LightRow } from "./lib";
import { useLightTypes, useRig } from "./useRig";

/** The tab title while on this page, put back on the way out. */
function usePageTitle(title: string) {
  useEffect(() => {
    const prev = document.title;
    document.title = title;
    return () => {
      document.title = prev;
    };
  }, [title]);
}

const PILL =
  "inline-flex h-9 items-center gap-2 rounded-full border border-stone-200/80 bg-card px-3 text-[15px] font-medium text-stone-700 shadow-sm";

const byPlace = (a: LightRow, b: LightRow) =>
  (a.zone ?? "~").localeCompare(b.zone ?? "~") || a.label.localeCompare(b.label, undefined, { numeric: true });

export default function LightsPage() {
  usePageTitle("Lights · screen/machine");
  const api = useMemo(() => new LightsApi(), []);
  const rig = useRig(api);
  const types = useLightTypes(api);
  const [sheetOpen, setSheetOpen] = useState(false);
  const [editing, setEditing] = useState<LightRow | null>(null);
  const [menuOpen, setMenuOpen] = useState(false);

  const rows = useMemo(() => rig.rows ?? [], [rig.rows]);
  const dmx = useMemo(() => rows.filter((r) => r.dmx).sort(byPlace), [rows]);
  const hue = useMemo(() => rows.filter((r) => r.hue).sort(byPlace), [rows]);
  const other = useMemo(() => rows.filter((r) => !r.dmx && !r.hue).sort(byPlace), [rows]);

  const link: { tone: Tone; word: string } = rig.error
    ? rig.rows
      ? { tone: "warn", word: "Reconnecting" }
      : { tone: "bad", word: "Offline" }
    : rig.at
      ? { tone: "ok", word: "Connected" }
      : { tone: "muted", word: "Connecting" };

  const openAdd = () => {
    setEditing(null);
    if (!types.types) types.reload();
    setSheetOpen(true);
  };
  const openEdit = (r: LightRow) => {
    setEditing(r);
    if (!types.types) types.reload();
    setSheetOpen(true);
  };

  const list = (items: LightRow[]) => (
    <DeviceList>
      {items.map((r) => (
        <LightItem key={r.id} row={r} api={api} types={types.types ?? []} onEdit={openEdit} onChanged={rig.refresh} />
      ))}
    </DeviceList>
  );

  return (
    <div className={cn(THEME_CLASS, "min-h-screen bg-background text-base text-foreground antialiased")}>
      <RoomThemeStyle />
      <div className="mx-auto w-full max-w-3xl px-4 pb-16 sm:px-6">
        <header className="pb-1 pt-3">
          <div className="flex items-center gap-1">
            <h1 className="mr-auto text-[28px] font-semibold leading-tight tracking-tight">Lights</h1>
            <span className={PILL} data-testid="lights-link" title={rig.error ?? undefined}>
              <Dot tone={link.tone} pulse={link.tone === "warn"} className="h-2.5 w-2.5" />
              {link.word}
            </span>
            <Button
              variant="ghost"
              size="icon"
              onClick={() => setMenuOpen(true)}
              aria-label="Menu"
              data-testid="menu-button"
              className="-mr-2 h-11 w-11 rounded-full hover:bg-stone-200/60 [&_svg]:size-6"
            >
              <Menu />
            </Button>
          </div>
          <nav aria-label="Back" className="flex flex-wrap items-center gap-x-1 text-[15px]">
            <Link to="/room" className="group grid h-11 place-items-center rounded-full px-0.5 focus-visible:outline-none">
              <span className={cn(PILL, "group-hover:bg-stone-50 group-focus-visible:ring-2 group-focus-visible:ring-ring")}>
                <ArrowLeft className="h-4 w-4 text-teal-700" aria-hidden /> Room
              </span>
            </Link>
          </nav>
        </header>

        <main className="space-y-5 pt-2">
          <div className="space-y-2">
            <Button onClick={openAdd} className={cn(TAP_BUTTON_CLASS, "w-full sm:w-auto")} data-testid="add-light">
              <Plus aria-hidden /> Add a light
            </Button>
            <p className="text-[15px] text-muted-foreground">
              Plug it into an Art-Net box on the house network, then tell the room what it is and where.
            </p>
          </div>

          {rig.error && (
            <Notice tone={rig.rows ? "warn" : "bad"} className="text-base">
              {rig.error}
            </Notice>
          )}

          {rig.off !== null && (
            <Notice tone="warn" className="text-base">
              <p>The lights side isn't driving any lights right now, so they won't follow the music or identify.</p>
              {rig.off && (
                <Unroll title={<span className="text-sm">Details</span>} className="mt-1">
                  <p className="pb-1 text-sm">{rig.off}</p>
                </Unroll>
              )}
            </Notice>
          )}

          {rig.rows === null ? (
            !rig.error && (
              <p className="flex items-center gap-2 py-3 text-base text-muted-foreground">
                <Spinner /> Finding the lights…
              </p>
            )
          ) : (
            <>
              <DeviceSection title="DMX lights">
                {dmx.length > 0 ? (
                  list(dmx)
                ) : (
                  <p className="py-3 text-base text-muted-foreground">No DMX lights yet. Add one with the button above.</p>
                )}
              </DeviceSection>
              {hue.length > 0 && <DeviceSection title="Hue lamps">{list(hue)}</DeviceSection>}
              {other.length > 0 && <DeviceSection title="Other lights">{list(other)}</DeviceSection>}
            </>
          )}

          {rig.problems.length > 0 && (
            <Unroll title="Things to check" hint={`${rig.problems.length}`} testId="lights-problems">
              <ul className="list-disc space-y-1 pb-2 pl-6 text-[15px]">
                {rig.problems.map((p) => (
                  <li key={p}>{p}</li>
                ))}
              </ul>
            </Unroll>
          )}

          <p className="text-sm text-muted-foreground">
            Changes here work at once. Whether each light follows the music is chosen on the{" "}
            <Link to="/room" className="font-medium text-teal-800 underline underline-offset-2">
              Room page
            </Link>
            .
          </p>
        </main>
      </div>

      <LightSheet
        open={sheetOpen}
        onOpenChange={setSheetOpen}
        editing={editing}
        api={api}
        rows={rows}
        types={types}
        onSaved={rig.refresh}
      />

      <AppMenu open={menuOpen} onOpenChange={setMenuOpen} current="other" />
    </div>
  );
}
