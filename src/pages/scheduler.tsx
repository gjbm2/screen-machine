// /scheduler: Schedules, what each screen does through the day (owner, 24 Oct: "you just haven't really worked the
// interface ui through properly throughout"; plan B11). A power page for the desk at PC22, usable on a phone.
//
// One header (PageHeader: "‹ Room" back, "Schedules", Mic and AI, the menu), then folds grouped by the site's rooms
// (rooms.json, from room_service's status): one per room with its screens, "Other screens" for the rest, a folded
// "Behind the scenes" (hidden destinations, and schedules that serve a room's actions without being a screen of it,
// e.g. the cloud's) and a folded "Advanced" (the variables registry and every scheduler event). Empty groups are left
// out. Each schedule is a SchedulerCard: one row closed (status dot, name, the next run in words), the controls open.
//
// Deep links: ?destination=<id> opens that card and its group, scrolls to it and rings it; ?room=<room id> opens that
// room's group (the Room page's Today's art links here). An unknown id says so in one line.
//
// Reads: the destinations once (and on Refresh), and ONE poll of /api/schedulers/all/status every 10 s while the page
// is visible. A card reads its own stack, queue and log only while open. The first load shows "Reading…"; after that
// refreshes happen in the background and every card keeps its place and open state.

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { RefreshCcw } from "lucide-react";
import { Button } from "@/components/ui/button";
import { AppMenu } from "@/components/nav/AppMenu";
import { PageHeader } from "@/components/nav/PageHeader";
import { roomPageUrl, useRoomLink } from "@/components/room/roomLink";
import { useRoomEvents } from "@/components/room/EventsList";
import { SchemaEditModal } from "@/components/scheduler/SchemaEditModal";
import { SchedulerCard, type RoomRef, type ScheduleDest } from "@/components/scheduler/SchedulerCard";
import { SchedulerEventsPanel } from "@/components/scheduler/SchedulerEventsPanel";
import { VarsRegistryCard } from "@/components/scheduler/VarsRegistryCard";
import { runState, type SchedStatus } from "@/components/scheduler/scheduleWords";
import { cn } from "@/lib/utils";
import { Notice, Unroll } from "@/pages/Room/bits";
import { openFold, Section } from "@/pages/Room/Section";
import { RoomThemeStyle, THEME_CLASS } from "@/pages/Room/theme";
import { publishApi, schedulerApi, API_URL } from "@/shared/api";

const POLL_MS = 10_000;

interface Dest extends ScheduleDest {
  hidden?: boolean;
}

interface EditorState {
  destination: string;
  name: string;
  schema: string;
  initialData: unknown;
  saveEndpoint: string;
  saveMethod: "POST" | "PUT";
}

interface Group {
  id: string;
  title: string;
  items: string[];
  defaultOpen: boolean;
  room?: RoomRef;
}

function usePageTitle(title: string) {
  useEffect(() => {
    const prev = document.title;
    document.title = title;
    return () => {
      document.title = prev;
    };
  }, [title]);
}

/** "2 running · 1 paused" for a closed group. */
function groupSummary(ids: string[], statuses: Record<string, SchedStatus>): string {
  const n = { running: 0, paused: 0, stopped: 0, error: 0 };
  ids.forEach((id) => n[runState(statuses[id])]++);
  const parts = [
    n.running && `${n.running} running`,
    n.paused && `${n.paused} paused`,
    n.stopped && `${n.stopped} not running`,
    n.error && `${n.error} with a problem`,
  ].filter(Boolean);
  return parts.join(" · ") || `${ids.length} schedules`;
}

/** Reports which destinations run a room's actions (from the same shared read as its EventsList). */
function RoomActionsProbe({ room, report }: { room: RoomRef; report: (room: RoomRef, dests: string[]) => void }) {
  const actions = useRoomEvents(room.id);
  const key = actions ? [...new Set(actions.map((a) => a.dest ?? a.scope))].sort().join("|") : null;
  const reportRef = useRef(report);
  reportRef.current = report;
  useEffect(() => {
    if (key !== null) reportRef.current(room, key ? key.split("|") : []);
  }, [key, room]);
  return null;
}

