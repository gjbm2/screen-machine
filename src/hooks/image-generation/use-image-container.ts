
import { useState, useCallback } from 'react';

export const useImageContainer = () => {
  const [imageContainerOrder, setImageContainerOrder] = useState<string[]>([]);
  const [expandedContainers, setExpandedContainers] = useState<Record<string, boolean>>({});
  const [nextContainerId, setNextContainerId] = useState(1);

  const handleReorderContainers = useCallback((sourceIndex: number, destinationIndex: number) => {
    setImageContainerOrder(prev => {
      const result = Array.from(prev);
      const [removed] = result.splice(sourceIndex, 1);
      result.splice(destinationIndex, 0, removed);
      return result;
    });
  }, []);

  const handleAddNewContainer = useCallback((batchId: string) => {
    // When adding a new container, collapse all existing ones and expand the new one
    setExpandedContainers(prev => {
      // Create a completely new object instead of modifying the existing one
      const newState: Record<string, boolean> = {};
      
      // First set all existing containers to collapsed
      Object.keys(prev).forEach(key => {
        newState[key] = false;
      });
      
      // Then specifically set the new container to expanded
      newState[batchId] = true;
      
      return newState;
    });
    
    setImageContainerOrder(prev => [batchId, ...prev]);
  }, []);

  const handleDeleteContainer = useCallback((batchId: string, setGeneratedImages: Function) => {
    setImageContainerOrder(prev => prev.filter(id => id !== batchId));
    setExpandedContainers(prev => {
      const { [batchId]: removed, ...rest } = prev;
      return rest;
    });
    setGeneratedImages((prev: any) => {
      const { [batchId]: removed, ...rest } = prev;
      return rest;
    });
  }, []);

  return {
    imageContainerOrder,
    setImageContainerOrder,
    expandedContainers,
    setExpandedContainers,
    nextContainerId,
    setNextContainerId,
    handleReorderContainers,
    handleAddNewContainer,
    handleDeleteContainer
  };
};

export default useImageContainer;
