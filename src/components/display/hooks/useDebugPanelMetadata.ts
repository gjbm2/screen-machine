
import { useCallback } from 'react';
import { MetadataEntry } from '../types';

interface UseDebugPanelMetadataProps {
  imageUrl: string | null;
  metadata: Record<string, string>;
  setMetadataEntries: (entries: MetadataEntry[]) => void;
}

export const useDebugPanelMetadata = ({
  imageUrl,
  metadata,
  setMetadataEntries
}: UseDebugPanelMetadataProps) => {
  
  // Make this function return a Promise<Record<string, string>> as expected by the component
  const handleRefreshMetadata = useCallback(async (): Promise<Record<string, string>> => {
    console.log('[useDebugPanelMetadata] Refreshing metadata for URL:', imageUrl);
    
    try {
      if (!imageUrl) {
        console.error('[useDebugPanelMetadata] No image URL to refresh metadata for');
        return {};
      }
      
      // For now, just return the current metadata and update entries
      // In a real implementation, this would fetch new metadata
      const entries = Object.entries(metadata).map(([key, value]) => ({
        key,
        value: String(value)
      }));
      
      console.log('[useDebugPanelMetadata] Updated metadata entries:', entries);
      setMetadataEntries(entries);
      
      return metadata;
    } catch (err) {
      console.error('[useDebugPanelMetadata] Error refreshing metadata:', err);
      return {};
    }
  }, [imageUrl, metadata, setMetadataEntries]);

  return {
    handleRefreshMetadata
  };
};
