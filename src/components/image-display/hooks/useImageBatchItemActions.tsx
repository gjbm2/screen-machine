
import React from 'react';

interface UseImageBatchItemActionsProps {
  image: {
    url: string;
    batchId?: string;
    batchIndex?: number;
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
    if (onDeleteImage) {
      if (window.confirm('Are you sure you want to delete this image?')) {
        onDeleteImage(batchId, index);
      }
    }
  };

  const handleDeleteFromPanel = () => {
    if (onDeleteImage) {
      if (window.confirm('Are you sure you want to delete this image?')) {
        onDeleteImage(batchId, index);
      }
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

export default useImageBatchItemActions;
