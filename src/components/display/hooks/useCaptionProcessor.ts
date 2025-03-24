
import { useState, useEffect } from 'react';
import { DisplayParams } from '@/components/display/types';

export const useCaptionProcessor = (
  previewParams: DisplayParams,
  metadata: Record<string, string>,
  imageUrl: string | null,
  setProcessedCaption: (caption: string | null) => void
) => {
  // Process captions by replacing metadata tags
  useEffect(() => {
    if (!imageUrl) return;

    if (previewParams.caption) {
      if (previewParams.data !== undefined) {
        if (Object.keys(metadata).length > 0) {
          const newCaption = processCaptionWithMetadata(previewParams.caption, metadata);
          setProcessedCaption(newCaption);
        }
      } else {
        setProcessedCaption(previewParams.caption);
      }
    } else {
      setProcessedCaption(null);
    }
  }, [previewParams.caption, previewParams.data, metadata, imageUrl, setProcessedCaption]);
};

// Helper function for caption processing
export const processCaptionWithMetadata = (caption: string | null, metadata: Record<string, string>): string | null => {
  if (!caption) return null;
  
  let processedCaption = caption;
  
  // More detailed logging
  console.log('[processCaptionWithMetadata] Processing caption:', caption);
  console.log('[processCaptionWithMetadata] With metadata:', metadata);
  
  // Special case for {all} placeholder
  if (caption === '{all}') {
    const allMetadata = Object.entries(metadata)
      .map(([key, value]) => `${key}: ${value}`)
      .join('\n');
    return allMetadata || 'No metadata available';
  }
  
  // Replace individual tags
  Object.entries(metadata).forEach(([key, value]) => {
    const regex = new RegExp(`\\{${key}\\}`, 'g');
    processedCaption = processedCaption?.replace(regex, value) || '';
  });
  
  return processedCaption;
};
