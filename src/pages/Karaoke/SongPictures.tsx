// The playing song's artist pictures (owner, 26 Nov: the photo scenes, from pictures "found, vetted and kept" per
// artist; routes/karaoke/pictures.py). One control: hide a picture (for every song by that artist), with undo.

import { useEffect, useState } from "react";
import { X } from "lucide-react";
import { Spinner, Unroll } from "@/pages/Room/bits";
import { karaoke, type SongPictures as Pictures } from "@/shared/api/karaoke";

const POLL_MS = 15000;

export function SongPictures({ songId }: { songId: string }) {
  const [got, setGot] = useState<Pictures | null>(null);
  const [hidden, setHidden] = useState<string[]>([]);
  const [err, setErr] = useState(false);

  useEffect(() => {
    let dead = false;
    let timer = 0;
    setGot(null);
    setHidden([]);
    const load = () =>
      karaoke
        .pictures(songId)
        .then((r) => {
          if (dead) return;
          setGot(r);
          setErr(false);
          if (r.state === "fetching") timer = window.setTimeout(load, POLL_MS);
        })
        .catch(() => !dead && setErr(true));
    load();
    return () => {
      dead = true;
      window.clearTimeout(timer);
    };
  }, [songId]);

  const hide = (id: string, on: boolean) => {
    setHidden((h) => (on ? [...h, id] : h.filter((x) => x !== id)));
    karaoke.hidePicture(id, on).catch(() => setHidden((h) => (on ? h.filter((x) => x !== id) : [...h, id])));
  };

  const shown = (got?.pictures ?? []).filter((p) => !hidden.includes(p.id));
  const who = got?.artists.join(", ") || "the artist";
  const hint =
    got === null
      ? undefined
      : got.state === "fetching"
        ? "finding them…"
        : `${shown.length} of ${who}`;

  return (
    <Unroll title="Pictures for the screens" hint={hint} testId="karaoke-pictures">
      {err && <p className="text-sm text-muted-foreground">Couldn't read the pictures.</p>}
      {!err && got === null && (
        <div className="flex items-center gap-2 text-sm text-muted-foreground">
          <Spinner /> Looking…
        </div>
      )}
      {got && got.state === "none" && shown.length === 0 && (
        <p className="text-sm text-muted-foreground">No pictures of {who} were found.</p>
      )}
      {got && got.state === "fetching" && shown.length === 0 && (
        <p className="text-sm text-muted-foreground">Finding pictures of {who}…</p>
      )}
      {shown.length > 0 && (
        <>
          <p className="mb-2 text-xs text-muted-foreground">
            The photo scenes use these. Tap × to stop one showing for any {who} song.
          </p>
          <div className="grid grid-cols-3 gap-2 sm:grid-cols-4">
            {shown.map((p) => (
              <figure key={p.id} className="group relative overflow-hidden rounded-md bg-muted">
                <img
                  src={p.url}
                  alt={p.caption || p.artist}
                  title={[p.caption, p.era, p.credit && `${p.credit} (${p.license})`].filter(Boolean).join(" · ")}
                  loading="lazy"
                  className="aspect-square w-full object-cover"
                />
                <button
                  type="button"
                  aria-label="Hide this picture"
                  onClick={() => hide(p.id, true)}
                  className="absolute right-1 top-1 rounded-full bg-black/60 p-1 text-white"
                >
                  <X className="h-3.5 w-3.5" />
                </button>
              </figure>
            ))}
          </div>
        </>
      )}
      {hidden.length > 0 && (
        <button
          type="button"
          className="mt-2 text-xs underline text-muted-foreground"
          onClick={() => hide(hidden[hidden.length - 1], false)}
        >
          {hidden.length === 1 ? "1 picture hidden" : `${hidden.length} pictures hidden`}: undo the last
        </button>
      )}
    </Unroll>
  );
}
