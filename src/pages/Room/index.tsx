// /room: the room's control panel (AV_PROGRAMME §5), used mostly from a phone on
// the house Wi-Fi. The first screen answers "what's the room doing, and change it":
// the modes (room-wide) and the Music card under them (MusicCard.tsx), then one section
// per room of the site (status.rooms, from routes/data/rooms.json) with its screens and
// lights, then "Other" for anything in no room. Everything technical sits behind the menu button or a screen's "More".
//
// Talks to room_service (LIVE_V1 §3) over HTTP and its events socket. room_service
// is found at ?room=<url> (remembered), else the remembered sm_room_url, else this
// host on :8700. See README.md in this folder.

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { KeyRound, Settings2, WifiOff } from "lucide-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { loadToken, resolveRoomUrl, saveToken, storageSet, URL_KEY } from "./api";
import { Notice, Spinner, Unroll } from "./bits";
import { isConnected, linkState } from "./connection";
import { EffectsSection } from "./Effects";
import { LightsSection } from "./Fixtures";
import { buildTagIndex } from "./sceneTags";
import { useDescribe } from "./useDescribe";
import { useFixtures } from "./useFixtures";
import { NowPlayingRow } from "./NowPlayingRow";
import { Header } from "./Header";
import { MenuSheet } from "./MenuSheet";
import { ModeEditor } from "./ModeEditor";
import { ModeSwitcher } from "./ModeSwitcher";
import { MusicCard, useShowFeed, useShowState } from "./MusicCard";
import { PianoCard } from "./PianoCard";
import type { LookScope } from "./liveLib";
import type { LiveMember } from "./roomFx";
import { RoomLive } from "./RoomLive";
import { ScreenCard } from "./ScreenCard";
import { CARD_CLASS, RoomThemeStyle, TAP_BUTTON_CLASS, THEME_CLASS } from "./theme";
import { TokenDialog } from "./TokenPrompt";
import type { ModeDef, RoomStatus, SiteEffect, TrackedTransition } from "./types";
import { useRoomConnection } from "./useRoomConnection";
import { liveOf } from "./useScreenControls";
import { friendlyName } from "./words";

/** The tab title while on this page, put back on the way out. <html> itself is never touched. */
function usePageTitle(title: string) {
  useEffect(() => {
    const prev = document.title;
    document.title = title;
    return () => {
      document.title = prev;
    };
  }, [title]);
}

function useNow(ms: number): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), ms);
    return () => clearInterval(id);
  }, [ms]);
  return now;
}

const H2 = "mb-2 text-lg font-semibold text-stone-800";

/**
 * The screens a running mode change is still working on (a pending or running step), so their
 * cards don't offer an inline Cancel that would fight the mode just chosen. Before the steps are
 * known, the screens the target mode sets count.
 */
function screensInModeChange(t: TrackedTransition | null, modes: Record<string, ModeDef> | null): Set<string> {
  const out = new Set<string>();
  if (!t || t.phase === "done" || t.phase === "cancelled") return out;
  const steps = t.steps ?? [];
  if (!steps.length) {
    const screens = (t.to && modes?.[t.to]?.state?.screens) || null;
    if (screens && typeof screens === "object") for (const id of Object.keys(screens)) out.add(id);
    return out;
  }
  for (const st of steps) {
    const m = /^screens\.(.+)$/.exec(st.subsystem ?? "");
    if (m && (st.status === "pending" || st.status === "running")) out.add(m[1]);
  }
  return out;
}

/** One section of the page: a room (its screens, then its lights and effects), or "Other". */
interface Section {
  key: string;
  title: string;
  screens: string[];
  /** The section's lamp ids, or null for none. */
  lights: string[] | null;
  /** Device cards (rooms.json "effects"), e.g. the glitterball. */
  effects: SiteEffect[];
}

/**
 * The page's sections from status.rooms, in file order, then "Other" for screens and lamps in no room. A section
 * with nothing to show is left out. `lampIds` are the lamps the room has (null while still loading). Without
 * rooms (an older room_service) there are none: the page shows the flat list instead.
 */
function buildSections(status: RoomStatus | null, lampIds: string[] | null): Section[] {
  const screens = status?.screens ?? {};
  const rooms = status?.rooms ?? [];
  if (!rooms.length) return [];
  const placed = new Set<string>();
  const placedLamps = new Set<string>();
  const out: Section[] = rooms.map((r) => {
    const ids = (r.screens ?? []).filter((id) => id in screens && !placed.has(id));
    ids.forEach((id) => placed.add(id));
    (r.lights ?? []).forEach((id) => placedLamps.add(id));
    return {
      key: r.id,
      title: r.name || r.id,
      screens: ids,
      lights: r.lights?.length ? r.lights : null,
      effects: (r.effects ?? []).filter((e) => !!status?.subsystems?.[e.power.split(".")[0]]),
    };
  });
  const otherScreens = Object.keys(screens).filter((id) => !placed.has(id));
  const otherLamps = (lampIds ?? []).filter((id) => !placedLamps.has(id));
  out.push({ key: "_other", title: "Other", screens: otherScreens, lights: otherLamps.length ? otherLamps : null, effects: [] });
  const hasLamps = (ids: string[] | null) => !!ids && (lampIds === null || ids.some((id) => lampIds.includes(id)));
  return out.filter((s) => s.screens.length > 0 || hasLamps(s.lights) || s.effects.length > 0);
}

