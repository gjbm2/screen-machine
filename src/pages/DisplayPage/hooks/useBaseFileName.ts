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
        } else {
          // Fallback to using screen ID directly
          setBaseFileName(screenId);
        }
      } catch (error) {
        console.error('Error fetching destinations:', error);
        // Fallback to using screen ID directly
        setBaseFileName(screenId);
      }
    };

    getDestinations();
  }, [screenId]);

  return baseFileName;
}
