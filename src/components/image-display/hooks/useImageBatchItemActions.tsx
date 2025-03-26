
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
  
  const handleImageClick = useCallback(() => {
    console.log(`ImageBatchItem clicked with batchId=${batchId}, batchIndex=${batchIndex}`);
    onOpenFullscreenView(batchId, batchIndex);
  }, [batchId, batchIndex, onOpenFullscreenView]);
  
  const handleUseAsInput = useCallback((e: React.MouseEvent) => {
    e.stopPropagation();
    console.log('Using image as input:', imageUrl);
    onUseGeneratedAsInput(imageUrl);
  }, [imageUrl, onUseGeneratedAsInput]);
  
  const handleDelete = useCallback((e: React.MouseEvent) => {
    e.stopPropagation();
    onDeleteImage(batchId, batchIndex);
  }, [batchId, batchIndex, onDeleteImage]);
  
  const handleCreateAgain = useCallback((e: React.MouseEvent) => {
    e.stopPropagation();
    // Pass the batchId to create again in the same batch
    onCreateAgain(batchId);
    // We no longer roll up the batch when creating again (removed this behavior)
  }, [batchId, onCreateAgain]);
  
  // Use the fullscreen handler for opening the fullscreen view
  const handleFullScreen = useCallback((e: React.MouseEvent) => {
    e.stopPropagation();
    onOpenFullscreenView(batchId, batchIndex);
  }, [batchId, batchIndex, onOpenFullscreenView]);
  
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