export default function RoomPage() {
  usePageTitle("Room · screen/machine");
  const [searchParams, setSearchParams] = useSearchParams();
  const [baseUrl, setBaseUrl] = useState(() => resolveRoomUrl(window.location.search));
  // The saved token only goes to the server it was entered for (see loadToken).
  // URL and token always change together, in one render, so a token never reaches a server it wasn't meant for.
  const [saved, setSaved] = useState(() => loadToken(baseUrl));
  const token = saved.token;
  const [tokenOpen, setTokenOpen] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);
  const room = useRoomConnection(baseUrl, token);
  const { conn, status } = room;
  const now = useNow(1000);

  const connected = isConnected(conn, now);
  const link = linkState(conn, now);

  // A 401 brings up the setup-key prompt (operating is open; the key is for setup edits).
  useEffect(() => {
    if (conn.authRequired) setTokenOpen(true);
  }, [conn.authRequired]);

  const { reconnect } = room;
  const onSaveToken = useCallback(
    (t: string) => {
      saveToken(baseUrl, t);
      setSaved({ token: t, foreign: null });
      setTokenOpen(false);
      reconnect(); // also covers saving the same token again
    },
    [baseUrl, reconnect],
  );

  const changeUrl = useCallback(
    (url: string | null) => {
      storageSet(URL_KEY, url);
      // Keep ?room= in step, since it wins over the remembered URL on reload.
      const next = new URLSearchParams(searchParams);
      if (url) next.set("room", url);
      else next.delete("room");
      setSearchParams(next, { replace: true });
      const nextUrl = resolveRoomUrl(next.toString() ? `?${next.toString()}` : "");
      setBaseUrl(nextUrl);
      setSaved(loadToken(nextUrl));
    },
    [searchParams, setSearchParams],
  );

  const openMenu = useCallback(() => setMenuOpen(true), []);
  const openToken = useCallback(() => {
    setMenuOpen(false);
    setTokenOpen(true);
  }, []);

  const screens = Object.entries(status?.screens ?? {});
  // One fixtures poll for every room's lights.
  const fixtures = useFixtures(room.api, !!status);
  // The Music card's link to show_service (through room_service's /show), shared with the menu's AI listening row.
  const show = useShowFeed(room.api, !!status, room.addLog);
  const micState = useShowState(show).card?.mic_state;
  const micOn = !!micState?.on && !micState.muted;
  const lampIds = useMemo(() => {
    if (fixtures.missing || (fixtures.view && !fixtures.view.enabled)) return [];
    return fixtures.view ? fixtures.view.fixtures.filter((f) => f.kind === "hue").map((f) => f.id) : null;
  }, [fixtures.missing, fixtures.view]);
  const sections = useMemo(() => buildSections(status, lampIds), [status, lampIds]);
  // Target labels and schemas plus the modes' entries, re-read when modes.json changes (effects, mode editor).
  const { describe, refresh: refreshDescribe } = useDescribe(room.api, status?.config?.revision, !!status);
  // The screens' names from the site config, by target: they beat a target's own label in the modes' words.
  const screenNames = useMemo(
    () =>
      Object.fromEntries(
        Object.entries(status?.screens ?? {}).flatMap(([id, sc]) => (sc.name ? [[`screens.${id}`, sc.name]] : [])),
      ) as Record<string, string>,
    [status?.screens],
  );
  const [editing, setEditing] = useState<string | null>(null);
  // The scene library's tags (every facet and word, with counts): what a mode can steer the Art Director with.
  const tagIndex = useMemo(() => buildTagIndex(room.catalog), [room.catalog]);
  const modeBusy = useMemo(() => screensInModeChange(room.transition, room.modes), [room.transition, room.modes]);

  // /room?screen=<id> (the TVs' QR codes): that screen's card opens and is highlighted, and the page
  // scrolls to it once, after the modes above it have laid out (or failed to load).
  const focusScreen = searchParams.get("screen")?.trim() || null;
  const laidOut = !!status && (room.modes !== null || !!room.modesError);
  const unknownFocus = !!focusScreen && !!status && !(focusScreen in (status.screens ?? {}));
  const scrolledTo = useRef<string | null>(null);
  useEffect(() => {
    if (!focusScreen || !laidOut || scrolledTo.current === focusScreen) return;
    const el = document.querySelector<HTMLElement>(`[data-screen="${CSS.escape(focusScreen)}"]`);
    if (!el) return;
    scrolledTo.current = focusScreen;
    const smooth = !window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
    requestAnimationFrame(() => el.scrollIntoView({ block: "start", behavior: smooth ? "smooth" : "auto" }));
  }, [focusScreen, laidOut, screens.length]);

  const screenCard = (id: string, scope?: LookScope | null) => {
    const sc = status?.screens?.[id];
    if (!sc) return null;
    return (
      <ScreenCard
        key={id}
        screenId={id}
        screen={sc}
        catalog={room.catalog}
        catalogError={room.catalogError}
        library={room.library}
        subscribeStream={room.subscribeStream}
        api={room.api}
        connected={connected}
        streamReachable={status?.stream_service?.reachable}
        inModeChange={modeBusy.has(id)}
        focused={id === focusScreen}
        scope={scope}
        addLog={room.addLog}
        reportError={room.reportError}
      />
    );
  };

  // A room's live screens (live or going live, with a session), for its live card: the room's controls.
  const liveMembers = (ids: string[]): LiveMember[] =>
    ids.flatMap((id) => {
      const sc = status?.screens?.[id];
      const obs = sc?.observed;
      const ph = obs?.phase;
      return sc && liveOf(sc) && (ph === "live" || ph === "entering") && obs?.session
        ? [{ id, name: sc.name || friendlyName(id), session: obs.session }]
        : [];
    });

  return (
    <div className={cn(THEME_CLASS, "min-h-screen bg-background text-base text-foreground antialiased")}>
      <RoomThemeStyle />
      <div className="mx-auto w-full max-w-3xl px-4 pb-16 sm:px-6">
        <Header link={link} onMenu={openMenu} screen={focusScreen} micOn={micOn} />

        <main className="space-y-5 pt-1">
          {conn.authRequired && !tokenOpen && (
            <div className={cn(CARD_CLASS, "space-y-3 p-5")}>
              <p className="flex items-center gap-2 text-[17px] font-semibold">
                <KeyRound className="h-5 w-5 text-amber-700" aria-hidden /> This room needs a key
              </p>
              <p className="text-base text-muted-foreground">Ask whoever runs the room, or enter it here.</p>
              <div className="flex flex-wrap gap-2">
                <Button onClick={() => setTokenOpen(true)} className={TAP_BUTTON_CLASS}>
                  Enter the key
                </Button>
                <Button variant="outline" onClick={openMenu} className={cn(TAP_BUTTON_CLASS, "bg-white")}>
                  <Settings2 /> Settings
                </Button>
              </div>
            </div>
          )}

          {status && !connected && !conn.authRequired && (
            <Notice tone="warn" className="px-4 py-3 text-base leading-snug">
              <p className="flex items-center gap-2 font-semibold">
                <WifiOff className="h-4 w-4 shrink-0" aria-hidden /> Lost touch with the room
              </p>
              <p className="mt-0.5">Showing what it was last doing. Taps on a mode keep trying until the room answers.</p>
              {conn.lastError && (
                <Unroll title={<span className="text-sm">Details</span>} className="mt-1">
                  <p className="pb-1 text-sm">{conn.lastError}</p>
                </Unroll>
              )}
            </Notice>
          )}

          {status ? (
            <>
              <section aria-labelledby="room-modes">
                <h2 id="room-modes" className={H2}>
                  Mode
                </h2>
                <ModeSwitcher
                  api={room.api}
                  modes={room.modes}
                  modesError={room.modesError}
                  mode={status.mode}
                  transition={room.transition}
                  catalog={room.catalog}
                  connected={connected}
                  now={now}
                  addLog={room.addLog}
                  reportError={room.reportError}
                  onEdit={setEditing}
                  describe={describe}
                  subsystems={status.subsystems}
                  names={screenNames}
                  onChanged={() => {
                    void room.refreshMeta();
                    refreshDescribe();
                  }}
                />
              </section>

              <MusicCard api={room.api} feed={show} now={now} addLog={room.addLog} />

              <PianoCard api={room.api} connected={connected} addLog={room.addLog} reportError={room.reportError} />

              {unknownFocus && (
                <Notice tone="muted" className="text-base">
                  There's no screen called “{focusScreen}” in the room. Here are the ones it has.
                </Notice>
              )}

              {sections.length > 0 ? (
                sections.map((sec) => (
                  <section key={sec.key} aria-labelledby={`room-sec-${sec.key}`} className="space-y-3">
                    <h2 id={`room-sec-${sec.key}`} className={H2}>
                      {sec.title}
                    </h2>
                    {/* A room of several screens: its controls act on all of them, so they have their own card
                        (scope follows where a control sits, owner 17 Oct). */}
                    {sec.key !== "_other" && sec.screens.length > 1 && room.library ? (
                      <RoomLive
                        room={{ id: sec.key, name: sec.title }}
                        members={liveMembers(sec.screens)}
                        total={sec.screens.length}
                        api={room.api}
                        lib={room.library}
                        subscribe={room.subscribeStream}
                        addLog={room.addLog}
                        reportError={room.reportError}
                      >
                        {(scopeFor) => sec.screens.map((id) => screenCard(id, scopeFor(id)))}
                      </RoomLive>
                    ) : (
                      sec.screens.map((id) => screenCard(id))
                    )}
                    {sec.lights && (
                      <LightsSection
                        api={room.api}
                        connected={connected}
                        addLog={room.addLog}
                        reportError={room.reportError}
                        fixtures={fixtures}
                        only={sec.lights}
                        sub
                      />
                    )}
                    {sec.effects.length > 0 && (
                      <EffectsSection
                        effects={sec.effects}
                        status={status}
                        describe={describe}
                        api={room.api}
                        connected={connected}
                        addLog={room.addLog}
                        reportError={room.reportError}
                      />
                    )}
                  </section>
                ))
              ) : (
                <>
                  <section aria-labelledby="room-screens">
                    <h2 id="room-screens" className={H2}>
                      Screens
                    </h2>
                    {screens.length === 0 ? (
                      <p className="text-base text-muted-foreground">The room has no screens set up.</p>
                    ) : (
                      <div className="space-y-3">{screens.map(([id]) => screenCard(id))}</div>
                    )}
                  </section>
                  <LightsSection
                    api={room.api}
                    connected={connected}
                    addLog={room.addLog}
                    reportError={room.reportError}
                    fixtures={fixtures}
                  />
                </>
              )}

              <NowPlayingRow api={room.api} addLog={room.addLog} />
            </>
          ) : (
            !conn.authRequired && (
              <div className={cn(CARD_CLASS, "space-y-3 p-6 text-center")} data-testid="room-waiting">
                {conn.lastError ? (
                  <>
                    <WifiOff className="mx-auto h-8 w-8 text-stone-400" aria-hidden />
                    <p className="text-[17px] font-semibold">Can't reach the room</p>
                    <p className="text-base text-muted-foreground">
                      Check you're on the house Wi-Fi. The page keeps trying by itself.
                    </p>
                    <div className="flex justify-center">
                      <Button variant="outline" onClick={openMenu} className={cn(TAP_BUTTON_CLASS, "bg-white")}>
                        <Settings2 /> Connection settings
                      </Button>
                    </div>
                    <Unroll title={<span className="text-sm">Details</span>} className="mx-auto max-w-md text-left">
                      <p className="pb-1 text-sm text-muted-foreground">{conn.lastError}</p>
                      <p className="break-all pb-2 font-mono text-xs text-muted-foreground">{baseUrl}</p>
                    </Unroll>
                  </>
                ) : (
                  <p className="flex items-center justify-center gap-2 text-base text-muted-foreground">
                    <Spinner /> Connecting to the room…
                  </p>
                )}
              </div>
            )
          )}
        </main>
      </div>

      <MenuSheet
        open={menuOpen}
        onOpenChange={setMenuOpen}
        baseUrl={baseUrl}
        conn={conn}
        link={link}
        now={now}
        status={status}
        modes={room.modes}
        transition={room.transition}
        catalog={room.catalog}
        describe={describe}
        names={screenNames}
        hasToken={!!token}
        foreignToken={saved.foreign}
        onChangeUrl={changeUrl}
        onOpenToken={openToken}
        onRefresh={() => void room.refreshMeta()}
        log={room.log}
        statusCount={room.statusCount}
        onClearLog={room.clearLog}
      />

      <ModeEditor
        name={editing}
        onClose={() => setEditing(null)}
        api={room.api}
        describe={describe}
        status={status}
        tagIndex={tagIndex}
        connected={connected}
        onSaved={() => {
          void room.refreshMeta();
          refreshDescribe();
        }}
        addLog={room.addLog}
        names={Object.fromEntries(
          Object.entries(status?.screens ?? {}).flatMap(([id, sc]) => (sc.name ? [[`screens.${id}`, sc.name]] : [])),
        )}
      />

      <TokenDialog
        open={tokenOpen}
        onOpenChange={setTokenOpen}
        baseUrl={baseUrl}
        required={conn.authRequired}
        foreign={saved.foreign}
        onSave={onSaveToken}
      />
    </div>
  );
}