const Scheduler = () => {
  usePageTitle("Schedules · screen/machine");
  const [params] = useSearchParams();
  const wantDest = params.get("destination");
  const wantRoom = params.get("room");
  const link = useRoomLink();

  const [dests, setDests] = useState<Dest[] | null>(null);
  const [statuses, setStatuses] = useState<Record<string, SchedStatus>>({});
  const [loaded, setLoaded] = useState(false);
  const [readError, setReadError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [refreshKey, setRefreshKey] = useState(0);
  const [openCards, setOpenCards] = useState<Set<string>>(() => new Set(wantDest ? [wantDest] : []));
  const [highlight, setHighlight] = useState<string | null>(wantDest);
  const [note, setNote] = useState<string | null>(null);
  const [menuOpen, setMenuOpen] = useState(false);
  const [editor, setEditor] = useState<EditorState | null>(null);
  const [servesByRoom, setServesByRoom] = useState<Record<string, { room: RoomRef; dests: string[] }>>({});

  const readStatuses = useCallback(async () => {
    try {
      const r = await schedulerApi.getAllSchedulerStatuses();
      setStatuses((r?.statuses ?? {}) as Record<string, SchedStatus>);
      setReadError(null);
    } catch (e) {
      setReadError(e instanceof Error ? e.message : String(e));
    }
  }, []);

  const readDests = useCallback(async () => {
    try {
      const d = await publishApi.getPublishDestinations(true);
      setDests(d.map((x) => ({ id: x.id, name: x.name || x.id, hidden: x.hidden })));
    } catch (e) {
      setDests((prev) => prev ?? []);
      setReadError(e instanceof Error ? e.message : String(e));
    }
  }, []);

  useEffect(() => {
    void Promise.allSettled([readDests(), readStatuses()]).finally(() => setLoaded(true));
  }, [readDests, readStatuses]);

  // one poll of every schedule's status, only while the page is visible (and at once when it comes back)
  useEffect(() => {
    if (!loaded) return;
    let id: ReturnType<typeof setInterval> | null = null;
    const startPoll = () => {
      if (id === null) id = setInterval(() => void readStatuses(), POLL_MS);
    };
    const stopPoll = () => {
      if (id !== null) clearInterval(id);
      id = null;
    };
    const onVis = () => {
      if (document.hidden) stopPoll();
      else {
        void readStatuses();
        startPoll();
      }
    };
    if (!document.hidden) startPoll();
    document.addEventListener("visibilitychange", onVis);
    return () => {
      stopPoll();
      document.removeEventListener("visibilitychange", onVis);
    };
  }, [loaded, readStatuses]);

  const refresh = async () => {
    setRefreshing(true);
    await Promise.allSettled([readDests(), readStatuses()]);
    setRefreshKey((k) => k + 1);
    setRefreshing(false);
  };

  // ── groups ──
  const rooms = useMemo(
    () => (link.status?.rooms ?? []).map((r) => ({ id: r.id, name: r.name || r.id, screens: r.screens ?? [] })),
    [link.status?.rooms],
  );
  const reportServes = useCallback((room: RoomRef, ds: string[]) => {
    setServesByRoom((prev) => {
      const old = prev[room.id];
      if (old && old.dests.join("|") === ds.join("|")) return prev;
      return { ...prev, [room.id]: { room, dests: ds } };
    });
  }, []);

  const { byId, groups, roomOf, servesOf } = useMemo(() => {
    const byId = new Map<string, Dest>();
    (dests ?? []).forEach((d) => byId.set(d.id, d));
    Object.keys(statuses).forEach((id) => {
      if (!byId.has(id)) byId.set(id, { id, name: id, hidden: id.startsWith("_") });
    });
    const roomOf = new Map<string, RoomRef>();
    rooms.forEach((r) => r.screens.forEach((s) => roomOf.set(s, { id: r.id, name: r.name })));
    const servesOf = new Map<string, RoomRef[]>();
    Object.values(servesByRoom).forEach(({ room, dests: ds }) =>
      ds.forEach((d) => {
        if (!roomOf.has(d)) servesOf.set(d, [...(servesOf.get(d) ?? []), room]);
      }),
    );
    const ids = [...byId.keys()];
    const backstage = ids.filter((id) => !roomOf.has(id) && (byId.get(id)?.hidden || id.startsWith("_") || servesOf.has(id)));
    const other = ids.filter((id) => !roomOf.has(id) && !backstage.includes(id));
    const groups: Group[] = [
      ...rooms.map((r) => ({
        id: `sched-room-${r.id}`,
        title: r.name,
        items: r.screens.filter((s) => byId.has(s)),
        defaultOpen: true,
        room: { id: r.id, name: r.name },
      })),
      { id: "sched-other", title: rooms.length ? "Other screens" : "Screens", items: other, defaultOpen: true },
      { id: "sched-backstage", title: "Behind the scenes", items: backstage, defaultOpen: false },
    ].filter((g) => g.items.length > 0);
    return { byId, groups, roomOf, servesOf };
  }, [dests, statuses, rooms, servesByRoom]);

  // ── ?destination= / ?room=: once the groups are known (rooms come from room_service; give it 3 s) ──
  const [waited, setWaited] = useState(false);
  useEffect(() => {
    const t = setTimeout(() => setWaited(true), 3000);
    return () => clearTimeout(t);
  }, []);
  const ready = loaded && dests !== null && (link.onLan === false || !!link.status || waited);
  const revealed = useRef(false);
  useEffect(() => {
    if (!ready || revealed.current) return;
    if (!wantDest && !wantRoom) return;
    revealed.current = true;
    if (wantDest) {
      const g = groups.find((x) => x.items.includes(wantDest));
      if (!g) {
        setNote(`There's no schedule for “${wantDest}” here.`);
        setHighlight(null);
        return;
      }
      openFold(g.id);
      setOpenCards((s) => new Set(s).add(wantDest));
      const smooth = !window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
      setTimeout(() => document.getElementById(`schedule-${wantDest}`)?.scrollIntoView({ behavior: smooth ? "smooth" : "auto", block: "start" }), 350);
      return;
    }
    if (wantRoom) {
      const g = groups.find((x) => x.room?.id === wantRoom);
      if (g) openFold(g.id);
      else setNote(`There's no room called “${wantRoom}” here.`);
    }
  }, [ready, wantDest, wantRoom, groups]);

  // ── the full editor ──
  const openEditor = async (destId: string, layer: number | null) => {
    try {
      const schema = await schedulerApi.getSchedulerSchema();
      if (!schema) return;
      const name = byId.get(destId)?.name ?? destId;
      if (layer === null) {
        setEditor({ destination: destId, name, schema, initialData: {}, saveEndpoint: `${API_URL}/schedulers/${destId}/schedule`, saveMethod: "POST" });
      } else {
        const r = await schedulerApi.getScheduleAtPosition(destId, layer);
        setEditor({
          destination: destId,
          name,
          schema,
          initialData: r.schedule,
          saveEndpoint: `${API_URL}/schedulers/${destId}/schedule/${layer}`,
          saveMethod: "PUT",
        });
      }
    } catch {
      /* the API layer has said what went wrong */
    }
  };
  const editorSaved = async () => {
    const ed = editor;
    if (!ed) return;
    if (ed.saveMethod === "POST" && runState(statuses[ed.destination]) === "stopped") {
      // a new schedule on a schedule that isn't running starts it, as before
      try {
        await schedulerApi.startScheduler(ed.destination, {});
      } catch {
        /* said by the API layer */
      }
    }
    await readStatuses();
    setRefreshKey((k) => k + 1);
  };

  const backTo = wantDest && roomOf.has(wantDest) ? roomPageUrl(wantDest) : "/room#today";
  const toggleCard = (id: string, open: boolean) => {
    if (!open && highlight === id) setHighlight(null);
    setOpenCards((s) => {
      const n = new Set(s);
      if (open) n.add(id);
      else n.delete(id);
      return n;
    });
  };

  return (
    <div className={cn(THEME_CLASS, "min-h-screen bg-background text-base text-foreground antialiased")}>
      <RoomThemeStyle />
      {rooms.map((r) => (
        <RoomActionsProbe key={r.id} room={{ id: r.id, name: r.name }} report={reportServes} />
      ))}
      <div className="mx-auto w-full min-w-0 max-w-4xl px-4 pb-28 sm:px-6">
        <PageHeader title="Schedules" current="scheduler" onMenu={() => setMenuOpen(true)} toggles="auto" back={{ to: backTo }} compact />

        <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-2">
          <p className="min-w-0 flex-1 basis-56 text-[15px] leading-snug text-muted-foreground">
            What each screen does through the day. Today's values, such as the theme, are changed on the Room page under Today's art.
          </p>
          <Button type="button" variant="outline" onClick={() => void refresh()} disabled={refreshing} className="h-11 rounded-xl px-4 text-base">
            <RefreshCcw aria-hidden className={cn(refreshing && "motion-safe:animate-spin")} /> Refresh
          </Button>
        </div>

        {note && (
          <Notice tone="info" className="mt-3">
            {note}
          </Notice>
        )}
        {readError && loaded && (
          <Notice tone="warn" className="mt-3">
            The schedules can't be read just now ({readError}). It keeps trying.
          </Notice>
        )}

        {!loaded ? (
          <p className="py-10 text-center text-[15px] text-muted-foreground">Reading the schedules…</p>
        ) : (
          <main className="mt-2 min-w-0 divide-y divide-stone-200/80 border-y border-stone-200/80" data-testid="schedules-page">
            {groups.map((g) => (
              <Section key={g.id} id={g.id} title={g.title} summary={groupSummary(g.items, statuses)} defaultOpen={g.defaultOpen}>
                <div className="grid min-w-0 gap-3">
                  {g.items.map((id) => (
                    <SchedulerCard
                      key={id}
                      dest={{ id, name: byId.get(id)?.name ?? id }}
                      status={statuses[id]}
                      open={openCards.has(id)}
                      onOpenChange={(o) => toggleCard(id, o)}
                      highlight={highlight === id}
                      room={roomOf.get(id) ?? null}
                      servesRooms={servesOf.get(id) ?? []}
                      refreshKey={refreshKey}
                      onChanged={() => void readStatuses()}
                      onEdit={(d, layer) => void openEditor(d, layer)}
                    />
                  ))}
                </div>
              </Section>
            ))}
            {groups.length === 0 && <p className="py-6 text-[15px] text-muted-foreground">No screens have a schedule.</p>}
            <Section id="sched-advanced" title="Advanced" summary="The variables registry and every scheduler event">
              <div className="min-w-0 space-y-2">
                <Unroll title="Variables registry" contentClassName="min-w-0 pt-2">
                  <VarsRegistryCard />
                </Unroll>
                <Unroll title="All scheduler events" contentClassName="min-w-0 pt-2">
                  <SchedulerEventsPanel />
                </Unroll>
              </div>
            </Section>
          </main>
        )}
      </div>

      <AppMenu open={menuOpen} onOpenChange={setMenuOpen} current="scheduler" />

      {editor && (
        <SchemaEditModal
          open={!!editor}
          onOpenChange={(o) => !o && setEditor(null)}
          onSave={() => void editorSaved()}
          destination={editor.destination}
          destinationName={editor.name}
          schema={editor.schema}
          initialData={editor.initialData}
          saveEndpoint={editor.saveEndpoint}
          saveMethod={editor.saveMethod}
          scriptsDirectory="routes/scheduler/scripts"
        />
      )}
    </div>
  );
};

export default Scheduler;
