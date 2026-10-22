// The last anchor and exit-target PNGs for a screen (LIVE_V1 §3), for checking
// the cuts. They are fetched (with the setup key when one is saved, for a server
// that asks for it) and shown as blob URLs rather than plain <img src>.

import { useCallback, useEffect, useRef, useState } from "react";
import { ExternalLink, ImageOff, RefreshCw } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "@/components/ui/dialog";
import { RoomApi } from "./api";
import { SectionTitle } from "./bits";
import { friendlyName } from "./words";

type Kind = "anchor" | "target";
const KINDS: Kind[] = ["anchor", "target"];
const KIND_LABEL: Record<Kind, string> = { anchor: "Going live (anchor)", target: "Back to art (target)" };
const KIND_SHORT: Record<Kind, string> = { anchor: "Going live", target: "Back to art" };

interface Snap {
  state: "loading" | "none" | "ok" | "error";
  url: string | null;
  error?: string;
  fetchedAt?: number;
  size?: { w: number; h: number };
  bytes?: number;
}

const EMPTY: Snap = { state: "loading", url: null };

export function Snapshots({
  api,
  screenId,
  revs,
  exists,
  onError,
}: {
  api: RoomApi;
  screenId: string;
  /** Bumped by the card when a cut has produced a new snapshot of that kind. */
  revs: Record<Kind, number>;
  /**
   * Not in v1 status; room_service sends screens.<id>.snapshots. false = there is no such PNG
   * yet, so it isn't fetched (no 404s); undefined = unknown, fetch and see.
   */
  exists?: { anchor?: boolean; target?: boolean };
  onError: (e: unknown) => void;
}) {
  const [snaps, setSnaps] = useState<Record<Kind, Snap>>({ anchor: EMPTY, target: EMPTY });
  const [manual, setManual] = useState(0);
  const [open, setOpen] = useState<Kind | null>(null);
  // Every blob URL this card made, revoked when replaced and on unmount.
  const urls = useRef<Record<Kind, string | null>>({ anchor: null, target: null });

  // Each kind is fetched on its own: a cut produces one new PNG, and they can be 4K.
  const load = useCallback(
    (kind: Kind, signal: AbortSignal, known: boolean | undefined) => {
      if (known === false) {
        const old = urls.current[kind];
        if (old) URL.revokeObjectURL(old);
        urls.current[kind] = null;
        setSnaps((s) => ({ ...s, [kind]: { state: "none", url: null } }));
        return;
      }
      // Keep showing the previous image while the new one loads.
      setSnaps((s) => ({ ...s, [kind]: { ...s[kind], state: s[kind].url ? s[kind].state : "loading" } }));
      api
        .snapshot(screenId, kind, signal)
        .then((blob) => {
          if (signal.aborted) return;
          const old = urls.current[kind];
          if (!blob) {
            if (old) URL.revokeObjectURL(old);
            urls.current[kind] = null;
            setSnaps((s) => ({ ...s, [kind]: { state: "none", url: null, fetchedAt: Date.now() } }));
            return;
          }
          const url = URL.createObjectURL(blob);
          urls.current[kind] = url;
          if (old) URL.revokeObjectURL(old);
          setSnaps((s) => ({ ...s, [kind]: { state: "ok", url, fetchedAt: Date.now(), bytes: blob.size } }));
        })
        .catch((e) => {
          if (signal.aborted) return;
          onError(e);
          setSnaps((s) => ({ ...s, [kind]: { ...s[kind], state: "error", error: e instanceof Error ? e.message : String(e) } }));
        });
    },
    [api, screenId, onError],
  );

  const anchorKnown = exists?.anchor;
  const targetKnown = exists?.target;
  useEffect(() => {
    const ac = new AbortController();
    load("anchor", ac.signal, anchorKnown);
    return () => ac.abort();
  }, [load, revs.anchor, manual, anchorKnown]);

  useEffect(() => {
    const ac = new AbortController();
    load("target", ac.signal, targetKnown);
    return () => ac.abort();
  }, [load, revs.target, manual, targetKnown]);

  useEffect(
    () => () => {
      for (const k of KINDS) if (urls.current[k]) URL.revokeObjectURL(urls.current[k] as string);
    },
    [],
  );

  const current = open ? snaps[open] : null;

  return (
    <div>
      <SectionTitle
        right={
          <Button
            variant="ghost"
            size="sm"
            onClick={() => setManual((n) => n + 1)}
            title="Fetch the snapshots again"
            className="-my-1 h-11 rounded-xl text-[15px]"
          >
            <RefreshCw /> Refresh
          </Button>
        }
      >
        Snapshots
      </SectionTitle>
      <p className="-mt-1 mb-2 text-sm text-muted-foreground">The still frames the last cut started from and ended on.</p>
      <div className="grid grid-cols-2 gap-2">
        {KINDS.map((kind) => {
          const s = snaps[kind];
          const shown = !!s.url && s.state !== "error";
          return (
            <button
              key={kind}
              type="button"
              data-snapshot={kind}
              data-state={s.state}
              disabled={!shown}
              onClick={() => setOpen(kind)}
              className="group relative aspect-video overflow-hidden rounded-xl border border-stone-200 bg-stone-100 text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-default"
              title={shown ? "Open full size" : s.error}
            >
              {shown && s.url ? (
                <img
                  src={s.url}
                  alt={`${KIND_LABEL[kind]} for ${screenId}`}
                  className="h-full w-full object-contain transition-opacity group-hover:opacity-90"
                  decoding="async"
                  onError={() =>
                    // The body wasn't a decodable image: say so rather than show a broken-image icon.
                    setSnaps((prev) =>
                      prev[kind].url === s.url
                        ? { ...prev, [kind]: { ...prev[kind], state: "error", error: "not a decodable PNG" } }
                        : prev,
                    )
                  }
                  onLoad={(e) => {
                    const img = e.currentTarget;
                    setSnaps((prev) =>
                      prev[kind].url === s.url
                        ? { ...prev, [kind]: { ...prev[kind], size: { w: img.naturalWidth, h: img.naturalHeight } } }
                        : prev,
                    );
                  }}
                />
              ) : (
                <div className="flex h-full w-full flex-col items-center justify-center gap-1 pb-5 text-xs text-muted-foreground">
                  {s.state === "loading" ? (
                    "Loading…"
                  ) : (
                    <>
                      <ImageOff className="h-4 w-4" />
                      {s.state === "error" ? "Couldn't load" : "None yet"}
                    </>
                  )}
                </div>
              )}
              <span className="absolute inset-x-0 bottom-0 flex justify-between gap-1 bg-stone-900/65 px-2 py-0.5 text-[11px] text-white">
                <span className="truncate">{KIND_SHORT[kind]}</span>
                {s.size && <span className="hidden font-mono min-[480px]:inline">{s.size.w}×{s.size.h}</span>}
              </span>
            </button>
          );
        })}
      </div>

      <Dialog open={!!open} onOpenChange={(o) => !o && setOpen(null)}>
        <DialogContent fullscreen noPadding className="flex flex-col gap-0 border-0 bg-black [--foreground:0_0%_100%]">
          <DialogDescription className="sr-only">
            The full-size PNG room_service rendered for this cut, as the stream's first (anchor) or last (target) frame.
          </DialogDescription>
          <DialogTitle className="flex flex-wrap items-center gap-x-3 gap-y-1 border-b border-white/10 px-4 py-3 pr-14 text-sm font-medium text-white">
            {open && `${friendlyName(screenId)} · ${KIND_LABEL[open]}`}
            {current?.size && <span className="font-mono text-xs text-white/60">{current.size.w}×{current.size.h}</span>}
            {current?.bytes !== undefined && (
              <span className="font-mono text-xs text-white/60">{(current.bytes / 1024).toFixed(0)} KiB</span>
            )}
            {current?.fetchedAt && (
              <span className="text-xs text-white/60">fetched {new Date(current.fetchedAt).toLocaleTimeString()}</span>
            )}
            {current?.url && (
              <a
                href={current.url}
                target="_blank"
                rel="noreferrer"
                className="inline-flex items-center gap-1 text-xs text-sky-300 hover:underline"
              >
                <ExternalLink className="h-3 w-3" /> Open in new tab
              </a>
            )}
          </DialogTitle>
          <div className="min-h-0 flex-1 overflow-auto">
            {current?.url && (
              <img src={current.url} alt={open ? KIND_LABEL[open] : ""} className="mx-auto block h-full w-full object-contain" />
            )}
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
}
