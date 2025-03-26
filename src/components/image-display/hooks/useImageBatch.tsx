
import { useState, useEffect } from 'react';
import { useIsMobile } from '@/hooks/use-mobile';
import { ImageGenerationStatus } from '@/types/workflows';
import { ViewMode } from '../ImageDisplay';

interface Image {
  url: string;
  prompt?: string;
  workflow: string;
  batchIndex: number;
  status: ImageGenerationStatus;
  referenceImageUrl?: string;
}

interface UseImageBatchProps {
  batchId: string;
  images: Image[];
  hasGeneratingImages?: boolean;
}

export const useImageBatch = ({ 
  batchId, 
  images, 
  hasGeneratingImages = false 
}: UseImageBatchProps) => {
  const [showDeleteDialog, setShowDeleteDialog] = useState(false);
  const [activeImageIndex, setActiveImageIndex] = useState(0);
  const isMobile = useIsMobile();

  // Ensure the activeImageIndex is always within bounds when images array changes
  useEffect(() => {
    if (images.length > 0 && activeImageIndex >= images.length) {
      console.log(`ImageBatch: Resetting activeImageIndex from ${activeImageIndex} to 0 because images.length=${images.length}`);
      setActiveImageIndex(0);
    }
  }, [images, activeImageIndex]);
  
  // If this is a new batch with generating images, auto-scroll to it in mobile view
  useEffect(() => {
    if (isMobile && hasGeneratingImages) {
      console.log(`Auto-scrolling to batch ${batchId} with generating images on mobile`);
      
      // Scroll this batch into view
      setTimeout(() => {
        const element = document.getElementById(batchId);
        if (element) {
          element.scrollIntoView({ behavior: 'smooth', block: 'start' });
        }
      }, 100);
    }
  }, [batchId, hasGeneratingImages, isMobile]);

  // Filter images by their status
  const getFilteredImages = () => {
    if (!images || images.length === 0) return {
      anyGenerating: false,
      completedImages: [],
      failedImages: [],
      generatingImages: [],
      allNonCompletedImages: []
    };

    const anyGenerating = images.some(img => img.status === 'generating') || hasGeneratingImages;
    const completedImages = images.filter(img => img.status === 'completed');
    const failedImages = images.filter(img => img.status === 'failed' || img.status === 'error');
    const generatingImages = images.filter(img => img.status === 'generating');
    
    const allNonCompletedImages = [...failedImages, ...generatingImages];

    return {
      anyGenerating,
      completedImages,
      failedImages,
      generatingImages,
      allNonCompletedImages
    };
  };

  // Calculate safe active index
  const getSafeActiveIndex = (completedImages: Image[]) => {
    return completedImages.length > 0 
      ? Math.min(Math.max(0, activeImageIndex), completedImages.length - 1) 
      : 0;
  };

  return {
    showDeleteDialog,
    setShowDeleteDialog,
    activeImageIndex,
    setActiveImageIndex,
    isMobile,
    getFilteredImages,
    getSafeActiveIndex
  };
};

export default useImageBatch;
