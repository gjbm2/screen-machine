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

  // Filter out headless destinations and hidden destinations (except _recent)
  const nonHeadlessDestinations = useMemo(() => {
    return destinations.filter(dest => 
      dest.headless !== true && 
      (dest.hidden !== true || dest.id === '_recent')
    );
  }, [destinations]);

  // Create a backward-compatible list of destinations with buckets
  const destinationsWithBuckets = useMemo(() => {
    return destinations.filter(dest => 
      dest.hidden !== true || dest.id === '_recent'
    );
  }, [destinations]);

  return {
    destinations,             // All destinations without filtering
    destinationsWithBuckets,  // All non-hidden destinations (except _recent)
    nonHeadlessDestinations,  // Only destinations that aren't marked as headless (except _recent)
    loading,
    error
  };
} 