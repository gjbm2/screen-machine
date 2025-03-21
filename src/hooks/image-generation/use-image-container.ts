
import { useState } from 'react';
import { GeneratedImage } from './types';

export const useImageContainer = () => {
  const [imageContainerOrder, setImageContainerOrder] = useState<string[]>([]);
  const [nextContainerId, setNextContainerId] = useState<number>(1);

  const handleReorderContainers = (sourceIndex: number, destinationIndex: number) => {
    setImageContainerOrder(prev => {
      const newOrder = [...prev];
      const [removed] = newOrder.splice(sourceIndex, 1);
      newOrder.splice(destinationIndex, 0, removed);
      return newOrder;
    });
  };

  const handleDeleteContainer = (batchId: string, setGeneratedImages: React.Dispatch<React.SetStateAction<GeneratedImage[]>>) => {
    setGeneratedImages(prev => prev.filter(img => img.batchId !== batchId));
    setImageContainerOrder(prev => prev.filter(id => id !== batchId));
  };

  return {
    imageContainerOrder,
    setImageContainerOrder,
    nextContainerId,
    setNextContainerId,
    handleReorderContainers,
    handleDeleteContainer
  };
};

export default useImageContainer;
