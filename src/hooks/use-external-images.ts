import { useEffect, useCallback, useRef } from 'react';
import { useReferenceImages } from '@/contexts/ReferenceImagesContext';

/**
 * A hook to handle external image URLs passed via the global window.externalImageUrls property
 * This enables "Use as input" functionality by letting images be shared across components
 * 
 * @deprecated Use useReferenceImages context instead
 */
export const useExternalImageUrls = (
  setPreviewUrls: React.Dispatch<React.SetStateAction<string[]>>
) => {
  // A map to track which base URLs have been deleted
  const deletedBaseUrls = useRef(new Set<string>());
  const { referenceUrls } = useReferenceImages();
  
  // Function to sync local state with context (not global state anymore)
  const syncWithGlobalState = useCallback(() => {
    // This no longer modifies window.externalImageUrls to avoid infinite loops
    console.log('useExternalImageUrls.syncWithGlobalState called (deprecated)');
  }, []);

  // Check for external images when referenceUrls change
  useEffect(() => {
    if (referenceUrls && referenceUrls.length > 0) {
      console.log('Reference URLs from context detected:', referenceUrls);
      
      // Add these URLs to our preview URLs if they're not already there
      setPreviewUrls(prev => {
        // First create a set of existing URLs for faster lookup
        const existingUrls = new Set(prev);
        
        // Filter out URLs that have been marked as deleted
        const filteredUrls = referenceUrls.filter(url => {
          const baseUrl = url.split('?')[0];
          return !deletedBaseUrls.current.has(baseUrl);
        });
        
        // Only add URLs that don't already exist
        const newUrls = [...prev];
        for (const url of filteredUrls) {
          if (!existingUrls.has(url)) {
            newUrls.push(url);
          }
        }
        
        return newUrls;
      });
    }
  }, [referenceUrls, setPreviewUrls]);

  // Function to mark URLs as deleted to prevent them from reappearing
  const markUrlAsDeleted = useCallback((url: string) => {
    const baseUrl = url.split('?')[0];
    console.log('Marking base URL as deleted:', baseUrl);
    deletedBaseUrls.current.add(baseUrl);
  }, []);

  return {
    syncWithGlobalState,
    markUrlAsDeleted
  };
};

export default useExternalImageUrls;
