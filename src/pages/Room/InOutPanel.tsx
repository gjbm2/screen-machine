// What is coming in and what is going out, always on view at the top of the Room page (owner, 25 Oct: "you have hidden
// basic diagnostic insights: what inputs are coming in, what is current output state"; after a dead piano USB link
// took 20 minutes to find). In: the piano (is it sending notes, and how recently), each room mic (live level, or off),
// the music player. Out: each screen (live or art, with its own live page one tap away), the lamps, the music.
// Reads GET /show/api/v1/inputs (a few hundred bytes) every 2 s while the page is visible; nothing else polls here.

import { useEffect, useState } from "react";
import { ChevronDown, ExternalLink } from "lucide-react";
import { displayUrl } from "@/components/nav/appNav";
import { cn } from "@/lib/utils";
import { openFold } from "./Section";
import type { RoomApi } from "./api";

const POLL_MS = 2000;
const OPEN_KEY = "sm_room_inout_open";

interface InputsBody {
  inputs?: { stream: string; label?: string; state?: string; device?: string | null; rms_dbfs?: number | null; silent?: boolean | null }[];
  pianos?: { stream: string; label?: string; state?: string }[];
  piano?: { present?: boolean; keys_down?: number; last_note_age_s?: number | null; notes_per_s?: number } | null;
}

export interface OutScreen {
  id: string;
  name: string;
  /** "Live · Gallery", "Showing art", ... (the screen card's own words) */
  text: string;
  live: boolean;
}

function ago(s: number): string {
  if (s < 2) return "now";
  if (s < 60) return `${Math.round(s)} s ago`;
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  return `${Math.round(s / 3600)} h ago`;
}

function Dot({ tone }: { tone: "on" | "idle" | "off" | "bad" }) {
  return (
    <span
      aria-hidden
      className={cn(
        "mt-[7px] h-2.5 w-2.5 shrink-0 rounded-full",
        tone === "on" ? "bg-teal-500" : tone === "idle" ? "bg-teal-800/50" : tone === "bad" ? "bg-rose-600" : "bg-stone-300",
      )}
    />
  );
}

