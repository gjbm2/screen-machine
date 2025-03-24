
import { useEffect } from 'react';

interface UseDebugPanelCaptionProps {
  caption: string;
  metadataEntries: Array<{key: string, value: string}>;
  setPreviewCaption: (caption: string | null) => void;
  onApplyCaption: (caption: string | null) => void;
  imageUrl: string | null;
}

export const useDebugPanelCaption = ({
  caption,
  metadataEntries,
  setPreviewCaption,
  onApplyCaption,
  imageUrl
}: UseDebugPanelCaptionProps) => {
  
  // Process caption with metadata replacements
  useEffect(() => {
    console.log('[useDebugPanelCaption] Processing caption template:', caption);
    console.log('[useDebugPanelCaption] Available metadata entries:', metadataEntries);
    
    if (caption === '{all}') {
      const allMetadata = metadataEntries
        .map(entry => `${entry.key}: ${entry.value}`)
        .join('\n');
        
      console.log('[useDebugPanelCaption] All metadata caption:', allMetadata);
      setPreviewCaption(allMetadata);
      onApplyCaption(allMetadata);
    } else if (caption) {
      // Process template tags like {key}
      try {
        const processed = caption.replace(/\{([^}]+)\}/g, (match, key) => {
          const entry = metadataEntries.find(e => e.key === key);
          const replacement = entry ? entry.value : match;
          console.log(`[useDebugPanelCaption] Replacing ${match} with:`, replacement);
          return replacement;
        });
        
        console.log('[useDebugPanelCaption] Processed caption:', processed);
        setPreviewCaption(processed);
        onApplyCaption(processed);
      } catch (err) {
        console.error('[useDebugPanelCaption] Error processing caption:', err);
        setPreviewCaption(caption);
        onApplyCaption(caption);
      }
    } else {
      console.log('[useDebugPanelCaption] No caption to process');
      setPreviewCaption(null);
      onApplyCaption(null);
    }
  }, [caption, metadataEntries, onApplyCaption, setPreviewCaption]);

  const insertMetadataTag = (key: string) => {
    console.log('[useDebugPanelCaption] Inserting metadata tag:', key);
    
    return () => {
      const textArea = document.getElementById('caption-textarea') as HTMLTextAreaElement;
      if (textArea) {
        const selectionStart = textArea.selectionStart;
        const selectionEnd = textArea.selectionEnd;
        const textBefore = caption.substring(0, selectionStart);
        const textAfter = caption.substring(selectionEnd);
        const newCaption = `${textBefore}{${key}}${textAfter}`;
        
        setTimeout(() => {
          textArea.focus();
          const newPosition = selectionStart + key.length + 2;
          textArea.setSelectionRange(newPosition, newPosition);
        }, 50);
        
        return newCaption;
      }
      return `${caption}{${key}}`;
    };
  };

  const insertAllMetadata = () => {
    console.log('[useDebugPanelCaption] Inserting all metadata');
    return '{all}';
  };

  return {
    insertMetadataTag,
    insertAllMetadata
  };
};
