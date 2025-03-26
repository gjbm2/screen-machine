
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
    // Add the new container to the beginning of the order
    setImageContainerOrder(prev => [batchId, ...prev]);
    
    // Set this new container to be expanded by default
    setExpandedContainers(prev => ({
      ...prev,
      [batchId]: true
    }));
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

  // New function to collapse all containers except the specified one
  const collapseAllExcept = useCallback((exceptBatchId: string) => {
    setExpandedContainers(prev => {
      const result: Record<string, boolean> = {};
      // Set all containers to collapsed
      Object.keys(prev).forEach(batchId => {
        result[batchId] = false;
      });
      // Set the specified container to expanded
      result[exceptBatchId] = true;
      return result;
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
    handleDeleteContainer,
    collapseAllExcept
  };
};

export default useImageContainer;
