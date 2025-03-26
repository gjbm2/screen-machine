
import { useState, useEffect } from 'react';
import useFullscreen from './useFullscreen';
import useImageSort from './useImageSort';

export const useImageDisplayState = (
  imageContainerOrder: string[],
  generatedImages: any[],
  isLoading: boolean
) => {
  // Initialize with all containers expanded by default
  const [viewMode, setViewMode] = useState<'normal' | 'small' | 'table'>('normal');
  const [expandedContainers, setExpandedContainers] = useState<Record<string, boolean>>({});
  const [allImagesFlat, setAllImagesFlat] = useState<any[]>([]);

  // Initialize all containers as expanded when container order changes
  useEffect(() => {
    if (imageContainerOrder.length > 0) {
      const expanded: Record<string, boolean> = {};
      imageContainerOrder.forEach(batchId => {
        expanded[batchId] = true; // Set all containers to expanded by default
      });
      setExpandedContainers(prev => ({
        ...prev,
        ...expanded
      }));
    }
  }, [imageContainerOrder]);

  const { 
    showFullScreenView, 
    setShowFullScreenView,
    fullScreenBatchId,
    fullScreenImageIndex,
    setFullScreenImageIndex,
    currentGlobalIndex,
    openFullScreenView,
    handleNavigateGlobal,
    handleNavigateWithBatchAwareness
  } = useFullscreen(allImagesFlat);

  useEffect(() => {
    const allImages = generatedImages
      .filter(img => img.status === 'completed' || img.status === 'failed' || img.status === 'error')
      .map(img => ({
        url: img.url,
        prompt: img.prompt,
        batchId: img.batchId,
        batchIndex: img.batchIndex,
        workflow: img.workflow,
        timestamp: img.timestamp,
        referenceImageUrl: img.referenceImageUrl,
        params: img.params,
        refiner: img.refiner
      }))
      .sort((a, b) => b.timestamp - a.timestamp);
    
    setAllImagesFlat(allImages);
  }, [generatedImages]);

  const handleToggleExpand = (batchId: string) => {
    setExpandedContainers(prev => ({
      ...prev,
      [batchId]: !prev[batchId]
    }));
  };
  
  const getImageBatches = () => {
    const batches: Record<string, any[]> = {};
    
    generatedImages.forEach(image => {
      if (!batches[image.batchId]) {
        batches[image.batchId] = [];
      }
      batches[image.batchId].push(image);
    });
    
    return batches;
  };

  const batches = getImageBatches();
  const hasBatches = Object.keys(batches).length > 0 || isLoading;
  
  // CRITICAL: Modify these handlers to ensure normal view clicks never trigger fullscreen
  
  // This is used for small view mode only, not normal mode
  const handleSmallImageClick = (image: any) => {
    if (image?.url && image.batchId && viewMode === 'small') {
      // Only proceed with fullscreen if in small mode
      const batchImages = batches[image.batchId]?.filter(img => img.status === 'completed') || [];
      const batchIndex = batchImages.findIndex(img => img.batchIndex === image.batchIndex);
      const indexToUse = batchIndex !== -1 ? batchIndex : 0;
      openFullScreenView(image.batchId, indexToUse);
    }
    // In normal mode, do nothing to open fullscreen
  };

  // This is used for table view only, not normal view
  const handleTableRowClick = (batchId: string) => {
    if (viewMode === 'table') {
      const batchImages = batches[batchId]?.filter(img => img.status === 'completed');
      if (batchImages && batchImages.length > 0) {
        // Only open fullscreen in table view
        openFullScreenView(batchId, 0);
      } else {
        setExpandedContainers(prev => ({
          ...prev,
          [batchId]: true
        }));
      }
    }
    // In normal mode, only expand the container
    else {
      setExpandedContainers(prev => ({
        ...prev,
        [batchId]: true
      }));
    }
  };
  
  const getAllImages = () => {
    // Return all individual images instead of just one per batch
    return generatedImages
      .filter(img => img.status === 'completed' || img.status === 'generating' || img.status === 'failed' || img.status === 'error')
      .sort((a, b) => {
        if (a.status === 'generating' && b.status !== 'generating') return -1;
        if (a.status !== 'generating' && b.status === 'generating') return 1;
        
        if (a.status === 'generating' && b.status === 'generating') {
          return (b.timestamp || 0) - (a.timestamp || 0);
        }
        
        return (b.timestamp || 0) - (a.timestamp || 0);
      });
  };

  const { 
    sortField, 
    sortDirection, 
    handleSortClick, 
    getSortedContainers 
  } = useImageSort(imageContainerOrder, batches);

  return {
    viewMode,
    setViewMode,
    expandedContainers,
    allImagesFlat,
    showFullScreenView,
    setShowFullScreenView,
    fullScreenBatchId,
    fullScreenImageIndex,
    setFullScreenImageIndex,
    currentGlobalIndex,
    openFullScreenView,
    handleNavigateGlobal,
    handleNavigateWithBatchAwareness,
    handleToggleExpand,
    batches,
    hasBatches,
    handleSmallImageClick,
    handleTableRowClick,
    getAllImages,
    sortField,
    sortDirection,
    handleSortClick,
    getSortedContainers
  };
};

export default useImageDisplayState;
