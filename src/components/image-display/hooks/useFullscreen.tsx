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
      img => img.batchId === batchId && img.batchIndex === imageIndex
    );
    
    if (selectedImage) {
      console.log(`Found selected image with batchIndex=${imageIndex}:`, selectedImage);
      const globalIndex = allImagesFlat.findIndex(
        img => img.batchId === batchId && img.batchIndex === imageIndex
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
    const batchesMap = allImagesFlat.reduce((acc, img) => {
      if (!acc[img.batchId]) {
        acc[img.batchId] = [];
      }
      acc[img.batchId].push(img);
      return acc;
    }, {} as Record<string, any[]>);
    
    const batchIds = Object.keys(batchesMap).sort();
    
    if (!fullScreenBatchId || !batchIds.includes(fullScreenBatchId)) {
      console.error('Cannot navigate: Invalid batch ID');
      return;
    }
    
    const currentBatchImages = batchesMap[fullScreenBatchId];
    
    currentBatchImages.sort((a, b) => a.batchIndex - b.batchIndex);
    
    if (direction === 'next') {
      const nextIndexInBatch = currentBatchImages.findIndex(img => img.batchIndex === fullScreenImageIndex) + 1;
      
      if (nextIndexInBatch < currentBatchImages.length) {
        const nextImage = currentBatchImages[nextIndexInBatch];
        setFullScreenImageIndex(nextImage.batchIndex);
        
        const globalIndex = allImagesFlat.findIndex(
          img => img.batchId === fullScreenBatchId && img.batchIndex === nextImage.batchIndex
        );
        if (globalIndex !== -1) {
          setCurrentGlobalIndex(globalIndex);
        }
      } else {
        const currentBatchIndex = batchIds.indexOf(fullScreenBatchId);
        if (currentBatchIndex < batchIds.length - 1) {
          const nextBatchId = batchIds[currentBatchIndex + 1];
          const nextBatchImages = batchesMap[nextBatchId];
          
          nextBatchImages.sort((a, b) => a.batchIndex - b.batchIndex);
          
          const firstImageInNextBatch = nextBatchImages[0];
          
          setFullScreenBatchId(nextBatchId);
          setFullScreenImageIndex(firstImageInNextBatch.batchIndex);
          
          const globalIndex = allImagesFlat.findIndex(
            img => img.batchId === nextBatchId && img.batchIndex === firstImageInNextBatch.batchIndex
          );
          
          if (globalIndex !== -1) {
            setCurrentGlobalIndex(globalIndex);
          }
        }
      }
    } else if (direction === 'prev') {
      const currentIndexInBatch = currentBatchImages.findIndex(img => img.batchIndex === fullScreenImageIndex);
      
      if (currentIndexInBatch > 0) {
        const prevImage = currentBatchImages[currentIndexInBatch - 1];
        setFullScreenImageIndex(prevImage.batchIndex);
        
        const globalIndex = allImagesFlat.findIndex(
          img => img.batchId === fullScreenBatchId && img.batchIndex === prevImage.batchIndex
        );
        
        if (globalIndex !== -1) {
          setCurrentGlobalIndex(globalIndex);
        }
      } else {
        const currentBatchIndex = batchIds.indexOf(fullScreenBatchId);
        if (currentBatchIndex > 0) {
          const prevBatchId = batchIds[currentBatchIndex - 1];
          const prevBatchImages = batchesMap[prevBatchId];
          
          prevBatchImages.sort((a, b) => a.batchIndex - b.batchIndex);
          
          const lastImageInPrevBatch = prevBatchImages[prevBatchImages.length - 1];
          
          setFullScreenBatchId(prevBatchId);
          setFullScreenImageIndex(lastImageInPrevBatch.batchIndex);
          
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
