
import { useState } from 'react';
import { SortField, SortDirection } from '../ImageDisplay';

export const useImageSort = (imageContainerOrder: string[], batches: Record<string, any[]>) => {
  const [sortField, setSortField] = useState<SortField>('timestamp');
  const [sortDirection, setSortDirection] = useState<SortDirection>('desc');

  const handleSortClick = (field: SortField) => {
    if (sortField === field) {
      setSortDirection(prev => prev === 'asc' ? 'desc' : 'asc');
    } else {
      setSortField(field);
      setSortDirection('desc');
    }
  };

  const getSortedContainers = () => {
    return [...imageContainerOrder].sort((a, b) => {
      const batchA = batches[a]?.[0];
      const batchB = batches[b]?.[0];
      
      if (!batchA) return 1;
      if (!batchB) return -1;
      
      let comparison = 0;
      
      switch (sortField) {
        case 'index':
          comparison = (batchA.containerId || 0) - (batchB.containerId || 0);
          break;
        case 'prompt':
          comparison = (batchA.prompt || '').localeCompare(batchB.prompt || '');
          break;
        case 'batchSize':
          comparison = batches[a].filter(img => img.status === 'completed').length - 
                      batches[b].filter(img => img.status === 'completed').length;
          break;
        case 'timestamp':
        default:
          comparison = batchA.timestamp - batchB.timestamp;
          break;
      }
      
      return sortDirection === 'asc' ? comparison : -comparison;
    });
  };

  return {
    sortField,
    sortDirection,
    handleSortClick,
    getSortedContainers
  };
};

export default useImageSort;
