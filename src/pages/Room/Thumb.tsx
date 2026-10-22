// A thumbnail that costs nothing until it scrolls into view: a neutral placeholder first, the
// image only once its box is near the visible part of its scroll area, faded in when decoded.
// A picker with ninety-odd (or nine hundred) of these must never stall a phone.

import { useContext, useEffect, useRef, useState } from "react";
import { ImageIcon } from "lucide-react";
import { cn } from "@/lib/utils";
import { ThumbRoot } from "./liveLib";

export function Thumb({ src, alt = "", className }: { src: string | null | undefined; alt?: string; className?: string }) {
  const root = useContext(ThumbRoot);
  const box = useRef<HTMLDivElement>(null);
  const [near, setNear] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    setLoaded(false);
    setFailed(false);
  }, [src]);

  useEffect(() => {
    if (!src || near) return;
    const el = box.current;
    if (!el) return;
    if (typeof IntersectionObserver === "undefined") {
      setNear(true);
      return;
    }
    const io = new IntersectionObserver(
      (entries) => {
        if (entries.some((e) => e.isIntersecting)) {
          setNear(true);
          io.disconnect();
        }
      },
      { root, rootMargin: "240px 0px" },
    );
    io.observe(el);
    return () => io.disconnect();
  }, [src, near, root]);

  const show = !!src && near && !failed;
  return (
    <div ref={box} className={cn("relative overflow-hidden bg-stone-100", className)}>
      {(!show || !loaded) && (
        <ImageIcon aria-hidden className="absolute left-1/2 top-1/2 h-5 w-5 -translate-x-1/2 -translate-y-1/2 text-stone-300" />
      )}
      {show && (
        <img
          src={src}
          alt={alt}
          decoding="async"
          loading="lazy"
          draggable={false}
          onLoad={() => setLoaded(true)}
          onError={() => setFailed(true)}
          className={cn(
            "absolute inset-0 h-full w-full object-cover transition-opacity duration-300 motion-reduce:transition-none",
            loaded ? "opacity-100" : "opacity-0",
          )}
        />
      )}
    </div>
  );
}
