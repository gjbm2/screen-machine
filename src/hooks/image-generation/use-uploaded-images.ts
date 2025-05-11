import { useState, useEffect, useRef } from 'react';
import { useReferenceImages } from '@/contexts/ReferenceImagesContext';

/**
 * Hook to manage uploaded image URLs
 * Now uses the ReferenceImagesContext as the single source of truth
 */
export const useUploadedImages = () => {
  // Use our context to get the reference URLs
  const { referenceUrls, addReferenceUrl, removeReferenceUrl, clearReferenceUrls } = useReferenceImages();
  const isUpdatingFromWindow = useRef(false);

  // When referenceUrls changes, update the global variable
  useEffect(() => {
    // Prevent loops by not updating if we're in the middle of updating from window
    if (isUpdatingFromWindow.current) return;

    // Check if the arrays are actually different to avoid infinite loops
    const currentExternalUrls = window.externalImageUrls || [];
    const areArraysEqual = 
      currentExternalUrls.length === referenceUrls.length && 
      currentExternalUrls.every((url, i) => url === referenceUrls[i]);
    
    // Only update if there's an actual difference
    if (!areArraysEqual) {
      window.externalImageUrls = [...referenceUrls];
    }
  }, [referenceUrls]);
  
  // Listen for external changes to window.externalImageUrls
  useEffect(() => {
    const handleStorageChange = () => {
      if (window.externalImageUrls) {
        const currentExternalUrls = window.externalImageUrls;
        const areArraysEqual = 
          currentExternalUrls.length === referenceUrls.length && 
          currentExternalUrls.every((url, i) => url === referenceUrls[i]);
        
        if (!areArraysEqual) {
          isUpdatingFromWindow.current = true;
          clearReferenceUrls();
          currentExternalUrls.forEach(url => addReferenceUrl(url, true));
          isUpdatingFromWindow.current = false;
        }
      }
    };
    
    // Initial sync if window.externalImageUrls exists
    if (window.externalImageUrls && window.externalImageUrls.length > 0) {
      handleStorageChange();
    }
    
    // No need for event listeners as direct property access should be sufficient
    
    return () => {
      // No cleanup needed
    };
  }, []);

  // Create a compatible setUploadedImageUrls function
  const setUploadedImageUrls = (newUrls: string[] | ((prev: string[]) => string[])) => {
    // Clear existing URLs
    clearReferenceUrls();
    
    // Add new URLs
    if (typeof newUrls === 'function') {
      const calculatedUrls = newUrls(referenceUrls);
      calculatedUrls.forEach(url => {
        addReferenceUrl(url, true);
      });
    } else {
      newUrls.forEach(url => {
        addReferenceUrl(url, true);
      });
    }
  };

  // Create a removeUrl function
  const removeUrl = (url: string) => {
    removeReferenceUrl(url);
  };

  return {
    uploadedImageUrls: referenceUrls,
    setUploadedImageUrls,
    removeUrl
  };
};
