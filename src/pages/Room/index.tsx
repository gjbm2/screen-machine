// /room: the room's control panel (AV_PROGRAMME §5), used mostly from a phone on
// the house Wi-Fi. Layout A (owner, 24 Oct: "too much going on today and not enough folded away"): the header (its
// status line says what the room is doing: "Normal · art on all 3 screens · lights off · nothing playing"), the modes
// with Auto above them, then the folds (Section.tsx), each one line with its summary while closed, so the closed page
// reads as an overview. From 1024 px two columns (owner, 25 Oct: space pass): the modes and Look on the left, the rest
// on the right, the player under both:
//   Look         who is choosing; Calmer · Wilder, Describe it, the palette (MusicCard.tsx DirectorControls) and, while
//                a room's screens are live, that room's scene and effect picking (RoomLive.tsx)
//   Screens      each screen's card, under its room's name (status.rooms, from routes/data/rooms.json; "Other" last)
//   Today's art  each room's TodayCard (TodayFold.tsx)
//   Listening    room mic, AI listening, what it hears, levels (MusicCard.tsx ListeningPanel); the Mic and AI
//                switches are the header's (TopToggles.tsx), and tapping their line opens this fold
//   Lights       House lights and Lights low (RoomLightsRow.tsx), each room's lamps and room effects (the glitterball);
//                "Set up lights ›" at the right of its heading
//   Piano        the piano's keys and pedals (PianoRow.tsx), Record (also in its heading), the last recordings
// and the dock at the bottom (the app's player). Each fold ends with its "… settings ›" link (/settings#<group>).
//
// What is open (ia, 24 Oct): the person's taps are remembered per fold; on top of that, when the room changes mode
// while the page is open, Look opens for a mode that puts the screens live, Lights for one where only the lamps
// follow the music, and Piano for the mode Auto starts when the piano plays (each closes again on leaving); a
// recording starting anywhere opens Piano. Fold order never changes. Everything technical sits behind the menu
// button or a screen's "More".
//
// Talks to room_service (LIVE_V1 §3) over HTTP and its events socket. room_service
// is found at ?room=<url> (remembered), else the remembered sm_room_url, else this
// host on :8700. See README.md in this folder.

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { ChevronRight, KeyRound, Settings2, WifiOff } from "lucide-react";
import { rendererConsoleUrl, settingsUrl } from "@/components/nav/appNav";
import { useAppPlayer } from "@/components/player/appPlayer";
import { PLAYER_BUSY } from "@/pages/Music/usePlayer";
import { Button } from "@/components/ui/button";
import { shortNames } from "@/components/room/TodayCard";
import { cn } from "@/lib/utils";
import { loadToken, resolveRoomUrl, saveToken, type RoomApi } from "./api";
import { useAutoRules } from "./AutoTriggers";
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
import { AutoRow, directorWords, DirectorControls, ListeningPanel, listeningWords, useShowFeed, useShowState } from "./MusicCard";
import { ListenHere } from "./ListenHere";
import { PianoCard } from "./PianoCard";
import { RecordPill, useRecorder } from "./PianoRecorder";
import { PianoRow } from "./PianoRow";
import { lightsFollow, lightsWords, RoomLightsRow } from "./RoomLightsRow";
import type { LookScope } from "./liveLib";
import type { LiveMember } from "./roomFx";
import { RoomLives, type RoomLiveSpec } from "./RoomLive";
import { ScreenCard } from "./ScreenCard";
import { sceneChoices } from "./catalog";
import { FOOT_LINK, openFold, Section, setFold } from "./Section";
import { useTopToggles } from "./TopToggles";
import { TodayFold } from "./TodayFold";
import { CARD_CLASS, RoomThemeStyle, TAP_BUTTON_CLASS, THEME_CLASS } from "./theme";
import { TokenDialog } from "./TokenPrompt";
import type { FixturesView, ModeDef, RoomStatus, SiteEffect, TrackedTransition } from "./types";
import { usePcLoad } from "./usePcLoad";
import { useRoomConnection } from "./useRoomConnection";
import { liveOf } from "./useScreenControls";
import { friendlyName, summariseScreen } from "./words";

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

const H3 = "mb-2 text-base font-semibold text-stone-700";
/** While the music side's card was read over HTTP this recently it still counts as answering (as MusicCard). */
const SHOW_FRESH_MS = 12_000;

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

