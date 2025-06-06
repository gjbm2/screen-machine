// src/pages/DisplayPage/hooks/useFilePolling.ts
import { useEffect, useRef, useState } from "react";

const POLL_INTERVAL = 1000;

export default function useFilePolling(baseFileName: string | null) {
  const [currentSrc, setCurrentSrc] = useState<string | null>(null);
  const [videoKey, setVideoKey] = useState<string>("initial");
  const lastModifiedRef = useRef<number>(0);
  const [lastModified, setLastModified] = useState<number>(0);
  const [fileType, setFileType] = useState<string | null>(null);
  const [fileName, setFileName] = useState<string | null>(null);

  useEffect(() => {
    if (!baseFileName) return;

    const base = `/output/${baseFileName}`;

    const detectInitialFile = async () => {
      const jpgUrl = `${base}.jpg`;
      const JPGUrl = `${base}.JPG`;
      const mp4Url = `${base}.mp4`;

      const [jpgRes, JPGRes, mp4Res] = await Promise.allSettled([
        fetch(jpgUrl, { method: "HEAD" }),
        fetch(JPGUrl, { method: "HEAD" }),
        fetch(mp4Url, { method: "HEAD" }),
      ]);

      const jpgModified = jpgRes.status === "fulfilled"
        ? new Date(jpgRes.value.headers.get("last-modified") || 0).getTime()
        : 0;
      const JPGModified = JPGRes.status === "fulfilled"
        ? new Date(JPGRes.value.headers.get("last-modified") || 0).getTime()
        : 0;
      const mp4Modified = mp4Res.status === "fulfilled"
        ? new Date(mp4Res.value.headers.get("last-modified") || 0).getTime()
        : 0;

      const latestType = Math.max(jpgModified, JPGModified) > mp4Modified ? 
        (jpgModified > JPGModified ? "jpg" : "JPG") : "mp4";
      const latestModified = Math.max(jpgModified, JPGModified, mp4Modified);
      lastModifiedRef.current = latestModified;
      setLastModified(latestModified);
      setFileType(latestType);
      setFileName(`${baseFileName}.${latestType}`);

      const initialUrl = `${base}.${latestType}?t=${latestModified}`;
      setCurrentSrc(initialUrl);
      setVideoKey(`${Date.now()}`);
    };

    const checkForChange = async () => {
      const jpgUrl = `${base}.jpg`;
      const JPGUrl = `${base}.JPG`;
      const mp4Url = `${base}.mp4`;

      const [jpgRes, JPGRes, mp4Res] = await Promise.allSettled([
        fetch(jpgUrl, { method: "HEAD" }),
        fetch(JPGUrl, { method: "HEAD" }),
        fetch(mp4Url, { method: "HEAD" }),
      ]);

      const jpgModified = jpgRes.status === "fulfilled"
        ? new Date(jpgRes.value.headers.get("last-modified") || 0).getTime()
        : 0;
      const JPGModified = JPGRes.status === "fulfilled"
        ? new Date(JPGRes.value.headers.get("last-modified") || 0).getTime()
        : 0;
      const mp4Modified = mp4Res.status === "fulfilled"
        ? new Date(mp4Res.value.headers.get("last-modified") || 0).getTime()
        : 0;

      const latestType = Math.max(jpgModified, JPGModified) > mp4Modified ? 
        (jpgModified > JPGModified ? "jpg" : "JPG") : "mp4";
      const latestModified = Math.max(jpgModified, JPGModified, mp4Modified);

      if (latestModified > lastModifiedRef.current) {
        lastModifiedRef.current = latestModified;
        setLastModified(latestModified);
        setFileType(latestType);
        setFileName(`${baseFileName}.${latestType}`);
        const newUrl = `${base}.${latestType}?t=${latestModified}`;
        setCurrentSrc(newUrl);
        setVideoKey(`${Date.now()}`);
      }
    };

    detectInitialFile();
    const interval = setInterval(checkForChange, POLL_INTERVAL);
    return () => clearInterval(interval);
  }, [baseFileName]);

  return { currentSrc, videoKey, lastModified, fileType, fileName };
}
