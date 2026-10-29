// How it's deciding (owner, 29 Oct: "surface more of the art director's reasoning within the rooms page during live
// modes too, so that it's possible to inspect whether it's behaving sensibly or not"). Read-only, in the Look fold
// under the director's controls. Everything is show_service's card `reasoning` (show_service/README.md S5.33):
//   - the song's plan in one line ("This song: house, euphoric: geometric and fire scenes, Rainbow colours"), and who
//     made it (the AI, or the director on its own);
//   - under "How it's deciding": what it goes on (Sonos's title, the piano's MIDI, the room mic, AI listening), what it
//     hears and where each word came from, the feel and how its looks change, the screens' favoured and rested scene
//     kinds, the colours, the lights' scene and why, and the latest decisions, newest first.
// Nothing here is a control, and no id or file name is shown: the words are show_service's own or written here.

import type { ReactNode } from "react";
import { Unroll } from "./bits";
import { useShowState, type ShowFeed } from "./MusicCard";
import type { ShowReasoning } from "./types";

/** Where a word came from, in page words (show_service's words_from values). */
const FROM: Record<string, string> = {
  human: "you",
  plan: "the plan",
  listener: "the AI",
  ml: "the local music model",
  bands: "how lively it is",
  vibe: "the feel",
  feel: "the feel",
};

function from(v: string | null | undefined): string | null {
  return v ? (FROM[v] ?? v) : null;
}

function clock(t: number): string {
  try {
    return new Date(t * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
  } catch {
    return "";
  }
}

function ago(at: number | null | undefined): string | null {
  if (at == null || !Number.isFinite(at)) return null;
  const s = Math.max(0, Date.now() / 1000 - at);
  if (s < 60) return "just now";
  const m = Math.round(s / 60);
  return m < 60 ? `${m} min ago` : `${Math.round(m / 60)} h ago`;
}

function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <p className="text-sm leading-snug">
      <span className="text-muted-foreground">{label} </span>
      <span className="text-stone-800">{children}</span>
    </p>
  );
}

export function heardWords(r: ShowReasoning | null | undefined): string | null {
  const h = r?.heard;
  if (!h) return null;
  const parts: string[] = [];
  if (h.genre) parts.push(from(h.genre_from) ? `${h.genre} (${from(h.genre_from)})` : h.genre);
  if (h.moods?.length) parts.push(from(h.moods_from) ? `${h.moods.join(", ")} (${from(h.moods_from)})` : h.moods.join(", "));
  if (h.band) parts.push(h.band);
  if (h.energy != null) parts.push(`energy ${Math.round(h.energy * 100)}%`);
  if (h.phase && h.phase !== "calm") parts.push(h.phase);
  if (h.tempo) parts.push(`${h.tempo} bpm`);
  return parts.length ? parts.join(" · ") : null;
}

/** The plan in one line, for the Look fold ("This song: …"); null without a plan. */
export function planLine(r: ShowReasoning | null | undefined): string | null {
  const p = r?.plan;
  if (!p?.why) return null;
  return p.song ? `${p.song}: ${p.why}` : p.why;
}

export function DirectorWhy({ feed }: { feed: ShowFeed }) {
  const { card } = useShowState(feed);
  const r = card?.reasoning;
  if (!r) return null;
  const p = r.plan;
  const line = planLine(r);
  const heard = heardWords(r);
  const lights = Object.entries(p?.lights ?? {}).map(([g, n]) => `${n} (${g})`);
  return (
    <div className="mt-3 space-y-1 px-1" data-testid="director-why">
      {line && (
        <p className="text-sm leading-snug" data-testid="director-why-plan">
          <span className="text-muted-foreground">This song </span>
          <span className="text-stone-800">{line}</span>
          <span className="text-muted-foreground">
            {" "}
            · {p?.by === "the AI" ? "planned by the AI" : "the director's own plan"}
            {ago(p?.at) ? `, ${ago(p?.at)}` : ""}
          </span>
        </p>
      )}
      <Unroll title="How it's deciding" testId="director-why-more">
        <div className="space-y-1.5 px-1 pb-1">
          {r.sources?.length ? <Row label="Goes on">{r.sources.join(" · ")}</Row> : <Row label="Goes on">nothing yet: no sound, no song</Row>}
          {heard && <Row label="Hears">{heard}</Row>}
          {r.heard?.character && <Row label="The AI hears">{r.heard.character}</Row>}
          {r.feel ? (
            <Row label="Feel">
              {[r.feel.label, r.feel.changes ? `looks change ${r.feel.changes}` : null, r.feel.beat, r.feel.peaks]
                .filter(Boolean)
                .join(" · ")}
            </Row>
          ) : (
            <Row label="Feel">none: the director picks the colours only</Row>
          )}
          {p && (p.favour?.length || p.rest?.length) ? (
            <Row label="Screens">
              {[p.favour?.length ? `favour ${p.favour.join(" and ")} scenes` : null, p.rest?.length ? `a rest from ${p.rest.join(", ")}` : null]
                .filter(Boolean)
                .join(" · ")}
            </Row>
          ) : null}
          {p?.palette && <Row label="Colours">{p.palette}</Row>}
          {(r.lights?.scene || lights.length > 0) && (
            <Row label="Lights">
              {r.lights?.scene ? `${r.lights.scene}${r.lights.why ? `: ${r.lights.why}` : ""}` : null}
              {lights.length > 0 ? `${r.lights?.scene ? " · " : ""}planned: ${lights.join(", ")}` : null}
            </Row>
          )}
          {p?.together && <Row label="Playing together">{p.together.replace(/-/g, " ")}</Row>}
          {p && (p.wildness != null || (p.taste ?? 0) !== 0) && (
            <Row label="How hard">
              {[
                p.wildness != null ? `${Math.round(p.wildness * 100)}% wild` : null,
                (p.taste ?? 0) > 0 ? "a little wilder: people asked for it before" : (p.taste ?? 0) < 0 ? "a little calmer: people asked for it before" : null,
              ]
                .filter(Boolean)
                .join(" · ")}
            </Row>
          )}
          {r.ai_error && <Row label="The AI's plan">didn't come ({r.ai_error}); the director's own stands</Row>}
          {r.recent?.length ? (
            <div className="pt-1" data-testid="director-why-recent">
              <p className="text-sm text-muted-foreground">Latest</p>
              <ul className="mt-0.5 space-y-0.5">
                {r.recent.map((x, i) => (
                  <li key={`${x.t}-${i}`} className="flex gap-2 text-sm leading-snug">
                    <span className="shrink-0 tabular-nums text-muted-foreground">{clock(x.t)}</span>
                    <span className="min-w-0 text-stone-800">{x.text}</span>
                  </li>
                ))}
              </ul>
            </div>
          ) : null}
        </div>
      </Unroll>
    </div>
  );
}
