// /lights, Lights setup: add, place and identify the room's lights (contract D). A sub-page of the Room page: the
// app's one header (PageHeader) with "‹ Room" back to the Room page's Lights fold, and a gear to the light settings.
// Every light show_service's rig knows (GET /show/api/v1/rig, polled every 2 s while the page is visible): the DMX
// lights on Art-Net boxes, which are added, edited and removed here, and the Hue lamps from the Hue bridge, whose
// status and colour come from room_service (it drives them). Lights are grouped by room (rooms.json), each row with its
// kind; names drop their room's name; where a light is comes from the Room map once it's placed there. Each row has
// Identify. Add a light is the page's main action (LightForm.tsx; Move it inside it).
// "Lights | Room map" at the top (?tab=map, else remembered on this device) switches to the Room map (RoomMap.tsx).
// ?light=<id> opens that light's row (from the Room page's "Set up this light ›"). What a light does is chosen on the
// Room page, not here. Same light, warm skin as /room (its theme and pieces).
// Under the lights, Remote controls (RemoteControls.tsx): the devices worked by their own remotes through the RM4 Pro
// (the pinspots, the glitterball), each button learned and tested here.

import { useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { Plus, Settings } from "lucide-react";
import { AppLink } from "@/components/nav/AppLink";
import { AppMenu } from "@/components/nav/AppMenu";
import { settingsUrl } from "@/components/nav/appNav";
import { PageHeader, type HeaderStatus } from "@/components/nav/PageHeader";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { RoomApi, storageGet, storageSet } from "@/pages/Room/api";
import { Notice, Spinner, Unroll } from "@/pages/Room/bits";
import { DeviceList } from "@/pages/Room/Devices";
import { CARD_CLASS, RoomThemeStyle, TAP_BUTTON_CLASS, THEME_CLASS } from "@/pages/Room/theme";
import type { Fixture } from "@/pages/Room/types";
import { useFixtures } from "@/pages/Room/useFixtures";
import { LightsApi } from "./api";
import { LightSheet } from "./LightForm";
import { LightItem, type LightView } from "./LightItem";
import {
  doingWords,
  fixtureStatus,
  fixtureSwatch,
  kindWords,
  lightInModes,
  modeUseLines,
  roomOfLight,
  screenSpots,
  shortName,
  statusOf,
  swatchOf,
  whereWords,
  type LightRow,
  type ModeUse,
  type Placeable,
} from "./lib";
import { RemoteControls } from "./RemoteControls";
import { RoomMap } from "./RoomMap";
import { useLightTypes, useRig, useRoomFacts, useSpace } from "./useRig";

type Tab = "lights" | "map";
const TAB_KEY = "lights.tab";

/** The tab last shown on this device (Room's storage helpers: a blocked store reads as none). */
function storedTab(): Tab {
  return storageGet(TAB_KEY) === "map" ? "map" : "lights";
}

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

const byPlace = (a: LightRow, b: LightRow) =>
  Number(b.hue) - Number(a.hue) ||
  (a.zone ?? "~").localeCompare(b.zone ?? "~") ||
  a.label.localeCompare(b.label, undefined, { numeric: true });

interface Group {
  key: string;
  title: string;
  roomId: string | null;
  rows: LightRow[];
}

export default function LightsPage() {
  usePageTitle("Lights setup · screen/machine");
  const [params, setParams] = useSearchParams();
  const api = useMemo(() => new LightsApi(), []);
  const roomApi = useMemo(() => new RoomApi(api.roomUrl, ""), [api]);
  const rig = useRig(api);
  const types = useLightTypes(api);
  const facts = useRoomFacts(api.roomUrl, true);
  const fixtures = useFixtures(roomApi, true);
  const space = useSpace(api);
  const [sheetOpen, setSheetOpen] = useState(false);
  const [editing, setEditing] = useState<LightRow | null>(null);
  const [menuOpen, setMenuOpen] = useState(false);
  const focusId = params.get("light");
  const [tab, setTabState] = useState<Tab>(() => (focusId ? "lights" : params.get("tab") === "map" ? "map" : storedTab()));
  const [mapSelect, setMapSelect] = useState<string | null>(null);
  const setTab = (t: Tab) => {
    setTabState(t);
    storageSet(TAB_KEY, t);
    const next = new URLSearchParams(params);
    next.delete("light");
    if (t === "map") next.set("tab", "map");
    else next.delete("tab");
    setParams(next, { replace: true });
  };
  // The map's words change when something is moved there: read the room again when coming back to the list.
  const reloadSpace = space.reload;
  useEffect(() => {
    if (tab === "lights") reloadSpace();
  }, [tab, reloadSpace]);

  const rows = useMemo(() => rig.rows ?? [], [rig.rows]);
  const dmxCount = rows.filter((r) => r.dmx).length;
  const hueById = useMemo(() => {
    const m = new Map<string, Fixture>();
    for (const f of fixtures.view?.fixtures ?? []) if (f.kind === "hue") m.set(f.id, f);
    return m;
  }, [fixtures.view]);
  const bridge = fixtures.view?.hue?.bridge ?? null;

  const spots = useMemo(() => screenSpots(space.space, rig.all, facts.screenNames), [space.space, rig.all, facts.screenNames]);
  const describe = facts.describe;

  /** The modes that set a light: its own entry, else (a light the room hasn't picked up yet) its kind's. */
  const inModes = useMemo(
    () =>
      (id: string, dmx: boolean): ModeUse[] => {
        const target = dmx ? `light.${id}` : id;
        const room = roomOfLight(facts.rooms, id, dmx);
        const known = !!describe?.targets?.[target];
        const kinds = known || !dmx ? [] : ["light", ...(room ? [`${room.id}:lights`, `${room.id}:light`] : [])];
        return lightInModes(describe?.modes, target, kinds);
      },
    [describe, facts.rooms],
  );

  const viewOf = (r: LightRow): LightView => {
    const room = roomOfLight(facts.rooms, r.id, r.dmx);
    const f = r.hue ? hueById.get(r.id) : undefined;
    return {
      name: shortName(r.label, room?.name),
      kind: kindWords(r),
      where: whereWords(r.id, space.space, spots, r.placement, r.zone),
      status: f ? fixtureStatus(f) : statusOf(r),
      swatch: f ? fixtureSwatch(f) : { hex: swatchOf(r) },
      doing: f?.status_text || doingWords(r),
      modes: describe ? modeUseLines(inModes(r.id, r.dmx)) : null,
    };
  };

  // By room (rooms.json), the rooms with lights only; lights no room lists last.
  const groups = useMemo((): Group[] => {
    const out: Group[] = [];
    const taken = new Set<string>();
    for (const room of facts.rooms) {
      const mine = rows.filter((r) => !taken.has(r.id) && roomOfLight([room], r.id, r.dmx));
      if (!mine.length) continue;
      mine.forEach((r) => taken.add(r.id));
      out.push({ key: room.id, title: room.name, roomId: room.id, rows: [...mine].sort(byPlace) });
    }
    const rest = rows.filter((r) => !taken.has(r.id)).sort(byPlace);
    if (rest.length) out.push({ key: "_rest", title: out.length ? "Not in a room" : "Lights", roomId: null, rows: rest });
    return out;
  }, [rows, facts.rooms]);

  // The map: lights by their short names, screens by the screen's own name.
  const mapThings = useMemo(
    (): Placeable[] =>
      rig.all.map((t) => {
        if (t.kind === "screen") return { ...t, label: (t.screen && facts.screenNames[t.screen]) || t.label };
        const room = roomOfLight(facts.rooms, t.id, rows.find((r) => r.id === t.id)?.dmx ?? false);
        return { ...t, label: shortName(t.label, room?.name) };
      }),
    [rig.all, rows, facts.rooms, facts.screenNames],
  );

  const status: HeaderStatus | null = rig.error
    ? rig.rows
      ? { tone: "warn", word: "Reconnecting", sentence: rig.error, pulse: true }
      : { tone: "bad", word: "Offline", sentence: rig.error }
    : rig.at
      ? null
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

  return (
    <div className={cn(THEME_CLASS, "min-h-screen bg-background text-base text-foreground antialiased")}>
      <RoomThemeStyle />
      <div className="mx-auto w-full max-w-3xl px-4 pb-16 sm:px-6">
        <PageHeader
          title="Lights"
          current="lights"
          onMenu={() => setMenuOpen(true)}
          back={{ to: "/room#lights", label: "Room" }}
          status={status}
          actions={
            <AppLink
              href={settingsUrl("lights")}
              aria-label="Light settings"
              title="Light settings"
              className="grid h-11 w-11 place-items-center rounded-full text-stone-700 hover:bg-stone-200/60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            >
              <Settings className="h-5 w-5" aria-hidden />
            </AppLink>
          }
        />

        <main className="space-y-5 pt-2">
          <div role="tablist" aria-label="Show" className="grid grid-cols-2 rounded-full border border-stone-200 bg-card p-1 shadow-sm" data-testid="lights-tabs">
            {(
              [
                ["lights", "Lights"],
                ["map", "Room map"],
              ] as [Tab, string][]
            ).map(([t, words]) => (
              <button
                key={t}
                type="button"
                role="tab"
                aria-selected={tab === t}
                onClick={() => setTab(t)}
                className={cn(
                  "min-h-10 rounded-full text-[15px] font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                  tab === t ? "bg-teal-600 text-white" : "text-stone-700 hover:bg-stone-100",
                )}
              >
                {words}
              </button>
            ))}
          </div>

          {tab === "map" ? (
            <RoomMap api={api} things={mapThings} selectedId={mapSelect} />
          ) : (
            <>
              <div className="space-y-2">
                <Button onClick={openAdd} className={cn(TAP_BUTTON_CLASS, "w-full sm:w-auto")} data-testid="add-light">
                  <Plus aria-hidden /> Add a light
                </Button>
                <p className="text-[15px] text-muted-foreground">
                  {rig.rows !== null && dmxCount === 0
                    ? "No DMX lights yet. Plug one into an Art-Net box on the house network, then add it here."
                    : "Plug it into an Art-Net box on the house network, then tell the room what it is and where."}
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
                groups.map((g) => (
                  <section key={g.key} aria-label={g.title} data-testid={`lights-group-${g.key}`}>
                    <h2 className="text-lg font-semibold text-stone-800">{g.title}</h2>
                    {g.rows.some((r) => r.hue) && (
                      <p className="mb-2 text-sm text-muted-foreground">
                        The Hue lamps come from the Hue bridge{bridge ? ` (${bridge})` : ""} and are named in the Hue app.
                      </p>
                    )}
                    <div className={cn(CARD_CLASS, "mt-2 px-3 py-2 sm:px-4")}>
                      <DeviceList>
                        {g.rows.map((r) => (
                          <LightItem
                            key={r.id}
                            row={r}
                            view={viewOf(r)}
                            api={api}
                            types={types.types ?? []}
                            focus={focusId === r.id}
                            onEdit={openEdit}
                            onChanged={rig.refresh}
                          />
                        ))}
                      </DeviceList>
                    </div>
                  </section>
                ))
              )}

              <RemoteControls api={roomApi} />

              {rig.problems.length > 0 && (
                <Unroll title="Things to check" hint={`${rig.problems.length}`} testId="lights-problems">
                  <ul className="list-disc space-y-1 pb-2 pl-6 text-[15px]">
                    {rig.problems.map((p) => (
                      <li key={p}>{p}</li>
                    ))}
                  </ul>
                </Unroll>
              )}
            </>
          )}
        </main>
      </div>

      <LightSheet
        open={sheetOpen}
        onOpenChange={setSheetOpen}
        editing={editing}
        api={api}
        rows={rows}
        types={types}
        ui={rig.ui}
        inModes={describe ? (id) => inModes(id, true) : undefined}
        onSaved={rig.refresh}
        onShowOnMap={(id) => {
          setMapSelect(id);
          setTab("map");
        }}
      />

      <AppMenu open={menuOpen} onOpenChange={setMenuOpen} current="lights" />
    </div>
  );
}
