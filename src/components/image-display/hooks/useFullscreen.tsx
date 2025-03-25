
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
    
    const selectedImage = allImagesFlat.find(
      img => img.batchId === batchId && img.batchIndex === imageIndex
    );
    
    if (selectedImage) {
      const globalIndex = allImagesFlat.findIndex(
        img => img.batchId === batchId && img.batchIndex === imageIndex
      );
      
      if (globalIndex !== -1) {
        setCurrentGlobalIndex(globalIndex);
      }
    }
  };
  
  const handleNavigateGlobal = (index: number) => {
    if (index >= 0 && index < allImagesFlat.length) {
      const targetImage = allImagesFlat[index];
      setFullScreenBatchId(targetImage.batchId);
      setFullScreenImageIndex(targetImage.batchIndex);
      setCurrentGlobalIndex(index);
    }
  };
  
  // Add a new function for batch-aware navigation
  const handleNavigateWithBatchAwareness = (direction: 'next' | 'prev') => {
    // First, group all images by batchId
    const batchesMap = allImagesFlat.reduce((acc, img) => {
      if (!acc[img.batchId]) {
        acc[img.batchId] = [];
      }
      acc[img.batchId].push(img);
      return acc;
    }, {} as Record<string, any[]>);
    
    // Sort batch IDs to maintain consistent order
    const batchIds = Object.keys(batchesMap).sort();
    
    if (!fullScreenBatchId || !batchIds.includes(fullScreenBatchId)) {
      console.error('Cannot navigate: Invalid batch ID');
      return;
    }
    
    // Get current batch images
    const currentBatchImages = batchesMap[fullScreenBatchId];
    
    // Sort images within the batch by batchIndex
    currentBatchImages.sort((a, b) => a.batchIndex - b.batchIndex);
    
    if (direction === 'next') {
      // Check if we can move to the next image in the current batch
      const nextIndexInBatch = currentBatchImages.findIndex(img => img.batchIndex === fullScreenImageIndex) + 1;
      
      if (nextIndexInBatch < currentBatchImages.length) {
        // Move to the next image in the same batch
        const nextImage = currentBatchImages[nextIndexInBatch];
        setFullScreenImageIndex(nextImage.batchIndex);
        
        // Update global index
        const globalIndex = allImagesFlat.findIndex(
          img => img.batchId === fullScreenBatchId && img.batchIndex === nextImage.batchIndex
        );
        if (globalIndex !== -1) {
          setCurrentGlobalIndex(globalIndex);
        }
      } else {
        // Move to the first image of the next batch
        const currentBatchIndex = batchIds.indexOf(fullScreenBatchId);
        if (currentBatchIndex < batchIds.length - 1) {
          const nextBatchId = batchIds[currentBatchIndex + 1];
          const nextBatchImages = batchesMap[nextBatchId];
          
          // Sort images in the next batch
          nextBatchImages.sort((a, b) => a.batchIndex - b.batchIndex);
          
          // Get the first image in the next batch
          const firstImageInNextBatch = nextBatchImages[0];
          
          setFullScreenBatchId(nextBatchId);
          setFullScreenImageIndex(firstImageInNextBatch.batchIndex);
          
          // Update global index
          const globalIndex = allImagesFlat.findIndex(
            img => img.batchId === nextBatchId && img.batchIndex === firstImageInNextBatch.batchIndex
          );
          
          if (globalIndex !== -1) {
            setCurrentGlobalIndex(globalIndex);
          }
        }
      }
    } else if (direction === 'prev') {
      // Check if we can move to the previous image in the current batch
      const currentIndexInBatch = currentBatchImages.findIndex(img => img.batchIndex === fullScreenImageIndex);
      
      if (currentIndexInBatch > 0) {
        // Move to the previous image in the same batch
        const prevImage = currentBatchImages[currentIndexInBatch - 1];
        setFullScreenImageIndex(prevImage.batchIndex);
        
        // Update global index
        const globalIndex = allImagesFlat.findIndex(
          img => img.batchId === fullScreenBatchId && img.batchIndex === prevImage.batchIndex
        );
        
        if (globalIndex !== -1) {
          setCurrentGlobalIndex(globalIndex);
        }
      } else {
        // Move to the last image of the previous batch
        const currentBatchIndex = batchIds.indexOf(fullScreenBatchId);
        if (currentBatchIndex > 0) {
          const prevBatchId = batchIds[currentBatchIndex - 1];
          const prevBatchImages = batchesMap[prevBatchId];
          
          // Sort images in the previous batch
          prevBatchImages.sort((a, b) => a.batchIndex - b.batchIndex);
          
          // Get the last image in the previous batch
          const lastImageInPrevBatch = prevBatchImages[prevBatchImages.length - 1];
          
          setFullScreenBatchId(prevBatchId);
          setFullScreenImageIndex(lastImageInPrevBatch.batchIndex);
          
          // Update global index
          const globalIndex = allImagesFlat.findIndex(
            img => img.batchId === prevBatchId && img.batchIndex === lastImageInPrevBatch.batchIndex
          );
          
          if (globalIndex !== -1) {
            setCurrentGlobalIndex(globalIndex);
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
    handleNavigateGlobal,
    handleNavigateWithBatchAwareness
  };
};

export default useFullscreen;
