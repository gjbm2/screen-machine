
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
    if (!imageUrl) {
      console.log('[useCaptionProcessor] No image URL, clearing caption');
      setProcessedCaption(null);
      return;
    }

    if (previewParams.caption) {
      console.log('[useCaptionProcessor] Processing caption:', previewParams.caption);
      console.log('[useCaptionProcessor] With metadata:', metadata);
      console.log('[useCaptionProcessor] Metadata keys available:', Object.keys(metadata));
      
      // Always process caption with metadata, regardless of data flag
      const newCaption = processCaptionWithMetadata(previewParams.caption, metadata);
      console.log('[useCaptionProcessor] Processed caption result:', newCaption);
      setProcessedCaption(newCaption);
    } else {
      console.log('[useCaptionProcessor] No caption in params, clearing caption');
      setProcessedCaption(null);
    }
  }, [previewParams.caption, metadata, imageUrl, setProcessedCaption]);
};

// Helper function for caption processing with improved logging
export const processCaptionWithMetadata = (caption: string | null, metadata: Record<string, string>): string | null => {
  if (!caption) return null;
  
  let processedCaption = caption;
  
  // More detailed logging
  console.log('[processCaptionWithMetadata] Processing caption:', caption);
  console.log('[processCaptionWithMetadata] With metadata:', metadata);
  console.log('[processCaptionWithMetadata] Metadata keys:', Object.keys(metadata));
  
  // Special case for {all} placeholder
  if (caption === '{all}') {
    if (Object.keys(metadata).length === 0) {
      console.log('[processCaptionWithMetadata] No metadata available for {all} tag');
      return 'No metadata available';
    }
    
    const allMetadata = Object.entries(metadata)
      .map(([key, value]) => `${key}: ${value}`)
      .join('\n');
    
    console.log('[processCaptionWithMetadata] All metadata caption:', allMetadata);
    return allMetadata || 'No metadata available';
  }
  
  // Replace individual tags with detailed logging
  Object.entries(metadata).forEach(([key, value]) => {
    const regex = new RegExp(`\\{${key}\\}`, 'g');
    const prevCaption = processedCaption;
    processedCaption = processedCaption?.replace(regex, value) || '';
    
    // If a replacement was made, log it
    if (prevCaption !== processedCaption) {
      console.log(`[processCaptionWithMetadata] Replaced {${key}} with "${value}"`);
    }
  });
  
  // Check if there are any unreplaced tags remaining
  const unreplacedTagsMatch = processedCaption.match(/\{([^}]+)\}/g);
  if (unreplacedTagsMatch) {
    console.warn('[processCaptionWithMetadata] Unreplaced tags found:', unreplacedTagsMatch);
    
    // Replace unreplaced tags with their tag names to make it clearer
    unreplacedTagsMatch.forEach(tag => {
      const tagName = tag.substring(1, tag.length - 1);
      processedCaption = processedCaption?.replace(tag, `[${tagName}]`) || '';
    });
  }
  
  return processedCaption;
};
