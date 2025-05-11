import { useState, useEffect, useRef } from 'react';

export const useUploadedImages = () => {
  const [uploadedImageUrls, setUploadedImageUrls] = useState<string[]>([]);
  // Track which URLs we've removed to prevent them from re-appearing
  const removedUrls = useRef(new Set<string>());

  // When uploadedImageUrls changes, store them in a global variable
  // for access in other components, but ensure uniqueness
  useEffect(() => {
    if (uploadedImageUrls.length > 0) {
      // Filter out any URLs that were marked as removed
      const filteredUrls = uploadedImageUrls.filter(url => {
        // Check the base URL (without query parameters)
        const baseUrl = url.split('?')[0];
        // Check if this URL or its base has been marked as removed
        const isRemoved = [...removedUrls.current].some(
          removedUrl => removedUrl === url || removedUrl.split('?')[0] === baseUrl
        );
        return !isRemoved;
      });
      
      // Convert to Set and back to array to ensure uniqueness
      const uniqueUrls = [...new Set(filteredUrls)];
      console.log('Setting global externalImageUrls:', uniqueUrls);
      window.externalImageUrls = uniqueUrls; 
    } else {
      // Clear the global variable if there are no uploaded images
      window.externalImageUrls = [];
    }
  }, [uploadedImageUrls]);

  // Add a function to remove a URL (and prevent it from re-appearing)
  const removeUrl = (urlToRemove: string) => {
    // Mark this URL as removed
    removedUrls.current.add(urlToRemove);
    
    // Update state to filter out the removed URL
    setUploadedImageUrls(prev => 
      prev.filter(url => {
        // Check if URLs match exactly, or if base URLs match
        const baseUrlToRemove = urlToRemove.split('?')[0];
        const baseUrl = url.split('?')[0];
        return url !== urlToRemove && baseUrl !== baseUrlToRemove;
      })
    );
  };

  return {
    uploadedImageUrls,
    setUploadedImageUrls,
    removeUrl
  };
};
