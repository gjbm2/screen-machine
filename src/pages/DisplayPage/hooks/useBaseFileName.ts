// src/pages/DisplayPage/hooks/useBaseFileName.ts
import { useEffect, useState } from "react";
import { getPublishDestinations } from "@/services/PublishService";

export default function useBaseFileName(screenId: string | undefined): string | null {
  const [baseFileName, setBaseFileName] = useState<string | null>(null);

  useEffect(() => {
    if (!screenId) return;
    const destinations = getPublishDestinations();
    const matched = destinations.find(d => d.id === screenId && d.type === "output_file");
    if (matched) {
      setBaseFileName(matched.file.replace(/\.(jpg|mp4)$/, ""));
    }
  }, [screenId]);

  return baseFileName;
}
