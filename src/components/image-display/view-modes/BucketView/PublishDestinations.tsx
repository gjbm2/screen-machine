import React, { useState, useEffect } from 'react';
import { Button } from '@/components/ui/button';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip';
import * as LucideIcons from 'lucide-react';
import apiService from '@/utils/api';
import { PublishDestination } from '@/utils/api';
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from '@/components/ui/dropdown-menu';
import { ChevronDown } from 'lucide-react';

interface PublishDestinationsProps {
  selectedDestination: string | null;
  onSelectDestination: (destination: string) => void;
}

export function PublishDestinations({ selectedDestination, onSelectDestination }: PublishDestinationsProps) {
  const [destinations, setDestinations] = useState<PublishDestination[]>([]);
  const [buckets, setBuckets] = useState<string[]>([]);
  const [loading, setLoading] = useState<boolean>(true);
  
  // Load publish destinations
  useEffect(() => {
    const fetchDestinations = async () => {
      try {
        console.log('Loading publish destinations');
        const dests = await apiService.getPublishDestinations();
        // Filter to only destinations with has_bucket=true
        const bucketDests = dests.filter(d => d.has_bucket);
        setDestinations(bucketDests);
        
        // Select first destination by default if none is selected
        if (!selectedDestination && bucketDests.length > 0) {
          onSelectDestination(bucketDests[0].id);
        }
      } catch (error) {
        console.error('Error fetching destinations:', error);
      }
    };
    
    fetchDestinations();
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
