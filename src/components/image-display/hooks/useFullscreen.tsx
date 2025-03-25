
import { useState } from 'react';

export const useFullscreen = (allImagesFlat: any[]) => {
  const [showFullScreenView, setShowFullScreenView] = useState(false);
  const [fullScreenBatchId, setFullScreenBatchId] = useState<string | null>(null);
  const [fullScreenImageIndex, setFullScreenImageIndex] = useState(0);
  const [currentGlobalIndex, setCurrentGlobalIndex] = useState<number | null>(null);

  const openFullScreenView = (batchId: string, imageIndex: number = 0) => {
    setFullScreenBatchId(batchId);
    setFullScreenImageIndex(imageIndex);
    setShowFullScreenView(true);
    
    // Find the exact image in our flat list of all images
    const selectedImage = allImagesFlat.find(
      img => img.batchId === batchId && img.batchIndex === imageIndex
    );
    
    if (selectedImage) {
      // Find the global index of this image in the flat list
      const globalIndex = allImagesFlat.findIndex(
        img => img.batchId === batchId && img.batchIndex === imageIndex
      );
      
      if (globalIndex !== -1) {
        setCurrentGlobalIndex(globalIndex);
      }
    }
  };
  
  // This function allows navigation between all images regardless of batch
  const handleNavigateGlobal = (index: number) => {
    if (index >= 0 && index < allImagesFlat.length) {
      const targetImage = allImagesFlat[index];
      
      // Update the batch ID and image index to match the target image
      setFullScreenBatchId(targetImage.batchId);
      setFullScreenImageIndex(targetImage.batchIndex);
      
      // Update the global index tracking
      setCurrentGlobalIndex(index);
      
      console.log(`Navigating to global image ${index}:`, targetImage);
    }
  };

  return {
    showFullScreenView,
    setShowFullScreenView,
    fullScreenBatchId,
    fullScreenImageIndex,
    setFullScreenImageIndex,
    currentGlobalIndex,
    openFullScreenView,
    handleNavigateGlobal
  };
};

export default useFullscreen;
