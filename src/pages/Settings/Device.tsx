// Settings › This device (#device): what this browser keeps for itself. Its name (other devices' Play on lists show
// it: "Greg's iPad", not "iPad"), the page the app opens on (/start), and the choices it remembers, each shown as it
// is with a Reset (they're made where they're used: the piano sound on its chips, the score view on the score, Plays
// here in Play on). "Reset this device's choices" clears them all, after a Confirm.

import { useEffect, useMemo, useState } from "react";
import { Smartphone } from "lucide-react";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { storageGet, storageSet, type RoomApi } from "@/pages/Room/api";
import { deviceName, fetchDevices, guessDeviceName, myOutput, registerDevice } from "@/pages/Room/listenStream";
import { THEME_CLASS } from "@/pages/Room/theme";
import type { ShowTestView } from "@/pages/Room/types";
import { clearKeys, DEVICE_NAME_KEY, foldKeys, HOME_KEY, KEYS, readHome, reloadApp, writeHome, type Home } from "./deviceStore";
import { Chips, Group, Hint, Row, Sub } from "./Group";

const SMALL = "h-10 rounded-xl bg-white px-4 text-[15px]";

function ResetRow({ label, value, keys, testId }: { label: string; value: string; keys: readonly string[]; testId: string }) {
  const set = keys.some((k) => storageGet(k) !== null);
  return (
    <Row label={label} hint={value} testId={testId}>
      {set ? (
        <Button
          variant="outline"
          className={SMALL}
          onClick={() => {
            clearKeys(keys);
            reloadApp();
          }}
        >
          Reset
        </Button>
      ) : (
        <span className="text-sm text-muted-foreground">As it came</span>
      )}
    </Row>
  );
}

const pct = (v: string | null) => {
  const x = Number(v);
  return Number.isFinite(x) && x > 0 ? `${Math.round(x * 100)}%` : null;
};

