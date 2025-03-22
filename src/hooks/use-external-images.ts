
import { useEffect } from 'react';

/**
 * A hook to handle external image URLs passed via the global window.externalImageUrls property
 * This enables "Use as input" functionality by letting images be shared across components
 */
export const useExternalImageUrls = (
  setPreviewUrls: React.Dispatch<React.SetStateAction<string[]>>
) => {
  // Check for external images when component mounts and props change
  useEffect(() => {
    const urlsFromProps = window.externalImageUrls || [];
    if (urlsFromProps && urlsFromProps.length > 0) {
      console.log('External image URLs detected:', urlsFromProps);
      
      // Add these URLs to our preview URLs if they're not already there
      setPreviewUrls(prev => {
        const newUrls = [...prev];
        for (const url of urlsFromProps) {
          if (!newUrls.includes(url)) {
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
          const newUrls = [...prev];
          for (const url of externalUrls) {
            if (!newUrls.includes(url)) {
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
};

export default useExternalImageUrls;
