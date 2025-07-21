import React, { useState, useEffect } from 'react';
import { Button } from '@/components/ui/button';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip';
import * as LucideIcons from 'lucide-react';
import apiService from '@/utils/api';
import { PublishDestination } from '@/utils/api';
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from '@/components/ui/dropdown-menu';
import { ChevronDown } from 'lucide-react';
import { usePublishDestinations } from '@/hooks/usePublishDestinations';

interface PublishDestinationsProps {
  selectedDestination: string | null;
  onSelectDestination: (destination: string) => void;
}

export function PublishDestinations({ selectedDestination, onSelectDestination }: PublishDestinationsProps) {
  const { destinations } = usePublishDestinations();
  const [buckets, setBuckets] = useState<string[]>([]);
  const [loading, setLoading] = useState<boolean>(true);
  
  // Filter to only destinations with has_bucket=true
  const bucketDests = destinations.filter(d => d.has_bucket);
  
  // Select first destination by default if none is selected
  useEffect(() => {
    if (!selectedDestination && bucketDests.length > 0) {
      onSelectDestination(bucketDests[0].id);
    }
  }, [selectedDestination, bucketDests, onSelectDestination]);
  
  // Load buckets to verify which ones exist
  useEffect(() => {
    const loadBuckets = async () => {
      setLoading(true);
      try {
        const bucketList = await apiService.fetchAllBuckets();
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
  
  const selectedDest = destinations.find(d => d.id === selectedDestination);
  
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="outline" className="w-[200px] justify-between">
          {selectedDest ? selectedDest.name || selectedDest.id : 'Select Destination'}
          <ChevronDown className="ml-2 h-4 w-4" />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="start">
        {destinations.map((dest) => (
          <DropdownMenuItem
            key={dest.id}
            onClick={() => onSelectDestination(dest.id)}
          >
            {dest.name || dest.id}
          </DropdownMenuItem>
        ))}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
