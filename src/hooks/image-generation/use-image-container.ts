
import { useState, useCallback, useEffect } from 'react';
import { useIsMobile } from '@/hooks/use-mobile';

export const useImageContainer = () => {
  const [imageContainerOrder, setImageContainerOrder] = useState<string[]>([]);
  const [expandedContainers, setExpandedContainers] = useState<Record<string, boolean>>({});
  const [nextContainerId, setNextContainerId] = useState(1);
  const isMobile = useIsMobile();

  const handleReorderContainers = useCallback((sourceIndex: number, destinationIndex: number) => {
    setImageContainerOrder(prev => {
      const result = Array.from(prev);
      const [removed] = result.splice(sourceIndex, 1);
      result.splice(destinationIndex, 0, removed);
      return result;
    });
  }, []);

  const handleAddNewContainer = useCallback((batchId: string) => {
    // When adding a new container, ALWAYS expand it (removed collapsing others)
    setExpandedContainers(prev => ({
      ...prev,
      [batchId]: true // Set new container to expanded
    }));
    
    setImageContainerOrder(prev => [batchId, ...prev]);
    
    // On mobile, scroll to the new container after a short delay to allow DOM updates
    if (isMobile) {
      setTimeout(() => {
        console.log(`[Mobile] Scrolling to newly added container ${batchId}`);
        const container = document.getElementById(batchId);
        if (container) {
          container.scrollIntoView({ behavior: 'smooth', block: 'start' });
        }
      }, 100);
    }
  }, [isMobile]);

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
