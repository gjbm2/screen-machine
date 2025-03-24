
import { useState, useEffect } from 'react';

export const useUploadedImages = () => {
  const [uploadedImageUrls, setUploadedImageUrls] = useState<string[]>([]);

  // When uploadedImageUrls changes, store them in a global variable
  // for access in other components, but ensure uniqueness
  useEffect(() => {
    if (uploadedImageUrls.length > 0) {
      // Convert to Set and back to array to ensure uniqueness
      const uniqueUrls = [...new Set(uploadedImageUrls)];
      console.log('Setting global externalImageUrls:', uniqueUrls);
      window.externalImageUrls = uniqueUrls; 
    } else {
      // Clear the global variable if there are no uploaded images
      window.externalImageUrls = [];
    }
  }, [uploadedImageUrls]);

  return {
    uploadedImageUrls,
    setUploadedImageUrls
  };
};