/** What a mode puts on show, from its targets (the modes view: target -> {value}): a screen live, a lamp following. */
function modeShows(def: ModeDef | undefined): { screens: boolean; lamps: boolean } {
  const targets = (def as { targets?: Record<string, { value?: unknown } | null> } | undefined)?.targets ?? {};
  let screens = false;
  let lamps = false;
  for (const [k, t] of Object.entries(targets)) {
    const v = t?.value;
    if (k.startsWith("screens.") && !!v && typeof v === "object" && "live" in v) screens = true;
    if ((k.startsWith("hue.") || k === "light" || k.startsWith("light.")) && v === "dmx") lamps = true;
  }
  return { screens, lamps };
}

/** A mode's tile is on the Room page's own strip (the mode's generic on_room_page field; absent = yes). */
function onRoomPage(def: ModeDef | undefined): boolean {
  const v = (def as { on_room_page?: unknown } | undefined)?.on_room_page;
  return v !== false && v !== "more";
}

function andList(xs: string[]): string {
  return xs.length <= 1 ? (xs[0] ?? "") : `${xs.slice(0, -1).join(", ")} and ${xs[xs.length - 1]}`;
}

/** show_service's own words for "the piano is playing" (its vocabulary), read once: the rule that starts the piano's
 *  mode names it. Until it answers, the word's usual label. */
function usePianoWord(api: RoomApi, enabled: boolean): string {
  const [label, setLabel] = useState("the piano is playing");
  useEffect(() => {
    if (!enabled) return;
    const ac = new AbortController();
    fetch(`${api.baseUrl}/show/api/v1/situations`, { signal: ac.signal, cache: "no-cache" })
      .then((r) => (r.ok ? r.json() : null))
      .then((b: { vocabulary?: { id?: string; label?: string }[] } | null) => {
        const w = b?.vocabulary?.find((x) => x?.id === "piano_playing")?.label;
        if (w) setLabel(w);
      })
      .catch(() => {
        /* the usual label */
      });
    return () => ac.abort();
  }, [api, enabled]);
  return label;
}

