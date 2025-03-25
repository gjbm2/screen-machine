
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
  
  const handleUseAsInput = useCallback(() => {
    onUseGeneratedAsInput(imageUrl);
  }, [imageUrl, onUseGeneratedAsInput]);
  
  const handleDelete = useCallback(() => {
    onDeleteImage(batchId, batchIndex);
  }, [batchId, batchIndex, onDeleteImage]);
  
  const handleCreateAgain = useCallback(() => {
    onCreateAgain(batchId);
  }, [batchId, onCreateAgain]);
  
  return {
    handleImageClick,
    handleUseAsInput,
    handleDelete,
    handleCreateAgain
  };
};
