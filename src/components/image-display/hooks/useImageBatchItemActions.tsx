
import React from 'react';

interface UseImageBatchItemActionsProps {
  image: {
    url: string;
    prompt?: string;
    workflow?: string;
    timestamp?: number;
    params?: Record<string, any>;
    batchId?: string;
    batchIndex?: number;
    status?: string;
    refiner?: string;
    referenceImageUrl?: string;
  };
  batchId: string;
  index: number;
  onCreateAgain?: (batchId: string) => void;
  onUseAsInput?: ((imageUrl: string) => void) | null;
  onDeleteImage?: (batchId: string, index: number) => void;
  onFullScreen?: (batchId: string, index: number) => void;
}

export const useImageBatchItemActions = ({
  image,
  batchId,
  index,
  onCreateAgain,
  onUseAsInput,
  onDeleteImage,
  onFullScreen
}: UseImageBatchItemActionsProps) => {
  
  const handleCreateAgain = () => {
    if (onCreateAgain) {
      onCreateAgain(batchId);
    }
  };

  const handleUseAsInput = () => {
    if (onUseAsInput && image.url) {
      onUseAsInput(image.url);
    }
  };

  const handleFullScreen = (e: React.MouseEvent) => {
    e.stopPropagation();
    if (onFullScreen) {
      onFullScreen(batchId, index);
    }
  };
  
  const handleDeleteImage = (e: React.MouseEvent) => {
    e.stopPropagation();
    e.preventDefault();
    if (onDeleteImage) {
      onDeleteImage(batchId, index);
    }
  };
  
  const handleDeleteFromPanel = () => {
    if (onDeleteImage) {
      onDeleteImage(batchId, index);
    }
  };

  return {
    handleCreateAgain,
    handleUseAsInput,
    handleFullScreen,
    handleDeleteImage,
    handleDeleteFromPanel
  };
};
