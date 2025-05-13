import { useCallback } from 'react';

export const useImageNavigator = (length: number) => {
  const clamp = useCallback((idx: number) => {
    if (length === 0) return 0;
    return (idx + length) % length;
  }, [length]);

  const prevIndex = useCallback((idx: number) => clamp(idx - 1), [clamp]);
  const nextIndex = useCallback((idx: number) => clamp(idx + 1), [clamp]);

  return { clamp, prevIndex, nextIndex };
}; 