// A room's live card (owner, 17 Oct: a control acts where it sits; the rooms are config, routes/data/rooms.json).
// In a room with more than one screen, the controls that are the room's live here, above its screens' cards, and act
// on every live screen in the room: the scene for all of them, Scenes and Effects each picked by itself or by you
// (with Next scene / Next effect), the room's picked effects, Change effect (with No effects and Add on top), How
// many, energy, Up next, and under an unroll what picking picks from and how often. A screen's own card keeps only
// that screen's: its scene by hand, its settings, its colours and feel, its own effects and their strength.
//
// It shows while any screen in the room is live; the screens' cards are drawn by `children`, given each one's scope.

import { useMemo, useState, type ReactNode } from "react";
import { Sparkles } from "lucide-react";
import { cn } from "@/lib/utils";
import type { RoomApi } from "./api";
import { Notice, Spinner, Unroll } from "./bits";
import { EffectChips, EffectPicker, EffectSheet, LookSettings, PickingRows, SceneButton, ScenePicker } from "./LivePanel";
import { cap, droppedWords, fxChips, fxLabel, ownWords, sceneInfo, type LookScope, type SceneInfo } from "./liveLib";
import { effectSlots, type LiveMember } from "./roomFx";
import { CARD_CLASS } from "./theme";
import type { Library } from "./types";
import { UpNext } from "./UpNext";
import type { NewLogEntry, StreamEvent } from "./useRoomConnection";
import { useRoomLive, type RoomLiveState } from "./useRoomLive";
import { useUpNext } from "./useUpNext";

export function RoomLive({
  room,
  members,
  total,
  api,
  lib,
  subscribe,
  addLog,
  reportError,
  children,
}: {
  room: { id: string; name: string };
  /** The room's live screens, in rooms.json order. */
  members: LiveMember[];
  /** How many screens the room has. */
  total: number;
  api: RoomApi;
  lib: Library | null;
  subscribe: (fn: (e: StreamEvent) => void) => () => void;
  addLog: (e: NewLogEntry) => void;
  reportError: (e: unknown) => void;
  children: (scopeFor: (screenId: string) => LookScope | null) => ReactNode;
}) {
  const rl = useRoomLive({ api, roomId: room.id, roomName: room.name, members, lib, subscribe, addLog, reportError });
  const { takeOffPickedRaw, swapPickedRaw, views } = rl;
  const key = members.map((m) => `${m.id}=${m.name}=${(views[m.id]?.own ?? []).join(",")}`).join(" ");
  const scopes = useMemo(() => {
    const out: Record<string, LookScope> = {};
    for (const m of members)
      out[m.id] = {
        hosted: true,
        shared: members.filter((o) => o.id !== m.id).map((o) => o.name),
        own: views[m.id]?.own ?? [],
        takeOffPicked: takeOffPickedRaw,
        swapPicked: swapPickedRaw,
      };
    return out;
    // members and views are rebuilt often; key is what the scopes say
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, takeOffPickedRaw, swapPickedRaw]);
  return (
    <>
      {members.length > 0 && <RoomLiveCard rl={rl} room={room} members={members} total={total} api={api} lib={lib} subscribe={subscribe} />}
      {children((id) => scopes[id] ?? null)}
    </>
  );
}

