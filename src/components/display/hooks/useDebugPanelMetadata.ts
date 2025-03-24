
import { useEffect, useRef } from 'react';
import { toast } from 'sonner';
import { extractImageMetadata } from '../utils';

interface UseDebugPanelMetadataProps {
  imageUrl: string | null;
  metadata: Record<string, string>;
  setMetadataEntries: (entries: Array<{key: string, value: string}>) => void;
}

export const useDebugPanelMetadata = ({
  imageUrl,
  metadata,
  setMetadataEntries
}: UseDebugPanelMetadataProps) => {
  const previousImageUrlRef = useRef<string | null>(null);
  const previousMetadataRef = useRef<Record<string, string>>({});

  // Update metadata entries when metadata changes
  useEffect(() => {
    console.log('[useDebugPanelMetadata] Processing metadata:', metadata);
    console.log('[useDebugPanelMetadata] Previous image URL:', previousImageUrlRef.current);
    console.log('[useDebugPanelMetadata] Current image URL:', imageUrl);
    
    const metadataChanged = JSON.stringify(metadata) !== JSON.stringify(previousMetadataRef.current);
    console.log('[useDebugPanelMetadata] Metadata changed:', metadataChanged);
    
    if ((imageUrl !== previousImageUrlRef.current) || metadataChanged) {
      console.log('[useDebugPanelMetadata] Image URL or metadata changed, processing entries');
      previousImageUrlRef.current = imageUrl;
      previousMetadataRef.current = { ...metadata };
      
      // Process metadata into a format suitable for the UI
      if (metadata && typeof metadata === 'object') {
        console.log('[useDebugPanelMetadata] Converting metadata object to entries');
        
        const entries = Object.entries(metadata).map(([key, value]) => ({
          key,
          value: String(value) // Ensure value is a string
        }));
        
        console.log('[useDebugPanelMetadata] Processed metadata entries:', entries);
        setMetadataEntries(entries);
      } else {
        console.warn('[useDebugPanelMetadata] Invalid metadata format:', metadata);
        setMetadataEntries([]);
      }
    }
  }, [metadata, imageUrl, setMetadataEntries]);

  // Handle manual metadata refresh
  const handleRefreshMetadata = async () => {
    console.log('[useDebugPanelMetadata] handleRefreshMetadata called');
    if (!imageUrl) {
      toast.error("No image URL to extract metadata from");
      return {};
    }
    
    try {
      toast.info("Manually refreshing metadata...");
      // Add cache-busting parameter
      const cacheBustUrl = `${imageUrl}${imageUrl.includes('?') ? '&' : '?'}manualRefresh=${Date.now()}`;
      console.log('[useDebugPanelMetadata] Refreshing metadata for URL:', cacheBustUrl);
      
      // First try the direct API endpoint with fetch
      try {
        console.log('[useDebugPanelMetadata] Trying direct API call to extract-metadata');
        const apiUrl = '/api/extract-metadata';
        const params = new URLSearchParams({ url: cacheBustUrl });
        const response = await fetch(`${apiUrl}?${params.toString()}`);
        
        if (!response.ok) {
          throw new Error(`API call failed: ${response.status}`);
        }
        
        const data = await response.json();
        console.log('[useDebugPanelMetadata] API response:', data);
        
        if (data && typeof data === 'object' && Object.keys(data).length > 0) {
          toast.success(`Found ${Object.keys(data).length} metadata entries`);
          // Force page refresh to update UI with new metadata
          window.location.reload();
          return data;
        }
        
        console.warn('[useDebugPanelMetadata] API call returned no metadata or invalid format');
      } catch (apiErr) {
        console.error('[useDebugPanelMetadata] Error in direct API call:', apiErr);
      }
      
      // Fallback to using the extractImageMetadata utility
      console.log('[useDebugPanelMetadata] Falling back to extractImageMetadata utility');
      const newMetadata = await extractImageMetadata(cacheBustUrl);
      console.log('[useDebugPanelMetadata] Metadata from utility:', newMetadata);
      
      if (Object.keys(newMetadata).length > 0) {
        toast.success(`Found ${Object.keys(newMetadata).length} metadata entries`);
        // Force page refresh to update UI with new metadata
        window.location.reload();
        return newMetadata;
      }
      
      toast.warning("No metadata found in this image");
      return {};
    } catch (err) {
      console.error('[useDebugPanelMetadata] Error refreshing metadata:', err);
      toast.error("Failed to refresh metadata");
      return {};
    }
  };

  return {
    handleRefreshMetadata
  };
};
