
import { useState, useCallback } from 'react';

export const useImageGenerationLoading = () => {
  const [isFirstRun, setIsFirstRun] = useState(true);
  const [fullscreenRefreshTrigger, setFullscreenRefreshTrigger] = useState(0);
  const [lastBatchIdUsed, setLastBatchIdUsed] = useState<string | null>(null);
  const [activeGenerations, setActiveGenerations] = useState<string[]>([]);

  const handleGenerationComplete = useCallback(() => {
    setFullscreenRefreshTrigger(prev => prev + 1);
  }, []);

  return {
    isFirstRun,
    setIsFirstRun,
    fullscreenRefreshTrigger,
    setFullscreenRefreshTrigger,
    lastBatchIdUsed,
    setLastBatchIdUsed,
    activeGenerations,
    setActiveGenerations,
    handleGenerationComplete
  };
};
