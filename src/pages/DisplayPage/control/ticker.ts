// A clock that keeps time in a hidden tab (owner, 25 Nov: "a resilient browser loop as a failsafe to restore
// connections on client side"). A hidden page's own timers are slowed to about once a minute (Chrome's intensive
// throttling), so a page's heartbeats stopped, room_service closed its socket as dead and its reconnects crawled. A
// dedicated worker's timer is not slowed that way: it posts a tick, the page runs `fn` on it. The page's own timer
// runs too, so a browser without workers (or one that blocks them) still ticks; `fn` must be cheap and idempotent.
export function startTicker(ms: number, fn: () => void): () => void {
  let worker: Worker | null = null;
  let url: string | null = null;
  try {
    url = URL.createObjectURL(new Blob([`setInterval(() => postMessage(0), ${Math.max(50, ms)});`], { type: "text/javascript" }));
    worker = new Worker(url);
    worker.onmessage = () => fn();
    worker.onerror = () => {
      worker?.terminate();
      worker = null;
    };
  } catch {
    worker = null;
  }
  const own = setInterval(fn, ms);
  return () => {
    clearInterval(own);
    worker?.terminate();
    if (url) URL.revokeObjectURL(url);
  };
}
