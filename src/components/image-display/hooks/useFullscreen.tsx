
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
  
  // Group images by batch ID to support batch-aware navigation
  const getImagesByBatch = () => {
    const batchMap: Record<string, any[]> = {};
    
    allImagesFlat.forEach(image => {
      if (!batchMap[image.batchId]) {
        batchMap[image.batchId] = [];
      }
      batchMap[image.batchId].push(image);
    });
    
    // Sort images within each batch by batchIndex
    Object.keys(batchMap).forEach(batchId => {
      batchMap[batchId].sort((a, b) => a.batchIndex - b.batchIndex);
    });
    
    return batchMap;
  };
  
  // Get ordered batch IDs
  const getOrderedBatchIds = () => {
    const uniqueBatchIds = Array.from(new Set(allImagesFlat.map(img => img.batchId)));
    // Sort batch IDs by the timestamp of their first image (newest first)
    return uniqueBatchIds.sort((a, b) => {
      const aTimestamp = allImagesFlat.find(img => img.batchId === a)?.timestamp || 0;
      const bTimestamp = allImagesFlat.find(img => img.batchId === b)?.timestamp || 0;
      return bTimestamp - aTimestamp; // Newest first
    });
  };
  
  // Navigate between images while respecting batch boundaries
  const handleNavigateWithBatchAwareness = (direction: 'next' | 'prev') => {
    if (!fullScreenBatchId || allImagesFlat.length === 0) return;
    
    const batchMap = getImagesByBatch();
    const orderedBatchIds = getOrderedBatchIds();
    const currentBatchImages = batchMap[fullScreenBatchId] || [];
    const currentBatchIndex = orderedBatchIds.indexOf(fullScreenBatchId);
    
    if (direction === 'next') {
      // Check if we can go to the next image in the current batch
      if (fullScreenImageIndex < currentBatchImages.length - 1) {
        // Move to the next image in the current batch
        const nextImageIndex = fullScreenImageIndex + 1;
        setFullScreenImageIndex(nextImageIndex);
        
        // Update global index
        const nextGlobalImage = allImagesFlat.findIndex(
          img => img.batchId === fullScreenBatchId && img.batchIndex === nextImageIndex
        );
        if (nextGlobalImage !== -1) {
          setCurrentGlobalIndex(nextGlobalImage);
        }
      } else if (currentBatchIndex < orderedBatchIds.length - 1) {
        // Move to the first image of the next batch
        const nextBatchId = orderedBatchIds[currentBatchIndex + 1];
        if (batchMap[nextBatchId] && batchMap[nextBatchId].length > 0) {
          setFullScreenBatchId(nextBatchId);
          setFullScreenImageIndex(0);
          
          // Update global index
          const nextGlobalImage = allImagesFlat.findIndex(
            img => img.batchId === nextBatchId && img.batchIndex === 0
          );
          if (nextGlobalImage !== -1) {
            setCurrentGlobalIndex(nextGlobalImage);
          }
        }
      }
    } else if (direction === 'prev') {
      // Check if we can go to the previous image in the current batch
      if (fullScreenImageIndex > 0) {
        // Move to the previous image in the current batch
        const prevImageIndex = fullScreenImageIndex - 1;
        setFullScreenImageIndex(prevImageIndex);
        
        // Update global index
        const prevGlobalImage = allImagesFlat.findIndex(
          img => img.batchId === fullScreenBatchId && img.batchIndex === prevImageIndex
        );
        if (prevGlobalImage !== -1) {
          setCurrentGlobalIndex(prevGlobalImage);
        }
      } else if (currentBatchIndex > 0) {
        // Move to the last image of the previous batch
        const prevBatchId = orderedBatchIds[currentBatchIndex - 1];
        if (batchMap[prevBatchId] && batchMap[prevBatchId].length > 0) {
          const lastImageIndex = batchMap[prevBatchId].length - 1;
          setFullScreenBatchId(prevBatchId);
          setFullScreenImageIndex(lastImageIndex);
          
          // Update global index
          const prevGlobalImage = allImagesFlat.findIndex(
            img => img.batchId === prevBatchId && img.batchIndex === lastImageIndex
          );
          if (prevGlobalImage !== -1) {
            setCurrentGlobalIndex(prevGlobalImage);
          }
        }
      }
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
    handleNavigateGlobal: setCurrentGlobalIndex,
    handleNavigateWithBatchAwareness
  };
};

export default useFullscreen;
