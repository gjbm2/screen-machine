// /settings: everything set occasionally or once (owner, 24 Oct: "where are settings? isn't that a standard thing to
// expose in sensible places?"; the plan is ia.txt §2). One page of folds, in a fixed order, each its own file:
//   This device · Room & modes · Screens (one fold per screen) · Look · Lights · Listening & AI · Music & piano ·
//   Schedules · Connection · Developer
// Each fold can be linked to as /settings#<group> (#screen-<id> for one screen): the Room page's folds end with a
// "… settings ›" link to theirs, and the menu has a Settings row. With no hash every fold starts closed.
//
// Daily controls stay on the operating pages (each switch once, next to what it changes); this page holds the rest,
// with plain controls and no JSON. It writes only through the endpoints that already exist (the room's config PUT,
// show_service's mic input, the light-sensor curve, a screen's brightness override); anything without one is shown as
// it is, read only. It's a sub-page: one back link (to where you came from, else the Room page), which works in the
// iPad Home Screen app too. Open off the premises, like /music and /lights.
//
// It talks to room_service like the Room page (the same address rules: ?room=, the remembered one, same-origin /rs over
// HTTPS) and keeps its own connection (status, modes, the library and the events socket).

import { useCallback, useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { AppMenu } from "@/components/nav/AppMenu";
import { PageHeader } from "@/components/nav/PageHeader";
import { cn } from "@/lib/utils";
import { getRawPublishDestinations } from "@/shared/api/admin";
import { resolveRoomUrl, storageSet, URL_KEY } from "@/pages/Room/api";
import { isConnected, linkState } from "@/pages/Room/connection";
import { ModeEditor } from "@/pages/Room/ModeEditor";
import { buildTagIndex } from "@/pages/Room/sceneTags";
import { RoomThemeStyle, THEME_CLASS } from "@/pages/Room/theme";
import type { ShowTestView } from "@/pages/Room/types";
import { useDescribe } from "@/pages/Room/useDescribe";
import { useRoomConnection } from "@/pages/Room/useRoomConnection";
import { ConnectionGroup } from "./Connection";
import { DeveloperGroup } from "./Developer";
import { DeviceGroup } from "./Device";
import { LightsGroup } from "./Lights";
import { ListeningGroup } from "./Listening";
import { LookGroup } from "./Look";
import { ModesGroup } from "./Modes";
import { MusicGroup } from "./Music";
import { SchedulesGroup } from "./Schedules";
import { ScreensGroup, type Destination } from "./Screens";

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

export default function SettingsPage() {
  usePageTitle("Settings · screen/machine");
  const [searchParams, setSearchParams] = useSearchParams();
  const [baseUrl, setBaseUrl] = useState(() => resolveRoomUrl(window.location.search));
  const room = useRoomConnection(baseUrl, "");
  const { conn, status } = room;
  const now = useNow(1000);
  const connected = isConnected(conn, now);
  const link = linkState(conn, now);
  const [menuOpen, setMenuOpen] = useState(false);
  const [editing, setEditing] = useState<string | null>(null);

  const { describe, refresh: refreshDescribe } = useDescribe(room.api, status?.config?.revision, !!status);
  const tagIndex = useMemo(() => buildTagIndex(room.catalog), [room.catalog]);
  const names = useMemo(
    () =>
      Object.fromEntries(
        Object.entries(status?.screens ?? {}).flatMap(([id, sc]) => (sc.name ? [[`screens.${id}`, sc.name]] : [])),
      ) as Record<string, string>,
    [status?.screens],
  );

  // read once: the piano's sounds and places (This device, Music & piano), and the screens' site config
  const [showTest, setShowTest] = useState<ShowTestView | null>(null);
  const [showRead, setShowRead] = useState(false);
  useEffect(() => {
    const ac = new AbortController();
    room.api
      .showTest(ac.signal)
      .then((v) => setShowTest(v))
      .catch(() => setShowTest(null))
      .finally(() => !ac.signal.aborted && setShowRead(true));
    return () => ac.abort();
  }, [room.api]);
  const [dests, setDests] = useState<Destination[] | null>(null);
  useEffect(() => {
    let alive = true;
    getRawPublishDestinations()
      .then((d) => alive && setDests(Array.isArray(d) ? (d as Destination[]) : []))
      .catch(() => alive && setDests([]));
    return () => {
      alive = false;
    };
  }, []);

  const changeUrl = useCallback(
    (url: string | null) => {
      storageSet(URL_KEY, url);
      const next = new URLSearchParams(searchParams);
      if (url) next.set("room", url);
      else next.delete("room");
      setSearchParams(next, { replace: true });
      setBaseUrl(resolveRoomUrl(next.toString() ? `?${next.toString()}` : ""));
    },
    [searchParams, setSearchParams],
  );

  const onChanged = useCallback(() => {
    void room.refreshMeta();
    refreshDescribe();
  }, [room, refreshDescribe]);

  const liveNow = Object.values(status?.screens ?? {}).some((sc) => sc.observed?.phase === "live" || sc.observed?.layer === "live");
  const shared = { api: room.api, lib: room.library, subscribe: room.subscribeStream, addLog: room.addLog, reportError: room.reportError };

  return (
    <div className={cn(THEME_CLASS, "min-h-screen bg-background text-base text-foreground antialiased")}>
      <RoomThemeStyle />
      <div className="mx-auto w-full max-w-3xl px-4 pb-16 sm:px-6">
        <PageHeader
          title="Settings"
          current="settings"
          onMenu={() => setMenuOpen(true)}
          back={{ to: "/room" }}
          status={{ tone: link.tone, word: link.word, sentence: link.sentence, pulse: link.pulse }}
        />

        <main className="divide-y divide-stone-200/80 border-y border-stone-200/80 mt-2" data-testid="settings-page">
          <DeviceGroup api={room.api} showTest={showTest} />
          <ModesGroup
            api={room.api}
            status={status}
            modes={room.modes}
            describe={describe}
            names={names}
            onEdit={setEditing}
            onChanged={onChanged}
            addLog={room.addLog}
          />
          <ScreensGroup status={status} dests={dests} {...shared} />
          <LookGroup status={status} {...shared} />
          <LightsGroup api={room.api} status={status} />
          <ListeningGroup api={room.api} />
          <MusicGroup api={room.api} showTest={showTest} />
          <SchedulesGroup dests={dests} />
          <ConnectionGroup
            baseUrl={baseUrl}
            onChangeUrl={changeUrl}
            conn={conn}
            link={link}
            now={now}
            status={status}
            showAnswering={showRead ? showTest !== null : null}
            modes={room.modes}
            transition={room.transition}
            log={room.log}
            statusCount={room.statusCount}
            onClearLog={room.clearLog}
            onRefresh={() => void room.refreshMeta()}
          />
          <DeveloperGroup api={room.api} connected={connected} liveNow={liveNow} addLog={room.addLog} reportError={room.reportError} />
        </main>
      </div>

      <AppMenu open={menuOpen} onOpenChange={setMenuOpen} current="settings" />

      <ModeEditor
        name={editing}
        onClose={() => setEditing(null)}
        api={room.api}
        describe={describe}
        status={status}
        tagIndex={tagIndex}
        connected={connected}
        onSaved={onChanged}
        addLog={room.addLog}
        names={names}
      />
    </div>
  );
}
