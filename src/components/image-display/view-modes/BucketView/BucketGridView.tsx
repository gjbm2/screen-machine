import React, { useState, useEffect } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Bucket, BucketItem, PublishDestination } from '@/utils/api';
import apiService from '@/utils/api';
import { PublishDestinations } from './PublishDestinations';
import { BucketImage } from './BucketImage';
import { Button } from '@/components/ui/button';
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from '@/components/ui/dropdown-menu';
import { Separator } from '@/components/ui/separator';
import { Settings, Loader2 } from 'lucide-react';
import { toast } from 'sonner';
import { DndContext, closestCenter, KeyboardSensor, PointerSensor, useSensor, useSensors } from '@dnd-kit/core';
import { SortableContext, arrayMove, sortableKeyboardCoordinates, rectSortingStrategy } from '@dnd-kit/sortable';
import { SortableBucketImage } from './SortableBucketImage';

interface BucketDetails extends Bucket {
  published?: string;
  publishedAt?: string;
}

interface BucketGridViewProps {
  onFullScreenView?: (item: BucketItem) => void;
}

export function BucketGridView({ onFullScreenView }: BucketGridViewProps) {
  // State for selected destination/bucket
  const [selectedDestination, setSelectedDestination] = useState<PublishDestination | null>(null);
  const [selectedItem, setSelectedItem] = useState<BucketItem | null>(null);
  const [bucketDetails, setBucketDetails] = useState<Bucket | null>(null);
  
  // Query client for cache invalidation
  const queryClient = useQueryClient();
  
  // Fetch all available buckets
  const { data: buckets = [], isLoading: isBucketsLoading, error: bucketsError } = useQuery({
    queryKey: ['buckets'],
    queryFn: apiService.fetchAllBuckets,
    refetchOnWindowFocus: false
  });
  
  // Fetch publish destinations
  const { data: publishDestinations = [], isLoading: isDestinationsLoading } = useQuery({
    queryKey: ['publishDestinations'],
    queryFn: apiService.getPublishDestinations,
    refetchOnWindowFocus: false
  });
  
  useEffect(() => {
    console.log('Loaded buckets:', buckets);
    console.log('Loaded publish destinations:', publishDestinations);
  }, [buckets, publishDestinations]);
  
  // Fetch details for currently selected bucket
  const fetchBucketDetails = async () => {
    if (!selectedDestination) return;
    try {
      const details = await apiService.getBucketDetails(selectedDestination.id);
      console.log('Fetched bucket details:', details);
      
      // Transform items to match BucketItem interface
      const transformedItems = details.items.map((item: any) => ({
        filename: item.filename,
        url: item.raw_url,  // Use raw_url from backend
        thumbnail_url: item.thumbnail_url,
        thumbnail_embedded: item.thumbnail_embedded,
        favorite: item.favorite,
        metadata: item.metadata
      }));
      
      setBucketDetails({
        name: details.name,
        items: transformedItems,
        published: details.published,
        publishedAt: details.published_at,  // Use published_at from backend
        favorites: details.favorites,
        sequence: details.sequence
      });
    } catch (error) {
      console.error('Error fetching bucket details:', error);
      toast.error('Failed to fetch bucket details');
    }
  };
  
  useEffect(() => {
    fetchBucketDetails();
  }, [selectedDestination]);
  
  useEffect(() => {
    if (bucketDetails) {
      console.log('Bucket details loaded:', bucketDetails);
    }
  }, [bucketDetails]);
  
  // Refresh buckets after certain operations
  const refreshBucket = () => {
    if (selectedDestination?.id) {
      console.log('Refreshing bucket:', selectedDestination.id);
      queryClient.invalidateQueries({ queryKey: ['bucket', selectedDestination.id] });
    }
  };
  
  // Handle operations on images
  const handleToggleFavorite = async (bucket: string, filename: string, currentState: boolean) => {
    if (!selectedDestination?.id) return;
    console.log('Toggle favorite:', bucket, filename, currentState);
    const success = await apiService.toggleFavorite(selectedDestination.id, filename, currentState);
    if (success) {
      refreshBucket();
    }
  };
  
  const handleDelete = async (bucket: string, filename: string) => {
    if (!selectedDestination?.id) return;
    if (confirm('Are you sure you want to delete this image?')) {
      console.log('Deleting image:', bucket, filename);
      const success = await apiService.deleteImage(selectedDestination.id, filename);
      if (success) {
        refreshBucket();
      }
    }
  };
  
  const handleMoveUp = async (bucket: string, filename: string) => {
    if (!selectedDestination?.id) return;
    console.log('Moving image up:', bucket, filename);
    const success = await apiService.moveImage(selectedDestination.id, filename, 'up');
    if (success) {
      refreshBucket();
    }
  };
  
  const handleMoveDown = async (bucket: string, filename: string) => {
    if (!selectedDestination?.id) return;
    console.log('Moving image down:', bucket, filename);
    const success = await apiService.moveImage(selectedDestination.id, filename, 'down');
    if (success) {
      refreshBucket();
    }
  };
  
  const handleCopyTo = async (sourcePublishDestination: string, targetPublishDestination: string, filename: string) => {
    console.log('Copying image:', sourcePublishDestination, targetPublishDestination, filename);
    const success = await apiService.copyImageToBucket(sourcePublishDestination, targetPublishDestination, filename);
    if (success) {
      // Invalidate both source and target bucket queries
      queryClient.invalidateQueries({ queryKey: ['bucket', sourcePublishDestination] });
      queryClient.invalidateQueries({ queryKey: ['bucket', targetPublishDestination] });
      toast.success('Image copied successfully');
    }
  };
  
  const handlePublish = async (bucket: string, filename: string) => {
    if (!selectedDestination?.id) return;
    console.log('Publishing image:', bucket, filename);
    const success = await apiService.publishImage({
      bucket: selectedDestination.id,
      filename,
      destination: selectedDestination.id
    });
    if (success) {
      refreshBucket();
      toast.success('Image published successfully');
    }
  };
  
  const handleBucketMaintenance = async (action: 'purge' | 'reindex' | 'extract') => {
    if (!selectedDestination?.id) return;
    
    const actionLabels = {
      purge: 'purge non-favorites',
      reindex: 're-index',
      extract: 'extract metadata'
    };
    
    if (confirm(`Are you sure you want to ${actionLabels[action]} this bucket?`)) {
      console.log('Performing bucket maintenance:', action, selectedDestination.id);
      const success = await apiService.performBucketMaintenance(selectedDestination.id, action);
      if (success) {
        refreshBucket();
      }
    }
  };
  
  const handleOpenImage = (item: BucketItem) => {
    if (!selectedDestination?.id) return;
    const bucketItem = {
      ...item,
      bucket: selectedDestination.id
    };
    
    setSelectedItem(bucketItem);
    if (onFullScreenView) {
      console.log('Opening image in fullscreen:', bucketItem);
      onFullScreenView(bucketItem);
    }
  };
  
  const handleSelectDestination = (destination: PublishDestination) => {
    console.log('Selected destination:', destination);
    setSelectedDestination(destination);
  };
  
  // DnD sensors for drag and drop functionality
  const sensors = useSensors(
    useSensor(PointerSensor, {
      activationConstraint: {
        distance: 8,
      },
    }),
    useSensor(KeyboardSensor, {
      coordinateGetter: sortableKeyboardCoordinates,
    })
  );
  
  // Handle DnD end event
  const handleDragEnd = (event: any) => {
    const { active, over } = event;
    
    if (active.id !== over.id) {
      // Find indices of items being dragged
      const oldIndex = bucketDetails?.items.findIndex(item => item.filename === active.id);
      const newIndex = bucketDetails?.items.findIndex(item => item.filename === over.id);
      
      if (oldIndex !== undefined && newIndex !== undefined && oldIndex >= 0 && newIndex >= 0) {
        // If moving up, call moveUp multiple times
        if (oldIndex > newIndex) {
          toast.info(`Moving image ${oldIndex - newIndex} positions up`);
          for (let i = 0; i < oldIndex - newIndex; i++) {
            handleMoveUp(selectedDestination.id, active.id);
          }
        } 
        // If moving down, call moveDown multiple times
        else if (oldIndex < newIndex) {
          toast.info(`Moving image ${newIndex - oldIndex} positions down`);
          for (let i = 0; i < newIndex - oldIndex; i++) {
            handleMoveDown(selectedDestination.id, active.id);
          }
        }
      }
    }
  };
  
  const isLoading = isBucketsLoading || isDestinationsLoading;
  const hasError = bucketsError;

  return (
    <div className="flex flex-col h-full">
      <div className="flex items-center justify-between p-4">
      <PublishDestinations 
        selectedDestination={selectedDestination?.id || null}
        onSelectDestination={handleSelectDestination}
      />
      {selectedDestination && bucketDetails && (
        <div className="mb-4 flex justify-between items-center">
          <div>
            <h2 className="text-lg font-semibold">{bucketDetails.name}</h2>
            {bucketDetails.published && (
              <p className="text-sm text-gray-600 dark:text-gray-400">
                Published: {bucketDetails.published}
                {bucketDetails.publishedAt && ` at ${bucketDetails.publishedAt}`}
              </p>
            )}
          </div>
          
          {/* Bucket maintenance dropdown */}
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button variant="outline" size="sm">
                <Settings className="h-4 w-4 mr-1" />
                <span className="hidden sm:inline">Tools</span>
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end">
              <DropdownMenuItem onClick={() => handleBucketMaintenance('purge')}>
                Purge non-favorites
              </DropdownMenuItem>
              <DropdownMenuItem onClick={() => handleBucketMaintenance('reindex')}>
                Re-index bucket
              </DropdownMenuItem>
              <DropdownMenuItem onClick={() => handleBucketMaintenance('extract')}>
                Extract JSON metadata
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        </div>
      )}
        </div>
      
      <Separator />
      
      <div className="flex-1 overflow-auto p-4">
        {selectedDestination && bucketDetails ? (
        <DndContext 
          sensors={sensors}
          collisionDetection={closestCenter}
          onDragEnd={handleDragEnd}
        >
          <SortableContext 
            items={bucketDetails.items.map(item => item.filename)}
            strategy={rectSortingStrategy}
          >
              <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 xl:grid-cols-6 gap-4">
                {bucketDetails.items.map((item) => (
                  <SortableBucketImage
                    key={item.filename}
                    id={item.filename}
                    bucket={selectedDestination.id}
                    item={item}
                    buckets={publishDestinations.map(dest => dest.id)}
                    onToggleFavorite={handleToggleFavorite}
                    onDelete={handleDelete}
                    onCopyTo={handleCopyTo}
                    onMoveUp={handleMoveUp}
                    onMoveDown={handleMoveDown}
                    onOpen={handleOpenImage}
                    onPublish={handlePublish}
                  />
                ))}
            </div>
          </SortableContext>
        </DndContext>
        ) : (
          <div className="flex items-center justify-center h-full">
            <p className="text-gray-500">Select a destination to view images</p>
          </div>
      )}
      </div>
    </div>
  );
}
