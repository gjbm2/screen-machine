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
        console.log('Raw publish destinations from API:', data);
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

  // Only filter out headless destinations as a convenience
  const nonHeadlessDestinations = useMemo(() => {
    return destinations.filter(dest => dest.headless !== true);
  }, [destinations]);

  // Create a backward-compatible list of destinations with buckets
  const destinationsWithBuckets = useMemo(() => {
    return destinations;
  }, [destinations]);

  return {
    destinations,             // All destinations without filtering
    destinationsWithBuckets,  // All destinations (for backward compatibility)
    nonHeadlessDestinations,  // Only destinations that aren't marked as headless
    loading,
    error
  };
} 