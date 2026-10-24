// Settings › Listening & AI (#listening): which mic the room listens through (the Room page's MicPicker, by import:
// PUT /show/api/v1/input), what the mic is doing now, and the AI listener's settings as they are (GET
// /show/api/v1/listening: who it sends to, the daily budget, how often it listens, what it has cost today), read only
// for now. Mic and AI on or off are the switches at the top of the Room and Music pages, not here.

import { useEffect, useState } from "react";
import { Mic } from "lucide-react";
import type { RoomApi } from "@/pages/Room/api";
import { MicPicker } from "@/pages/Room/MicPicker";
import type { ShowMicState } from "@/pages/Room/types";
import { Group, Hint, LinkRow, ReadRow, Row, Sub } from "./Group";

interface ListeningView {
  provider?: string | null;
  model?: string | null;
  budget_usd?: number | null;
  spend_today_usd?: number | null;
  calls_today?: number | null;
  cadence_s?: number | null;
  est_evening_usd?: number | null;
  estimate?: { fast_cadence_s?: number | null; steady_cadence_s?: number | null; clip_s?: number | null; evening_h?: number | null } | null;
}

const usd = (v: number | null | undefined) => (typeof v === "number" ? `$${v < 10 ? v.toFixed(2) : Math.round(v)}` : null);
const secs = (v: number | null | undefined) => (typeof v === "number" ? `every ${Math.round(v)} s` : null);

export function ListeningGroup({ api }: { api: RoomApi }) {
  return (
    <Group id="listening" title="Listening & AI" icon={<Mic />} summary="Which mic the room uses · the AI listener">
      <ListeningBody api={api} />
    </Group>
  );
}

/** Mounted only while the group is open, so a closed group costs no requests. */
function ListeningBody({ api }: { api: RoomApi }) {
  const [ai, setAi] = useState<ListeningView | null>(null);
  const [mic, setMic] = useState<ShowMicState | null>(null);
  useEffect(() => {
    let alive = true;
    const read = () => {
      fetch(`${api.baseUrl}/show/api/v1/listening`, { cache: "no-store" })
        .then((r) => (r.ok ? r.json() : null))
        .then((v) => alive && setAi(v))
        .catch(() => alive && setAi(null));
      api
        .showMic()
        .then((m) => alive && setMic(m))
        .catch(() => alive && setMic(null));
    };
    read();
    const id = window.setInterval(read, 15_000);
    return () => {
      alive = false;
      window.clearInterval(id);
    };
  }, [api]);

  const steady = ai?.estimate?.steady_cadence_s ?? ai?.cadence_s;
  const fast = ai?.estimate?.fast_cadence_s;

  return (
    <>
      <Hint>Mic and AI on or off are the switches at the top of the Room and Music pages.</Hint>

      <Sub>Mic</Sub>
      <Row label="The room listens through" stack testId="settings-mic-input">
        <MicPicker api={api} />
      </Row>
      {mic && <ReadRow label="Now" value={mic.why || (mic.on ? "On" : "Off")} />}

      <Sub>AI listening</Sub>
      {ai ? (
        <div className="space-y-1.5" data-testid="settings-ai">
          {ai.provider && <ReadRow label="Sends clips to" value={[ai.provider, ai.model].filter(Boolean).join(" · ")} />}
          {usd(ai.budget_usd) && <ReadRow label="Spends at most" value={`${usd(ai.budget_usd)} a day`} />}
          {usd(ai.spend_today_usd) && (
            <ReadRow label="Spent today" value={`${usd(ai.spend_today_usd)}${typeof ai.calls_today === "number" ? ` · ${ai.calls_today} clips` : ""}`} />
          )}
          {secs(steady) && <ReadRow label="Listens" value={`${secs(steady)}${fast ? `, ${secs(fast)} when the music changes` : ""}`} />}
          {usd(ai.est_evening_usd) && (
            <ReadRow label="An evening costs about" value={`${usd(ai.est_evening_usd)}${ai.estimate?.evening_h ? ` (${ai.estimate.evening_h} h)` : ""}`} />
          )}
          <Hint>Read only here for now.</Hint>
        </div>
      ) : (
        <Hint>Not read yet: the music side may be starting.</Hint>
      )}

      <LinkRow href="/room#listening">What it hears, on the Room page</LinkRow>
    </>
  );
}