/** The lamps in a word for the top line: following the music, on, or off (null: no lamps). */
function lampsWord(view: FixturesView | null, status: RoomStatus | null): string | null {
  const lamps = view?.enabled ? view.fixtures.filter((f) => f.kind === "hue") : [];
  if (!lamps.length) return null;
  if (lightsFollow(view, status)) return "lights follow the music";
  return lamps.some((f) => f.status === "house" || f.light?.on) ? "lights on" : "lights off";
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
  const [searchParams] = useSearchParams();
  const [baseUrl] = useState(() => resolveRoomUrl(window.location.search));
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

  const openMenu = useCallback(() => setMenuOpen(true), []);

  const screens = Object.entries(status?.screens ?? {});
  // One fixtures poll for every room's lights.
  const fixtures = useFixtures(room.api, !!status);
  // The Music card's link to show_service (through room_service's /show), shared with the menu's AI listening row.
  const show = useShowFeed(room.api, !!status, room.addLog);
  const showState = useShowState(show);
  const showCard = showState.card;
  const showAnswering = !!showCard && (showState.live || (showState.lastAt !== null && now - showState.lastAt < SHOW_FRESH_MS));
  // The top bar's Mic and AI switches (owner, 24 Oct), on the same card; their details are in Listening.
  const toggles = useTopToggles(room.api, show, room.addLog);
  const openListening = useCallback(() => openFold("listening"), []);
  // What the folds' summaries need that a card inside them reads for itself (told up, nothing fetched again).
  const [pianoLinked, setPianoLinked] = useState<boolean | null>(null);
  const [pianoLine, setPianoLine] = useState<string | null | undefined>(undefined);
  // "PC busy" in the header when other work overloads the PC (owner, 22 Oct).
  const pc = usePcLoad(room.api, !!status);
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
  // "How Auto picks ›" Edit opens the mode's editor at "Starts by itself"
  const [editAt, setEditAt] = useState<"starts" | null>(null);
  const editRule = useCallback((mode: string) => {
    setEditAt("starts");
    setEditing(mode);
  }, []);
  const editMode = useCallback((mode: string) => {
    setEditAt(null);
    setEditing(mode);
  }, []);
  // The scene library's tags (every facet and word, with counts): what a mode can steer the Art Director with.
  const tagIndex = useMemo(() => buildTagIndex(room.catalog), [room.catalog]);
  const modeBusy = useMemo(() => screensInModeChange(room.transition, room.modes), [room.transition, room.modes]);

  // /room?screen=<id> (the TVs' QR codes): that screen's card opens and is highlighted, and the page
  // scrolls to it once, after the modes above it have laid out (or failed to load).
  const focusScreen = searchParams.get("screen")?.trim() || null;
  const laidOut = !!status && (room.modes !== null || !!room.modesError);
  const unknownFocus = !!focusScreen && !!status && !(focusScreen in (status.screens ?? {}));
  // "Whole room ›": the same page without ?screen= (anything else in the address stays)
  const wholeRoomHref = useMemo(() => {
    const q = new URLSearchParams(searchParams);
    q.delete("screen");
    const qs = q.toString();
    return `/room${qs ? `?${qs}` : ""}`;
  }, [searchParams]);
  const scrolledTo = useRef<string | null>(null);
  useEffect(() => {
    if (!focusScreen || !laidOut || scrolledTo.current === focusScreen) return;
    const el = document.querySelector<HTMLElement>(`[data-screen="${CSS.escape(focusScreen)}"]`);
    if (!el) return;
    scrolledTo.current = focusScreen;
    const smooth = !window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
    requestAnimationFrame(() => el.scrollIntoView({ block: "start", behavior: smooth ? "smooth" : "auto" }));
  }, [focusScreen, laidOut, screens.length]);

  // ── The folds' summaries and groups (from what the page already reads) ──
  const scenes = useMemo(() => sceneChoices(room.catalog), [room.catalog]);
  const anyLive = screens.some(([, sc]) => ["live", "entering"].includes(sc.observed?.phase ?? "") || sc.observed?.layer === "live");
  const director = directorWords(showCard, showAnswering);
  const screenGroups = sections.filter((sec) => sec.screens.length > 0);
  const lightGroups = sections.filter((sec) => !!sec.lights || sec.effects.length > 0);
  const todayRooms = sections.filter((sec) => sec.key !== "_other").map((sec) => ({ id: sec.key, name: sec.title }));
  const orderedScreens = sections.length > 0 ? screenGroups.flatMap((sec) => sec.screens) : screens.map(([id]) => id);
  const shortScreenNames = shortNames(orderedScreens.map((id) => status?.screens?.[id]?.name || friendlyName(id)));
  const screenTexts = orderedScreens.flatMap((id, i) => {
    const sc = status?.screens?.[id];
    return sc ? [{ name: shortScreenNames[i], text: summariseScreen(sc, scenes, status?.stream_service?.reachable).text }] : [];
  });
  // every screen saying the same thing says it once ("North, South, Dev test: Showing art")
  const screensSummary = !screenTexts.length
    ? "No screens set up"
    : screenTexts.length > 1 && screenTexts.every((x) => x.text === screenTexts[0].text)
      ? `${screenTexts.map((x) => x.name).join(", ")}: ${screenTexts[0].text}`
      : screenTexts.map((x) => `${x.name}: ${x.text}`).join(", ");
  const pianoSummary =
    [pianoLinked === false ? "Not connected" : null, pianoLine].filter(Boolean).join(" · ") || (pianoLinked ? "Connected" : "Piano");
  // From the lights' real states (owner's walk, 24 Oct: it said "4 lamps follow the music" while all four were off).
  const lightsSummary = lightsWords(fixtures.view, fixtures.missing, status, lightGroups.some((sec) => sec.effects.length > 0));
  const lampsFollow = lightsFollow(fixtures.view, status);

  // ── What the room is doing, in one line at the top (ia, 24 Oct: "is anything on?" without opening anything) ──
  const modeLabel = (id: string | null | undefined) => (id ? room.modes?.[id]?.label || friendlyName(id) : null);
  const liveIds = orderedScreens.filter((id) => {
    const o = status?.screens?.[id]?.observed;
    return ["live", "entering"].includes(o?.phase ?? "") || o?.layer === "live";
  });
  const screensWord = (() => {
    const n = orderedScreens.length;
    if (!n) return null;
    const all = n === 1 ? "the screen" : n === 2 ? "both screens" : `all ${n} screens`;
    if (!liveIds.length) return `art on ${all}`;
    if (liveIds.length === n) return `live on ${all}`;
    const nameOf = (id: string) => shortScreenNames[orderedScreens.indexOf(id)] ?? friendlyName(id);
    return `${andList(liveIds.map(nameOf))} live · art on ${andList(orderedScreens.filter((id) => !liveIds.includes(id)).map(nameOf))}`;
  })();
  const recorder = useRecorder(room.api.baseUrl);
  const player = useAppPlayer(room.api.baseUrl);
  const pv = player?.view ?? null;
  const sonosPlaying = Object.entries(
    ((status?.subsystems as Record<string, { believed?: unknown }> | undefined)?.sonos?.believed as Record<string, unknown> | undefined) ?? {},
  ).some(([k, v]) => k.endsWith(".playback") && v === "playing");
  // (a recording is the header's "● Recording" pill, not said again here)
  const musicWord =
    pv?.current && PLAYER_BUSY.includes(pv.state)
      ? `playing ${pv.current.title}`
      : pv?.current && pv.state === "paused"
        ? `paused: ${pv.current.title}`
        : sonosPlaying
          ? "music playing"
          : "nothing playing";
  const target = status?.mode?.target ?? null;
  const roomLine = [
    target && target !== status?.mode?.current ? `Switching to ${modeLabel(target)}` : modeLabel(status?.mode?.current),
    screensWord,
    lampsWord(fixtures.view, status),
    musicWord,
  ]
    .filter(Boolean)
    .join(" · ");

  // ── Look: whether anything shows the look now, and the modes it shows in (read from the modes, never named here) ──
  const nothingFollows = !anyLive && !lampsFollow;
  const lookModes = Object.entries(room.modes ?? {})
    .filter(([, def]) => onRoomPage(def) && (modeShows(def).screens || modeShows(def).lamps))
    .map(([id, def]) => def.label || friendlyName(id));

  // ── Folds that open with the room's state (ia, 24 Oct); the person's taps are remembered after that ──
  const autoRules = useAutoRules(room.api, 60_000, !!status);
  const pianoWord = usePianoWord(room.api, !!status);
  const pianoModes = useMemo(
    () => new Set((autoRules ?? []).filter((r) => !!r.enter && r.enter.includes(pianoWord)).map((r) => r.mode)),
    [autoRules, pianoWord],
  );
  const current = status?.mode?.current ?? null;
  const seenMode = useRef<string | null>(null);
  useEffect(() => {
    if (!current || !room.modes) return;
    if (seenMode.current === null) {
      seenMode.current = current; // the mode the page opened in: the folds stay as remembered
      return;
    }
    if (seenMode.current === current) return;
    seenMode.current = current;
    const shows = modeShows(room.modes[current]);
    setFold("look", shows.screens);
    setFold("lights", shows.lamps && !shows.screens);
    setFold("piano", pianoModes.has(current) || recorder.recording);
  }, [current, room.modes, pianoModes, recorder.recording]);
  const wasRecording = useRef(recorder.recording);
  useEffect(() => {
    if (recorder.recording && !wasRecording.current) setFold("piano", true);
    wasRecording.current = recorder.recording;
  }, [recorder.recording]);

  const screenCard = (id: string, scope?: LookScope | null, roomId?: string) => {
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
        room={roomId}
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
  // Each room of several screens: its live state (the card in Look, the scopes for its screens' cards in Screens).
  const liveSpecs: RoomLiveSpec[] = room.library
    ? sections
        .filter((sec) => sec.key !== "_other" && sec.screens.length > 1)
        .map((sec) => ({ room: { id: sec.key, name: sec.title }, members: liveMembers(sec.screens), total: sec.screens.length }))
    : [];

  return (
    <div className={cn(THEME_CLASS, "min-h-screen bg-background text-base text-foreground antialiased")}>
      <RoomThemeStyle />
      <div className="mx-auto w-full max-w-3xl px-4 pb-16 sm:px-6 lg:max-w-6xl">
        <Header
          link={link}
          onMenu={openMenu}
          screen={focusScreen && !unknownFocus ? focusScreen : null}
          toggles={status ? toggles : undefined}
          onDetails={openListening}
          pc={pc}
          line={status ? roomLine : null}
        />

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
              {/* one column up to 1024 px; from there two (owner, 25 Oct: "REASON THROUGH USE OF SPACE"): the modes and
                  Look on the left, everything else on the right, the player under both */}
              <RoomLives
                specs={liveSpecs}
                api={room.api}
                lib={room.library}
                subscribe={room.subscribeStream}
                addLog={room.addLog}
                reportError={room.reportError}
              >
                {(slots) => {
                  const cards = liveSpecs.map((sp) => slots[sp.room.id]?.card).filter(Boolean);
                  const byHand = liveSpecs.some((sp) => slots[sp.room.id]?.scenesByItself === false);
                  return (
                    <div className="lg:grid lg:grid-cols-2 lg:items-start lg:gap-x-8">
                      <div data-testid="room-col-modes">
                        <section id="modes" aria-label="Modes" className="scroll-mt-[calc(var(--app-top,0px)+0.5rem)]">
                          {/* Auto above the modes (owner, 24 Oct: "why wouldn't auto switch be ABOVE the various modes? it
                              determines them after all") */}
                          <div className="mb-2">
                            <AutoRow api={room.api} feed={show} addLog={room.addLog} onEdit={editRule} current={status.mode?.current} />
                          </div>
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
                            onEdit={editMode}
                            describe={describe}
                            subsystems={status.subsystems}
                            names={screenNames}
                            onChanged={() => {
                              void room.refreshMeta();
                              refreshDescribe();
                            }}
                          />
                        </section>
                        <div className="mt-5 border-t border-stone-200/80 lg:border-b">
                          <Section
                            id="look"
                            title="Look"
                            summary={
                              nothingFollows
                                ? "Nothing follows the look right now"
                                : [director.summary, byHand ? "scenes picked by you" : null].filter(Boolean).join(" · ")
                            }
                            defaultOpen={anyLive}
                            foot={
                              <>
                                <Link to={settingsUrl("look")} className={FOOT_LINK}>
                                  Look settings <ChevronRight className="h-4 w-4" aria-hidden />
                                </Link>
                                <a href={rendererConsoleUrl()} target="_blank" rel="noopener noreferrer" className={FOOT_LINK}>
                                  Scene library <ChevronRight className="h-4 w-4" aria-hidden />
                                </a>
                              </>
                            }
                          >
                            {/* the controls stay usable while nothing shows the look: a pick is kept for later */}
                            {/* the fold summary already says who is choosing / that nothing follows: here only what it adds */}
                            {(nothingFollows ? lookModes.length > 0 : !!director.line) && (
                              <p className="px-1 text-sm leading-snug text-muted-foreground" data-testid="look-who">
                                {nothingFollows ? `Shows in ${andList(lookModes)}` : director.line}
                              </p>
                            )}
                            <DirectorControls api={room.api} feed={show} addLog={room.addLog} />
                            {cards}
                          </Section>
                        </div>
                      </div>

                      <div className="divide-y divide-stone-200/80 border-y border-stone-200/80" data-testid="room-col-folds">
                          <Section
                            id="screens"
                            title="Screens"
                            summary={screensSummary}
                            forceOpen={focusScreen}
                            foot={
                              <Link to={settingsUrl("screens")} className={FOOT_LINK}>
                                Screen settings <ChevronRight className="h-4 w-4" aria-hidden />
                              </Link>
                            }
                          >
                            {focusScreen && !unknownFocus && (
                              <p className="px-1 text-[15px] leading-snug text-stone-700" data-testid="screens-opened-for">
                                Opened for {status.screens?.[focusScreen]?.name || friendlyName(focusScreen)} ·{" "}
                                <Link to={wholeRoomHref} className="font-medium text-teal-800 underline-offset-4 hover:underline">
                                  Whole room ›
                                </Link>
                              </p>
                            )}
                            {unknownFocus && (
                              <Notice tone="muted" className="text-base">
                                There's no screen called “{focusScreen}” in the room. Here are the ones it has.
                              </Notice>
                            )}
                            {sections.length > 0 ? (
                              screenGroups.map((sec) => (
                                <div key={sec.key} aria-labelledby={`room-sec-${sec.key}`} role="group">
                                  {screenGroups.length > 1 && (
                                    <h3 id={`room-sec-${sec.key}`} className={H3}>
                                      {sec.title}
                                    </h3>
                                  )}
                                  <div className="grid grid-cols-1 items-start gap-3 sm:grid-cols-2 lg:grid-cols-1">
                                    {sec.screens.map((id) =>
                                      screenCard(id, slots[sec.key]?.scopeFor(id) ?? null, sec.key === "_other" ? undefined : sec.key),
                                    )}
                                  </div>
                                </div>
                              ))
                            ) : screens.length === 0 ? (
                              <p className="text-base text-muted-foreground">The room has no screens set up.</p>
                            ) : (
                              <div className="grid grid-cols-1 items-start gap-3 sm:grid-cols-2 lg:grid-cols-1">{screens.map(([id]) => screenCard(id))}</div>
                            )}
                          </Section>

                        <TodayFold
                          rooms={todayRooms}
                          onChange={(what) => room.addLog({ type: "schedule", level: "info", summary: what })}
                        />

                        <Section
                          id="listening"
                          title="Listening"
                          summary={listeningWords(showCard, pianoLinked)}
                          foot={
                            <Link to={settingsUrl("listening")} className={FOOT_LINK}>
                              Listening settings <ChevronRight className="h-4 w-4" aria-hidden />
                            </Link>
                          }
                        >
                          <ListeningPanel api={room.api} feed={show} now={now} addLog={room.addLog} />
                        </Section>

                        <Section
                          id="lights"
                          title="Lights"
                          summary={lightsSummary}
                          hidden={sections.length > 0 && lightGroups.length === 0}
                          action={
                            // the setup page from the heading, not ~2,000 px down under the rows (owner, 24 Oct)
                            <Link
                              to="/lights"
                              className="inline-flex min-h-11 items-center gap-0.5 rounded-xl px-2 text-[15px] font-medium text-teal-800 hover:bg-stone-100/70 hover:underline"
                            >
                              Set up lights <ChevronRight className="h-4 w-4" aria-hidden />
                            </Link>
                          }
                          foot={
                            <Link to={settingsUrl("lights")} className={FOOT_LINK}>
                              Light settings <ChevronRight className="h-4 w-4" aria-hidden />
                            </Link>
                          }
                        >
                          {(lampIds === null || lampIds.length > 0) && <RoomLightsRow api={room.api} feed={show} addLog={room.addLog} />}
                          {sections.length > 0 ? (
                            lightGroups.map((sec) => (
                              <div key={sec.key} className="space-y-3">
                                {sec.lights && (
                                  <LightsSection
                                    api={room.api}
                                    connected={connected}
                                    addLog={room.addLog}
                                    reportError={room.reportError}
                                    fixtures={fixtures}
                                    only={sec.lights}
                                    title={sec.title}
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
                              </div>
                            ))
                          ) : (
                            <LightsSection
                              api={room.api}
                              connected={connected}
                              addLog={room.addLog}
                              reportError={room.reportError}
                              fixtures={fixtures}
                              title="Lights"
                              sub
                            />
                          )}
                        </Section>

                        {/* one Piano fold, always there (so /room#piano works): its keys and pedals, then Record and the
                            recordings; Record is in its heading too, one tap with the fold closed */}
                        <Section
                          id="piano"
                          title="Piano"
                          summary={pianoSummary}
                          defaultOpen={recorder.recording}
                          action={(open) => <RecordPill baseUrl={room.api.baseUrl} idle={!open} />}
                        >
                          <PianoRow api={room.api} onConnected={setPianoLinked} className={cn(CARD_CLASS, "px-3 py-2 sm:px-4")} />
                          {pianoLine === null && pianoLinked === null && (
                            <p className="px-1 text-base text-muted-foreground">Nothing has come from the piano yet.</p>
                          )}
                          <PianoCard
                            api={room.api}
                            connected={connected}
                            addLog={room.addLog}
                            reportError={room.reportError}
                            bare
                            onSummary={setPianoLine}
                          />
                        </Section>
                      </div>
                    </div>
                  );
                }}
              </RoomLives>

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
        <ListenHere api={room.api} />
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
        onRefresh={() => void room.refreshMeta()}
        log={room.log}
        statusCount={room.statusCount}
        onClearLog={room.clearLog}
      />

      <ModeEditor
        name={editing}
        initialSection={editAt}
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
