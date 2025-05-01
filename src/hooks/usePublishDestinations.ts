import { useState, useEffect } from 'react';
import apiService from '@/utils/api';
import { PublishDestination } from '@/utils/api';

export function usePublishDestinations() {
  const [destinations, setDestinations] = useState<PublishDestination[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const fetchDestinations = async () => {
      try {
        setIsLoading(true);
        const data = await apiService.getPublishDestinations();
        setDestinations(data);
        setError(null);
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Failed to fetch publish destinations');
      } finally {
        setIsLoading(false);
      }
    };

    fetchDestinations();
  }, []);

  return { destinations, isLoading, error };
} 