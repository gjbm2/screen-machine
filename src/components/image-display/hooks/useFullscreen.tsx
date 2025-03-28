
import { useState } from 'react';

export const useFullscreen = (allImagesFlat: any[]) => {
  const [showFullScreenView, setShowFullScreenView] = useState(false);
  const [fullScreenBatchId, setFullScreenBatchId] = useState<string | null>(null);
  const [fullScreenImageIndex, setFullScreenImageIndex] = useState(0);
  const [currentGlobalIndex, setCurrentGlobalIndex] = useState<number | null>(null);

  const openFullScreenView = (batchId: string, imageIndex: number = 0) => {
    console.log(`Opening fullscreen view for batchId=${batchId}, requesting imageIndex=${imageIndex}`);
    setFullScreenBatchId(batchId);
    setFullScreenImageIndex(imageIndex);
    setShowFullScreenView(true);
    
    // Log images in this batch for debugging
    const batchImages = allImagesFlat.filter(img => img.batchId === batchId);
    console.log(`Batch ${batchId} contains ${batchImages.length} images with indexes:`, 
      batchImages.map(img => ({batchId: img.batchId, batchIndex: img.batchIndex})));
    
    const selectedImage = allImagesFlat.find(
      img => img.batchId === batchId && Number(img.batchIndex) === Number(imageIndex)
    );
    
    if (selectedImage) {
      console.log(`Found selected image with batchIndex=${imageIndex}:`, selectedImage);
      const globalIndex = allImagesFlat.findIndex(
        img => img.batchId === batchId && Number(img.batchIndex) === Number(imageIndex)
      );
      
      if (globalIndex !== -1) {
        console.log(`Setting currentGlobalIndex to ${globalIndex}`);
        setCurrentGlobalIndex(globalIndex);
      }
    } else {
      console.warn(`Could not find image with batchId=${batchId} and batchIndex=${imageIndex}!`);
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
  
  const handleNavigateWithBatchAwareness = (direction: 'next' | 'prev') => {
    // Add debug logging to help track the issue
    console.log(`Navigation: ${direction} button clicked`);
    console.log(`Current state: batchId=${fullScreenBatchId}, imageIndex=${fullScreenImageIndex}, globalIndex=${currentGlobalIndex}`);
    
    const batchesMap = allImagesFlat.reduce((acc, img) => {
      if (!acc[img.batchId]) {
        acc[img.batchId] = [];
      }
      acc[img.batchId].push(img);
      return acc;
    }, {} as Record<string, any[]>);
    
    const batchIds = Object.keys(batchesMap).sort();
    console.log(`Available batch IDs: ${batchIds.join(', ')}`);
    
    if (!fullScreenBatchId || !batchIds.includes(fullScreenBatchId)) {
      console.error('Cannot navigate: Invalid batch ID');
      return;
    }
    
    const currentBatchImages = batchesMap[fullScreenBatchId];
    console.log(`Current batch ${fullScreenBatchId} has ${currentBatchImages.length} images`);
    
    // Sort images by batch index (ensuring they're converted to numbers)
    currentBatchImages.sort((a, b) => Number(a.batchIndex) - Number(b.batchIndex));
    
    // Log the available batch indexes for debugging
    console.log('Available batch indexes:', currentBatchImages.map(img => ({
      batchIndex: img.batchIndex, 
      type: typeof img.batchIndex
    })));
    console.log(`Looking for image with batchIndex=${fullScreenImageIndex}, type=${typeof fullScreenImageIndex}`);
    
    if (direction === 'next') {
      // FIX: Convert both sides to number for consistent comparison
      const nextIndexInBatch = currentBatchImages.findIndex(img => Number(img.batchIndex) === Number(fullScreenImageIndex)) + 1;
      console.log(`Found at position ${nextIndexInBatch - 1}, next image position: ${nextIndexInBatch}`);
      
      if (nextIndexInBatch < currentBatchImages.length) {
        const nextImage = currentBatchImages[nextIndexInBatch];
        console.log(`Navigating to next image in batch: ${nextImage.batchIndex}`);
        setFullScreenImageIndex(nextImage.batchIndex);
        
        const globalIndex = allImagesFlat.findIndex(
          img => img.batchId === fullScreenBatchId && Number(img.batchIndex) === Number(nextImage.batchIndex)
        );
        if (globalIndex !== -1) {
          setCurrentGlobalIndex(globalIndex);
        }
      } else {
        const currentBatchIndex = batchIds.indexOf(fullScreenBatchId);
        if (currentBatchIndex < batchIds.length - 1) {
          const nextBatchId = batchIds[currentBatchIndex + 1];
          const nextBatchImages = batchesMap[nextBatchId];
          
          nextBatchImages.sort((a, b) => Number(a.batchIndex) - Number(b.batchIndex));
          
          const firstImageInNextBatch = nextBatchImages[0];
          console.log(`Navigating to first image in next batch: ${nextBatchId}, index ${firstImageInNextBatch.batchIndex}`);
          
          setFullScreenBatchId(nextBatchId);
          setFullScreenImageIndex(firstImageInNextBatch.batchIndex);
          
          const globalIndex = allImagesFlat.findIndex(
            img => img.batchId === nextBatchId && Number(img.batchIndex) === Number(firstImageInNextBatch.batchIndex)
          );
          
          if (globalIndex !== -1) {
            setCurrentGlobalIndex(globalIndex);
          }
        }
      }
    } else if (direction === 'prev') {
      // FIX: Convert both sides to number for consistent comparison
      const currentIndexInBatch = currentBatchImages.findIndex(img => Number(img.batchIndex) === Number(fullScreenImageIndex));
      console.log(`Found at position ${currentIndexInBatch} in current batch`);
      
      if (currentIndexInBatch > 0) {
        const prevImage = currentBatchImages[currentIndexInBatch - 1];
        console.log(`Navigating to previous image in batch: ${prevImage.batchIndex}`);
        setFullScreenImageIndex(prevImage.batchIndex);
        
        const globalIndex = allImagesFlat.findIndex(
          img => img.batchId === fullScreenBatchId && Number(img.batchIndex) === Number(prevImage.batchIndex)
        );
        
        if (globalIndex !== -1) {
          setCurrentGlobalIndex(globalIndex);
        }
      } else {
        const currentBatchIndex = batchIds.indexOf(fullScreenBatchId);
        if (currentBatchIndex > 0) {
          const prevBatchId = batchIds[currentBatchIndex - 1];
          const prevBatchImages = batchesMap[prevBatchId];
          
          prevBatchImages.sort((a, b) => Number(a.batchIndex) - Number(b.batchIndex));
          
          const lastImageInPrevBatch = prevBatchImages[prevBatchImages.length - 1];
          console.log(`Navigating to last image in previous batch: ${prevBatchId}, index ${lastImageInPrevBatch.batchIndex}`);
          
          setFullScreenBatchId(prevBatchId);
          setFullScreenImageIndex(lastImageInPrevBatch.batchIndex);
          
          const globalIndex = allImagesFlat.findIndex(
            img => img.batchId === prevBatchId && Number(img.batchIndex) === Number(lastImageInPrevBatch.batchIndex)
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
