import { useEffect, useCallback, useRef } from 'react';

/**
 * A hook to handle external image URLs passed via the global window.externalImageUrls property
 * This enables "Use as input" functionality by letting images be shared across components
 */
export const useExternalImageUrls = (
  setPreviewUrls: React.Dispatch<React.SetStateAction<string[]>>
) => {
  // A map to track which base URLs have been deleted
  const deletedBaseUrls = useRef(new Set<string>());
  
  // Function to sync local state with global state
  const syncWithGlobalState = useCallback(() => {
    setPreviewUrls(prev => {
      // Ensure the global variable exists
      if (!window.externalImageUrls) {
        window.externalImageUrls = [];
      }
      
      // Set global state to match current local state
      window.externalImageUrls = [...prev];
      
      return prev;
    });
  }, [setPreviewUrls]);

  // Check for external images when component mounts and props change
  useEffect(() => {
    const urlsFromProps = window.externalImageUrls || [];
    if (urlsFromProps && urlsFromProps.length > 0) {
      console.log('External image URLs detected:', urlsFromProps);
      
      // Add these URLs to our preview URLs if they're not already there
      setPreviewUrls(prev => {
        // First create a set of existing URLs for faster lookup
        const existingUrls = new Set(prev);
        
        // Filter out URLs that have been marked as deleted
        const filteredUrls = urlsFromProps.filter(url => {
          const baseUrl = url.split('?')[0];
          return !deletedBaseUrls.current.has(baseUrl) || url.includes('_t=');
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
      
      // Clear the global variable after using it to prevent duplicates
      window.externalImageUrls = [];
    }
  }, [window.externalImageUrls, setPreviewUrls]);

  // Check for external images on component mount and at intervals
  useEffect(() => {
    const checkForExternalImages = () => {
      const externalUrls = window.externalImageUrls || [];
      if (externalUrls.length > 0) {
        console.log('External image URLs detected on periodic check:', externalUrls);
        
        setPreviewUrls(prev => {
          // Create a set of existing URLs for faster lookup
          const existingUrls = new Set(prev);
          
          // Filter out URLs that have been marked as deleted
          const filteredUrls = externalUrls.filter(url => {
            const baseUrl = url.split('?')[0];
            return !deletedBaseUrls.current.has(baseUrl) || url.includes('_t=');
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
        
        // Clear the global variable after using it
        window.externalImageUrls = [];
      }
    };
    
    // Check immediately on mount/update
    checkForExternalImages();
    
    // Also set up an interval to periodically check for external images
    const intervalId = setInterval(checkForExternalImages, 1000);
    
    return () => {
      clearInterval(intervalId);
    };
  }, [setPreviewUrls]);

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
