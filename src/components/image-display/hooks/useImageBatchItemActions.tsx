
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
    console.log(`IMPORTANT: This function should NOT trigger fullscreen in normal view`);
    // This function should never trigger fullscreen in normal view
    // The fullscreen functionality is ONLY handled via the dedicated fullscreen button
  }, [batchId, batchIndex]);
  
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
  
  // Use the fullscreen handler for opening the fullscreen view
  const handleFullScreen = useCallback((e: React.MouseEvent) => {
    e.stopPropagation();
    console.log(`Opening fullscreen view for batchId=${batchId}, batchIndex=${batchIndex}`);
    console.log(`This is triggered ONLY by the fullscreen button in normal view!`);
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
