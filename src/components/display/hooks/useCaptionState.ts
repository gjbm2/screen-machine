
import { useState } from 'react';
import { processCaptionWithMetadata } from '../utils';

export const useCaptionState = () => {
  const [processedCaption, setProcessedCaption] = useState<string | null>(null);

  const updateCaption = (caption: string | null, metadata: Record<string, string>) => {
    if (!caption) {
      setProcessedCaption(null);
      return;
    }
    
    const newCaption = processCaptionWithMetadata(caption, metadata);
    setProcessedCaption(newCaption);
  };

  return {
    processedCaption,
    setProcessedCaption,
    updateCaption
  };
};