function RoomLiveCard({
  rl,
  room,
  members,
  total,
  api,
  lib,
  subscribe,
}: {
  rl: RoomLiveState;
  room: { id: string; name: string };
  members: LiveMember[];
  total: number;
  api: RoomApi;
  lib: Library | null;
  subscribe: (fn: (e: StreamEvent) => void) => () => void;
}) {
  const [scenePick, setScenePick] = useState(false);
  const [fxPick, setFxPick] = useState(false);
  const [sheetKey, setSheetKey] = useState<string | null>(null);
  const un = useUpNext({ api, sessionId: rl.lead?.session ?? null, subscribe });
  const look = rl.look;
  const chips = useMemo(() => fxChips(look, lib, { yours: false, picked: true }), [look, lib]);

  const n = members.length;
  const names = members.map((m) => m.name);
  const both = n >= 2 ? (n === 2 ? "both screens" : `all ${n} screens`) : null;
  const title = n === 1 ? names[0] : n === total && both ? cap(both) : names.join(" and ");
  const sub =
    n >= 2
      ? `Live together: a change here changes ${both}.`
      : total > 1
        ? `The only one live here just now. ${total === 2 ? "The other screen joins" : "The others join"} in when ${total === 2 ? "it goes" : "they go"} live.`
        : null;
  const max = effectSlots(lib);
  const fxOn = !!look?.auto?.fx?.enabled;
  const scenesNow = new Set(members.map((m) => rl.views[m.id]?.current_scene).filter(Boolean));
  const sceneNow: SceneInfo | null =
    scenesNow.size > 1 ? { key: "", label: "Each screen its own scene", thumb: null } : sceneInfo(lib, api, look?.current_scene ?? null);
  const picked = new Set(chips.filter((c) => c.kind === "picked" && c.preset).map((c) => c.preset as string));
  const sheetChip = chips.find((c) => c.key === sheetKey) ?? null;
  const changing = !!rl.busy?.startsWith("changing scene");
  // A screen that set parts for itself (from its own card) keeps them; the renderer gives a part back to the room when
  // the room changes that part.
  const ownLines = members.flatMap((m) => {
    const w = ownWords(rl.views[m.id]?.own);
    return w ? [`${m.name} keeps its own ${w}.`] : [];
  });

  return (
    <section aria-label={`${room.name}: live`} data-testid={`roomlive-${room.id}`} className={cn(CARD_CLASS, "space-y-3 border-teal-200 px-4 pb-4 pt-3")}>
      <div>
        <div className="text-[17px] font-semibold leading-snug">{title}</div>
        {sub && <p className="text-[15px] leading-snug text-muted-foreground">{sub}</p>}
        {ownLines.map((l) => (
          <p key={l} className="text-[15px] leading-snug text-muted-foreground">
            {l}
          </p>
        ))}
      </div>

      {!look ? (
        rl.readFailed ? (
          <Notice tone="warn">
            Can't read the screens' live settings right now.{" "}
            <button type="button" className="font-medium underline underline-offset-2" onClick={() => void rl.refresh()}>
              Try again
            </button>
          </Notice>
        ) : (
          <p className="flex items-center gap-2 text-[15px] text-muted-foreground">
            <Spinner /> Reading the screens' live settings…
          </p>
        )
      ) : (
        <>
          <SceneButton
            info={sceneNow}
            caption={changing ? "Changing scene…" : both ? `Change scene · ${both}` : "Change scene"}
            busy={changing}
            onClick={() => setScenePick(true)}
            testId={`roomscene-${room.id}`}
          />

          <div className="space-y-3 border-t border-stone-200/80 pt-3">
            <PickingRows
              id={room.id}
              look={look}
              lib={lib}
              plan={un.read}
              picks={rl.picks}
              anyOn={rl.anyOn}
              where={both}
              onNextScene={() => void rl.nextScene()}
              onNextEffect={() => void rl.nextEffect()}
              onScenePicking={(c) => void rl.setScenePicking(c)}
              onFxPicking={(c) => void rl.setFxPicking(c)}
              onDirection={(label, d) => void rl.setDirection(label, d)}
              onHowMany={(k) => void rl.setHowMany(k)}
            >
              <EffectChips
                id={room.id}
                chips={chips}
                where={both}
                onOpen={(c) => setSheetKey(c.key)}
                trailing={
                  <button
                    type="button"
                    onClick={() => setFxPick(true)}
                    data-testid={`roomfx-${room.id}`}
                    className="inline-flex h-11 items-center gap-1.5 rounded-full border border-dashed border-stone-400 bg-white px-4 text-base hover:bg-stone-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                  >
                    <Sparkles className="h-4 w-4" aria-hidden /> Change effect
                  </button>
                }
              />
            </PickingRows>
          </div>

          {rl.lead && (
            <UpNext screenId={rl.lead.id} un={un} lib={lib} api={api} autoOn={!!look.auto?.enabled} fxAutoOn={fxOn} sharedLine={false} />
          )}

          {rl.busy && !changing && (
            <p className="flex items-center gap-2 text-[15px] text-muted-foreground">
              <Spinner /> {cap(rl.busy)}…
            </p>
          )}
          {rl.error && <Notice tone="bad">{rl.error}</Notice>}
          {rl.info && <Notice tone="muted">{rl.info}</Notice>}

          {rl.lead && (
            <Unroll title="What it picks from, and how often" testId={`roommore-${room.id}`}>
              <LookSettings
                screenId={rl.lead.id}
                lv={rl}
                lib={lib}
                api={api}
                parts="picking"
                words={both ? `Changes here go to ${both}.` : null}
              />
            </Unroll>
          )}

          <ScenePicker
            open={scenePick}
            onOpenChange={setScenePick}
            lib={lib}
            api={api}
            mode="cue"
            currentKey={scenesNow.size > 1 ? null : sceneNow?.key ?? null}
            description={both ? `Tap one to switch ${both} to it now.` : undefined}
            onPick={(key, how) => void rl.changeScene(key, sceneInfo(lib, api, key)?.label ?? key, how)}
            testId={`roomscenepicker-${room.id}`}
          />
          <EffectPicker
            open={fxPick}
            onOpenChange={setFxPick}
            lib={lib}
            api={api}
            mode="change"
            title="Change effect"
            description={
              `Tap one to put it on now, in place of the one it picked${both ? `, on ${both}` : ""}.` +
              (fxOn ? " It moves on at its next change. Turn Pick effects by itself off to keep it." : "")
            }
            selected={picked}
            onPick={(key, label, onTop) => {
              if (picked.has(key)) void rl.takeOffPicked(key, label);
              else if (onTop) void rl.addOnTop(key, label, max, (d) => droppedWords(max, fxLabel(d, lib)));
              else void rl.changeEffect(key, label);
            }}
            onNone={() => void rl.noEffects()}
            noneSub={both ? "On every screen here" : "On this screen"}
            onTopHint={`Keeps what's on and adds this one${both ? ` to ${both}` : ""}, as yours.`}
            testId={`roomfxpicker-${room.id}`}
          />
          <EffectSheet
            chip={sheetChip}
            onClose={() => setSheetKey(null)}
            fxOn={fxOn}
            where={both}
            testId={`roomfxsheet-${room.id}`}
            onTakeOff={(c) => void rl.takeOffPicked(c.preset ?? c.key, c.label)}
            onSwap={(c) => (c.preset ? void rl.swapPicked(c.preset, c.label) : void rl.nextEffect())}
            onNextScene={() => void rl.nextScene()}
          />
        </>
      )}
    </section>
  );
}