export function DeviceGroup({ api, showTest }: { api: RoomApi; showTest: ShowTestView | null }) {
  const guess = useMemo(() => guessDeviceName(), []);
  const [name, setName] = useState(() => storageGet(DEVICE_NAME_KEY) ?? "");
  const [savedName, setSavedName] = useState(name);
  const [nameNote, setNameNote] = useState<string | null>(null);
  const [home, setHome] = useState<Home | null>(() => readHome());
  const [confirm, setConfirm] = useState(false);

  useEffect(() => setNameNote(null), [name]);

  const saveName = async () => {
    const v = name.trim().replace(/\s+/g, " ").slice(0, 40);
    storageSet(DEVICE_NAME_KEY, v || null);
    setName(v);
    setSavedName(v);
    // A device the room already knows (it has been picked in Play on) takes the new name now; one it doesn't know isn't
    // added to the room's list just by being named.
    const view = await fetchDevices(undefined, api.baseUrl);
    const known = !!view?.devices.some((d) => d.id === myOutput());
    const told = known ? await registerDevice(null, api.baseUrl) : false;
    setNameNote(
      known
        ? told
          ? `Play on lists it as “${deviceName()}” now.`
          : "Saved here. The room didn't answer, so its Play on list catches up next time this device plays."
        : `Saved. Play on will list it as “${deviceName()}”.`,
    );
  };

  // what's remembered, in words
  const listenOn = storageGet(KEYS.playsHere[0]) === "1";
  const also = storageGet(KEYS.playsHere[1]) === "1";
  const soundId = storageGet(KEYS.piano[0]);
  const soundLabel = soundId ? showTest?.instruments?.find((i) => i.id === soundId)?.label ?? soundId : null;
  const speed = pct(storageGet(KEYS.piano[2]));
  const via = storageGet(KEYS.scenes[0]);
  const beat = storageGet(KEYS.scenes[1]);
  const fxAdd = storageGet(KEYS.scenes[2]);
  const scoreMode = storageGet(KEYS.score[0]);
  const staff = storageGet(KEYS.score[1]);
  const paper = storageGet(KEYS.score[2]);
  const folds = foldKeys();
  const tabsSet = KEYS.tabs.filter((k) => storageGet(k) !== null).length;
  const all = [
    DEVICE_NAME_KEY,
    HOME_KEY,
    ...KEYS.playsHere,
    ...KEYS.piano,
    ...KEYS.scenes,
    ...KEYS.score,
    ...KEYS.tabs,
    ...folds,
  ];

  const nameWords = savedName ? `“${savedName}”` : `“${guess}” (from the browser)`;

  return (
    <Group id="device" title="This device" icon={<Smartphone />} summary={`${savedName || guess} · opens on ${home === "/music" ? "Music" : "Room"}`}>
      <Row
        label="Name this device"
        hint={`Other phones and tablets see this name in Play on. Now ${nameWords}.`}
        stack
        testId="settings-device-name"
      >
        <form
          className="flex flex-wrap gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            void saveName();
          }}
        >
          <Input
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder={guess}
            maxLength={40}
            aria-label="This device's name"
            className="h-11 min-w-0 flex-1 basis-48 rounded-xl bg-white text-base"
            autoCapitalize="words"
          />
          <Button type="submit" disabled={name.trim() === savedName} className="h-11 rounded-xl px-5 text-base">
            Save
          </Button>
        </form>
        {nameNote && <p className="mt-1 text-sm text-muted-foreground">{nameNote}</p>}
      </Row>

      <Row label="Open the app on" hint="Where the app starts when it opens from its own start link (/start)." stack>
        <Chips<Home>
          label="Open the app on"
          value={home ?? "/room"}
          options={[
            { value: "/room", label: "Room" },
            { value: "/music", label: "Music" },
          ]}
          onChange={(h) => {
            writeHome(h);
            setHome(h);
          }}
          testId="settings-device-home"
        />
      </Row>

      <Sub>Remembered on this device</Sub>
      <Hint>Each is chosen where it's used. Reset puts it back as it came and reloads the app.</Hint>
      <ResetRow
        label="Plays the music here"
        value={`${listenOn ? "On" : "Off"}${also ? " · also plays whatever the room plays" : ""} · change it in Play on`}
        keys={KEYS.playsHere}
        testId="settings-device-plays"
      />
      <ResetRow
        label="Piano sound"
        value={[soundLabel ?? "The room's usual sound", speed ? `speed ${speed}` : null].filter(Boolean).join(" · ")}
        keys={KEYS.piano}
        testId="settings-device-piano"
      />
      <ResetRow
        label="Changing scene"
        value={[
          via && via !== "__default" ? `transition: ${via}` : "the room's transition",
          beat === "1" ? "on the beat" : null,
          fxAdd === "1" ? "effects add on top" : null,
        ]
          .filter(Boolean)
          .join(" · ")}
        keys={KEYS.scenes}
        testId="settings-device-scenes"
      />
      <ResetRow
        label="Score view"
        value={[scoreMode === "pages" ? "Pages" : "Scroll", staff ? `notes ${staff} px` : null, paper ? `paper ${paper}` : null]
          .filter(Boolean)
          .join(" · ")}
        keys={KEYS.score}
        testId="settings-device-score"
      />
      <ResetRow
        label="Open folds and tabs"
        value={folds.length + tabsSet ? `${folds.length + tabsSet} remembered` : "None remembered"}
        keys={[...folds, ...KEYS.tabs]}
        testId="settings-device-folds"
      />

      <div className="pt-1">
        <Button variant="outline" className="h-11 rounded-xl bg-white px-5 text-base" onClick={() => setConfirm(true)} data-testid="settings-device-reset">
          Reset this device's choices
        </Button>
      </div>

      <AlertDialog open={confirm} onOpenChange={setConfirm}>
        <AlertDialogContent className={THEME_CLASS}>
          <AlertDialogHeader>
            <AlertDialogTitle>Reset this device's choices?</AlertDialogTitle>
            <AlertDialogDescription>
              Its name, where the app opens, Plays here, the piano sound, the score view and the open folds go back as they came. The room
              itself doesn't change.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              onClick={() => {
                clearKeys(all);
                reloadApp();
              }}
            >
              Reset
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </Group>
  );
}
