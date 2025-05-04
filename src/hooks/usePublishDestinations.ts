import { useState, useEffect, useMemo } from 'react';
import { Api, PublishDestination } from '../utils/api';

const api = new Api();

export function usePublishDestinations() {
  const [destinations, setDestinations] = useState<PublishDestination[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    async function fetchDestinations() {
      try {
        const data = await api.getPublishDestinations();
        setDestinations(data);
        setError(null);
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Failed to fetch publish destinations');
      } finally {
        setLoading(false);
      }
    }

    fetchDestinations();
  }, []);

  const destinationsWithBuckets = useMemo(() => {
    return destinations.filter(dest => dest.has_bucket);
  }, [destinations]);

  return {
    destinations,
    destinationsWithBuckets,
    loading,
    error
  };
} 