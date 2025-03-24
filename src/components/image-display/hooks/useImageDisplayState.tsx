import { useState, useEffect } from 'react';
import useFullscreen from './useFullscreen';
import useImageSort from './useImageSort';

export const useImageDisplayState = (
  imageContainerOrder: string[],
  generatedImages: any[],
  isLoading: boolean
) => {
  const [viewMode, setViewMode] = useState<'normal' | 'small' | 'table'>('normal');
  const [expandedContainers, setExpandedContainers] = useState<Record<string, boolean>>({});
  const [allImagesFlat, setAllImagesFlat] = useState<any[]>([]);

  const { 
    showFullScreenView, 
    setShowFullScreenView,
    fullScreenBatchId,
    fullScreenImageIndex,
    setFullScreenImageIndex,
    currentGlobalIndex,
    openFullScreenView,
    handleNavigateGlobal
  } = useFullscreen(allImagesFlat);

  useEffect(() => {
    if (imageContainerOrder.length > 0) {
      if (isLoading) {
        const updatedExpandedState: Record<string, boolean> = {};
        
        imageContainerOrder.forEach(id => {
          updatedExpandedState[id] = false;
        });
        
        updatedExpandedState[imageContainerOrder[0]] = true;
        
        setExpandedContainers(updatedExpandedState);
        
        const container = document.getElementById(imageContainerOrder[0]);
        if (container) {
          container.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
        }
      }
    }
  }, [imageContainerOrder, isLoading]);
  
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
  
  const handleSmallImageClick = (image: any) => {
    if (image?.url && image.batchId) {
      openFullScreenView(image.batchId, image.batchIndex || 0);
    }
  };

  const handleTableRowClick = (batchId: string) => {
    const batchImages = batches[batchId]?.filter(img => img.status === 'completed');
    if (batchImages && batchImages.length === 1) {
      openFullScreenView(batchId, 0);
    } else {
      setExpandedContainers(prev => ({
        ...prev,
        [batchId]: true
      }));
    }
  };
  
  const getAllImages = () => {
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
