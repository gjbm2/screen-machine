import React, { useState, useEffect } from 'react';
import { Button } from '@/components/ui/button';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip';
import * as LucideIcons from 'lucide-react';
import apiService from '@/utils/api';
import { PublishDestination } from '@/utils/api';

interface PublishDestinationsProps {
  selectedDestination: string | null;
  onSelectDestination: (destination: PublishDestination) => void;
}

export function PublishDestinations({ selectedDestination, onSelectDestination }: PublishDestinationsProps) {
  const [publishDestinations, setPublishDestinations] = useState<PublishDestination[]>([]);
  const [buckets, setBuckets] = useState<string[]>([]);
  const [loading, setLoading] = useState<boolean>(true);
  
  // Load publish destinations
  useEffect(() => {
    const loadDestinations = async () => {
      try {
        console.log('Loading publish destinations');
        const destinations = await apiService.getPublishDestinations();
        setPublishDestinations(destinations);
        
        // Select first destination by default if none is selected
        if (!selectedDestination && destinations.length > 0) {
          onSelectDestination(destinations[0]);
        }
      } catch (error) {
        console.error('Error loading publish destinations:', error);
      }
    };
    
    loadDestinations();
  }, [selectedDestination, onSelectDestination]);
  
  // Load buckets to verify which ones exist
  useEffect(() => {
    const loadBuckets = async () => {
      setLoading(true);
      try {
        console.log('Fetching bucket list');
        const bucketList = await apiService.fetchAllBuckets();
        console.log('Available buckets:', bucketList);
        setBuckets(bucketList);
      } catch (error) {
        console.error('Error fetching buckets:', error);
      } finally {
        setLoading(false);
      }
    };
    
    loadBuckets();
  }, []);
  
  // Get the LucideIcon component for a given icon name
  const getIcon = (iconName: string) => {
    const IconComponent = (LucideIcons as any)[iconName] || LucideIcons.Image;
    return <IconComponent className="h-5 w-5" />;
  };
  
  // Check if the destination has a corresponding bucket
  const destinationHasBucket = (destination: PublishDestination) => {
    return buckets.includes(destination.id);
  };
  
  return (
    <div className="flex flex-wrap gap-2 mb-4 p-2 bg-gray-50 dark:bg-gray-800 rounded-md">
      {loading ? (
        <div className="text-sm text-gray-500 p-2">Loading destinations...</div>
      ) : publishDestinations.length === 0 ? (
        <div className="text-sm text-gray-500 p-2">No publish destinations found</div>
      ) : (
        publishDestinations.map((destination) => {
          const isSelected = selectedDestination === destination.id;
          const hasBucket = destinationHasBucket(destination);
          
          return (
            <Tooltip key={destination.id}>
              <TooltipTrigger asChild>
                <Button
                  variant={isSelected ? "default" : "outline"}
                  size="sm"
                  className={`px-3 ${!hasBucket ? 'opacity-50' : ''}`}
                  onClick={() => {
                    console.log('Destination clicked:', destination.id, 'Has bucket:', hasBucket);
                    if (hasBucket) {
                      onSelectDestination(destination);
                    }
                  }}
                  disabled={!hasBucket}
                >
                  {getIcon(destination.icon)}
                  <span className="ml-2 hidden sm:inline">{destination.name}</span>
                </Button>
              </TooltipTrigger>
              <TooltipContent>
                <p>{destination.description}</p>
                {!hasBucket && <p className="text-xs text-red-500">Bucket not found</p>}
              </TooltipContent>
            </Tooltip>
          );
        })
      )}
    </div>
  );
}
