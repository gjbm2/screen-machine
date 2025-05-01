// src/pages/DisplayPage/hooks/useBaseFileName.ts
import { useEffect, useState } from "react";
import apiService from '@/utils/api';

export default function useBaseFileName(screenId: string | undefined): string | null {
  const [baseFileName, setBaseFileName] = useState<string | null>(null);

  useEffect(() => {
    if (!screenId) return;
    
    const getDestinations = async () => {
      try {
        const destinations = await apiService.getPublishDestinations();
    const matched = destinations.find(d => d.id === screenId && d.type === "output_file");
    if (matched) {
      setBaseFileName(matched.file.replace(/\.(jpg|mp4)$/, ""));
    }
      } catch (error) {
        console.error('Error fetching destinations:', error);
        setBaseFileName(null);
      }
    };

    getDestinations();
  }, [screenId]);

  return baseFileName;
}