export function InOutPanel({
  api,
  screens,
  lights,
  music,
}: {
  api: RoomApi;
  screens: OutScreen[];
  lights: string;
  music: string;
}) {
  const [body, setBody] = useState<InputsBody | null>(null);
  const [down, setDown] = useState(false);
  // closed by default: one line of dots; open shows the words (owner, 25 Oct: "on mobile it takes up MOST OF A SCREEN
  // HEIGHT"). Remembered on this device.
  const [open, setOpenState] = useState<boolean>(() => {
    try {
      return localStorage.getItem(OPEN_KEY) === "1";
    } catch {
      return false;
    }
  });
  const setOpen = (v: boolean) => {
    setOpenState(v);
    try {
      localStorage.setItem(OPEN_KEY, v ? "1" : "0");
    } catch {
      /* not remembered */
    }
  };

  useEffect(() => {
    let stop = false;
    let timer: number | undefined;
    const load = async () => {
      if (document.visibilityState === "visible") {
        try {
          const b = (await api.showInputs()) as InputsBody;
          if (!stop) {
            setBody(b);
            setDown(false);
          }
        } catch {
          if (!stop) setDown(true);
        }
      }
      if (!stop) timer = window.setTimeout(load, POLL_MS);
    };
    void load();
    return () => {
      stop = true;
      window.clearTimeout(timer);
    };
  }, [api]);

  // ── In ──
  const pianoLink = body?.pianos?.[0];
  const pv = body?.piano ?? null;
  const age = typeof pv?.last_note_age_s === "number" ? pv.last_note_age_s : null;
  const pianoConnected = !!pianoLink && !["down", "off"].includes(pianoLink.state ?? "");
  const pianoTone = down || !pianoConnected ? "bad" : age !== null && age < 5 ? "on" : "idle";
  const pianoText = down
    ? "the music side isn't answering"
    : !pianoLink
      ? "no piano set up"
      : !pianoConnected
        ? `relay off (${pianoLink.label || "mini-PC"})`
        : age === null
          ? "relay on · no notes yet"
          : age < 5
            ? `notes arriving${pv?.notes_per_s ? ` · ${pv.notes_per_s.toFixed(1)}/s` : ""}${pv?.keys_down ? ` · ${pv.keys_down} down` : ""}`
            : `relay on · no notes for ${ago(age).replace(" ago", "")}`;
  const mics = (body?.inputs ?? []).map((m) => {
    const level = typeof m.rms_dbfs === "number" ? m.rms_dbfs : null;
    const on = m.state === "streaming";
    return {
      key: m.stream,
      name: (m.device && m.device !== "none" ? m.device : null) || m.label || m.stream,
      tone: (on ? (level !== null && level > -50 ? "on" : "idle") : m.state === "off" ? "off" : "bad") as "on" | "idle" | "off" | "bad",
      text: on ? (level !== null ? `${Math.round(level)} dB` : "on") : m.state === "off" ? "off" : m.state || "down",
    };
  });

  const row = "flex min-w-0 items-start gap-2 text-[15px] leading-snug";
  const chip = (tone: "on" | "idle" | "off" | "bad", label: string, key: string) => (
    <span key={key} className="inline-flex items-center gap-1 whitespace-nowrap">
      <span
        aria-hidden
        className={cn(
          "h-2 w-2 rounded-full",
          tone === "on" ? "bg-teal-500" : tone === "idle" ? "bg-teal-800/50" : tone === "bad" ? "bg-rose-600" : "bg-stone-300",
        )}
      />
      {label}
    </span>
  );
  const head = (
    <button
      type="button"
      onClick={() => setOpen(!open)}
      aria-expanded={open}
      className="flex min-h-11 w-full items-center gap-2 text-left text-sm text-stone-700"
      data-testid="in-out-toggle"
    >
      <span className="flex min-w-0 flex-1 flex-wrap items-center gap-x-3 gap-y-0.5">
        <span className="font-semibold text-stone-500">In</span>
        {chip(pianoTone, "Piano", "p")}
        {mics.filter((m) => m.tone !== "off").map((m) => chip(m.tone, "Mic", m.key))}
        <span className="font-semibold text-stone-500">Out</span>
        {screens.map((sc) => chip(sc.live ? "on" : "idle", sc.name, sc.id))}
      </span>
      <ChevronDown aria-hidden className={cn("h-5 w-5 shrink-0 text-stone-500 transition-transform", open && "rotate-180")} />
    </button>
  );
  if (!open) {
    return (
      <div className="rounded-2xl border border-stone-200 bg-white/70 px-3" data-testid="in-out">
        {head}
      </div>
    );
  }
  return (
    <div className="rounded-2xl border border-stone-200 bg-white/70 px-3 pb-3" data-testid="in-out">
      {head}
    <div className="grid gap-3 px-1 sm:grid-cols-2">
      <button
        type="button"
        onClick={() => openFold("listening")}
        className="min-w-0 space-y-1 text-left"
        title="Details: Listening"
        data-testid="in-line"
      >
        <p className={row}>
          <Dot tone={pianoTone} />
          <span className="min-w-0">
            <span className="font-medium">Piano</span> · {pianoText}
          </span>
        </p>
        {mics.map((m) => (
          <p key={m.key} className={row}>
            <Dot tone={m.tone} />
            <span className="min-w-0 truncate">
              <span className="font-medium">Mic {m.name}</span> · {m.text}
            </span>
          </p>
        ))}
      </button>
      <div className="min-w-0 space-y-1">
        {screens.map((s) => (
          <a
            key={s.id}
            href={displayUrl(s.id)}
            target="_blank"
            rel="noreferrer"
            className={cn(row, "group hover:text-teal-800")}
            title={`Open what ${s.name} is showing, live`}
            data-testid={`out-${s.id}`}
          >
            <Dot tone={s.live ? "on" : "idle"} />
            <span className="min-w-0 flex-1 truncate">
              <span className="font-medium">{s.name}</span> · {s.text}
            </span>
            <ExternalLink className="mt-[3px] h-4 w-4 shrink-0 text-stone-500 group-hover:text-teal-800" aria-hidden />
          </a>
        ))}
        <p className={row}>
          <Dot tone="idle" />
          <span className="min-w-0 truncate">
            <span className="font-medium">Lights</span> · {lights}
          </span>
        </p>
        <p className={row}>
          <Dot tone={/^(playing|recording)/.test(music) ? "on" : "idle"} />
          <span className="min-w-0 truncate">
            <span className="font-medium">Music</span> · {music}
          </span>
        </p>
      </div>
    </div>
    </div>
  );
}
