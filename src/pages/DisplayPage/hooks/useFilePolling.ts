// src/pages/DisplayPage/hooks/useFilePolling.ts
import { useEffect, useRef, useState } from "react";

const POLL_INTERVAL = 1000;

export default function useFilePolling(baseFileName: string | null) {
  const [currentSrc, setCurrentSrc] = useState<string | null>(null);
  const [videoKey, setVideoKey] = useState<string>("initial");
  const lastModifiedRef = useRef<number>(0);

  useEffect(() => {
    if (!baseFileName) return;

    const base = `/output/${baseFileName}`;

    const detectInitialFile = async () => {
      const jpgUrl = `${base}.jpg`;
      const mp4Url = `${base}.mp4`;

      const [jpgRes, mp4Res] = await Promise.allSettled([
        fetch(jpgUrl, { method: "HEAD" }),
        fetch(mp4Url, { method: "HEAD" }),
      ]);

      const jpgModified = jpgRes.status === "fulfilled"
        ? new Date(jpgRes.value.headers.get("last-modified") || 0).getTime()
        : 0;
      const mp4Modified = mp4Res.status === "fulfilled"
        ? new Date(mp4Res.value.headers.get("last-modified") || 0).getTime()
        : 0;

      const latestType = jpgModified > mp4Modified ? "jpg" : "mp4";
      const latestModified = Math.max(jpgModified, mp4Modified);
      lastModifiedRef.current = latestModified;

      const initialUrl = `${base}.${latestType}?t=${latestModified}`;
      setCurrentSrc(initialUrl);
      setVideoKey(`${Date.now()}`);
    };

    const checkForChange = async () => {
      const jpgUrl = `${base}.jpg`;
      const mp4Url = `${base}.mp4`;

      const [jpgRes, mp4Res] = await Promise.allSettled([
        fetch(jpgUrl, { method: "HEAD" }),
        fetch(mp4Url, { method: "HEAD" }),
      ]);

      const jpgModified = jpgRes.status === "fulfilled"
        ? new Date(jpgRes.value.headers.get("last-modified") || 0).getTime()
        : 0;
      const mp4Modified = mp4Res.status === "fulfilled"
        ? new Date(mp4Res.value.headers.get("last-modified") || 0).getTime()
        : 0;

      const latestType = jpgModified > mp4Modified ? "jpg" : "mp4";
      const latestModified = Math.max(jpgModified, mp4Modified);

      if (latestModified > lastModifiedRef.current) {
        lastModifiedRef.current = latestModified;
        const newUrl = `${base}.${latestType}?t=${latestModified}`;
        setCurrentSrc(newUrl);
        setVideoKey(`${Date.now()}`);
      }
    };

    detectInitialFile();
    const interval = setInterval(checkForChange, POLL_INTERVAL);
    return () => clearInterval(interval);
  }, [baseFileName]);

  return { currentSrc, videoKey };
}
