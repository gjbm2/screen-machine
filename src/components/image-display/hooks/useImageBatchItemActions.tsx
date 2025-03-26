
import { useCallback } from 'react';

interface UseImageBatchItemActionsProps {
  batchId: string;
  batchIndex: number;
  onOpenFullscreenView: (batchId: string, imageIndex: number) => void;
  onUseGeneratedAsInput: (url: string) => void;
  onDeleteImage: (batchId: string, imageIndex: number) => void;
  onCreateAgain: (batchId: string) => void;
  imageUrl: string;
}

export const useImageBatchItemActions = ({
  batchId,
  batchIndex,
  onOpenFullscreenView,
  onUseGeneratedAsInput,
  onDeleteImage,
  onCreateAgain,
  imageUrl
}: UseImageBatchItemActionsProps) => {
  
  // This handler is now ONLY used by the Fullscreen button, not general image clicks
  const handleFullScreen = useCallback((e: React.MouseEvent) => {
    e.stopPropagation();
    onOpenFullscreenView(batchId, batchIndex);
  }, [batchId, batchIndex, onOpenFullscreenView]);
  
  const handleUseAsInput = useCallback((e: React.MouseEvent) => {
    e.stopPropagation();
    console.log('Using image as input:', imageUrl);
    onUseGeneratedAsInput(imageUrl);
  }, [imageUrl, onUseGeneratedAsInput]);
  
  const handleDelete = useCallback((e: React.MouseEvent) => {
    e.stopPropagation();
    console.log(`Deleting image in batch ${batchId} at index ${batchIndex}`);
    onDeleteImage(batchId, batchIndex);
  }, [batchId, batchIndex, onDeleteImage]);
  
  const handleCreateAgain = useCallback((e: React.MouseEvent) => {
    e.stopPropagation();
    // Pass the batchId to create again in the same batch
    console.log(`Creating again in batch ${batchId}`);
    onCreateAgain(batchId);
  }, [batchId, onCreateAgain]);
  
  // No longer used for general image clicks
  const handleImageClick = useCallback(() => {
    console.log(`ImageBatchItem clicked with batchId=${batchId}, batchIndex=${batchIndex}`);
    // We no longer call onOpenFullscreenView here to prevent fullscreen on image click
  }, [batchId, batchIndex]);
  
  const handleDeleteImage = handleDelete;
  const handleDeleteFromPanel = handleDelete;
  
  return {
    handleImageClick,
    handleUseAsInput,
    handleDelete,
    handleCreateAgain,
    handleFullScreen,
    handleDeleteImage,
    handleDeleteFromPanel
  };
};
